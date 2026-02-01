# Databricks notebook source
# DBTITLE 1,Auto reload Local Package
# MAGIC %load_ext autoreload
# MAGIC %autoreload 2

# COMMAND ----------

# DBTITLE 1,Install Packages
# MAGIC %pip install databricks-langchain[memory]==0.12.1 databricks-vectorsearch==0.63 databricks-agents mlflow-skinny[databricks]

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

# DBTITLE 1,Configuration
"""
Configuration loaded from config.py and .env file.
To update configuration, edit .env file instead of this notebook.

RELOADING CONFIGURATION AFTER CHANGING .env:
If you modify the .env file, you need to reload the configuration:
1. Change the line below from: config = get_config()
   To: config = get_config(reload=True)
2. Re-run this cell to reload all config values
3. Re-extract any config variables you need (CATALOG, SCHEMA, etc.)
"""

# Import centralized configuration
import sys
import os

# Add parent directory to path to import config
notebook_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
parent_dir = os.path.dirname(notebook_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from config import get_config

# Load configuration from .env
# NOTE: If you change .env file, call get_config(reload=True) to reload
# Example: config = get_config(reload=True)
config = get_config(reload=True)

# Extract configuration values
CATALOG = config.unity_catalog.catalog_name
SCHEMA = config.unity_catalog.schema_name
TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"
VECTOR_SEARCH_INDEX = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks_vs_index"

# LLM Endpoints - using same endpoint for now, can be customized in .env later
LLM_ENDPOINT_CLARIFICATION = config.llm.endpoint_name
LLM_ENDPOINT_PLANNING = config.llm.endpoint_name
LLM_ENDPOINT_SQL_SYNTHESIS = config.llm.endpoint_name
LLM_ENDPOINT_SUMMARIZE = config.llm.endpoint_name

# Lakebase configuration for state management (from .env)
LAKEBASE_INSTANCE_NAME = config.lakebase.instance_name
EMBEDDING_ENDPOINT = config.lakebase.embedding_endpoint
EMBEDDING_DIMS = config.lakebase.embedding_dims

# Table Metadata configuration (from .env)
SQL_WAREHOUSE_ID = config.table_metadata.sql_warehouse_id
GENIE_SPACE_ID = config.table_metadata.genie_space_ids
# Print configuration summary
config.print_summary()

# COMMAND ----------

# DBTITLE 1,ONE-TIME SETUP: Initialize Lakebase Tables for State Management
"""
IMPORTANT: Run this cell ONCE to set up Lakebase tables for state management.

This creates:
1. checkpoints table - For short-term memory (multi-turn conversations)
2. store table - For long-term memory (user preferences with semantic search)

Prerequisites:
- Lakebase instance must be created in: SQL Warehouses -> Lakebase Postgres -> Create database instance
- Update LAKEBASE_INSTANCE_NAME above with your instance name
"""

# Uncomment and run ONCE to initialize tables
# from databricks_langchain import CheckpointSaver, DatabricksStore
#
# print("Initializing Lakebase tables...")
# print(f"Instance: {LAKEBASE_INSTANCE_NAME}")
#
# # Setup checkpoint table for short-term memory
# with CheckpointSaver(instance_name=LAKEBASE_INSTANCE_NAME) as saver:
#     saver.setup()
#     print("✓ Checkpoint table created/verified")
#
# # Setup store table for long-term memory
# store = DatabricksStore(
#     instance_name=LAKEBASE_INSTANCE_NAME,
#     embedding_endpoint=EMBEDDING_ENDPOINT,
#     embedding_dims=EMBEDDING_DIMS,
# )
# store.setup()
# print("✓ Store table created/verified")
#
# print("\n✅ Lakebase tables are ready for state management!")
# print("="*80)

# COMMAND ----------

# DBTITLE 1,ONE-TIME SETUP: Register Unity Catalog Functions for Metadata Querying
# """
# Register UC functions that will be used as tools by the SQL Synthesis Agent.

# These UC functions query different levels of the enriched genie docs chunks table:
# 1. get_space_summary: High-level space information
# 2. get_table_overview: Table-level metadata
# 3. get_column_detail: Column-level metadata
# 4. get_space_details: Complete metadata (last resort - token intensive)

# All functions use LANGUAGE SQL for better performance and compatibility.
# """

# print("="*80)
# print("REGISTERING UNITY CATALOG FUNCTIONS")
# print("="*80)
# print(f"Target table: {TABLE_NAME}")
# print(f"Functions will be created in: {CATALOG}.{SCHEMA}")
# print("="*80)

# # Optional: Drop existing functions if you need to recreate them
# # Uncomment these lines if you need to drop and recreate the functions
# # spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_space_summary')
# # spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_table_overview')
# # spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_column_detail')
# # spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_space_details')

# # UC Function 1: get_space_summary (SQL scalar function)
# spark.sql(f"""
# CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_space_summary(
#     space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs to query, or "null" to retrieve all spaces. Example: ["space_1", "space_2"] or "null"'
# )
# RETURNS STRING
# LANGUAGE SQL
# COMMENT 'Get high-level summary of Genie spaces. Returns JSON with space summaries including chunk_id, chunk_type, space_title, and content.'
# RETURN
#     SELECT COALESCE(
#         to_json(
#             map_from_entries(
#                 collect_list(
#                     struct(
#                         space_id,
#                         named_struct(
#                             'chunk_id', chunk_id,
#                             'chunk_type', chunk_type,
#                             'space_title', space_title,
#                             'content', searchable_content
#                         )
#                     )
#                 )
#             )
#         ),
#         '{{}}'
#     ) as result
#     FROM {TABLE_NAME}
#     WHERE chunk_type = 'space_summary'
#     AND (
#         space_ids_json IS NULL 
#         OR TRIM(LOWER(space_ids_json)) IN ('null', 'none', '')
#         OR array_contains(from_json(space_ids_json, 'array<string>'), space_id)
#     )
# """)
# print("✓ Registered: get_space_summary")

# # UC Function 2: get_table_overview (SQL scalar function with grouping)
# spark.sql(f"""
# CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_table_overview(
#     space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs to query (required, prefer single space). Example: ["space_1"]',
#     table_names_json STRING DEFAULT 'null' COMMENT 'JSON array of table names to filter, or "null" for all tables in the specified spaces. Example: ["table1", "table2"] or "null"'
# )
# RETURNS STRING
# LANGUAGE SQL
# COMMENT 'Get table-level metadata for specific Genie spaces. Returns JSON with table metadata including chunk_id, chunk_type, table_name, and content grouped by space.'
# RETURN
#     SELECT COALESCE(
#         to_json(
#             map_from_entries(
#                 collect_list(
#                     struct(
#                         space_id,
#                         named_struct(
#                             'space_title', space_title,
#                             'tables', tables
#                         )
#                     )
#                 )
#             )
#         ),
#         '{{}}'
#     ) as result
#     FROM (
#         SELECT 
#             space_id,
#             first(space_title) as space_title,
#             collect_list(
#                 named_struct(
#                     'chunk_id', chunk_id,
#                     'chunk_type', chunk_type,
#                     'table_name', table_name,
#                     'content', searchable_content
#                 )
#             ) as tables
#         FROM {TABLE_NAME}
#         WHERE chunk_type = 'table_overview'
#         AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
#         AND (
#             table_names_json IS NULL 
#             OR TRIM(LOWER(table_names_json)) IN ('null', 'none', '')
#             OR array_contains(from_json(table_names_json, 'array<string>'), table_name)
#         )
#         GROUP BY space_id
#     )
# """)
# print("✓ Registered: get_table_overview")

# # UC Function 3: get_column_detail (SQL scalar function with grouping)
# spark.sql(f"""
# CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_column_detail(
#     space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs to query (required, prefer single space). Example: ["space_1"]',
#     table_names_json STRING DEFAULT 'null' COMMENT 'JSON array of table names to filter (required, prefer single table). Example: ["table1"]',
#     column_names_json STRING DEFAULT 'null' COMMENT 'JSON array of column names to filter, or "null" for all columns in the specified tables. Example: ["col1", "col2"] or "null"'
# )
# RETURNS STRING
# LANGUAGE SQL
# COMMENT 'Get column-level metadata for specific Genie spaces. Returns JSON with column metadata including chunk_id, chunk_type, table_name, column_name, and content grouped by space.'
# RETURN
#     SELECT COALESCE(
#         to_json(
#             map_from_entries(
#                 collect_list(
#                     struct(
#                         space_id,
#                         named_struct(
#                             'space_title', space_title,
#                             'columns', columns
#                         )
#                     )
#                 )
#             )
#         ),
#         '{{}}'
#     ) as result
#     FROM (
#         SELECT 
#             space_id,
#             first(space_title) as space_title,
#             collect_list(
#                 named_struct(
#                     'chunk_id', chunk_id,
#                     'chunk_type', chunk_type,
#                     'table_name', table_name,
#                     'column_name', column_name,
#                     'content', searchable_content
#                 )
#             ) as columns
#         FROM {TABLE_NAME}
#         WHERE chunk_type = 'column_detail'
#         AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
#         AND array_contains(from_json(table_names_json, 'array<string>'), table_name)
#         AND (
#             column_names_json IS NULL 
#             OR TRIM(LOWER(column_names_json)) IN ('null', 'none', '')
#             OR array_contains(from_json(column_names_json, 'array<string>'), column_name)
#         )
#         GROUP BY space_id
#     )
# """)
# print("✓ Registered: get_column_detail")

# # UC Function 4: get_space_details (SQL scalar function - last resort)
# spark.sql(f"""
# CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_space_details(
#     space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs to query (required). Example: ["space_1", "space_2"]. WARNING: Returns large metadata - use as LAST RESORT.'
# )
# RETURNS STRING
# LANGUAGE SQL
# COMMENT 'Get complete metadata for specific Genie spaces - use as LAST RESORT (token intensive). Returns JSON with complete space metadata including chunk_id, chunk_type, space_title, and all available metadata content.'
# RETURN
#     SELECT COALESCE(
#         to_json(
#             map_from_entries(
#                 collect_list(
#                     struct(
#                         space_id,
#                         named_struct(
#                             'chunk_id', chunk_id,
#                             'chunk_type', chunk_type,
#                             'space_title', space_title,
#                             'complete_metadata', searchable_content
#                         )
#                     )
#                 )
#             )
#         ),
#         '{{}}'
#     ) as result
#     FROM {TABLE_NAME}
#     WHERE chunk_type = 'space_details'
#     AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
# """)
# print("✓ Registered: get_space_details")

# print("\n" + "="*80)
# print("✅ ALL 4 UC FUNCTIONS REGISTERED SUCCESSFULLY!")
# print("="*80)
# print("Functions available for SQL Synthesis Agent:")
# print(f"  1. {CATALOG}.{SCHEMA}.get_space_summary")
# print(f"  2. {CATALOG}.{SCHEMA}.get_table_overview")
# print(f"  3. {CATALOG}.{SCHEMA}.get_column_detail")
# print(f"  4. {CATALOG}.{SCHEMA}.get_space_details")
# print("="*80)

# COMMAND ----------

# DBTITLE 1,📝💾%%writefile agent.py
#%%writefile agent.py
"""
Super Agent (Hybrid Architecture) - Multi-Agent System Orchestrator

This notebook implements a hybrid architecture combining:
- OOP agent classes (from agent.py) for modularity and reusability
- Explicit state management (from Super_Agent.py) for observability and debugging

Architecture Benefits:
1. ✅ OOP modularity for agent logic - Easy to test and maintain
2. ✅ Explicit state for observability - Clear debugging and monitoring
3. ✅ Best practices from both approaches
4. ✅ Production-ready with rapid development capabilities

Components:
1. Clarification Agent - Validates query clarity (OOP class)
2. Planning Agent - Creates execution plan and identifies relevant spaces (OOP class)
3. SQL Synthesis Agent (Table Route) - Generates SQL using UC tools (OOP class)
4. SQL Synthesis Agent (Genie Route) - Generates SQL using Genie agents (OOP class)
5. SQL Execution Agent - Executes SQL and returns results (OOP class)

The Super Agent uses LangGraph with explicit state tracking for orchestration.
"""

import json
from typing import Dict, List, Optional, Any, Annotated, Literal, Generator
from typing_extensions import TypedDict
import operator
from uuid import uuid4
import re
from functools import partial
from databricks_langchain import (
    ChatDatabricks,
    VectorSearchRetrieverTool,
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
    CheckpointSaver,  # For short-term memory (distributed serving)
    DatabricksStore,  # For long-term memory (user preferences)
)

# Import conversation management modules
import sys
from pathlib import Path
# Add kumc_poc to path if not already present
kumc_poc_path = str(Path(__file__).parent.parent / "kumc_poc") if '__file__' in globals() else "../kumc_poc"
if kumc_poc_path not in sys.path:
    sys.path.insert(0, kumc_poc_path)

# NOTE: No imports from kumc_poc - all TypedDicts and logic are inline
# This simplifies the agent and makes it self-contained
from databricks_langchain.genie import GenieAgent
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel, RunnableConfig
from langchain_core.tools import tool
import mlflow
import logging

# LangGraph imports
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

# MLflow ResponsesAgent imports
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    output_to_responses_items_stream,
    to_chat_completions_input,
)

# Setup logging
logger = logging.getLogger(__name__)


########################################
# Configuration Loading with ModelConfig
########################################

from mlflow.models import ModelConfig

# Development configuration (used for local testing)
# When deployed, this will be overridden by the config passed to log_model()
development_config = {
    # Unity Catalog Configuration
    "catalog_name": "yyang",
    "schema_name": "multi_agent_genie",
    
    # LLM Endpoint Configuration
    "llm_endpoint": "databricks-claude-sonnet-4-5",
    
    # Vector Search Configuration
    "vs_endpoint_name": "genie_multi_agent_vs",
    "embedding_model": "databricks-gte-large-en",
    
    # Lakebase Configuration (for State Management)
    "lakebase_instance_name": "multi-agent-genie-system-state-db",
    "lakebase_embedding_endpoint": "databricks-gte-large-en",
    "lakebase_embedding_dims": 1024,
    
    # Genie Space IDs
    "genie_space_ids": [
        "01f0eab621401f9faa11e680f5a2bcd0",
        "01f0eababd9f1bcab5dea65cf67e48e3",
        "01f0eac186d11b9897bc1d43836cc4e1"
    ],
    
    # SQL Warehouse ID
    "sql_warehouse_id": "148ccb90800933a1",
    
    # Table Metadata Enrichment
    "sample_size": 20,
    "max_unique_values": 20,
}

# Initialize ModelConfig
# For local development: Uses development_config above
# For Model Serving: Uses config passed during mlflow.pyfunc.log_model(model_config=...)
model_config = ModelConfig(development_config=development_config)

logger.info("="*80)
logger.info("CONFIGURATION LOADED via ModelConfig")
logger.info("="*80)

# Extract configuration values
CATALOG = model_config.get("catalog_name")
SCHEMA = model_config.get("schema_name")
TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"
VECTOR_SEARCH_INDEX = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks_vs_index"

# LLM Endpoints
LLM_ENDPOINT_CLARIFICATION = model_config.get("llm_endpoint")
LLM_ENDPOINT_PLANNING = model_config.get("llm_endpoint")
LLM_ENDPOINT_SQL_SYNTHESIS = model_config.get("llm_endpoint")
LLM_ENDPOINT_SUMMARIZE = model_config.get("llm_endpoint")

# Lakebase configuration for state management
LAKEBASE_INSTANCE_NAME = model_config.get("lakebase_instance_name")
EMBEDDING_ENDPOINT = model_config.get("lakebase_embedding_endpoint")
EMBEDDING_DIMS = model_config.get("lakebase_embedding_dims")

# Genie space IDs
GENIE_SPACE_IDS = model_config.get("genie_space_ids")

# UC Functions
UC_FUNCTION_NAMES = [
    f"{CATALOG}.{SCHEMA}.get_space_summary",
    f"{CATALOG}.{SCHEMA}.get_table_overview",
    f"{CATALOG}.{SCHEMA}.get_column_detail",
    f"{CATALOG}.{SCHEMA}.get_space_details",
]

logger.info(f"Catalog: {CATALOG}, Schema: {SCHEMA}")
logger.info(f"Lakebase: {LAKEBASE_INSTANCE_NAME}")
logger.info(f"Genie Spaces: {len(GENIE_SPACE_IDS)} spaces configured")
logger.info("="*80)

# Initialize UC Function Client
client = DatabricksFunctionClient()
set_uc_function_client(client)

logger.info(f"Configuration loaded: Catalog={CATALOG}, Schema={SCHEMA}, Lakebase={LAKEBASE_INSTANCE_NAME}")

print("✓ All dependencies imported successfully (including memory support)")
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
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()

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

def load_space_context(table_name: str) -> Dict[str, str]:
    """
    Load space context from Delta table.
    Called fresh on each request - no caching for dynamic refresh.
    
    Args:
        table_name: Full table name (catalog.schema.table)
        
    Returns:
        Dictionary mapping space_id to searchable_content
    """
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    
    df = spark.sql(f"""
        SELECT space_id, searchable_content
        FROM {table_name}
        WHERE chunk_type = 'space_summary'
    """)
    
    context = {row["space_id"]: row["searchable_content"] 
               for row in df.collect()}
    
    print(f"✓ Loaded {len(context)} Genie spaces for context")
    return context

# Note: Context is now loaded dynamically in clarification_node
# This allows refresh without model redeployment
# ==============================================================================
# Inline TypedDicts for Unified Agent (No kumc_poc imports)
# ==============================================================================

from typing import TypedDict, Optional, List, Dict, Any, Literal, Annotated
from datetime import datetime
import operator
import uuid as uuid_module

class ConversationTurn(TypedDict):
    """
    Represents a single conversation turn with all its context.
    Inline definition for simplified unified agent.
    """
    turn_id: str
    query: str
    intent_type: Literal["new_question", "refinement", "continuation"]
    parent_turn_id: Optional[str]
    context_summary: Optional[str]
    timestamp: str  # ISO format datetime string
    triggered_clarification: Optional[bool]
    metadata: Optional[Dict[str, Any]]

class ClarificationRequest(TypedDict):
    """Unified clarification request object."""
    reason: str
    options: List[str]
    turn_id: str
    timestamp: str
    best_guess: Optional[str]
    best_guess_confidence: Optional[float]

class IntentMetadata(TypedDict):
    """Intent metadata for business logic."""
    intent_type: Literal["new_question", "refinement", "continuation"]
    confidence: float
    reasoning: str
    topic_change_score: float
    domain: Optional[str]
    operation: Optional[str]
    complexity: Literal["simple", "moderate", "complex"]
    parent_turn_id: Optional[str]

class AgentState(TypedDict):
    """Simplified agent state using turn-based context management."""
    # Turn Management
    current_turn: Optional[ConversationTurn]
    turn_history: Annotated[List[ConversationTurn], operator.add]
    intent_metadata: Optional[IntentMetadata]
    
    # Clarification
    pending_clarification: Optional[ClarificationRequest]
    question_clear: bool
    
    # Deprecated
    original_query: Optional[str]
    
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
    
    # Execution
    execution_result: Optional[Dict[str, Any]]
    execution_error: Optional[str]
    
    # Summary
    final_summary: Optional[str]
    
    # Conversation Management
    user_id: Optional[str]
    thread_id: Optional[str]
    user_preferences: Optional[Dict[str, Any]]
    
    # Control Flow
    next_agent: Optional[str]
    messages: Annotated[List, operator.add]

# Helper functions
def create_conversation_turn(
    query: str,
    intent_type: Literal["new_question", "refinement", "continuation"],
    parent_turn_id: Optional[str] = None,
    context_summary: Optional[str] = None,
    triggered_clarification: bool = False,
    metadata: Optional[Dict[str, Any]] = None
) -> ConversationTurn:
    """Factory function to create a ConversationTurn."""
    return ConversationTurn(
        turn_id=str(uuid_module.uuid4()),
        query=query,
        intent_type=intent_type,
        parent_turn_id=parent_turn_id,
        context_summary=context_summary,
        timestamp=datetime.utcnow().isoformat(),
        triggered_clarification=triggered_clarification,
        metadata=metadata or {}
    )

def create_clarification_request(
    reason: str,
    options: List[str],
    turn_id: str,
    best_guess: Optional[str] = None,
    best_guess_confidence: Optional[float] = None
) -> ClarificationRequest:
    """Factory function to create a ClarificationRequest."""
    return ClarificationRequest(
        reason=reason,
        options=options,
        turn_id=turn_id,
        timestamp=datetime.utcnow().isoformat(),
        best_guess=best_guess,
        best_guess_confidence=best_guess_confidence
    )

def format_clarification_message(clarification: ClarificationRequest) -> str:
    """Format a clarification request into a user-friendly message."""
    message = f"I need clarification: {clarification['reason']}\n\n"
    message += "Please choose one of the following options or provide your own clarification:\n"
    for i, option in enumerate(clarification['options'], 1):
        message += f"{i}. {option}\n"
    return message

def get_reset_state_template() -> Dict[str, Any]:
    """Get template for resetting per-query execution fields."""
    return {
        "pending_clarification": None,
        "question_clear": False,
        "plan": None,
        "sub_questions": None,
        "requires_multiple_spaces": None,
        "relevant_space_ids": None,
        "relevant_spaces": None,
        "vector_search_relevant_spaces_info": None,
        "requires_join": None,
        "join_strategy": None,
        "execution_plan": None,
        "genie_route_plan": None,
        "sql_query": None,
        "sql_synthesis_explanation": None,
        "synthesis_error": None,
        "execution_result": None,
        "execution_error": None,
        "final_summary": None,
    }

print("✓ Inline TypedDicts defined (no kumc_poc imports)")

# State Reset Template
# All per-query execution fields that should be cleared for each new query.
# This prevents stale data from persisting across queries when using CheckpointSaver.
# Used by both Model Serving (run_agent) and local testing (invoke_super_agent_hybrid).
# Use the shared reset state template from conversation_models
RESET_STATE_TEMPLATE = get_reset_state_template()

# NOTE: Turn-based fields (current_turn, turn_history, intent_metadata) are NOT reset
# They are managed by unified_intent_context_clarification_node and persist across queries

# For reference, the template includes per-query fields (see conversation_models.get_reset_state_template()):
# RESET_STATE_TEMPLATE = {
    # Clarification fields (per-query) - SIMPLIFIED from 7+ legacy fields to 2
    # "pending_clarification": None,
    # "question_clear": False,
    
    # Planning fields (per-query)
    # "plan": None,
    # "sub_questions": None,
    # "requires_multiple_spaces": None,
    # "relevant_space_ids": None,
    # "relevant_spaces": None,
    # "vector_search_relevant_spaces_info": None,
    # "requires_join": None,
    # "join_strategy": None,
    # "execution_plan": None,
    # "genie_route_plan": None,
    
    # SQL fields (per-query)
    # "sql_query": None,
    # "sql_synthesis_explanation": None,
    # "synthesis_error": None,
    
    # Execution fields (per-query)
    # "execution_result": None,
    # "execution_error": None,
    
    # Summary (per-query)
    # "final_summary": None,
