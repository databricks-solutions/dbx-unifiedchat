# Databricks notebook source
# DBTITLE 1,Install Packages
# MAGIC %pip install databricks-langchain==0.12.1 databricks-vectorsearch==0.63

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

"""
Super Agent (Hybrid Architecture) - Functional Version
No agent classes - Pure functional programming style for easier readability

This is a simplified version of Super_Agent_hybrid.py that:
- Removes all agent classes (OOP) except SuperAgentHybridResponsesAgent
- Uses simple functions instead of classes
- Easier to read and understand
- Maintains same functionality

Only SuperAgentHybridResponsesAgent class is kept for MLflow logging.
"""

import json
from typing import Dict, List, Optional, Any, Annotated, Literal, Generator
from typing_extensions import TypedDict
import operator
from uuid import uuid4
import re
from functools import partial

# COMMAND ----------

# DBTITLE 1,Configuration
CATALOG = "yyang"
SCHEMA = "multi_agent_genie"
TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"
VECTOR_SEARCH_INDEX = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks_vs_index"
LLM_ENDPOINT_CLARIFICATION = "databricks-claude-haiku-4-5"
LLM_ENDPOINT_PLANNING = "databricks-claude-haiku-4-5"
LLM_ENDPOINT_SQL_SYNTHESIS = "databricks-claude-sonnet-4-5"
LLM_ENDPOINT_SUMMARIZE = "databricks-claude-haiku-4-5"

print("="*80)
print("SUPER AGENT (HYBRID) - FUNCTIONAL VERSION")
print("="*80)
print(f"Catalog: {CATALOG}")
print(f"Schema: {SCHEMA}")
print(f"Table: {TABLE_NAME}")
print(f"Vector Search Index: {VECTOR_SEARCH_INDEX}")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Import Dependencies
from databricks_langchain import (
    ChatDatabricks,
    VectorSearchRetrieverTool,
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
)
from databricks_langchain.genie import GenieAgent
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableLambda
import mlflow

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import MemorySaver

from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    output_to_responses_items_stream,
    to_chat_completions_input,
)

print("✓ All dependencies imported")

# COMMAND ----------

# DBTITLE 1,Register Unity Catalog Functions
print("="*80)
print("REGISTERING UNITY CATALOG FUNCTIONS")
print("="*80)

# UC Function 1: get_space_summary
spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_space_summary(
    space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Get high-level summary of Genie spaces'
RETURN
    SELECT COALESCE(
        to_json(
            map_from_entries(
                collect_list(
                    struct(
                        space_id,
                        named_struct(
                            'chunk_id', chunk_id,
                            'chunk_type', chunk_type,
                            'space_title', space_title,
                            'content', searchable_content
                        )
                    )
                )
            )
        ),
        '{{}}'
    ) as result
    FROM {TABLE_NAME}
    WHERE chunk_type = 'space_summary'
    AND (
        space_ids_json IS NULL 
        OR TRIM(LOWER(space_ids_json)) IN ('null', 'none', '')
        OR array_contains(from_json(space_ids_json, 'array<string>'), space_id)
    )
""")
print("✓ Registered: get_space_summary")

# UC Function 2: get_table_overview
spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_table_overview(
    space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs',
    table_names_json STRING DEFAULT 'null' COMMENT 'JSON array of table names'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Get table-level metadata'
RETURN
    SELECT COALESCE(
        to_json(
            map_from_entries(
                collect_list(
                    struct(
                        space_id,
                        named_struct(
                            'space_title', space_title,
                            'tables', tables
                        )
                    )
                )
            )
        ),
        '{{}}'
    ) as result
    FROM (
        SELECT 
            space_id,
            first(space_title) as space_title,
            collect_list(
                named_struct(
                    'chunk_id', chunk_id,
                    'chunk_type', chunk_type,
                    'table_name', table_name,
                    'content', searchable_content
                )
            ) as tables
        FROM {TABLE_NAME}
        WHERE chunk_type = 'table_overview'
        AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
        AND (
            table_names_json IS NULL 
            OR TRIM(LOWER(table_names_json)) IN ('null', 'none', '')
            OR array_contains(from_json(table_names_json, 'array<string>'), table_name)
        )
        GROUP BY space_id
    )
""")
print("✓ Registered: get_table_overview")

# UC Function 3: get_column_detail
spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_column_detail(
    space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs',
    table_names_json STRING DEFAULT 'null' COMMENT 'JSON array of table names',
    column_names_json STRING DEFAULT 'null' COMMENT 'JSON array of column names'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Get column-level metadata'
RETURN
    SELECT COALESCE(
        to_json(
            map_from_entries(
                collect_list(
                    struct(
                        space_id,
                        named_struct(
                            'space_title', space_title,
                            'columns', columns
                        )
                    )
                )
            )
        ),
        '{{}}'
    ) as result
    FROM (
        SELECT 
            space_id,
            first(space_title) as space_title,
            collect_list(
                named_struct(
                    'chunk_id', chunk_id,
                    'chunk_type', chunk_type,
                    'table_name', table_name,
                    'column_name', column_name,
                    'content', searchable_content
                )
            ) as columns
        FROM {TABLE_NAME}
        WHERE chunk_type = 'column_detail'
        AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
        AND array_contains(from_json(table_names_json, 'array<string>'), table_name)
        AND (
            column_names_json IS NULL 
            OR TRIM(LOWER(column_names_json)) IN ('null', 'none', '')
            OR array_contains(from_json(column_names_json, 'array<string>'), column_name)
        )
        GROUP BY space_id
    )
""")
print("✓ Registered: get_column_detail")

# UC Function 4: get_space_details
spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_space_details(
    space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs'
)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Get complete metadata - use as LAST RESORT'
RETURN
    SELECT COALESCE(
        to_json(
            map_from_entries(
                collect_list(
                    struct(
                        space_id,
                        named_struct(
                            'chunk_id', chunk_id,
                            'chunk_type', chunk_type,
                            'space_title', space_title,
                            'complete_metadata', searchable_content
                        )
                    )
                )
            )
        ),
        '{{}}'
    ) as result
    FROM {TABLE_NAME}
    WHERE chunk_type = 'space_details'
    AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
""")
print("✓ Registered: get_space_details")

