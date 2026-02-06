# Databricks notebook source
# MAGIC %md
# MAGIC # Multi-Agent System for Genie Space Querying with LangGraph Supervisor
# MAGIC
# MAGIC This notebook implements a sophisticated multi-agent system using Mosaic AI Agent Framework and LangGraph's `create_supervisor` pattern.
# MAGIC
# MAGIC ## 🎯 System Architecture
# MAGIC
# MAGIC The system uses a **supervisor-based multi-agent architecture** with 6 specialized agents:
# MAGIC
# MAGIC 1. **Clarification Agent**: 
# MAGIC    - Validates query clarity with lenient approach
# MAGIC    - Dynamically loads space context from Delta table
# MAGIC    - Requests clarification only when truly needed (max 1 time)
# MAGIC    - Preserves original query through clarification flow
# MAGIC
# MAGIC 2. **Planning Agent**: 
# MAGIC    - Analyzes queries and searches vector index for relevant spaces
# MAGIC    - Creates detailed execution plans with genie_route_plan
# MAGIC    - Determines optimal strategy: table_route or genie_route
# MAGIC    - Identifies join requirements and sub-questions
# MAGIC
# MAGIC 3. **SQL Synthesis Agent (Table Route)**: 
# MAGIC    - Generates SQL using UC metadata functions intelligently
# MAGIC    - Queries metadata on-demand with minimal sufficiency
# MAGIC    - Best for complex joins across multiple tables
# MAGIC    - Returns SQL with detailed explanation
# MAGIC
# MAGIC 4. **SQL Synthesis Agent (Genie Route)**: 
# MAGIC    - Routes queries to individual Genie Space Agents
# MAGIC    - **OPTIMIZED**: Only creates Genie agents for relevant spaces (not all spaces)
# MAGIC    - Combines SQL fragments from multiple Genie agents
# MAGIC    - Includes retry logic and disaster recovery
# MAGIC    - Best for decomposable queries across independent spaces
# MAGIC
# MAGIC 5. **SQL Execution Agent**: 
# MAGIC    - Executes generated SQL queries on delta tables
# MAGIC    - Returns structured results with success status
# MAGIC    - Comprehensive error handling and formatting
# MAGIC    - Supports multiple output formats (dict, json, markdown)
# MAGIC
# MAGIC 6. **Result Summarize Agent** (NEW!): 
# MAGIC    - Generates comprehensive natural language summary
# MAGIC    - Includes: original query, plan, SQL, results, errors
# MAGIC    - Formats results in user-friendly way
# MAGIC    - **MANDATORY**: Every workflow ends with this agent
# MAGIC
# MAGIC ## 🚀 Key Features
# MAGIC
# MAGIC - ✅ **Supervisor Pattern**: Uses LangGraph's `create_supervisor` for intelligent agent routing
# MAGIC - ✅ **Comprehensive State**: Full observability with AgentState TypedDict
# MAGIC - ✅ **Dynamic Context Loading**: Clarification agent loads fresh space context (no redeployment needed)
# MAGIC - ✅ **Optimized Genie Route**: Only creates Genie agents for relevant spaces
# MAGIC - ✅ **Clarification Flow**: Preserves context and combines original query with clarification
# MAGIC - ✅ **Result Summarization**: Every workflow ends with comprehensive user-friendly summary
# MAGIC - ✅ **Streaming Support**: Full streaming via ResponsesAgent wrapper
# MAGIC - ✅ **Error Handling**: Graceful handling with explanations at every step
# MAGIC
# MAGIC ## 📋 Prerequisites
# MAGIC
# MAGIC Before running this notebook, ensure:
# MAGIC 1. **Unity Catalog Functions** registered:
# MAGIC    - `get_space_summary` - High-level space information
# MAGIC    - `get_table_overview` - Table-level metadata
# MAGIC    - `get_column_detail` - Column-level metadata
# MAGIC    - `get_space_details` - Complete metadata (last resort)
# MAGIC 2. **Genie Spaces** created and configured
# MAGIC 3. **Vector Search Index** created for space summaries
# MAGIC 4. **Delta Table** with enriched genie docs chunks

# COMMAND ----------

# MAGIC %pip install -U -qqq langgraph-supervisor==0.0.31 mlflow[databricks] databricks-langchain==0.12.1 databricks-vectorsearch==0.63 databricks-agents uv 
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Define the Multi-Agent System

# COMMAND ----------

