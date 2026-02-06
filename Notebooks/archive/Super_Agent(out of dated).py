# Databricks notebook source
# DBTITLE 1,Install Packages
# MAGIC %pip install databricks-langchain databricks-vectorsearch langgraph

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

"""
Super Agent - Multi-Agent System Orchestrator

This notebook implements the Super Agent that orchestrates:
1. Clarification Agent - Validates query clarity
2. Planning Agent - Creates execution plan and identifies relevant spaces
3. SQL Synthesis Agent - Generates SQL (fast or genie route)
4. SQL Execution Agent - Executes SQL and returns results

The Super Agent uses LangGraph to manage state and agent transitions.
"""

import json
from typing import Dict, List, Optional, Any, Annotated, Literal, Generator
from typing_extensions import TypedDict
import operator
from uuid import uuid4

# COMMAND ----------

# DBTITLE 1,Configuration
# Configuration
CATALOG = "yyang"
SCHEMA = "multi_agent_genie"
TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"
VECTOR_SEARCH_INDEX = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks_vs_index"
LLM_ENDPOINT_CLARIFICATION = "databricks-claude-haiku-4-5"
LLM_ENDPOINT_PLANNING = "databricks-claude-haiku-4-5"
LLM_ENDPOINT_SQL_SYNTHESIS = "databricks-claude-sonnet-4-5"  # More powerful for SQL synthesis

print("="*80)
print("SUPER AGENT CONFIGURATION")
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
from langchain_core.runnables import RunnableLambda
import mlflow

# LangGraph imports
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import MemorySaver

# MLflow ResponsesAgent imports
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    output_to_responses_items_stream,
    to_chat_completions_input,
)

print("✓ All dependencies imported successfully")

# COMMAND ----------

# DBTITLE 1,Helper Function - Query Delta Table
def query_delta_table(table_name: str, filter_field: str, filter_value: str, select_fields: List[str] = None) -> Any:
    """
    Query a delta table with a filter condition.
    
    Args:
        table_name: Full table name (catalog.schema.table)
        filter_field: Field name to filter on
        filter_value: Value to filter by
        select_fields: List of fields to select (None = all fields)
    
    Returns:
        Spark DataFrame with query results
    """
    if select_fields:
        fields_str = ", ".join(select_fields)
    else:
        fields_str = "*"
    
    df = spark.sql(f"""
        SELECT {fields_str}
        FROM {table_name}
        WHERE {filter_field} = '{filter_value}'
    """)
    
    return df

# Load space summaries for context
space_summary_df = query_delta_table(
    table_name=TABLE_NAME,
    filter_field="chunk_type",
    filter_value="space_summary",
    select_fields=["space_id", "space_title", "searchable_content"]
)

# Convert to context dictionary
space_summary_list = space_summary_df.collect()
context = {}
for row in space_summary_list:
    space_id = row["space_id"]
    context[space_id] = row["searchable_content"]

print(f"✓ Loaded {len(context)} Genie spaces for context")

# COMMAND ----------

# DBTITLE 1,Define Agent State
class AgentState(TypedDict):
    """State that flows through the multi-agent system"""
    # Input
    original_query: str
    
    # Clarification
    question_clear: bool
    clarification_needed: Optional[str]
    clarification_options: Optional[List[str]]
    
    # Planning
    sub_questions: Optional[List[str]]
    requires_multiple_spaces: Optional[bool]
    relevant_space_ids: Optional[List[str]]
    relevant_spaces: Optional[List[Dict[str, Any]]]
    vector_search_relevant_spaces_info: Optional[List[Dict[str, str]]]
    requires_join: Optional[bool]
    join_strategy: Optional[str]  # "table_route" or "genie_route"
    execution_plan: Optional[str]
    genie_route_plan: Optional[Dict[str, str]]
    
    # SQL Synthesis
    sql_query: Optional[str]
    synthesis_error: Optional[str]
    
    # Execution
    execution_result: Optional[Dict[str, Any]]
    execution_error: Optional[str]
    
    # Control flow
    next_agent: Optional[str]
    messages: Annotated[List, operator.add]
    
print("✓ Agent State defined")

# COMMAND ----------

# DBTITLE 1,Agent 1: Clarification Agent
def clarification_agent_node(state: AgentState) -> AgentState:
    """
    Check if the user query is clear and answerable.
    If not, provide clarification options.
    """
    print("\n" + "="*80)
    print("🔍 CLARIFICATION AGENT")
    print("="*80)
    
    query = state["original_query"]
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
    
    clarity_prompt = f"""
Analyze the following question for clarity and specificity based on the context.

Question: {query}

Context: {json.dumps(context, indent=2)}

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
    
    response = llm.invoke(clarity_prompt)
    json_str = response.content.strip('```json').strip('```')
    
    try:
        clarity_result = json.loads(json_str)
        state["question_clear"] = clarity_result.get("question_clear", True)
        state["clarification_needed"] = clarity_result.get("clarification_needed")
        state["clarification_options"] = clarity_result.get("clarification_options")
        
        if state["question_clear"]:
            print("✓ Query is clear - proceeding to planning")
            state["next_agent"] = "planning"
        else:
            print("⚠ Query needs clarification")
            state["next_agent"] = "end"
            
        state["messages"].append(
            SystemMessage(content=f"Clarification result: {json.dumps(clarity_result, indent=2)}")
        )
        
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing clarification response: {e}")
        # Default to clear if parsing fails
        state["question_clear"] = True
        state["next_agent"] = "planning"
    
    return state

# COMMAND ----------

# DBTITLE 1,Agent 2: Planning Agent
def planning_agent_node(state: AgentState) -> AgentState:
    """
    Create execution plan by:
    1. Searching for relevant Genie spaces
    2. Analyzing query requirements
    3. Determining execution strategy
    """
    print("\n" + "="*80)
    print("📋 PLANNING AGENT")
    print("="*80)
    
    query = state["original_query"]
    
    # Step 1: Vector Search for relevant spaces
    print("Step 1: Searching for relevant Genie spaces...")
    vs_tool = VectorSearchRetrieverTool(
        index_name=VECTOR_SEARCH_INDEX,
        num_results=5,
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
    
    state["relevant_spaces"] = relevant_spaces
    print(f"✓ Found {len(relevant_spaces)} relevant spaces")
    
    # Step 2: Create execution plan
    print("Step 2: Creating execution plan...")
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING)
    
    planning_prompt = f"""