print("✅ ALL UC FUNCTIONS REGISTERED")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Helper Functions
def load_space_context(table_name: str) -> Dict[str, str]:
    """Load space context from Delta table."""
    df = spark.sql(f"""
        SELECT space_id, searchable_content
        FROM {table_name}
        WHERE chunk_type = 'space_summary'
    """)
    
    context = {row["space_id"]: row["searchable_content"] 
               for row in df.collect()}
    
    print(f"✓ Loaded {len(context)} Genie spaces")
    return context


def parse_json_from_llm_response(content: str) -> Dict[str, Any]:
    """Extract JSON from LLM response (handles markdown code blocks)."""
    # Try to extract JSON from markdown code blocks
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        json_str = content.strip()
    
    # Remove trailing commas
    json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"⚠ JSON parsing error: {e}")
        print(f"Content (first 300 chars): {content[:300]}")
        raise


def extract_sql_from_text(text: str) -> Optional[str]:
    """Extract SQL query from text (handles markdown code blocks)."""
    sql_query = None
    
    if "```sql" in text.lower():
        sql_match = re.search(r'```sql\s*(.*?)\s*```', text, re.IGNORECASE | re.DOTALL)
        if sql_match:
            sql_query = sql_match.group(1).strip()
    elif "```" in text:
        sql_match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
        if sql_match:
            potential_sql = sql_match.group(1).strip()
            if any(kw in potential_sql.upper() for kw in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                sql_query = potential_sql
    elif any(kw in text.upper() for kw in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
        sql_query = text
    
    return sql_query

print("✓ Helper functions defined")

# COMMAND ----------

# DBTITLE 1,Agent State Definition
class AgentState(TypedDict):
    """State that flows through the multi-agent system."""
    # Input
    original_query: str
    
    # Clarification
    question_clear: bool
    clarification_needed: Optional[str]
    clarification_options: Optional[List[str]]
    clarification_count: Optional[int]
    user_clarification_response: Optional[str]
    clarification_message: Optional[str]
    combined_query_context: Optional[str]
    
    # Planning
    plan: Optional[Dict[str, Any]]
    sub_questions: Optional[List[str]]
    requires_multiple_spaces: Optional[bool]
    relevant_space_ids: Optional[List[str]]
    relevant_spaces: Optional[List[Dict[str, Any]]]
    vector_search_relevant_spaces_info: Optional[List[Dict[str, str]]]
    requires_join: Optional[bool]
    join_strategy: Optional[str]
    execution_plan: Optional[str]
    genie_route_plan: Optional[Dict[str, str]]
    
    # SQL Synthesis
    sql_query: Optional[str]
    sql_synthesis_explanation: Optional[str]
    synthesis_error: Optional[str]
    has_sql: Optional[bool]
    
    # Execution
    execution_result: Optional[Dict[str, Any]]
    execution_error: Optional[str]
    
    # Summary
    final_summary: Optional[str]
    
    # Control
    next_agent: Optional[str]
    messages: Annotated[List, operator.add]

print("✓ AgentState defined")

# COMMAND ----------

# DBTITLE 1,Clarification Functions
def check_query_clarity(
    query: str, 
    context: Dict[str, str],
    llm: Runnable,
    clarification_count: int = 0
) -> Dict[str, Any]:
    """Check if query is clear and answerable."""
    if clarification_count >= 1:
        print("⚠ Max clarification attempts reached - proceeding")
        return {"question_clear": True}
    
    clarity_prompt = f"""
Analyze the following question for clarity.

IMPORTANT: Only mark as unclear if TRULY VAGUE or IMPOSSIBLE to answer.

Question: {query}

Context (Available Data):
{json.dumps(context, indent=2)}

Determine if:
1. Question is clear and answerable (BE LENIENT - default TRUE)
2. Question is TRULY VAGUE and needs clarification
3. Metrics/dimensions/filters can be mapped to available data

Return JSON:
{{
    "question_clear": true/false,
    "clarification_needed": "explanation or null",
    "clarification_options": ["option 1", "option 2", "option 3"] or null
}}
"""
    
    response = llm.invoke(clarity_prompt)
    content = response.content.strip()
    
    try:
        return parse_json_from_llm_response(content)
    except:
        print("Defaulting to question_clear=True")
        return {"question_clear": True}

print("✓ Clarification functions defined")

# COMMAND ----------

# DBTITLE 1,Planning Functions
def search_relevant_spaces(
    query: str,
    vector_search_index: str,
    num_results: int = 5
) -> List[Dict[str, Any]]:
    """Search for relevant Genie spaces using vector search."""
    vs_tool = VectorSearchRetrieverTool(
        index_name=vector_search_index,
        num_results=num_results,
        columns=["space_id", "space_title", "searchable_content"],
        filters={"chunk_type": "space_summary"},
        query_type="ANN",
        include_metadata=True,
        include_score=True
    )
    
    docs = vs_tool.invoke({"query": query})
    
    relevant_spaces = []
    for doc in docs:
        relevant_spaces.append({
            "space_id": doc.metadata.get("space_id", ""),
            "space_title": doc.metadata.get("space_title", ""),
            "searchable_content": doc.page_content,
            "score": doc.metadata.get("score", 0.0)
        })
    
    return relevant_spaces


def create_execution_plan(
    query: str,
    relevant_spaces: List[Dict[str, Any]],
    llm: Runnable
) -> Dict[str, Any]:
    """Create execution plan based on query and relevant spaces."""
    planning_prompt = f"""
You are a query planning expert. Analyze the question and create an execution plan.

Question: {query}

Potentially relevant Genie spaces:
{json.dumps(relevant_spaces, indent=2)}

Determine:
1. What are the sub-questions?
2. How many Genie spaces needed? (List space_ids)
3. Do we need to JOIN data across spaces?
4. Best strategy: "table_route" or "genie_route"?
5. Execution plan description
6. For genie_route: Return genie_route_plan mapping space_id to partial_question

Return JSON:
{{
    "original_query": "{query}",
    "vector_search_relevant_spaces_info": {[{sp['space_id']: sp['space_title']} for sp in relevant_spaces]},
    "question_clear": true,
    "sub_questions": ["sub-question 1", "sub-question 2"],
    "requires_multiple_spaces": true/false,
    "relevant_space_ids": ["space_id_1", "space_id_2"],
    "requires_join": true/false,
    "join_strategy": "table_route" or "genie_route",
    "execution_plan": "Brief description",
    "genie_route_plan": {{"space_id_1":"partial_question_1"}} or null
}}
"""
    
    response = llm.invoke(planning_prompt)
    content = response.content.strip()
    
    return parse_json_from_llm_response(content)

print("✓ Planning functions defined")

# COMMAND ----------

# DBTITLE 1,SQL Synthesis Functions - Table Route
def create_uc_sql_synthesis_agent(llm: Runnable, catalog: str, schema: str):
    """Create SQL synthesis agent with UC function tools."""
    client = DatabricksFunctionClient()
    set_uc_function_client(client)
    
    uc_function_names = [
        f"{catalog}.{schema}.get_space_summary",
        f"{catalog}.{schema}.get_table_overview",
        f"{catalog}.{schema}.get_column_detail",
        f"{catalog}.{schema}.get_space_details",
    ]
    
    uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
    tools = uc_toolkit.tools
    
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=(
            "You are a SQL synthesis agent.\n\n"
            "WORKFLOW:\n"
            "1. Review execution plan and metadata\n"
            "2. If sufficient → Generate SQL\n"
            "3. If insufficient, call UC functions:\n"
            "   a) get_space_summary\n"
            "   b) get_table_overview\n"
            "   c) get_column_detail\n"
            "   d) get_space_details (LAST RESORT)\n"
            "4. Generate executable SQL\n\n"
            "OUTPUT:\n"
            "1. Your explanation\n"
            "2. SQL in ```sql code block\n"
        )
    )
    
    return agent


def synthesize_sql_table_route(plan: Dict[str, Any], llm: Runnable, catalog: str, schema: str) -> Dict[str, Any]:
    """Synthesize SQL using UC function tools."""
    agent = create_uc_sql_synthesis_agent(llm, catalog, schema)
    
    agent_message = {
        "messages": [{
            "role": "user",
            "content": f"""
Generate SQL for this plan:
{json.dumps(plan, indent=2)}

Use UC function tools to gather metadata.
"""
        }]
    }
    
    result = agent.invoke(agent_message)
    
    if result and "messages" in result:
        final_content = result["messages"][-1].content
        original_content = final_content
        
        sql_query = extract_sql_from_text(final_content)
        has_sql = sql_query is not None
        
        # Remove SQL block to get explanation
        if sql_query:
            explanation = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL).strip()
            if not explanation:
                explanation = "SQL generated successfully."
        else:
            explanation = final_content.strip()
        
        return {
            "sql": sql_query,
            "explanation": explanation,
            "has_sql": has_sql
        }
    
    raise Exception("No response from agent")