# MAGIC %%writefile agent.py
# MAGIC import json
# MAGIC import re
# MAGIC from typing import Dict, List, Optional, Any, Generator, Annotated
# MAGIC from typing_extensions import TypedDict
# MAGIC from uuid import uuid4
# MAGIC import operator
# MAGIC
# MAGIC import mlflow
# MAGIC from databricks_langchain import (
# MAGIC     ChatDatabricks,
# MAGIC     DatabricksFunctionClient,
# MAGIC     UCFunctionToolkit,
# MAGIC     VectorSearchRetrieverTool,
# MAGIC     set_uc_function_client,
# MAGIC )
# MAGIC from databricks_langchain.genie import GenieAgent
# MAGIC from langchain_core.runnables import Runnable, RunnableLambda
# MAGIC from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
# MAGIC from langchain.agents import create_agent
# MAGIC from langgraph.graph.state import CompiledStateGraph
# MAGIC from langgraph_supervisor import create_supervisor
# MAGIC from mlflow.pyfunc import ResponsesAgent
# MAGIC from mlflow.types.responses import (
# MAGIC     ResponsesAgentRequest,
# MAGIC     ResponsesAgentResponse,
# MAGIC     ResponsesAgentStreamEvent,
# MAGIC     output_to_responses_items_stream,
# MAGIC     to_chat_completions_input,
# MAGIC )
# MAGIC from pydantic import BaseModel
# MAGIC from functools import partial
# MAGIC
# MAGIC client = DatabricksFunctionClient()
# MAGIC set_uc_function_client(client)
# MAGIC
# MAGIC ########################################
# MAGIC # Configuration
# MAGIC ########################################
# MAGIC
# MAGIC # TODO: Update these configuration values
# MAGIC CATALOG = "yyang"
# MAGIC SCHEMA = "multi_agent_genie"
# MAGIC TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"
# MAGIC VECTOR_SEARCH_INDEX = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks_vs_index"
# MAGIC LLM_ENDPOINT_NAME = "databricks-claude-sonnet-4-5"
# MAGIC LLM_ENDPOINT_PLANNING = "databricks-claude-haiku-4-5"  # Planning can use lighter model
# MAGIC LLM_ENDPOINT_CLARIFICATION = "databricks-claude-haiku-4-5"  # Fast model for clarity checks
# MAGIC LLM_ENDPOINT_SQL_SYNTHESIS = "databricks-claude-sonnet-4-5"  # Powerful model for SQL
# MAGIC LLM_ENDPOINT_SUMMARIZE = "databricks-claude-haiku-4-5"  # Fast model for summarization
# MAGIC
# MAGIC ########################################
# MAGIC # Unity Catalog Functions (Prerequisites)
# MAGIC ########################################
# MAGIC
# MAGIC # IMPORTANT: Before running this agent, ensure UC functions are registered:
# MAGIC # The following UC functions must exist in your catalog:
# MAGIC # 1. {CATALOG}.{SCHEMA}.get_space_summary - High-level space information
# MAGIC # 2. {CATALOG}.{SCHEMA}.get_table_overview - Table-level metadata
# MAGIC # 3. {CATALOG}.{SCHEMA}.get_column_detail - Column-level metadata
# MAGIC # 4. {CATALOG}.{SCHEMA}.get_space_details - Complete metadata (last resort)
# MAGIC #
# MAGIC # To register these functions, see the Super_Agent_hybrid.py notebook
# MAGIC # or the register_uc_functions.py script for the complete SQL CREATE FUNCTION statements.
# MAGIC #
# MAGIC # These functions query the enriched_genie_docs_chunks table at different granularities
# MAGIC # and are used by the SQL Synthesis Table Route Agent to gather metadata intelligently.
# MAGIC
# MAGIC ########################################
# MAGIC # Agent State Definition
# MAGIC ########################################
# MAGIC
# MAGIC class AgentState(TypedDict):
# MAGIC     """
# MAGIC     Explicit state that flows through the multi-agent system.
# MAGIC     This provides full observability and makes debugging easier.
# MAGIC     """
# MAGIC     # Input
# MAGIC     original_query: str
# MAGIC     
# MAGIC     # Clarification
# MAGIC     question_clear: bool
# MAGIC     clarification_needed: Optional[str]
# MAGIC     clarification_options: Optional[List[str]]
# MAGIC     clarification_count: Optional[int]  # Track clarification attempts (max 1)
# MAGIC     user_clarification_response: Optional[str]  # User's response to clarification
# MAGIC     clarification_message: Optional[str]  # The clarification question asked by agent
# MAGIC     combined_query_context: Optional[str]  # Combined context: original + clarification + response
# MAGIC     
# MAGIC     # Planning
# MAGIC     plan: Optional[Dict[str, Any]]
# MAGIC     sub_questions: Optional[List[str]]
# MAGIC     requires_multiple_spaces: Optional[bool]
# MAGIC     relevant_space_ids: Optional[List[str]]
# MAGIC     relevant_spaces: Optional[List[Dict[str, Any]]]
# MAGIC     vector_search_relevant_spaces_info: Optional[List[Dict[str, str]]]
# MAGIC     requires_join: Optional[bool]
# MAGIC     join_strategy: Optional[str]  # "table_route" or "genie_route"
# MAGIC     execution_plan: Optional[str]
# MAGIC     genie_route_plan: Optional[Dict[str, str]]
# MAGIC     
# MAGIC     # SQL Synthesis
# MAGIC     sql_query: Optional[str]
# MAGIC     sql_synthesis_explanation: Optional[str]  # Agent's explanation/reasoning
# MAGIC     synthesis_error: Optional[str]
# MAGIC     has_sql: Optional[bool]
# MAGIC     
# MAGIC     # Execution
# MAGIC     execution_result: Optional[Dict[str, Any]]
# MAGIC     execution_error: Optional[str]
# MAGIC     
# MAGIC     # Summary
# MAGIC     final_summary: Optional[str]  # Natural language summary of the workflow execution
# MAGIC     
# MAGIC     # Control flow
# MAGIC     next_agent: Optional[str]
# MAGIC     messages: Annotated[List, operator.add]
# MAGIC
# MAGIC ########################################
# MAGIC # Agent Type Definitions
# MAGIC ########################################
# MAGIC
# MAGIC class InCodeSubAgent(BaseModel):
# MAGIC     tools: list[str]
# MAGIC     name: str
# MAGIC     description: str
# MAGIC     system_prompt: Optional[str] = None
# MAGIC
# MAGIC
# MAGIC class GenieSubAgent(BaseModel):
# MAGIC     space_id: str
# MAGIC     name: str
# MAGIC     description: str
# MAGIC
# MAGIC
# MAGIC TOOLS = []
# MAGIC
# MAGIC ########################################
# MAGIC # Helper Functions
# MAGIC ########################################
# MAGIC
# MAGIC def stringify_content(state):
# MAGIC     """Converts list content to JSON string for better parsing."""
# MAGIC     msgs = state["messages"]
# MAGIC     if isinstance(msgs[-1].content, list):
# MAGIC         msgs[-1].content = json.dumps(msgs[-1].content, indent=4)
# MAGIC     return {"messages": msgs}
# MAGIC
# MAGIC
# MAGIC def enforce_limit(messages, n=10):
# MAGIC     """Appends an instruction to the last user message to limit the result size."""
# MAGIC     last = messages[-1] if messages else {"content": ""}
# MAGIC     content = last.get("content", "") if isinstance(last, dict) else last.content
# MAGIC     return f"{content}\n\nPlease limit the result to at most {n} rows."
# MAGIC
# MAGIC
# MAGIC def extract_genie_sql(resp: dict) -> tuple:
# MAGIC     """Extracts thinking, SQL, and answer from Genie agent response."""
# MAGIC     thinking = None
# MAGIC     sql = None
# MAGIC     answer = None
# MAGIC
# MAGIC     for msg in resp["messages"]:
# MAGIC         if isinstance(msg, AIMessage):
# MAGIC             if msg.name == "query_reasoning":
# MAGIC                 thinking = msg.content
# MAGIC             elif msg.name == "query_sql":
# MAGIC                 sql = msg.content
# MAGIC             elif msg.name == "query_result":
# MAGIC                 answer = msg.content
# MAGIC     return thinking, sql, answer
# MAGIC
# MAGIC
# MAGIC def query_delta_table(table_name: str, filter_field: str, filter_value: str, select_fields: List[str] = None):
# MAGIC     """Query a delta table with a filter condition."""
# MAGIC     from pyspark.sql import SparkSession
# MAGIC     spark = SparkSession.builder.getOrCreate()
# MAGIC     
# MAGIC     if select_fields:
# MAGIC         fields_str = ", ".join(select_fields)
# MAGIC     else:
# MAGIC         fields_str = "*"
# MAGIC     
# MAGIC     df = spark.sql(f"""
# MAGIC         SELECT {fields_str}
# MAGIC         FROM {table_name}
# MAGIC         WHERE {filter_field} = '{filter_value}'
# MAGIC     """)
# MAGIC     
# MAGIC     return df
# MAGIC
# MAGIC
# MAGIC def load_space_context(table_name: str) -> Dict[str, str]:
# MAGIC     """
# MAGIC     Load space context from Delta table.
# MAGIC     Called fresh on each request - no caching for dynamic refresh.
# MAGIC     
# MAGIC     Args:
# MAGIC         table_name: Full table name (catalog.schema.table)
# MAGIC         
# MAGIC     Returns:
# MAGIC         Dictionary mapping space_id to searchable_content
# MAGIC     """
# MAGIC     from pyspark.sql import SparkSession
# MAGIC     spark = SparkSession.builder.getOrCreate()
# MAGIC     
# MAGIC     df = spark.sql(f"""
# MAGIC         SELECT space_id, searchable_content
# MAGIC         FROM {table_name}
# MAGIC         WHERE chunk_type = 'space_summary'
# MAGIC     """)
# MAGIC     
# MAGIC     context = {row["space_id"]: row["searchable_content"] 
# MAGIC                for row in df.collect()}
# MAGIC     
# MAGIC     print(f"✓ Loaded {len(context)} Genie spaces for context")
# MAGIC     return context
# MAGIC
# MAGIC
# MAGIC ########################################
# MAGIC # Create Genie Agents
# MAGIC ########################################
# MAGIC
# MAGIC def create_genie_agents_from_relevant_spaces(relevant_spaces: List[Dict[str, Any]]) -> List:
# MAGIC     """
# MAGIC     Create Genie agents as tools only for relevant spaces (not all spaces).
# MAGIC     Uses RunnableLambda wrapper pattern to avoid closure issues.
# MAGIC     
# MAGIC     Args:
# MAGIC         relevant_spaces: List of relevant spaces from PlanningAgent's Vector Search.
# MAGIC                         Each dict should have: space_id, space_title, searchable_content
# MAGIC     
# MAGIC     Returns:
# MAGIC         List of Genie agent tools
# MAGIC     """
# MAGIC     genie_agent_tools = []
# MAGIC     
# MAGIC     print(f"  Creating Genie agent tools for {len(relevant_spaces)} relevant spaces...")
# MAGIC     
# MAGIC     for space in relevant_spaces:
# MAGIC         space_id = space.get("space_id")
# MAGIC         space_title = space.get("space_title", space_id)
# MAGIC         searchable_content = space.get("searchable_content", "")
# MAGIC         
# MAGIC         if not space_id:
# MAGIC             print(f"  ⚠ Warning: Space missing space_id, skipping: {space}")
# MAGIC             continue
# MAGIC         
# MAGIC         genie_agent_name = f"Genie_{space_title}"
# MAGIC         description = searchable_content
# MAGIC         
# MAGIC         # Create Genie agent
# MAGIC         genie_agent = GenieAgent(
# MAGIC             genie_space_id=space_id,
# MAGIC             genie_agent_name=genie_agent_name,
# MAGIC             description=description,
# MAGIC             include_context=True,
# MAGIC             message_processor=lambda msgs: enforce_limit(msgs, n=5)
# MAGIC         )
# MAGIC         
# MAGIC         # Wrap the agent call in a function that only takes a string argument
# MAGIC         # This function also returns a function to avoid closure issues
# MAGIC         def make_agent_invoker(agent):
# MAGIC             return lambda question: agent.invoke(
# MAGIC                 {"messages": [{"role": "user", "content": question}]}
# MAGIC             )
# MAGIC         
# MAGIC         runnable = RunnableLambda(make_agent_invoker(genie_agent))
# MAGIC         runnable.name = genie_agent_name
# MAGIC         runnable.description = description
# MAGIC         
# MAGIC         genie_agent_tools.append(
# MAGIC             runnable.as_tool(
# MAGIC                 name=genie_agent_name,
# MAGIC                 description=description,
# MAGIC                 arg_types={"question": str}
# MAGIC             )
# MAGIC         )
# MAGIC         
# MAGIC         print(f"  ✓ Created Genie agent tool: {genie_agent_name} ({space_id})")
# MAGIC     
# MAGIC     return genie_agent_tools
# MAGIC
# MAGIC
# MAGIC def create_genie_agents(table_name: str) -> tuple:
# MAGIC     """Create Genie agents from space summary data."""
# MAGIC     from pyspark.sql import SparkSession
# MAGIC     spark = SparkSession.builder.getOrCreate()
# MAGIC     
# MAGIC     # Query space summary data
# MAGIC     space_summary_df = query_delta_table(
# MAGIC         table_name=table_name,
# MAGIC         filter_field="chunk_type",
# MAGIC         filter_value="space_summary",
# MAGIC         select_fields=["space_id", "space_title", "searchable_content"]
# MAGIC     )
# MAGIC     
# MAGIC     genie_agents = []
# MAGIC     genie_agent_tools = []
# MAGIC     genie_subagent_configs = []
# MAGIC     
# MAGIC     for row in space_summary_df.collect():
# MAGIC         space_id = row["space_id"]
# MAGIC         space_title = row["space_title"]
# MAGIC         searchable_content = row["searchable_content"]
# MAGIC         genie_agent_name = f"Genie_{space_title}"
# MAGIC         description = searchable_content
# MAGIC         
# MAGIC         # Create Genie agent
# MAGIC         genie_agent = GenieAgent(
# MAGIC             genie_space_id=space_id,
# MAGIC             genie_agent_name=genie_agent_name,
# MAGIC             description=description,
# MAGIC             include_context=True,
# MAGIC             message_processor=lambda msgs: enforce_limit(msgs, n=10)
# MAGIC         )
# MAGIC         
# MAGIC         genie_agents.append(genie_agent)
# MAGIC         
# MAGIC         # Wrap agent for tool use
# MAGIC         def make_agent_invoker(agent):
# MAGIC             return lambda question: agent.invoke(
# MAGIC                 {"messages": [{"role": "user", "content": question}]}
# MAGIC             )
# MAGIC         
# MAGIC         runnable = RunnableLambda(make_agent_invoker(genie_agent))
# MAGIC         runnable.name = genie_agent_name
# MAGIC         runnable.description = description
# MAGIC         
# MAGIC         genie_agent_tools.append(
# MAGIC             runnable.as_tool(
# MAGIC                 name=genie_agent_name,
# MAGIC                 description=description,
# MAGIC                 arg_types={"question": str}
# MAGIC             )
# MAGIC         )
# MAGIC         
# MAGIC         # Store config for supervisor
# MAGIC         genie_subagent_configs.append(
# MAGIC             GenieSubAgent(
# MAGIC                 space_id=space_id,
# MAGIC                 name=genie_agent_name,
# MAGIC                 description=description
# MAGIC             )
# MAGIC         )
# MAGIC     
# MAGIC     return genie_agents, genie_agent_tools, genie_subagent_configs, space_summary_df
# MAGIC
# MAGIC
# MAGIC ########################################
# MAGIC # Create LangGraph Supervisor Agent
# MAGIC ########################################
# MAGIC
# MAGIC def create_langgraph_supervisor(
# MAGIC     llm: Runnable,
# MAGIC     in_code_agents: list[InCodeSubAgent] = [],
# MAGIC     additional_agents: list[Runnable] = [],
# MAGIC ):
# MAGIC     """Create a LangGraph supervisor with specialized agents."""
# MAGIC     agents = []
# MAGIC     agent_descriptions = ""
# MAGIC
# MAGIC     # Process inline code agents
# MAGIC     for agent in in_code_agents:
# MAGIC         agent_descriptions += f"- {agent.name}: {agent.description}\n"
# MAGIC         
# MAGIC         # Handle agents with tools
# MAGIC         if agent.tools:
# MAGIC             uc_toolkit = UCFunctionToolkit(function_names=agent.tools)
# MAGIC             TOOLS.extend(uc_toolkit.tools)
# MAGIC             agent_tools = uc_toolkit.tools
# MAGIC         else:
# MAGIC             agent_tools = []
# MAGIC         
# MAGIC         # Create agent with custom system prompt if provided
# MAGIC         if agent.system_prompt:
# MAGIC             created_agent = create_agent(
# MAGIC                 llm, 
# MAGIC                 tools=agent_tools, 
# MAGIC                 name=agent.name,
# MAGIC                 system_prompt=agent.system_prompt
# MAGIC             )
# MAGIC         else:
# MAGIC             created_agent = create_agent(llm, tools=agent_tools, name=agent.name)
# MAGIC         
# MAGIC         agents.append(created_agent)
# MAGIC
# MAGIC     # Add additional pre-built agents
# MAGIC     for agent in additional_agents:
# MAGIC         agents.append(agent)
# MAGIC         # Extract description from agent if available
# MAGIC         agent_name = getattr(agent, 'name', 'unknown_agent')
# MAGIC         agent_desc = getattr(agent, 'description', 'Additional agent')
# MAGIC         agent_descriptions += f"- {agent_name}: {agent_desc}\n"
# MAGIC
# MAGIC     # Supervisor prompt
# MAGIC     prompt = f"""
# MAGIC You are a supervisor in a multi-agent system for analyzing and querying Genie spaces.
# MAGIC
# MAGIC Your role is to:
# MAGIC 1. Understand the user's query
# MAGIC 2. Route to appropriate agents in the correct sequence
# MAGIC 3. Coordinate handoffs between agents
# MAGIC 4. Ensure comprehensive results are generated
# MAGIC
# MAGIC Available Agents:
# MAGIC {agent_descriptions}
# MAGIC
# MAGIC REQUIRED Workflow (ALL paths must complete ALL steps):
# MAGIC 1. **Clarification Agent**: Validates query clarity first
# MAGIC    - If unclear → Ask for clarification and wait for user response
# MAGIC    - If clear → Proceed to Planning Agent
# MAGIC
# MAGIC 2. **Planning Agent**: Analyzes query and creates execution plan
# MAGIC    - Searches vector index for relevant spaces
# MAGIC    - Determines strategy: table_route or genie_route
# MAGIC    - Creates detailed execution plan
# MAGIC
# MAGIC 3. **SQL Synthesis Agent** (choose ONE based on plan):
# MAGIC    - **Table Route**: Uses UC metadata functions for direct SQL generation
# MAGIC    - **Genie Route**: Routes to multiple Genie agents and combines SQL
# MAGIC    - If synthesis fails → Skip to Result Summarize Agent
# MAGIC
# MAGIC 4. **SQL Execution Agent**: Executes the generated SQL
# MAGIC    - Runs SQL on delta tables
# MAGIC    - Returns structured results with success status
# MAGIC    - If execution fails → Still proceed to Result Summarize Agent
# MAGIC
# MAGIC 5. **Result Summarize Agent** (REQUIRED - ALWAYS call this agent last):
# MAGIC    - Generates comprehensive natural language summary
# MAGIC    - Includes: original query, plan, SQL, results, errors
# MAGIC    - Formats results in user-friendly way
# MAGIC    - This is MANDATORY - every workflow must end here
# MAGIC
# MAGIC CRITICAL RULES:
# MAGIC - ALWAYS route to Result Summarize Agent as the final step
# MAGIC - Result Summarize Agent must receive complete state information
# MAGIC - Never skip the summarization step
# MAGIC - Maintain full context throughout the workflow
# MAGIC
# MAGIC Your coordination ensures users receive:
# MAGIC - Clear execution plan
# MAGIC - Generated SQL query (if successful)
# MAGIC - Query results (if executed)
# MAGIC - Comprehensive summary of the entire workflow
# MAGIC - Proper error handling and explanations
# MAGIC     """
# MAGIC
# MAGIC     return create_supervisor(
# MAGIC         agents=agents,
# MAGIC         model=llm,
# MAGIC         prompt=prompt,
# MAGIC         add_handoff_messages=False,
# MAGIC         output_mode="full_history",
# MAGIC     ).compile()
# MAGIC
# MAGIC
# MAGIC ##########################################
# MAGIC # Wrap LangGraph Supervisor as a ResponsesAgent
# MAGIC ##########################################
# MAGIC
# MAGIC class LangGraphResponsesAgent(ResponsesAgent):
# MAGIC     """
# MAGIC     Wrapper class to make the Supervisor Agent compatible with Databricks Model Serving.
# MAGIC     
# MAGIC     This class implements the ResponsesAgent interface required for deployment
# MAGIC     to Databricks Model Serving endpoints with proper streaming support.
# MAGIC     
# MAGIC     Supports three scenarios:
# MAGIC     1. New query: Fresh start with new original_query
# MAGIC     2. Clarification response: User answering agent's clarification question
# MAGIC     3. Follow-up query: New query with access to previous conversation context
# MAGIC     """
# MAGIC     
# MAGIC     def __init__(self, agent: CompiledStateGraph):
# MAGIC         """
# MAGIC         Initialize the ResponsesAgent wrapper.
# MAGIC         
# MAGIC         Args:
# MAGIC             agent: The compiled LangGraph workflow (supervisor)
# MAGIC         """
# MAGIC         self.agent = agent
# MAGIC
# MAGIC     def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
# MAGIC         """
# MAGIC         Make a prediction (non-streaming).
# MAGIC         
# MAGIC         Args:
# MAGIC             request: The request containing input messages
# MAGIC             
# MAGIC         Returns:
# MAGIC             ResponsesAgentResponse with output items
# MAGIC         """
# MAGIC         outputs = [
# MAGIC             event.item
# MAGIC             for event in self.predict_stream(request)
# MAGIC             if event.type == "response.output_item.done"
# MAGIC         ]
# MAGIC         return ResponsesAgentResponse(output=outputs, custom_outputs=request.custom_inputs)
# MAGIC
# MAGIC     def predict_stream(
# MAGIC         self,
# MAGIC         request: ResponsesAgentRequest,
# MAGIC     ) -> Generator[ResponsesAgentStreamEvent, None, None]:
# MAGIC         """
# MAGIC         Make a streaming prediction.
# MAGIC         
# MAGIC         Handles three scenarios:
# MAGIC         1. New query: Fresh start with new original_query
# MAGIC         2. Clarification response: User answering agent's clarification question
# MAGIC         3. Follow-up query: New query with access to previous conversation context
# MAGIC         
# MAGIC         Args:
# MAGIC             request: The request containing:
# MAGIC                 - input: List of messages (user query is the last message)
# MAGIC                 - custom_inputs: Dict with optional keys:
# MAGIC                     - thread_id (str): Thread identifier for conversation continuity (default: "default")
# MAGIC                     - is_clarification_response (bool): Set to True when user is answering clarification
# MAGIC                     - clarification_count (int): Preserved from previous state
# MAGIC                     - original_query (str): Preserved from previous state for clarification responses
# MAGIC                     - clarification_message (str): Preserved from previous state for clarification responses
# MAGIC             
# MAGIC         Yields:
# MAGIC             ResponsesAgentStreamEvent for each step in the workflow
# MAGIC         """
# MAGIC         # Convert request input to chat completions format
# MAGIC         cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
# MAGIC         
# MAGIC         # Get the latest user message
# MAGIC         latest_query = cc_msgs[-1]["content"] if cc_msgs else ""
# MAGIC         
# MAGIC         # Check if this is a clarification response
# MAGIC         is_clarification_response = request.custom_inputs.get("is_clarification_response", False) if request.custom_inputs else False
# MAGIC         
# MAGIC         # Initialize state based on scenario
# MAGIC         if is_clarification_response:
# MAGIC             # Scenario 2: Clarification Response
# MAGIC             # User is answering the agent's clarification question
# MAGIC             # We need to preserve state from previous call and add user's response
# MAGIC             
# MAGIC             # Get preserved state from custom_inputs (caller must pass these)
# MAGIC             original_query = request.custom_inputs.get("original_query", latest_query)
# MAGIC             clarification_message = request.custom_inputs.get("clarification_message", "")
# MAGIC             clarification_count = request.custom_inputs.get("clarification_count", 1)
# MAGIC             
# MAGIC             initial_state = {
# MAGIC                 # Preserve from previous state
# MAGIC                 "original_query": original_query,  # Keep original unchanged
# MAGIC                 "clarification_message": clarification_message,  # Keep clarification question
# MAGIC                 "clarification_count": clarification_count,  # Keep count
# MAGIC                 
# MAGIC                 # Add user's clarification response
# MAGIC                 "user_clarification_response": latest_query,
# MAGIC                 "question_clear": False,  # Will be set to True by clarification agent
# MAGIC                 
# MAGIC                 # Messages
# MAGIC                 "messages": [HumanMessage(content=f"Clarification response: {latest_query}")],
# MAGIC             }
# MAGIC             
# MAGIC         else:
# MAGIC             # Scenario 1 & 3: New Query or Follow-Up Query
# MAGIC             initial_state = {
# MAGIC                 "original_query": latest_query,
# MAGIC                 "question_clear": False,
# MAGIC                 "messages": [
# MAGIC                     SystemMessage(content="""You are a multi-agent Q&A analysis system.
# MAGIC Your role is to help users query and analyze cross-domain data.
# MAGIC
# MAGIC Guidelines:
# MAGIC - Always explain your reasoning and execution plan
# MAGIC - Validate SQL queries before execution
# MAGIC - Provide clear, comprehensive summaries
# MAGIC - If information is missing, ask for clarification (max once)
# MAGIC - Use UC functions and Genie agents to generate accurate SQL
# MAGIC - Return results with proper context and explanations"""),
# MAGIC                     HumanMessage(content=latest_query)
# MAGIC                 ],
# MAGIC             }
# MAGIC         
# MAGIC         first_message = True
# MAGIC         seen_ids = set()
# MAGIC
# MAGIC         # Stream the workflow execution
# MAGIC         for _, events in self.agent.stream(initial_state, stream_mode=["updates"]):
# MAGIC             new_msgs = [
# MAGIC                 msg
# MAGIC                 for v in events.values()
# MAGIC                 for msg in v.get("messages", [])
# MAGIC                 if hasattr(msg, 'id') and msg.id not in seen_ids
# MAGIC             ]
# MAGIC             if first_message:
# MAGIC                 seen_ids.update(msg.id for msg in new_msgs[: len(cc_msgs)])
# MAGIC                 new_msgs = new_msgs[len(cc_msgs) :]
# MAGIC                 first_message = False
# MAGIC             else:
# MAGIC                 seen_ids.update(msg.id for msg in new_msgs)
# MAGIC                 # Get node name
# MAGIC                 if events:
# MAGIC                     node_name = tuple(events.keys())[0]
# MAGIC                     yield ResponsesAgentStreamEvent(
# MAGIC                         type="response.output_item.done",
# MAGIC                         item=self.create_text_output_item(
# MAGIC                             text=f"<name>{node_name}</name>", id=str(uuid4())
# MAGIC                         ),
# MAGIC                     )
# MAGIC             if len(new_msgs) > 0:
# MAGIC                 yield from output_to_responses_items_stream(new_msgs)
# MAGIC
# MAGIC
# MAGIC #######################################################
# MAGIC # Configure the Multi-Agent System
# MAGIC #######################################################
# MAGIC
# MAGIC # Initialize LLM
# MAGIC llm = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME)
# MAGIC llm_planning = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING)
# MAGIC
# MAGIC # Create Genie agents (for genie route)
# MAGIC genie_agents, genie_agent_tools, genie_subagent_configs, space_summary_df = create_genie_agents(TABLE_NAME)
# MAGIC
# MAGIC # Build space context for clarification and planning
# MAGIC space_summary_list = space_summary_df.collect()
# MAGIC context = {}
# MAGIC for row in space_summary_list:
# MAGIC     space_id = row["space_id"]
# MAGIC     context[space_id] = row["searchable_content"]
# MAGIC
# MAGIC # Define UC function names
# MAGIC UC_FUNCTION_NAMES = [
# MAGIC     f"{CATALOG}.{SCHEMA}.get_space_summary",
# MAGIC     f"{CATALOG}.{SCHEMA}.get_table_overview",
# MAGIC     f"{CATALOG}.{SCHEMA}.get_column_detail",
# MAGIC     f"{CATALOG}.{SCHEMA}.get_space_details",
# MAGIC ]
# MAGIC
# MAGIC ########################################
# MAGIC # SQL Execution Tool
# MAGIC ########################################
# MAGIC
# MAGIC def execute_sql_on_delta_tables(
# MAGIC     sql_query: str,
# MAGIC     max_rows: int = 100,
# MAGIC     return_format: str = "dict"
# MAGIC ) -> Dict[str, Any]:
# MAGIC     """
# MAGIC     Execute SQL query on delta tables and return formatted results.
# MAGIC     
# MAGIC     Args:
# MAGIC         sql_query: Support two types:
# MAGIC             1) The result from invoke the SQL synthesis agent (dict with messages)
# MAGIC             2) The SQL query string (can be raw SQL or contain markdown code blocks)
# MAGIC         max_rows: Maximum number of rows to return (default: 100)
# MAGIC         return_format: Format of the result - "dict", "json", or "markdown"
# MAGIC     
# MAGIC     Returns:
# MAGIC         Dictionary containing:
# MAGIC         - success: bool - Whether execution was successful
# MAGIC         - sql: str - The executed SQL query
# MAGIC         - result: Any - Query results in requested format
# MAGIC         - row_count: int - Number of rows returned
# MAGIC         - columns: List[str] - Column names
# MAGIC         - error: str - Error message if failed (optional)
# MAGIC     """
# MAGIC     from pyspark.sql import SparkSession
# MAGIC     import pandas as pd
# MAGIC     
# MAGIC     spark = SparkSession.builder.getOrCreate()
# MAGIC     
# MAGIC     # Step 1: Extract SQL from agent result or markdown code blocks if present
# MAGIC     if sql_query and isinstance(sql_query, dict) and "messages" in sql_query:
# MAGIC         sql_query = sql_query["messages"][-1].content
# MAGIC     
# MAGIC     extracted_sql = sql_query.strip()
# MAGIC     
# MAGIC     # Try to extract SQL from markdown code blocks
# MAGIC     if "```sql" in extracted_sql.lower():
# MAGIC         sql_match = re.search(r'```sql\s*(.*?)\s*```', extracted_sql, re.IGNORECASE | re.DOTALL)
# MAGIC         if sql_match:
# MAGIC             extracted_sql = sql_match.group(1).strip()
# MAGIC     elif "```" in extracted_sql:
# MAGIC         # Extract any code block
# MAGIC         sql_match = re.search(r'```\s*(.*?)\s*```', extracted_sql, re.DOTALL)
# MAGIC         if sql_match:
# MAGIC             extracted_sql = sql_match.group(1).strip()
# MAGIC     
# MAGIC     # Step 2: Add LIMIT clause if not present (for safety)
# MAGIC     if "limit" not in extracted_sql.lower():
# MAGIC         extracted_sql = f"{extracted_sql.rstrip(';')} LIMIT {max_rows}"
# MAGIC     
# MAGIC     try:
# MAGIC         # Step 3: Execute the SQL query
# MAGIC         print(f"\n{'='*80}")
# MAGIC         print("🔍 EXECUTING SQL QUERY")
# MAGIC         print(f"{'='*80}")
# MAGIC         print(f"SQL:\n{extracted_sql}")
# MAGIC         print(f"{'='*80}\n")
# MAGIC         
# MAGIC         df = spark.sql(extracted_sql)
# MAGIC         
# MAGIC         # Step 4: Collect results
# MAGIC         results_list = df.collect()
# MAGIC         row_count = len(results_list)
# MAGIC         columns = df.columns
# MAGIC         
# MAGIC         print(f"✅ Query executed successfully!")
# MAGIC         print(f"📊 Rows returned: {row_count}")
# MAGIC         print(f"📋 Columns: {', '.join(columns)}\n")
# MAGIC         
# MAGIC         # Step 5: Format results based on return_format
# MAGIC         if return_format == "dataframe":
# MAGIC             result_data = df.toPandas()
# MAGIC         elif return_format == "json":
# MAGIC             result_data = df.toJSON().collect()
# MAGIC         elif return_format == "markdown":
# MAGIC             pandas_df = df.toPandas()
# MAGIC             result_data = pandas_df.to_markdown(index=False)
# MAGIC         else:  # dict (default)
# MAGIC             result_data = [row.asDict() for row in results_list]
# MAGIC         
# MAGIC         # Step 6: Display preview
# MAGIC         print(f"{'='*80}")
# MAGIC         print("📄 RESULTS PREVIEW (first 10 rows)")
# MAGIC         print(f"{'='*80}")
# MAGIC         df.show(n=min(10, row_count), truncate=False)
# MAGIC         print(f"{'='*80}\n")
# MAGIC         
# MAGIC         return {
# MAGIC             "success": True,
# MAGIC             "sql": extracted_sql,
# MAGIC             "result": result_data,
# MAGIC             "row_count": row_count,
# MAGIC             "columns": columns,
# MAGIC         }
# MAGIC         
# MAGIC     except Exception as e:
# MAGIC         error_msg = str(e)
# MAGIC         print(f"\n{'='*80}")
# MAGIC         print("❌ SQL EXECUTION FAILED")
# MAGIC         print(f"{'='*80}")
# MAGIC         print(f"Error: {error_msg}")
# MAGIC         print(f"{'='*80}\n")
# MAGIC         
# MAGIC         return {
# MAGIC             "success": False,
# MAGIC             "sql": extracted_sql,
# MAGIC             "result": None,
# MAGIC             "row_count": 0,
# MAGIC             "columns": [],
# MAGIC             "error": error_msg
# MAGIC         }
# MAGIC
# MAGIC
# MAGIC # Create SQL execution tool
# MAGIC from langchain_core.tools import tool
# MAGIC
# MAGIC @tool("execute_sql_tool")
# MAGIC def execute_sql_tool(sql_query: str, max_rows: int = 100) -> str:
# MAGIC     """
# MAGIC     Execute SQL query on delta tables and return results.
# MAGIC     
# MAGIC     Args:
# MAGIC         sql_query: The SQL query to execute
# MAGIC         max_rows: Maximum number of rows to return (default: 100)
# MAGIC     
# MAGIC     Returns:
# MAGIC         JSON string with execution results
# MAGIC     """
# MAGIC     result = execute_sql_on_delta_tables(sql_query, max_rows, return_format="dict")
# MAGIC     return json.dumps(result, indent=2, default=str)
# MAGIC
# MAGIC
# MAGIC ########################################
# MAGIC # Create Genie Route Agent with Genie Tools
# MAGIC ########################################
# MAGIC
# MAGIC def create_genie_route_agent(llm: Runnable, genie_agent_tools: list) -> Runnable:
# MAGIC     """Create SQL synthesis agent for genie route using Genie agent tools."""
# MAGIC     
# MAGIC     system_prompt = """You are a SQL synthesis agent, which can take analysis plan, and route queries to the corresponding Genie Agent.
# MAGIC The Plan given to you is a JSON:
# MAGIC {
# MAGIC 'original_query': 'The User's Question',
# MAGIC 'vector_search_relevant_spaces_info': [{'space_id': 'space_id_1',
# MAGIC    'space_title': 'space_title_1'},
# MAGIC   {'space_id': 'space_id_2',
# MAGIC    'space_title': 'space_title_2'},
# MAGIC   {'space_id': 'space_id_3',
# MAGIC    'space_title': 'space_title_3'}],
# MAGIC "question_clear": true,
# MAGIC "sub_questions": ["sub-question 1", "sub-question 2", ...],
# MAGIC "requires_multiple_spaces": true/false,
# MAGIC "relevant_space_ids": ["space_id_1", "space_id_2", ...],
# MAGIC "requires_join": true/false,
# MAGIC "join_strategy": "table_route" or "genie_route" or null,
# MAGIC "execution_plan": "Brief description of execution plan",
# MAGIC "genie_route_plan": {'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', 'space_id_3':'partial_question_3', ...} or null,}
# MAGIC
# MAGIC ## Tool Calling Plan:
# MAGIC 1. Under the key of 'genie_route_plan' in the JSON, extracting 'partial_question_1' and feed to the right Genie Agent tool of 'space_id_1' with the input as a string.
# MAGIC 2. Asynchronously send all other partial_questions to the corresponding Genie Agent tools accordingly.
# MAGIC 3. You have access to all Genie Agents as tools given to you; locate the proper Genie Agent Tool by searching the 'space_id_1' in the tool's description. After each Genie agent returns result, only extract the SQL string from the Genie tool output JSON {"thinking": thinking, "sql": sql, "answer": answer}.
# MAGIC 4. If you find you are still missing necessary analytical components (metrics, filters, dimensions, etc.) to assemble the final SQL, which might be due to some genie agent tool may not have the necessary information being assigned, try to leverage other most likely Genie agents to find the missing pieces.
# MAGIC
# MAGIC ## Disaster Recovery (DR) Plan:
# MAGIC 1. If one Genie agent tool fail to generate a SQL query, allow retry AS IS only one time;
# MAGIC 2. If fail again, try to reframe the partial question 'partial_question_1' according to the error msg returned by the genie tool, e.g., genie tool may say "I dont have information for cost related information", you can remove those components in the 'partial_question_1' which doesn't exist in the genie tool. For example, if the genie tool "Genie_MemberBenefits" doesn't contain benefit cost related information, you can reframe the question by removing the cost-related components in the 'partial_question_1', generate 'partial_question_1_v2' and try again. Only try once;
# MAGIC 3. If fail again, return response as is.
# MAGIC
# MAGIC
# MAGIC ## Overall SQL Synthesis Plan:
# MAGIC Then, you can combine all the SQL pieces into a single SQL query, and return the final SQL query.
# MAGIC OUTPUT REQUIREMENTS:
# MAGIC - Generate complete, executable SQL with:
# MAGIC   * Proper JOINs based on execution plan strategy
# MAGIC   * WHERE clauses for filtering
# MAGIC   * Appropriate aggregations
# MAGIC   * Clear column aliases
# MAGIC   * Always use real column name existed in the data, never make up one
# MAGIC - Return your response with:
# MAGIC 1. Your explanation combining both the individual Genie thinking and your own reasoning
# MAGIC 2. The final SQL query in a ```sql code block
# MAGIC     """
# MAGIC     
# MAGIC     return create_agent(
# MAGIC         model=llm,
# MAGIC         tools=genie_agent_tools,
# MAGIC         name="sql_synthesis_genie_route",
# MAGIC         system_prompt=system_prompt
# MAGIC     )
# MAGIC
# MAGIC
# MAGIC ########################################
# MAGIC # Create All Sub-Agents with Individual LLMs
# MAGIC ########################################
# MAGIC
# MAGIC # Keep IN_CODE_AGENTS empty - all agents now created individually with their own LLMs
# MAGIC IN_CODE_AGENTS = []
# MAGIC
# MAGIC # Create LLMs for different agents (can be customized per agent)
# MAGIC llm_clarification = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING, temperature=0.1)  # Fast model for simple task
# MAGIC llm_planning = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING, temperature=0.1)
# MAGIC llm_table_route = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME, temperature=0.1)  # Powerful model for SQL synthesis
# MAGIC llm_genie_route = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING, temperature=0.1)
# MAGIC llm_execution = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING, temperature=0.1)
# MAGIC
# MAGIC # 1. Clarification Agent
# MAGIC def create_clarification_agent_with_context(llm: Runnable, table_name: str) -> Runnable:
# MAGIC     """
# MAGIC     Create clarification agent with dynamically loaded context.
# MAGIC     Loads fresh context from table on each call.
# MAGIC     """
# MAGIC     context = load_space_context(table_name)
# MAGIC     
# MAGIC     return create_agent(
# MAGIC         model=llm,
# MAGIC         tools=[],
# MAGIC         name="clarification_agent",
# MAGIC         system_prompt=f"""
# MAGIC You are a clarification agent. Your job is to analyze user queries and determine if they are clear enough to execute.
# MAGIC
# MAGIC IMPORTANT: Only mark as unclear if the question is TRULY VAGUE or IMPOSSIBLE to answer.
# MAGIC Be lenient - if the question can reasonably be answered with the available data, mark it as clear.
# MAGIC
# MAGIC Available Genie Spaces:
# MAGIC {json.dumps(context, indent=2)}
# MAGIC
# MAGIC Determine if:
# MAGIC 1. The question is clear and answerable as-is (BE LENIENT - default to TRUE)
# MAGIC 2. The question is TRULY VAGUE and needs critical clarification (ONLY if essential information is missing)
# MAGIC 3. If the question mentions any metrics/dimensions/filters that can be mapped to available data with certain confidence, mark it as CLEAR; otherwise, mark it as UNCLEAR and ask for clarification.
# MAGIC
# MAGIC If clarification is truly needed, provide:
# MAGIC - A brief explanation of what's critically unclear
# MAGIC - 2-3 specific clarification options the user can choose from
# MAGIC
# MAGIC Return your analysis as JSON:
# MAGIC {{
# MAGIC     "question_clear": true/false,
# MAGIC     "clarification_needed": "explanation if unclear (null if clear)",
# MAGIC     "clarification_options": ["option 1", "option 2", "option 3"] or null
# MAGIC }}
# MAGIC
# MAGIC Only return valid JSON, no explanations.
# MAGIC         """
# MAGIC     )
# MAGIC
# MAGIC clarification_agent = create_clarification_agent_with_context(llm_clarification, TABLE_NAME)
# MAGIC clarification_agent.name = "clarification_agent"
# MAGIC clarification_agent.description = "Validates query clarity and requests clarification if the user query is ambiguous or missing information."
# MAGIC
# MAGIC # 2. Planning Agent
# MAGIC planning_agent = create_agent(
# MAGIC     model=llm_planning,
# MAGIC     tools=[],
# MAGIC     name="planning_agent",
# MAGIC     system_prompt="""
# MAGIC You are a query planning expert. Analyze the following question and create an execution plan.
# MAGIC
# MAGIC You will receive:
# MAGIC 1. The user's query (or combined query context with clarification)
# MAGIC 2. Potentially relevant Genie spaces from vector search
# MAGIC
# MAGIC Break down the question and determine:
# MAGIC 1. What are the sub-questions or analytical components?
# MAGIC 2. How many Genie spaces are needed to answer completely? (List their space_ids)
# MAGIC 3. If multiple spaces are needed, do we need to JOIN data across them? Reasoning whether the sub-questions are totally independent without joining need.
# MAGIC     - JOIN needed: E.g., "How many active plan members over 50 are on Lexapro?" requires joining member data with pharmacy claims.
# MAGIC     - No need for JOIN: E.g., "How many active plan members over 50? How much total cost for all Lexapro claims?" - Two independent questions.
# MAGIC 4. If JOIN is needed, what's the best strategy:
# MAGIC     - "table_route": Directly synthesize SQL across multiple tables
# MAGIC     - "genie_route": Query each Genie Space Agent separately, then combine SQL queries
# MAGIC     - If user explicitly asks for "genie_route", use it; otherwise, use "table_route"
# MAGIC     - always populate the join_strategy field in the JSON output.
# MAGIC 5. Execution plan: A brief description of how to execute the plan.
# MAGIC     - For genie_route: Return "genie_route_plan": {'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}
# MAGIC     - For table_route: Return "genie_route_plan": null
# MAGIC     - Each partial_question should be similar to original but scoped to that space
# MAGIC     - Add "Please limit to top 10 rows" to each partial question
# MAGIC
# MAGIC Return your analysis as JSON:
# MAGIC {
# MAGIC     "original_query": "user query",
# MAGIC     "vector_search_relevant_spaces_info": [{"space_id": "space_id_1", "space_title": "title_1"}, ...],
# MAGIC     "question_clear": true,
# MAGIC     "sub_questions": ["sub-question 1", "sub-question 2", ...],
# MAGIC     "requires_multiple_spaces": true/false,
# MAGIC     "relevant_space_ids": ["space_id_1", "space_id_2", ...],
# MAGIC     "requires_join": true/false,
# MAGIC     "join_strategy": "table_route" or "genie_route",
# MAGIC     "execution_plan": "Brief description of execution plan",
# MAGIC     "genie_route_plan": {'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'} or null
# MAGIC }
# MAGIC
# MAGIC Only return valid JSON, no explanations.
# MAGIC     """
# MAGIC )
# MAGIC planning_agent.name = "planning_agent"
# MAGIC planning_agent.description = "Analyzes queries, searches for relevant Genie spaces, identifies join requirements, and creates execution plans (table_route or genie_route)."
# MAGIC
# MAGIC # 3. SQL Synthesis Table Route Agent
# MAGIC uc_toolkit_table_route = UCFunctionToolkit(function_names=UC_FUNCTION_NAMES)
# MAGIC TOOLS.extend(uc_toolkit_table_route.tools)
# MAGIC
# MAGIC sql_synthesis_table_route = create_agent(
# MAGIC     model=llm_table_route,
# MAGIC     tools=uc_toolkit_table_route.tools,
# MAGIC     name="sql_synthesis_table_route",
# MAGIC     system_prompt="""
# MAGIC You are a specialized SQL synthesis agent in a multi-agent system.
# MAGIC
# MAGIC ROLE: You receive execution plans from the planning agent and generate SQL queries.
# MAGIC
# MAGIC ## WORKFLOW:
# MAGIC 1. Review the execution plan and provided metadata
# MAGIC 2. If metadata is sufficient → Generate SQL immediately
# MAGIC 3. If insufficient, call UC function tools in this order:
# MAGIC    a) get_space_summary for space information
# MAGIC    b) get_table_overview for table schemas
# MAGIC    c) get_column_detail for specific columns
# MAGIC    d) get_space_details ONLY as last resort (token intensive)
# MAGIC 4. At last, if you still cannot find enough metadata in relevant spaces provided, dont stuck there. Expand the searching scope to all spaces mentioned in the execution plan's 'vector_search_relevant_spaces_info' field. Extract the space_id from 'vector_search_relevant_spaces_info'.
# MAGIC 5. Generate complete, executable SQL
# MAGIC
# MAGIC ## UC FUNCTION USAGE:
# MAGIC - Pass arguments as JSON array strings: '["space_id_1", "space_id_2"]' or 'null'
# MAGIC - Only query spaces from execution plan's relevant_space_ids
# MAGIC - Use minimal sufficiency: only query what you need
# MAGIC
# MAGIC ## OUTPUT REQUIREMENTS:
# MAGIC - Generate complete, executable SQL with:
# MAGIC   * Proper JOINs based on execution plan
# MAGIC   * WHERE clauses for filtering
# MAGIC   * Appropriate aggregations
# MAGIC   * Clear column aliases
# MAGIC   * Always use real column names, never make up ones
# MAGIC - Return your response with:
# MAGIC 1. Your explanations; If SQL cannot be generated, explain what metadata is missing
# MAGIC 2. The final SQL query in a ```sql code block
# MAGIC     """
# MAGIC )
# MAGIC sql_synthesis_table_route.name = "sql_synthesis_table_route"
# MAGIC sql_synthesis_table_route.description = "Generates SQL queries using UC metadata functions (table route). Best for queries requiring joins across multiple tables."
# MAGIC
# MAGIC # 4. SQL Synthesis Genie Route Agent (with Genie tools)
# MAGIC genie_route_agent = create_genie_route_agent(llm_genie_route, genie_agent_tools)
# MAGIC genie_route_agent.name = "sql_synthesis_genie_route"
# MAGIC genie_route_agent.description = "Generates SQL by routing queries to individual Genie agents and combining their SQL outputs. Use when join_strategy is 'genie_route'."
# MAGIC
# MAGIC # 5. SQL Execution Agent
# MAGIC TOOLS.append(execute_sql_tool)
# MAGIC sql_execution_agent = create_agent(
# MAGIC     model=llm_execution,
# MAGIC     tools=[execute_sql_tool],
# MAGIC     name="sql_execution_agent",
# MAGIC     system_prompt="""
# MAGIC     You are a SQL execution agent. Your job is to:
# MAGIC     1. Take SQL queries from the SQL synthesis agents
# MAGIC     2. Execute them on delta tables using the execute_sql_tool
# MAGIC     3. Return formatted results with execution status
# MAGIC     
# MAGIC     IMPORTANT: Always use the execute_sql_tool to run SQL queries.
# MAGIC     
# MAGIC     When you receive a SQL query:
# MAGIC     - Extract the SQL if it's in markdown formatting or JSON
# MAGIC     - Call execute_sql_tool with the SQL query
# MAGIC     - Parse and present the results clearly
# MAGIC     
# MAGIC     The tool returns a JSON with:
# MAGIC       * success: boolean indicating if execution succeeded
# MAGIC       * sql: the executed query
# MAGIC       * result: query results (list of dictionaries)
# MAGIC       * row_count: number of rows returned
# MAGIC       * columns: list of column names
# MAGIC       * error: error message if failed
# MAGIC     
# MAGIC     Present the results in a user-friendly format including:
# MAGIC     - Success status
# MAGIC     - SQL that was executed
# MAGIC     - Results summary (row count, columns)
# MAGIC     - Sample of the data (if successful)
# MAGIC     - Error details (if failed)
# MAGIC     """
# MAGIC )
# MAGIC sql_execution_agent.name = "sql_execution_agent"
# MAGIC sql_execution_agent.description = "Executes SQL queries on delta tables and returns structured results with success status, data, and metadata."
# MAGIC
# MAGIC # 6. Result Summarize Agent
# MAGIC llm_summarize = ChatDatabricks(endpoint=LLM_ENDPOINT_SUMMARIZE, temperature=0.1, max_tokens=2000)
# MAGIC result_summarize_agent = create_agent(
# MAGIC     model=llm_summarize,
# MAGIC     tools=[],
# MAGIC     name="result_summarize_agent",
# MAGIC     system_prompt="""
# MAGIC You are a result summarization agent. Generate a concise, natural language summary of what this multi-agent workflow accomplished.
# MAGIC
# MAGIC You will receive workflow execution details including:
# MAGIC - Original user query
# MAGIC - Clarification status and details
# MAGIC - Execution plan and strategy
# MAGIC - SQL generation details and explanation
# MAGIC - Execution results or errors
# MAGIC
# MAGIC Your task is to generate a detailed summary in natural language that:
# MAGIC 1. Describes what the user asked for
# MAGIC 2. Explains what the system did (planning, SQL generation, execution)
# MAGIC 3. States the outcome (success with X rows, error, needs clarification, etc.)
# MAGIC 4. Print out SQL synthesis explanation if any SQL was generated
# MAGIC 5. Print out SQL if any SQL was generated; make it a code block
# MAGIC 6. Print out the result itself (like a table)
# MAGIC
# MAGIC Keep it concise and user-friendly.
# MAGIC
# MAGIC Return your summary in this format:
# MAGIC
# MAGIC **Summary**: [Brief overview of what happened]
# MAGIC
# MAGIC **Original Query**: [User's original question]
# MAGIC
# MAGIC **Planning**: [What the planning agent determined]
# MAGIC **Strategy**: [table_route or genie_route]
# MAGIC
# MAGIC **SQL Generation**: [Success or failure, with explanation]
# MAGIC **SQL Query**: 
# MAGIC ```sql
# MAGIC [The SQL query if generated]
# MAGIC ```
# MAGIC
# MAGIC **Execution**: [Success or failure]
# MAGIC **Rows**: [Number of rows]
# MAGIC **Columns**: [Column names]
# MAGIC
# MAGIC **Result**: [Query results formatted as table or error message]
# MAGIC     """
# MAGIC )
# MAGIC result_summarize_agent.name = "result_summarize_agent"
# MAGIC result_summarize_agent.description = "Generates comprehensive natural language summary of the entire workflow execution including plans, SQL, results, and any errors."
# MAGIC
# MAGIC # Collect all agents for supervisor
# MAGIC ALL_AGENTS = [
# MAGIC     clarification_agent,
# MAGIC     planning_agent,
# MAGIC     sql_synthesis_table_route,
# MAGIC     genie_route_agent,
# MAGIC     sql_execution_agent,
# MAGIC     result_summarize_agent
# MAGIC ]
# MAGIC
# MAGIC #################################################
# MAGIC # Create supervisor and set up MLflow for tracing
# MAGIC #################################################
# MAGIC
# MAGIC # Create supervisor with all agents
# MAGIC supervisor = create_langgraph_supervisor(
# MAGIC     llm, 
# MAGIC     IN_CODE_AGENTS,  # Empty list now
# MAGIC     additional_agents=ALL_AGENTS  # All agents with their own LLMs
# MAGIC )
# MAGIC
# MAGIC mlflow.langchain.autolog()
# MAGIC AGENT = LangGraphResponsesAgent(supervisor)
# MAGIC mlflow.models.set_model(AGENT)