You are a query planning expert. Analyze the following question and create an execution plan.

Question: {query}

Potentially relevant Genie spaces:
{json.dumps(relevant_spaces, indent=2)}

Break down the question and determine:
1. What are the sub-questions or analytical components?
2. How many Genie spaces are needed to answer completely? (List their space_ids)
3. If multiple spaces are needed, do we need to JOIN data across them? Reasoning whether the sub-questions are totally independent without joining need.
    - JOIN needed: E.g., "How many active plan members over 50 are on Lexapro?" requires joining member data with pharmacy claims.
    - No need for JOIN: E.g., "How many active plan members over 50? How much total cost for all Lexapro claims?" - Two independent questions.
4. If JOIN is needed, what's the best strategy:
    - "table_route": Directly synthesize SQL across multiple tables
    - "genie_route": Query each Genie Space Agent separately, then combine SQL queries
    - If user explicitly asks for "genie_route", use it; otherwise, use "table_route"
5. Execution plan: A brief description of how to execute the plan.
    - For genie_route: Return {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}}
    - Each partial_question should be similar to original but scoped to that space
    - Add "Please limit to top 10 rows" to each partial question

Return your analysis as JSON:
{{
    "original_query": "{query}",
    "vector_search_relevant_spaces_info": {[(sp['space_id'], sp['space_title']) for sp in relevant_spaces]},
    "question_clear": true,
    "sub_questions": ["sub-question 1", "sub-question 2", ...],
    "requires_multiple_spaces": true/false,
    "relevant_space_ids": ["space_id_1", "space_id_2", ...],
    "requires_join": true/false,
    "join_strategy": "table_route" or "genie_route" or null,
    "execution_plan": "Brief description of execution plan",
    "genie_route_plan": {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}} or null
}}

Only return valid JSON, no explanations.
"""
    
    response = llm.invoke(planning_prompt)
    json_str = response.content.strip('```json').strip('```')
    
    try:
        plan_result = json.loads(json_str)
        
        # Update state with plan
        state["sub_questions"] = plan_result.get("sub_questions", [])
        state["requires_multiple_spaces"] = plan_result.get("requires_multiple_spaces", False)
        state["relevant_space_ids"] = plan_result.get("relevant_space_ids", [])
        state["requires_join"] = plan_result.get("requires_join", False)
        state["join_strategy"] = plan_result.get("join_strategy")
        state["execution_plan"] = plan_result.get("execution_plan", "")
        state["genie_route_plan"] = plan_result.get("genie_route_plan")
        state["vector_search_relevant_spaces_info"] = [
            {"space_id": sp["space_id"], "space_title": sp["space_title"]}
            for sp in relevant_spaces
        ]
        
        # Determine next agent
        if state["join_strategy"] == "genie_route":
            print("✓ Plan complete - using SLOW ROUTE (Genie agents)")
            state["next_agent"] = "sql_synthesis_slow"
        else:
            print("✓ Plan complete - using FAST ROUTE (direct SQL synthesis)")
            state["next_agent"] = "sql_synthesis_fast"
        
        state["messages"].append(
            SystemMessage(content=f"Execution plan: {json.dumps(plan_result, indent=2)}")
        )
        
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing planning response: {e}")
        state["next_agent"] = "end"
        state["execution_error"] = f"Planning failed: {str(e)}"
    
    return state

# COMMAND ----------

# DBTITLE 1,Agent 3a: SQL Synthesis Agent (Table Route)
def sql_synthesis_fast_node(state: AgentState) -> AgentState:
    """
    Synthesize SQL using UC function tools (table route).
    Queries metadata intelligently and generates SQL directly.
    """
    print("\n" + "="*80)
    print("⚡ SQL SYNTHESIS AGENT - FAST ROUTE")
    print("="*80)
    
    # Initialize UC Function Client
    client = DatabricksFunctionClient()
    set_uc_function_client(client)
    
    # Initialize LLM (more powerful model for SQL synthesis)
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    
    # Create UC Function Toolkit
    uc_function_names = [
        f"{CATALOG}.{SCHEMA}.get_space_summary",
        f"{CATALOG}.{SCHEMA}.get_table_overview",
        f"{CATALOG}.{SCHEMA}.get_column_detail",
        f"{CATALOG}.{SCHEMA}.get_space_details",
    ]
    
    uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
    tools = uc_toolkit.tools
    
    print(f"✓ Loaded {len(tools)} UC function tools")
    
    # Create SQL Synthesis Agent
    sql_agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=(
            "You are a specialized SQL synthesis agent in a multi-agent system.\n\n"
            "ROLE: You receive execution plans from the planning agent and generate SQL queries.\n\n"

            "## WORKFLOW:\n"
            "1. Review the execution plan and provided metadata\n"
            "2. If metadata is sufficient → Generate SQL immediately\n"
            "3. If insufficient, call UC function tools in this order:\n"
            "   a) get_space_summary for space information\n"
            "   b) get_table_overview for table schemas\n"
            "   c) get_column_detail for specific columns\n"
            "   d) get_space_details ONLY as last resort (token intensive)\n"
            "4. Generate complete, executable SQL\n\n"

            "## UC FUNCTION USAGE:\n"
            "- Pass arguments as JSON array strings: '[\"space_id_1\", \"space_id_2\"]' or 'null'\n"
            "- Only query spaces from execution plan's relevant_space_ids\n"
            "- Use minimal sufficiency: only query what you need\n\n"

            "## OUTPUT REQUIREMENTS:\n"
            "- Generate complete, executable SQL with:\n"
            "  * Proper JOINs based on execution plan\n"
            "  * WHERE clauses for filtering\n"
            "  * Appropriate aggregations\n"
            "  * Clear column aliases\n"
            "  * Always use real column names, never make up ones\n"
            "- Return ONLY the SQL query without explanations or markdown\n"
            "- If SQL cannot be generated, explain what metadata is missing"
        ),
    )
    
    # Prepare execution plan for agent
    plan_summary = {
        "original_query": state["original_query"],
        "vector_search_relevant_spaces_info": state.get("vector_search_relevant_spaces_info", []),
        "relevant_space_ids": state.get("relevant_space_ids", []),
        "execution_plan": state.get("execution_plan", ""),
        "requires_join": state.get("requires_join", False),
        "sub_questions": state.get("sub_questions", [])
    }
    
    # Invoke agent
    agent_message = {
        "messages": [
            {
                "role": "user",
                "content": f"""