# }

# Fields intentionally NOT in reset template:
# 
# NEW TURN-BASED FIELDS (persist across queries via CheckpointSaver):
# - current_turn: Set by intent_detection_node for each query
# - turn_history: Accumulated by reducer with operator.add, persists across conversation
# - intent_metadata: Set by intent_detection_node for each query
#
# DEPRECATED LEGACY FIELDS (removed from AgentState):
# - clarification_count: Replaced by adaptive_clarification_strategy() + turn_history
# - last_clarified_query: Replaced by turn_history with triggered_clarification flag
# - combined_query_context: Replaced by current_turn.context_summary (LLM-generated)
# - clarification_needed (as state field): Replaced by pending_clarification object
# - clarification_options (as state field): Replaced by pending_clarification object
#
# DEPRECATED BUT KEPT FOR BACKWARD COMPATIBILITY:
# - original_query: Kept in AgentState but deprecated. Use messages array instead.
#   This field is still set in initial_state for compatibility with legacy code.
#
# PERSISTENT FIELDS (never reset):
# - messages: Managed by operator.add in AgentState, persists across conversation
# - user_id, thread_id, user_preferences: Identity/context, persists for entire conversation
# - next_agent: Control flow field, managed by nodes and routing logic (not in reset template)

print("✓ State reset template defined for per-query field clearing")

class ClarificationAgent:
    """
    Agent responsible for checking query clarity.
    
    Hybrid approach: Can accept context directly (for testing) or load from table (for production).
    
    Usage:
        # Testing: Pass mock context
        agent = ClarificationAgent(llm, {"space1": "mock data"})
        
        # Production: Load from table
        agent = ClarificationAgent.from_table(llm, TABLE_NAME)
    """
    
    def __init__(self, llm: Runnable, context: Dict[str, str]):
        """
        Initialize with context directly.
        
        Args:
            llm: Language model for clarity checking
            context: Dictionary mapping space_id to searchable_content
        """
        self.llm = llm
        self.context = context
        self.name = "Clarification"
    
    @classmethod
    def from_table(cls, llm: Runnable, table_name: str):
        """
        Factory method to create agent by loading context from Delta table.
        Loads fresh context on each call - no caching for dynamic refresh.
        
        Args:
            llm: Language model for clarity checking
            table_name: Full table name (catalog.schema.table)
            
        Returns:
            ClarificationAgent instance with fresh context
        """
        context = load_space_context(table_name)
        return cls(llm, context)
    
    def check_clarity(self, query: str, context_summary: str = None) -> Dict[str, Any]:
        """
        Check if the user query is clear and answerable.
        
        NOTE: Clarification limiting is now handled by adaptive_clarification_strategy()
        in the clarification_node, not here. This agent only assesses clarity.
        
        Args:
            query: User's current question
            context_summary: Optional context summary from intent detection (includes conversation history)
            
        Returns:
            Dictionary with clarity analysis
        """
        
        # Use context_summary if available (includes conversation history), otherwise use raw query
        analysis_query = context_summary or query
        
        clarity_prompt = f"""
Analyze the following question for clarity and specificity based on the context.

IMPORTANT: Only mark as unclear if the question is TRULY VAGUE or IMPOSSIBLE to answer.
Be lenient - if the question can reasonably be answered with the available data, mark it as clear.

Current User Query: {query}

Full Context (includes conversation history if available):
{analysis_query}

Available Data Sources:
{json.dumps(self.context, indent=2)}

Determine if:
1. The question is clear and answerable as-is (BE LENIENT - default to TRUE)
2. The question is TRULY VAGUE and needs critical clarification (ONLY if essential information is missing)
3. If the question mentions any metrics/dimensions/filters that can be mapped to available data with certain confidence, mark it as CLEAR; otherwise, mark it as UNCLEAR and ask for clarification.
4. Consider the conversation context - if this is a refinement or follow-up with adequate context, mark it as CLEAR.


If clarification is truly needed, provide:
- A brief explanation of what's critically unclear
- 2-3 specific clarification options the user can choose from

Additionally, always provide:
- ambiguity_score: A score from 0.0 to 1.0 indicating query ambiguity
  * 0.0-0.3: Clear, all key information present
  * 0.3-0.5: Minor ambiguity, can reasonably infer meaning
  * 0.5-0.7: Moderate ambiguity, multiple possible interpretations
  * 0.7-1.0: High ambiguity, critical information missing
- best_guess: Your best interpretation of the query (state specific assumptions being made)
- best_guess_confidence: Confidence in your interpretation from 0.0 to 1.0
  * 0.0-0.3: Low confidence, many assumptions required
  * 0.3-0.7: Moderate confidence, some assumptions required
  * 0.7-1.0: High confidence, minimal assumptions required

Return your analysis as JSON:
{{
    "question_clear": true/false,
    "clarification_needed": "explanation if unclear (null if clear)",
    "clarification_options": ["option 1", "option 2", "option 3"] or null,
    "ambiguity_score": 0.0-1.0,
    "best_guess": "interpretation with specific assumptions stated",
    "best_guess_confidence": 0.0-1.0
}}

Only return valid JSON, no explanations.
"""
        
        response = self.llm.invoke(clarity_prompt)
        content = response.content.strip()
        
        # Use regex to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # No code blocks, assume entire content is JSON
            json_str = content
        
        # Remove any trailing commas before ] or }
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        
        try:
            clarity_result = json.loads(json_str)
            return clarity_result
        except json.JSONDecodeError as e:
            print(f"⚠ Clarification JSON parsing error at position {e.pos}: {e.msg}")
            print(f"Raw content (first 300 chars): {content[:300]}")
            print(f"Defaulting to question_clear=True")
            return {"question_clear": True}
    
    def __call__(self, query: str, context_summary: str = None) -> Dict[str, Any]:
        """Make agent callable for easy invocation."""
        return self.check_clarity(query, context_summary)