# COMMAND ----------

# MAGIC %md
# MAGIC ## ✅ Integrated Multi-Agent System
# MAGIC
# MAGIC The complete multi-agent system is now fully integrated with all agents working together:
# MAGIC
# MAGIC ### Integrated Agents (ALL_AGENTS):
# MAGIC
# MAGIC **All agents now have their own dedicated LLMs** for maximum flexibility and performance optimization.
# MAGIC
# MAGIC 1. **Clarification Agent** (ALL_AGENTS[0])
# MAGIC    - LLM: `llm_clarification` (Haiku - fast for simple tasks)
# MAGIC    - Validates query clarity with lenient approach
# MAGIC    - Requests clarification only when truly needed (max 1 time)
# MAGIC    - Dynamically loads space context from Delta table
# MAGIC
# MAGIC 2. **Planning Agent** (ALL_AGENTS[1])
# MAGIC    - LLM: `llm_planning` (Haiku - efficient for planning)
# MAGIC    - Analyzes queries and searches vector index
# MAGIC    - Creates detailed execution plans with genie_route_plan
# MAGIC    - Determines table_route or genie_route strategy
# MAGIC    - Identifies relevant spaces and join requirements
# MAGIC
# MAGIC 3. **SQL Synthesis Table Route** (ALL_AGENTS[2])
# MAGIC    - LLM: `llm_table_route` (Sonnet - powerful for SQL generation)
# MAGIC    - Uses UC metadata functions intelligently
# MAGIC    - Best for complex joins across tables
# MAGIC    - Provides SQL with explanation
# MAGIC
# MAGIC 4. **SQL Synthesis Genie Route** (ALL_AGENTS[3])
# MAGIC    - LLM: `llm_genie_route` (Sonnet - coordinates Genie agents)
# MAGIC    - Routes to individual Genie agents dynamically
# MAGIC    - Only creates Genie agents for relevant spaces (optimized)
# MAGIC    - Combines SQL from multiple sources with retry logic
# MAGIC    - Used when join_strategy is "genie_route"
# MAGIC
# MAGIC 5. **SQL Execution Agent** (ALL_AGENTS[4])
# MAGIC    - LLM: `llm_execution` (Haiku - executes with tool)
# MAGIC    - Executes generated SQL queries on delta tables
# MAGIC    - Returns structured results with success status
# MAGIC    - Includes data, row counts, and comprehensive error handling
# MAGIC
# MAGIC 6. **Result Summarize Agent** (ALL_AGENTS[5]) - NEW!
# MAGIC    - LLM: `llm_summarize` (Haiku - fast summarization)
# MAGIC    - Generates comprehensive natural language summary
# MAGIC    - Includes all workflow details: plan, SQL, results, errors
# MAGIC    - Formats results in user-friendly way
# MAGIC    - MANDATORY final step for all workflows
# MAGIC
# MAGIC ### LLM Configuration Benefits:
# MAGIC - **Cost Optimization**: Use cheaper models (Haiku) for simpler tasks
# MAGIC - **Performance**: Use powerful models (Sonnet) where needed
# MAGIC - **Flexibility**: Easy to adjust per agent based on requirements
# MAGIC - **Scalability**: Each agent can scale independently
# MAGIC
# MAGIC ### Complete Workflow:
# MAGIC ```
# MAGIC User Query → Clarification → Planning → SQL Synthesis (Table/Genie Route) → 
# MAGIC SQL Execution → Result Summarize → Comprehensive Response
# MAGIC ```
# MAGIC
# MAGIC ### Enhanced Features:
# MAGIC - **Dynamic Context Loading**: Clarification agent loads fresh space context from Delta table
# MAGIC - **Clarification Flow**: Preserves original query and combines with clarification response
# MAGIC - **Optimized Genie Route**: Only creates Genie agents for relevant spaces (not all spaces)
# MAGIC - **Comprehensive State**: Full observability with AgentState TypedDict
# MAGIC - **Result Summarization**: Every workflow ends with user-friendly summary
# MAGIC - **Error Handling**: Graceful handling with explanations at every step
# MAGIC
# MAGIC ### Final Output Format:
# MAGIC The supervisor returns comprehensive results including:
# MAGIC - **Summary**: Natural language overview of entire workflow
# MAGIC - **Original Query**: User's question (preserved through clarifications)
# MAGIC - **Execution Plan**: Strategy and approach from planning agent
# MAGIC - **SQL Query**: Generated SQL with explanation
# MAGIC - **Results**: Query execution results formatted as table
# MAGIC - **Errors**: Clear error messages if any step fails

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test the Agent

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