Generate a SQL query to answer the question according to the Query Plan:
{json.dumps(plan_summary, indent=2)}

Use your available UC function tools to gather metadata intelligently.
"""
            }
        ]
    }
    
    try:
        print("🤖 Invoking SQL synthesis agent...")
        result = sql_agent.invoke(agent_message)
        
        # Extract SQL from response
        if result and "messages" in result:
            final_content = result["messages"][-1].content
            
            # Extract from markdown if present
            import re
            if "```sql" in final_content.lower():
                sql_match = re.search(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
                if sql_match:
                    final_content = sql_match.group(1).strip()
            elif "```" in final_content:
                sql_match = re.search(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
                if sql_match:
                    final_content = sql_match.group(1).strip()
            
            state["sql_query"] = final_content
            state["next_agent"] = "sql_execution"
            print("✓ SQL query synthesized successfully")
            print(f"SQL Preview: {final_content[:200]}...")
            
        else:
            raise Exception("No SQL generated by agent")
            
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        state["synthesis_error"] = str(e)
        state["next_agent"] = "end"
    
    return state

# COMMAND ----------

# DBTITLE 1,Agent 3b: SQL Synthesis Agent (Genie Route)
def sql_synthesis_slow_node(state: AgentState) -> AgentState:
    """
    Synthesize SQL using Genie agents (genie route).
    Routes partial questions to Genie agents, then combines SQL.
    """
    print("\n" + "="*80)
    print("🐢 SQL SYNTHESIS AGENT - SLOW ROUTE")
    print("="*80)
    
    # Helper function for Genie agents
    def enforce_limit(messages, n=10):
        last = messages[-1] if messages else {"content": ""}
        content = last.get("content", "") if isinstance(last, dict) else last.content
        return f"{content}\n\nPlease limit the result to at most {n} rows."
    
    # Create Genie agents
    print("Step 1: Creating Genie agents...")
    genie_agents_dict = {}
    
    for row in space_summary_df.collect():
        space_id = row["space_id"]
        space_title = row["space_title"]
        searchable_content = row["searchable_content"]
        
        genie_agent = GenieAgent(
            genie_space_id=space_id,
            genie_agent_name=f"Genie_{space_title}",
            description=searchable_content,
            include_context=True,
            message_processor=lambda msgs: enforce_limit(msgs, n=10)
        )
        genie_agents_dict[space_id] = genie_agent
    
    print(f"✓ Created {len(genie_agents_dict)} Genie agents")
    
    # Get routing plan
    genie_route_plan = state.get("genie_route_plan", {})
    
    if not genie_route_plan:
        print("❌ No genie_route_plan found in state")
        state["synthesis_error"] = "No routing plan available for genie route"
        state["next_agent"] = "end"
        return state
    
    # Step 2: Route questions to Genie agents
    print(f"Step 2: Routing {len(genie_route_plan)} questions to Genie agents...")
    sql_fragments = {}
    
    for space_id, partial_question in genie_route_plan.items():
        if space_id not in genie_agents_dict:
            print(f"⚠ Warning: Space {space_id} not found in Genie agents")
            continue
        
        print(f"  Routing to space {space_id}: {partial_question[:60]}...")
        
        try:
            genie_agent = genie_agents_dict[space_id]
            resp = genie_agent.invoke({
                "messages": [{"role": "user", "content": partial_question}]
            })
            
            # Extract SQL from response
            sql = None
            for msg in resp["messages"]:
                if isinstance(msg, AIMessage) and msg.name == "query_sql":
                    sql = msg.content
                    break
            
            if sql:
                sql_fragments[space_id] = {
                    "question": partial_question,
                    "sql": sql
                }
                print(f"  ✓ Got SQL from space {space_id}")
            else:
                print(f"  ⚠ No SQL returned from space {space_id}")
                
        except Exception as e:
            print(f"  ❌ Error querying space {space_id}: {e}")
    
    # Step 3: Combine SQL fragments
    print(f"Step 3: Combining {len(sql_fragments)} SQL fragments...")
    
    if not sql_fragments:
        print("❌ No SQL fragments collected")
        state["synthesis_error"] = "No SQL generated from Genie agents"
        state["next_agent"] = "end"
        return state
    
    # Use LLM to combine SQL fragments
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    
    combine_prompt = f"""