print("✓ SQL synthesis (table route) functions defined")

# COMMAND ----------

# DBTITLE 1,SQL Synthesis Functions - Genie Route
def create_genie_agent_tools(relevant_spaces: List[Dict[str, Any]]) -> List:
    """Create Genie agents as tools for relevant spaces."""
    def enforce_limit(messages, n=5):
        last = messages[-1] if messages else {"content": ""}
        content = last.get("content", "") if isinstance(last, dict) else last.content
        return f"{content}\n\nPlease limit to {n} rows."
    
    genie_agent_tools = []
    
    print(f"  Creating Genie tools for {len(relevant_spaces)} spaces...")
    
    for space in relevant_spaces:
        space_id = space.get("space_id")
        space_title = space.get("space_title", space_id)
        description = space.get("searchable_content", "")
        
        if not space_id:
            continue
        
        genie_agent_name = f"Genie_{space_title}"
        
        genie_agent = GenieAgent(
            genie_space_id=space_id,
            genie_agent_name=genie_agent_name,
            description=description,
            include_context=True,
            message_processor=lambda msgs: enforce_limit(msgs, n=5)
        )
        
        def make_agent_invoker(agent):
            return lambda question: agent.invoke(
                {"messages": [{"role": "user", "content": question}]}
            )
        
        runnable = RunnableLambda(make_agent_invoker(genie_agent))
        runnable.name = genie_agent_name
        runnable.description = description
        
        genie_agent_tools.append(
            runnable.as_tool(
                name=genie_agent_name,
                description=description,
                arg_types={"question": str}
            )
        )
        
        print(f"  ✓ Created: {genie_agent_name}")
    
    return genie_agent_tools