print("✓ ClarificationAgent class defined")
class PlanningAgent:
    """
    Agent responsible for query analysis and execution planning.
    
    OOP design with vector search integration.
    """
    
    def __init__(self, llm: Runnable, vector_search_index: str):
        self.llm = llm
        self.vector_search_index = vector_search_index
        self.name = "Planning"
    
    def search_relevant_spaces(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """
        Search for relevant Genie spaces using vector search.
        
        Args:
            query: User's question
            num_results: Number of results to return
            
        Returns:
            List of relevant space dictionaries
        """
        vs_tool = VectorSearchRetrieverTool(
            index_name=self.vector_search_index,
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
            print(doc)
            relevant_spaces.append({
                "space_id": doc.metadata.get("space_id", ""),
                "space_title": doc.metadata.get("space_title", ""),
                "searchable_content": doc.page_content,
                "score": doc.metadata.get("score", 0.0)
            })
        
        return relevant_spaces
    
    def create_execution_plan(
        self, 
        query: str, 
        relevant_spaces: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Create execution plan based on query and relevant spaces.
        
        Args:
            query: User's question
            relevant_spaces: List of relevant Genie spaces
            
        Returns:
            Dictionary with execution plan
        """
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
    - always populate the join_strategy field in the JSON output.
5. Execution plan: A brief description of how to execute the plan.
    - For genie_route: Return "genie_route_plan": {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}}
    - For table_route: Return "genie_route_plan": null
    - Each partial_question should be similar to original but scoped to that space
    - Add "Please limit to top 10 rows" to each partial question

Return your analysis as JSON:
{{
    "original_query": "{query}",
    "vector_search_relevant_spaces_info":{[{sp['space_id']: sp['space_title']} for sp in relevant_spaces]},
    "question_clear": true,
    "sub_questions": ["sub-question 1", "sub-question 2", ...],
    "requires_multiple_spaces": true/false,
    "relevant_space_ids": ["space_id_1", "space_id_2", ...],
    "requires_join": true/false,
    "join_strategy": "table_route" or "genie_route",
    "execution_plan": "Brief description of execution plan",
    "genie_route_plan": {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}} or null
}}

Only return valid JSON, no explanations.
"""
        
        response = self.llm.invoke(planning_prompt)
        content = response.content.strip()
        
        # Use regex to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # No code blocks, assume entire content is JSON
            json_str = content
        
        # Remove any trailing commas before ] or }
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        
        try:
            plan_result = json.loads(json_str)
            return plan_result
        except json.JSONDecodeError as e:
            print(f"❌ Planning JSON parsing error at position {e.pos}: {e.msg}")
            print(f"Raw content (first 500 chars):\n{content[:500]}")
            print(f"Cleaned JSON (first 500 chars):\n{json_str[:500]}")
            
            # Try one more time with even more aggressive cleaning
            try:
                # Remove comments
                json_str_clean = re.sub(r'//.*$', '', json_str, flags=re.MULTILINE)
                # Remove trailing commas again
                json_str_clean = re.sub(r',(\s*[}\]])', r'\1', json_str_clean)
                plan_result = json.loads(json_str_clean)
                print("✓ Successfully parsed JSON after aggressive cleaning")
                return plan_result
            except:
                raise e  # Re-raise original error
    
    def __call__(self, query: str) -> Dict[str, Any]:
        """
        Analyze query and create execution plan.
        
        Returns:
            Complete execution plan with relevant spaces
        """
        # Search for relevant spaces
        relevant_spaces = self.search_relevant_spaces(query)
        
        # Create execution plan
        plan = self.create_execution_plan(query, relevant_spaces)
        
        return plan

print("✓ PlanningAgent class defined")
class SQLSynthesisTableAgent:
    """
    Agent responsible for fast SQL synthesis using UC function tools.
    
    OOP design with UC toolkit integration.
    """
    
    def __init__(
        self, 
        llm: Runnable, 
        catalog: str, 
        schema: str
    ):
        self.llm = llm
        self.catalog = catalog
        self.schema = schema
        self.name = "SQLSynthesisTable"
        
        # Initialize UC Function Client
        client = DatabricksFunctionClient()
        set_uc_function_client(client)
        
        # Create UC Function Toolkit
        uc_function_names = [
            f"{catalog}.{schema}.get_space_summary",
            f"{catalog}.{schema}.get_table_overview",
            f"{catalog}.{schema}.get_column_detail",
            f"{catalog}.{schema}.get_space_details",
        ]
        
        self.uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
        self.tools = self.uc_toolkit.tools
        
        # Create SQL synthesis agent with tools
        self.agent = create_agent(
            model=llm,
            tools=self.tools,
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
                "4. At last, if you still cannot find enough metadata in relevant spaces provided, dont stuck there. Expand the searching scope to all spaces mentioned in the execution plan's 'vector_search_relevant_spaces_info' field. Extract the space_id from 'vector_search_relevant_spaces_info'. \n"
                "5. Generate complete, executable SQL\n\n"

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
                "- Return your response with:\n"
                "1. Your explanations; If SQL cannot be generated, explain what metadata is missing\n"
                "2. The final SQL query in a ```sql code block\n\n"
            )
        )
    
    def synthesize_sql(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synthesize SQL query based on execution plan.
        
        Args:
            plan: Execution plan from planning agent
            
        Returns:
            Dictionary with:
            - sql: str - Extracted SQL query (None if cannot generate)
            - explanation: str - Agent's explanation/reasoning
            - has_sql: bool - Whether SQL was successfully extracted
        """
        # # Prepare plan summary for agent
        # plan_summary = {
        #     "original_query": plan.get("original_query", ""),
        #     "vector_search_relevant_spaces_info": plan.get("vector_search_relevant_spaces_info", []),
        #     "relevant_space_ids": plan.get("relevant_space_ids", []),
        #     "execution_plan": plan.get("execution_plan", ""),
        #     "requires_join": plan.get("requires_join", False),
        #     "sub_questions": plan.get("sub_questions", [])
        # }
        plan_result = plan
        # Invoke agent
        agent_message = {
            "messages": [
                {
                    "role": "user",
                    "content": f"""
Generate a SQL query to answer the question according to the Query Plan:
{json.dumps(plan_result, indent=2)}

Use your available UC function tools to gather metadata intelligently.
"""
                }
            ]
        }
        
        result = self.agent.invoke(agent_message)
        
        # Extract SQL and explanation from response
        if result and "messages" in result:
            final_content = result["messages"][-1].content
            original_content = final_content
            
            sql_query = None
            has_sql = False
            
            # Try to extract SQL from markdown if present
            if "```sql" in final_content.lower():
                sql_match = re.search(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
                if sql_match:
                    sql_query = sql_match.group(1).strip()
                    has_sql = True
                    # Remove SQL block from content to get explanation
                    final_content = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL)
            elif "```" in final_content:
                sql_match = re.search(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
                if sql_match:
                    # Check if it looks like SQL
                    potential_sql = sql_match.group(1).strip()
                    if any(keyword in potential_sql.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                        sql_query = potential_sql
                        has_sql = True
                        # Remove SQL block from content to get explanation
                        final_content = re.sub(r'```\s*.*?\s*```', '', final_content, flags=re.DOTALL)
            
            # Clean up explanation
            explanation = final_content.strip()
            if not explanation:
                explanation = original_content if not has_sql else "SQL query generated successfully."
            
            return {
                "sql": sql_query,
                "explanation": explanation,
                "has_sql": has_sql
            }
        else:
            raise Exception("No response from agent")
    
    def __call__(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Make agent callable."""
        return self.synthesize_sql(plan)

print("✓ SQLSynthesisTableAgent class defined")
class SQLSynthesisGenieAgent:
    """
    Agent responsible for Genie Route SQL synthesis using Genie agents as tools.
    
    EXECUTION MODES:
    ---------------
    1. LangGraph Agent Mode (default via synthesize_sql()):
       - Uses LangGraph agent with tool calling
       - Supports retries, disaster recovery, and adaptive routing
       - Agent decides which tools to call and when
       - Best for complex queries requiring orchestration
    
    2. RunnableParallel Mode (via invoke_genie_agents_parallel()):
       - Uses RunnableParallel for direct parallel execution
       - Faster for simple parallel queries
       - No retry logic or adaptive routing
       - Best for straightforward parallel execution
    
    ARCHITECTURE:
    ------------
    - Upgraded from RunnableLambda to RunnableParallel pattern
    - Each Genie agent is wrapped as both a tool and a parallel executor
    - Supports efficient parallel invocation using LangChain's RunnableParallel
    - Optimized to only create Genie agents for relevant spaces (not all spaces)
    """
    
    def __init__(self, llm: Runnable, relevant_spaces: List[Dict[str, Any]]):
        """
        Initialize SQL Synthesis Genie Agent with tool-calling pattern.
        
        Args:
            llm: Language model for SQL synthesis
            relevant_spaces: List of relevant spaces from PlanningAgent's Vector Search.
                            Each dict should have: space_id, space_title, searchable_content
        """
        self.llm = llm
        self.relevant_spaces = relevant_spaces
        self.name = "SQLSynthesisGenie"
        
        # Create Genie agents and their tool representations
        self.genie_agents = []
        self.genie_agent_tools = []
        self._create_genie_agent_tools()
        
        # Create SQL synthesis agent with Genie agent tools
        self.sql_synthesis_agent = self._create_sql_synthesis_agent()
    
    def _create_genie_agent_tools(self):
        """
        Create Genie agents as tools only for relevant spaces.
        
        Creates both:
        1. Individual tool wrappers for LangGraph agent tool calling
        2. A parallel executor mapping for efficient batch invocation
        
        Upgraded to use RunnableParallel pattern for better parallel execution.
        """
        def enforce_limit(messages, n=5):
            last = messages[-1] if messages else {"content": ""}
            content = last.get("content", "") if isinstance(last, dict) else last.content
            return f"{content}\n\nPlease limit the result to at most {n} rows."
        
        print(f"  Creating Genie agent tools for {len(self.relevant_spaces)} relevant spaces...")
        
        # Dictionary to hold parallel executors: space_id -> runnable
        parallel_executors = {}
        
        for space in self.relevant_spaces:
            space_id = space.get("space_id")
            space_title = space.get("space_title", space_id)
            searchable_content = space.get("searchable_content", "")
            
            if not space_id:
                print(f"  ⚠ Warning: Space missing space_id, skipping: {space}")
                continue
            
            genie_agent_name = f"Genie_{space_title}"
            description = searchable_content
            
            # Create Genie agent
            genie_agent = GenieAgent(
                genie_space_id=space_id,
                genie_agent_name=genie_agent_name,
                description=description,
                include_context=True,
                message_processor=lambda msgs: enforce_limit(msgs, n=5)
            )
            self.genie_agents.append(genie_agent)
            
            # Create agent invoker function using factory pattern to avoid closure issues
            def make_agent_invoker(agent):
                """Factory function to capture agent in closure properly"""
                def invoke_agent(question: str):
                    """Invoke agent with question and return response"""
                    return agent.invoke(
                        {"messages": [{"role": "user", "content": question}]}
                    )
                return invoke_agent
            
            # Create a RunnableLambda for this agent
            agent_runnable = RunnableLambda(make_agent_invoker(genie_agent))
            agent_runnable.name = genie_agent_name
            agent_runnable.description = description
            
            # Store in parallel executors dict
            parallel_executors[space_id] = agent_runnable
            
            # Create tool wrapper for LangGraph agent
            self.genie_agent_tools.append(
                agent_runnable.as_tool(
                    name=genie_agent_name,
                    description=description,
                    arg_types={"question": str}
                )
            )
            
            print(f"  ✓ Created Genie agent tool: {genie_agent_name} ({space_id})")
        
        # Store parallel executors for batch invocation
        self.parallel_executors = parallel_executors
    
    def _create_sql_synthesis_agent(self):
        """
        Create LangGraph SQL Synthesis Agent with Genie agent tools.
        
        Uses Databricks LangGraph SDK with create_agent pattern.
        Pattern copied from test_uc_functions.py lines 1375-1462
        """
        tools = []
        tools.extend(self.genie_agent_tools)
        
        print(f"✓ Created SQL Synthesis Agent with {len(tools)} Genie agent tools")
        
        # Create SQL Synthesis Agent (specialized for multi-agent system)
        sql_synthesis_agent = create_agent(
            model=self.llm,
            tools=tools,
            system_prompt=(
"""You are a SQL synthesis agent, which can take analysis plan, and route queries in parallel to the corresponding Genie Agent.
The Plan given to you is a JSON:
{
'original_query': 'The User's Question',
'vector_search_relevant_spaces_info': [{'space_id': 'space_id_1',
   'space_title': 'space_title_1'},
  {'space_id': 'space_id_2',
   'space_title': 'space_title_2'},
  {'space_id': 'space_id_3',
   'space_title': 'space_title_3'}],
"question_clear": true,
"sub_questions": ["sub-question 1", "sub-question 2", ...],
"requires_multiple_spaces": true/false,
"relevant_space_ids": ["space_id_1", "space_id_2", ...],
"requires_join": true/false,
"join_strategy": "table_route" or "genie_route" or null,
"execution_plan": "Brief description of execution plan",
"genie_route_plan": {'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', 'space_id_3':'partial_question_3', ...} or null,}

## Tool Calling Plan:
1. Under the key of 'genie_route_plan' in the JSON, extracting 'partial_question_1' and feed to the right Genie Agent tool of 'space_id_1' with the input as a string. 
2. In parallel, send all partial_questions to the corresponding Genie Agent tools accordingly using the 'genie_route_plan' as the mapping.
3. You have access to all Genie Agents as tools given to you; locate the proper Genie Agent Tool by searching the 'space_id_1' in the tool's description. After each Genie agent returns result, only extract the SQL string from the Genie tool output JSON {"thinking": thinking, "sql": sql, "answer": answer}.
4. If you find you are still missing necessary analytical components (metrics, filters, dimensions, etc.) to assemble the final SQL, which might be due to some genie agent tool may not have the necessary information being assigned, try to leverage other most likely Genie agents to find the missing pieces.

## Disaster Recovery (DR) Plan:
1. If one Genie agent tool fail to generate a SQL query, allow retry AS IS only one time; 
2. If fail again, try to reframe the partial question 'partial_question_1' according to the error msg returned by the genie tool, e.g., genie tool may say "I dont have information for cost related information", you can remove those components in the 'partial_question_1' which doesn't exist in the genie tool. For example, if the genie tool "Genie_MemberBenefits" doesn't contain benefit cost related information, you can reframe the question by removing the cost-related components in the 'partial_question_1', generate 'partial_question_1_v2' and try again. Only try once;
3. If fail again, return response as is. 


## Overall SQL Synthesis Plan:
Then, you can combine all the SQL pieces into a single SQL query, and return the final SQL query.
OUTPUT REQUIREMENTS:
- Generate complete, executable SQL with:
  * Proper JOINs based on execution plan strategy
  * WHERE clauses for filtering
  * Appropriate aggregations
  * Clear column aliases
  * Always use real column name existed in the data, never make up one
- Return your response with:
1. Your explanation combining both the individual Genie thinking and your own reasoning
2. The final SQL query in a ```sql code block"""
            )
        )
        
        return sql_synthesis_agent
    
    def invoke_genie_agents_parallel(self, genie_route_plan: Dict[str, str]) -> Dict[str, Any]:
        """
        Invoke multiple Genie agents in parallel using RunnableParallel.
        
        This method demonstrates the proper use of RunnableParallel for efficient
        parallel execution of multiple Genie agents simultaneously.
        
        Args:
            genie_route_plan: Dictionary mapping space_id to partial_question
                Example: {
                    "space_01j9t0jhx009k25rvp67y1k7j0": "Get member demographics",
                    "space_01j9t0jhx009k25rvp67y1k7j1": "Get benefit costs"
                }
        
        Returns:
            Dictionary mapping space_id to agent response
            Example: {
                "space_01j9t0jhx009k25rvp67y1k7j0": {...response...},
                "space_01j9t0jhx009k25rvp67y1k7j1": {...response...}
            }
        """
        if not genie_route_plan:
            return {}
        
        # Build parallel tasks that expect a dict input like {"space_id1": "question1", ...}
        parallel_tasks = {}
        for space_id in genie_route_plan.keys():
            if space_id in self.parallel_executors:
                parallel_tasks[space_id] = RunnableLambda(
                    lambda inp, sid=space_id: self.parallel_executors[sid].invoke(inp[sid])
                )
            else:
                print(f"  ⚠ Warning: No executor found for space_id: {space_id}")
        
        if not parallel_tasks:
            print("  ⚠ Warning: No valid parallel tasks to execute")
            return {}
        
        # Create RunnableParallel with all tasks
        parallel_runner = RunnableParallel(**parallel_tasks)
        
        print(f"  🚀 Invoking {len(parallel_tasks)} Genie agents in parallel using RunnableParallel...")
        
        try:
            # Invoke all agents in parallel
            # Now invoke with the actual question mapping
            results = parallel_runner.invoke(genie_route_plan)
            
            print(f"  ✅ Parallel invocation completed for {len(results)} agents")
            print(results)
            return results
            
        except Exception as e:
            print(f"  ❌ Parallel invocation failed: {str(e)}")
            return {}
    
    def synthesize_sql(
        self, 
        plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Synthesize SQL using Genie agents (genie route) with autonomous tool calling.
        
        EXECUTION STRATEGY:
        1. PRIMARY: Try RunnableParallel for fast parallel execution
        2. FALLBACK: Use LangGraph agent with retries and DR if parallel fails
        
        Args:
            plan: Complete plan dictionary from PlanningAgent containing:
                - original_query: Original user question
                - execution_plan: Execution plan description
                - genie_route_plan: Mapping of space_id to partial question
                - vector_search_relevant_spaces_info: List of relevant spaces
                - relevant_space_ids: List of relevant space IDs
                - requires_join: Whether join is needed
                - join_strategy: Join strategy (table_route/genie_route)
            
        Returns:
            Dictionary with:
            - sql: str - Combined SQL query (None if cannot generate)
            - explanation: str - Agent's explanation/reasoning
            - has_sql: bool - Whether SQL was successfully extracted
        """
        # Build the plan result JSON for the agent
        plan_result = plan
        
        print(f"\n{'='*80}")
        print("🤖 SQL Synthesis Agent - Starting...")
        print(f"{'='*80}")
        print(f"Plan: {json.dumps(plan_result, indent=2)}")
        print(f"{'='*80}\n")
        
        # STRATEGY 1: Try RunnableParallel execution first (fast path)
        genie_route_plan = plan_result.get("genie_route_plan", {})
        use_parallel_fallback = False
        
        if genie_route_plan:
            print("🚀 PRIMARY STRATEGY: Attempting RunnableParallel execution...")
            try:
                # Invoke Genie agents in parallel
                parallel_results = self.invoke_genie_agents_parallel(genie_route_plan)
                
                if parallel_results:
                    print(f"  ✅ Parallel execution successful! Got {len(parallel_results)} results")
                    
                    # Extract SQL fragments from parallel results
                    sql_fragments = {}
                    for space_id, result in parallel_results.items():
                        # Extract SQL from Genie agent response
                        if isinstance(result, dict):
                            sql = result.get("sql") or result.get("messages", [{}])[-1].get("content", "")
                        else:
                            sql = str(result)
                        sql_fragments[space_id] = sql
                    
                    # Use LLM to combine SQL fragments
                    print("  🔧 Combining SQL fragments with LLM...")
                    combine_prompt = f"""
You are a SQL expert. Combine the following SQL fragments into a single, executable SQL query.

Original Question: {plan_result.get('original_query', 'N/A')}
Execution Plan: {plan_result.get('execution_plan', 'N/A')}

SQL Fragments from Genie Agents:
{json.dumps(sql_fragments, indent=2)}

Requirements:
- Generate complete, executable SQL with proper JOINs
- Use WHERE clauses for filtering
- Include appropriate aggregations
- Use clear column aliases
- Always use real column names from the data

Return your response with:
1. Brief explanation of your approach
2. The final SQL query in a ```sql code block
"""
                    
                    combine_result = self.llm.invoke(combine_prompt)
                    final_content = combine_result.content.strip() if hasattr(combine_result, 'content') else str(combine_result)
                    
                    # Extract SQL from the combined result
                    sql_query = None
                    has_sql = False
                    explanation = final_content
                    
                    if "```sql" in final_content.lower():
                        sql_match = re.search(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
                        if sql_match:
                            sql_query = sql_match.group(1).strip()
                            has_sql = True
                            explanation = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL)
                    
                    if has_sql and sql_query:
                        print("  ✅ PRIMARY STRATEGY SUCCESS: SQL generated via RunnableParallel")
                        return {
                            "sql": sql_query,
                            "explanation": f"[Parallel Execution] {explanation.strip()}",
                            "has_sql": True
                        }
                    else:
                        print("  ⚠️ PRIMARY STRATEGY: Could not extract SQL from combined results")
                        use_parallel_fallback = True
                else:
                    print("  ⚠️ PRIMARY STRATEGY: Parallel execution returned no results")
                    use_parallel_fallback = True
                    
            except Exception as e:
                print(f"  ❌ PRIMARY STRATEGY FAILED: {str(e)}")
                use_parallel_fallback = True
        else:
            print("  ℹ️ No genie_route_plan provided, skipping parallel execution")
            use_parallel_fallback = True
        
        # STRATEGY 2: Fallback to LangGraph agent (with retries and DR)
        if use_parallel_fallback:
            print("\n🔄 FALLBACK STRATEGY: Using LangGraph agent with retries/DR...")
        
        # Create the message for the agent
        agent_message = {
            "messages": [
                {
                    "role": "user",
                    "content": f"""
Generate a SQL query to answer the question according to the Query Plan:
{json.dumps(plan_result, indent=2)}
"""
                }
            ]
        }
        
        try:
            # Enable MLflow autologging for tracing
            mlflow.langchain.autolog()
            
            # Invoke the agent
            result = self.sql_synthesis_agent.invoke(agent_message)
            
            # Extract SQL from agent result
            # The agent returns {"messages": [...]}
            # Last message contains the final response
            final_message = result["messages"][-1]
            final_content = final_message.content.strip()
            
            print(f"\n{'='*80}")
            if use_parallel_fallback:
                print("✅ FALLBACK STRATEGY SUCCESS: LangGraph agent completed")
            else:
                print("✅ SQL Synthesis Agent completed")
            print(f"{'='*80}")
            print(f"Result: {final_content[:500]}...")
            print(f"{'='*80}\n")
            
            # Extract SQL and explanation from the result
            sql_query = None
            has_sql = False
            explanation = final_content
            
            # Clean markdown if present and extract SQL
            if "```sql" in final_content.lower():
                sql_match = re.search(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
                if sql_match:
                    sql_query = sql_match.group(1).strip()
                    has_sql = True
                    # Remove SQL block to get explanation
                    explanation = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL)
            elif "```" in final_content:
                sql_match = re.search(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
                if sql_match:
                    potential_sql = sql_match.group(1).strip()
                    if any(keyword in potential_sql.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                        sql_query = potential_sql
                        has_sql = True
                        # Remove SQL block to get explanation
                        explanation = re.sub(r'```\s*.*?\s*```', '', final_content, flags=re.DOTALL)
            else:
                # No markdown, check if the entire content is SQL
                if any(keyword in final_content.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                    sql_query = final_content
                    has_sql = True
                    explanation = "SQL query generated successfully by Genie agent tools."
            
            explanation = explanation.strip()
            if not explanation:
                explanation = final_content if not has_sql else "SQL query generated successfully by Genie agent tools."
            
            # Add strategy indicator to explanation
            if use_parallel_fallback:
                explanation = f"[Agent Orchestration - Fallback] {explanation}"
            
            return {
                "sql": sql_query,
                "explanation": explanation,
                "has_sql": has_sql
            }
            
        except Exception as e:
            print(f"\n{'='*80}")
            print("❌ SQL Synthesis Agent failed")
            print(f"{'='*80}")
            print(f"Error: {str(e)}")
            print(f"{'='*80}\n")
            
            return {
                "sql": None,
                "explanation": f"SQL synthesis failed: {str(e)}",
                "has_sql": False
            }
    
    def __call__(
        self, 
        plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make agent callable with plan dictionary."""
        return self.synthesize_sql(plan)

print("✓ SQLSynthesisGenieAgent class defined")
class SQLExecutionAgent:
    """
    Agent responsible for executing SQL queries.
    
    OOP design for clean execution logic.
    Synced with test_uc_functions.py implementation.
    """
    
    def __init__(self):
        self.name = "SQLExecution"
    
    def execute_sql(
        self, 
        sql_query: str, 
        max_rows: int = 100,
        return_format: str = "dict"
    ) -> Dict[str, Any]:
        """
        Execute SQL query on delta tables and return formatted results.
        
        Args:
            sql_query: Support two types: 
                1) The result from invoke the SQL synthesis agent (dict with messages)
                2) The SQL query string (can be raw SQL or contain markdown code blocks)
            max_rows: Maximum number of rows to return (default: 100)
            return_format: Format of the result - "dict", "json", or "markdown"
            
        Returns:
            Dictionary containing:
            - success: bool - Whether execution was successful
            - sql: str - The executed SQL query
            - result: Any - Query results in requested format
            - row_count: int - Number of rows returned
            - columns: List[str] - Column names
            - error: str - Error message if failed (optional)
        """
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        
        # Step 1: Extract SQL from agent result or markdown code blocks if present
        if sql_query and isinstance(sql_query, dict) and "messages" in sql_query:
            sql_query = sql_query["messages"][-1].content
        
        extracted_sql = sql_query.strip()
        
        if "```sql" in extracted_sql.lower():
            # Extract content between ```sql and ```
            sql_match = re.search(r'```sql\s*(.*?)\s*```', extracted_sql, re.IGNORECASE | re.DOTALL)
            if sql_match:
                extracted_sql = sql_match.group(1).strip()
        elif "```" in extracted_sql:
            # Extract any code block
            sql_match = re.search(r'```\s*(.*?)\s*```', extracted_sql, re.DOTALL)
            if sql_match:
                extracted_sql = sql_match.group(1).strip()
        
        # Step 2: Add LIMIT clause if not present (for safety)
        if "limit" not in extracted_sql.lower():
            extracted_sql = f"{extracted_sql.rstrip(';')} LIMIT {max_rows}"
        
        try:
            # Step 3: Execute the SQL query
            print(f"\n{'='*80}")
            print("🔍 EXECUTING SQL QUERY")
            print(f"{'='*80}")
            print(f"SQL:\n{extracted_sql}")
            print(f"{'='*80}\n")
            
            df = spark.sql(extracted_sql)
            
            # Step 4: Collect results
            results_list = df.collect()
            row_count = len(results_list)
            columns = df.columns
            
            print(f"✅ Query executed successfully!")
            print(f"📊 Rows returned: {row_count}")
            print(f"📋 Columns: {', '.join(columns)}\n")
            
            # Step 5: Format results based on return_format
            if return_format == "json":
                result_data = df.toJSON().collect()
            elif return_format == "markdown":
                # Create markdown table
                pandas_df = df.toPandas()
                result_data = pandas_df.to_markdown(index=False)
            else:  # dict (default)
                result_data = [row.asDict() for row in results_list]
            
            # Step 7: Display preview
            print(f"{'='*80}")
            print("📄 RESULTS PREVIEW (first 10 rows)")
            print(f"{'='*80}")
            # df.show(n=min(10, row_count), truncate=False) # comment out to save time.
            print(f"{'='*80}\n")
            
            return {
                "success": True,
                "sql": extracted_sql,
                "result": result_data,
                "row_count": row_count,
                "columns": columns,
            }
            
        except Exception as e:
            # Step 8: Handle errors
            error_msg = str(e)
            print(f"\n{'='*80}")
            print("❌ SQL EXECUTION FAILED")
            print(f"{'='*80}")
            print(f"Error: {error_msg}")
            print(f"{'='*80}\n")
            
            return {
                "success": False,
                "sql": extracted_sql,
                "result": None,
                "row_count": 0,
                "columns": [],
                "error": error_msg
            }
    
    def __call__(self, sql_query: str, max_rows: int = 100, return_format: str = "dict") -> Dict[str, Any]:
        """Make agent callable."""
        return self.execute_sql(sql_query, max_rows, return_format)

print("✓ SQLExecutionAgent class defined")
class ResultSummarizeAgent:
    """
    Agent responsible for generating a final summary of the workflow execution.
    
    Analyzes the entire workflow state and produces a natural language summary
    of what was accomplished, whether successful or not.
    
    OOP design for clean summarization logic.
    """
    
    def __init__(self, llm: Runnable):
        self.name = "ResultSummarize"
        self.llm = llm
    
    @staticmethod
    def _safe_json_dumps(obj: Any, indent: int = 2) -> str:
        """
        Safely serialize objects to JSON, converting dates/datetime to strings.
        
        Args:
            obj: Object to serialize
            indent: JSON indentation level
            
        Returns:
            JSON string with date/datetime objects converted to ISO format strings
        """
        from datetime import date, datetime
        from decimal import Decimal
        
        def default_handler(o):
            if isinstance(o, (date, datetime)):
                return o.isoformat()
            elif isinstance(o, Decimal):
                return float(o)
            else:
                raise TypeError(f'Object of type {o.__class__.__name__} is not JSON serializable')
        
        return json.dumps(obj, indent=indent, default=default_handler)
    
    def generate_summary(self, state: AgentState) -> str:
        """
        Generate a natural language summary of the workflow execution.
        
        Args:
            state: The complete workflow state
            
        Returns:
            String containing natural language summary
        """
        # Build context from state
        summary_prompt = self._build_summary_prompt(state)
        
        # Invoke LLM to generate summary
        response = self.llm.invoke(summary_prompt)
        summary = response.content.strip()
        
        return summary
    
    def _build_summary_prompt(self, state: AgentState) -> str:
        """Build the prompt for summary generation based on state."""
        
        original_query = state.get('original_query', 'N/A')
        question_clear = state.get('question_clear', False)
        pending_clarification = state.get('pending_clarification')
        execution_plan = state.get('execution_plan')
        join_strategy = state.get('join_strategy')
        sql_query = state.get('sql_query')
        sql_explanation = state.get('sql_synthesis_explanation')
        exec_result = state.get('execution_result', {})
        synthesis_error = state.get('synthesis_error')
        execution_error = state.get('execution_error')
        
        prompt = f"""You are a result summarization agent. Generate a concise, natural language summary of what this multi-agent workflow accomplished.

**Original User Query:** {original_query}

**Workflow Execution Details:**

"""
        
        # Add clarification info
        if not question_clear and pending_clarification:
            clarification_reason = pending_clarification.get('reason', 'Query needs clarification')
            prompt += f"""**Status:** Query needs clarification
**Clarification Needed:** {clarification_reason}
**Summary:** The query was too vague or ambiguous. Requested user clarification before proceeding.
"""
        else:
            # Add planning info
            if execution_plan:
                prompt += f"""**Planning:** {execution_plan}
**Strategy:** {join_strategy or 'N/A'}

"""
            
            # Add SQL synthesis info
            if sql_query:
                prompt += f"""**SQL Generation:** ✅ Successful
**SQL Query:** 
```sql
{sql_query}
```

"""
                if sql_explanation:
                    prompt += f"""**SQL Synthesis Explanation:** {sql_explanation[:2000]}{'...' if len(sql_explanation) > 2000 else ''}

"""
                
                # Add execution info
                if exec_result.get('success'):
                    row_count = exec_result.get('row_count', 0)
                    columns = exec_result.get('columns', [])
                    result = exec_result.get('result', [])
                    prompt += f"""**Execution:** ✅ Successful
**Rows:** {row_count} rows returned
**Columns:** {', '.join(columns[:5])}{'...' if len(columns) > 5 else ''}

**Result:** {self._safe_json_dumps(result, indent=2)}
"""
                elif execution_error:
                    prompt += f"""**Execution:** ❌ Failed
**Error:** {execution_error}

"""
            elif synthesis_error:
                prompt += f"""**SQL Generation:** ❌ Failed
**Error:** {synthesis_error}
**Explanation:** {sql_explanation or 'N/A'}

"""
        
        prompt += """
**Task:** Generate a detailed summary in natural language that:
1. Describes what the user asked for
2. Explains what the system did (planning, SQL generation, execution)
3. States the outcome (success with X rows, error, needs clarification, etc.)
4. print out SQL synthesis explanation if any SQL was generated
5. print out SQL if any SQL was generated; make it the code block
6. print out the result itself (like a table).


Keep it concise and user-friendly. 
"""
        
        return prompt
    
    def __call__(self, state: AgentState) -> str:
        """Make agent callable."""
        return self.generate_summary(state)

print("✓ ResultSummarizeAgent class defined")

# ==============================================================================
# DEPRECATED FUNCTIONS (Replaced by Intent Detection Service)
# ==============================================================================
# The following functions have been replaced by the IntentDetectionAgent:
# - find_most_recent_clarification_context() → Intent detection handles this
# - is_new_question() → Intent detection provides intent_type classification
#
# These have been removed to simplify the codebase. See:
# - kumc_poc/intent_detection_service.py for the replacement
# - kumc_poc/conversation_models.py for the new data models
# ==============================================================================

# ==============================================================================
# State Extraction Helpers (Token Optimization)
# ==============================================================================
"""
Minimal state extraction helpers to reduce token usage.

Instead of passing the entire AgentState (25+ fields) to each agent,
these helpers extract only the fields each node actually needs.

Expected savings: 60-70% token reduction per node
"""

def extract_intent_detection_context(state: AgentState) -> dict:
    """
    Extract minimal context for intent detection.
    
    OPTIMIZED: Applies message and turn history truncation
    """
    messages = state.get("messages", [])
    turn_history = state.get("turn_history", [])
    
    return {
        "messages": truncate_message_history(messages, max_turns=5),
        "turn_history": truncate_turn_history(turn_history, max_turns=10),
        "user_id": state.get("user_id"),
        "thread_id": state.get("thread_id"),
        # For logging: track original sizes
        "_original_message_count": len(messages),
        "_original_turn_count": len(turn_history)
    }

def extract_clarification_context(state: AgentState) -> dict:
    """
    Extract minimal context for clarification.
    
    OPTIMIZED: Applies message and turn history truncation
    """
    messages = state.get("messages", [])
    turn_history = state.get("turn_history", [])
    
    return {
        "current_turn": state.get("current_turn"),
        "turn_history": truncate_turn_history(turn_history, max_turns=10),
        "intent_metadata": state.get("intent_metadata"),
        "messages": truncate_message_history(messages, max_turns=5),
        # For logging: track original sizes
        "_original_message_count": len(messages),
        "_original_turn_count": len(turn_history)
    }

def extract_planning_context(state: AgentState) -> dict:
    """Extract minimal context for planning."""
    return {
        "current_turn": state.get("current_turn"),
        "intent_metadata": state.get("intent_metadata"),
        "original_query": state.get("original_query")  # Backward compat
    }

def extract_synthesis_table_context(state: AgentState) -> dict:
    """Extract minimal context for table-based SQL synthesis."""
    return {
        "plan": state.get("plan", {}),
        "relevant_space_ids": state.get("relevant_space_ids", [])
    }

def extract_synthesis_genie_context(state: AgentState) -> dict:
    """Extract minimal context for genie-based SQL synthesis."""
    return {
        "plan": state.get("plan", {}),
        "relevant_spaces": state.get("relevant_spaces", []),
        "genie_route_plan": state.get("genie_route_plan")
    }

def extract_execution_context(state: AgentState) -> dict:
    """Extract minimal context for SQL execution."""
    return {
        "sql_query": state.get("sql_query")
    }

def extract_summarize_context(state: AgentState) -> dict:
    """
    Extract minimal context for result summarization.
    
    OPTIMIZED: Applies message history truncation
    """
    messages = state.get("messages", [])
    
    return {
        "messages": truncate_message_history(messages, max_turns=5),
        "sql_query": state.get("sql_query"),
        "execution_result": state.get("execution_result"),
        "execution_error": state.get("execution_error"),
        "sql_synthesis_explanation": state.get("sql_synthesis_explanation"),
        "synthesis_error": state.get("synthesis_error"),
        # For logging: track original size
        "_original_message_count": len(messages)
    }

print("✓ State extraction helpers defined (for token optimization)")

# ==============================================================================
# Message History Truncation (Token Optimization - Priority 3)
# ==============================================================================
"""
Smart message history truncation to keep only recent turns.

Reduces token usage in long conversations by keeping only:
- All SystemMessage instances (prompts)
- Last N HumanMessage/AIMessage pairs

Example: After 10 turns (20 messages):
- Before: 18K tokens
- After: 6K tokens (67% reduction)
"""

def truncate_message_history(
    messages: List, 
    max_turns: int = 5,
    keep_system: bool = True
) -> List:
    """
    Keep only recent turns + system messages.
    
    Args:
        messages: Full message history
        max_turns: Number of recent turns to keep (default 5)
        keep_system: Whether to preserve all SystemMessage instances
        
    Returns:
        Truncated message list
    """
    if not messages:
        return []
    
    # Separate system messages from conversation
    system_msgs = []
    conversation_msgs = []
    
    for msg in messages:
        if isinstance(msg, SystemMessage) and keep_system:
            system_msgs.append(msg)
        else:
            conversation_msgs.append(msg)
    
    # Keep only last N turns (each turn = HumanMessage + AIMessage pair)
    recent_msgs = conversation_msgs[-(max_turns * 2):] if len(conversation_msgs) > max_turns * 2 else conversation_msgs
    
    return system_msgs + recent_msgs


def truncate_turn_history(
    turn_history: List, 
    max_turns: int = 10
) -> List:
    """
    Keep only recent turns in turn_history.
    
    Args:
        turn_history: Full turn history
        max_turns: Number of recent turns to keep (default 10)
        
    Returns:
        Truncated turn history list
    """
    if not turn_history:
        return []
    
    # Keep only last N turns
    return turn_history[-max_turns:] if len(turn_history) > max_turns else turn_history


print("✓ Message truncation functions defined (keeps last 5 message turns, 10 turn_history)")

# ==============================================================================
# Unified Intent, Context, and Clarification Node (Simplified - No kumc_poc imports)
# ==============================================================================

def check_clarification_rate_limit(turn_history: List[ConversationTurn], window_size: int = 5) -> bool:
    """
    Check if clarification was triggered in the last N turns (sliding window).
    
    Args:
        turn_history: List of conversation turns
        window_size: Number of recent turns to check (default: 5)
    
    Returns:
        True if rate limited (skip clarification), False if ok to clarify
    """
    if not turn_history:
        return False
    
    # Look at last N turns
    recent_turns = turn_history[-window_size:]
    
    # Check if any triggered clarification
    for turn in recent_turns:
        if turn.get("triggered_clarification", False):
            return True  # Rate limited
    
    return False  # OK to clarify


def unified_intent_context_clarification_node(state: AgentState) -> dict:
    """
    Unified node that combines intent detection, context generation, and clarity check.
    
    Single LLM call for:
    1. Intent classification (new_question, refinement, continuation)
    2. Context summary generation
    3. Clarity assessment with rate limiting (max 1 per 5 turns)
    
    Returns: Dictionary with state updates
    """
    from langgraph.config import get_stream_writer
    
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("🎯 UNIFIED INTENT, CONTEXT & CLARIFICATION AGENT")
    print("="*80)
    
    # Get current query from messages
    messages = state.get("messages", [])
    turn_history = state.get("turn_history", [])
    
    human_messages = [m for m in messages if isinstance(m, HumanMessage)]
    current_query = human_messages[-1].content if human_messages else ""
    
    writer({"type": "agent_start", "agent": "unified_intent_context_clarification", "query": current_query})
    
    print(f"Query: {current_query}")
    print(f"Turn history: {len(turn_history)} turns")
    
    # Format conversation context
    conversation_context = ""
    if turn_history:
        conversation_context = "Previous conversation:\n"
        for i, turn in enumerate(turn_history[-5:], 1):  # Last 5 turns
            intent_label = turn['intent_type'].replace('_', ' ').title()
            conversation_context += f"{i}. [{intent_label}] {turn['query']}\n"
            if turn.get('context_summary'):
                conversation_context += f"   Context: {turn['context_summary'][:100]}...\n"
    else:
        conversation_context = "No previous conversation (first query)."
    
    # Load space context for clarity check
    space_context = load_space_context(TABLE_NAME)
    
    # Single unified prompt for intent + context + clarity
    unified_prompt = f"""Analyze the user's query in the context of the conversation history.

Current Query: {current_query}

Conversation History:
{conversation_context}

Available Data Sources:
{json.dumps(space_context, indent=2)}

## Task 1: Classify Intent
Classify the query into ONE of these categories:
1. **new_question**: A completely different topic/domain from previous queries
2. **refinement**: Narrowing/filtering/modifying the previous query on same topic
3. **continuation**: Follow-up exploring same topic from different angle

## Task 2: Generate Context Summary
Create a 2-3 sentence summary that:
- Synthesizes the conversation history
- States clearly what the user wants
- Is actionable for SQL query planning

## Task 3: Check Clarity
Determine if the query is clear enough to generate SQL:
- Is the question clear and answerable as-is? (BE LENIENT - default to TRUE)
- ONLY mark as unclear if CRITICAL information is missing
- If unclear, provide 2-3 specific clarification options

Return ONLY valid JSON:
{{
  "intent_type": "new_question" | "refinement" | "continuation",
  "confidence": 0.95,
  "context_summary": "2-3 sentence summary for planning agent",
  "question_clear": true/false,
  "clarification_reason": "Why unclear (if question_clear=false)",
  "clarification_options": ["Option 1", "Option 2", "Option 3"] or null,
  "metadata": {{
    "domain": "patients | claims | providers | medications | ...",
    "complexity": "simple | moderate | complex",
    "topic_change_score": 0.8
  }}
}}
"""
    
    # Call LLM
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
    
    try:
        print("🤖 Invoking unified LLM call...")
        response = llm.invoke(unified_prompt)
        content = response.content if hasattr(response, 'content') else str(response)
        
        # Parse JSON response
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        result = json.loads(content)
        
        # Extract results
        intent_type = result["intent_type"].lower()
        confidence = result["confidence"]
        context_summary = result["context_summary"]
        question_clear = result["question_clear"]
        clarification_reason = result.get("clarification_reason")
        clarification_options = result.get("clarification_options", [])
        metadata = result.get("metadata", {})
        
        print(f"✓ Intent: {intent_type} (confidence: {confidence:.2f})")
        print(f"  Context: {context_summary[:100]}...")
        print(f"  Question clear: {question_clear}")
        
        # Create conversation turn
        turn = create_conversation_turn(
            query=current_query,
            intent_type=intent_type,
            parent_turn_id=None,  # Could extract from history if needed
            context_summary=context_summary,
            triggered_clarification=False,  # Will be updated if clarification triggered
            metadata=metadata
        )
        
        # Create intent metadata
        intent_metadata = IntentMetadata(
            intent_type=intent_type,
            confidence=confidence,
            reasoning=f"Unified analysis: {intent_type}",
            topic_change_score=metadata.get("topic_change_score", 0.5),
            domain=metadata.get("domain"),
            operation=None,
            complexity=metadata.get("complexity", "moderate"),
            parent_turn_id=None
        )
        
        # Emit events
        writer({
            "type": "intent_detected",
            "intent_type": intent_type,
            "confidence": confidence,
            "complexity": metadata.get("complexity", "moderate")
        })
        
        # Check if clarification needed
        if not question_clear:
            print(f"⚠ Query unclear: {clarification_reason}")
            
            # Check rate limit
            is_rate_limited = check_clarification_rate_limit(turn_history, window_size=5)
            
            if is_rate_limited:
                print("⚠ Clarification rate limit reached (1 per 5 turns)")
                print("  Proceeding with best-effort interpretation")
                
                writer({"type": "clarification_skipped", "reason": "Rate limited (1 per 5 turns)"})
                
                # Force proceed to planning
                return {
                    "current_turn": turn,
                    "turn_history": [turn],
                    "intent_metadata": intent_metadata,
                    "question_clear": True,  # Force clear
                    "pending_clarification": None,
                    "messages": [
                        SystemMessage(content=f"Clarification rate limited. Proceeding with: {context_summary}")
                    ]
                }
            else:
                # OK to clarify
                print("✓ Requesting clarification from user")
                
                # Create clarification request
                clarification_request = create_clarification_request(
                    reason=clarification_reason or "Query needs more specificity",
                    options=clarification_options,
                    turn_id=turn["turn_id"],
                    best_guess=context_summary,
                    best_guess_confidence=confidence
                )
                
                # Mark turn as triggering clarification
                turn["triggered_clarification"] = True
                
                # Format message
                clarification_message = format_clarification_message(clarification_request)
                
                writer({"type": "clarification_requested", "reason": clarification_reason})
                
                return {
                    "current_turn": turn,
                    "turn_history": [turn],
                    "intent_metadata": intent_metadata,
                    "question_clear": False,
                    "pending_clarification": clarification_request,
                    "messages": [
                        AIMessage(content=clarification_message),
                        SystemMessage(content=f"Clarification requested for turn {turn['turn_id']}")
                    ]
                }
        else:
            # Question is clear, proceed to planning
            print("✓ Query is clear - proceeding to planning")
            
            writer({"type": "clarity_analysis", "clear": True, "reasoning": "Query is clear and answerable"})
            
            return {
                "current_turn": turn,
                "turn_history": [turn],
                "intent_metadata": intent_metadata,
                "question_clear": True,
                "pending_clarification": None,
                "messages": [
                    SystemMessage(content=f"Intent: {intent_type}, proceeding to planning")
                ]
            }
        
    except Exception as e:
        print(f"❌ Unified agent error: {e}")
        import traceback
        traceback.print_exc()
        
        # Fallback: create minimal turn and proceed
        turn = create_conversation_turn(
            query=current_query,
            intent_type="new_question",
            context_summary=f"Query: {current_query}",
            triggered_clarification=False,
            metadata={}
        )
        
        intent_metadata = IntentMetadata(
            intent_type="new_question",
            confidence=0.5,
            reasoning=f"Error fallback: {str(e)}",
            topic_change_score=1.0,
            domain=None,
            operation=None,
            complexity="moderate",
            parent_turn_id=None
        )
        
        return {
            "current_turn": turn,
            "turn_history": [turn],
            "intent_metadata": intent_metadata,
            "question_clear": True,  # Proceed despite error
            "pending_clarification": None,
            "messages": [
                SystemMessage(content=f"Unified agent error (proceeding anyway): {str(e)}")
            ]
        }

print("✓ Unified intent, context, and clarification node defined")

# ==============================================================================
# OLD Intent Detection Node (DEPRECATED - kept for reference)
# ==============================================================================

def intent_detection_node_OLD(state: AgentState) -> dict:
    """
    Dedicated node for intent classification and context building.
    
    This runs BEFORE clarification to inform all downstream logic including:
    - Clarification decisions (skip for clarification_response)
    - Planning strategies (different approaches for refinements vs new questions)
    - Business logic (billing, analytics, routing)
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with turn tracking and intent metadata updates
    """
    from langgraph.config import get_stream_writer
    
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("🎯 INTENT DETECTION AGENT (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for intent detection
    context = extract_intent_detection_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    # OPTIMIZATION: Log message and turn history truncation
    original_msg_count = context.get("_original_message_count", 0)
    original_turn_count = context.get("_original_turn_count", 0)
    messages = context.get("messages", [])
    turn_history = context.get("turn_history", [])
    
    if original_msg_count > len(messages):
        saved_pct = ((original_msg_count - len(messages)) / original_msg_count * 100)
        print(f"✂️ Message truncation: {original_msg_count} → {len(messages)} messages ({saved_pct:.0f}% reduction)")
    if original_turn_count > len(turn_history):
        saved_pct = ((original_turn_count - len(turn_history)) / original_turn_count * 100)
        print(f"✂️ Turn history truncation: {original_turn_count} → {len(turn_history)} turns ({saved_pct:.0f}% reduction)")
    
    # Get current query and conversation context
    # IMPORTANT: Extract only from HumanMessage to avoid capturing SystemMessage or AIMessage
    human_messages = [m for m in messages if isinstance(m, HumanMessage)]
    current_query = human_messages[-1].content if human_messages else ""
    
    writer({"type": "agent_start", "agent": "intent_detection", "query": current_query})
    
    # Initialize Intent Detection Agent
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
    intent_agent = IntentDetectionAgent(llm)
    
    # Detect intent using minimal context
    intent_result = intent_agent.detect_intent(
        current_query=current_query,
        turn_history=turn_history,
        messages=messages  # Already from minimal context
    )
    
    # Create conversation turn
    turn = create_conversation_turn(
        query=current_query,
        intent_type=intent_result["intent_type"],
        parent_turn_id=intent_result.get("parent_turn_id"),
        context_summary=intent_result.get("context_summary"),
        triggered_clarification=False,  # Will be updated by clarification node
        metadata=intent_result.get("metadata", {})
    )
    
    # Create intent metadata
    intent_metadata = create_intent_metadata_from_result(intent_result)
    
    # Emit intent detection event
    writer({
        "type": "intent_detected",
        "intent_type": intent_result["intent_type"],
        "confidence": intent_result["confidence"],
        "reasoning": intent_result["reasoning"],
        "topic_change_score": intent_result["topic_change_score"],
        "complexity": intent_metadata["complexity"]
    })
    
    print(f"✓ Intent detected: {intent_result['intent_type']}")
    print(f"  Confidence: {intent_result['confidence']:.2f}")
    print(f"  Topic Change: {intent_result['topic_change_score']:.2f}")
    print(f"  Complexity: {intent_metadata['complexity']}")
    print(f"  Context Summary: {intent_result.get('context_summary', 'N/A')[:100]}...")
    
    # Return state updates
    # NOTE: next_agent is not needed here - workflow edges define routing
    return {
        "current_turn": turn,
        "turn_history": [turn],  # Reducer will append
        "intent_metadata": intent_metadata,
        "messages": [
            SystemMessage(content=f"Intent detected: {intent_result['intent_type']} (confidence: {intent_result['confidence']:.2f})")
        ]
    }

print("✓ Intent detection node defined")

# ==============================================================================
# Adaptive Clarification Strategy (NEW)
# ==============================================================================

def adaptive_clarification_strategy(
    clarity_result: Dict[str, Any],
    intent_metadata: IntentMetadata,
    turn_history: List[ConversationTurn]
) -> bool:
    """
    Decide whether to ask for clarification based on multiple factors.
    
    No hard count limits - adaptive based on context, query complexity,
    recent clarification frequency, and business rules.
    
    IMPORTANT: This function should NEVER be called for clarification_response intents
    because they should be caught by early-exit checks in clarification_node.
    
    Args:
        clarity_result: Result from ClarificationAgent.check_clarity()
        intent_metadata: Metadata from intent detection
        turn_history: List of recent conversation turns
    
    Returns:
        True if clarification should be requested, False otherwise
    """
    print("\n🤔 Evaluating adaptive clarification strategy...")
    
    # DEFENSIVE ASSERTION: This should never be reached for clarification_response
    intent_type = intent_metadata.get("intent_type", "")
    if intent_type.lower() == "clarification_response":
        print("🚨 CRITICAL WARNING: adaptive_clarification_strategy called with clarification_response!")
        print("   This should NEVER happen - clarification_node should have exited early!")
        print("   Forcing return False to prevent clarifying a clarification.")
        print("   Please investigate why the early-exit check failed.")
        return False
    
    # Factor 1: Ambiguity severity (from clarity check)
    ambiguity_score = clarity_result.get("ambiguity_score", 0.5)
    if ambiguity_score < 0.3:
        print(f"  ✓ Factor 1: Low ambiguity ({ambiguity_score:.2f}) - skip clarification")
        return False
    print(f"  • Factor 1: Ambiguity score = {ambiguity_score:.2f} (threshold: 0.3)")
    
    # Factor 2: Recent clarification frequency (don't annoy users)
    recent_turns = turn_history[-5:] if len(turn_history) >= 5 else turn_history
    recent_clarifications = sum(1 for t in recent_turns if t.get("triggered_clarification"))
    if recent_clarifications >= 3:
        print(f"  ✓ Factor 2: Too many recent clarifications ({recent_clarifications}/5) - skip")
        return False
    print(f"  • Factor 2: Recent clarifications = {recent_clarifications}/5 (max: 3)")
    
    # Factor 3: Query complexity (simple queries don't need clarification)
    complexity = intent_metadata.get("complexity", "moderate")
    if complexity == "simple":
        print(f"  ✓ Factor 3: Simple query - skip clarification")
        return False
    print(f"  • Factor 3: Complexity = {complexity}")
    
    # Factor 4: Confidence in best guess
    best_guess_confidence = clarity_result.get("best_guess_confidence", 0.0)
    if best_guess_confidence > 0.7:
        print(f"  ✓ Factor 4: High confidence in best guess ({best_guess_confidence:.2f}) - skip")
        return False
    print(f"  • Factor 4: Best guess confidence = {best_guess_confidence:.2f} (threshold: 0.7)")
    
    # Factor 5: Business rules (extensible for domain-specific needs)
    domain = intent_metadata.get("domain", "")
    urgent_domains = ["urgent_alerts", "simple_lookups", "operational_queries"]
    if domain in urgent_domains:
        print(f"  ✓ Factor 5: Urgent domain ({domain}) - skip clarification")
        return False
    print(f"  • Factor 5: Domain = {domain} (not urgent)")
    
    # Factor 6: Intent type (clarification responses should never trigger clarification)
    intent_type = intent_metadata.get("intent_type", "")
    if should_skip_clarification_for_intent(intent_type):
        print(f"  ✓ Factor 6: Intent type ({intent_type}) should skip clarification")
        return False
    
    print(f"  ✅ All factors passed - request clarification")
    return True

print("✓ Adaptive clarification strategy defined")

# ==============================================================================
# Refactored Clarification Node (Turn-Based Context)
# ==============================================================================

def clarification_node(state: AgentState) -> dict:
    """
    Simplified clarification node using turn-based context.
    
    IMPROVEMENTS:
    - Uses current_turn instead of parsing messages
    - No clarification_count tracking (uses adaptive strategy)
    - Intent-aware (skips clarification for clarification_response)
    - Unified ClarificationRequest object (no 7+ separate fields)
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    from langgraph.config import get_stream_writer
    
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("🔍 CLARIFICATION AGENT (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for clarification
    context = extract_clarification_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    # OPTIMIZATION: Log message and turn history truncation
    original_msg_count = context.get("_original_message_count", 0)
    original_turn_count = context.get("_original_turn_count", 0)
    messages = context.get("messages", [])
    turn_history = context.get("turn_history", [])
    
    if original_msg_count > len(messages):
        saved_pct = ((original_msg_count - len(messages)) / original_msg_count * 100)
        print(f"✂️ Message truncation: {original_msg_count} → {len(messages)} messages ({saved_pct:.0f}% reduction)")
    if original_turn_count > len(turn_history):
        saved_pct = ((original_turn_count - len(turn_history)) / original_turn_count * 100)
        print(f"✂️ Turn history truncation: {original_turn_count} → {len(turn_history)} turns ({saved_pct:.0f}% reduction)")
    
    # Get current turn and intent from state (set by intent_detection_node)
    current_turn = context.get("current_turn")
    if not current_turn:
        # Fallback for backward compatibility - create a proper ConversationTurn
        print("⚠ No current_turn found, falling back to legacy behavior")
        # IMPORTANT: Extract only from HumanMessage to avoid capturing SystemMessage or AIMessage
        # messages already extracted above for truncation logging
        human_messages = [m for m in messages if isinstance(m, HumanMessage)]
        query = human_messages[-1].content if human_messages else state.get("original_query", "")
        current_turn = create_conversation_turn(
            query=query,
            intent_type="new_question",
            parent_turn_id=None,
            context_summary=None,
            triggered_clarification=False,
            metadata={}
        )
    
    query = current_turn["query"]
    intent_type = current_turn.get("intent_type", "new_question")
    context_summary = current_turn.get("context_summary")
    
    writer({"type": "agent_start", "agent": "clarification", "query": query})
    
    print(f"Query: {query}")
    print(f"Intent: {intent_type}")
    
    # DEFENSE-IN-DEPTH: Multiple layers of protection against clarifying clarification responses
    # Layer 1: Primary check using helper function (consistent with adaptive strategy)
    if should_skip_clarification_for_intent(intent_type):
        print(f"✓✓ CLARIFICATION SKIP TRIGGERED (Layer 1) ✓✓")
        print(f"   Intent type '{intent_type}' should never be clarified")
        print(f"   Reason: User is already responding to a clarification request")
        print(f"   Using context summary from intent detection (validated by 2-phase approach)")
        print(f"   Context: {context_summary[:200] if context_summary else 'N/A'}...")
        
        writer({
            "type": "clarification_skipped", 
            "reason": f"Intent type '{intent_type}' should skip clarification",
            "layer": "primary_intent_check",
            "validated_by": "two_phase_detection"
        })
        
        return {
            "question_clear": True,
            "next_agent": "planning",
            "pending_clarification": None,
            "messages": [
                SystemMessage(content=f"Clarification response processed (validated by 2-phase detection). Context: {context_summary[:200] if context_summary else 'N/A'}...")
            ]
        }
    
    # Layer 2: Explicit check for clarification_response (backward compatibility)
    if intent_type.lower() == "clarification_response":
        # This should never be reached due to Layer 1, but kept as defensive programming
        print("⚠ WARNING: Layer 2 clarification skip triggered (should not happen!)")
        print("  This indicates Layer 1 check may have failed - investigating...")
        print(f"  Intent: {intent_type}")
        writer({"type": "clarification_skipped", "reason": "Intent is clarification_response", "layer": "fallback_explicit_check"})
        
        return {
            "question_clear": True,
            "next_agent": "planning",
            "pending_clarification": None,
            "messages": [
                SystemMessage(content=f"Clarification response processed (fallback). Context: {context_summary[:200] if context_summary else 'N/A'}...")
            ]
        }
    
    # Check if query needs clarification
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
    
    writer({"type": "agent_thinking", "agent": "clarification", "content": "Analyzing query clarity..."})
    clarification_agent = ClarificationAgent.from_table(llm, TABLE_NAME)
    
    # Call clarity check with context_summary (includes conversation history)
    # This provides full context to help evaluate clarity
    if context_summary:
        print(f"  Using context_summary for clarity analysis")
    clarity_result = clarification_agent.check_clarity(query, context_summary)
    
    # Prepare state updates (don't modify state in-place)
    question_clear = clarity_result.get("question_clear", True)
    clarification_needed = clarity_result.get("clarification_needed")
    clarification_options = clarity_result.get("clarification_options", [])
    
    # Emit clarity analysis result
    writer({"type": "clarity_analysis", "clear": question_clear, "reasoning": clarification_needed or "Query is clear and answerable"})
    
    if question_clear:
        print("✓ Query is clear - proceeding to planning")
        
        return {
            "question_clear": True,
            "next_agent": "planning",
            "pending_clarification": None,
            "messages": [
                SystemMessage(content=f"Query is clear. Proceeding to planning.")
            ]
        }
    
    # Query is unclear - decide if we should ask for clarification using adaptive strategy
    print("⚠ Query appears unclear - evaluating adaptive strategy...")
    
    # Use minimal context (already extracted)
    intent_metadata = context.get("intent_metadata", {})
    turn_history = context.get("turn_history", [])
    
    # Add backward compatibility defaults if ClarificationAgent didn't return these fields
    # (older versions or LLM failures may not include them)
    clarity_result_safe = {
        **clarity_result,
        "ambiguity_score": clarity_result.get("ambiguity_score", 0.8 if not question_clear else 0.2),
        "best_guess": clarity_result.get("best_guess", f"Interpreting as: {query}"),
        "best_guess_confidence": clarity_result.get("best_guess_confidence", 0.5)
    }
    
    should_clarify = adaptive_clarification_strategy(
        clarity_result=clarity_result_safe,
        intent_metadata=intent_metadata,
        turn_history=turn_history
    )
    
    if not should_clarify:
        # Proceed with best-effort interpretation (don't ask for clarification)
        print("✓ Adaptive strategy: proceeding with best-effort interpretation")
        writer({"type": "clarification_skipped", "reason": "Adaptive strategy decided to proceed"})
        
        return {
            "question_clear": True,  # Proceed despite ambiguity
            "next_agent": "planning",
            "pending_clarification": None,
            "messages": [
                SystemMessage(content=f"Query has some ambiguity but proceeding with best-effort interpretation: {clarity_result_safe['best_guess']}")
            ]
        }
    
    # Request clarification
    print("✅ Requesting clarification from user")
    print(f"   Reason: {clarification_needed}")
    if clarification_options:
        print("   Options:")
        for i, opt in enumerate(clarification_options, 1):
            print(f"     {i}. {opt}")
    
    # Create unified ClarificationRequest object
    clarification_request = create_clarification_request(
        reason=clarification_needed or "Query needs more specificity",
        options=clarification_options,
        turn_id=current_turn["turn_id"],
        best_guess=clarity_result_safe.get("best_guess"),
        best_guess_confidence=clarity_result_safe.get("best_guess_confidence")
    )
    
    # Format clarification message for user
    clarification_message = format_clarification_message(clarification_request)
    
    # Update turn to mark that it triggered clarification
    current_turn["triggered_clarification"] = True
    
    writer({"type": "clarification_requested", "reason": clarification_needed})
    
    return {
        "question_clear": False,
        "pending_clarification": clarification_request,
        "current_turn": current_turn,  # Update with triggered_clarification flag
        "messages": [
            AIMessage(content=clarification_message),
            SystemMessage(content=f"Clarification requested for turn {current_turn['turn_id']}")
        ]
    }


def planning_node(state: AgentState) -> dict:
    """
    Planning node wrapping PlanningAgent class using turn-based context.
    
    IMPROVEMENTS:
    - Uses current_turn.context_summary (LLM-generated) instead of manual combined_query_context
    - Intent-aware planning (different strategies for refinements vs new questions)
    - Clean separation from clarification logic
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    from langgraph.config import get_stream_writer
    
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("📋 PLANNING AGENT (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for planning
    context = extract_planning_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    # Get current turn and intent from state
    current_turn = context.get("current_turn")
    if not current_turn:
        # Fallback for backward compatibility
        print("⚠ No current_turn found, falling back to legacy behavior")
        query = context.get("original_query", "")
        intent_type = "new_question"
        context_summary = None
    else:
        query = current_turn["query"]
        intent_type = current_turn.get("intent_type", "new_question")
        context_summary = current_turn.get("context_summary")
    
    # Use context_summary if available (LLM-generated from intent detection)
    # This replaces the manual combined_query_context template
    planning_query = context_summary or query
    
    # Emit agent start event
    writer({"type": "agent_start", "agent": "planning", "query": planning_query[:100]})
    
    print(f"Query: {query}")
    print(f"Intent: {intent_type}")
    if context_summary:
        print(f"✓ Using context summary from intent detection")
        print(f"  Summary: {context_summary[:200]}...")
    else:
        print(f"✓ Using query directly (no context needed)")
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING)
    
    # Use OOP agent
    planning_agent = PlanningAgent(llm, VECTOR_SEARCH_INDEX)
    
    # Emit vector search start event
    writer({"type": "vector_search_start", "index": VECTOR_SEARCH_INDEX})
    
    # Get relevant spaces with full metadata (for Genie agents)
    # Use planning_query which includes context_summary if available
    relevant_spaces_full = planning_agent.search_relevant_spaces(planning_query)
    
    # Emit vector search results
    writer({"type": "vector_search_results", "spaces": relevant_spaces_full, "count": len(relevant_spaces_full)})
    
    # Emit plan formulation start
    writer({"type": "agent_thinking", "agent": "planning", "content": "Creating execution plan..."})
    
    # Create execution plan
    # IMPORTANT: Use planning_query (with context_summary) not just query
    plan = planning_agent.create_execution_plan(planning_query, relevant_spaces_full)
    
    # Extract plan components
    join_strategy = plan.get("join_strategy")
    
    # Emit plan formulation result
    writer({"type": "plan_formulation", "strategy": join_strategy, "requires_join": plan.get("requires_join", False)})
    
    # Determine next agent
    if join_strategy == "genie_route":
        print("✓ Plan complete - using GENIE ROUTE (Genie agents)")
        next_agent = "sql_synthesis_genie"
    else:
        print("✓ Plan complete - using TABLE ROUTE (direct SQL synthesis)")
        next_agent = "sql_synthesis_table"
    
    # Return only updates (no in-place modifications)
    return {
        "plan": plan,
        "sub_questions": plan.get("sub_questions", []),
        "requires_multiple_spaces": plan.get("requires_multiple_spaces", False),
        "relevant_space_ids": plan.get("relevant_space_ids", []),
        "requires_join": plan.get("requires_join", False),
        "join_strategy": join_strategy,
        "execution_plan": plan.get("execution_plan", ""),
        "genie_route_plan": plan.get("genie_route_plan"),
        "vector_search_relevant_spaces_info": plan.get("vector_search_relevant_spaces_info", []),
        "relevant_spaces": relevant_spaces_full,
        "next_agent": next_agent,
        "messages": [
            SystemMessage(content=f"Execution plan: {json.dumps(plan, indent=2)}")
        ]
    }


def sql_synthesis_table_node(state: AgentState) -> dict:
    """
    Fast SQL synthesis node wrapping SQLSynthesisTableAgent class.
    Combines OOP modularity with explicit state management.
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    from langgraph.config import get_stream_writer
    
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("⚡ SQL SYNTHESIS AGENT - TABLE ROUTE (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for table-based synthesis
    context = extract_synthesis_table_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    plan = context.get("plan", {})
    relevant_space_ids = context.get("relevant_space_ids", [])
    
    # Emit synthesis start event
    writer({"type": "sql_synthesis_start", "route": "table", "spaces": relevant_space_ids})
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    
    # Use OOP agent
    sql_agent = SQLSynthesisTableAgent(llm, CATALOG, SCHEMA)
    
    print("plan loaded from state is:", plan)
    print(json.dumps(plan, indent=2))
    
    try:
        print("🤖 Invoking SQL synthesis agent...")
        writer({"type": "agent_thinking", "agent": "sql_synthesis_table", "content": "Generating SQL query using table schemas..."})
        writer({"type": "uc_function_call", "function": "get_table_schemas", "params": {"space_ids": relevant_space_ids}})
        result = sql_agent(plan)
        
        # Extract SQL and explanation
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        if has_sql and sql_query and explanation:
            print("✓ SQL query synthesized successfully")
            print(f"SQL Preview: {sql_query[:200]}...")
            if explanation:
                print(f"Agent Explanation: {explanation[:200]}...")
            
            # Emit SQL generated event
            writer({"type": "sql_generated", "query_preview": sql_query[:200]})
            
            # Return updates for successful synthesis
            return {
                "sql_query": sql_query,
                "has_sql": has_sql,
                "sql_synthesis_explanation": explanation,
                "next_agent": "sql_execution",
                "messages": [
                    AIMessage(content=f"SQL Synthesis (Table Route):\n{explanation}")
                ]
            }
        else:
            print("⚠ No SQL generated - agent explanation:")
            print(f"  {explanation}")
            
            # Return updates for failed synthesis
            return {
                "synthesis_error": "Cannot generate SQL query",
                "sql_synthesis_explanation": explanation,
                "next_agent": "summarize",
                "messages": [
                    AIMessage(content=f"SQL Synthesis Failed (Table Route):\n{explanation}")
                ]
            }
        
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        error_msg = str(e)
        # Return updates for exception
        return {
            "synthesis_error": error_msg,
            "sql_synthesis_explanation": error_msg,
            "messages": [
                AIMessage(content=f"SQL Synthesis Failed (Table Route):\n{error_msg}")
            ]
        }


def sql_synthesis_genie_node(state: AgentState) -> dict:
    """
    Slow SQL synthesis node wrapping SQLSynthesisGenieAgent class.
    Combines OOP modularity with explicit state management.
    
    Uses relevant_spaces from PlanningAgent (no need to re-query all spaces).
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    from langgraph.config import get_stream_writer
    
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("🐢 SQL SYNTHESIS AGENT - GENIE ROUTE (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for genie-based synthesis
    context = extract_synthesis_genie_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    # Get relevant spaces from state (already discovered by PlanningAgent)
    relevant_spaces = context.get("relevant_spaces", [])
    relevant_space_ids = [s.get("space_id") for s in relevant_spaces if s.get("space_id")]
    
    # Emit synthesis start event
    writer({"type": "sql_synthesis_start", "route": "genie", "spaces": relevant_space_ids})
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    
    if not relevant_spaces:
        print("❌ No relevant_spaces found in state")
        # Return error update
        return {
            "synthesis_error": "No relevant spaces available for genie route"
        }
    
    # Use OOP agent - only creates Genie agents for relevant spaces
    sql_agent = SQLSynthesisGenieAgent(llm, relevant_spaces)
    
    # Use minimal context (already extracted)
    plan = context.get("plan", {})
    genie_route_plan = context.get("genie_route_plan") or plan.get("genie_route_plan", {})
    
    if not genie_route_plan:
        print("❌ No genie_route_plan found in plan")
        # Return error update
        return {
            "synthesis_error": "No routing plan available for genie route"
        }
    
    try:
        print(f"🤖 Querying {len(genie_route_plan)} Genie agents...")
        writer({"type": "agent_thinking", "agent": "sql_synthesis_genie", "content": f"Calling {len(genie_route_plan)} Genie agents for SQL generation..."})
        
        # Emit events for each Genie agent call
        for space_id in genie_route_plan.keys():
            writer({"type": "genie_agent_call", "space_id": space_id, "query": genie_route_plan[space_id][:100]})
        
        result = sql_agent(plan)
        
        # Extract SQL and explanation
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        # Update explicit state
        if has_sql and sql_query and explanation:
            print("✓ SQL fragments combined successfully")
            print(f"SQL Preview: {sql_query[:200]}...")
            if explanation:
                print(f"Agent Explanation: {explanation[:200]}...")
            
            # Emit SQL generated event
            writer({"type": "sql_generated", "query_preview": sql_query[:200]})
            
            # Return updates for successful synthesis
            return {
                "sql_query": sql_query,
                "has_sql": has_sql,
                "sql_synthesis_explanation": explanation,
                "next_agent": "sql_execution",
                "messages": [
                    AIMessage(content=f"SQL Synthesis (Genie Route):\n{explanation}")
                ]
            }
        else:
            print("⚠ No SQL generated - agent explanation:")
            print(f"  {explanation}")
            
            # Return updates for failed synthesis
            return {
                "synthesis_error": "Cannot generate SQL query from Genie agent fragments",
                "sql_synthesis_explanation": explanation,
                "next_agent": "summarize",
                "messages": [
                    AIMessage(content=f"SQL Synthesis Failed (Genie Route):\n{explanation}")
                ]
            }
        
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        error_msg = str(e)
        # Return updates for exception
        return {
            "synthesis_error": error_msg,
            "sql_synthesis_explanation": error_msg,
            "messages": [
                AIMessage(content=f"SQL Synthesis Failed (Genie Route):\n{error_msg}")
            ]
        }


def sql_execution_node(state: AgentState) -> dict:
    """
    SQL execution node wrapping SQLExecutionAgent class.
    Combines OOP modularity with explicit state management.
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    from langgraph.config import get_stream_writer
    
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("🚀 SQL EXECUTION AGENT (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for execution
    context = extract_execution_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    sql_query = context.get("sql_query")
    
    if not sql_query:
        print("❌ No SQL query to execute")
        # Return error update
        return {
            "execution_error": "No SQL query provided"
        }
    
    # Emit validation start event
    writer({"type": "sql_validation_start", "query": sql_query[:200]})
    
    # Emit execution start event
    writer({"type": "sql_execution_start", "estimated_complexity": "standard"})
    
    # Use OOP agent
    execution_agent = SQLExecutionAgent()
    result = execution_agent(sql_query)
    
    # Prepare updates based on result
    updates = {
        "execution_result": result,
        "next_agent": "summarize",
        "messages": []
    }
    
    if result["success"]:
        print(f"✓ Query executed successfully!")
        print(f"📊 Rows returned: {result['row_count']}")
        print(f"📋 Columns: {', '.join(result['columns'])}")
        
        # Emit execution complete event
        writer({"type": "sql_execution_complete", "rows": result['row_count'], "columns": result['columns']})
        
        updates["messages"].append(
            SystemMessage(content=f"Execution successful: {result['row_count']} rows returned")
        )
    else:
        print(f"❌ SQL execution failed: {result.get('error', 'Unknown error')}")
        updates["execution_error"] = result.get("error")
        
        updates["messages"].append(
            SystemMessage(content=f"Execution failed: {result.get('error')}")
        )
    
    return updates


def summarize_node(state: AgentState) -> dict:
    """
    Result summarize node wrapping ResultSummarizeAgent class.
    
    This is the final node that all workflow paths go through.
    Generates a natural language summary AND preserves all workflow data.
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    from langgraph.config import get_stream_writer
    
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("📝 RESULT SUMMARIZE AGENT (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for summarization
    context = extract_summarize_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    # Emit summary start event
    writer({"type": "summary_start", "content": "Generating comprehensive summary..."})
    
    # Create LLM for summarization (no max_tokens limit for comprehensive output)
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SUMMARIZE, temperature=0.1, max_tokens=2000)
    
    # Use OOP agent to generate summary (pass minimal context instead of full state)
    summarize_agent = ResultSummarizeAgent(llm)
    summary = summarize_agent(context)
    
    print(f"\n✅ Summary Generated:")
    print(f"{summary}")
    
    # Display what's being returned
    print(f"\n📦 State Fields Being Returned:")
    print(f"  ✓ final_summary: {len(summary)} chars")
    if context.get("sql_query"):
        print(f"  ✓ sql_query: {len(context['sql_query'])} chars")
    if context.get("execution_result"):
        exec_result = context["execution_result"]
        if exec_result.get("success"):
            print(f"  ✓ execution_result: {exec_result.get('row_count', 0)} rows")
        else:
            print(f"  ✓ execution_result: Failed - {exec_result.get('error', 'Unknown')[:50]}...")
    if context.get("sql_synthesis_explanation"):
        print(f"  ✓ sql_synthesis_explanation: {len(context['sql_synthesis_explanation'])} chars")
    if state.get("execution_plan"):
        print(f"  ✓ execution_plan: {state['execution_plan'][:80]}...")
    if state.get("synthesis_error"):
        print(f"  ⚠ synthesis_error: {state['synthesis_error'][:50]}...")
    if state.get("execution_error"):
        print(f"  ⚠ execution_error: {state['execution_error'][:50]}...")
    
    print("="*80)
    
    # Build comprehensive final message with ALL workflow information
    final_message_parts = []
    
    # 1. Summary
    final_message_parts.append(f"📝 **Summary:**\n{summary}\n")
    
    # 2. Original Query
    if state.get("original_query"):
        final_message_parts.append(f"🔍 **Original Query:**\n{state['original_query']}\n")
    
    # 3. Execution Plan
    if state.get("execution_plan"):
        final_message_parts.append(f"📋 **Execution Plan:**\n{state['execution_plan']}")
        if state.get("join_strategy"):
            final_message_parts.append(f"Strategy: {state['join_strategy']}\n")
    
    # 4. SQL Synthesis Explanation
    if state.get("sql_synthesis_explanation"):
        final_message_parts.append(f"💭 **SQL Synthesis Explanation:**\n{state['sql_synthesis_explanation']}\n")
    
    # 5. Generated SQL
    if state.get("sql_query"):
        final_message_parts.append(f"💻 **Generated SQL:**\n```sql\n{state['sql_query']}\n```\n")
    
    # 6. Execution Results
    exec_result = state.get("execution_result")
    if exec_result:
        if exec_result.get("success"):
            final_message_parts.append(f"✅ **Execution Successful:**\n")
            final_message_parts.append(f"- Rows: {exec_result.get('row_count', 0)}\n")
            final_message_parts.append(f"- Columns: {', '.join(exec_result.get('columns', []))}\n")
            
            # Convert results to pandas DataFrame and display
            results = exec_result.get("result", [])
            if results:
                try:
                    import pandas as pd
                    df = pd.DataFrame(results)
                    
                    final_message_parts.append(f"\n📊 **Query Results:**\n")
                    
                    # Display DataFrame
                    print("\n" + "="*80)
                    print("📊 QUERY RESULTS (Pandas DataFrame)")
                    print("="*80)
                    try:
                        display(df)  # Use Databricks display() for interactive view
                    except:
                        print(df.to_string())  # Fallback to string representation
                    print("="*80 + "\n")
                    
                    # Add DataFrame info to message
                    final_message_parts.append(f"DataFrame shape: {df.shape}\n")
                    final_message_parts.append(f"Preview (first 5 rows):\n```\n{df.head().to_string()}\n```\n")
                    
                    # Note: DataFrame not stored in state (not msgpack serializable)
                    # Users can recreate it from state['execution_result']['result']
                    
                except Exception as e:
                    final_message_parts.append(f"⚠️ Could not convert to DataFrame: {e}\n")
                    final_message_parts.append(f"Raw results (first 3): {results[:3]}\n")
        else:
            final_message_parts.append(f"❌ **Execution Failed:**\n")
            final_message_parts.append(f"Error: {exec_result.get('error', 'Unknown error')}\n")
    
    # 7. Errors (if any)
    if state.get("synthesis_error"):
        final_message_parts.append(f"❌ **Synthesis Error:**\n{state['synthesis_error']}\n")
    if state.get("execution_error"):
        final_message_parts.append(f"❌ **Execution Error:**\n{state['execution_error']}\n")
    
    # 8. Relevant Spaces (if any)
    if state.get("relevant_space_ids"):
        final_message_parts.append(f"\n🎯 **Relevant Genie Spaces:** {len(state['relevant_space_ids'])} spaces analyzed\n")
    
    # Combine all parts into final comprehensive message
    comprehensive_message = "\n".join(final_message_parts)
    
    print(f"\n✅ Comprehensive final message created ({len(comprehensive_message)} chars)")
    
    # Route to END via fixed edge (summarize → END)
    # Return only updates (final_summary and the comprehensive message)
    return {
        "final_summary": summary,
        "messages": [
            AIMessage(content=comprehensive_message)
        ]
    }

print("✓ All node wrappers defined (including summarize)")

# ==============================================================================
# Business Logic Integration Hooks (NEW)
# ==============================================================================

class BusinessLogicIntegration:
    """
    Example integration points for business logic using intent metadata.
    
    This demonstrates how to use the intent_metadata from intent detection
    for billing, analytics, routing, and personalization.
    """
    
    @staticmethod
    def calculate_usage_cost(state: AgentState) -> Dict[str, Any]:
        """Calculate usage cost based on intent type and complexity."""
        intent_metadata = state.get("intent_metadata", {})
        intent_type = intent_metadata.get("intent_type", "new_question")
        complexity = intent_metadata.get("complexity", "moderate")
        
        base_rates = {
            "new_question": 0.10,
            "refinement": 0.05,
            "continuation": 0.07,
            "clarification_response": 0.00
        }
        complexity_multipliers = {"simple": 1.0, "moderate": 1.5, "complex": 2.0}
        
        base_cost = base_rates.get(intent_type, 0.10)
        multiplier = complexity_multipliers.get(complexity, 1.5)
        
        return {
            "total_cost": base_cost * multiplier,
            "intent_type": intent_type,
            "complexity": complexity
        }
    
    @staticmethod
    def log_analytics_event(state: AgentState) -> Dict[str, Any]:
        """Log analytics event for conversation analysis."""
        intent_metadata = state.get("intent_metadata", {})
        current_turn = state.get("current_turn", {})
        turn_history = state.get("turn_history", [])
        
        event = {
            "event_type": "query_processed",
            "intent_type": intent_metadata.get("intent_type"),
            "complexity": intent_metadata.get("complexity"),
            "turn_count": len(turn_history),
            "clarification_requested": state.get("pending_clarification") is not None
        }
        print(f"📊 Analytics: {event['intent_type']} | {event['complexity']}")
        return event

print("✓ Business logic integration hooks defined")

def create_super_agent_hybrid():
    """
    Create the Hybrid Super Agent LangGraph workflow.
    
    Combines:
    - OOP agent classes for modularity
    - Explicit state management for observability
    """
    print("\n" + "="*80)
    print("🏗️ BUILDING HYBRID SUPER AGENT WORKFLOW")
    print("="*80)
    
    # Create the graph with explicit state
    workflow = StateGraph(AgentState)
    
    # Add nodes - SIMPLIFIED with unified node
    workflow.add_node("unified_intent_context_clarification", unified_intent_context_clarification_node)  # NEW: Unified node
    workflow.add_node("planning", planning_node)
    workflow.add_node("sql_synthesis_table", sql_synthesis_table_node)
    workflow.add_node("sql_synthesis_genie", sql_synthesis_genie_node)
    workflow.add_node("sql_execution", sql_execution_node)
    workflow.add_node("summarize", summarize_node)  # Final summarization node
    
    # Define routing logic based on explicit state
    def route_after_unified(state: AgentState) -> str:
        """Route after unified node: planning or END (clarification)"""
        if state.get("question_clear", False):
            return "planning"
        return END  # End if clarification needed
    
    def route_after_planning(state: AgentState) -> str:
        next_agent = state.get("next_agent", "summarize")
        if next_agent == "sql_synthesis_table":
            return "sql_synthesis_table"
        elif next_agent == "sql_synthesis_genie":
            return "sql_synthesis_genie"
        return "summarize"
    
    def route_after_synthesis(state: AgentState) -> str:
        next_agent = state.get("next_agent", "summarize")
        if next_agent == "sql_execution":
            return "sql_execution"
        return "summarize"  # Summarize if synthesis error
    
    # Add edges with conditional routing
    # NEW: Entry point is now unified node
    workflow.set_entry_point("unified_intent_context_clarification")
    
    # Route from unified node to planning or END (clarification)
    workflow.add_conditional_edges(
        "unified_intent_context_clarification",
        route_after_unified,
        {
            "planning": "planning",
            END: END
        }
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
        {
            "sql_execution": "sql_execution",
            "summarize": "summarize"
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_genie",
        route_after_synthesis,
        {
            "sql_execution": "sql_execution",
            "summarize": "summarize"
        }
    )
    
    # SQL execution always goes to summarize
    workflow.add_edge("sql_execution", "summarize")
    
    # Summarize is the final node before END
    workflow.add_edge("summarize", END)
    
    # NOTE: Workflow compiled WITHOUT checkpointer here
    # Checkpointer will be added at runtime in SuperAgentHybridResponsesAgent
    # This allows distributed Model Serving with CheckpointSaver
    app_graph = workflow
    
    print("✓ Workflow nodes added:")
    print("  1. Unified Intent+Context+Clarification Node (SIMPLIFIED - no kumc_poc imports)")
    print("  2. Planning Agent (OOP)")
    print("  3. SQL Synthesis Agent - Table Route (OOP)")
    print("  4. SQL Synthesis Agent - Genie Route (OOP)")
    print("  5. SQL Execution Agent (OOP)")
    print("  6. Result Summarize Agent (OOP) - FINAL NODE")
    print("\n✓ Single LLM call for intent + context + clarity (faster, cheaper)")
    print("✓ Smart clarification rate limiting (1 per 5 turns, sliding window)")
    print("✓ Self-contained implementation (no external imports)")
    print("✓ Conditional routing configured")
    print("✓ All paths route to summarize node before END")
    print("✓ Checkpointer will be added at runtime (distributed serving)")
    print("\n✅ Simplified Hybrid Super Agent workflow created successfully!")
    print("="*80)
    
    return app_graph

# Create the Hybrid Super Agent
super_agent_hybrid = create_super_agent_hybrid()
class SuperAgentHybridResponsesAgent(ResponsesAgent):
    """
    Enhanced ResponsesAgent with both short-term and long-term memory for distributed Model Serving.
    
    Features:
    - Short-term memory (CheckpointSaver): Multi-turn conversations within a session
    - Long-term memory (DatabricksStore): User preferences across sessions with semantic search
    - Connection pooling and automatic credential rotation
    - Works seamlessly in distributed Model Serving (multiple instances)
    
    Memory Architecture:
    - Short-term: Stored per thread_id in Lakebase checkpoints table
    - Long-term: Stored per user_id in Lakebase store table with vector embeddings
    """
    
    def __init__(self, workflow: StateGraph):
        """
        Initialize the ResponsesAgent wrapper.
        
        Args:
            workflow: The uncompiled LangGraph StateGraph workflow
        """
        self.workflow = workflow
        self.lakebase_instance_name = LAKEBASE_INSTANCE_NAME
        self._store = None
        self._memory_tools = None
        print("✓ SuperAgentHybridResponsesAgent initialized with memory support")
    
    @property
    def store(self):
        """Lazy initialization of DatabricksStore for long-term memory."""
        if self._store is None:
            logger.info(f"Initializing DatabricksStore with instance: {self.lakebase_instance_name}")
            self._store = DatabricksStore(
                instance_name=self.lakebase_instance_name,
                embedding_endpoint=EMBEDDING_ENDPOINT,
                embedding_dims=EMBEDDING_DIMS,
            )
            self._store.setup()  # Creates store table if not exists
            logger.info("✓ DatabricksStore initialized")
        return self._store
    
    @property
    def memory_tools(self):
        """Create memory tools for long-term memory access."""
        if self._memory_tools is None:
            logger.info("Creating memory tools for long-term memory")
            
            @tool
            def get_user_memory(query: str, config: RunnableConfig) -> str:
                """Search for relevant user information using semantic search.
                
                Use this tool to retrieve previously saved information about the user,
                such as their preferences, facts they've shared, or other personal details.
                
                Args:
                    query: The search query to find relevant memories
                    config: Runtime configuration containing user_id
                """
                user_id = config.get("configurable", {}).get("user_id")
                if not user_id:
                    return "Memory not available - no user_id provided."
                
                namespace = ("user_memories", user_id.replace(".", "-"))
                results = self.store.search(namespace, query=query, limit=5)
                
                if not results:
                    return "No memories found for this user."
                
                memory_items = [f"- [{item.key}]: {json.dumps(item.value)}" for item in results]
                return f"Found {len(results)} relevant memories (ranked by similarity):\n" + "\n".join(memory_items)
            
            @tool
            def save_user_memory(memory_key: str, memory_data_json: str, config: RunnableConfig) -> str:
                """Save information about the user to long-term memory.
                
                Use this tool to remember important information the user shares,
                such as preferences, facts, or other personal details.
                
                Args:
                    memory_key: A descriptive key for this memory (e.g., "preferences", "favorite_visualization")
                    memory_data_json: JSON string with the information to remember. 
                        Example: '{"preferred_chart_type": "bar", "default_spaces": ["patient_data"]}'
                    config: Runtime configuration containing user_id
                """
                user_id = config.get("configurable", {}).get("user_id")
                if not user_id:
                    return "Cannot save memory - no user_id provided."
                
                namespace = ("user_memories", user_id.replace(".", "-"))
                
                try:
                    memory_data = json.loads(memory_data_json)
                    if not isinstance(memory_data, dict):
                        return f"Failed: memory_data must be a JSON object, not {type(memory_data).__name__}"
                    self.store.put(namespace, memory_key, memory_data)
                    return f"Successfully saved memory with key '{memory_key}' for user"
                except json.JSONDecodeError as e:
                    return f"Failed to save memory: Invalid JSON format - {str(e)}"
            
            @tool
            def delete_user_memory(memory_key: str, config: RunnableConfig) -> str:
                """Delete a specific memory from the user's long-term memory.
                
                Use this when the user asks you to forget something or remove
                a piece of information from their memory.
                
                Args:
                    memory_key: The key of the memory to delete
                    config: Runtime configuration containing user_id
                """
                user_id = config.get("configurable", {}).get("user_id")
                if not user_id:
                    return "Cannot delete memory - no user_id provided."
                
                namespace = ("user_memories", user_id.replace(".", "-"))
                self.store.delete(namespace, memory_key)
                return f"Successfully deleted memory with key '{memory_key}' for user"
            
            self._memory_tools = [get_user_memory, save_user_memory, delete_user_memory]
            logger.info(f"✓ Created {len(self._memory_tools)} memory tools")
        
        return self._memory_tools
    
    def _get_or_create_thread_id(self, request: ResponsesAgentRequest) -> str:
        """Get thread_id from request or create a new one.
        
        Priority:
        1. Use thread_id from custom_inputs if present
        2. Use conversation_id from chat context if available
        3. Generate a new UUID
        """
        ci = dict(request.custom_inputs or {})
        
        if "thread_id" in ci:
            return ci["thread_id"]
        
        # Use conversation_id from ChatContext as thread_id
        if request.context and getattr(request.context, "conversation_id", None):
            return request.context.conversation_id
        
        # Generate new thread_id
        return str(uuid4())
    
    def _get_user_id(self, request: ResponsesAgentRequest) -> Optional[str]:
        """Extract user_id from request context.
        
        Priority:
        1. Use user_id from chat context (preferred for Model Serving)
        2. Use user_id from custom_inputs
        """
        if request.context and getattr(request.context, "user_id", None):
            return request.context.user_id
        
        if request.custom_inputs and "user_id" in request.custom_inputs:
            return request.custom_inputs["user_id"]
        
        return None
    
    def make_json_serializable(self, obj):
        """
        Convert LangChain objects and other non-serializable objects to JSON-serializable format.
        
        Args:
            obj: Object to convert
            
        Returns:
            JSON-serializable version of the object
        """
        from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, ToolMessage, AIMessageChunk
        from uuid import UUID
        
        # Handle None
        if obj is None:
            return None
        
        # Handle UUID objects
        if isinstance(obj, UUID):
            return str(obj)
        
        # Handle bytes
        if isinstance(obj, bytes):
            try:
                return obj.decode('utf-8', errors='ignore')
            except:
                return f"<bytes:{len(obj)}>"
        
        # Handle set
        if isinstance(obj, set):
            return [self.make_json_serializable(item) for item in obj]
        
        # Handle LangChain message objects
        if isinstance(obj, BaseMessage):
            msg_dict = {
                "type": obj.__class__.__name__,
                "content": str(obj.content) if obj.content else ""
            }
            if hasattr(obj, 'id') and obj.id:
                msg_dict["id"] = str(obj.id)
            if hasattr(obj, 'name') and obj.name:
                msg_dict["name"] = obj.name
            if hasattr(obj, 'tool_calls') and obj.tool_calls:
                # Recursively serialize tool calls
                msg_dict["tool_calls"] = [
                    self.make_json_serializable(tc) for tc in obj.tool_calls[:2]
                ]  # Limit to 2 for brevity
            return msg_dict
        
        # Handle dictionaries recursively
        if isinstance(obj, dict):
            return {str(k): self.make_json_serializable(v) for k, v in obj.items()}
        
        # Handle lists and tuples recursively
        if isinstance(obj, (list, tuple)):
            return [self.make_json_serializable(item) for item in obj]
        
        # Handle primitives
        if isinstance(obj, (str, int, float, bool)):
            return obj
        
        # For anything else, convert to string representation
        try:
            return str(obj)
        except Exception:
            return f"<{type(obj).__name__}>"
    
    def format_custom_event(self, custom_data: dict) -> str:
        """
        Format custom streaming events for user-friendly display.
        
        Args:
            custom_data: Dictionary containing custom event data with 'type' key
            
        Returns:
            Formatted string with emoji and readable event description
        """
        event_type = custom_data.get("type", "unknown")
        
        formatters = {
            "agent_thinking": lambda d: f"💭 {d['agent'].upper()}: {d['content']}",
            "agent_start": lambda d: f"🚀 Starting {d['agent']} agent for: {d.get('query', '')[:50]}...",
            "intent_detection": lambda d: f"🎯 Intent: {d['result']} - {d.get('reasoning', '')}",
            "clarity_analysis": lambda d: f"✓ Query {'clear' if d['clear'] else 'unclear'}: {d.get('reasoning', '')}",
            "vector_search_start": lambda d: f"🔍 Searching vector index: {d['index']}",
            "vector_search_results": lambda d: f"📊 Found {d['count']} relevant spaces: {[s.get('space_id', 'unknown') for s in d.get('spaces', [])]}",
            "plan_formulation": lambda d: f"📋 Execution plan: {d.get('strategy', 'unknown')} strategy",
            "uc_function_call": lambda d: f"🔧 Calling UC function: {d['function']}",
            "sql_generated": lambda d: f"📝 SQL generated: {d.get('query_preview', '')}...",
            "sql_validation_start": lambda d: f"✅ Validating SQL query...",
            "sql_execution_start": lambda d: f"⚡ Executing SQL query...",
            "sql_execution_complete": lambda d: f"✓ Query complete: {d.get('rows', 0)} rows, {len(d.get('columns', []))} columns",
            "summary_start": lambda d: f"📄 Generating summary...",
            "genie_agent_call": lambda d: f"🤖 Calling Genie agent for space: {d.get('space_id', 'unknown')}",
        }
        
        # Bulletproof JSON fallback handler
        def json_fallback(obj):
            """Final fallback for json.dumps() - converts anything to string."""
            try:
                return str(obj)
            except:
                return f"<{type(obj).__name__}>"
        
        # Fallback formatter now uses make_json_serializable with json_fallback
        formatter = formatters.get(
            event_type,
            lambda d: f"ℹ️ {event_type}: {json.dumps(self.make_json_serializable(d), indent=2, default=json_fallback)}"
        )
        
        try:
            return formatter(custom_data)
        except Exception as e:
            logger.warning(f"Error formatting custom event {event_type}: {e}")
            # Enhanced error handling with serialization fallback
            try:
                serialized = self.make_json_serializable(custom_data)
                return f"ℹ️ {event_type}: {json.dumps(serialized, indent=2, default=json_fallback)}"
            except Exception as e2:
                logger.warning(f"Error serializing custom event {event_type}: {e2}")
                return f"ℹ️ {event_type}: {str(custom_data)}"
    
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
        Make a streaming prediction with both short-term and long-term memory.
        
        SIMPLIFIED API: All conversation turns use the same simple format.
        The agent auto-detects clarification responses and follow-ups from message history.
        
        Memory Systems:
        - Short-term (CheckpointSaver): Preserves conversation state across distributed instances
        - Long-term (DatabricksStore): User preferences accessible via memory tools
        
        Args:
            request: The request containing:
                - input: List of messages (user query is the last message)
                - context.conversation_id: Used as thread_id (preferred)
                - context.user_id: Used for long-term memory (preferred)
                - custom_inputs: Dict with optional keys:
                    - thread_id (str): Thread identifier override
                    - user_id (str): User identifier override
            
        Yields:
            ResponsesAgentStreamEvent for each step in the workflow
            
        Usage in Model Serving (ALL scenarios use same format):
            # First query in a conversation
            POST /invocations
            {
                "messages": [{"role": "user", "content": "Show me patient data"}],
                "context": {
                    "conversation_id": "session_001",
                    "user_id": "user@example.com"
                }
            }
            
            # Clarification response (SIMPLIFIED - auto-detected!)
            POST /invocations
            {
                "messages": [{"role": "user", "content": "Patient count by age group"}],
                "context": {
                    "conversation_id": "session_001",  # Same thread_id
                    "user_id": "user@example.com"
                }
            }
            
            # Follow-up query (agent remembers context automatically)
            POST /invocations
            {
                "messages": [{"role": "user", "content": "Now show by gender"}],
                "context": {
                    "conversation_id": "session_001",  # Same thread_id
                    "user_id": "user@example.com"
                }
            }
        """
        # Get identifiers
        thread_id = self._get_or_create_thread_id(request)
        user_id = self._get_user_id(request)
        
        # Update custom_inputs with resolved identifiers
        ci = dict(request.custom_inputs or {})
        ci["thread_id"] = thread_id
        if user_id:
            ci["user_id"] = user_id
        request.custom_inputs = ci
        
        logger.info(f"Processing request - thread_id: {thread_id}, user_id: {user_id}")
        
        # Convert request input to chat completions format
        cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
        
        # Get the latest user message
        latest_query = cc_msgs[-1]["content"] if cc_msgs else ""
        
        # Configure runtime with thread_id and user_id
        run_config = {"configurable": {"thread_id": thread_id}}
        if user_id:
            run_config["configurable"]["user_id"] = user_id
        
        # SIMPLIFIED: Unified state initialization for all scenarios
        # CheckpointSaver will restore previous conversation context automatically
        # The intent_detection_node runs first and creates current_turn
        initial_state = {
            **RESET_STATE_TEMPLATE,  # Reset all per-query execution fields
            "original_query": latest_query,
            "messages": [
                SystemMessage(content="""You are a multi-agent Q&A analysis system.
Your role is to help users query and analyze cross-domain data.

Guidelines:
- Always explain your reasoning and execution plan
- Validate SQL queries before execution
- Provide clear, comprehensive summaries
- If information is missing, ask for clarification (max once)
- Use UC functions and Genie agents to generate accurate SQL
- Return results with proper context and explanations"""),
                HumanMessage(content=latest_query)
            ]
            # NOTE: current_turn, intent_metadata, turn_history are NOT in RESET_STATE_TEMPLATE
            # They are managed by unified_intent_context_clarification_node and persist via CheckpointSaver
        }
        
        # Add user_id to state for long-term memory access
        if user_id:
            initial_state["user_id"] = user_id
            initial_state["thread_id"] = thread_id
        
        first_message = True
        seen_ids = set()
        
        # Execute workflow with CheckpointSaver for distributed serving
        # CRITICAL: CheckpointSaver as context manager ensures all instances share state
        with CheckpointSaver(instance_name=self.lakebase_instance_name) as checkpointer:
            # Compile graph with checkpointer at runtime
            # This allows distributed Model Serving to access shared state
            app = self.workflow.compile(checkpointer=checkpointer)
            
            logger.info(f"Executing workflow with checkpointer (thread: {thread_id})")
            
            # Stream the workflow execution with enhanced visibility modes
            # CheckpointSaver will:
            # 1. Restore previous state from thread_id (if exists) from Lakebase
            # 2. Merge with initial_state (initial_state takes precedence)
            # 3. Preserve conversation history across distributed instances
            # Stream modes:
            # - updates: State changes after each node
            # - messages: LLM token-by-token streaming
            # - custom: Agent-specific events (thinking, decisions, progress)
            # - debug: Maximum execution detail
            for event in app.stream(initial_state, run_config, stream_mode=["updates", "messages", "custom", "debug"]):
                event_type = event[0]
                event_data = event[1]
                
                # Handle streaming text deltas (messages mode)
                if event_type == "messages":
                    try:
                        # Extract the message chunk
                        chunk = event_data[0] if isinstance(event_data, (list, tuple)) else event_data
                        
                        # Stream text content as deltas for real-time visibility in Playground
                        if isinstance(chunk, AIMessageChunk) and (content := chunk.content):
                            yield ResponsesAgentStreamEvent(
                                **self.create_text_delta(delta=content, item_id=chunk.id),
                            )
                    except Exception as e:
                        logger.warning(f"Error processing message chunk: {e}")
                
                # Handle node updates (updates mode)
                elif event_type == "updates":
                    events = event_data
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
                        # Emit node name as a step indicator with enhanced details
                        if events:
                            node_name = tuple(events.keys())[0]
                            node_update = events[node_name]
                            updated_keys = [k for k in node_update.keys() if k != "messages"]
                            
                            # Enhanced step indicator with state keys
                            step_text = f"🔹 Step: {node_name}"
                            if updated_keys:
                                step_text += f" | Keys updated: {', '.join(updated_keys)}"
                            
                            yield ResponsesAgentStreamEvent(
                                type="response.output_item.done",
                                item=self.create_text_output_item(
                                    text=step_text, id=str(uuid4())
                                ),
                            )
                            
                            # Emit routing decision if next_agent changed
                            if "next_agent" in node_update:
                                next_agent = node_update["next_agent"]
                                yield ResponsesAgentStreamEvent(
                                    type="response.output_item.done",
                                    item=self.create_text_output_item(
                                        text=f"🔀 Routing decision: Next agent = {next_agent}",
                                        id=str(uuid4())
                                    ),
                                )
                    
                    # Process messages for tool calls, tool results, and final text
                    for msg in new_msgs:
                        # Check if message has tool calls
                        if hasattr(msg, 'tool_calls') and msg.tool_calls:
                            # Emit function call items for tool invocations
                            for tool_call in msg.tool_calls:
                                try:
                                    yield ResponsesAgentStreamEvent(
                                        type="response.output_item.done",
                                        item=self.create_function_call_item(
                                            id=str(uuid4()),
                                            call_id=tool_call.get("id", str(uuid4())),
                                            name=tool_call.get("name", "unknown"),
                                            arguments=json.dumps(tool_call.get("args", {})),
                                        ),
                                    )
                                except Exception as e:
                                    logger.warning(f"Error emitting tool call: {e}")
                        # Handle ToolMessage for tool results
                        elif hasattr(msg, '__class__') and msg.__class__.__name__ == 'ToolMessage':
                            try:
                                tool_name = getattr(msg, 'name', 'unknown')
                                tool_content = str(msg.content)[:200] if msg.content else "No content"
                                yield ResponsesAgentStreamEvent(
                                    type="response.output_item.done",
                                    item=self.create_text_output_item(
                                        text=f"🔨 Tool result ({tool_name}): {tool_content}...",
                                        id=str(uuid4())
                                    ),
                                )
                            except Exception as e:
                                logger.warning(f"Error emitting tool result: {e}")
                        else:
                            # Emit regular message content
                            yield from output_to_responses_items_stream([msg])
                
                # Handle custom mode (agent-specific events)
                elif event_type == "custom":
                    try:
                        custom_data = event_data
                        formatted_text = self.format_custom_event(custom_data)
                        yield ResponsesAgentStreamEvent(
                            type="response.output_item.done",
                            item=self.create_text_output_item(
                                text=formatted_text,
                                id=str(uuid4())
                            ),
                        )
                    except Exception as e:
                        logger.warning(f"Error processing custom event: {e}")
                
                # Handle debug mode (maximum detail)
                elif event_type == "debug":
                    try:
                        debug_data = event_data
                        # Convert to JSON-serializable format (handles LangChain messages)
                        serializable_data = self.make_json_serializable(debug_data)
                        
                        # Bulletproof JSON serialization with fallback for ANY remaining non-serializable objects
                        def json_fallback(obj):
                            """Final fallback for json.dumps() - converts anything to string."""
                            try:
                                return str(obj)
                            except:
                                return f"<{type(obj).__name__}>"
                        
                        # Emit detailed debug information (truncated for readability)
                        debug_str = json.dumps(serializable_data, indent=2, default=json_fallback)
                        if len(debug_str) > 500:
                            debug_str = debug_str[:500] + "..."
                        yield ResponsesAgentStreamEvent(
                            type="response.output_item.done",
                            item=self.create_text_output_item(
                                text=f"🔍 Debug: {debug_str}",
                                id=str(uuid4())
                            ),
                        )
                    except Exception as e:
                        logger.warning(f"Error processing debug event: {e}")
        
        logger.info(f"Workflow execution completed (thread: {thread_id})")


# Create the deployable agent
AGENT = SuperAgentHybridResponsesAgent(super_agent_hybrid)

print("\n" + "="*80)
print("✅ HYBRID SUPER AGENT RESPONSES AGENT CREATED")
print("="*80)
print("Architecture: OOP Agents + Explicit State Management")
print("Benefits:")
print("  ✓ Modular and testable agent classes")
print("  ✓ Full state observability for debugging")
print("  ✓ Production-ready with development-friendly design")
print("\nThis agent is now ready for:")
print("  1. Local testing with AGENT.predict()")
print("  2. Logging with mlflow.pyfunc.log_model()")
print("  3. Deployment to Databricks Model Serving")
print("\nMemory Features:")
print("  ✓ Short-term memory: Multi-turn conversations (CheckpointSaver)")
print("  ✓ Long-term memory: User preferences (DatabricksStore)")
print("  ✓ Works in distributed Model Serving (shared state via Lakebase)")
print("="*80)
print("\n🎉 Enhanced Granular Streaming Features:")
print("  ✓ Agent thinking and reasoning visibility")
print("  ✓ Intent detection (new question vs follow-up)")
print("  ✓ Clarity analysis with reasoning")
print("  ✓ Vector search progress and results")
print("  ✓ Execution plan formulation")
print("  ✓ UC function calls and Genie agent invocations")
print("  ✓ SQL generation progress")
print("  ✓ SQL validation and execution progress")
print("  ✓ Tool calls and tool results")
print("  ✓ Routing decisions between agents")
print("  ✓ Summary generation progress")
print("  ✓ Debug mode for maximum detail")
print("="*80)

# Set the agent for MLflow tracking
mlflow.langchain.autolog()
mlflow.models.set_model(AGENT)

# COMMAND ----------

# DBTITLE 1,Load Back the AGENT
from agent import AGENT

# COMMAND ----------

# or load the notebook with magic run
%run ./agent.py

# COMMAND ----------

# DBTITLE 1,Test Enhanced Granular Streaming
"""
Test the enhanced granular streaming to verify all execution steps are visible.
This will show agent thinking, tool calls, intermediate results, and routing decisions.

NOTE: Debug mode JSON serialization issue has been fixed!
The make_json_serializable() method now properly handles LangChain message objects
(AIMessage, SystemMessage, etc.) so debug events stream without errors.
"""

from mlflow.types.responses import ResponsesAgentRequest
from uuid import uuid4

test_query = "Show me the top 10 active plan members over 50 years old and with diabetes"

print(f"\n{'='*80}")
print(f"Testing Enhanced Granular Streaming")
print(f"{'='*80}")
print(f"Query: {test_query}\n")

# Create streaming request
thread_id = f"test-streaming-{str(uuid4())[:8]}"
print("thread_id in use:", thread_id)
request = ResponsesAgentRequest(
    input=[{"role": "user", "content": test_query}],
    custom_inputs={"thread_id": f"{thread_id}"}
)

# Stream all events and count them by type
event_counts = {"custom": 0, "updates": 0, "messages": 0, "debug": 0, "tool_calls": 0, "tool_results": 0, "routing": 0}

print("Streaming events:")
print("-" * 80)

for event in AGENT.predict_stream(request):
    if event.type == "response.output_item.done":
        item = event.item
        if hasattr(item, 'text') and item.text:
            text = item.text
            
            # Categorize event types
            if text.startswith("💭") or text.startswith("🚀") or text.startswith("🎯") or text.startswith("✓") or text.startswith("🔍") or text.startswith("📊") or text.startswith("📋") or text.startswith("🔧") or text.startswith("📝") or text.startswith("✅") or text.startswith("⚡") or text.startswith("📄"):
                event_counts["custom"] += 1
            elif text.startswith("🔹 Step:"):
                event_counts["updates"] += 1
            elif text.startswith("🔀 Routing"):
                event_counts["routing"] += 1
            elif text.startswith("🔨 Tool result"):
                event_counts["tool_results"] += 1
            elif text.startswith("🔍 Debug:"):
                event_counts["debug"] += 1
            
            # Print event (truncate long events)
            display_text = text if len(text) <= 150 else text[:150] + "..."
            print(f"  {display_text}")
        elif hasattr(item, 'function_call'):
            event_counts["tool_calls"] += 1
            print(f"  🛠️ Function call: {item.function_call.name}")

print("-" * 80)
print("\nEvent Summary:")
print(f"  Custom events (agent thinking/progress): {event_counts['custom']}")
print(f"  Node updates (state changes): {event_counts['updates']}")
print(f"  Routing decisions: {event_counts['routing']}")
print(f"  Tool calls: {event_counts['tool_calls']}")
print(f"  Tool results: {event_counts['tool_results']}")
print(f"  Debug events: {event_counts['debug']}")
print(f"  Total events: {sum(event_counts.values())}")
print(f"\n{'='*80}")
print("✅ Enhanced streaming test complete!")
print("All agent execution steps are now visible to users in real-time.")
print(f"{'='*80}\n")


# COMMAND ----------

# DBTITLE 1,clarification msg
follow_up_msg =  "Patients currently enrolled in a plan (based on enrollment date ranges); use diabetes code (E10-E14)"
print("thread_id in use:", thread_id)
# follow up of thread from above, update here
# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": f"{thread_id}"}
))

# COMMAND ----------

# DBTITLE 1,no clarify but actaully a new question msg
follow_up_msg =  "What are top 10 patients with highest medical charges?"
print("thread_id in use:", thread_id)
# follow up of thread from above, update here
# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": f"{thread_id}"}
))

# COMMAND ----------

# DBTITLE 1,continution msg
follow_up_msg =  "Now I want to look at medical and pharmacy cost combined"
print("thread_id in use:", thread_id)
# follow up of thread from above, update here
# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": f"{thread_id}"}
))

# COMMAND ----------

# DBTITLE 1,switch to new topic
follow_up_msg =  "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? use genie route"
thread_id = f"test-streaming-{str(uuid4())[:8]}"
print("thread_id in use:", thread_id)
# follow up of thread from above, update here
# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": f"{thread_id}"}
))

# COMMAND ----------

# DBTITLE 1,response to clarify
follow_up_msg =  "you decide; use genie route"
print("thread_id in use:", thread_id)
# follow up of thread from above, update here
# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": f"{thread_id}"}
))