You are an expert SQL developer. Combine the following SQL fragments into a single executable SQL query.

Original Question: {state['original_query']}

Execution Plan: {state.get('execution_plan', '')}

SQL Fragments from Genie Agents:
{json.dumps(sql_fragments, indent=2)}

Generate a complete SQL query that:
1. Combines these fragments with proper JOINs
2. Answers the original question
3. Uses real table and column names from the fragments
4. Includes proper WHERE clauses and aggregations

Return ONLY the final SQL query, no explanations or markdown.
"""
    
    try:
        response = llm.invoke(combine_prompt)
        combined_sql = response.content.strip()
        
        # Clean markdown if present
        import re
        if "```" in combined_sql:
            sql_match = re.search(r'```(?:sql)?\s*(.*?)\s*```', combined_sql, re.IGNORECASE | re.DOTALL)
            if sql_match:
                combined_sql = sql_match.group(1).strip()
        
        state["sql_query"] = combined_sql
        state["next_agent"] = "sql_execution"
        print("✓ SQL fragments combined successfully")
        print(f"SQL Preview: {combined_sql[:200]}...")
        
    except Exception as e:
        print(f"❌ Failed to combine SQL fragments: {e}")
        state["synthesis_error"] = str(e)
        state["next_agent"] = "end"
    
    return state

# COMMAND ----------

# DBTITLE 1,Agent 4: SQL Execution Agent
def sql_execution_node(state: AgentState) -> AgentState:
    """
    Execute the synthesized SQL query on delta tables.
    """
    print("\n" + "="*80)
    print("🚀 SQL EXECUTION AGENT")
    print("="*80)
    
    sql_query = state.get("sql_query")
    
    if not sql_query:
        print("❌ No SQL query to execute")
        state["execution_error"] = "No SQL query provided"
        state["next_agent"] = "end"
        return state
    
    # Extract SQL from markdown if present
    import re
    extracted_sql = sql_query.strip()
    
    if "```sql" in extracted_sql.lower():
        sql_match = re.search(r'```sql\s*(.*?)\s*```', extracted_sql, re.IGNORECASE | re.DOTALL)
        if sql_match:
            extracted_sql = sql_match.group(1).strip()
    elif "```" in extracted_sql:
        sql_match = re.search(r'```\s*(.*?)\s*```', extracted_sql, re.DOTALL)
        if sql_match:
            extracted_sql = sql_match.group(1).strip()
    
    # Add LIMIT if not present
    if "limit" not in extracted_sql.lower():
        extracted_sql = f"{extracted_sql.rstrip(';')} LIMIT 100"
    
    try:
        print("🔍 Executing SQL...")
        print(f"SQL: {extracted_sql[:500]}...")
        
        df = spark.sql(extracted_sql)
        results_list = df.collect()
        row_count = len(results_list)
        columns = df.columns
        
        print(f"✓ Query executed successfully!")
        print(f"📊 Rows returned: {row_count}")
        print(f"📋 Columns: {', '.join(columns)}")
        
        # Format results
        result_data = [row.asDict() for row in results_list]
        
        # Display preview
        print("\n" + "="*80)
        print("📄 RESULTS PREVIEW (first 10 rows)")
        print("="*80)
        df.show(n=min(10, row_count), truncate=False)
        print("="*80)
        
        state["execution_result"] = {
            "success": True,
            "sql": extracted_sql,
            "result": result_data,
            "row_count": row_count,
            "columns": columns
        }
        state["next_agent"] = "end"
        
        state["messages"].append(
            SystemMessage(content=f"Execution successful: {row_count} rows returned")
        )
        
    except Exception as e:
        error_msg = str(e)
        print(f"❌ SQL execution failed: {error_msg}")
        
        state["execution_error"] = error_msg
        state["execution_result"] = {
            "success": False,
            "sql": extracted_sql,
            "error": error_msg
        }
        state["next_agent"] = "end"
        
        state["messages"].append(
            SystemMessage(content=f"Execution failed: {error_msg}")
        )
    
    return state

# COMMAND ----------

# DBTITLE 1,Build LangGraph Workflow
def create_super_agent():
    """
    Create the Super Agent LangGraph workflow.
    """
    print("\n" + "="*80)
    print("🏗️ BUILDING SUPER AGENT WORKFLOW")
    print("="*80)
    
    # Create the graph
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("clarification", clarification_agent_node)
    workflow.add_node("planning", planning_agent_node)
    workflow.add_node("sql_synthesis_fast", sql_synthesis_fast_node)
    workflow.add_node("sql_synthesis_slow", sql_synthesis_slow_node)
    workflow.add_node("sql_execution", sql_execution_node)
    
    # Define routing logic
    def route_after_clarification(state: AgentState) -> str:
        if state.get("question_clear", False):
            return "planning"
        return "end"
    
    def route_after_planning(state: AgentState) -> str:
        next_agent = state.get("next_agent", "end")
        if next_agent == "sql_synthesis_fast":
            return "sql_synthesis_fast"
        elif next_agent == "sql_synthesis_slow":
            return "sql_synthesis_slow"
        return "end"
    
    def route_after_synthesis(state: AgentState) -> str:
        next_agent = state.get("next_agent", "end")
        if next_agent == "sql_execution":
            return "sql_execution"
        return "end"
    
    # Add edges
    workflow.set_entry_point("clarification")
    
    workflow.add_conditional_edges(
        "clarification",
        route_after_clarification,
        {
            "planning": "planning",
            "end": END
        }
    )
    
    workflow.add_conditional_edges(
        "planning",
        route_after_planning,
        {
            "sql_synthesis_fast": "sql_synthesis_fast",
            "sql_synthesis_slow": "sql_synthesis_slow",
            "end": END
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_fast",
        route_after_synthesis,
        {
            "sql_execution": "sql_execution",
            "end": END
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_slow",
        route_after_synthesis,
        {
            "sql_execution": "sql_execution",
            "end": END
        }
    )
    
    workflow.add_edge("sql_execution", END)
    
    # Compile the graph
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)
    
    print("✓ Workflow nodes added:")
    print("  1. Clarification Agent")
    print("  2. Planning Agent")
    print("  3. SQL Synthesis Agent (Table Route)")
    print("  4. SQL Synthesis Agent (Genie Route)")
    print("  5. SQL Execution Agent")
    print("\n✓ Workflow edges and routing configured")
    print("✓ Memory checkpointer enabled")
    print("\n✅ Super Agent workflow compiled successfully!")
    print("="*80)
    
    return app

# Create the Super Agent
super_agent = create_super_agent()

# COMMAND ----------

# DBTITLE 1,ResponsesAgent Wrapper for Deployment
class SuperAgentResponsesAgent(ResponsesAgent):
    """
    Wrapper class to make the Super Agent compatible with Databricks Model Serving.
    
    This class implements the ResponsesAgent interface required for deployment
    to Databricks Model Serving endpoints with proper streaming support.
    """
    
    def __init__(self, agent: CompiledStateGraph):
        """
        Initialize the ResponsesAgent wrapper.
        
        Args:
            agent: The compiled LangGraph workflow
        """
        self.agent = agent
        print("✓ SuperAgentResponsesAgent initialized")
    
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        """
        Make a prediction (non-streaming).
        
        Args:
            request: The request containing input messages
            
        Returns:
            ResponsesAgentResponse with output items
        """
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
        """
        Make a streaming prediction.
        
        Args:
            request: The request containing input messages
            
        Yields:
            ResponsesAgentStreamEvent for each step in the workflow
        """
        # Convert request input to chat completions format
        cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
        
        # Initialize state with messages
        initial_state = {
            "original_query": cc_msgs[-1]["content"] if cc_msgs else "",
            "question_clear": False,
            "messages": [HumanMessage(content=msg["content"]) for msg in cc_msgs if msg["role"] == "user"],
            "next_agent": "clarification"
        }
        
        # Configure with thread ID
        thread_id = request.custom_inputs.get("thread_id", "default") if request.custom_inputs else "default"
        config = {"configurable": {"thread_id": thread_id}}
        
        first_message = True
        seen_ids = set()
        
        # Stream the workflow execution
        # Can adjust `recursion_limit` to limit looping: https://docs.langchain.com/oss/python/langgraph/GRAPH_RECURSION_LIMIT
        for _, events in self.agent.stream(initial_state, config, stream_mode=["updates"]):
            new_msgs = [
                msg
                for v in events.values()
                for msg in v.get("messages", [])
                if hasattr(msg, 'id') and msg.id not in seen_ids
            ]
            
            if first_message:
                seen_ids.update(msg.id for msg in new_msgs[: len(cc_msgs)])
                new_msgs = new_msgs[len(cc_msgs) :]
                first_message = False
            else:
                seen_ids.update(msg.id for msg in new_msgs)
                # Get node name (assumes one name per node)
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


# Create the deployable agent
AGENT = SuperAgentResponsesAgent(super_agent)

print("\n" + "="*80)
print("✅ SUPER AGENT RESPONSES AGENT CREATED")
print("="*80)
print("This agent is now ready for:")
print("  1. Local testing with AGENT.predict()")
print("  2. Logging with mlflow.pyfunc.log_model()")
print("  3. Deployment to Databricks Model Serving")
print("="*80)

# Set the agent for MLflow tracking
mlflow.langchain.autolog()
mlflow.models.set_model(AGENT)

# COMMAND ----------

# DBTITLE 1,Helper Function: Invoke Super Agent
def invoke_super_agent(query: str, thread_id: str = "default") -> Dict[str, Any]:
    """
    Invoke the Super Agent with a user query.
    
    Args:
        query: User's question
        thread_id: Thread ID for conversation tracking (default: "default")
    
    Returns:
        Final state with execution results
    """
    print("\n" + "="*80)
    print("🚀 INVOKING SUPER AGENT")
    print("="*80)
    print(f"Query: {query}")
    print(f"Thread ID: {thread_id}")
    print("="*80)
    
    # Initialize state
    initial_state = {
        "original_query": query,
        "question_clear": False,
        "messages": [HumanMessage(content=query)],
        "next_agent": "clarification"
    }
    
    # Configure with thread
    config = {"configurable": {"thread_id": thread_id}}
    
    # Enable MLflow tracing
    mlflow.langchain.autolog()
    
    # Invoke the workflow
    final_state = super_agent.invoke(initial_state, config)
    
    print("\n" + "="*80)
    print("✅ SUPER AGENT EXECUTION COMPLETE")
    print("="*80)
    
    return final_state

# COMMAND ----------

# DBTITLE 1,Helper Function: Display Results
def display_results(final_state: Dict[str, Any]):
    """
    Display the results from the Super Agent execution.
    """
    print("\n" + "="*80)
    print("📊 FINAL RESULTS")
    print("="*80)
    
    # Query info
    print(f"\n🔍 Original Query:")
    print(f"  {final_state['original_query']}")
    
    # Clarification
    print(f"\n✓ Clarification:")
    if final_state.get('question_clear'):
        print("  Query is clear")
    else:
        print("  ⚠ Clarification needed:")
        print(f"    {final_state.get('clarification_needed', 'N/A')}")
        if final_state.get('clarification_options'):
            print("  Options:")
            for opt in final_state['clarification_options']:
                print(f"    - {opt}")
        return
    
    # Planning
    print(f"\n📋 Execution Plan:")
    print(f"  Strategy: {final_state.get('join_strategy', 'N/A')}")
    print(f"  Multiple Spaces: {final_state.get('requires_multiple_spaces', False)}")
    print(f"  Requires JOIN: {final_state.get('requires_join', False)}")
    print(f"  Relevant Spaces: {len(final_state.get('relevant_space_ids', []))}")
    
    # SQL
    if final_state.get('sql_query'):
        print(f"\n💻 Generated SQL:")
        print("─"*80)
        print(final_state['sql_query'][:1000])
        if len(final_state['sql_query']) > 1000:
            print("... (truncated)")
        print("─"*80)
    
    # Execution
    exec_result = final_state.get('execution_result')
    if exec_result:
        if exec_result.get('success'):
            print(f"\n✅ Execution Successful:")
            print(f"  Rows: {exec_result.get('row_count', 0)}")
            print(f"  Columns: {', '.join(exec_result.get('columns', []))}")
            
            # Show first few results
            results = exec_result.get('result', [])
            if results:
                print(f"\n📄 Sample Results (first 3 rows):")
                for i, row in enumerate(results[:3], 1):
                    print(f"  Row {i}: {row}")
        else:
            print(f"\n❌ Execution Failed:")
            print(f"  Error: {exec_result.get('error', 'Unknown error')}")
    
    # Errors
    if final_state.get('synthesis_error'):
        print(f"\n❌ Synthesis Error: {final_state['synthesis_error']}")
    if final_state.get('execution_error'):
        print(f"\n❌ Execution Error: {final_state['execution_error']}")
    
    print("\n" + "="*80)

# COMMAND ----------

# DBTITLE 1,Test Super Agent
# Example test query
test_query = "What is the average cost of medical claims in 2024?"

# Invoke Super Agent
final_state = invoke_super_agent(test_query, thread_id="test_001")

# Display results
display_results(final_state)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Additional Test Cases

# COMMAND ----------

# DBTITLE 1,Test Case 1: Simple Single-Space Query
test_query_1 = "How many patients are in the dataset?"
result_1 = invoke_super_agent(test_query_1, thread_id="test_simple")
display_results(result_1)

# COMMAND ----------

# DBTITLE 1,Test Case 2: Multi-Space Query with JOIN (Table Route)
test_query_2 = "What is the average cost of medical claims for patients diagnosed with diabetes?"
result_2 = invoke_super_agent(test_query_2, thread_id="test_multi_fast")
display_results(result_2)

# COMMAND ----------

# DBTITLE 1,Test Case 3: Multi-Space Query with JOIN (Genie Route - Explicit)
test_query_3 = "What is the average cost of medical claims for patients diagnosed with diabetes? Use genie route"
result_3 = invoke_super_agent(test_query_3, thread_id="test_multi_slow")
display_results(result_3)

# COMMAND ----------

# DBTITLE 1,Test Case 4: Complex Multi-Space Query
test_query_4 = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?"
result_4 = invoke_super_agent(test_query_4, thread_id="test_complex")
display_results(result_4)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test ResponsesAgent API

# COMMAND ----------

# DBTITLE 1,Test ResponsesAgent Predict (Non-Streaming)
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentInput

# Create a request in ResponsesAgent format
test_request = ResponsesAgentRequest(
    input=[
        ResponsesAgentInput(role="user", content="How many patients are in the dataset?")
    ],
    custom_inputs={"thread_id": "test_responses_001"}
)

# Test predict (non-streaming)
print("Testing ResponsesAgent.predict()...")
response = AGENT.predict(test_request)

print("\n" + "="*80)
print("ResponsesAgent Output:")
print("="*80)
for item in response.output:
    print(f"\nRole: {item.role}")
    print(f"Content: {item.content}")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Test ResponsesAgent Predict Stream
# Create a request
test_request_stream = ResponsesAgentRequest(
    input=[
        ResponsesAgentInput(role="user", content="What is the average cost of medical claims?")
    ],
    custom_inputs={"thread_id": "test_responses_stream_001"}
)

# Test predict_stream
print("Testing ResponsesAgent.predict_stream()...")
print("\n" + "="*80)
print("Streaming Events:")
print("="*80)

for event in AGENT.predict_stream(test_request_stream):
    print(event.model_dump(exclude_none=True))
    print("-"*80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Workflow Visualization

# COMMAND ----------

# DBTITLE 1,Visualize Super Agent Workflow
try:
    from IPython.display import Image, display
    
    # Get the graph visualization
    graph_image = super_agent.get_graph().draw_mermaid_png()
    display(Image(graph_image))
except Exception as e:
    print(f"Could not generate graph visualization: {e}")
    print("\nWorkflow structure:")
    print("  User Query → Clarification → Planning → SQL Synthesis → SQL Execution → Results")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC
# MAGIC ```
# MAGIC ===================================================================================
# MAGIC SUPER AGENT - MULTI-AGENT SYSTEM ORCHESTRATOR
# MAGIC ===================================================================================
# MAGIC
# MAGIC This notebook implements a complete multi-agent system using LangGraph to orchestrate
# MAGIC intelligent query processing, SQL synthesis, and execution.
# MAGIC
# MAGIC ARCHITECTURE:
# MAGIC ══════════════
# MAGIC
# MAGIC ┌─────────────────────┐
# MAGIC │    User Query       │
# MAGIC └──────────┬──────────┘
# MAGIC            ↓
# MAGIC ┌─────────────────────────────────────────┐
# MAGIC │        SUPER AGENT (LangGraph)          │
# MAGIC │     State Management & Orchestration    │
# MAGIC └──────────┬──────────────────────────────┘
# MAGIC            ↓
# MAGIC ┌─────────────────────┐
# MAGIC │ 1. CLARIFICATION    │ ← Validates query clarity
# MAGIC │    AGENT            │   Requests clarification if needed
# MAGIC └──────────┬──────────┘
# MAGIC            ↓
# MAGIC ┌─────────────────────┐
# MAGIC │ 2. PLANNING         │ ← Analyzes query
# MAGIC │    AGENT            │   Vector search for spaces
# MAGIC │                     │   Creates execution plan
# MAGIC │                     │   Determines fast/genie route
# MAGIC └──────────┬──────────┘
# MAGIC            ↓
# MAGIC       ┌────┴────┐
# MAGIC       ↓         ↓
# MAGIC   FAST ROUTE  SLOW ROUTE
# MAGIC       ↓         ↓
# MAGIC ┌─────────────────────┐  ┌─────────────────────┐
# MAGIC │ 3a. SQL SYNTHESIS   │  │ 3b. SQL SYNTHESIS   │
# MAGIC │     (UC Tools)      │  │     (Genie Agents)  │
# MAGIC │                     │  │                     │
# MAGIC │ - Query metadata    │  │ - Route to Genies   │
# MAGIC │ - Direct SQL gen    │  │ - Combine SQL       │
# MAGIC └──────────┬──────────┘  └──────────┬──────────┘
# MAGIC            └────┬────────────────────┘
# MAGIC                 ↓
# MAGIC        ┌─────────────────────┐
# MAGIC        │ 4. SQL EXECUTION    │ ← Execute SQL on Delta
# MAGIC        │    AGENT            │   Return results
# MAGIC        └──────────┬──────────┘
# MAGIC                   ↓
# MAGIC        ┌─────────────────────┐
# MAGIC        │    Final Answer     │
# MAGIC        └─────────────────────┘
# MAGIC
# MAGIC KEY FEATURES:
# MAGIC ══════════════
# MAGIC
# MAGIC ✅ LangGraph State Management - Maintains context across agents
# MAGIC ✅ Conditional Routing - Dynamically chooses fast/genie route
# MAGIC ✅ Memory Checkpointing - Supports conversation continuity
# MAGIC ✅ Error Handling - Graceful degradation at each stage
# MAGIC ✅ MLflow Tracing - Full observability of agent execution
# MAGIC ✅ Modular Design - Easy to extend with new agents
# MAGIC
# MAGIC COMPONENTS INTEGRATED:
# MAGIC ═══════════════════════
# MAGIC
# MAGIC 1. ✅ Clarification Agent (Claude Haiku) - Fast query validation
# MAGIC 2. ✅ Planning Agent (Claude Haiku) - Query analysis & routing
# MAGIC 3. ✅ SQL Synthesis Fast (Claude Sonnet + UC Tools) - Direct SQL generation
# MAGIC 4. ✅ SQL Synthesis Slow (Claude Haiku + Genie Agents) - Multi-agent SQL
# MAGIC 5. ✅ SQL Execution Agent - Delta table query execution
# MAGIC
# MAGIC USAGE:
# MAGIC ══════
# MAGIC
# MAGIC # Simple invocation
# MAGIC final_state = invoke_super_agent("Your question here", thread_id="session_123")
# MAGIC display_results(final_state)
# MAGIC
# MAGIC # Access results programmatically
# MAGIC if final_state['execution_result']['success']:
# MAGIC     data = final_state['execution_result']['result']
# MAGIC     sql = final_state['sql_query']
# MAGIC
# MAGIC NEXT STEPS:
# MAGIC ═══════════
# MAGIC
# MAGIC 1. Deploy to Databricks as a ResponsesAgent endpoint
# MAGIC 2. Add SQL validation agent (optional)
# MAGIC 3. Integrate with Genie UI
# MAGIC 4. Add retry logic and error recovery
# MAGIC 5. Monitor with MLflow in production
# MAGIC
# MAGIC ===================================================================================
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## Log and Deploy Agent

# COMMAND ----------

# DBTITLE 1,Prepare Resources for Automatic Authentication
"""
Prepare Databricks resources for automatic authentication passthrough.