def create_genie_sql_synthesis_agent(llm: Runnable, genie_agent_tools: List):
    """Create SQL synthesis agent with Genie agent tools."""
    agent = create_agent(
        model=llm,
        tools=genie_agent_tools,
        system_prompt=(
"""SQL synthesis agent using Genie agents as tools.

WORKFLOW:
1. Extract partial questions from genie_route_plan
2. Call corresponding Genie agent tools asynchronously
3. Extract SQL from each Genie agent response
4. Combine SQLs into final query

OUTPUT:
1. Your explanation (combine Genie thinking + your reasoning)
2. Final SQL in ```sql code block
"""
        )
    )
    
    return agent


def synthesize_sql_genie_route(
    plan: Dict[str, Any],
    relevant_spaces: List[Dict[str, Any]],
    llm: Runnable
) -> Dict[str, Any]:
    """Synthesize SQL using Genie agents."""
    genie_agent_tools = create_genie_agent_tools(relevant_spaces)
    agent = create_genie_sql_synthesis_agent(llm, genie_agent_tools)
    
    agent_message = {
        "messages": [{
            "role": "user",
            "content": f"""
Generate SQL for this plan:
{json.dumps(plan, indent=2)}
"""
        }]
    }
    
    print("🤖 Invoking Genie SQL synthesis agent...")
    
    try:
        mlflow.langchain.autolog()
        result = agent.invoke(agent_message)
        
        final_message = result["messages"][-1]
        final_content = final_message.content.strip()
        
        sql_query = extract_sql_from_text(final_content)
        has_sql = sql_query is not None
        
        if sql_query:
            explanation = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL).strip()
            if not explanation:
                explanation = "SQL generated by Genie agents."
        else:
            explanation = final_content
        
        return {
            "sql": sql_query,
            "explanation": explanation,
            "has_sql": has_sql
        }
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        return {
            "sql": None,
            "explanation": f"Failed: {str(e)}",
            "has_sql": False
        }

print("✓ SQL synthesis (genie route) functions defined")

# COMMAND ----------

# DBTITLE 1,SQL Execution Functions
def execute_sql_query(
    sql_query: str,
    max_rows: int = 100,
    return_format: str = "dict"
) -> Dict[str, Any]:
    """Execute SQL query and return results."""
    # Extract SQL from markdown if present
    if sql_query and isinstance(sql_query, dict) and "messages" in sql_query:
        sql_query = sql_query["messages"][-1].content
    
    extracted_sql = sql_query.strip()
    extracted_sql = extract_sql_from_text(extracted_sql) or extracted_sql
    
    # Add LIMIT if not present
    if "limit" not in extracted_sql.lower():
        extracted_sql = f"{extracted_sql.rstrip(';')} LIMIT {max_rows}"
    
    try:
        print(f"\n{'='*80}")
        print("🔍 EXECUTING SQL")
        print(f"{'='*80}")
        print(f"SQL:\n{extracted_sql}")
        print(f"{'='*80}\n")
        
        df = spark.sql(extracted_sql)
        results_list = df.collect()
        row_count = len(results_list)
        columns = df.columns
        
        print(f"✅ Success! {row_count} rows returned")
        
        if return_format == "json":
            result_data = df.toJSON().collect()
        elif return_format == "markdown":
            result_data = df.toPandas().to_markdown(index=False)
        else:
            result_data = [row.asDict() for row in results_list]
        
        df.show(n=min(10, row_count), truncate=False)
        
        return {
            "success": True,
            "sql": extracted_sql,
            "result": result_data,
            "row_count": row_count,
            "columns": columns,
        }
    except Exception as e:
        print(f"❌ SQL execution failed: {e}")
        return {
            "success": False,
            "sql": extracted_sql,
            "result": None,
            "row_count": 0,
            "columns": [],
            "error": str(e)
        }

print("✓ SQL execution functions defined")

# COMMAND ----------

# DBTITLE 1,Result Summarization Functions
def generate_result_summary(state: AgentState, llm: Runnable) -> str:
    """Generate natural language summary of workflow execution."""
    original_query = state.get('original_query', 'N/A')
    question_clear = state.get('question_clear', False)
    clarification_needed = state.get('clarification_needed')
    execution_plan = state.get('execution_plan')
    join_strategy = state.get('join_strategy')
    sql_query = state.get('sql_query')
    sql_explanation = state.get('sql_synthesis_explanation')
    exec_result = state.get('execution_result', {})
    synthesis_error = state.get('synthesis_error')
    execution_error = state.get('execution_error')
    
    prompt = f"""Generate a concise summary of this workflow execution.

**Original Query:** {original_query}

**Execution Details:**

"""
    
    if not question_clear:
        prompt += f"""**Status:** Needs clarification
**Clarification:** {clarification_needed}
"""
    else:
        if execution_plan:
            prompt += f"""**Planning:** {execution_plan}
**Strategy:** {join_strategy or 'N/A'}

"""
        
        if sql_query:
            prompt += f"""**SQL Generation:** ✅ Successful
**SQL:**
```sql
{sql_query}
```

"""
            if sql_explanation:
                prompt += f"""**Explanation:** {sql_explanation[:2000]}

"""
            
            if exec_result.get('success'):
                row_count = exec_result.get('row_count', 0)
                columns = exec_result.get('columns', [])
                result = exec_result.get('result', [])
                prompt += f"""**Execution:** ✅ Successful
**Rows:** {row_count}
**Columns:** {', '.join(columns[:5])}

**Result:** {json.dumps(result, indent=2)}
"""
            elif execution_error:
                prompt += f"""**Execution:** ❌ Failed
**Error:** {execution_error}
"""
        elif synthesis_error:
            prompt += f"""**SQL Generation:** ❌ Failed
**Error:** {synthesis_error}
"""
    
    prompt += """
**Task:** Generate detailed summary that:
1. Describes user's question
2. Explains system actions
3. States outcome
4. Includes SQL and results if generated
"""
    
    response = llm.invoke(prompt)
    return response.content.strip()