# COMMAND ----------

# DBTITLE 1,refinement msg
follow_up_msg =  "remove the active plan requirement, add their plan date_start AND date_end info"
print("thread_id in use:", thread_id)
# follow up of thread from above, update here
# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": f"{thread_id}"}
))

# COMMAND ----------

# DBTITLE 1,refinement msg
follow_up_msg =  "now I only want to see those NOT from Medicare plan type, give also top 10"
print("thread_id in use:", thread_id)
# follow up of thread from above, update here
# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": f"{thread_id}"}
))

# COMMAND ----------



# COMMAND ----------

# DBTITLE 1,Test Agent with Short-term Memory (multiple chat rounds)
"""
Test short-term memory: Agent remembers context within a conversation thread.
This works across distributed Model Serving instances via CheckpointSaver.
"""

# Example 1: Start a new conversation
thread_id = str(uuid4())
print(f"Starting conversation with thread_id: {thread_id}")

from mlflow.types.responses import ResponsesAgentRequest

# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": "Show me patient demographics"}],
    custom_inputs={"thread_id": thread_id}
))
print("\n--- Response 1 ---")
print(result1.model_dump(exclude_none=True))

# Second message - agent should remember context
result2 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": "Filter by age > 50"}],
    custom_inputs={"thread_id": thread_id}  # Same thread_id
))
print("\n--- Response 2 (with context) ---")
print(result2.model_dump(exclude_none=True))