from agent import AGENT

# Test with a sample query
input_example = {
    "input": [
        {"role": "user", "content": "What is the average cost of medical claims in 2024?"}
    ],
    "custom_inputs": {
        "thread_id": "simple_test_001"
    }
}

response = AGENT.predict(input_example)
print(response)

# COMMAND ----------

# "input": [{"role": "user", "content": user_clarification}],
# "custom_inputs": {
#     "is_clarification_response": True,
#     "original_query": vague_query,  # Preserved from step 1
#     "clarification_message": "...",  # Extracted from response_1
#     "clarification_count": 1  # Preserved from step 1
# }

original_query = "What is the average cost of medical claims in 2024?"
user_clarification = """
1. allowed amounts
2. actually claim service date in 2024
3. procedure table
"""
clarification_msg = """
I need to clarify a few things about your query before proceeding:
1. Cost Metric Clarification: Medical claims typically have multiple cost fields. Which one would you like to analyze?
Total line charges (billed amounts)
Allowed amounts (insurance-approved amounts)
Insurance payments (what insurance paid)
Patient out-of-pocket costs (copays, deductibles, coinsurance)
2. Time Period: You mentioned "2024" - can you confirm:
Do you have claims data from 2024 in your system?
Or did you mean a different year?
What specific date range should I use (e.g., all of 2024, specific quarters)?
3. Data Source: Should I calculate this from:
The medical_claim table (claim-level totals)
The procedure table (procedure-level detail)
Once you provide these details, I'll be able to generate the precise SQL query and get you the accurate average cost analysis you need.
"""