print("✓ Result summarization functions defined")

# COMMAND ----------

# DBTITLE 1,Node Functions
def clarification_node(state: AgentState) -> AgentState:
    """Clarification node - checks query clarity."""
    print("\n" + "="*80)
    print("🔍 CLARIFICATION AGENT")
    print("="*80)
    
    clarification_count = state.get("clarification_count", 0)
    user_response = state.get("user_clarification_response")
    
    if user_response and clarification_count > 0:
        print("✓ User provided clarification")
        
        original = state["original_query"]
        clarif_msg = state.get("clarification_message", "")
        
        combined_context = f"""**Original Query**: {original}

**Clarification Question**: {clarif_msg}

**User's Answer**: {user_response}

**Context**: User clarified. Use all info together."""
        
        state["combined_query_context"] = combined_context
        state["question_clear"] = True
        state["next_agent"] = "planning"
        
        state["messages"].append(
            SystemMessage(content=f"Clarification incorporated: {user_response}")
        )
        
        return state
    
    query = state["original_query"]
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
    
    context = load_space_context(TABLE_NAME)
    clarity_result = check_query_clarity(query, context, llm, clarification_count)
    
    state["question_clear"] = clarity_result.get("question_clear", True)
    state["clarification_needed"] = clarity_result.get("clarification_needed")
    state["clarification_options"] = clarity_result.get("clarification_options")
    
    if state["question_clear"]:
        print("✓ Query is clear")
        state["next_agent"] = "planning"
        state["combined_query_context"] = state["original_query"]
    else:
        print("⚠ Query needs clarification")
        state["clarification_count"] = clarification_count + 1
        
        clarification_message = (
            f"I need clarification: {state['clarification_needed']}\n\n"
            f"Please choose or provide clarification:\n"
        )
        if state["clarification_options"]:
            for i, opt in enumerate(state["clarification_options"], 1):
                clarification_message += f"{i}. {opt}\n"
        
        state["clarification_message"] = clarification_message
        state["messages"].append(AIMessage(content=clarification_message))
    
    state["messages"].append(
        SystemMessage(content=f"Clarity check: {json.dumps(clarity_result, indent=2)}")
    )
    
    return state


def planning_node(state: AgentState) -> AgentState:
    """Planning node - creates execution plan."""
    print("\n" + "="*80)
    print("📋 PLANNING AGENT")
    print("="*80)
    
    query = state.get("combined_query_context") or state["original_query"]
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING)
    
    relevant_spaces = search_relevant_spaces(query, VECTOR_SEARCH_INDEX)
    plan = create_execution_plan(query, relevant_spaces, llm)
    
    state["plan"] = plan
    state["sub_questions"] = plan.get("sub_questions", [])
    state["requires_multiple_spaces"] = plan.get("requires_multiple_spaces", False)
    state["relevant_space_ids"] = plan.get("relevant_space_ids", [])
    state["requires_join"] = plan.get("requires_join", False)
    state["join_strategy"] = plan.get("join_strategy")
    state["execution_plan"] = plan.get("execution_plan", "")
    state["genie_route_plan"] = plan.get("genie_route_plan")
    state["vector_search_relevant_spaces_info"] = plan.get("vector_search_relevant_spaces_info", [])
    state["relevant_spaces"] = relevant_spaces
    
    if state["join_strategy"] == "genie_route":
        print("✓ Using GENIE ROUTE")
        state["next_agent"] = "sql_synthesis_genie"
    else:
        print("✓ Using TABLE ROUTE")
        state["next_agent"] = "sql_synthesis_table"
    
    state["messages"].append(
        SystemMessage(content=f"Plan: {json.dumps(plan, indent=2)}")
    )
    
    return state


def sql_synthesis_table_node(state: AgentState) -> AgentState:
    """SQL synthesis node - table route."""
    print("\n" + "="*80)
    print("⚡ SQL SYNTHESIS - TABLE ROUTE")
    print("="*80)
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    plan = state.get("plan", {})
    
    try:
        result = synthesize_sql_table_route(plan, llm, CATALOG, SCHEMA)
        
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        state["sql_synthesis_explanation"] = explanation
        
        if has_sql and sql_query:
            state["sql_query"] = sql_query
            state["has_sql"] = has_sql
            state["next_agent"] = "sql_execution"
            print("✓ SQL synthesized")
            state["messages"].append(
                AIMessage(content=f"SQL (Table Route):\n{explanation}")
            )
        else:
            print("⚠ No SQL generated")
            state["synthesis_error"] = "Cannot generate SQL"
            state["next_agent"] = "summarize"
            state["messages"].append(
                AIMessage(content=f"SQL Failed:\n{explanation}")
            )
    except Exception as e:
        print(f"❌ Failed: {e}")
        state["synthesis_error"] = str(e)
        state["sql_synthesis_explanation"] = str(e)
        state["messages"].append(
            AIMessage(content=f"SQL Failed:\n{str(e)}")
        )
    
    return state