# Third message without thread_id - fresh conversation
result3 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": "What was I asking about?"}]
))
print("\n--- Response 3 (no context) ---")
print(result3.model_dump(exclude_none=True))

# COMMAND ----------

# Second message to follow-up/refine - agent should remember context
result2 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": "Filter patients where current age (as of today) is greater than 50"}],
    custom_inputs={"thread_id": thread_id}  # Same thread_id
))
print("\n--- Response 2 (with context) ---")
print(result2.model_dump(exclude_none=True))

# COMMAND ----------

# third message - agent should know this is a totally new question in the same thread
result2 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?"}],
    custom_inputs={"thread_id": thread_id}  # Same thread_id
))
print("\n--- Response 2 (with context) ---")
print(result2.model_dump(exclude_none=True))

# COMMAND ----------

# cause agent may need us to clarify the previous question as agent knows the previous question is a totally new question, we need to follow up here.
follow_up_msg = """
1. line allowed
2. aggregate by medical claim
3. age thing your call
"""
# clarify message -
result2 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": thread_id}
))
print("\n--- Response 2 (with context) ---")
print(result2.model_dump(exclude_none=True))


# COMMAND ----------

thread_id = str(uuid4)
# new message -
result2 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? Use Genie Route"}],
    custom_inputs={"thread_id": thread_id}  # new thread_id
))
print("\n--- Response 2 (with context) ---")
print(result2.model_dump(exclude_none=True))