This enables secure access to UC functions, Genie spaces, and tables
when the agent is deployed to Model Serving.
"""

from mlflow.models.resources import (
    DatabricksFunction,
    DatabricksServingEndpoint,
    DatabricksSQLWarehouse,
    DatabricksTable
)

# TODO: Update these with your specific resource names
# List all resources the agent needs access to
resources = [
    # Foundation model endpoints
    DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT_SQL_SYNTHESIS),
    DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT_PLANNING),
    DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT_CLARIFICATION),
    
    # UC Functions for SQL synthesis
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_summary"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_table_overview"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_column_detail"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_details"),
    
    # TODO: Add SQL Warehouse and tables (update with your warehouse_id)
    # DatabricksSQLWarehouse(warehouse_id="<your_warehouse_id>"),
    # DatabricksTable(table_name=f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"),
    
    # TODO: If using Genie spaces, add them here
    # from mlflow.models.resources import DatabricksGenieSpace
    # DatabricksGenieSpace(genie_space_id="<your_genie_space_id>"),
]

print("✓ Resources prepared for automatic authentication:")
for resource in resources:
    print(f"  - {resource}")

# COMMAND ----------

# DBTITLE 1,Log Agent to MLflow
"""
Log the agent to MLflow for deployment.

This creates a logged model that can be:
1. Validated before deployment
2. Registered to Unity Catalog
3. Deployed to Model Serving
"""

from pkg_resources import get_distribution

# Example input for validation
input_example = {
    "input": [
        {"role": "user", "content": "How many patients are in the dataset?"}
    ]
}

with mlflow.start_run(run_name="super_agent_multi_agent_system"):
    logged_agent_info = mlflow.pyfunc.log_model(
        name="agent",
        python_model=AGENT,
        resources=resources,
        input_example=input_example,
        pip_requirements=[
            f"databricks-connect=={get_distribution('databricks-connect').version}",
            f"mlflow=={get_distribution('mlflow').version}",
            f"databricks-langchain=={get_distribution('databricks-langchain').version}",
            f"langgraph=={get_distribution('langgraph').version}",
            "langchain",
            "langchain-core",
        ],
        signature=mlflow.models.infer_signature(
            model_input=input_example,
            model_output={"output": [{"role": "assistant", "content": "Sample response"}]}
        )
    )

print("\n" + "="*80)
print("✅ AGENT LOGGED TO MLFLOW")
print("="*80)
print(f"Run ID: {logged_agent_info.run_id}")
print(f"Model URI: {logged_agent_info.model_uri}")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Pre-Deployment Validation
"""
Validate the logged model before deployment.