def sql_synthesis_genie_node(state: AgentState) -> AgentState:
    """SQL synthesis node - genie route."""
    print("\n" + "="*80)
    print("🐢 SQL SYNTHESIS - GENIE ROUTE")
    print("="*80)
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    relevant_spaces = state.get("relevant_spaces", [])
    
    if not relevant_spaces:
        print("❌ No relevant spaces")
        state["synthesis_error"] = "No relevant spaces"
        return state
    
    plan = state.get("plan", {})
    
    try:
        result = synthesize_sql_genie_route(plan, relevant_spaces, llm)
        
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        state["sql_synthesis_explanation"] = explanation
        
        if has_sql and sql_query:
            state["sql_query"] = sql_query
            state["next_agent"] = "sql_execution"
            state["has_sql"] = has_sql
            print("✓ SQL synthesized")
            state["messages"].append(
                AIMessage(content=f"SQL (Genie Route):\n{explanation}")
            )
        else:
            print("⚠ No SQL generated")
            state["synthesis_error"] = "Cannot generate SQL"
            state["next_agent"] = "summarize"
            state["messages"].append(
                AIMessage(content=f"SQL Failed:\n{explanation}")
            )
    except Exception as e:
        print(f"❌ Failed: {e}")
        state["synthesis_error"] = str(e)
        state["sql_synthesis_explanation"] = str(e)
        state["messages"].append(
            AIMessage(content=f"SQL Failed:\n{str(e)}")
        )
    
    return state


def sql_execution_node(state: AgentState) -> AgentState:
    """SQL execution node."""
    print("\n" + "="*80)
    print("🚀 SQL EXECUTION")
    print("="*80)
    
    sql_query = state.get("sql_query")
    
    if not sql_query:
        print("❌ No SQL to execute")
        state["execution_error"] = "No SQL query"
        return state
    
    result = execute_sql_query(sql_query)
    
    if result["success"]:
        print(f"✅ Success: {result['row_count']} rows")
        state["messages"].append(
            SystemMessage(content=f"Executed: {result['row_count']} rows")
        )
    else:
        print(f"❌ Failed: {result.get('error')}")
        state["execution_error"] = result.get("error")
        state["messages"].append(
            SystemMessage(content=f"Failed: {result.get('error')}")
        )
    
    state["execution_result"] = result
    state["next_agent"] = "summarize"
    
    return state


def summarize_node(state: AgentState) -> AgentState:
    """Summarization node - final node."""
    print("\n" + "="*80)
    print("📝 RESULT SUMMARIZE")
    print("="*80)
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SUMMARIZE, temperature=0.1, max_tokens=2000)
    summary = generate_result_summary(state, llm)
    
    print(f"✅ Summary generated")
    state["final_summary"] = summary
    
    # Build comprehensive message
    final_parts = [f"📝 **Summary:**\n{summary}\n"]
    
    if state.get("original_query"):
        final_parts.append(f"🔍 **Original Query:**\n{state['original_query']}\n")
    
    if state.get("execution_plan"):
        final_parts.append(f"📋 **Plan:**\n{state['execution_plan']}\n")
    
    if state.get("sql_synthesis_explanation"):
        final_parts.append(f"💭 **Explanation:**\n{state['sql_synthesis_explanation']}\n")
    
    if state.get("sql_query"):
        final_parts.append(f"💻 **SQL:**\n```sql\n{state['sql_query']}\n```\n")
    
    exec_result = state.get("execution_result")
    if exec_result:
        if exec_result.get("success"):
            final_parts.append(f"✅ **Success:** {exec_result.get('row_count', 0)} rows\n")
            
            results = exec_result.get("result", [])
            if results:
                try:
                    import pandas as pd
                    df = pd.DataFrame(results)
                    final_parts.append(f"\n📊 **Results:**\n")
                    
                    try:
                        display(df)
                    except:
                        print(df.to_string())
                    
                    final_parts.append(f"Shape: {df.shape}\n")
                    final_parts.append(f"Preview:\n```\n{df.head().to_string()}\n```\n")
                except Exception as e:
                    final_parts.append(f"⚠️ DataFrame error: {e}\n")
        else:
            final_parts.append(f"❌ **Failed:**\n{exec_result.get('error')}\n")
    
    comprehensive_message = "\n".join(final_parts)
    state["messages"].append(AIMessage(content=comprehensive_message))
    
    return state

print("✓ All node functions defined")

# COMMAND ----------