# COMMAND ----------

# DBTITLE 1,Test Agent with Long-term Memory (User Preferences)
"""
Test long-term memory: Agent can save and recall user preferences across sessions.
Uses DatabricksStore with semantic search to find relevant memories.
"""

# Example 2: Using ChatContext for user_id (preferred for Model Serving)
# from mlflow.types.responses import ResponsesAgentRequest, ChatContext
# 
# user_email = "test.user@databricks.com"
# conversation_id = str(uuid4())
# 
# # Agent can save user preferences
# result1 = AGENT.predict(ResponsesAgentRequest(
#     input=[{"role": "user", "content": "I prefer viewing data as bar charts, and I usually work with patient demographics and clinical trials data"}],
#     context=ChatContext(
#         conversation_id=conversation_id,
#         user_id=user_email
#     )
# ))
# print("\n--- Saved User Preferences ---")
# print(result1.model_dump(exclude_none=True))
# 
# # In a new session, agent can recall preferences
# new_conversation_id = str(uuid4())
# result2 = AGENT.predict(ResponsesAgentRequest(
#     input=[{"role": "user", "content": "Show me the data I usually work with"}],
#     context=ChatContext(
#         conversation_id=new_conversation_id,  # Different conversation
#         user_id=user_email  # Same user
#     )
# ))
# print("\n--- Recalled User Preferences ---")
# print(result2.model_dump(exclude_none=True))