# Test with a sample query
follow_up = {
    "input": [
        {"role": "user", "content": f"{user_clarification}"}
    ],
    "custom_inputs": {
        "thread_id": "simple_test_001",
        "is_clarification_response": True,
        "original_query": original_query,  # Preserved from step 1
        "clarification_message": clarification_msg,  # Extracted from response_1
        "clarification_count": 1  # Preserved from step 1
    }
}

response = AGENT.predict(follow_up)
print(response)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Log the Agent as an MLflow Model

# COMMAND ----------

import mlflow
from agent import LLM_ENDPOINT_NAME, CATALOG, SCHEMA, UC_FUNCTION_NAMES, VECTOR_SEARCH_INDEX, TABLE_NAME
from mlflow.models.resources import (
    DatabricksFunction,
    DatabricksServingEndpoint,
    DatabricksSQLWarehouse,
    DatabricksTable,
    DatabricksVectorSearchIndex,
)
from pkg_resources import get_distribution

# Determine Databricks resources for automatic auth passthrough
resources = [DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT_NAME)]

# Add UC Functions
for func_name in UC_FUNCTION_NAMES:
    resources.append(DatabricksFunction(function_name=func_name))

# Add Vector Search Index
resources.append(DatabricksVectorSearchIndex(index_name=VECTOR_SEARCH_INDEX))