# DBTITLE 1,Build LangGraph Workflow
def create_super_agent_hybrid():
    """Create the hybrid super agent workflow."""
    print("\n" + "="*80)
    print("🏗️ BUILDING WORKFLOW")
    print("="*80)
    
    workflow = StateGraph(AgentState)
    
    workflow.add_node("clarification", clarification_node)
    workflow.add_node("planning", planning_node)
    workflow.add_node("sql_synthesis_table", sql_synthesis_table_node)
    workflow.add_node("sql_synthesis_genie", sql_synthesis_genie_node)
    workflow.add_node("sql_execution", sql_execution_node)
    workflow.add_node("summarize", summarize_node)
    
    def route_after_clarification(state: AgentState) -> str:
        return "planning" if state.get("question_clear", False) else END
    
    def route_after_planning(state: AgentState) -> str:
        next_agent = state.get("next_agent", "summarize")
        if next_agent == "sql_synthesis_table":
            return "sql_synthesis_table"
        elif next_agent == "sql_synthesis_genie":
            return "sql_synthesis_genie"
        return "summarize"
    
    def route_after_synthesis(state: AgentState) -> str:
        return "sql_execution" if state.get("next_agent") == "sql_execution" else "summarize"
    
    workflow.set_entry_point("clarification")
    
    workflow.add_conditional_edges(
        "clarification",
        route_after_clarification,
        {"planning": "planning", END: END}
    )
    
    workflow.add_conditional_edges(
        "planning",
        route_after_planning,
        {
            "sql_synthesis_table": "sql_synthesis_table",
            "sql_synthesis_genie": "sql_synthesis_genie",
            "summarize": "summarize"
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_table",
        route_after_synthesis,
        {"sql_execution": "sql_execution", "summarize": "summarize"}
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_genie",
        route_after_synthesis,
        {"sql_execution": "sql_execution", "summarize": "summarize"}
    )
    
    workflow.add_edge("sql_execution", "summarize")
    workflow.add_edge("summarize", END)
    
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)
    
    print("✅ Workflow compiled!")
    print("="*80)
    
    return app

super_agent_hybrid = create_super_agent_hybrid()

# COMMAND ----------