# COMMAND ----------

# DBTITLE 1,📡🔍Helper: Find All Required Resources for Deployment
"""
Run this helper to automatically discover all resources needed for deployment.
This ensures you don't miss any Genie spaces, tables, or UC functions.
"""

# Uncomment to discover resources
from databricks.sdk import WorkspaceClient
from pyspark.sql.functions import from_json, col
from pyspark.sql.types import MapType, StringType


print("="*80)
print("DISCOVERING DEPLOYMENT RESOURCES")
print("="*80)

# 1. Genie Space IDs (from config)
print("\n[1/4] Genie Space IDs:")
GENIE_SPACE_IDS = config.table_metadata.genie_space_ids
for space_id in GENIE_SPACE_IDS:
    print(f"  - {space_id}")

# 2. SQL Warehouse ID (you need to provide this manually)
print("\n[2/4] SQL Warehouse ID:")
print("  ⚠️ TODO: Get from SQL Warehouses UI → Click warehouse → Copy ID from URL or Details")
print("  Example: 'abc123def456'")
SQL_WAREHOUSE_ID = "148ccb90800933a1"  # UPDATE THIS!

# 3. Query underlying tables used by Genie spaces
print("\n[3/4] Querying underlying tables from metadata...")
try:
    query = f"""
    SELECT DISTINCT * 
    FROM {TABLE_NAME}
    WHERE table_name IS NOT NULL and chunk_type = 'table_overview'
    ORDER BY table_name
    """
    
    df = spark.sql(query)
    df_with_json = df.withColumn("metadata_json_parsed", from_json(col("metadata_json"), MapType(StringType(), StringType())))
    UNDERLYING_TABLES = df_with_json.select(col("metadata_json_parsed")["table_identifier"].alias("table_identifier")).toPandas().squeeze().tolist()
    print("UNDERLYING_TABLES:")
except Exception as e:
    print(f"Error: {e}")

for t in UNDERLYING_TABLES:
    print(f"  - {t}")

# 4. Generate resource list code
print("\n[4/4] Generated Resources Code:")
print("="*80)
print("""
# Copy this into your deployment cell:


resources = [
    # LLM endpoints
    DatabricksServingEndpoint(LLM_ENDPOINT_CLARIFICATION),
    DatabricksServingEndpoint(LLM_ENDPOINT_PLANNING),
    DatabricksServingEndpoint(LLM_ENDPOINT_SQL_SYNTHESIS),
    DatabricksServingEndpoint(LLM_ENDPOINT_SUMMARIZE),
    DatabricksServingEndpoint(EMBEDDING_ENDPOINT),
    
    # Lakebase for state management
    DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME),
    
    # Vector Search
    DatabricksVectorSearchIndex(index_name=VECTOR_SEARCH_INDEX),
    
    # SQL Warehouse
    DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
    
    # Genie Spaces (from config)
    *[DatabricksGenieSpace(genie_space_id=space_id) for space_id in GENIE_SPACE_IDS],
    
    # Tables
    DatabricksTable(table_name=TABLE_NAME),  # Metadata table
    *[DatabricksTable(table_name=table) for table in UNDERLYING_TABLES],
    
    # UC Functions
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_summary"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_table_overview"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_column_detail"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_details"),
]
""")
print("="*80)

# COMMAND ----------

# DBTITLE 1,🚀🤖Deploy Agent to Model Serving with Memory Support
"""
Register and deploy the agent with Lakebase resources for automatic authentication.
This enables the agent to access Lakebase in Model Serving without manual credentials.

⚠️ IMPORTANT: Before deploying, run the helper cell above to discover all required resources!

Per Databricks docs: "if you log a Genie Space, you must also log its tables, 
SQL Warehouses, and Unity Catalog functions"
Ref: https://docs.databricks.com/aws/en/generative-ai/agent-framework/agent-authentication
"""

# Step 1: Log model with resources
import mlflow
import logging
from mlflow.models.resources import (
    DatabricksServingEndpoint,
    DatabricksLakebase,
    DatabricksFunction,
    DatabricksVectorSearchIndex,
    DatabricksGenieSpace,
    DatabricksSQLWarehouse,
    DatabricksTable,
)
from pkg_resources import get_distribution

# # Declare all resources the agent needs
# resources = [
#     # LLM endpoints
#     DatabricksServingEndpoint(LLM_ENDPOINT_CLARIFICATION),
#     DatabricksServingEndpoint(LLM_ENDPOINT_PLANNING),
#     DatabricksServingEndpoint(LLM_ENDPOINT_SQL_SYNTHESIS),
#     DatabricksServingEndpoint(LLM_ENDPOINT_SUMMARIZE),
#     DatabricksServingEndpoint(EMBEDDING_ENDPOINT),
#     
#     # Lakebase for state management (CRITICAL!)
#     DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME),
#     
#     # Vector Search Index
#     DatabricksVectorSearchIndex(index_name=VECTOR_SEARCH_INDEX),
#     
#     # SQL Warehouse (required for Genie spaces and UC functions)
#     DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
#     
#     # Genie Spaces (IMPORTANT: Must declare all Genie spaces used by the agent!)
#     *[DatabricksGenieSpace(genie_space_id=space_id) for space_id in GENIE_SPACE_IDS],
#     
#     # Tables (metadata enrichment table + underlying Genie tables)
#     # Metadata enrichment table used by UC functions
#     DatabricksTable(table_name=TABLE_NAME),
#     # TODO: Add underlying tables that Genie spaces access
#     # Example: DatabricksTable(table_name=f"{CATALOG}.{SCHEMA}.patient_demographics"),
#     # You can query these from: SELECT DISTINCT table_name FROM enriched_genie_docs_chunks
#     
#     # UC Functions (metadata querying tools)
#     DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_summary"),
#     DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_table_overview"),
#     DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_column_detail"),
#     DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_details"),
# ]

resources = [
    # LLM endpoints
    DatabricksServingEndpoint(LLM_ENDPOINT_CLARIFICATION),
    DatabricksServingEndpoint(LLM_ENDPOINT_PLANNING),
    DatabricksServingEndpoint(LLM_ENDPOINT_SQL_SYNTHESIS),
    DatabricksServingEndpoint(LLM_ENDPOINT_SUMMARIZE),
    DatabricksServingEndpoint(EMBEDDING_ENDPOINT),
    
    # Lakebase for state management
    DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME),
    
    # Vector Search
    DatabricksVectorSearchIndex(index_name=VECTOR_SEARCH_INDEX),
    
    # SQL Warehouse
    DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
    
    # Genie Spaces (from config)
    *[DatabricksGenieSpace(genie_space_id=space_id) for space_id in GENIE_SPACE_IDS],
    
    # Tables
    DatabricksTable(table_name=TABLE_NAME),  # Metadata table
    *[DatabricksTable(table_name=table) for table in UNDERLYING_TABLES],
    
    # UC Functions
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_summary"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_table_overview"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_column_detail"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_details"),
]


input_example = {
    "input": [{"role": "user", "content": "Show me patient data"}],
    "custom_inputs": {"thread_id": "example-123"},
    "context": {"conversation_id": "sess-001", "user_id": "user@example.com"}
}

with mlflow.start_run():
    logged_agent_info = mlflow.pyfunc.log_model(
        name="super_agent_hybrid_with_memory",
        # ⚠️ IMPORTANT: Reference agent.py for clean MLflow deployment
        # agent.py contains runtime-essential components extracted from this notebook
        # This follows MLflow best practices with mlflow.langchain.autolog() and mlflow.models.set_model()
        python_model="./agent.py",  # Path relative to Notebooks/ folder
        input_example=input_example,
        resources=resources,
        # ⚠️ CRITICAL: Pass production config via ModelConfig (Databricks best practice!)
        # This config overrides the development_config in agent.py
        model_config="../prod_config.yaml",  # Production configuration
        pip_requirements=[
            f"databricks-langchain[memory]=={get_distribution('databricks-langchain').version}",
            f"databricks-agents=={get_distribution('databricks-agents').version}",
            f"databricks-vectorsearch=={get_distribution('databricks-vectorsearch').version}",
            f"mlflow[databricks]=={mlflow.__version__}",
        ]
    )
    print(f"✓ Model logged: {logged_agent_info.model_uri}")
    print(f"✓ Configuration: prod_config.yaml")

# Step 2: Register to Unity Catalog
mlflow.set_registry_uri("databricks-uc")
UC_MODEL_NAME = f"{CATALOG}.{SCHEMA}.super_agent_hybrid"

uc_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri,
    name=UC_MODEL_NAME
)
print(f"✓ Model registered: {UC_MODEL_NAME} version {uc_model_info.version}")

# Step 3: Deploy to Model Serving (No environment_vars needed!)
from databricks import agents

# ✅ With ModelConfig, configuration is packaged with the model
# No need for environment_vars parameter!
deployment_info = agents.deploy(
    UC_MODEL_NAME,
    uc_model_info.version,
    scale_to_zero=True,      # Cost optimization
    workload_size="Small",   # Start small, can scale up later
    # ✅ NO environment_vars needed - config is in model package!
)
print(f"✓ Deployed to Model Serving: {deployment_info.endpoint_name}")
print("\n" + "="*80)
print("✅ DEPLOYMENT COMPLETE")
print("="*80)
print(f"Model: {UC_MODEL_NAME} v{uc_model_info.version}")
print(f"Endpoint: {deployment_info.endpoint_name}")
print(f"Configuration: prod_config.yaml (packaged with model)")
print("\nMemory Features Enabled:")
print("  ✓ Short-term: Multi-turn conversations via CheckpointSaver")
print("  ✓ Long-term: User preferences via DatabricksStore")
print("  ✓ Distributed serving: State shared across all instances")
print("\nAdvantages of ModelConfig:")
print("  ✓ Configuration versioned with model")
print("  ✓ No environment_vars parameter needed")
print("  ✓ Easy to test different configs")
print("  ✓ Type-safe and structured")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Query Lakebase State (Monitoring)
"""
Query Lakebase to monitor checkpoint and memory usage.
"""

# # View recent checkpoints
# query_checkpoints = f"""
# SELECT 
#     thread_id,
#     checkpoint_id,
#     (checkpoint::json->>'ts')::timestamptz AS timestamp,
#     parent_checkpoint_id
# FROM checkpoints
# ORDER BY timestamp DESC
# LIMIT 20;
# """
# 
# # View user memories
# query_memories = f"""
# SELECT 
#     namespace,
#     key,
#     value,
#     updated_at
# FROM public.store
# WHERE namespace LIKE '%user_memories%'
# ORDER BY updated_at DESC
# LIMIT 50;
# """
# 
# print("Use these queries in your Lakebase SQL editor to monitor state:")
# print("\n1. Recent Checkpoints:")
# print(query_checkpoints)
# print("\n2. User Memories:")
# print(query_memories)

# COMMAND ----------

# DBTITLE 1,Helper Function: Invoke Hybrid Super Agent
def invoke_super_agent_hybrid(query: str, thread_id: str = "default") -> Dict[str, Any]:
    """
    Invoke the Hybrid Super Agent with a user query.
    
    Args:
        query: User's question
        thread_id: Thread ID for conversation tracking (default: "default")
    
    Returns:
        Final state with execution results
    """
    print("\n" + "="*80)
    print("🚀 INVOKING HYBRID SUPER AGENT")
    print("="*80)
    print(f"Query: {query}")
    print(f"Thread ID: {thread_id}")
    print("="*80)
    
    # Initialize state with reset template to clear per-query fields
    # NOTE: Workflow entry point is "intent_detection", so no need to set next_agent
    initial_state = {
        **RESET_STATE_TEMPLATE,  # Reset all per-query execution fields
        "original_query": query,  # DEPRECATED: Kept for backward compatibility
        "messages": [
            SystemMessage(content="""You are a multi-agent Q&A analysis system.
Your role is to help users query and analyze cross-domain data.

Guidelines:
- Always explain your reasoning and execution plan
- Validate SQL queries before execution
- Provide clear, comprehensive summaries
- If information is missing, ask for clarification (max once)
- Use UC functions and Genie agents to generate accurate SQL
- Return results with proper context and explanations"""),
            HumanMessage(content=query)
        ]
    }
    
    # Configure with thread
    config = {"configurable": {"thread_id": thread_id}}
    
    # Enable MLflow tracing
    mlflow.langchain.autolog()
    
    # Invoke the workflow
    final_state = super_agent_hybrid.invoke(initial_state, config)
    
    print("\n" + "="*80)
    print("✅ HYBRID SUPER AGENT EXECUTION COMPLETE")
    print("="*80)
    
    return final_state

# COMMAND ----------

# DBTITLE 1,Helper Function: Respond to Clarification (DEPRECATED - Use invoke_super_agent_hybrid)
def respond_to_clarification(
    clarification_response: str, 
    previous_state: Dict[str, Any] = None,  # No longer needed but kept for backward compatibility
    thread_id: str = "default"
) -> Dict[str, Any]:
    """
    SIMPLIFIED: Respond to a clarification request and continue the workflow.
    
    NOTE: This function is now just a wrapper around invoke_super_agent_hybrid().
    The agent auto-detects clarification responses from message history.
    You can call invoke_super_agent_hybrid() directly with the same thread_id.
    
    Args:
        clarification_response: Your clarification/answer to the agent's question
        previous_state: DEPRECATED - No longer needed, kept for backward compatibility
        thread_id: Thread ID for conversation tracking (must match previous call)
    
    Returns:
        Final state with execution results
    
    Example:
        # First call
        state1 = invoke_super_agent_hybrid("Show me the data", thread_id="session_001")
        
        # If clarification needed - SIMPLIFIED API!
        if not state1['question_clear']:
            clarification = state1.get('pending_clarification')
            if clarification:
                print("Clarification needed:", clarification['reason'])
                print("Options:", clarification['options'])
            
            # Just call invoke again with same thread_id
            state2 = respond_to_clarification(
                "Show me patient count by age group",
                thread_id="session_001"  # previous_state no longer needed!
            )
            
            # Or call invoke_super_agent_hybrid() directly:
            # state2 = invoke_super_agent_hybrid("Show me patient count by age group", thread_id="session_001")
    """
    print("\n" + "="*80)
    print("💬 RESPONDING TO CLARIFICATION (SIMPLIFIED)")
    print("="*80)
    print(f"User Response: {clarification_response}")
    print(f"Thread ID: {thread_id}")
    print("="*80)
    
    # SIMPLIFIED: Just call invoke_super_agent_hybrid with the same thread_id
    # The clarification_node will auto-detect this is a clarification response
    # by examining the message history
    return invoke_super_agent_hybrid(clarification_response, thread_id=thread_id)

# COMMAND ----------

# DBTITLE 1,Helper Function: Follow-Up Query
def ask_follow_up_query(
    new_query: str,
    thread_id: str = "default"
) -> Dict[str, Any]:
    """
    SIMPLIFIED: Ask a follow-up query in the same conversation thread.
    
    NOTE: This function is now just a wrapper around invoke_super_agent_hybrid().
    You can call invoke_super_agent_hybrid() directly with the same thread_id.
    
    Args:
        new_query: The new question to ask
        thread_id: Thread ID for conversation tracking (use same thread_id as previous calls)
    
    Returns:
        Final state with execution results
    
    Example:
        # First query
        state1 = invoke_super_agent_hybrid("Show me patient count", thread_id="session_001")
        display_results(state1)
        
        # Follow-up query - SIMPLIFIED API!
        # Option 1: Use this helper (deprecated but still works)
        state2 = ask_follow_up_query(
            "Now show me the average age by gender",
            thread_id="session_001"
        )
        
        # Option 2: Call invoke_super_agent_hybrid directly (recommended)
        state2 = invoke_super_agent_hybrid(
            "Now show me the average age by gender",
            thread_id="session_001"
        )
    """
    print("\n" + "="*80)
    print("💬 FOLLOW-UP QUERY (SIMPLIFIED)")
    print("="*80)
    print(f"New Query: {new_query}")
    print(f"Thread ID: {thread_id}")
    print("✓ This query will have access to previous conversation context")
    print("="*80)
    
    # SIMPLIFIED: Just call invoke_super_agent_hybrid with the same thread_id
    # CheckpointSaver will automatically restore conversation context
    return invoke_super_agent_hybrid(new_query, thread_id=thread_id)

# COMMAND ----------

# DBTITLE 1,Helper Function: Display Results
def display_results(final_state: Dict[str, Any]):
    """
    Display the results from the Hybrid Super Agent execution.
    Shows SQL query, execution results, plans, and any errors.
    """
    print("\n" + "="*80)
    print("📊 FINAL RESULTS")
    print("="*80)
    
    # Display Summary (if available)
    if final_state.get('final_summary'):
        print(f"\n📝 Summary:")
        print(f"  {final_state.get('final_summary')}")
        print()
    
    # Display Original Query
    print(f"\n🔍 Original Query:")
    print(f"  {final_state.get('original_query', 'N/A')}")
    
    # Display Clarification Info (if any)
    if not final_state.get('question_clear', True):
        clarification = final_state.get('pending_clarification')
        if clarification:
            print(f"\n⚠️  Clarification Needed:")
            print(f"  Reason: {clarification.get('reason', 'N/A')}")
            if clarification.get('options'):
                print(f"  Options:")
                for i, opt in enumerate(clarification.get('options', []), 1):
                    print(f"    {i}. {opt}")
    
    # Display Execution Plan
    if final_state.get('execution_plan'):
        print(f"\n📋 Execution Plan:")
        print(f"  {final_state.get('execution_plan')}")
        print(f"  Strategy: {final_state.get('join_strategy', 'N/A')}")
    
    # Display SQL Synthesis Explanation
    if final_state.get('sql_synthesis_explanation'):
        print(f"\n💭 SQL Synthesis Explanation:")
        print(f"  {final_state.get('sql_synthesis_explanation')}")
    
    # Display SQL
    if final_state.get('sql_query'):
        print(f"\n💻 Generated SQL:")
        print("─"*80)
        print(final_state.get('sql_query'))
        print("─"*80)
    
    # Display Execution Results
    exec_result = final_state.get('execution_result')
    if exec_result and exec_result.get('success'):
        print(f"\n✅ Execution Successful:")
        print(f"  Rows: {exec_result.get('row_count', 0)}")
        print(f"  Columns: {', '.join(exec_result.get('columns', []))}")
        
        # Display results preview
        results = exec_result.get("result", [])
        if results:
            print(f"\n📊 Query Results (first 10 rows):")
            for i, row in enumerate(results[:10], 1):
                print(f"  Row {i}: {row}")
    elif exec_result and not exec_result.get('success'):
        print(f"\n❌ Execution Failed:")
        print(f"  Error: {exec_result.get('error', 'Unknown error')}")
    
    # Display Errors
    if final_state.get('synthesis_error'):
        print(f"\n❌ Synthesis Error:")
        print(f"  {final_state.get('synthesis_error')}")
    if final_state.get('execution_error'):
        print(f"\n❌ Execution Error:")
        print(f"  {final_state.get('execution_error')}")
    
    print("\n" + "="*80)

