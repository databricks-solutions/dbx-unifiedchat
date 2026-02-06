"""
Unity Catalog Functions for Multi-Agent System

This module contains UC function implementations for the custom agents:
- Query Planning and Analysis
- SQL Synthesis
- SQL Execution

These functions can be registered in Unity Catalog and called by the LangGraph supervisor.
"""

import json
from typing import Dict, List, Optional, Any
from databricks.sdk.runtime import spark


########################################
# UC Function: Analyze Query and Create Plan
########################################

def analyze_query_plan(
    query: str,
    vector_search_index: str = "yyang.multi_agent_genie.enriched_genie_docs_chunks_vs_index",
    num_results: int = 5
) -> str:
    """
    Analyze a user query and create an execution plan.
    
    This function:
    1. Checks if the query is clear or needs clarification
    2. Searches for relevant Genie spaces using vector search
    3. Determines execution strategy (single/multi-space, join requirements)
    
    Args:
        query: The user's question
        vector_search_index: Full name of the vector search index
        num_results: Number of relevant spaces to retrieve
        
    Returns:
        JSON string with QueryPlan structure containing:
        - question_clear: bool
        - clarification_needed: str (if applicable)
        - clarification_options: list[str] (if applicable)
        - sub_questions: list[str]
        - requires_multiple_spaces: bool
        - relevant_space_ids: list[str]
        - requires_join: bool
        - join_strategy: str ("table_route" or "genie_route")
        - execution_plan: str
    """
    from databricks_langchain import ChatDatabricks
    
    # Initialize LLM for analysis
    llm = ChatDatabricks(endpoint="databricks-claude-sonnet-4-5")
    
    # Step 1: Check query clarity
    # TODO: include {context} in the prompt, context could be some part of the VS results that is relevant to the question
    clarity_prompt = f"""
    Analyze the following question for clarity and specificity:
    
    Question: {query}
    
    Determine if:
    1. The question is clear and answerable as-is
    2. The question needs clarification
    
    If clarification is needed, provide:
    - A brief explanation of what's unclear
    - 2-3 specific clarification options the user can choose from
    
    Return your analysis as JSON:
    {{
        "question_clear": true/false,
        "clarification_needed": "explanation if unclear",
        "clarification_options": ["option 1", "option 2", "option 3"]
    }}
    
    Only return valid JSON, no explanations.
    """
    
    clarity_response = llm.invoke(clarity_prompt)
    clarity_result = json.loads(clarity_response.content)
    
    if not clarity_result.get("question_clear", False):
        return json.dumps({
            "question_clear": False,
            "clarification_needed": clarity_result.get("clarification_needed"),
            "clarification_options": clarity_result.get("clarification_options", [])
        })
    
    # Step 2: Search for relevant Genie spaces using AI Bridge VectorSearchRetrieverTool
    from databricks_langchain import VectorSearchRetrieverTool
    
    # Create VectorSearchRetrieverTool with filter for space_summary chunks
    vs_tool = VectorSearchRetrieverTool(
        index_name=vector_search_index,
        num_results=num_results,
        filters={"chunk_type": "space_summary"},
        query_type="ANN",
    )
    
    # Invoke the tool to get results
    docs = vs_tool.invoke({"query": query})
    
    # Extract space information from document metadata
    relevant_spaces = []
    for doc in docs:
        relevant_spaces.append({
            "space_id": doc.metadata.get("space_id", ""),
            "space_title": doc.metadata.get("space_title", ""),
            "score": doc.metadata.get("score", 0.0)
        })
    
    if not relevant_spaces:
        relevant_spaces = []
    
    # Step 3: Create execution plan
    planning_prompt = f"""
    You are a query planning expert. Analyze the following question and create an execution plan.
    
    Question: {query}
    
    Potentially relevant Genie spaces:
    {json.dumps(relevant_spaces, indent=2)}
    
    Break down the question and determine:
    1. What are the sub-questions or analytical components?
    2. How many Genie spaces are needed to answer completely? (List their space_ids)
    3. If multiple spaces are needed, do we need to JOIN data across them?
    4. If JOIN is needed, what's the best strategy:
       - "table_route": Directly synthesize SQL across multiple tables
       - "genie_route": Query each space separately, then combine results
    5. If no JOIN needed, can answers be verbally merged?
    
    Return your analysis as JSON:
    {{
        "question_clear": true,
        "sub_questions": ["sub-question 1", "sub-question 2", ...],
        "requires_multiple_spaces": true/false,
        "relevant_space_ids": ["space_id_1", "space_id_2", ...],
        "requires_join": true/false,
        "join_strategy": "table_route" or "genie_route" or null,
        "execution_plan": "Brief description of execution plan"
    }}
    
    Only return valid JSON, no explanations.
    """
    
    planning_response = llm.invoke(planning_prompt)
    plan_result = json.loads(planning_response.content)
    
    return json.dumps(plan_result)


########################################
# UC Function: Synthesize SQL (Table Route)
########################################

def synthesize_sql_table_route(
    query: str,
    table_metadata_json: str
) -> str:
    """
    Synthesize SQL query directly across multiple tables (table route).
    
    Args:
        query: The user's question
        table_metadata_json: JSON string with table metadata including:
            - table_name
            - columns
            - relationships
            - sample_data
            
    Returns:
        SQL query string
    """
    from databricks_langchain import ChatDatabricks
    
    llm = ChatDatabricks(endpoint="databricks-claude-sonnet-4-5")
    table_metadata = json.loads(table_metadata_json)
    
    prompt = f"""
    You are an expert SQL developer. Generate a SQL query to answer the following question
    using the available tables.
    
    Question: {query}
    
    Available Tables and Metadata:
    {json.dumps(table_metadata, indent=2)}
    
    Generate a complete, executable SQL query. Include:
    - Proper JOINs where needed
    - WHERE clauses for filtering
    - Appropriate aggregations
    - Column aliases for clarity
    
    Return ONLY the SQL query, no explanations or markdown formatting.
    """
    
    response = llm.invoke(prompt)
    sql = response.content.strip()
    
    # Remove markdown code blocks if present
    if sql.startswith("```"):
        lines = sql.split("\n")
        sql = "\n".join(lines[1:-1]) if len(lines) > 2 else sql
    
    return sql