# DBTITLE 1,SuperAgentHybridResponsesAgent Class (For MLflow)
class SuperAgentHybridResponsesAgent(ResponsesAgent):
    """
    ResponsesAgent wrapper for Databricks Model Serving deployment.
    This is the ONLY class kept in this file for MLflow logging.
    """
    
    def __init__(self, agent: CompiledStateGraph):
        self.agent = agent
        print("✓ SuperAgentHybridResponsesAgent initialized")
    
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        """Non-streaming prediction."""
        outputs = [
            event.item
            for event in self.predict_stream(request)
            if event.type == "response.output_item.done"
        ]
        return ResponsesAgentResponse(output=outputs, custom_outputs=request.custom_inputs)
    
    def predict_stream(
        self,
        request: ResponsesAgentRequest,
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        """Streaming prediction."""
        cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
        latest_query = cc_msgs[-1]["content"] if cc_msgs else ""
        
        thread_id = request.custom_inputs.get("thread_id", "default") if request.custom_inputs else "default"
        config = {"configurable": {"thread_id": thread_id}}
        
        is_clarification_response = request.custom_inputs.get("is_clarification_response", False) if request.custom_inputs else False
        
        if is_clarification_response:
            original_query = request.custom_inputs.get("original_query", latest_query)
            clarification_message = request.custom_inputs.get("clarification_message", "")
            clarification_count = request.custom_inputs.get("clarification_count", 1)
            
            initial_state = {
                "original_query": original_query,
                "clarification_message": clarification_message,
                "clarification_count": clarification_count,
                "user_clarification_response": latest_query,
                "question_clear": False,
                "messages": [HumanMessage(content=f"Clarification: {latest_query}")],
                "next_agent": "clarification"
            }
        else:
            initial_state = {
                "original_query": latest_query,
                "question_clear": False,
                "messages": [
                    SystemMessage(content="You are a multi-agent Q&A system."),
                    HumanMessage(content=latest_query)
                ],
                "next_agent": "clarification"
            }
        
        first_message = True
        seen_ids = set()
        
        for _, events in self.agent.stream(initial_state, config, stream_mode=["updates"]):
            new_msgs = [
                msg
                for v in events.values()
                for msg in v.get("messages", [])
                if hasattr(msg, 'id') and msg.id not in seen_ids
            ]
            
            if first_message:
                seen_ids.update(msg.id for msg in new_msgs[: len(cc_msgs)])
                new_msgs = new_msgs[len(cc_msgs):]
                first_message = False
            else:
                seen_ids.update(msg.id for msg in new_msgs)
                if events:
                    node_name = tuple(events.keys())[0]
                    yield ResponsesAgentStreamEvent(
                        type="response.output_item.done",
                        item=self.create_text_output_item(
                            text=f"<name>{node_name}</name>", id=str(uuid4())
                        ),
                    )
            
            if len(new_msgs) > 0:
                yield from output_to_responses_items_stream(new_msgs)


AGENT = SuperAgentHybridResponsesAgent(super_agent_hybrid)

print("\n" + "="*80)
print("✅ AGENT CREATED FOR MLFLOW")
print("="*80)

mlflow.langchain.autolog()
mlflow.models.set_model(AGENT)

# COMMAND ----------

# DBTITLE 1,Helper Functions for Invocation
def invoke_super_agent_hybrid(query: str, thread_id: str = "default") -> Dict[str, Any]:
    """Invoke the super agent."""
    print("\n" + "="*80)
    print("🚀 INVOKING SUPER AGENT")
    print("="*80)
    print(f"Query: {query}")
    print(f"Thread: {thread_id}")
    print("="*80)
    
    initial_state = {
        "original_query": query,
        "question_clear": False,
        "messages": [
            SystemMessage(content="You are a multi-agent Q&A system."),
            HumanMessage(content=query)
        ],
        "next_agent": "clarification"
    }
    
    config = {"configurable": {"thread_id": thread_id}}
    mlflow.langchain.autolog()
    
    final_state = super_agent_hybrid.invoke(initial_state, config)
    
    print("\n" + "="*80)
    print("✅ EXECUTION COMPLETE")
    print("="*80)
    
    return final_state


def respond_to_clarification(
    clarification_response: str,
    previous_state: Dict[str, Any],
    thread_id: str = "default"
) -> Dict[str, Any]:
    """Respond to clarification request."""
    print("\n" + "="*80)
    print("💬 RESPONDING TO CLARIFICATION")
    print("="*80)
    
    new_state = {
        "original_query": previous_state["original_query"],
        "question_clear": False,
        "clarification_count": previous_state.get("clarification_count", 1),
        "clarification_message": previous_state.get("clarification_message", ""),
        "user_clarification_response": clarification_response,
        "messages": [HumanMessage(content=f"Clarification: {clarification_response}")],
        "next_agent": "clarification"
    }
    
    config = {"configurable": {"thread_id": thread_id}}
    mlflow.langchain.autolog()
    
    final_state = super_agent_hybrid.invoke(new_state, config)
    
    print("\n" + "="*80)
    print("✅ COMPLETE")
    print("="*80)
    
    return final_state


def ask_follow_up_query(new_query: str, thread_id: str = "default") -> Dict[str, Any]:
    """Ask follow-up query in same conversation."""
    print("\n" + "="*80)
    print("💬 FOLLOW-UP QUERY")
    print("="*80)
    print(f"Query: {new_query}")
    print(f"Thread: {thread_id}")
    print("="*80)
    
    new_state = {
        "original_query": new_query,
        "question_clear": False,
        "messages": [HumanMessage(content=new_query)],
        "next_agent": "clarification"
    }
    
    config = {"configurable": {"thread_id": thread_id}}
    mlflow.langchain.autolog()
    
    final_state = super_agent_hybrid.invoke(new_state, config)
    
    print("\n" + "="*80)
    print("✅ COMPLETE")
    print("="*80)
    
    return final_state


def display_results(final_state: Dict[str, Any]):
    """Display results from agent execution."""
    print("\n" + "="*80)
    print("📊 RESULTS")
    print("="*80)
    
    if final_state.get('final_summary'):
        print(f"\n📝 Summary:\n{final_state['final_summary']}\n")
    
    print(f"\n🔍 Query: {final_state.get('original_query', 'N/A')}")
    
    if not final_state.get('question_clear', True):
        print(f"\n⚠️ Clarification Needed:")
        print(f"  {final_state.get('clarification_needed', 'N/A')}")
    
    if final_state.get('execution_plan'):
        print(f"\n📋 Plan: {final_state['execution_plan']}")
        print(f"  Strategy: {final_state.get('join_strategy', 'N/A')}")
    
    if final_state.get('sql_query'):
        print(f"\n💻 SQL:\n{'─'*80}\n{final_state['sql_query']}\n{'─'*80}")
    
    exec_result = final_state.get('execution_result')
    if exec_result and exec_result.get('success'):
        print(f"\n✅ Success: {exec_result.get('row_count', 0)} rows")
        results = exec_result.get("result", [])
        if results:
            for i, row in enumerate(results[:10], 1):
                print(f"  Row {i}: {row}")
    elif exec_result:
        print(f"\n❌ Failed: {exec_result.get('error')}")
    
    print("\n" + "="*80)


def get_results_as_dataframe(final_state: Dict[str, Any]):
    """Convert results to pandas DataFrame."""
    import pandas as pd
    
    exec_result = final_state.get('execution_result')
    if not exec_result or not exec_result.get('success'):
        print("⚠️ No results to convert")
        return None
    
    results = exec_result.get('result', [])
    if not results:
        print("⚠️ No data")
        return None
    
    try:
        df = pd.DataFrame(results)
        print(f"✅ Converted {len(results)} rows to DataFrame")
        print(f"Shape: {df.shape}")
        return df
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

print("✓ Helper functions defined")

# COMMAND ----------

# DBTITLE 1,Test: Simple Query
test_query = "What is the average cost of medical claims per claim in 2024?"
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_001")
display_results(final_state)

# COMMAND ----------

# DBTITLE 1,Test: Genie Route
test_query = "What is the average cost of medical claims per claim in 2024? Use genie route"
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_002")
display_results(final_state)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC
# MAGIC This is a functional version of Super_Agent_hybrid.py:
# MAGIC
# MAGIC **Changes:**
# MAGIC - ❌ Removed all agent classes (ClarificationAgent, PlanningAgent, etc.)
# MAGIC - ✅ Converted to simple functions
# MAGIC - ✅ Kept SuperAgentHybridResponsesAgent for MLflow logging
# MAGIC - ✅ Easier to read and understand
# MAGIC
# MAGIC **Structure:**
# MAGIC 1. Configuration
# MAGIC 2. Imports
# MAGIC 3. UC Functions
# MAGIC 4. Helper functions
# MAGIC 5. AgentState
# MAGIC 6. Core logic functions (replacing classes)
# MAGIC 7. Node functions
# MAGIC 8. Workflow creation
# MAGIC 9. SuperAgentHybridResponsesAgent (for MLflow)
# MAGIC 10. Helper functions for invocation
# MAGIC
# MAGIC **Benefits:**
# MAGIC - ✅ Simpler to read
# MAGIC - ✅ Less boilerplate
# MAGIC - ✅ Same functionality
# MAGIC - ✅ Still production-ready