This ensures the model can be loaded and run correctly.
"""

print("Testing logged model...")
print("="*80)

test_output = mlflow.models.predict(
    model_uri=f"runs:/{logged_agent_info.run_id}/agent",
    input_data=input_example,
    env_manager="uv",
)

print("\nValidation Output:")
print(test_output)
print("\n" + "="*80)
print("✅ Pre-deployment validation successful!")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Register Model to Unity Catalog
"""
Register the model to Unity Catalog for governance and versioning.
"""

mlflow.set_registry_uri("databricks-uc")

# TODO: Update with your catalog, schema, and model name
UC_CATALOG = CATALOG  # or specify a different catalog for models
UC_SCHEMA = SCHEMA    # or specify a different schema for models
MODEL_NAME = "super_agent_multi_agent_system"
UC_MODEL_NAME = f"{UC_CATALOG}.{UC_SCHEMA}.{MODEL_NAME}"

print(f"Registering model to Unity Catalog: {UC_MODEL_NAME}")

# Register the model to UC
uc_registered_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri, 
    name=UC_MODEL_NAME,
    tags={
        "type": "multi_agent_system",
        "framework": "langgraph",
        "components": "clarification,planning,sql_synthesis,execution"
    }
)

print("\n" + "="*80)
print("✅ MODEL REGISTERED TO UNITY CATALOG")
print("="*80)
print(f"Model Name: {UC_MODEL_NAME}")
print(f"Version: {uc_registered_model_info.version}")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Deploy Agent to Model Serving
"""
Deploy the agent to Databricks Model Serving.