########################################
# UC Function: Synthesize SQL (Genie Route)
########################################

def synthesize_sql_genie_route(
    query: str,
    sub_queries_json: str
) -> str:
    """
    Combine SQL from multiple Genie agents into a unified query (genie route).
    
    Args:
        query: The original user question
        sub_queries_json: JSON string with list of dicts containing:
            - sub_query: str
            - sql: str
            - space_id: str
            
    Returns:
        Combined SQL query string
    """
    from databricks_langchain import ChatDatabricks
    
    llm = ChatDatabricks(endpoint="databricks-claude-sonnet-4-5")
    sub_queries = json.loads(sub_queries_json)
    
    prompt = f"""
    You are an expert SQL developer. Combine the following SQL queries into a single query
    that answers the original question.
    
    Original Question: {query}
    
    Sub-queries and their SQL:
    {json.dumps(sub_queries, indent=2)}
    
    Generate a unified SQL query that:
    - Combines results from sub-queries using JOINs, CTEs, or subqueries
    - Ensures proper correlation between results
    - Maintains data integrity
    - Returns the final answer to the original question
    
    Return ONLY the SQL query, no explanations or markdown formatting.
    """
    
    response = llm.invoke(prompt)
    sql = response.content.strip()
    
    # Remove markdown code blocks if present
    if sql.startswith("```"):
        lines = sql.split("\n")
        sql = "\n".join(lines[1:-1]) if len(lines) > 2 else sql
    
    return sql


########################################
# UC Function: Execute SQL
########################################

def execute_sql_query(sql: str) -> str:
    """
    Execute a SQL query and return results as a formatted string.
    
    Args:
        sql: SQL query to execute
        
    Returns:
        JSON string with execution results:
        {
            "success": bool,
            "result": str (markdown table if success),
            "row_count": int (if success),
            "columns": list[str] (if success),
            "error": str (if failure),
            "sql": str (if failure)
        }
    """
    try:
        result_df = spark.sql(sql)
        
        # Convert to markdown table for display
        pandas_df = result_df.toPandas()
        
        # Convert to markdown manually (tabulate might not be available in UC function)
        columns = list(pandas_df.columns)
        markdown_lines = []
        
        # Header
        markdown_lines.append("| " + " | ".join(columns) + " |")
        markdown_lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
        
        # Rows
        for _, row in pandas_df.iterrows():
            markdown_lines.append("| " + " | ".join(str(val) for val in row.values) + " |")
        
        markdown_table = "\n".join(markdown_lines)
        
        return json.dumps({
            "success": True,
            "result": markdown_table,
            "row_count": len(pandas_df),
            "columns": columns
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "sql": sql
        })


########################################
# UC Function: Get Table Metadata
########################################

def get_table_metadata(space_ids_json: str) -> str:
    """
    Retrieve table metadata for given Genie space IDs.
    
    This function queries the enriched Genie documentation to get table schemas,
    relationships, and sample data for the spaces.
    
    Args:
        space_ids_json: JSON string with list of space_ids
        
    Returns:
        JSON string with table metadata for each space
    """
    space_ids = json.loads(space_ids_json)
    
    # Query to get table metadata from enriched docs
    query = f"""
    SELECT 
        space_id,
        space_title,
        chunk_type,
        chunk_content,
        metadata
    FROM yyang.multi_agent_genie.enriched_genie_docs_chunks
    WHERE space_id IN ({','.join([f"'{sid}'" for sid in space_ids])})
        AND chunk_type IN ('table_schema', 'table_relationships')
    ORDER BY space_id, chunk_type
    """
    
    try:
        result_df = spark.sql(query)
        metadata_list = []
        
        for row in result_df.collect():
            metadata_list.append({
                "space_id": row.space_id,
                "space_title": row.space_title,
                "chunk_type": row.chunk_type,
                "content": row.chunk_content,
                "metadata": json.loads(row.metadata) if row.metadata else {}
            })
        
        return json.dumps(metadata_list)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "space_ids": space_ids
        })


########################################
# Helper: Verbal Merge Results
########################################

def verbal_merge_results(
    query: str,
    results_json: str
) -> str:
    """
    Verbally merge results from multiple Genie agents.
    
    Args:
        query: Original user question
        results_json: JSON string with list of results from different agents
        
    Returns:
        Merged response text
    """
    from databricks_langchain import ChatDatabricks
    
    llm = ChatDatabricks(endpoint="databricks-claude-sonnet-4-5")
    results = json.loads(results_json)
    
    merge_prompt = f"""
    You are an expert at synthesizing information from multiple sources.
    
    Original Question: {query}
    
    Results from different data sources:
    {json.dumps(results, indent=2)}
    
    Provide a comprehensive answer that:
    - Combines insights from all sources
    - Maintains accuracy and clarity
    - Highlights any complementary or contrasting information
    - Provides a cohesive narrative
    
    Return a clear, well-structured answer.
    """
    
    merged_response = llm.invoke(merge_prompt)
    return merged_response.content