# Add Delta Table
resources.append(DatabricksTable(table_name=TABLE_NAME))

# TODO: Add SQL Warehouse ID
# resources.append(DatabricksSQLWarehouse(warehouse_id="<your_warehouse_id>"))

with mlflow.start_run():
    logged_agent_info = mlflow.pyfunc.log_model(
        name="agent",
        python_model="agent.py",
        resources=resources,
        pip_requirements=[
            f"databricks-connect=={get_distribution('databricks-connect').version}",
            f"mlflow=={get_distribution('mlflow').version}",
            f"databricks-langchain=={get_distribution('databricks-langchain').version}",
            f"langgraph=={get_distribution('langgraph').version}",
            f"langgraph-supervisor=={get_distribution('langgraph-supervisor').version}",
            f"databricks-vectorsearch=={get_distribution('databricks-vectorsearch').version}",
        ],
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pre-deployment Validation

# COMMAND ----------

import mlflow
mlflow.models.predict(
    model_uri=f"runs:/{logged_agent_info.run_id}/agent",
    input_data=input_example,
    env_manager="uv",
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Register to Unity Catalog

# COMMAND ----------

mlflow.set_registry_uri("databricks-uc")

# TODO: define the catalog, schema, and model name for your UC model
catalog = "yyang"
schema = "multi_agent_genie"
model_name = "super_agent_langgraph"
UC_MODEL_NAME = f"{catalog}.{schema}.{model_name}"

# register the model to UC
uc_registered_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri, name=UC_MODEL_NAME
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Deploy the Agent

# COMMAND ----------

from databricks import agents

agents.deploy(
    UC_MODEL_NAME, 
    uc_registered_model_info.version, 
    tags={"endpointSource": "multi_agent_genie", "version": "v1"}, 
    deploy_feedback_model=False
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Additional Testing Examples

# COMMAND ----------

# Test clarification flow
test_queries = [
    {
        "query": "How many patients are?",
        "description": "Vague query - should trigger clarification"
    },
    {
        "query": "What is the average cost of medical claims in 2024?",
        "description": "Clear query - should proceed directly"
    },
    {
        "query": "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?",
        "description": "Complex multi-space query requiring join"
    },
    {
        "query": "Show me the top 10 most expensive medications and patient counts by age group",
        "description": "Multi-space query - tests genie_route or table_route decision"
    }
]

for test_case in test_queries:
    query = test_case["query"]
    description = test_case["description"]
    
    print(f"\n{'='*80}")
    print(f"Test Case: {description}")
    print(f"Query: {query}")
    print(f"{'='*80}\n")
    
    input_data = {"input": [{"role": "user", "content": query}]}
    
    try:
        response = AGENT.predict(input_data)
        print("\n📊 AGENT RESPONSE:")
        print(response)
        
        # Check if clarification was requested
        # (This would be in the response output)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
    
    print(f"\n{'='*80}")
    print("Test case complete")
    print(f"{'='*80}\n")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Clarification Response Flow
# MAGIC
# MAGIC This test demonstrates how to handle clarification requests and responses

# COMMAND ----------

# Test clarification response workflow
print("="*80)
print("TESTING CLARIFICATION RESPONSE FLOW")
print("="*80)

# Step 1: Send a vague query that should trigger clarification
vague_query = "Show me the data"

print(f"\n📤 Sending vague query: {vague_query}")
input_data_1 = {"input": [{"role": "user", "content": vague_query}]}

response_1 = AGENT.predict(input_data_1)
print("\n📥 Response 1 (should contain clarification request):")
print(response_1)

# In a real scenario, you would:
# 1. Parse the response to check if clarification was requested
# 2. Extract clarification_message, original_query, clarification_count from state
# 3. Get user's clarification response
# 4. Send follow-up request with is_clarification_response=True

# Example of how to send clarification response:
# (This is pseudocode - actual implementation depends on how you access state)
"""
# Step 2: User provides clarification
user_clarification = "Show me patient count by age group from the enrollment data"

print(f"\n📤 User provides clarification: {user_clarification}")

# Send clarification response with preserved state
input_data_2 = {
    "input": [{"role": "user", "content": user_clarification}],
    "custom_inputs": {
        "is_clarification_response": True,
        "original_query": vague_query,  # Preserved from step 1
        "clarification_message": "...",  # Extracted from response_1
        "clarification_count": 1  # Preserved from step 1
    }
}

response_2 = AGENT.predict(input_data_2)
print("\n📥 Response 2 (should contain query results):")
print(response_2)
"""

print("\n" + "="*80)
print("Note: Clarification response handling requires access to previous state")
print("See ResponsesAgent.predict_stream() for full implementation details")
print("="*80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Genie Route Agent

# COMMAND ----------

from agent import genie_route_agent, execute_sql_on_delta_tables

# Example plan result (typically comes from planning agent)
example_plan = {
    "original_query": "What is the average cost of medical claims for patients diagnosed with diabetes?",
    "question_clear": True,
    "requires_multiple_spaces": True,
    "relevant_space_ids": ["space_id_1", "space_id_2"],
    "requires_join": True,
    "join_strategy": "genie_route",
    "genie_route_plan": {
        "space_id_1": "What are the medical claims for patients?",
        "space_id_2": "What are the diagnosis codes for diabetes?"
    }
}

# Test genie route agent with plan
agent_message = {
    "messages": [
        {
            "role": "user",
            "content": f"""
Generate a SQL query according to the following Query Plan:
{json.dumps(example_plan, indent=2)}
"""
        }
    ]
}

# Invoke genie route agent
genie_route_result = genie_route_agent.invoke(agent_message)
print("Genie Route Agent Result:")
print(genie_route_result)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test SQL Execution Tool

# COMMAND ----------

# Example SQL query to execute
test_sql = """
SELECT 
    COUNT(*) as total_count,
    AVG(allowed_amount) as avg_amount
FROM yyang.multi_agent_genie.medical_claim
LIMIT 100
"""

# Execute SQL
execution_result = execute_sql_on_delta_tables(test_sql, max_rows=100, return_format="dict")
print("\nExecution Result:")
print(json.dumps(execution_result, indent=2, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test End-to-End Workflow with SQL Execution

# COMMAND ----------

# Example: Get SQL from table route agent, then execute it
query = "How many patients are in the dataset?"

# Step 1: Create input for the agent
input_data = {"input": [{"role": "user", "content": query}]}

# Step 2: Get response from supervisor (which routes to appropriate agent)
response = AGENT.predict(input_data)

# Step 3: Extract SQL from response (if any)
# Note: You would parse the response to extract the SQL query
# For demonstration, using a sample SQL
sample_sql = """
SELECT COUNT(DISTINCT patient_id) as total_patients
FROM yyang.multi_agent_genie.enrollment
"""

# Step 4: Execute the SQL
execution_result = execute_sql_on_delta_tables(sample_sql, max_rows=10, return_format="dict")

print("Query:", query)
print("\nSQL Generated:", sample_sql)
print("\nExecution Result:")
print(json.dumps(execution_result, indent=2, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Result Summarization
# MAGIC
# MAGIC The Result Summarize Agent provides comprehensive summaries of workflow execution

# COMMAND ----------

# Test result summarization with a clear query
test_query_for_summary = "What is the total count of medical claims in 2024?"

print("="*80)
print("TESTING RESULT SUMMARIZATION")
print("="*80)
print(f"\nQuery: {test_query_for_summary}\n")

input_data = {"input": [{"role": "user", "content": test_query_for_summary}]}

# The response should include:
# 1. Summary of what happened
# 2. Original query
# 3. Execution plan
# 4. SQL query (if generated)
# 5. Execution results (if successful)
# 6. Any errors encountered
response = AGENT.predict(input_data)

print("\n📊 COMPREHENSIVE RESPONSE WITH SUMMARY:")
print("="*80)
print(response)
print("="*80)

print("\n✅ The response includes:")
print("  - Natural language summary of the workflow")
print("  - Complete execution plan from planning agent")
print("  - Generated SQL query with explanation")
print("  - Query execution results formatted as table")
print("  - Error details if any step failed")
print("\nThis comprehensive output is generated by the Result Summarize Agent (ALL_AGENTS[5])")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Inspect State After Execution
# MAGIC
# MAGIC You can also directly invoke the supervisor to get full state

# COMMAND ----------

# Direct invocation gives access to full state
from agent import supervisor

test_query = "What is the average cost per claim?"
initial_state = {
    "original_query": test_query,
    "question_clear": False,
    "messages": [
        {"role": "system", "content": "Multi-agent Q&A system"},
        {"role": "user", "content": test_query}
    ]
}

print("="*80)
print("DIRECT SUPERVISOR INVOCATION FOR STATE INSPECTION")
print("="*80)

# Invoke supervisor directly
final_state = supervisor.invoke(initial_state)

print("\n📊 FINAL STATE KEYS:")
print(list(final_state.keys()))

print("\n🔍 KEY STATE FIELDS:")
print(f"  - original_query: {final_state.get('original_query', 'N/A')}")
print(f"  - question_clear: {final_state.get('question_clear', 'N/A')}")
print(f"  - execution_plan: {final_state.get('execution_plan', 'N/A')[:100]}...")
print(f"  - join_strategy: {final_state.get('join_strategy', 'N/A')}")
print(f"  - has_sql: {final_state.get('has_sql', 'N/A')}")

if final_state.get('sql_query'):
    print(f"\n💻 SQL QUERY:")
    print(final_state['sql_query'])

if final_state.get('execution_result'):
    exec_result = final_state['execution_result']
    print(f"\n📊 EXECUTION RESULT:")
    print(f"  - Success: {exec_result.get('success', False)}")
    print(f"  - Row count: {exec_result.get('row_count', 0)}")
    print(f"  - Columns: {exec_result.get('columns', [])}")

if final_state.get('final_summary'):
    print(f"\n📝 FINAL SUMMARY:")
    print(final_state['final_summary'])

print("\n" + "="*80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Compare Table Route vs Genie Route

# COMMAND ----------

# Test both routes with the same query
test_query = "What is the average cost of medical claims in 2024?"

print("="*80)
print("COMPARING TABLE ROUTE VS GENIE ROUTE")
print("="*80)
print(f"\nQuery: {test_query}\n")

print("📝 ROUTE COMPARISON NOTES:")
print("-"*80)
print("1. **Table Route** (SQL Synthesis Table Route Agent):")
print("   - Uses UC metadata functions (get_space_summary, get_table_overview, etc.)")
print("   - Queries metadata on-demand with intelligent progression")
print("   - Best for complex joins across multiple tables")
print("   - Faster for queries requiring detailed schema information")
print("")
print("2. **Genie Route** (SQL Synthesis Genie Route Agent):")
print("   - Routes queries to individual Genie Space Agents")
print("   - Each Genie agent generates SQL for its space")
print("   - Combines SQL fragments into final query")
print("   - OPTIMIZED: Only creates Genie agents for relevant spaces (not all spaces)")
print("   - Best for queries that can be decomposed into independent sub-queries")
print("")
print("The Planning Agent automatically determines the best strategy based on:")
print("  - Query complexity")
print("  - Number of spaces needed")
print("  - Join requirements")
print("  - User preference (if explicitly requested)")
print("-"*80)

# Table Route Test
print("\n🚀 TABLE ROUTE TEST:")
print("-"*80)
table_route_input = {
    "input": [
        {"role": "user", "content": f"{test_query}\nPlease use table_route for SQL synthesis."}
    ]
}

print("Sending request with table_route preference...")
try:
    table_route_response = AGENT.predict(table_route_input)
    print("\n✅ Table Route Response:")
    print(table_route_response)
except Exception as e:
    print(f"\n❌ Table Route Error: {e}")

print("\n" + "="*80 + "\n")

# Genie Route Test
print("🚀 GENIE ROUTE TEST:")
print("-"*80)
genie_route_input = {
    "input": [
        {"role": "user", "content": f"{test_query}\nPlease use genie_route for SQL synthesis."}
    ]
}

print("Sending request with genie_route preference...")
print("Note: The Genie Route agent will:")
print("  1. Receive genie_route_plan from Planning Agent")
print("  2. Create Genie agents ONLY for relevant spaces")
print("  3. Route partial queries to each Genie agent")
print("  4. Extract SQL from each Genie agent response")
print("  5. Combine SQL fragments into final query")
print("")

try:
    genie_route_response = AGENT.predict(genie_route_input)
    print("\n✅ Genie Route Response:")
    print(genie_route_response)
except Exception as e:
    print(f"\n❌ Genie Route Error: {e}")

print("\n" + "="*80)
print("COMPARISON COMPLETE")
print("="*80)
print("\n💡 TIP: Review MLflow traces to see:")
print("  - Token usage differences")
print("  - Latency differences")
print("  - SQL quality differences")
print("  - Agent routing decisions")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next Steps
# MAGIC
# MAGIC 1. **Test the multi-agent system** with various queries
# MAGIC    - Test clarification flow with ambiguous queries
# MAGIC    - Test both table_route and genie_route strategies
# MAGIC    - Test error handling and recovery
# MAGIC 2. **Monitor MLflow traces** to understand agent routing and execution
# MAGIC    - View supervisor routing decisions
# MAGIC    - Track agent performance and token usage
# MAGIC 3. **Fine-tune agent prompts** based on test results
# MAGIC    - Adjust clarification thresholds
# MAGIC    - Refine SQL synthesis instructions
# MAGIC 4. **Compare Table Route vs Genie Route** performance and accuracy
# MAGIC    - Measure latency differences
# MAGIC    - Compare SQL quality and accuracy
# MAGIC 5. **Review summarization quality**
# MAGIC    - Ensure summaries are comprehensive and user-friendly
# MAGIC    - Validate result formatting
# MAGIC 6. **Deploy to production** after validation
# MAGIC    - Register model to Unity Catalog
# MAGIC    - Deploy to Model Serving endpoint
# MAGIC    - Set up monitoring and alerts
# MAGIC
# MAGIC ## Key Components Now Available
# MAGIC
# MAGIC - ✅ **Clarification Agent**: Validates query clarity with dynamic context loading
# MAGIC - ✅ **Planning Agent**: Creates execution plans with vector search
# MAGIC - ✅ **SQL Synthesis Table Route**: Uses UC metadata functions intelligently
# MAGIC - ✅ **SQL Synthesis Genie Route**: Uses Genie agent tools (optimized for relevant spaces)
# MAGIC - ✅ **SQL Execution Agent**: Executes SQL on delta tables with comprehensive error handling
# MAGIC - ✅ **Result Summarize Agent**: Generates comprehensive natural language summaries (NEW!)
# MAGIC - ✅ **Genie Agent Tools**: Individual Genie space agents created on-demand
# MAGIC - ✅ **Enhanced State Management**: Full observability with AgentState
# MAGIC - ✅ **Clarification Flow**: Preserves context through clarification requests
# MAGIC - ✅ **Streaming Support**: Full streaming with ResponsesAgent wrapper