This creates an endpoint that can be queried via REST API or AI Playground.
"""

from databricks import agents

print(f"Deploying {UC_MODEL_NAME} version {uc_registered_model_info.version}...")
print("="*80)

deployment_info = agents.deploy(
    UC_MODEL_NAME, 
    uc_registered_model_info.version,
    tags={
        "endpointSource": "super_agent_notebook",
        "system": "multi_agent_genie"
    },
    deploy_feedback_model=False  # Set to True if you want feedback collection
)

print("\n" + "="*80)
print("✅ AGENT DEPLOYED TO MODEL SERVING")
print("="*80)
print(f"Endpoint Name: {UC_MODEL_NAME}")
print("\nNext Steps:")
print("  1. Test in AI Playground")
print("  2. Share with stakeholders for feedback")
print("  3. Integrate into production applications")
print("  4. Monitor with MLflow tracing")
print("="*80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Testing the Deployed Endpoint

# COMMAND ----------

# DBTITLE 1,Test Deployed Endpoint via API
"""
Test the deployed endpoint using the ChatDatabricks client.
"""

from databricks_langchain import ChatDatabricks

# Create client for the deployed endpoint
deployed_agent = ChatDatabricks(
    endpoint=UC_MODEL_NAME,
    use_responses_api=True
)

# Test query
test_query = "How many patients are in the dataset?"

print(f"Testing deployed endpoint: {UC_MODEL_NAME}")
print(f"Query: {test_query}")
print("="*80)

response = deployed_agent.invoke([{"role": "user", "content": test_query}])

print("\nDeployed Agent Response:")
print(response.content)
print("="*80)

# COMMAND ----------