# COMMAND ----------

# DBTITLE 1,Helper Function: Get Results as DataFrame
def get_results_as_dataframe(final_state: Dict[str, Any]):
    """
    Convert execution results to pandas DataFrame for easy analysis.
    
    Args:
        final_state: The final state from invoke_super_agent_hybrid()
        
    Returns:
        pandas.DataFrame or None if no results
        
    Example:
        final_state = invoke_super_agent_hybrid("Show claims")
        df = get_results_as_dataframe(final_state)
        if df is not None:
            print(df.describe())
            df.plot()
    """
    import pandas as pd
    
    exec_result = final_state.get('execution_result')
    if not exec_result or not exec_result.get('success'):
        print("⚠️ No successful execution results to convert")
        return None
    
    results = exec_result.get('result', [])
    if not results:
        print("⚠️ No data in results")
        return None
    
    try:
        df = pd.DataFrame(results)
        print(f"✅ Converted {len(results)} rows to pandas DataFrame")
        print(f"Shape: {df.shape}")
        print(f"Columns: {list(df.columns)}")
        return df
    except Exception as e:
        print(f"❌ Error converting to DataFrame: {e}")
        return None

print("✓ Helper functions defined")

# COMMAND ----------

# DBTITLE 1,Test Start Here
# Example test query
test_query = "What is the average cost of medical claims per claim in 2024?"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_001")

# COMMAND ----------

# DBTITLE 1,same query for genie route
# Example test query
test_query = "What is the average cost of medical claims per claim in 2024? Use genie route"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_001_genie")

# COMMAND ----------

# Example test query
test_query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_003_table")

##----clarify quetsions-----
#  Options:
#      1. Which specific diabetes diagnosis codes (ICD-10) should be included in the analysis (e.g., E10.x for Type 1, E11.x for Type 2, or all diabetes-related codes)?
#      2. Which cost metric should be used: line charges, allowed amounts, patient copays, or another financial measure from the claims data?
#      3. Should the analysis include only claims where diabetes is the primary diagnosis, or any claim where diabetes appears as a secondary diagnosis?

# COMMAND ----------

#-------
follow_up = """
 1. E10-E14
  2. both line charges and allowed
  3. any claim where a diabetes diagnosis appears

"""
# User provides clarification
final_state = respond_to_clarification(
    follow_up,
    previous_state=final_state,
    thread_id="test_hybrid_003_table"
)

# COMMAND ----------

# Example test query
test_query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? Use genie route"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_003_genie")

##----clarify quetsions-----
#  Options:
#      1. Which specific diabetes diagnosis codes (ICD-10) should be included in the analysis (e.g., E10.x for Type 1, E11.x for Type 2, or all diabetes-related codes)?
#      2. Which cost metric should be used: line charges, allowed amounts, patient copays, or another financial measure from the claims data?
#      3. Should the analysis include only claims where diabetes is the primary diagnosis, or any claim where diabetes appears as a secondary diagnosis?

# COMMAND ----------

#-------
follow_up = """
  1. both line charges and allowed
  2. you decide
  3. E10-E14

"""
# User provides clarification
final_state = respond_to_clarification(
    follow_up,
    previous_state=final_state,
    thread_id="test_hybrid_003_genie"
)

# COMMAND ----------

final_state

# COMMAND ----------

# DBTITLE 1,Test Hybrid Super Agent (table route)
# Display results
display_results(final_state)

# COMMAND ----------

# DBTITLE 1,Test Hybrid Super Agent (genie route)
# Example test query
test_query = "What is the average cost of medical claims  per claim in 2024? use genie route"

# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_002")

# Display results
display_results(final_state)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Additional Test Cases

# COMMAND ----------

# DBTITLE 1,Test Case 1: Simple Single-Space Query
test_query_1 = "How many patients are in the dataset?"
result_1 = invoke_super_agent_hybrid(test_query_1, thread_id="test_hybrid_simple")
display_results(result_1)

# COMMAND ----------

# DBTITLE 1,Test Case 2: Multi-Space Query with JOIN (Table Route)
test_query_2 = "What is the average cost of medical claims for patients diagnosed with diabetes?"
result_2 = invoke_super_agent_hybrid(test_query_2, thread_id="test_hybrid_fast")
display_results(result_2)

# COMMAND ----------

# DBTITLE 1,Test Case 3: Multi-Space Query with JOIN (Genie Route - Explicit)
test_query_3 = "What is the average cost of medical claims for patients diagnosed with diabetes? Use genie route"
result_3 = invoke_super_agent_hybrid(test_query_3, thread_id="test_hybrid_slow")
display_results(result_3)

# COMMAND ----------

# DBTITLE 1,Test Case 4: Complex Multi-Space Query
test_query_4 = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? Dont clarify. Use Genie Route."
result_4 = invoke_super_agent_hybrid(test_query_4, thread_id="test_hybrid_complex")
display_results(result_4)

# COMMAND ----------

# DBTITLE 1,Test Case 5: Clarification Flow (Vague Query → Clarify → Continue)
# Test with an intentionally vague query
test_query_5 = "How many?"  # Vague - should trigger clarification
result_5a = invoke_super_agent_hybrid(test_query_5, thread_id="test_hybrid_clarification")
display_results(result_5a)

# If clarification was requested, respond to it
if not result_5a.get('question_clear'):
    print("\n" + "="*80)
    print("📝 PROVIDING CLARIFICATION")
    print("="*80)
    
    # User provides clarification
    result_5b = respond_to_clarification(
        "I want to know how many patients are in the dataset",
        previous_state=result_5a,
        thread_id="test_hybrid_clarification"
    )
    
    # Display final results after clarification
    display_results(result_5b)
else:
    print("✓ No clarification needed - query was clear enough")

# COMMAND ----------

# DBTITLE 1,Test Case 6: Another Clarification Example
# Test with another vague query
test_query_6 = "Show me the data"  # Vague - should trigger clarification
result_6a = invoke_super_agent_hybrid(test_query_6, thread_id="test_hybrid_clarification2")
display_results(result_6a)

# If clarification was requested, respond to it
if not result_6a.get('question_clear'):
    print("\n" + "="*80)
    print("📝 PROVIDING CLARIFICATION")
    print("="*80)
    
    # User chooses one of the suggested options
    result_6b = respond_to_clarification(
        "Show me patient count grouped by age group",
        previous_state=result_6a,
        thread_id="test_hybrid_clarification2"
    )
    
    # Display final results after clarification
    display_results(result_6b)
else:
    print("✓ No clarification needed - query was clear enough")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Clarification Flow Summary
# MAGIC
# MAGIC The clarification system now works as follows:
# MAGIC
# MAGIC 1. **Lenient by Default**: Only asks for clarification on truly vague queries
# MAGIC 2. **Max 1 Clarification**: Won't ask endlessly - at most one clarification request
# MAGIC 3. **Easy Response Flow**: Use `respond_to_clarification()` to provide feedback
# MAGIC 4. **Proceeds Automatically**: After clarification, continues to planning and execution
# MAGIC
# MAGIC **Example Flow:**
# MAGIC ```python
# MAGIC # Step 1: Try with vague query
# MAGIC state1 = invoke_super_agent_hybrid("How many?", thread_id="session_001")
# MAGIC
# MAGIC # Step 2: Check if clarification needed
# MAGIC if not state1['question_clear']:
# MAGIC     # Step 3: Provide clarification
# MAGIC     state2 = respond_to_clarification(
# MAGIC         "How many patients are in the dataset?",
# MAGIC         previous_state=state1,
# MAGIC         thread_id="session_001"
# MAGIC     )
# MAGIC     # Step 4: Get results
# MAGIC     display_results(state2)
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC
# MAGIC ```
# MAGIC ===================================================================================
# MAGIC HYBRID SUPER AGENT - BEST OF BOTH WORLDS
# MAGIC ===================================================================================
# MAGIC
# MAGIC This notebook implements a hybrid architecture combining:
# MAGIC
# MAGIC 1. OOP AGENT CLASSES (from agent.py)
# MAGIC    ✅ Modular and testable
# MAGIC    ✅ Clean separation of concerns
# MAGIC    ✅ Reusable across projects
# MAGIC    ✅ Easy to extend and maintain
# MAGIC
# MAGIC 2. EXPLICIT STATE MANAGEMENT (from Super_Agent.py)
# MAGIC    ✅ Full observability
# MAGIC    ✅ Easy debugging
# MAGIC    ✅ Clear data flow
# MAGIC    ✅ Type-safe state tracking
# MAGIC
# MAGIC ARCHITECTURE:
# MAGIC ══════════════
# MAGIC
# MAGIC Agent Classes (OOP):
# MAGIC - ClarificationAgent
# MAGIC - PlanningAgent
# MAGIC - SQLSynthesisTableAgent
# MAGIC - SQLSynthesisGenieAgent
# MAGIC - SQLExecutionAgent
# MAGIC
# MAGIC Node Wrappers:
# MAGIC - clarification_node(state) → calls ClarificationAgent, updates state
# MAGIC - planning_node(state) → calls PlanningAgent, updates state
# MAGIC - sql_synthesis_table_node(state) → calls SQLSynthesisTableAgent, updates state
# MAGIC - sql_synthesis_genie_node(state) → calls SQLSynthesisGenieAgent, updates state
# MAGIC - sql_execution_node(state) → calls SQLExecutionAgent, updates state
# MAGIC - summarize_node(state) → calls ResultSummarizeAgent, preserves ALL state fields
# MAGIC
# MAGIC State Management:
# MAGIC - AgentState(TypedDict) with explicit fields
# MAGIC - Full observability at every step
# MAGIC - Easy to debug and monitor
# MAGIC - ALL fields preserved in final state (sql_query, results, explanations, errors, summary)
# MAGIC
# MAGIC BENEFITS:
# MAGIC ═════════
# MAGIC
# MAGIC ✅ Production Ready - Clean OOP design for maintainability
# MAGIC ✅ Easy Debugging - Explicit state shows exactly what's happening
# MAGIC ✅ Modular - Agent classes can be tested independently
# MAGIC ✅ Observable - All state transitions are visible
# MAGIC ✅ Extensible - Easy to add new agents or modify existing ones
# MAGIC ✅ Team-Friendly - Clear contracts and interfaces
# MAGIC
# MAGIC USAGE:
# MAGIC ══════
# MAGIC
# MAGIC # Simple invocation
# MAGIC final_state = invoke_super_agent_hybrid("Your question here", thread_id="session_123")
# MAGIC display_results(final_state)
# MAGIC
# MAGIC # Access ALL preserved fields programmatically
# MAGIC summary = final_state['final_summary']  # Natural language summary
# MAGIC sql = final_state.get('sql_query')  # Generated SQL
# MAGIC explanation = final_state.get('sql_synthesis_explanation')  # SQL generation explanation
# MAGIC plan = final_state.get('execution_plan')  # Execution plan
# MAGIC
# MAGIC if final_state['execution_result']['success']:
# MAGIC     data = final_state['execution_result']['result']  # Query results
# MAGIC     row_count = final_state['execution_result']['row_count']  # Number of rows
# MAGIC     columns = final_state['execution_result']['columns']  # Column names
# MAGIC else:
# MAGIC     error = final_state['execution_result'].get('error')  # Execution error
# MAGIC     synthesis_error = final_state.get('synthesis_error')  # SQL generation error
# MAGIC
# MAGIC NEXT STEPS:
# MAGIC ═══════════
# MAGIC
# MAGIC 1. Deploy to Databricks as a ResponsesAgent endpoint
# MAGIC 2. Monitor with MLflow tracing in production
# MAGIC 3. Extend with additional specialized agents
# MAGIC 4. Integrate with Genie UI
# MAGIC
# MAGIC ===================================================================================
# MAGIC ```

# COMMAND ----------

# DBTITLE 1,Example: Complete Clarification Flow with Context Preservation
# MAGIC %md
# MAGIC ### Example: Clarification Flow with Context Preservation
# MAGIC
# MAGIC This example demonstrates how the agent:
# MAGIC 1. Detects vague queries and asks for clarification
# MAGIC 2. **PRESERVES** original query, clarification message, and user response separately
# MAGIC 3. **COMBINES** all context for planning agent
# MAGIC 4. Continues workflow seamlessly
# MAGIC
# MAGIC """
# MAGIC # Example 1: Clarification Flow with Context Combination
# MAGIC session_id = "demo_clarification_001"
# MAGIC
# MAGIC # Step 1: Ask a vague query
# MAGIC print("="*80)
# MAGIC print("STEP 1: Initial vague query")
# MAGIC print("="*80)
# MAGIC state1 = invoke_super_agent_hybrid(
# MAGIC     "Show me the data about patients",  # Vague - what data? which patients?
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC
# MAGIC # Check if clarification is needed
# MAGIC if not state1.get('question_clear'):
# MAGIC     print("\n" + "="*80)
# MAGIC     print("CLARIFICATION REQUESTED BY AGENT")
# MAGIC     print("="*80)
# MAGIC     print(f"Original Query (preserved): {state1['original_query']}")
# MAGIC     clarification = state1.get('pending_clarification')
# MAGIC     if clarification:
# MAGIC         print(f"Clarification Needed: {clarification['reason']}")
# MAGIC         print(f"Options: {clarification['options']}")
# MAGIC     
# MAGIC     # Step 2: User provides clarification
# MAGIC     print("\n" + "="*80)
# MAGIC     print("STEP 2: User provides clarification")
# MAGIC     print("="*80)
# MAGIC     state2 = respond_to_clarification(
# MAGIC         "Show me patient count grouped by age group",
# MAGIC         previous_state=state1,
# MAGIC         thread_id=session_id
# MAGIC     )
# MAGIC     
# MAGIC     # Verify context was properly combined
# MAGIC     print("\n" + "="*80)
# MAGIC     print("CONTEXT VERIFICATION")
# MAGIC     print("="*80)
# MAGIC     print(f"✓ Original Query Preserved: {state2.get('original_query')}")
# MAGIC     # NEW: Access clarification through pending_clarification object
# MAGIC     clarification = state2.get('pending_clarification')
# MAGIC     if clarification:
# MAGIC         print(f"✓ Clarification Reason: {clarification.get('reason', 'N/A')[:100]}...")
# MAGIC     # NEW: Context is now in current_turn.context_summary (from intent detection)
# MAGIC     current_turn = state2.get('current_turn')
# MAGIC     if current_turn:
# MAGIC         print(f"✓ Context Summary Created: {current_turn.get('context_summary') is not None}")
# MAGIC     
# MAGIC     display_results(state2)
# MAGIC else:
# MAGIC     display_results(state1)
# MAGIC """

# COMMAND ----------

# DBTITLE 1,Example: Follow-Up Queries with Conversation Continuity
# MAGIC %md
# MAGIC ### Example: Follow-Up Queries with Conversation Continuity
# MAGIC
# MAGIC This example demonstrates conversation continuity across multiple queries:
# MAGIC - Each new query has access to previous conversation context
# MAGIC - Thread-based memory preserves state across invocations
# MAGIC - Users can ask related follow-up questions naturally
# MAGIC
# MAGIC """
# MAGIC # Example 2: Multi-Turn Conversation with Follow-Ups
# MAGIC session_id = "demo_followup_001"
# MAGIC
# MAGIC # Turn 1: First query
# MAGIC print("="*80)
# MAGIC print("TURN 1: Initial Query")
# MAGIC print("="*80)
# MAGIC state1 = invoke_super_agent_hybrid(
# MAGIC     "How many active plan members do we have?",
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC display_results(state1)
# MAGIC
# MAGIC # Turn 2: Follow-up query building on Turn 1
# MAGIC print("\n" + "="*80)
# MAGIC print("TURN 2: Follow-Up Query")
# MAGIC print("="*80)
# MAGIC state2 = ask_follow_up_query(
# MAGIC     "What's the breakdown by age group?",  # Refers to "active plan members" from Turn 1
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC display_results(state2)
# MAGIC
# MAGIC # Turn 3: Another follow-up
# MAGIC print("\n" + "="*80)
# MAGIC print("TURN 3: Second Follow-Up")
# MAGIC print("="*80)
# MAGIC state3 = ask_follow_up_query(
# MAGIC     "Now show me the gender distribution for the 50+ age group",  # Builds on previous context
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC display_results(state3)
# MAGIC
# MAGIC # Turn 4: Completely new question in same thread
# MAGIC print("\n" + "="*80)
# MAGIC print("TURN 4: New Question (but same thread)")
# MAGIC print("="*80)
# MAGIC state4 = ask_follow_up_query(
# MAGIC     "Which medications are most prescribed for diabetes patients?",  # New topic
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC display_results(state4)
# MAGIC
# MAGIC print("\n" + "="*80)
# MAGIC print("✅ CONVERSATION SUMMARY")
# MAGIC print("="*80)
# MAGIC print(f"Session ID: {session_id}")
# MAGIC print(f"Total Turns: 4")
# MAGIC print(f"✓ All queries had access to previous conversation context")
# MAGIC print(f"✓ Thread-based memory preserved state across invocations")
# MAGIC print("="*80)
# MAGIC """

# COMMAND ----------

# DBTITLE 1,Example: Clarification + Follow-Up Combined
# MAGIC %md
# MAGIC ### Example: Clarification + Follow-Up Combined
# MAGIC
# MAGIC This example shows the complete workflow:
# MAGIC 1. Vague query → Clarification requested
# MAGIC 2. User clarifies → Workflow completes
# MAGIC 3. Follow-up query → Uses context from Turn 1+2
# MAGIC
# MAGIC """
# MAGIC # Example 3: Complete Multi-Turn Workflow
# MAGIC session_id = "demo_complete_001"
# MAGIC
# MAGIC # Turn 1: Vague query triggers clarification
# MAGIC print("="*80)
# MAGIC print("TURN 1: Vague Query")
# MAGIC print("="*80)
# MAGIC state1 = invoke_super_agent_hybrid(
# MAGIC     "Show me patient costs",  # Vague - what costs? which patients?
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC
# MAGIC if not state1.get('question_clear'):
# MAGIC     # Turn 2: Respond to clarification
# MAGIC     print("\n" + "="*80)
# MAGIC     print("TURN 2: Clarification Response")
# MAGIC     print("="*80)
# MAGIC     state2 = respond_to_clarification(
# MAGIC         "Show me average total claim costs for diabetic patients by insurance type",
# MAGIC         previous_state=state1,
# MAGIC         thread_id=session_id
# MAGIC     )
# MAGIC     display_results(state2)
# MAGIC     
# MAGIC     # Turn 3: Follow-up question
# MAGIC     print("\n" + "="*80)
# MAGIC     print("TURN 3: Follow-Up After Clarification")
# MAGIC     print("="*80)
# MAGIC     state3 = ask_follow_up_query(
# MAGIC         "What about only Medicare patients over 65?",  # Refines previous query
# MAGIC         thread_id=session_id
# MAGIC     )
# MAGIC     display_results(state3)
# MAGIC     
# MAGIC     # Turn 4: Another follow-up
# MAGIC     print("\n" + "="*80)
# MAGIC     print("TURN 4: Second Follow-Up")
# MAGIC     print("="*80)
# MAGIC     state4 = ask_follow_up_query(
# MAGIC         "Compare that to Medicaid patients in the same age group",
# MAGIC         thread_id=session_id
# MAGIC     )
# MAGIC     display_results(state4)
# MAGIC     
# MAGIC     print("\n" + "="*80)
# MAGIC     print("✅ WORKFLOW SUMMARY")
# MAGIC     print("="*80)
# MAGIC     print(f"Session ID: {session_id}")
# MAGIC     print(f"Turn 1: Vague query → Clarification requested")
# MAGIC     print(f"Turn 2: User clarified → Context combined and preserved")
# MAGIC     print(f"Turn 3-4: Follow-ups → Used combined context from Turn 1+2")
# MAGIC     print(f"✓ Original query always preserved")
# MAGIC     print(f"✓ Clarification context properly combined")
# MAGIC     print(f"✓ Thread memory maintained conversation continuity")
# MAGIC     print("="*80)
# MAGIC """

# COMMAND ----------

# DBTITLE 1,Example: Multiple Conversations with Different Threads
# MAGIC %md
# MAGIC ### Example: Multiple Parallel Conversations
# MAGIC
# MAGIC Thread IDs enable multiple independent conversations:
# MAGIC - Each thread maintains its own conversation context
# MAGIC - Different users or sessions don't interfere with each other
# MAGIC
# MAGIC """
# MAGIC # Example 4: Multiple Independent Conversations
# MAGIC # Conversation A: Patient demographics
# MAGIC thread_a = "user_alice_session_001"
# MAGIC stateA1 = invoke_super_agent_hybrid("Show patient count by age", thread_id=thread_a)
# MAGIC stateA2 = ask_follow_up_query("Now by gender", thread_id=thread_a)
# MAGIC stateA3 = ask_follow_up_query("Focus on 50+ age group", thread_id=thread_a)
# MAGIC
# MAGIC # Conversation B: Medication analysis (completely independent)
# MAGIC thread_b = "user_bob_session_001"
# MAGIC stateB1 = invoke_super_agent_hybrid("Which drugs are most prescribed?", thread_id=thread_b)
# MAGIC stateB2 = ask_follow_up_query("For diabetes patients only", thread_id=thread_b)
# MAGIC
# MAGIC # Conversation C: Cost analysis (independent)
# MAGIC thread_c = "user_charlie_session_001"
# MAGIC stateC1 = invoke_super_agent_hybrid("Average claim costs", thread_id=thread_c)
# MAGIC
# MAGIC print("\n" + "="*80)
# MAGIC print("✅ MULTIPLE CONVERSATIONS")
# MAGIC print("="*80)
# MAGIC print(f"Thread A ({thread_a}): 3 turns about patient demographics")
# MAGIC print(f"Thread B ({thread_b}): 2 turns about medications")
# MAGIC print(f"Thread C ({thread_c}): 1 turn about costs")
# MAGIC print(f"✓ All threads maintained independent conversation contexts")
# MAGIC print(f"✓ No cross-contamination between threads")
# MAGIC print("="*80)
# MAGIC """

# COMMAND ----------


