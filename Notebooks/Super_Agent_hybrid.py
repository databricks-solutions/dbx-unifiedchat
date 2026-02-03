# Databricks notebook source
# DBTITLE 1,Auto reload Local Package
# MAGIC %load_ext autoreload
# MAGIC %autoreload 2

# COMMAND ----------

# DBTITLE 1,Install Packages
# MAGIC %pip install python-dotenv databricks-sdk databricks-sql-connector databricks-langchain[memory]==0.12.1 databricks-vectorsearch==0.63 databricks-agents mlflow[databricks]>=3.6.0

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

# DBTITLE 1,Configuration
"""
Configuration loaded from config.py and .env file.
To update configuration, edit .env file instead of this notebook.

SQL_WAREHOUSE_ID CONFIGURATION:
Required for SQLExecutionAgent to execute queries via SQL Warehouse.

Development Environment:
- Set SQL_WAREHOUSE_ID in .env file: SQL_WAREHOUSE_ID=your-dev-warehouse-id
- Get warehouse ID from: SQL Warehouses UI → Click warehouse → Copy ID from URL or Details

Production Environment (Model Serving):
- Option 1: Set SQL_WAREHOUSE_ID in .env file (if same warehouse for dev/prod)
- Option 2: Configure via Model Serving endpoint environment variables:
  - Add environment variable: SQL_WAREHOUSE_ID=your-prod-warehouse-id
  - Or use secrets: SQL_WAREHOUSE_ID={{secrets/scope/warehouse_id_key}}
- The agent will use environment variables if available, falling back to .env

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

# Validate SQL_WAREHOUSE_ID is configured
if not SQL_WAREHOUSE_ID:
    print("⚠️ WARNING: SQL_WAREHOUSE_ID is not configured!")
    print("Set it in .env file: SQL_WAREHOUSE_ID=your-warehouse-id")
    print("Get warehouse ID from: SQL Warehouses UI → Click warehouse → Copy ID from URL or Details")
    print("Example: SQL_WAREHOUSE_ID=148ccb90800933a1")
    raise ValueError("SQL_WAREHOUSE_ID must be configured in .env file")

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
# MAGIC %%writefile agent.py
# MAGIC """
# MAGIC Super Agent (Hybrid Architecture) - Multi-Agent System Orchestrator
# MAGIC
# MAGIC This notebook implements a hybrid architecture combining:
# MAGIC - OOP agent classes (from agent.py) for modularity and reusability
# MAGIC - Explicit state management (from Super_Agent.py) for observability and debugging
# MAGIC
# MAGIC Architecture Benefits:
# MAGIC 1. ✅ OOP modularity for agent logic - Easy to test and maintain
# MAGIC 2. ✅ Explicit state for observability - Clear debugging and monitoring
# MAGIC 3. ✅ Best practices from both approaches
# MAGIC 4. ✅ Production-ready with rapid development capabilities
# MAGIC
# MAGIC Components:
# MAGIC 1. Clarification Agent - Validates query clarity (OOP class)
# MAGIC 2. Planning Agent - Creates execution plan and identifies relevant spaces (OOP class)
# MAGIC 3. SQL Synthesis Agent (Table Route) - Generates SQL using UC tools (OOP class)
# MAGIC 4. SQL Synthesis Agent (Genie Route) - Generates SQL using Genie agents (OOP class)
# MAGIC 5. SQL Execution Agent - Executes SQL and returns results (OOP class)
# MAGIC
# MAGIC The Super Agent uses LangGraph with explicit state tracking for orchestration.
# MAGIC """
# MAGIC
# MAGIC import json
# MAGIC from typing import Dict, List, Optional, Any, Annotated, Literal, Generator
# MAGIC from typing_extensions import TypedDict
# MAGIC import operator
# MAGIC from uuid import uuid4
# MAGIC import re
# MAGIC from functools import partial
# MAGIC from databricks_langchain import (
# MAGIC     ChatDatabricks,
# MAGIC     VectorSearchRetrieverTool,
# MAGIC     DatabricksFunctionClient,
# MAGIC     UCFunctionToolkit,
# MAGIC     set_uc_function_client,
# MAGIC     CheckpointSaver,  # For short-term memory (distributed serving)
# MAGIC     DatabricksStore,  # For long-term memory (user preferences)
# MAGIC )
# MAGIC
# MAGIC # Import conversation management modules
# MAGIC import sys
# MAGIC from pathlib import Path
# MAGIC # # (obsolete) Add kumc_poc to path if not already present
# MAGIC # kumc_poc_path = str(Path(__file__).parent.parent / "kumc_poc") if '__file__' in globals() else "../kumc_poc"
# MAGIC # if kumc_poc_path not in sys.path:
# MAGIC #     sys.path.insert(0, kumc_poc_path)
# MAGIC
# MAGIC # NOTE: No imports from kumc_poc - all TypedDicts and logic are inline
# MAGIC # This simplifies the agent and makes it self-contained
# MAGIC from databricks_langchain.genie import GenieAgent
# MAGIC from langchain.agents import create_agent
# MAGIC from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, AIMessageChunk
# MAGIC from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel, RunnableConfig
# MAGIC from langchain_core.tools import tool, StructuredTool
# MAGIC import mlflow
# MAGIC import logging
# MAGIC from pydantic import BaseModel, Field
# MAGIC import json
# MAGIC
# MAGIC
# MAGIC # LangGraph imports
# MAGIC from langgraph.graph import StateGraph, END
# MAGIC from langgraph.graph.state import CompiledStateGraph
# MAGIC
# MAGIC # MLflow ResponsesAgent imports
# MAGIC from mlflow.pyfunc import ResponsesAgent
# MAGIC from mlflow.types.responses import (
# MAGIC     ResponsesAgentRequest,
# MAGIC     ResponsesAgentResponse,
# MAGIC     ResponsesAgentStreamEvent,
# MAGIC     output_to_responses_items_stream,
# MAGIC     to_chat_completions_input,
# MAGIC )
# MAGIC
# MAGIC # Setup logging
# MAGIC logger = logging.getLogger(__name__)
# MAGIC
# MAGIC
# MAGIC ########################################
# MAGIC # Configuration Loading with ModelConfig
# MAGIC ########################################
# MAGIC
# MAGIC from mlflow.models import ModelConfig
# MAGIC
# MAGIC # Development configuration (used for local testing)
# MAGIC # When deployed, this will be overridden by the config passed to log_model()
# MAGIC development_config = {
# MAGIC     # Unity Catalog Configuration
# MAGIC     "catalog_name": "yyang",
# MAGIC     "schema_name": "multi_agent_genie",
# MAGIC     
# MAGIC     # LLM Endpoint Configuration
# MAGIC     "llm_endpoint": "databricks-claude-sonnet-4-5",
# MAGIC     
# MAGIC     # Vector Search Configuration
# MAGIC     "vs_endpoint_name": "genie_multi_agent_vs",
# MAGIC     "embedding_model": "databricks-gte-large-en",
# MAGIC     
# MAGIC     # Lakebase Configuration (for State Management)
# MAGIC     "lakebase_instance_name": "multi-agent-genie-system-state-db",
# MAGIC     "lakebase_embedding_endpoint": "databricks-gte-large-en",
# MAGIC     "lakebase_embedding_dims": 1024,
# MAGIC     
# MAGIC     # Genie Space IDs
# MAGIC     "genie_space_ids": [
# MAGIC         "01f0eab621401f9faa11e680f5a2bcd0",
# MAGIC         "01f0eababd9f1bcab5dea65cf67e48e3",
# MAGIC         "01f0eac186d11b9897bc1d43836cc4e1"
# MAGIC     ],
# MAGIC     
# MAGIC     # SQL Warehouse ID
# MAGIC     "sql_warehouse_id": "148ccb90800933a1",
# MAGIC     
# MAGIC     # Table Metadata Enrichment
# MAGIC     "sample_size": 20,
# MAGIC     "max_unique_values": 20,
# MAGIC }
# MAGIC
# MAGIC # Initialize ModelConfig
# MAGIC # For local development: Uses development_config above
# MAGIC # For Model Serving: Uses config passed during mlflow.pyfunc.log_model(model_config=...)
# MAGIC model_config = ModelConfig(development_config=development_config)
# MAGIC
# MAGIC logger.info("="*80)
# MAGIC logger.info("CONFIGURATION LOADED via ModelConfig")
# MAGIC logger.info("="*80)
# MAGIC
# MAGIC # Extract configuration values
# MAGIC CATALOG = model_config.get("catalog_name")
# MAGIC SCHEMA = model_config.get("schema_name")
# MAGIC TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"
# MAGIC VECTOR_SEARCH_INDEX = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks_vs_index"
# MAGIC
# MAGIC # LLM Endpoints
# MAGIC LLM_ENDPOINT_CLARIFICATION = model_config.get("llm_endpoint")
# MAGIC LLM_ENDPOINT_PLANNING = model_config.get("llm_endpoint")
# MAGIC LLM_ENDPOINT_SQL_SYNTHESIS = model_config.get("llm_endpoint")
# MAGIC LLM_ENDPOINT_SUMMARIZE = model_config.get("llm_endpoint")
# MAGIC
# MAGIC # Lakebase configuration for state management
# MAGIC LAKEBASE_INSTANCE_NAME = model_config.get("lakebase_instance_name")
# MAGIC EMBEDDING_ENDPOINT = model_config.get("lakebase_embedding_endpoint")
# MAGIC EMBEDDING_DIMS = model_config.get("lakebase_embedding_dims")
# MAGIC
# MAGIC # Genie space IDs
# MAGIC GENIE_SPACE_IDS = model_config.get("genie_space_ids")
# MAGIC
# MAGIC # SQL Warehouse ID (required for SQLExecutionAgent)
# MAGIC SQL_WAREHOUSE_ID = model_config.get("sql_warehouse_id")
# MAGIC
# MAGIC # UC Functions
# MAGIC UC_FUNCTION_NAMES = [
# MAGIC     f"{CATALOG}.{SCHEMA}.get_space_summary",
# MAGIC     f"{CATALOG}.{SCHEMA}.get_table_overview",
# MAGIC     f"{CATALOG}.{SCHEMA}.get_column_detail",
# MAGIC     f"{CATALOG}.{SCHEMA}.get_space_details",
# MAGIC ]
# MAGIC
# MAGIC logger.info(f"Catalog: {CATALOG}, Schema: {SCHEMA}")
# MAGIC logger.info(f"Lakebase: {LAKEBASE_INSTANCE_NAME}")
# MAGIC logger.info(f"Genie Spaces: {len(GENIE_SPACE_IDS)} spaces configured")
# MAGIC logger.info(f"SQL Warehouse ID: {SQL_WAREHOUSE_ID}")
# MAGIC
# MAGIC # Validate SQL_WAREHOUSE_ID is configured
# MAGIC if not SQL_WAREHOUSE_ID:
# MAGIC     error_msg = (
# MAGIC         "SQL_WAREHOUSE_ID is not configured! "
# MAGIC         "Ensure 'sql_warehouse_id' is set in prod_config.yaml or development_config."
# MAGIC     )
# MAGIC     logger.error(error_msg)
# MAGIC     raise ValueError(error_msg)
# MAGIC
# MAGIC logger.info("="*80)
# MAGIC
# MAGIC # Initialize UC Function Client
# MAGIC client = DatabricksFunctionClient()
# MAGIC set_uc_function_client(client)
# MAGIC
# MAGIC logger.info(f"Configuration loaded: Catalog={CATALOG}, Schema={SCHEMA}, Lakebase={LAKEBASE_INSTANCE_NAME}")
# MAGIC
# MAGIC print("✓ All dependencies imported successfully (including memory support)")
# MAGIC def query_delta_table(table_name: str, filter_field: str, filter_value: str, select_fields: List[str] = None) -> Any:
# MAGIC     """
# MAGIC     Query a delta table with a filter condition.
# MAGIC     
# MAGIC     Args:
# MAGIC         table_name: Full table name (catalog.schema.table)
# MAGIC         filter_field: Field name to filter on
# MAGIC         filter_value: Value to filter by
# MAGIC         select_fields: List of fields to select (None = all fields)
# MAGIC     
# MAGIC     Returns:
# MAGIC         Spark DataFrame with query results
# MAGIC     """
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
# MAGIC # Note: Context is now loaded dynamically in clarification_node
# MAGIC # This allows refresh without model redeployment
# MAGIC # ==============================================================================
# MAGIC # Inline TypedDicts for Unified Agent (No kumc_poc imports)
# MAGIC # ==============================================================================
# MAGIC
# MAGIC from typing import TypedDict, Optional, List, Dict, Any, Literal, Annotated
# MAGIC from datetime import datetime
# MAGIC import operator
# MAGIC import uuid as uuid_module
# MAGIC
# MAGIC class ConversationTurn(TypedDict):
# MAGIC     """
# MAGIC     Represents a single conversation turn with all its context.
# MAGIC     Inline definition for simplified unified agent.
# MAGIC     """
# MAGIC     turn_id: str
# MAGIC     query: str
# MAGIC     intent_type: Literal["new_question", "refinement", "continuation", "clarification_response"]
# MAGIC     parent_turn_id: Optional[str]
# MAGIC     context_summary: Optional[str]
# MAGIC     timestamp: str  # ISO format datetime string
# MAGIC     triggered_clarification: Optional[bool]
# MAGIC     metadata: Optional[Dict[str, Any]]
# MAGIC
# MAGIC class ClarificationRequest(TypedDict):
# MAGIC     """Unified clarification request object."""
# MAGIC     reason: str
# MAGIC     options: List[str]
# MAGIC     turn_id: str
# MAGIC     timestamp: str
# MAGIC     best_guess: Optional[str]
# MAGIC     best_guess_confidence: Optional[float]
# MAGIC
# MAGIC class IntentMetadata(TypedDict):
# MAGIC     """Intent metadata for business logic."""
# MAGIC     intent_type: Literal["new_question", "refinement", "continuation", "clarification_response"]
# MAGIC     confidence: float
# MAGIC     reasoning: str
# MAGIC     topic_change_score: float
# MAGIC     domain: Optional[str]
# MAGIC     operation: Optional[str]
# MAGIC     complexity: Literal["simple", "moderate", "complex"]
# MAGIC     parent_turn_id: Optional[str]
# MAGIC
# MAGIC class AgentState(TypedDict):
# MAGIC     """Simplified agent state using turn-based context management."""
# MAGIC     # Turn Management
# MAGIC     current_turn: Optional[ConversationTurn]
# MAGIC     turn_history: Annotated[List[ConversationTurn], operator.add]
# MAGIC     intent_metadata: Optional[IntentMetadata]
# MAGIC     
# MAGIC     # Clarification
# MAGIC     pending_clarification: Optional[ClarificationRequest]
# MAGIC     question_clear: bool
# MAGIC     
# MAGIC     # Deprecated
# MAGIC     original_query: Optional[str]
# MAGIC     
# MAGIC     # Planning
# MAGIC     plan: Optional[Dict[str, Any]]
# MAGIC     sub_questions: Optional[List[str]]
# MAGIC     requires_multiple_spaces: Optional[bool]
# MAGIC     relevant_space_ids: Optional[List[str]]
# MAGIC     relevant_spaces: Optional[List[Dict[str, Any]]]
# MAGIC     vector_search_relevant_spaces_info: Optional[List[Dict[str, str]]]
# MAGIC     requires_join: Optional[bool]
# MAGIC     join_strategy: Optional[str]
# MAGIC     execution_plan: Optional[str]
# MAGIC     genie_route_plan: Optional[Dict[str, str]]
# MAGIC     
# MAGIC     # SQL Synthesis
# MAGIC     sql_query: Optional[str]
# MAGIC     sql_synthesis_explanation: Optional[str]
# MAGIC     synthesis_error: Optional[str]
# MAGIC     
# MAGIC     # Execution
# MAGIC     execution_result: Optional[Dict[str, Any]]
# MAGIC     execution_error: Optional[str]
# MAGIC     
# MAGIC     # Summary
# MAGIC     final_summary: Optional[str]
# MAGIC     
# MAGIC     # Conversation Management
# MAGIC     user_id: Optional[str]
# MAGIC     thread_id: Optional[str]
# MAGIC     user_preferences: Optional[Dict[str, Any]]
# MAGIC     
# MAGIC     # Control Flow
# MAGIC     next_agent: Optional[str]
# MAGIC     messages: Annotated[List, operator.add]
# MAGIC
# MAGIC # Helper functions
# MAGIC def create_conversation_turn(
# MAGIC     query: str,
# MAGIC     intent_type: Literal["new_question", "refinement", "continuation", "clarification_response"],
# MAGIC     parent_turn_id: Optional[str] = None,
# MAGIC     context_summary: Optional[str] = None,
# MAGIC     triggered_clarification: bool = False,
# MAGIC     metadata: Optional[Dict[str, Any]] = None
# MAGIC ) -> ConversationTurn:
# MAGIC     """
# MAGIC     Factory function to create a ConversationTurn with runtime validation.
# MAGIC     
# MAGIC     Args:
# MAGIC         query: User's query string
# MAGIC         intent_type: Must be one of: "new_question", "refinement", "continuation", "clarification_response"
# MAGIC         parent_turn_id: Optional parent turn ID for context
# MAGIC         context_summary: Optional summary of conversation context
# MAGIC         triggered_clarification: Whether this turn triggered clarification
# MAGIC         metadata: Optional additional metadata
# MAGIC         
# MAGIC     Returns:
# MAGIC         ConversationTurn with validated intent_type
# MAGIC         
# MAGIC     Raises:
# MAGIC         ValueError: If intent_type is not one of the allowed values
# MAGIC     """
# MAGIC     # Runtime validation to enforce type contract
# MAGIC     valid_intent_types = {"new_question", "refinement", "continuation", "clarification_response"}
# MAGIC     if intent_type not in valid_intent_types:
# MAGIC         raise ValueError(
# MAGIC             f"Invalid intent_type: '{intent_type}'. "
# MAGIC             f"Must be one of: {valid_intent_types}."
# MAGIC         )
# MAGIC     
# MAGIC     return ConversationTurn(
# MAGIC         turn_id=str(uuid_module.uuid4()),
# MAGIC         query=query,
# MAGIC         intent_type=intent_type,
# MAGIC         parent_turn_id=parent_turn_id,
# MAGIC         context_summary=context_summary,
# MAGIC         timestamp=datetime.utcnow().isoformat(),
# MAGIC         triggered_clarification=triggered_clarification,
# MAGIC         metadata=metadata or {}
# MAGIC     )
# MAGIC
# MAGIC def create_clarification_request(
# MAGIC     reason: str,
# MAGIC     options: List[str],
# MAGIC     turn_id: str,
# MAGIC     best_guess: Optional[str] = None,
# MAGIC     best_guess_confidence: Optional[float] = None
# MAGIC ) -> ClarificationRequest:
# MAGIC     """Factory function to create a ClarificationRequest."""
# MAGIC     return ClarificationRequest(
# MAGIC         reason=reason,
# MAGIC         options=options,
# MAGIC         turn_id=turn_id,
# MAGIC         timestamp=datetime.utcnow().isoformat(),
# MAGIC         best_guess=best_guess,
# MAGIC         best_guess_confidence=best_guess_confidence
# MAGIC     )
# MAGIC
# MAGIC def format_clarification_message(clarification: ClarificationRequest) -> str:
# MAGIC     """Format a clarification request into a user-friendly message."""
# MAGIC     message = f"I need clarification: {clarification['reason']}\n\n"
# MAGIC     message += "Please choose one of the following options or provide your own clarification:\n"
# MAGIC     for i, option in enumerate(clarification['options'], 1):
# MAGIC         message += f"{i}. {option}\n"
# MAGIC     return message
# MAGIC
# MAGIC def get_reset_state_template() -> Dict[str, Any]:
# MAGIC     """Get template for resetting per-query execution fields."""
# MAGIC     return {
# MAGIC         "pending_clarification": None,
# MAGIC         "question_clear": False,
# MAGIC         "plan": None,
# MAGIC         "sub_questions": None,
# MAGIC         "requires_multiple_spaces": None,
# MAGIC         "relevant_space_ids": None,
# MAGIC         "relevant_spaces": None,
# MAGIC         "vector_search_relevant_spaces_info": None,
# MAGIC         "requires_join": None,
# MAGIC         "join_strategy": None,
# MAGIC         "execution_plan": None,
# MAGIC         "genie_route_plan": None,
# MAGIC         "sql_query": None,
# MAGIC         "sql_synthesis_explanation": None,
# MAGIC         "synthesis_error": None,
# MAGIC         "execution_result": None,
# MAGIC         "execution_error": None,
# MAGIC         "final_summary": None,
# MAGIC     }
# MAGIC
# MAGIC print("✓ Inline TypedDicts defined (no kumc_poc imports)")
# MAGIC
# MAGIC # State Reset Template
# MAGIC # All per-query execution fields that should be cleared for each new query.
# MAGIC # This prevents stale data from persisting across queries when using CheckpointSaver.
# MAGIC # Used by both Model Serving (run_agent) and local testing (invoke_super_agent_hybrid).
# MAGIC # Use the shared reset state template from conversation_models
# MAGIC RESET_STATE_TEMPLATE = get_reset_state_template()
# MAGIC
# MAGIC # NOTE: Turn-based fields (current_turn, turn_history, intent_metadata) are NOT reset
# MAGIC # They are managed by unified_intent_context_clarification_node and persist across queries
# MAGIC
# MAGIC # For reference, the template includes per-query fields (see conversation_models.get_reset_state_template()):
# MAGIC # RESET_STATE_TEMPLATE = {
# MAGIC     # Clarification fields (per-query) - SIMPLIFIED from 7+ legacy fields to 2
# MAGIC     # "pending_clarification": None,
# MAGIC     # "question_clear": False,
# MAGIC     
# MAGIC     # Planning fields (per-query)
# MAGIC     # "plan": None,
# MAGIC     # "sub_questions": None,
# MAGIC     # "requires_multiple_spaces": None,
# MAGIC     # "relevant_space_ids": None,
# MAGIC     # "relevant_spaces": None,
# MAGIC     # "vector_search_relevant_spaces_info": None,
# MAGIC     # "requires_join": None,
# MAGIC     # "join_strategy": None,
# MAGIC     # "execution_plan": None,
# MAGIC     # "genie_route_plan": None,
# MAGIC     
# MAGIC     # SQL fields (per-query)
# MAGIC     # "sql_query": None,
# MAGIC     # "sql_synthesis_explanation": None,
# MAGIC     # "synthesis_error": None,
# MAGIC     
# MAGIC     # Execution fields (per-query)
# MAGIC     # "execution_result": None,
# MAGIC     # "execution_error": None,
# MAGIC     
# MAGIC     # Summary (per-query)
# MAGIC     # "final_summary": None,
# MAGIC # }
# MAGIC
# MAGIC # Fields intentionally NOT in reset template:
# MAGIC # 
# MAGIC # NEW TURN-BASED FIELDS (persist across queries via CheckpointSaver):
# MAGIC # - current_turn: Set by intent_detection_node for each query
# MAGIC # - turn_history: Accumulated by reducer with operator.add, persists across conversation
# MAGIC # - intent_metadata: Set by intent_detection_node for each query
# MAGIC #
# MAGIC # DEPRECATED LEGACY FIELDS (removed from AgentState):
# MAGIC # - clarification_count: Replaced by turn_history with triggered_clarification flag
# MAGIC # - last_clarified_query: Replaced by turn_history with triggered_clarification flag
# MAGIC # - combined_query_context: Replaced by current_turn.context_summary (LLM-generated)
# MAGIC # - clarification_needed (as state field): Replaced by pending_clarification object
# MAGIC # - clarification_options (as state field): Replaced by pending_clarification object
# MAGIC #
# MAGIC # DEPRECATED BUT KEPT FOR BACKWARD COMPATIBILITY:
# MAGIC # - original_query: Kept in AgentState but deprecated. Use messages array instead.
# MAGIC #   This field is still set in initial_state for compatibility with legacy code.
# MAGIC #
# MAGIC # PERSISTENT FIELDS (never reset):
# MAGIC # - messages: Managed by operator.add in AgentState, persists across conversation
# MAGIC # - user_id, thread_id, user_preferences: Identity/context, persists for entire conversation
# MAGIC # - next_agent: Control flow field, managed by nodes and routing logic (not in reset template)
# MAGIC
# MAGIC print("✓ State reset template defined for per-query field clearing")
# MAGIC
# MAGIC class PlanningAgent:
# MAGIC     """
# MAGIC     Agent responsible for query analysis and execution planning.
# MAGIC     
# MAGIC     OOP design with vector search integration.
# MAGIC     """
# MAGIC     
# MAGIC     def __init__(self, llm: Runnable, vector_search_index: str):
# MAGIC         self.llm = llm
# MAGIC         self.vector_search_index = vector_search_index
# MAGIC         self.name = "Planning"
# MAGIC     
# MAGIC     def search_relevant_spaces(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
# MAGIC         """
# MAGIC         Search for relevant Genie spaces using vector search.
# MAGIC         
# MAGIC         Args:
# MAGIC             query: User's question
# MAGIC             num_results: Number of results to return
# MAGIC             
# MAGIC         Returns:
# MAGIC             List of relevant space dictionaries
# MAGIC         """
# MAGIC         vs_tool = VectorSearchRetrieverTool(
# MAGIC             index_name=self.vector_search_index,
# MAGIC             num_results=num_results,
# MAGIC             columns=["space_id", "space_title", "searchable_content"],
# MAGIC             filters={"chunk_type": "space_summary"},
# MAGIC             query_type="ANN",
# MAGIC             include_metadata=True,
# MAGIC             include_score=True
# MAGIC         )
# MAGIC         
# MAGIC         docs = vs_tool.invoke({"query": query})
# MAGIC         
# MAGIC         relevant_spaces = []
# MAGIC         for doc in docs:
# MAGIC             print(doc)
# MAGIC             relevant_spaces.append({
# MAGIC                 "space_id": doc.metadata.get("space_id", ""),
# MAGIC                 "space_title": doc.metadata.get("space_title", ""),
# MAGIC                 "searchable_content": doc.page_content,
# MAGIC                 "score": doc.metadata.get("score", 0.0)
# MAGIC             })
# MAGIC         
# MAGIC         return relevant_spaces
# MAGIC     
# MAGIC     def create_execution_plan(
# MAGIC         self, 
# MAGIC         query: str, 
# MAGIC         relevant_spaces: List[Dict[str, Any]],
# MAGIC         original_query: str = None
# MAGIC     ) -> Dict[str, Any]:
# MAGIC         """
# MAGIC         Create execution plan based on query and relevant spaces.
# MAGIC         
# MAGIC         Args:
# MAGIC             query: User's question (may be context_summary if available)
# MAGIC             relevant_spaces: List of relevant Genie spaces
# MAGIC             original_query: Original user query from this turn (before context enrichment)
# MAGIC             
# MAGIC         Returns:
# MAGIC             Dictionary with execution plan
# MAGIC         """
# MAGIC         # Use original_query if provided, otherwise use query as original
# MAGIC         original_query_display = original_query if original_query is not None else query
# MAGIC         
# MAGIC         planning_prompt = f"""
# MAGIC You are a query planning expert. Analyze the following question and create an execution plan.
# MAGIC
# MAGIC User original query this turn: {original_query_display}
# MAGIC
# MAGIC Question: {query}
# MAGIC
# MAGIC Potentially relevant Genie spaces:
# MAGIC {json.dumps(relevant_spaces, indent=2)}
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
# MAGIC     - For genie_route: Return "genie_route_plan": {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}}
# MAGIC     - For table_route: Return "genie_route_plan": null
# MAGIC     - Each partial_question should be similar to original but scoped to that space
# MAGIC     - Add "Please limit to top 10 rows" to each partial question
# MAGIC
# MAGIC Return your analysis as JSON:
# MAGIC {{
# MAGIC     "original_query": "{query}",
# MAGIC     "vector_search_relevant_spaces_info":{[{sp['space_id']: sp['space_title']} for sp in relevant_spaces]},
# MAGIC     "question_clear": true,
# MAGIC     "sub_questions": ["sub-question 1", "sub-question 2", ...],
# MAGIC     "requires_multiple_spaces": true/false,
# MAGIC     "relevant_space_ids": ["space_id_1", "space_id_2", ...],
# MAGIC     "requires_join": true/false,
# MAGIC     "join_strategy": "table_route" or "genie_route",
# MAGIC     "execution_plan": "Brief description of execution plan",
# MAGIC     "genie_route_plan": {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}} or null
# MAGIC }}
# MAGIC
# MAGIC Only return valid JSON, no explanations.
# MAGIC """
# MAGIC         
# MAGIC         response = self.llm.invoke(planning_prompt)
# MAGIC         content = response.content.strip()
# MAGIC         
# MAGIC         # Use regex to extract JSON from markdown code blocks
# MAGIC         json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
# MAGIC         if json_match:
# MAGIC             json_str = json_match.group(1).strip()
# MAGIC         else:
# MAGIC             # No code blocks, assume entire content is JSON
# MAGIC             json_str = content
# MAGIC         
# MAGIC         # Remove any trailing commas before ] or }
# MAGIC         json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
# MAGIC         
# MAGIC         try:
# MAGIC             plan_result = json.loads(json_str)
# MAGIC             return plan_result
# MAGIC         except json.JSONDecodeError as e:
# MAGIC             print(f"❌ Planning JSON parsing error at position {e.pos}: {e.msg}")
# MAGIC             print(f"Raw content (first 500 chars):\n{content[:500]}")
# MAGIC             print(f"Cleaned JSON (first 500 chars):\n{json_str[:500]}")
# MAGIC             
# MAGIC             # Try one more time with even more aggressive cleaning
# MAGIC             try:
# MAGIC                 # Remove comments
# MAGIC                 json_str_clean = re.sub(r'//.*$', '', json_str, flags=re.MULTILINE)
# MAGIC                 # Remove trailing commas again
# MAGIC                 json_str_clean = re.sub(r',(\s*[}\]])', r'\1', json_str_clean)
# MAGIC                 plan_result = json.loads(json_str_clean)
# MAGIC                 print("✓ Successfully parsed JSON after aggressive cleaning")
# MAGIC                 return plan_result
# MAGIC             except:
# MAGIC                 raise e  # Re-raise original error
# MAGIC     
# MAGIC     def __call__(self, query: str) -> Dict[str, Any]:
# MAGIC         """
# MAGIC         Analyze query and create execution plan.
# MAGIC         
# MAGIC         Returns:
# MAGIC             Complete execution plan with relevant spaces
# MAGIC         """
# MAGIC         # Search for relevant spaces
# MAGIC         relevant_spaces = self.search_relevant_spaces(query)
# MAGIC         
# MAGIC         # Create execution plan
# MAGIC         plan = self.create_execution_plan(query, relevant_spaces)
# MAGIC         
# MAGIC         return plan
# MAGIC
# MAGIC print("✓ PlanningAgent class defined")
# MAGIC class SQLSynthesisTableAgent:
# MAGIC     """
# MAGIC     Agent responsible for fast SQL synthesis using UC function tools.
# MAGIC     
# MAGIC     OOP design with UC toolkit integration.
# MAGIC     """
# MAGIC     
# MAGIC     def __init__(
# MAGIC         self, 
# MAGIC         llm: Runnable, 
# MAGIC         catalog: str, 
# MAGIC         schema: str
# MAGIC     ):
# MAGIC         self.llm = llm
# MAGIC         self.catalog = catalog
# MAGIC         self.schema = schema
# MAGIC         self.name = "SQLSynthesisTable"
# MAGIC         
# MAGIC         # Initialize UC Function Client
# MAGIC         client = DatabricksFunctionClient()
# MAGIC         set_uc_function_client(client)
# MAGIC         
# MAGIC         # Create UC Function Toolkit
# MAGIC         uc_function_names = [
# MAGIC             f"{catalog}.{schema}.get_space_summary",
# MAGIC             f"{catalog}.{schema}.get_table_overview",
# MAGIC             f"{catalog}.{schema}.get_column_detail",
# MAGIC             f"{catalog}.{schema}.get_space_details",
# MAGIC         ]
# MAGIC         
# MAGIC         self.uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
# MAGIC         self.tools = self.uc_toolkit.tools
# MAGIC         
# MAGIC         # Create SQL synthesis agent with tools
# MAGIC         self.agent = create_agent(
# MAGIC             model=llm,
# MAGIC             tools=self.tools,
# MAGIC             system_prompt=(
# MAGIC                 "You are a specialized SQL synthesis agent in a multi-agent system.\n\n"
# MAGIC                 "ROLE: You receive execution plans from the planning agent and generate SQL queries.\n\n"
# MAGIC
# MAGIC                 "## WORKFLOW:\n"
# MAGIC                 "1. Review the execution plan and provided metadata\n"
# MAGIC                 "2. If metadata is sufficient → Generate SQL immediately\n"
# MAGIC                 "3. If insufficient, call UC function tools in this order:\n"
# MAGIC                 "   a) get_space_summary for space information\n"
# MAGIC                 "   b) get_table_overview for table schemas\n"
# MAGIC                 "   c) get_column_detail for specific columns\n"
# MAGIC                 "   d) get_space_details ONLY as last resort (token intensive)\n"
# MAGIC                 "4. At last, if you still cannot find enough metadata in relevant spaces provided, dont stuck there. Expand the searching scope to all spaces mentioned in the execution plan's 'vector_search_relevant_spaces_info' field. Extract the space_id from 'vector_search_relevant_spaces_info'. \n"
# MAGIC                 "5. Generate complete, executable SQL\n\n"
# MAGIC
# MAGIC                 "## UC FUNCTION USAGE:\n"
# MAGIC                 "- Pass arguments as JSON array strings: '[\"space_id_1\", \"space_id_2\"]' or 'null'\n"
# MAGIC                 "- Only query spaces from execution plan's relevant_space_ids\n"
# MAGIC                 "- Use minimal sufficiency: only query what you need\n\n"
# MAGIC
# MAGIC                 "## OUTPUT REQUIREMENTS:\n"
# MAGIC                 "- Generate complete, executable SQL with:\n"
# MAGIC                 "  * Proper JOINs based on execution plan\n"
# MAGIC                 "  * WHERE clauses for filtering\n"
# MAGIC                 "  * Appropriate aggregations\n"
# MAGIC                 "  * Clear column aliases\n"
# MAGIC                 "  * Always use real column names, never make up ones\n"
# MAGIC                 "- Return your response with:\n"
# MAGIC                 "1. Your explanations; If SQL cannot be generated, explain what metadata is missing\n"
# MAGIC                 "2. The final SQL query in a ```sql code block\n\n"
# MAGIC             )
# MAGIC         )
# MAGIC     
# MAGIC     def synthesize_sql(self, plan: Dict[str, Any]) -> Dict[str, Any]:
# MAGIC         """
# MAGIC         Synthesize SQL query based on execution plan.
# MAGIC         
# MAGIC         Args:
# MAGIC             plan: Execution plan from planning agent
# MAGIC             
# MAGIC         Returns:
# MAGIC             Dictionary with:
# MAGIC             - sql: str - Extracted SQL query (None if cannot generate)
# MAGIC             - explanation: str - Agent's explanation/reasoning
# MAGIC             - has_sql: bool - Whether SQL was successfully extracted
# MAGIC         """
# MAGIC         # # Prepare plan summary for agent
# MAGIC         # plan_summary = {
# MAGIC         #     "original_query": plan.get("original_query", ""),
# MAGIC         #     "vector_search_relevant_spaces_info": plan.get("vector_search_relevant_spaces_info", []),
# MAGIC         #     "relevant_space_ids": plan.get("relevant_space_ids", []),
# MAGIC         #     "execution_plan": plan.get("execution_plan", ""),
# MAGIC         #     "requires_join": plan.get("requires_join", False),
# MAGIC         #     "sub_questions": plan.get("sub_questions", [])
# MAGIC         # }
# MAGIC         plan_result = plan
# MAGIC         # Invoke agent
# MAGIC         agent_message = {
# MAGIC             "messages": [
# MAGIC                 {
# MAGIC                     "role": "user",
# MAGIC                     "content": f"""
# MAGIC Generate a SQL query to answer the question according to the Query Plan:
# MAGIC {json.dumps(plan_result, indent=2)}
# MAGIC
# MAGIC Use your available UC function tools to gather metadata intelligently.
# MAGIC """
# MAGIC                 }
# MAGIC             ]
# MAGIC         }
# MAGIC         
# MAGIC         result = self.agent.invoke(agent_message)
# MAGIC         
# MAGIC         # Extract SQL and explanation from response
# MAGIC         if result and "messages" in result:
# MAGIC             final_content = result["messages"][-1].content
# MAGIC             original_content = final_content
# MAGIC             
# MAGIC             sql_query = None
# MAGIC             has_sql = False
# MAGIC             
# MAGIC             # Try to extract SQL from markdown if present
# MAGIC             if "```sql" in final_content.lower():
# MAGIC                 sql_match = re.search(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
# MAGIC                 if sql_match:
# MAGIC                     sql_query = sql_match.group(1).strip()
# MAGIC                     has_sql = True
# MAGIC                     # Remove SQL block from content to get explanation
# MAGIC                     final_content = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL)
# MAGIC             elif "```" in final_content:
# MAGIC                 sql_match = re.search(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
# MAGIC                 if sql_match:
# MAGIC                     # Check if it looks like SQL
# MAGIC                     potential_sql = sql_match.group(1).strip()
# MAGIC                     if any(keyword in potential_sql.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
# MAGIC                         sql_query = potential_sql
# MAGIC                         has_sql = True
# MAGIC                         # Remove SQL block from content to get explanation
# MAGIC                         final_content = re.sub(r'```\s*.*?\s*```', '', final_content, flags=re.DOTALL)
# MAGIC             
# MAGIC             # Clean up explanation
# MAGIC             explanation = final_content.strip()
# MAGIC             if not explanation:
# MAGIC                 explanation = original_content if not has_sql else "SQL query generated successfully."
# MAGIC             
# MAGIC             return {
# MAGIC                 "sql": sql_query,
# MAGIC                 "explanation": explanation,
# MAGIC                 "has_sql": has_sql
# MAGIC             }
# MAGIC         else:
# MAGIC             raise Exception("No response from agent")
# MAGIC     
# MAGIC     def __call__(self, plan: Dict[str, Any]) -> Dict[str, Any]:
# MAGIC         """Make agent callable."""
# MAGIC         return self.synthesize_sql(plan)
# MAGIC
# MAGIC print("✓ SQLSynthesisTableAgent class defined")
# MAGIC class SQLSynthesisGenieAgent:
# MAGIC     """
# MAGIC     Agent responsible for Genie Route SQL synthesis using Genie agents as tools.
# MAGIC     
# MAGIC     EXECUTION MODES:
# MAGIC     ---------------
# MAGIC     1. LangGraph Agent Mode (default via synthesize_sql()):
# MAGIC        - Uses LangGraph agent with tool calling
# MAGIC        - Supports retries, disaster recovery, and adaptive routing
# MAGIC        - Agent decides which tools to call and when
# MAGIC        - Best for complex queries requiring orchestration
# MAGIC     
# MAGIC     2. RunnableParallel Mode (via invoke_genie_agents_parallel()):
# MAGIC        - Uses RunnableParallel for direct parallel execution
# MAGIC        - Faster for simple parallel queries
# MAGIC        - No retry logic or adaptive routing
# MAGIC        - Best for straightforward parallel execution
# MAGIC     
# MAGIC     ARCHITECTURE:
# MAGIC     ------------
# MAGIC     - Upgraded from RunnableLambda to RunnableParallel pattern
# MAGIC     - Each Genie agent is wrapped as both a tool and a parallel executor
# MAGIC     - Supports efficient parallel invocation using LangChain's RunnableParallel
# MAGIC     - Optimized to only create Genie agents for relevant spaces (not all spaces)
# MAGIC     """
# MAGIC     
# MAGIC     def __init__(self, llm: Runnable, relevant_spaces: List[Dict[str, Any]]):
# MAGIC         """
# MAGIC         Initialize SQL Synthesis Genie Agent with tool-calling pattern.
# MAGIC         
# MAGIC         Args:
# MAGIC             llm: Language model for SQL synthesis
# MAGIC             relevant_spaces: List of relevant spaces from PlanningAgent's Vector Search.
# MAGIC                             Each dict should have: space_id, space_title, searchable_content
# MAGIC         """
# MAGIC         self.llm = llm
# MAGIC         self.relevant_spaces = relevant_spaces
# MAGIC         self.name = "SQLSynthesisGenie"
# MAGIC         
# MAGIC         # Create Genie agents and their tool representations
# MAGIC         self.genie_agents = []
# MAGIC         self.genie_agent_tools = []
# MAGIC         self._create_genie_agent_tools()
# MAGIC         
# MAGIC         # Create SQL synthesis agent with Genie agent tools
# MAGIC         self.sql_synthesis_agent = self._create_sql_synthesis_agent()
# MAGIC     
# MAGIC     def _create_genie_agent_tools(self):
# MAGIC         """
# MAGIC         Create Genie agents as tools only for relevant spaces.
# MAGIC         
# MAGIC         Creates both:
# MAGIC         1. Individual tool wrappers for LangGraph agent tool calling
# MAGIC         2. A parallel executor mapping for efficient batch invocation
# MAGIC         
# MAGIC         Uses LangChain preferred syntax with Pydantic BaseModel and StructuredTool.
# MAGIC         """
# MAGIC         def enforce_limit(messages, n=5):
# MAGIC             last = messages[-1] if messages else {"content": ""}
# MAGIC             content = last.get("content", "") if isinstance(last, dict) else last.content
# MAGIC             return f"{content}\n\nPlease limit the result to at most {n} rows."
# MAGIC         
# MAGIC         print(f"  Creating Genie agent tools for {len(self.relevant_spaces)} relevant spaces...")
# MAGIC         
# MAGIC         for space in self.relevant_spaces:
# MAGIC             space_id = space.get("space_id")
# MAGIC             space_title = space.get("space_title", space_id)
# MAGIC             searchable_content = space.get("searchable_content", "")
# MAGIC             
# MAGIC             if not space_id:
# MAGIC                 print(f"  ⚠ Warning: Space missing space_id, skipping: {space}")
# MAGIC                 continue
# MAGIC             
# MAGIC             genie_agent_name = f"Genie_{space_title}"
# MAGIC             description = searchable_content
# MAGIC             
# MAGIC             # Create Genie agent with message_processor
# MAGIC             genie_agent = GenieAgent(
# MAGIC                 genie_space_id=space_id,
# MAGIC                 genie_agent_name=genie_agent_name,
# MAGIC                 description=description,
# MAGIC                 include_context=True,
# MAGIC                 message_processor=lambda msgs: enforce_limit(msgs, n=5)
# MAGIC             )
# MAGIC             self.genie_agents.append(genie_agent)
# MAGIC             
# MAGIC             # Define tool input schema using Pydantic
# MAGIC             class GenieToolInput(BaseModel):
# MAGIC                 question: str = Field(..., description="Natural-language query to run in the Genie Space")
# MAGIC                 conversation_id: Optional[str] = Field(None, description="Optional Genie conversation for continuity")
# MAGIC             
# MAGIC             # Create tool function using factory pattern to capture agent
# MAGIC             def make_genie_tool_call(agent):
# MAGIC                 """Factory function to capture agent in closure properly"""
# MAGIC                 def _genie_tool_call(question: str, conversation_id: Optional[str] = None):
# MAGIC                     """
# MAGIC                     StructuredTool with args_schema expects individual field arguments,
# MAGIC                     not a single Pydantic object.
# MAGIC                     """
# MAGIC                     # GenieAgent expects a LangChain-style message list
# MAGIC                     result = agent.invoke({
# MAGIC                         "messages": [{"role": "user", "content": question}],
# MAGIC                         "conversation_id": conversation_id,
# MAGIC                     })
# MAGIC                     # Extract final output + optional context
# MAGIC                     out = {"conversation_id": result.get("conversation_id")}
# MAGIC                     msgs = result["messages"]
# MAGIC                     def _get(name): 
# MAGIC                         return next((getattr(m, "content", "") for m in msgs if getattr(m, "name", None) == name), None)
# MAGIC                     out["answer"] = _get("query_result") or ""
# MAGIC                     reasoning = _get("query_reasoning")
# MAGIC                     sql = _get("query_sql")
# MAGIC                     if reasoning: out["reasoning"] = reasoning
# MAGIC                     if sql: out["sql"] = sql
# MAGIC                     return out
# MAGIC                 return _genie_tool_call
# MAGIC             
# MAGIC             # Create StructuredTool
# MAGIC             genie_tool = StructuredTool(
# MAGIC                 name=genie_agent_name,
# MAGIC                 description=(
# MAGIC                     f"Use for governed analytics queries (NL→SQL) in {space_title}. "
# MAGIC                     f"{description}. "
# MAGIC                     "Returns an answer and, when available, the generated SQL and reasoning."
# MAGIC                 ),
# MAGIC                 args_schema=GenieToolInput,
# MAGIC                 func=make_genie_tool_call(genie_agent),
# MAGIC             )
# MAGIC             self.genie_agent_tools.append(genie_tool)
# MAGIC             
# MAGIC             print(f"  ✓ Created Genie agent tool: {genie_agent_name} ({space_id})")
# MAGIC     
# MAGIC     def _create_parallel_execution_tool(self):
# MAGIC         """
# MAGIC         Create a tool that allows the agent to invoke multiple Genie agents in parallel.
# MAGIC         
# MAGIC         This tool gives the agent control over parallel execution with the same
# MAGIC         disaster recovery capabilities as individual tool calls.
# MAGIC         
# MAGIC         Uses RunnableParallel pattern with StructuredTool for type safety.
# MAGIC         """
# MAGIC
# MAGIC         
# MAGIC         # Define input schema for parallel execution
# MAGIC         class ParallelGenieInput(BaseModel):
# MAGIC             genie_route_plan: Dict[str, str] = Field(
# MAGIC                 ..., 
# MAGIC                 description="Dictionary mapping space_id to question. Example: {'space_id_1': 'Get member demographics', 'space_id_2': 'Get benefits'}"
# MAGIC             )
# MAGIC         
# MAGIC         # Merge function to combine outputs from multiple Genie agents
# MAGIC         def merge_genie_outputs(outputs: Dict[str, Any]) -> Dict[str, Any]:
# MAGIC             """
# MAGIC             Merge outputs from multiple Genie agents into a unified result.
# MAGIC             
# MAGIC             Args:
# MAGIC                 outputs: Dictionary keyed by space_id, each containing agent results
# MAGIC             
# MAGIC             Returns:
# MAGIC                 Unified dictionary with extracted SQL, reasoning, and metadata from all agents
# MAGIC             """
# MAGIC             merged_results = {}
# MAGIC             
# MAGIC             for space_id, result in outputs.items():
# MAGIC                 extracted = {
# MAGIC                     "space_id": space_id,
# MAGIC                     "question": outputs.get(f"{space_id}_question", ""),
# MAGIC                     "sql": "",
# MAGIC                     "reasoning": "",
# MAGIC                     "answer": "",
# MAGIC                     "conversation_id": "",
# MAGIC                     "success": False
# MAGIC                 }
# MAGIC                 
# MAGIC                 # Handle direct dict output from StructuredTool
# MAGIC                 if isinstance(result, dict):
# MAGIC                     extracted["answer"] = result.get("answer", "")
# MAGIC                     extracted["sql"] = result.get("sql", "")
# MAGIC                     extracted["reasoning"] = result.get("reasoning", "")
# MAGIC                     extracted["conversation_id"] = result.get("conversation_id", "")
# MAGIC                     extracted["success"] = bool(result.get("sql") or result.get("answer"))
# MAGIC                 
# MAGIC                 # Handle message-based output (fallback)
# MAGIC                 elif isinstance(result, dict) and "messages" in result:
# MAGIC                     messages = result.get("messages", [])
# MAGIC                     
# MAGIC                     # Extract reasoning (query_reasoning)
# MAGIC                     for msg in messages:
# MAGIC                         if hasattr(msg, 'name') and msg.name == 'query_reasoning':
# MAGIC                             extracted["reasoning"] = msg.content if hasattr(msg, 'content') else ""
# MAGIC                             break
# MAGIC                     
# MAGIC                     # Extract SQL (query_sql)
# MAGIC                     for msg in messages:
# MAGIC                         if hasattr(msg, 'name') and msg.name == 'query_sql':
# MAGIC                             extracted["sql"] = msg.content if hasattr(msg, 'content') else ""
# MAGIC                             extracted["success"] = True
# MAGIC                             break
# MAGIC                     
# MAGIC                     # Extract answer (query_result)
# MAGIC                     for msg in messages:
# MAGIC                         if hasattr(msg, 'name') and msg.name == 'query_result':
# MAGIC                             extracted["answer"] = msg.content if hasattr(msg, 'content') else ""
# MAGIC                             break
# MAGIC                     
# MAGIC                     # Extract conversation_id
# MAGIC                     extracted["conversation_id"] = result.get("conversation_id", "")
# MAGIC                 
# MAGIC                 merged_results[space_id] = extracted
# MAGIC             
# MAGIC             return merged_results
# MAGIC         
# MAGIC         # Build a mapping from space_id to tool for easy lookup
# MAGIC         space_id_to_tool = {}
# MAGIC         for space in self.relevant_spaces:
# MAGIC             space_id = space.get("space_id")
# MAGIC             if space_id:
# MAGIC                 # Find the corresponding tool by matching space_id
# MAGIC                 for tool in self.genie_agent_tools:
# MAGIC                     # Match tool to space by checking if space_title is in tool name
# MAGIC                     space_title = space.get("space_title", space_id)
# MAGIC                     if f"Genie_{space_title}" == tool.name:
# MAGIC                         space_id_to_tool[space_id] = tool
# MAGIC                         break
# MAGIC         
# MAGIC         # Tool function that builds and invokes dynamic parallel execution
# MAGIC         def invoke_parallel_genie_agents(genie_route_plan: Dict[str, str]) -> Dict[str, Any]:
# MAGIC             """
# MAGIC             Invoke multiple Genie agents in parallel for efficient SQL generation.
# MAGIC             
# MAGIC             StructuredTool with args_schema expects individual field arguments,
# MAGIC             not a single Pydantic object.
# MAGIC             
# MAGIC             Args:
# MAGIC                 genie_route_plan: Dictionary mapping space_id to question
# MAGIC             
# MAGIC             Returns:
# MAGIC                 Dictionary with results from each Genie agent, keyed by space_id.
# MAGIC                 Each result contains the SQL query, reasoning, and answer from that agent.
# MAGIC             """
# MAGIC             try:
# MAGIC                 route_plan = genie_route_plan
# MAGIC                 
# MAGIC                 # Validate all requested space_ids exist
# MAGIC                 for space_id in route_plan.keys():
# MAGIC                     if space_id not in space_id_to_tool:
# MAGIC                         return {
# MAGIC                             "error": f"No tool found for space_id: {space_id}",
# MAGIC                             "available_space_ids": list(space_id_to_tool.keys())
# MAGIC                         }
# MAGIC                 
# MAGIC                 if not route_plan:
# MAGIC                     return {"error": "No valid parallel tasks to execute"}
# MAGIC                 
# MAGIC                 # Build dynamic parallel tasks - each task invokes the corresponding tool's func
# MAGIC                 # Call the underlying function directly with individual arguments
# MAGIC                 parallel_tasks = {}
# MAGIC                 for space_id, question in route_plan.items():
# MAGIC                     tool = space_id_to_tool[space_id]
# MAGIC                     # Create a lambda that calls the tool's func with individual kwargs
# MAGIC                     # Use default argument to capture values properly in closure
# MAGIC                     parallel_tasks[space_id] = RunnableLambda(
# MAGIC                         lambda inp, sid=space_id, t=tool: t.func(
# MAGIC                             question=inp[sid], conversation_id=None
# MAGIC                         )
# MAGIC                     )
# MAGIC                 
# MAGIC                 # Create parallel runner and compose with merge function
# MAGIC                 parallel = RunnableParallel(**parallel_tasks)
# MAGIC                 composed = parallel | RunnableLambda(merge_genie_outputs)
# MAGIC                 
# MAGIC                 # Invoke the composed chain
# MAGIC                 results = composed.invoke(route_plan)
# MAGIC                 
# MAGIC                 return results
# MAGIC                 
# MAGIC             except Exception as e:
# MAGIC                 return {"error": f"Parallel execution failed: {str(e)}"}
# MAGIC         
# MAGIC         # Create StructuredTool with proper schema
# MAGIC         parallel_tool = StructuredTool(
# MAGIC             name="invoke_parallel_genie_agents",
# MAGIC             description=(
# MAGIC                 "Invoke multiple Genie agents in PARALLEL for fast SQL generation. "
# MAGIC                 "Input: Dictionary mapping space_id to question. "
# MAGIC                 "Example: {'space_01j9t0jhx009k25rvp67y1k7j0': 'Get member demographics', 'space_01j9t0jhx009k25rvp67y1k7j1': 'Get benefit costs'}. "
# MAGIC                 "Returns: Dictionary with SQL, reasoning, and answer from each agent. "
# MAGIC                 "Use this tool when: "
# MAGIC                 "(1) You need to query multiple Genie spaces simultaneously, "
# MAGIC                 "(2) The queries are independent (no dependencies between them), "
# MAGIC                 "(3) You want faster execution than calling each agent sequentially. "
# MAGIC                 "After getting results, check if you have all needed SQL components. If missing information, you can: "
# MAGIC                 "call this tool again with updated questions, or call individual Genie agent tools for specific missing pieces."
# MAGIC             ),
# MAGIC             args_schema=ParallelGenieInput,
# MAGIC             func=invoke_parallel_genie_agents,
# MAGIC         )
# MAGIC         
# MAGIC         return parallel_tool
# MAGIC     
# MAGIC     def _create_sql_synthesis_agent(self):
# MAGIC         """
# MAGIC         Create LangGraph SQL Synthesis Agent with Genie agent tools.
# MAGIC         
# MAGIC         Uses Databricks LangGraph SDK with create_agent pattern.
# MAGIC         Includes both individual Genie agent tools AND a parallel execution tool.
# MAGIC         """
# MAGIC         tools = []
# MAGIC         tools.extend(self.genie_agent_tools)
# MAGIC         
# MAGIC         # Add parallel execution tool
# MAGIC         parallel_tool = self._create_parallel_execution_tool()
# MAGIC         tools.append(parallel_tool)
# MAGIC         
# MAGIC         print(f"✓ Created SQL Synthesis Agent with {len(self.genie_agent_tools)} Genie agent tools + 1 parallel execution tool")
# MAGIC         
# MAGIC         # Create SQL Synthesis Agent (specialized for multi-agent system)
# MAGIC         sql_synthesis_agent = create_agent(
# MAGIC             model=self.llm,
# MAGIC             tools=tools,
# MAGIC             system_prompt=(
# MAGIC """You are a SQL synthesis agent with access to both INDIVIDUAL and PARALLEL Genie agent execution tools.
# MAGIC
# MAGIC The Plan given to you is a JSON:
# MAGIC {
# MAGIC 'original_query': 'The User's Question',
# MAGIC 'vector_search_relevant_spaces_info': [{'space_id': 'space_id_1', 'space_title': 'space_title_1'}, ...],
# MAGIC "question_clear": true,
# MAGIC "sub_questions": ["sub-question 1", "sub-question 2", ...],
# MAGIC "requires_multiple_spaces": true/false,
# MAGIC "relevant_space_ids": ["space_id_1", "space_id_2", ...],
# MAGIC "requires_join": true/false,
# MAGIC "join_strategy": "table_route" or "genie_route" or null,
# MAGIC "execution_plan": "Brief description of execution plan",
# MAGIC "genie_route_plan": {'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', ...} or null,}
# MAGIC
# MAGIC ## TOOL EXECUTION STRATEGY:
# MAGIC
# MAGIC ### OPTION 1: PARALLEL EXECUTION (Recommended for Speed)
# MAGIC Use the `invoke_parallel_genie_agents` tool to query multiple Genie spaces simultaneously.
# MAGIC
# MAGIC 1. Extract the genie_route_plan from the input JSON
# MAGIC 2. Convert it to a JSON string: '{"space_id_1": "question1", "space_id_2": "question2"}'
# MAGIC 3. Call: invoke_parallel_genie_agents(genie_route_plan='{"space_id_1": "question1", ...}')
# MAGIC 4. You'll receive JSON with SQL and thinking from ALL agents at once
# MAGIC 5. Check if you have all needed SQL components
# MAGIC 6. If missing information:
# MAGIC    - Reframe questions and call invoke_parallel_genie_agents again with updated questions
# MAGIC    - OR call specific individual Genie agent tools for missing pieces
# MAGIC
# MAGIC ### OPTION 2: SEQUENTIAL EXECUTION (Use for Dependencies)
# MAGIC Call individual Genie agent tools one by one when:
# MAGIC - One query depends on results from another
# MAGIC - You need more control over error handling for specific agents
# MAGIC - You want to adaptively query based on previous results
# MAGIC
# MAGIC ## DISASTER RECOVERY (DR) - WORKS FOR BOTH PARALLEL AND SEQUENTIAL:
# MAGIC
# MAGIC 1. **First Attempt**: Try your query AS IS
# MAGIC 2. **If fails**: Analyze the error message
# MAGIC    - If agent says "I don't have information for X", remove X from the question
# MAGIC    - If agent returns empty/incomplete SQL, try rephrasing the question
# MAGIC 3. **Retry Once**: Call the same tool with updated question(s)
# MAGIC 4. **If still fails**: Try alternative Genie agents that might have the information
# MAGIC 5. **Final fallback**: Work with what you have and explain limitations
# MAGIC
# MAGIC ## EXAMPLE PARALLEL EXECUTION WITH DR:
# MAGIC
# MAGIC Step 1: Call invoke_parallel_genie_agents with initial questions
# MAGIC Step 2: Check results - if space_1 succeeded but space_2 failed
# MAGIC Step 3: Keep space_1 SQL, retry space_2 with reframed question using invoke_parallel_genie_agents
# MAGIC Step 4: Combine all successful SQL fragments
# MAGIC
# MAGIC ## SQL SYNTHESIS:
# MAGIC Combine all SQL fragments into a single query.
# MAGIC
# MAGIC OUTPUT REQUIREMENTS:
# MAGIC - Generate complete, executable SQL with:
# MAGIC   * Proper JOINs based on execution plan strategy
# MAGIC   * WHERE clauses for filtering  
# MAGIC   * Appropriate aggregations
# MAGIC   * Clear column aliases
# MAGIC   * Always use real column names from the data
# MAGIC - Return your response with:
# MAGIC   1. Your explanation (including which execution strategy you used)
# MAGIC   2. The final SQL query in a ```sql code block"""
# MAGIC             )
# MAGIC         )
# MAGIC         
# MAGIC         return sql_synthesis_agent
# MAGIC     
# MAGIC     def invoke_genie_agents_parallel(self, genie_route_plan: Dict[str, str]) -> Dict[str, Any]:
# MAGIC         """
# MAGIC         Invoke multiple Genie agents in parallel using RunnableParallel.
# MAGIC         
# MAGIC         This method demonstrates the proper use of RunnableParallel for efficient
# MAGIC         parallel execution of multiple Genie agents simultaneously.
# MAGIC         
# MAGIC         Args:
# MAGIC             genie_route_plan: Dictionary mapping space_id to partial_question
# MAGIC                 Example: {
# MAGIC                     "space_01j9t0jhx009k25rvp67y1k7j0": "Get member demographics",
# MAGIC                     "space_01j9t0jhx009k25rvp67y1k7j1": "Get benefit costs"
# MAGIC                 }
# MAGIC         
# MAGIC         Returns:
# MAGIC             Dictionary mapping space_id to agent response
# MAGIC             Example: {
# MAGIC                 "space_01j9t0jhx009k25rvp67y1k7j0": {...response...},
# MAGIC                 "space_01j9t0jhx009k25rvp67y1k7j1": {...response...}
# MAGIC             }
# MAGIC         """
# MAGIC         if not genie_route_plan:
# MAGIC             return {}
# MAGIC         
# MAGIC         # Build space_id to tool mapping
# MAGIC         space_id_to_tool = {}
# MAGIC         for space in self.relevant_spaces:
# MAGIC             space_id = space.get("space_id")
# MAGIC             if space_id and space_id in genie_route_plan:
# MAGIC                 # Find the corresponding tool by matching space_id
# MAGIC                 for tool in self.genie_agent_tools:
# MAGIC                     space_title = space.get("space_title", space_id)
# MAGIC                     if f"Genie_{space_title}" == tool.name:
# MAGIC                         space_id_to_tool[space_id] = tool
# MAGIC                         break
# MAGIC         
# MAGIC         # Build parallel tasks that call tool.func() directly with individual arguments
# MAGIC         parallel_tasks = {}
# MAGIC         for space_id, question in genie_route_plan.items():
# MAGIC             if space_id in space_id_to_tool:
# MAGIC                 tool = space_id_to_tool[space_id]
# MAGIC                 parallel_tasks[space_id] = RunnableLambda(
# MAGIC                     lambda inp, sid=space_id, t=tool: t.func(
# MAGIC                         question=inp[sid], conversation_id=None
# MAGIC                     )
# MAGIC                 )
# MAGIC             else:
# MAGIC                 print(f"  ⚠ Warning: No tool found for space_id: {space_id}")
# MAGIC         
# MAGIC         if not parallel_tasks:
# MAGIC             print("  ⚠ Warning: No valid parallel tasks to execute")
# MAGIC             return {}
# MAGIC         
# MAGIC         # Create RunnableParallel with all tasks
# MAGIC         parallel_runner = RunnableParallel(**parallel_tasks)
# MAGIC         
# MAGIC         print(f"  🚀 Invoking {len(parallel_tasks)} Genie agents in parallel using RunnableParallel...")
# MAGIC         
# MAGIC         try:
# MAGIC             # Invoke all agents in parallel
# MAGIC             # Now invoke with the actual question mapping
# MAGIC             results = parallel_runner.invoke(genie_route_plan)
# MAGIC             
# MAGIC             print(f"  ✅ Parallel invocation completed for {len(results)} agents")
# MAGIC             print(results)
# MAGIC             return results
# MAGIC             
# MAGIC         except Exception as e:
# MAGIC             print(f"  ❌ Parallel invocation failed: {str(e)}")
# MAGIC             return {}
# MAGIC     
# MAGIC     def synthesize_sql(
# MAGIC         self, 
# MAGIC         plan: Dict[str, Any]
# MAGIC     ) -> Dict[str, Any]:
# MAGIC         """
# MAGIC         Synthesize SQL using Genie agents with intelligent tool selection.
# MAGIC         
# MAGIC         The agent has access to:
# MAGIC         1. invoke_parallel_genie_agents tool - For fast parallel execution
# MAGIC         2. Individual Genie agent tools - For sequential/dependent queries
# MAGIC         
# MAGIC         The agent autonomously decides which strategy to use and handles
# MAGIC         disaster recovery with retry logic for both parallel and sequential execution
# MAGIC         
# MAGIC         Args:
# MAGIC             plan: Complete plan dictionary from PlanningAgent containing:
# MAGIC                 - original_query: Original user question
# MAGIC                 - execution_plan: Execution plan description
# MAGIC                 - genie_route_plan: Mapping of space_id to partial question
# MAGIC                 - vector_search_relevant_spaces_info: List of relevant spaces
# MAGIC                 - relevant_space_ids: List of relevant space IDs
# MAGIC                 - requires_join: Whether join is needed
# MAGIC                 - join_strategy: Join strategy (table_route/genie_route)
# MAGIC             
# MAGIC         Returns:
# MAGIC             Dictionary with:
# MAGIC             - sql: str - Combined SQL query (None if cannot generate)
# MAGIC             - explanation: str - Agent's explanation/reasoning
# MAGIC             - has_sql: bool - Whether SQL was successfully extracted
# MAGIC         """
# MAGIC         # Build the plan result JSON for the agent
# MAGIC         plan_result = plan
# MAGIC         
# MAGIC         print(f"\n{'='*80}")
# MAGIC         print("🤖 SQL Synthesis Agent - Starting (with parallel execution tool)...")
# MAGIC         print(f"{'='*80}")
# MAGIC         print(f"Plan: {json.dumps(plan_result, indent=2)}")
# MAGIC         print(f"{'='*80}\n")
# MAGIC         
# MAGIC         # Create the message for the agent
# MAGIC         # The agent will autonomously decide whether to use:
# MAGIC         # 1. invoke_parallel_genie_agents tool (fast parallel execution)
# MAGIC         # 2. Individual Genie agent tools (sequential execution)
# MAGIC         # 3. A combination of both strategies
# MAGIC         agent_message = {
# MAGIC             "messages": [
# MAGIC                 {
# MAGIC                     "role": "user",
# MAGIC                     "content": f"""
# MAGIC Generate a SQL query to answer the question according to the Query Plan:
# MAGIC {json.dumps(plan_result, indent=2)}
# MAGIC
# MAGIC RECOMMENDED APPROACH:
# MAGIC If 'genie_route_plan' is provided with multiple spaces, consider using the invoke_parallel_genie_agents tool for faster execution.
# MAGIC Convert the genie_route_plan to a JSON string and call the tool to get all SQL fragments in parallel.
# MAGIC Then combine them into a final SQL query.
# MAGIC """
# MAGIC                 }
# MAGIC             ]
# MAGIC         }
# MAGIC         
# MAGIC         try:
# MAGIC             # MLflow autologging is enabled globally at agent initialization
# MAGIC             # No need to call it again here to avoid context issues
# MAGIC             
# MAGIC             # Invoke the agent
# MAGIC             result = self.sql_synthesis_agent.invoke(agent_message)
# MAGIC             
# MAGIC             # Extract SQL from agent result
# MAGIC             # The agent returns {"messages": [...]}
# MAGIC             # Last message contains the final response
# MAGIC             final_message = result["messages"][-1]
# MAGIC             final_content = final_message.content.strip()
# MAGIC             
# MAGIC             print(f"\n{'='*80}")
# MAGIC             print("✅ SQL Synthesis Agent completed")
# MAGIC             print(f"{'='*80}")
# MAGIC             print(f"Result: {final_content[:500]}...")
# MAGIC             print(f"{'='*80}\n")
# MAGIC             
# MAGIC             # Extract SQL and explanation from the result
# MAGIC             sql_query = None
# MAGIC             has_sql = False
# MAGIC             explanation = final_content
# MAGIC             
# MAGIC             # Clean markdown if present and extract SQL
# MAGIC             if "```sql" in final_content.lower():
# MAGIC                 sql_match = re.search(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
# MAGIC                 if sql_match:
# MAGIC                     sql_query = sql_match.group(1).strip()
# MAGIC                     has_sql = True
# MAGIC                     # Remove SQL block to get explanation
# MAGIC                     explanation = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL)
# MAGIC             elif "```" in final_content:
# MAGIC                 sql_match = re.search(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
# MAGIC                 if sql_match:
# MAGIC                     potential_sql = sql_match.group(1).strip()
# MAGIC                     if any(keyword in potential_sql.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
# MAGIC                         sql_query = potential_sql
# MAGIC                         has_sql = True
# MAGIC                         # Remove SQL block to get explanation
# MAGIC                         explanation = re.sub(r'```\s*.*?\s*```', '', final_content, flags=re.DOTALL)
# MAGIC             else:
# MAGIC                 # No markdown, check if the entire content is SQL
# MAGIC                 if any(keyword in final_content.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
# MAGIC                     sql_query = final_content
# MAGIC                     has_sql = True
# MAGIC                     explanation = "SQL query generated successfully by Genie agent tools."
# MAGIC             
# MAGIC             explanation = explanation.strip()
# MAGIC             if not explanation:
# MAGIC                 explanation = final_content if not has_sql else "SQL query generated successfully by Genie agent tools."
# MAGIC             
# MAGIC             return {
# MAGIC                 "sql": sql_query,
# MAGIC                 "explanation": explanation,
# MAGIC                 "has_sql": has_sql
# MAGIC             }
# MAGIC             
# MAGIC         except Exception as e:
# MAGIC             print(f"\n{'='*80}")
# MAGIC             print("❌ SQL Synthesis Agent failed")
# MAGIC             print(f"{'='*80}")
# MAGIC             print(f"Error: {str(e)}")
# MAGIC             print(f"{'='*80}\n")
# MAGIC             
# MAGIC             return {
# MAGIC                 "sql": None,
# MAGIC                 "explanation": f"SQL synthesis failed: {str(e)}",
# MAGIC                 "has_sql": False
# MAGIC             }
# MAGIC     
# MAGIC     def __call__(
# MAGIC         self, 
# MAGIC         plan: Dict[str, Any]
# MAGIC     ) -> Dict[str, Any]:
# MAGIC         """Make agent callable with plan dictionary."""
# MAGIC         return self.synthesize_sql(plan)
# MAGIC
# MAGIC print("✓ SQLSynthesisGenieAgent class defined")
# MAGIC class SQLExecutionAgent:
# MAGIC     """
# MAGIC     Agent responsible for executing SQL queries using Databricks SQL Warehouse.
# MAGIC     
# MAGIC     PRODUCTION-READY DESIGN:
# MAGIC     - Uses databricks-sql-connector with unified authentication (Config + credentials_provider)
# MAGIC     - Automatically handles OAuth credentials when deployed with registered resources
# MAGIC     - Supports both development (notebook) and production (Model Serving) environments
# MAGIC     
# MAGIC     AUTHENTICATION WITH AUTOMATIC PASSTHROUGH:
# MAGIC     When you register resources during agent deployment:
# MAGIC     
# MAGIC         resources = [
# MAGIC             DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
# MAGIC             # ... other resources
# MAGIC         ]
# MAGIC         mlflow.langchain.log_model(..., resources=resources)
# MAGIC     
# MAGIC     Databricks automatically:
# MAGIC     1. Creates a service principal for your agent
# MAGIC     2. Manages OAuth token generation and rotation
# MAGIC     3. Injects credentials into the Model Serving environment
# MAGIC     
# MAGIC     The Config() class automatically reads workspace host and injected OAuth credentials,
# MAGIC     eliminating the need for manual DATABRICKS_HOST/DATABRICKS_TOKEN configuration.
# MAGIC     
# MAGIC     Reference: https://docs.databricks.com/generative-ai/agent-framework/agent-authentication
# MAGIC     
# MAGIC     LEGACY MANUAL AUTHENTICATION (if not using automatic passthrough):
# MAGIC     If you're not using resource registration, you can still manually configure:
# MAGIC     - DATABRICKS_HOST and DATABRICKS_TOKEN via Model Serving environment variables
# MAGIC     - Config() will still read them from the environment
# MAGIC     """
# MAGIC     
# MAGIC     def __init__(self, warehouse_id: str):
# MAGIC         """
# MAGIC         Initialize SQL Execution Agent.
# MAGIC         
# MAGIC         Args:
# MAGIC             warehouse_id: Databricks SQL Warehouse ID for query execution
# MAGIC         """
# MAGIC         self.name = "SQLExecution"
# MAGIC         self.warehouse_id = warehouse_id
# MAGIC     
# MAGIC     def execute_sql(
# MAGIC         self, 
# MAGIC         sql_query: str, 
# MAGIC         max_rows: int = 100,
# MAGIC         return_format: str = "dict"
# MAGIC     ) -> Dict[str, Any]:
# MAGIC         """
# MAGIC         Execute SQL query using Databricks SQL Warehouse and return formatted results.
# MAGIC         
# MAGIC         PRODUCTION BEST PRACTICES IMPLEMENTED:
# MAGIC         1. Context Managers: Uses 'with' statements for automatic resource cleanup
# MAGIC         2. Connection Resilience: Configures timeouts and retry logic for transient failures
# MAGIC         3. Proper Error Handling: Categorizes errors for better production debugging
# MAGIC         4. ANSI SQL Mode: Ensures consistent SQL behavior across environments
# MAGIC         5. Model Serving Compatible: Works without Spark session via REST API
# MAGIC         
# MAGIC         Connection Configuration:
# MAGIC         - Socket timeout: 900s (balances Model Serving 297s limit with warehouse query time)
# MAGIC         - HTTP retries: 30 attempts with exponential backoff (1-60s)
# MAGIC         - Session config: ANSI mode enabled for SQL compliance
# MAGIC         
# MAGIC         Args:
# MAGIC             sql_query: Support two types: 
# MAGIC                 1) The result from invoke the SQL synthesis agent (dict with messages)
# MAGIC                 2) The SQL query string (can be raw SQL or contain markdown code blocks)
# MAGIC             max_rows: Maximum number of rows to return (default: 100)
# MAGIC             return_format: Format of the result - "dict", "json", or "markdown"
# MAGIC             
# MAGIC         Returns:
# MAGIC             Dictionary containing:
# MAGIC             - success: bool - Whether execution was successful
# MAGIC             - sql: str - The executed SQL query
# MAGIC             - result: Any - Query results in requested format
# MAGIC             - row_count: int - Number of rows returned
# MAGIC             - columns: List[str] - Column names
# MAGIC             - error: str - Error message if failed (optional)
# MAGIC             - error_type: str - Exception type for debugging (only on failure)
# MAGIC             - error_hint: str - Suggested resolution (only on failure)
# MAGIC         """
# MAGIC         from databricks import sql
# MAGIC         from databricks.sdk.core import Config
# MAGIC         import json
# MAGIC         
# MAGIC         # Step 1: Extract SQL from agent result or markdown code blocks if present
# MAGIC         if sql_query and isinstance(sql_query, dict) and "messages" in sql_query:
# MAGIC             sql_query = sql_query["messages"][-1].content
# MAGIC         
# MAGIC         extracted_sql = sql_query.strip()
# MAGIC         
# MAGIC         if "```sql" in extracted_sql.lower():
# MAGIC             # Extract content between ```sql and ```
# MAGIC             sql_match = re.search(r'```sql\s*(.*?)\s*```', extracted_sql, re.IGNORECASE | re.DOTALL)
# MAGIC             if sql_match:
# MAGIC                 extracted_sql = sql_match.group(1).strip()
# MAGIC         elif "```" in extracted_sql:
# MAGIC             # Extract any code block
# MAGIC             sql_match = re.search(r'```\s*(.*?)\s*```', extracted_sql, re.DOTALL)
# MAGIC             if sql_match:
# MAGIC                 extracted_sql = sql_match.group(1).strip()
# MAGIC         
# MAGIC         # Step 2: Enforce LIMIT clause (for safety and token management)
# MAGIC         # Always enforce max_rows limit, even if query already has LIMIT
# MAGIC         limit_pattern = re.search(r'\bLIMIT\s+(\d+)\b', extracted_sql, re.IGNORECASE)
# MAGIC         if limit_pattern:
# MAGIC             existing_limit = int(limit_pattern.group(1))
# MAGIC             if existing_limit > max_rows:
# MAGIC                 # Replace existing LIMIT with max_rows if it exceeds the limit
# MAGIC                 extracted_sql = re.sub(
# MAGIC                     r'\bLIMIT\s+\d+\b', 
# MAGIC                     f'LIMIT {max_rows}', 
# MAGIC                     extracted_sql, 
# MAGIC                     flags=re.IGNORECASE
# MAGIC                 )
# MAGIC                 print(f"⚠️  Reduced LIMIT from {existing_limit} to {max_rows} (max_rows enforcement)")
# MAGIC         else:
# MAGIC             # Add LIMIT if not present
# MAGIC             extracted_sql = f"{extracted_sql.rstrip(';')} LIMIT {max_rows}"
# MAGIC         
# MAGIC         try:
# MAGIC             # Step 3: Initialize Databricks Config for unified authentication
# MAGIC             # BEST PRACTICE: Config() automatically reads workspace host and OAuth credentials
# MAGIC             # - In Model Serving with automatic passthrough: reads injected service principal credentials
# MAGIC             # - In notebooks: reads from notebook context or environment variables
# MAGIC             # - With manual config: reads DATABRICKS_HOST and DATABRICKS_TOKEN from environment
# MAGIC             cfg = Config()
# MAGIC             
# MAGIC             # Step 4: Execute the SQL query using SQL Warehouse
# MAGIC             print(f"\n{'='*80}")
# MAGIC             print("🔍 EXECUTING SQL QUERY (via SQL Warehouse)")
# MAGIC             print(f"{'='*80}")
# MAGIC             print(f"Warehouse ID: {self.warehouse_id}")
# MAGIC             print(f"SQL:\n{extracted_sql}")
# MAGIC             print(f"{'='*80}\n")
# MAGIC             
# MAGIC             # Connect to SQL Warehouse using context manager (production best practice)
# MAGIC             # Context managers ensure proper cleanup even if exceptions occur
# MAGIC             # credentials_provider=cfg lets the connector fetch OAuth tokens transparently
# MAGIC             with sql.connect(
# MAGIC                 server_hostname=cfg.host,
# MAGIC                 http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
# MAGIC                 credentials_provider=lambda: cfg.authenticate,  # Unified authentication - handles OAuth automatically
# MAGIC                 # Production settings for resilience
# MAGIC                 session_configuration={
# MAGIC                     "ansi_mode": "true"  # Enable ANSI SQL compliance for consistent behavior
# MAGIC                 },
# MAGIC                 socket_timeout=900,  # 15 minutes - Model Serving has 297s limit, warehouse queries can be longer
# MAGIC                 http_retry_delay_min=1,  # Minimum retry delay in seconds
# MAGIC                 http_retry_delay_max=60,  # Maximum retry delay in seconds
# MAGIC                 http_retry_max_redirects=5,  # Max HTTP redirects
# MAGIC                 http_retry_stop_after_attempts=30,  # Max retry attempts for transient failures
# MAGIC             ) as connection:
# MAGIC                 
# MAGIC                 # Use nested context manager for cursor (ensures cursor cleanup)
# MAGIC                 with connection.cursor() as cursor:
# MAGIC                     
# MAGIC                     # Execute query
# MAGIC                     cursor.execute(extracted_sql)
# MAGIC                     
# MAGIC                     # Fetch results
# MAGIC                     results = cursor.fetchall()
# MAGIC                     row_count = len(results)
# MAGIC                     columns = [desc[0] for desc in cursor.description]
# MAGIC                     
# MAGIC                     print(f"✅ Query executed successfully!")
# MAGIC                     print(f"📊 Rows returned: {row_count}")
# MAGIC                     print(f"📋 Columns: {', '.join(columns)}\n")
# MAGIC                     
# MAGIC                     # Step 5: Convert results to list of dicts for compatibility
# MAGIC                     result_data = [dict(zip(columns, row)) for row in results]
# MAGIC                     
# MAGIC                 # Cursor automatically closed here by context manager
# MAGIC             
# MAGIC             # Connection automatically closed here by context manager
# MAGIC             
# MAGIC             # Step 6: Format results based on return_format
# MAGIC             if return_format == "json":
# MAGIC                 # Convert to JSON strings (matching old spark behavior)
# MAGIC                 result_data = [json.dumps(row) for row in result_data]
# MAGIC             elif return_format == "markdown":
# MAGIC                 # Create markdown table
# MAGIC                 import pandas as pd
# MAGIC                 pandas_df = pd.DataFrame(result_data)
# MAGIC                 result_data = pandas_df.to_markdown(index=False)
# MAGIC             # else: dict format (default) - already in correct format
# MAGIC             
# MAGIC             # Step 7: Display preview
# MAGIC             print(f"{'='*80}")
# MAGIC             print("📄 RESULTS PREVIEW (first 10 rows)")
# MAGIC             print(f"{'='*80}")
# MAGIC             # Preview first 10 rows
# MAGIC             for i, row in enumerate(result_data[:10]):
# MAGIC                 if return_format == "markdown":
# MAGIC                     break  # Don't print individual rows for markdown
# MAGIC                 print(f"Row {i+1}: {row}")
# MAGIC             print(f"{'='*80}\n")
# MAGIC             
# MAGIC             return {
# MAGIC                 "success": True,
# MAGIC                 "sql": extracted_sql,
# MAGIC                 "result": result_data,
# MAGIC                 "row_count": row_count,
# MAGIC                 "columns": columns,
# MAGIC             }
# MAGIC             
# MAGIC         except Exception as e:
# MAGIC             # Step 8: Handle errors with specific exception types for better diagnostics
# MAGIC             error_type = type(e).__name__
# MAGIC             error_msg = str(e)
# MAGIC             
# MAGIC             # Provide production-grade error categorization
# MAGIC             if "DatabaseError" in error_type or "OperationalError" in error_type:
# MAGIC                 error_category = "SQL Execution Error"
# MAGIC                 error_hint = "Check SQL syntax and table/column permissions"
# MAGIC             elif "ConnectionError" in error_type or "timeout" in error_msg.lower():
# MAGIC                 error_category = "Connection Error"
# MAGIC                 error_hint = "Verify SQL Warehouse is running and network connectivity"
# MAGIC             elif "Authentication" in error_msg or "Unauthorized" in error_msg:
# MAGIC                 error_category = "Authentication Error"
# MAGIC                 error_hint = "Verify access token and warehouse permissions"
# MAGIC             else:
# MAGIC                 error_category = "General Error"
# MAGIC                 error_hint = "Review full error details below"
# MAGIC             
# MAGIC             print(f"\n{'='*80}")
# MAGIC             print(f"❌ SQL EXECUTION FAILED - {error_category}")
# MAGIC             print(f"{'='*80}")
# MAGIC             print(f"Error Type: {error_type}")
# MAGIC             print(f"Error Message: {error_msg}")
# MAGIC             print(f"Hint: {error_hint}")
# MAGIC             print(f"Warehouse ID: {self.warehouse_id}")
# MAGIC             print(f"{'='*80}\n")
# MAGIC             
# MAGIC             return {
# MAGIC                 "success": False,
# MAGIC                 "sql": extracted_sql,
# MAGIC                 "result": None,
# MAGIC                 "row_count": 0,
# MAGIC                 "columns": [],
# MAGIC                 "error": f"{error_category}: {error_msg}",
# MAGIC                 "error_type": error_type,
# MAGIC                 "error_hint": error_hint
# MAGIC             }
# MAGIC     
# MAGIC     def __call__(self, sql_query: str, max_rows: int = 100, return_format: str = "dict") -> Dict[str, Any]:
# MAGIC         """Make agent callable."""
# MAGIC         return self.execute_sql(sql_query, max_rows, return_format)
# MAGIC
# MAGIC print("✓ SQLExecutionAgent class defined")
# MAGIC class ResultSummarizeAgent:
# MAGIC     """
# MAGIC     Agent responsible for generating a final summary of the workflow execution.
# MAGIC     
# MAGIC     Analyzes the entire workflow state and produces a natural language summary
# MAGIC     of what was accomplished, whether successful or not.
# MAGIC     
# MAGIC     OOP design for clean summarization logic.
# MAGIC     """
# MAGIC     
# MAGIC     def __init__(self, llm: Runnable):
# MAGIC         self.name = "ResultSummarize"
# MAGIC         self.llm = llm
# MAGIC     
# MAGIC     @staticmethod
# MAGIC     def _safe_json_dumps(obj: Any, indent: int = 2) -> str:
# MAGIC         """
# MAGIC         Safely serialize objects to JSON, converting dates/datetime to strings.
# MAGIC         
# MAGIC         Args:
# MAGIC             obj: Object to serialize
# MAGIC             indent: JSON indentation level
# MAGIC             
# MAGIC         Returns:
# MAGIC             JSON string with date/datetime objects converted to ISO format strings
# MAGIC         """
# MAGIC         from datetime import date, datetime
# MAGIC         from decimal import Decimal
# MAGIC         
# MAGIC         def default_handler(o):
# MAGIC             if isinstance(o, (date, datetime)):
# MAGIC                 return o.isoformat()
# MAGIC             elif isinstance(o, Decimal):
# MAGIC                 return float(o)
# MAGIC             else:
# MAGIC                 raise TypeError(f'Object of type {o.__class__.__name__} is not JSON serializable')
# MAGIC         
# MAGIC         return json.dumps(obj, indent=indent, default=default_handler)
# MAGIC     
# MAGIC     def generate_summary(self, state: AgentState) -> str:
# MAGIC         """
# MAGIC         Generate a natural language summary of the workflow execution.
# MAGIC         
# MAGIC         Args:
# MAGIC             state: The complete workflow state
# MAGIC             
# MAGIC         Returns:
# MAGIC             String containing natural language summary
# MAGIC         """
# MAGIC         # Build context from state
# MAGIC         summary_prompt = self._build_summary_prompt(state)
# MAGIC         
# MAGIC         # Invoke LLM to generate summary
# MAGIC         response = self.llm.invoke(summary_prompt)
# MAGIC         summary = response.content.strip()
# MAGIC         
# MAGIC         return summary
# MAGIC     
# MAGIC     def _build_summary_prompt(self, state: AgentState) -> str:
# MAGIC         """Build the prompt for summary generation based on state."""
# MAGIC         
# MAGIC         original_query = state.get('original_query', 'N/A')
# MAGIC         question_clear = state.get('question_clear', False)
# MAGIC         pending_clarification = state.get('pending_clarification')
# MAGIC         execution_plan = state.get('execution_plan')
# MAGIC         join_strategy = state.get('join_strategy')
# MAGIC         sql_query = state.get('sql_query')
# MAGIC         sql_explanation = state.get('sql_synthesis_explanation')
# MAGIC         exec_result = state.get('execution_result', {})
# MAGIC         synthesis_error = state.get('synthesis_error')
# MAGIC         execution_error = state.get('execution_error')
# MAGIC         
# MAGIC         prompt = f"""You are a result summarization agent. Generate a concise, natural language summary of what this multi-agent workflow accomplished.
# MAGIC
# MAGIC **Original User Query:** {original_query}
# MAGIC
# MAGIC **Workflow Execution Details:**
# MAGIC
# MAGIC """
# MAGIC         
# MAGIC         # Add clarification info
# MAGIC         if not question_clear and pending_clarification:
# MAGIC             clarification_reason = pending_clarification.get('reason', 'Query needs clarification')
# MAGIC             prompt += f"""**Status:** Query needs clarification
# MAGIC **Clarification Needed:** {clarification_reason}
# MAGIC **Summary:** The query was too vague or ambiguous. Requested user clarification before proceeding.
# MAGIC """
# MAGIC         else:
# MAGIC             # Add planning info
# MAGIC             if execution_plan:
# MAGIC                 prompt += f"""**Planning:** {execution_plan}
# MAGIC **Strategy:** {join_strategy or 'N/A'}
# MAGIC
# MAGIC """
# MAGIC             
# MAGIC             # Add SQL synthesis info
# MAGIC             if sql_query:
# MAGIC                 prompt += f"""**SQL Generation:** ✅ Successful
# MAGIC **SQL Query:** 
# MAGIC ```sql
# MAGIC {sql_query}
# MAGIC ```
# MAGIC
# MAGIC """
# MAGIC                 if sql_explanation:
# MAGIC                     prompt += f"""**SQL Synthesis Explanation:** {sql_explanation[:2000]}{'...' if len(sql_explanation) > 2000 else ''}
# MAGIC
# MAGIC """
# MAGIC                 
# MAGIC                 # Add execution info
# MAGIC                 if exec_result.get('success'):
# MAGIC                     row_count = exec_result.get('row_count', 0)
# MAGIC                     columns = exec_result.get('columns', [])
# MAGIC                     result = exec_result.get('result', [])
# MAGIC                     
# MAGIC                     # TOKEN PROTECTION: Sample results to prevent huge prompts
# MAGIC                     # - Max 10 rows
# MAGIC                     # - Max 10 columns per row
# MAGIC                     # - Max 5000 characters for JSON
# MAGIC                     MAX_PREVIEW_ROWS = 10
# MAGIC                     MAX_PREVIEW_COLS = 10
# MAGIC                     MAX_JSON_CHARS = 5000
# MAGIC                     
# MAGIC                     # Sample rows
# MAGIC                     result_preview = result[:MAX_PREVIEW_ROWS] if len(result) > MAX_PREVIEW_ROWS else result
# MAGIC                     
# MAGIC                     # Sample columns (if result has too many columns)
# MAGIC                     if result_preview and len(columns) > MAX_PREVIEW_COLS:
# MAGIC                         # Keep only first MAX_PREVIEW_COLS columns
# MAGIC                         sampled_cols = columns[:MAX_PREVIEW_COLS]
# MAGIC                         result_preview = [
# MAGIC                             {k: v for k, v in row.items() if k in sampled_cols}
# MAGIC                             for row in result_preview
# MAGIC                         ]
# MAGIC                         col_display = ', '.join(sampled_cols) + f'... (+{len(columns) - MAX_PREVIEW_COLS} more columns)'
# MAGIC                     else:
# MAGIC                         col_display = ', '.join(columns[:10]) + ('...' if len(columns) > 10 else '')
# MAGIC                     
# MAGIC                     # Serialize to JSON
# MAGIC                     result_json = self._safe_json_dumps(result_preview, indent=2)
# MAGIC                     
# MAGIC                     # Truncate JSON if too large
# MAGIC                     if len(result_json) > MAX_JSON_CHARS:
# MAGIC                         result_json = result_json[:MAX_JSON_CHARS] + f'\n... (truncated, {len(result_json) - MAX_JSON_CHARS} chars omitted)'
# MAGIC                     
# MAGIC                     prompt += f"""**Execution:** ✅ Successful
# MAGIC **Rows:** {row_count} rows returned{f' (showing first {MAX_PREVIEW_ROWS})' if row_count > MAX_PREVIEW_ROWS else ''}
# MAGIC **Columns:** {col_display}
# MAGIC
# MAGIC **Result Preview:** 
# MAGIC {result_json}
# MAGIC {f'... and {row_count - MAX_PREVIEW_ROWS} more rows' if row_count > MAX_PREVIEW_ROWS else ''}
# MAGIC """
# MAGIC                 elif execution_error:
# MAGIC                     prompt += f"""**Execution:** ❌ Failed
# MAGIC **Error:** {execution_error}
# MAGIC
# MAGIC """
# MAGIC             elif synthesis_error:
# MAGIC                 prompt += f"""**SQL Generation:** ❌ Failed
# MAGIC **Error:** {synthesis_error}
# MAGIC **Explanation:** {sql_explanation or 'N/A'}
# MAGIC
# MAGIC """
# MAGIC         
# MAGIC         prompt += """
# MAGIC **Task:** Generate a detailed summary in natural language that:
# MAGIC 1. Describes what the user asked for
# MAGIC 2. Explains what the system did (planning, SQL generation, execution)
# MAGIC 3. States the outcome (success with X rows, error, needs clarification, etc.)
# MAGIC 4. print out SQL synthesis explanation if any SQL was generated
# MAGIC 5. print out SQL if any SQL was generated; make it the code block
# MAGIC 6. print out the result itself (like a table).
# MAGIC
# MAGIC
# MAGIC Keep it concise and user-friendly. 
# MAGIC """
# MAGIC         
# MAGIC         return prompt
# MAGIC     
# MAGIC     def __call__(self, state: AgentState) -> str:
# MAGIC         """Make agent callable."""
# MAGIC         return self.generate_summary(state)
# MAGIC
# MAGIC print("✓ ResultSummarizeAgent class defined")
# MAGIC
# MAGIC # ==============================================================================
# MAGIC # DEPRECATED FUNCTIONS (Replaced by Intent Detection Service)
# MAGIC # ==============================================================================
# MAGIC # The following functions have been replaced by the IntentDetectionAgent:
# MAGIC # - find_most_recent_clarification_context() → Intent detection handles this
# MAGIC # - is_new_question() → Intent detection provides intent_type classification
# MAGIC #
# MAGIC # These have been removed to simplify the codebase. See:
# MAGIC # - kumc_poc/intent_detection_service.py for the replacement
# MAGIC # - kumc_poc/conversation_models.py for the new data models
# MAGIC # ==============================================================================
# MAGIC
# MAGIC # ==============================================================================
# MAGIC # State Extraction Helpers (Token Optimization)
# MAGIC # ==============================================================================
# MAGIC """
# MAGIC Minimal state extraction helpers to reduce token usage.
# MAGIC
# MAGIC Instead of passing the entire AgentState (25+ fields) to each agent,
# MAGIC these helpers extract only the fields each node actually needs.
# MAGIC
# MAGIC Expected savings: 60-70% token reduction per node
# MAGIC """
# MAGIC
# MAGIC def extract_intent_detection_context(state: AgentState) -> dict:
# MAGIC     """
# MAGIC     Extract minimal context for intent detection.
# MAGIC     
# MAGIC     OPTIMIZED: Applies message and turn history truncation
# MAGIC     """
# MAGIC     messages = state.get("messages", [])
# MAGIC     turn_history = state.get("turn_history", [])
# MAGIC     
# MAGIC     return {
# MAGIC         "messages": truncate_message_history(messages, max_turns=5),
# MAGIC         "turn_history": truncate_turn_history(turn_history, max_turns=10),
# MAGIC         "user_id": state.get("user_id"),
# MAGIC         "thread_id": state.get("thread_id"),
# MAGIC         # For logging: track original sizes
# MAGIC         "_original_message_count": len(messages),
# MAGIC         "_original_turn_count": len(turn_history)
# MAGIC     }
# MAGIC
# MAGIC def extract_clarification_context(state: AgentState) -> dict:
# MAGIC     """
# MAGIC     Extract minimal context for clarification.
# MAGIC     
# MAGIC     OPTIMIZED: Applies message and turn history truncation
# MAGIC     """
# MAGIC     messages = state.get("messages", [])
# MAGIC     turn_history = state.get("turn_history", [])
# MAGIC     
# MAGIC     return {
# MAGIC         "current_turn": state.get("current_turn"),
# MAGIC         "turn_history": truncate_turn_history(turn_history, max_turns=10),
# MAGIC         "intent_metadata": state.get("intent_metadata"),
# MAGIC         "messages": truncate_message_history(messages, max_turns=5),
# MAGIC         # For logging: track original sizes
# MAGIC         "_original_message_count": len(messages),
# MAGIC         "_original_turn_count": len(turn_history)
# MAGIC     }
# MAGIC
# MAGIC def extract_planning_context(state: AgentState) -> dict:
# MAGIC     """Extract minimal context for planning."""
# MAGIC     return {
# MAGIC         "current_turn": state.get("current_turn"),
# MAGIC         "intent_metadata": state.get("intent_metadata"),
# MAGIC         "original_query": state.get("original_query")  # Backward compat
# MAGIC     }
# MAGIC
# MAGIC def extract_synthesis_table_context(state: AgentState) -> dict:
# MAGIC     """Extract minimal context for table-based SQL synthesis."""
# MAGIC     return {
# MAGIC         "plan": state.get("plan", {}),
# MAGIC         "relevant_space_ids": state.get("relevant_space_ids", [])
# MAGIC     }
# MAGIC
# MAGIC def extract_synthesis_genie_context(state: AgentState) -> dict:
# MAGIC     """Extract minimal context for genie-based SQL synthesis."""
# MAGIC     return {
# MAGIC         "plan": state.get("plan", {}),
# MAGIC         "relevant_spaces": state.get("relevant_spaces", []),
# MAGIC         "genie_route_plan": state.get("genie_route_plan")
# MAGIC     }
# MAGIC
# MAGIC def extract_execution_context(state: AgentState) -> dict:
# MAGIC     """Extract minimal context for SQL execution."""
# MAGIC     return {
# MAGIC         "sql_query": state.get("sql_query")
# MAGIC     }
# MAGIC
# MAGIC def extract_summarize_context(state: AgentState) -> dict:
# MAGIC     """
# MAGIC     Extract minimal context for result summarization.
# MAGIC     
# MAGIC     OPTIMIZED: Applies message history truncation
# MAGIC     """
# MAGIC     messages = state.get("messages", [])
# MAGIC     
# MAGIC     return {
# MAGIC         "messages": truncate_message_history(messages, max_turns=5),
# MAGIC         "sql_query": state.get("sql_query"),
# MAGIC         "execution_result": state.get("execution_result"),
# MAGIC         "execution_error": state.get("execution_error"),
# MAGIC         "sql_synthesis_explanation": state.get("sql_synthesis_explanation"),
# MAGIC         "synthesis_error": state.get("synthesis_error"),
# MAGIC         # For logging: track original size
# MAGIC         "_original_message_count": len(messages)
# MAGIC     }
# MAGIC
# MAGIC print("✓ State extraction helpers defined (for token optimization)")
# MAGIC
# MAGIC # ==============================================================================
# MAGIC # Message History Truncation (Token Optimization - Priority 3)
# MAGIC # ==============================================================================
# MAGIC """
# MAGIC Smart message history truncation to keep only recent turns.
# MAGIC
# MAGIC Reduces token usage in long conversations by keeping only:
# MAGIC - All SystemMessage instances (prompts)
# MAGIC - Last N HumanMessage/AIMessage pairs
# MAGIC
# MAGIC Example: After 10 turns (20 messages):
# MAGIC - Before: 18K tokens
# MAGIC - After: 6K tokens (67% reduction)
# MAGIC """
# MAGIC
# MAGIC def truncate_message_history(
# MAGIC     messages: List, 
# MAGIC     max_turns: int = 5,
# MAGIC     keep_system: bool = True
# MAGIC ) -> List:
# MAGIC     """
# MAGIC     Keep only recent turns + system messages.
# MAGIC     
# MAGIC     Args:
# MAGIC         messages: Full message history
# MAGIC         max_turns: Number of recent turns to keep (default 5)
# MAGIC         keep_system: Whether to preserve all SystemMessage instances
# MAGIC         
# MAGIC     Returns:
# MAGIC         Truncated message list
# MAGIC     """
# MAGIC     if not messages:
# MAGIC         return []
# MAGIC     
# MAGIC     # Separate system messages from conversation
# MAGIC     system_msgs = []
# MAGIC     conversation_msgs = []
# MAGIC     
# MAGIC     for msg in messages:
# MAGIC         if isinstance(msg, SystemMessage) and keep_system:
# MAGIC             system_msgs.append(msg)
# MAGIC         else:
# MAGIC             conversation_msgs.append(msg)
# MAGIC     
# MAGIC     # Keep only last N turns (each turn = HumanMessage + AIMessage pair)
# MAGIC     recent_msgs = conversation_msgs[-(max_turns * 2):] if len(conversation_msgs) > max_turns * 2 else conversation_msgs
# MAGIC     
# MAGIC     return system_msgs + recent_msgs
# MAGIC
# MAGIC
# MAGIC def truncate_turn_history(
# MAGIC     turn_history: List, 
# MAGIC     max_turns: int = 10
# MAGIC ) -> List:
# MAGIC     """
# MAGIC     Keep only recent turns in turn_history.
# MAGIC     
# MAGIC     Args:
# MAGIC         turn_history: Full turn history
# MAGIC         max_turns: Number of recent turns to keep (default 10)
# MAGIC         
# MAGIC     Returns:
# MAGIC         Truncated turn history list
# MAGIC     """
# MAGIC     if not turn_history:
# MAGIC         return []
# MAGIC     
# MAGIC     # Keep only last N turns
# MAGIC     return turn_history[-max_turns:] if len(turn_history) > max_turns else turn_history
# MAGIC
# MAGIC
# MAGIC print("✓ Message truncation functions defined (keeps last 5 message turns, 10 turn_history)")
# MAGIC
# MAGIC # ==============================================================================
# MAGIC # Unified Intent, Context, and Clarification Node (Simplified - No kumc_poc imports)
# MAGIC # ==============================================================================
# MAGIC
# MAGIC def check_clarification_rate_limit(turn_history: List[ConversationTurn], window_size: int = 5) -> bool:
# MAGIC     """
# MAGIC     Check if clarification was triggered in the last N turns (sliding window).
# MAGIC     
# MAGIC     Args:
# MAGIC         turn_history: List of conversation turns
# MAGIC         window_size: Number of recent turns to check (default: 5)
# MAGIC     
# MAGIC     Returns:
# MAGIC         True if rate limited (skip clarification), False if ok to clarify
# MAGIC     """
# MAGIC     if not turn_history:
# MAGIC         return False
# MAGIC     
# MAGIC     # Look at last N turns
# MAGIC     recent_turns = turn_history[-window_size:]
# MAGIC     
# MAGIC     # Check if any triggered clarification
# MAGIC     for turn in recent_turns:
# MAGIC         if turn.get("triggered_clarification", False):
# MAGIC             return True  # Rate limited
# MAGIC     
# MAGIC     return False  # OK to clarify
# MAGIC
# MAGIC
# MAGIC def unified_intent_context_clarification_node(state: AgentState) -> dict:
# MAGIC     """
# MAGIC     Unified node that combines intent detection, context generation, and clarity check.
# MAGIC     
# MAGIC     Single LLM call for:
# MAGIC     1. Intent classification (new_question, refinement, continuation, clarification_response)
# MAGIC     2. Context summary generation
# MAGIC     3. Clarity assessment with rate limiting (max 1 per 5 turns)
# MAGIC     
# MAGIC     Returns: Dictionary with state updates
# MAGIC     """
# MAGIC     from langgraph.config import get_stream_writer
# MAGIC     
# MAGIC     writer = get_stream_writer()
# MAGIC     
# MAGIC     print("\n" + "="*80)
# MAGIC     print("🎯 UNIFIED INTENT, CONTEXT & CLARIFICATION AGENT")
# MAGIC     print("="*80)
# MAGIC     
# MAGIC     # Get current query from messages
# MAGIC     messages = state.get("messages", [])
# MAGIC     turn_history = state.get("turn_history", [])
# MAGIC     
# MAGIC     human_messages = [m for m in messages if isinstance(m, HumanMessage)]
# MAGIC     current_query = human_messages[-1].content if human_messages else ""
# MAGIC     
# MAGIC     writer({"type": "agent_start", "agent": "unified_intent_context_clarification", "query": current_query})
# MAGIC     
# MAGIC     print(f"Query: {current_query}")
# MAGIC     print(f"Turn history: {len(turn_history)} turns")
# MAGIC     
# MAGIC     # Format conversation context
# MAGIC     conversation_context = ""
# MAGIC     if turn_history:
# MAGIC         conversation_context = "Previous conversation:\n"
# MAGIC         for i, turn in enumerate(turn_history[-5:], 1):  # Last 5 turns
# MAGIC             intent_label = turn['intent_type'].replace('_', ' ').title()
# MAGIC             conversation_context += f"{i}. [{intent_label}] {turn['query']}\n"
# MAGIC             if turn.get('context_summary'):
# MAGIC                 conversation_context += f"   Context: {turn['context_summary'][:1000]}...\n"
# MAGIC     else:
# MAGIC         conversation_context = "No previous conversation (first query)."
# MAGIC     
# MAGIC     # Load space context for clarity check
# MAGIC     space_context = load_space_context(TABLE_NAME)
# MAGIC     
# MAGIC     # Single unified prompt for intent + context + clarity
# MAGIC     unified_prompt = f"""Analyze the user's query in the context of the conversation history.
# MAGIC
# MAGIC Current Query: {current_query}
# MAGIC
# MAGIC Conversation History:
# MAGIC {conversation_context}
# MAGIC
# MAGIC Available Data Sources:
# MAGIC {json.dumps(space_context, indent=2)}
# MAGIC
# MAGIC ## Task 1: Classify Intent
# MAGIC Classify the query into ONE of these categories:
# MAGIC 1. **new_question**: A completely different topic/domain from previous queries
# MAGIC 2. **refinement**: Narrowing/filtering/modifying the previous query on same topic
# MAGIC 3. **continuation**: Follow-up exploring same topic from different angle
# MAGIC 4. **clarification_response**: User is providing the clarification response to the clarification request
# MAGIC
# MAGIC ## Task 2: Generate Context Summary
# MAGIC Create a 2-3 sentence summary that:
# MAGIC - Synthesizes the conversation history
# MAGIC - States clearly what the user wants
# MAGIC - Is actionable for SQL query planning
# MAGIC
# MAGIC ## Task 3: Check Clarity
# MAGIC Determine if the query is clear enough to generate SQL:
# MAGIC - Is the question clear and answerable as-is? (BE LENIENT - default to TRUE)
# MAGIC - ONLY mark as unclear if CRITICAL information is missing
# MAGIC - If unclear, provide 2-3 specific clarification options
# MAGIC - Never mark as unclear if the question is a clarification response to a previous clarification request
# MAGIC
# MAGIC Return ONLY valid JSON:
# MAGIC {{
# MAGIC   "intent_type": "new_question" | "refinement" | "continuation" | "clarification_response",
# MAGIC   "confidence": 0.95,
# MAGIC   "context_summary": "2-3 sentence summary for planning agent",
# MAGIC   "question_clear": true/false,
# MAGIC   "clarification_reason": "Why unclear (if question_clear=false)",
# MAGIC   "clarification_options": ["Option 1", "Option 2", "Option 3"] or null,
# MAGIC   "metadata": {{
# MAGIC     "domain": "patients | claims | providers | medications | ...",
# MAGIC     "complexity": "simple | moderate | complex",
# MAGIC     "topic_change_score": 0.8
# MAGIC   }}
# MAGIC }}
# MAGIC """
# MAGIC     
# MAGIC     # Call LLM
# MAGIC     llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
# MAGIC     
# MAGIC     try:
# MAGIC         print("🤖 Invoking unified LLM call...")
# MAGIC         response = llm.invoke(unified_prompt)
# MAGIC         content = response.content if hasattr(response, 'content') else str(response)
# MAGIC         
# MAGIC         # Parse JSON response
# MAGIC         if "```json" in content:
# MAGIC             content = content.split("```json")[1].split("```")[0].strip()
# MAGIC         elif "```" in content:
# MAGIC             content = content.split("```")[1].split("```")[0].strip()
# MAGIC         
# MAGIC         result = json.loads(content)
# MAGIC         
# MAGIC         # Extract results
# MAGIC         intent_type = result["intent_type"].lower()
# MAGIC         confidence = result["confidence"]
# MAGIC         context_summary = result["context_summary"]
# MAGIC         question_clear = result["question_clear"]
# MAGIC         clarification_reason = result.get("clarification_reason")
# MAGIC         clarification_options = result.get("clarification_options", [])
# MAGIC         metadata = result.get("metadata", {})
# MAGIC         
# MAGIC         print(f"✓ Intent: {intent_type} (confidence: {confidence:.2f})")
# MAGIC         print(f"  Context: {context_summary[:100]}...")
# MAGIC         print(f"  Question clear: {question_clear}")
# MAGIC         
# MAGIC         # Create conversation turn
# MAGIC         turn = create_conversation_turn(
# MAGIC             query=current_query,
# MAGIC             intent_type=intent_type,
# MAGIC             parent_turn_id=None,  # Could extract from history if needed
# MAGIC             context_summary=context_summary,
# MAGIC             triggered_clarification=False,  # Will be updated if clarification triggered
# MAGIC             metadata=metadata
# MAGIC         )
# MAGIC         
# MAGIC         # Create intent metadata
# MAGIC         intent_metadata = IntentMetadata(
# MAGIC             intent_type=intent_type,
# MAGIC             confidence=confidence,
# MAGIC             reasoning=f"Unified analysis: {intent_type}",
# MAGIC             topic_change_score=metadata.get("topic_change_score", 0.5),
# MAGIC             domain=metadata.get("domain"),
# MAGIC             operation=None,
# MAGIC             complexity=metadata.get("complexity", "moderate"),
# MAGIC             parent_turn_id=None
# MAGIC         )
# MAGIC         
# MAGIC         # Emit events
# MAGIC         writer({
# MAGIC             "type": "intent_detected",
# MAGIC             "intent_type": intent_type,
# MAGIC             "confidence": confidence,
# MAGIC             "complexity": metadata.get("complexity", "moderate")
# MAGIC         })
# MAGIC         
# MAGIC         # Check if clarification needed
# MAGIC         if not question_clear:
# MAGIC             print(f"⚠ Query unclear: {clarification_reason}")
# MAGIC             
# MAGIC             # Check rate limit
# MAGIC             is_rate_limited = check_clarification_rate_limit(turn_history, window_size=5)
# MAGIC             
# MAGIC             if is_rate_limited:
# MAGIC                 print("⚠ Clarification rate limit reached (1 per 5 turns)")
# MAGIC                 print("  Proceeding with best-effort interpretation")
# MAGIC                 
# MAGIC                 writer({"type": "clarification_skipped", "reason": "Rate limited (1 per 5 turns)"})
# MAGIC                 
# MAGIC                 # Force proceed to planning
# MAGIC                 return {
# MAGIC                     "current_turn": turn,
# MAGIC                     "turn_history": [turn],
# MAGIC                     "intent_metadata": intent_metadata,
# MAGIC                     "question_clear": True,  # Force clear
# MAGIC                     "pending_clarification": None,
# MAGIC                     "messages": [
# MAGIC                         SystemMessage(content=f"Clarification rate limited. Proceeding with: {context_summary}")
# MAGIC                     ]
# MAGIC                 }
# MAGIC             else:
# MAGIC                 # OK to clarify
# MAGIC                 print("✓ Requesting clarification from user")
# MAGIC                 
# MAGIC                 # Create clarification request
# MAGIC                 clarification_request = create_clarification_request(
# MAGIC                     reason=clarification_reason or "Query needs more specificity",
# MAGIC                     options=clarification_options,
# MAGIC                     turn_id=turn["turn_id"],
# MAGIC                     best_guess=context_summary,
# MAGIC                     best_guess_confidence=confidence
# MAGIC                 )
# MAGIC                 
# MAGIC                 # Mark turn as triggering clarification
# MAGIC                 turn["triggered_clarification"] = True
# MAGIC                 
# MAGIC                 # Format message
# MAGIC                 clarification_message = format_clarification_message(clarification_request)
# MAGIC                 
# MAGIC                 writer({"type": "clarification_requested", "reason": clarification_reason})
# MAGIC                 
# MAGIC                 return {
# MAGIC                     "current_turn": turn,
# MAGIC                     "turn_history": [turn],
# MAGIC                     "intent_metadata": intent_metadata,
# MAGIC                     "question_clear": False,
# MAGIC                     "pending_clarification": clarification_request,
# MAGIC                     "messages": [
# MAGIC                         AIMessage(content=clarification_message),
# MAGIC                         SystemMessage(content=f"Clarification requested for turn {turn['turn_id']}")
# MAGIC                     ]
# MAGIC                 }
# MAGIC         else:
# MAGIC             # Question is clear, proceed to planning
# MAGIC             print("✓ Query is clear - proceeding to planning")
# MAGIC             
# MAGIC             writer({"type": "clarity_analysis", "clear": True, "reasoning": "Query is clear and answerable"})
# MAGIC             
# MAGIC             return {
# MAGIC                 "current_turn": turn,
# MAGIC                 "turn_history": [turn],
# MAGIC                 "intent_metadata": intent_metadata,
# MAGIC                 "question_clear": True,
# MAGIC                 "pending_clarification": None,
# MAGIC                 "messages": [
# MAGIC                     SystemMessage(content=f"Intent: {intent_type}, proceeding to planning")
# MAGIC                 ]
# MAGIC             }
# MAGIC         
# MAGIC     except Exception as e:
# MAGIC         print(f"❌ Unified agent error: {e}")
# MAGIC         import traceback
# MAGIC         traceback.print_exc()
# MAGIC         
# MAGIC         # Fallback: create minimal turn and proceed
# MAGIC         turn = create_conversation_turn(
# MAGIC             query=current_query,
# MAGIC             intent_type="new_question",
# MAGIC             context_summary=f"Query: {current_query}",
# MAGIC             triggered_clarification=False,
# MAGIC             metadata={}
# MAGIC         )
# MAGIC         
# MAGIC         intent_metadata = IntentMetadata(
# MAGIC             intent_type="new_question",
# MAGIC             confidence=0.5,
# MAGIC             reasoning=f"Error fallback: {str(e)}",
# MAGIC             topic_change_score=1.0,
# MAGIC             domain=None,
# MAGIC             operation=None,
# MAGIC             complexity="moderate",
# MAGIC             parent_turn_id=None
# MAGIC         )
# MAGIC         
# MAGIC         return {
# MAGIC             "current_turn": turn,
# MAGIC             "turn_history": [turn],
# MAGIC             "intent_metadata": intent_metadata,
# MAGIC             "question_clear": True,  # Proceed despite error
# MAGIC             "pending_clarification": None,
# MAGIC             "messages": [
# MAGIC                 SystemMessage(content=f"Unified agent error (proceeding anyway): {str(e)}")
# MAGIC             ]
# MAGIC         }
# MAGIC
# MAGIC print("✓ Unified intent, context, and clarification node defined")
# MAGIC
# MAGIC def planning_node(state: AgentState) -> dict:
# MAGIC     """
# MAGIC     Planning node wrapping PlanningAgent class using turn-based context.
# MAGIC     
# MAGIC     IMPROVEMENTS:
# MAGIC     - Uses current_turn.context_summary (LLM-generated) instead of manual combined_query_context
# MAGIC     - Intent-aware planning (different strategies for refinements vs new questions)
# MAGIC     - Clean separation from clarification logic
# MAGIC     
# MAGIC     OPTIMIZED: Uses minimal state extraction to reduce token usage
# MAGIC     
# MAGIC     Returns: Dictionary with only the state updates (for clean MLflow traces)
# MAGIC     """
# MAGIC     from langgraph.config import get_stream_writer
# MAGIC     
# MAGIC     writer = get_stream_writer()
# MAGIC     
# MAGIC     print("\n" + "="*80)
# MAGIC     print("📋 PLANNING AGENT (Token Optimized)")
# MAGIC     print("="*80)
# MAGIC     
# MAGIC     # OPTIMIZATION: Extract only minimal context needed for planning
# MAGIC     context = extract_planning_context(state)
# MAGIC     print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
# MAGIC     
# MAGIC     # Get current turn and intent from state
# MAGIC     current_turn = context.get("current_turn")
# MAGIC     if not current_turn:
# MAGIC         # Fallback for backward compatibility
# MAGIC         print("⚠ No current_turn found, falling back to legacy behavior")
# MAGIC         query = context.get("original_query", "")
# MAGIC         intent_type = "new_question"
# MAGIC         context_summary = None
# MAGIC     else:
# MAGIC         query = current_turn["query"]
# MAGIC         intent_type = current_turn.get("intent_type", "new_question")
# MAGIC         context_summary = current_turn.get("context_summary")
# MAGIC     
# MAGIC     # Use context_summary if available (LLM-generated from intent detection)
# MAGIC     # This replaces the manual combined_query_context template
# MAGIC     planning_query = context_summary or query
# MAGIC     
# MAGIC     # Emit agent start event
# MAGIC     writer({"type": "agent_start", "agent": "planning", "query": planning_query[:100]})
# MAGIC     
# MAGIC     print(f"Query: {query}")
# MAGIC     print(f"Intent: {intent_type}")
# MAGIC     if context_summary:
# MAGIC         print(f"✓ Using context summary from intent detection")
# MAGIC         print(f"  Summary: {context_summary[:200]}...")
# MAGIC     else:
# MAGIC         print(f"✓ Using query directly (no context needed)")
# MAGIC     
# MAGIC     llm = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING)
# MAGIC     
# MAGIC     # Use OOP agent
# MAGIC     planning_agent = PlanningAgent(llm, VECTOR_SEARCH_INDEX)
# MAGIC     
# MAGIC     # Emit vector search start event
# MAGIC     writer({"type": "vector_search_start", "index": VECTOR_SEARCH_INDEX})
# MAGIC     
# MAGIC     # Get relevant spaces with full metadata (for Genie agents)
# MAGIC     # Use planning_query which includes context_summary if available
# MAGIC     relevant_spaces_full = planning_agent.search_relevant_spaces(planning_query)
# MAGIC     
# MAGIC     # Emit vector search results
# MAGIC     writer({"type": "vector_search_results", "spaces": relevant_spaces_full, "count": len(relevant_spaces_full)})
# MAGIC     
# MAGIC     # Emit plan formulation start
# MAGIC     writer({"type": "agent_thinking", "agent": "planning", "content": "Creating execution plan..."})
# MAGIC     
# MAGIC     # Create execution plan
# MAGIC     # IMPORTANT: Use planning_query (with context_summary) not just query
# MAGIC     # Pass original_query so it can be shown in the prompt before context_summary
# MAGIC     plan = planning_agent.create_execution_plan(planning_query, relevant_spaces_full, original_query=query)
# MAGIC     
# MAGIC     # Extract plan components
# MAGIC     join_strategy = plan.get("join_strategy")
# MAGIC     
# MAGIC     # Emit plan formulation result
# MAGIC     writer({"type": "plan_formulation", "strategy": join_strategy, "requires_join": plan.get("requires_join", False)})
# MAGIC     
# MAGIC     # Determine next agent
# MAGIC     if join_strategy == "genie_route":
# MAGIC         print("✓ Plan complete - using GENIE ROUTE (Genie agents)")
# MAGIC         next_agent = "sql_synthesis_genie"
# MAGIC     else:
# MAGIC         print("✓ Plan complete - using TABLE ROUTE (direct SQL synthesis)")
# MAGIC         next_agent = "sql_synthesis_table"
# MAGIC     
# MAGIC     # Return only updates (no in-place modifications)
# MAGIC     return {
# MAGIC         "plan": plan,
# MAGIC         "sub_questions": plan.get("sub_questions", []),
# MAGIC         "requires_multiple_spaces": plan.get("requires_multiple_spaces", False),
# MAGIC         "relevant_space_ids": plan.get("relevant_space_ids", []),
# MAGIC         "requires_join": plan.get("requires_join", False),
# MAGIC         "join_strategy": join_strategy,
# MAGIC         "execution_plan": plan.get("execution_plan", ""),
# MAGIC         "genie_route_plan": plan.get("genie_route_plan"),
# MAGIC         "vector_search_relevant_spaces_info": plan.get("vector_search_relevant_spaces_info", []),
# MAGIC         "relevant_spaces": relevant_spaces_full,
# MAGIC         "next_agent": next_agent,
# MAGIC         "messages": [
# MAGIC             SystemMessage(content=f"Execution plan: {json.dumps(plan, indent=2)}")
# MAGIC         ]
# MAGIC     }
# MAGIC
# MAGIC
# MAGIC def sql_synthesis_table_node(state: AgentState) -> dict:
# MAGIC     """
# MAGIC     Fast SQL synthesis node wrapping SQLSynthesisTableAgent class.
# MAGIC     Combines OOP modularity with explicit state management.
# MAGIC     
# MAGIC     OPTIMIZED: Uses minimal state extraction to reduce token usage
# MAGIC     
# MAGIC     Returns: Dictionary with only the state updates (for clean MLflow traces)
# MAGIC     """
# MAGIC     from langgraph.config import get_stream_writer
# MAGIC     
# MAGIC     writer = get_stream_writer()
# MAGIC     
# MAGIC     print("\n" + "="*80)
# MAGIC     print("⚡ SQL SYNTHESIS AGENT - TABLE ROUTE (Token Optimized)")
# MAGIC     print("="*80)
# MAGIC     
# MAGIC     # OPTIMIZATION: Extract only minimal context needed for table-based synthesis
# MAGIC     context = extract_synthesis_table_context(state)
# MAGIC     print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
# MAGIC     
# MAGIC     plan = context.get("plan", {})
# MAGIC     relevant_space_ids = context.get("relevant_space_ids", [])
# MAGIC     
# MAGIC     # Emit synthesis start event
# MAGIC     writer({"type": "sql_synthesis_start", "route": "table", "spaces": relevant_space_ids})
# MAGIC     
# MAGIC     llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
# MAGIC     
# MAGIC     # Use OOP agent
# MAGIC     sql_agent = SQLSynthesisTableAgent(llm, CATALOG, SCHEMA)
# MAGIC     
# MAGIC     print("plan loaded from state is:", plan)
# MAGIC     print(json.dumps(plan, indent=2))
# MAGIC     
# MAGIC     try:
# MAGIC         print("🤖 Invoking SQL synthesis agent...")
# MAGIC         
# MAGIC         # Emit detailed start event
# MAGIC         writer({"type": "agent_thinking", "agent": "sql_synthesis_table", "content": "🧠 Starting SQL synthesis using UC function tools..."})
# MAGIC         writer({"type": "agent_step", "agent": "sql_synthesis_table", "step": "analyzing_plan", "content": f"📋 Analyzing execution plan for {len(relevant_space_ids)} relevant spaces"})
# MAGIC         
# MAGIC         # Emit tool preparation event
# MAGIC         uc_functions = ["get_space_summary", "get_table_overview", "get_column_detail", "get_space_details"]
# MAGIC         writer({"type": "tools_available", "agent": "sql_synthesis_table", "tools": uc_functions, "content": f"🔧 Available UC functions: {', '.join(uc_functions)}"})
# MAGIC         
# MAGIC         # Emit query strategy
# MAGIC         writer({"type": "agent_thinking", "agent": "sql_synthesis_table", "content": f"🎯 Strategy: Query metadata for spaces {relevant_space_ids} then synthesize SQL"})
# MAGIC         
# MAGIC         # Call the agent
# MAGIC         result = sql_agent(plan)
# MAGIC         
# MAGIC         # Emit tool completion event
# MAGIC         writer({"type": "agent_step", "agent": "sql_synthesis_table", "step": "metadata_gathered", "content": "✅ Metadata collection complete, synthesizing SQL query..."})
# MAGIC         
# MAGIC         # Extract SQL and explanation
# MAGIC         sql_query = result.get("sql")
# MAGIC         explanation = result.get("explanation", "")
# MAGIC         has_sql = result.get("has_sql", False)
# MAGIC         
# MAGIC         if has_sql and sql_query and explanation:
# MAGIC             print("✓ SQL query synthesized successfully")
# MAGIC             print(f"SQL Preview: {sql_query[:200]}...")
# MAGIC             if explanation:
# MAGIC                 print(f"Agent Explanation: {explanation[:200]}...")
# MAGIC             
# MAGIC             # Emit detailed success events
# MAGIC             writer({"type": "sql_generated", "agent": "sql_synthesis_table", "query_preview": sql_query[:200], "content": f"💻 SQL Query Generated ({len(sql_query)} chars)"})
# MAGIC             writer({"type": "agent_result", "agent": "sql_synthesis_table", "result": "success", "content": f"✅ SQL synthesis complete: {explanation[:150]}..."})
# MAGIC             
# MAGIC             # Return updates for successful synthesis
# MAGIC             return {
# MAGIC                 "sql_query": sql_query,
# MAGIC                 "has_sql": has_sql,
# MAGIC                 "sql_synthesis_explanation": explanation,
# MAGIC                 "next_agent": "sql_execution",
# MAGIC                 "messages": [
# MAGIC                     AIMessage(content=f"SQL Synthesis (Table Route):\n{explanation}")
# MAGIC                 ]
# MAGIC             }
# MAGIC         else:
# MAGIC             print("⚠ No SQL generated - agent explanation:")
# MAGIC             print(f"  {explanation}")
# MAGIC             
# MAGIC             # Emit failure event
# MAGIC             writer({"type": "agent_result", "agent": "sql_synthesis_table", "result": "no_sql", "content": f"⚠️ Could not generate SQL: {explanation[:150]}..."})
# MAGIC             
# MAGIC             # Return updates for failed synthesis
# MAGIC             return {
# MAGIC                 "synthesis_error": "Cannot generate SQL query",
# MAGIC                 "sql_synthesis_explanation": explanation,
# MAGIC                 "next_agent": "summarize",
# MAGIC                 "messages": [
# MAGIC                     AIMessage(content=f"SQL Synthesis Failed (Table Route):\n{explanation}")
# MAGIC                 ]
# MAGIC             }
# MAGIC         
# MAGIC     except Exception as e:
# MAGIC         print(f"❌ SQL synthesis failed: {e}")
# MAGIC         error_msg = str(e)
# MAGIC         # Return updates for exception
# MAGIC         return {
# MAGIC             "synthesis_error": error_msg,
# MAGIC             "sql_synthesis_explanation": error_msg,
# MAGIC             "messages": [
# MAGIC                 AIMessage(content=f"SQL Synthesis Failed (Table Route):\n{error_msg}")
# MAGIC             ]
# MAGIC         }
# MAGIC
# MAGIC
# MAGIC def sql_synthesis_genie_node(state: AgentState) -> dict:
# MAGIC     """
# MAGIC     Slow SQL synthesis node wrapping SQLSynthesisGenieAgent class.
# MAGIC     Combines OOP modularity with explicit state management.
# MAGIC     
# MAGIC     Uses relevant_spaces from PlanningAgent (no need to re-query all spaces).
# MAGIC     
# MAGIC     OPTIMIZED: Uses minimal state extraction to reduce token usage
# MAGIC     
# MAGIC     Returns: Dictionary with only the state updates (for clean MLflow traces)
# MAGIC     """
# MAGIC     from langgraph.config import get_stream_writer
# MAGIC     
# MAGIC     writer = get_stream_writer()
# MAGIC     
# MAGIC     print("\n" + "="*80)
# MAGIC     print("🐢 SQL SYNTHESIS AGENT - GENIE ROUTE (Token Optimized)")
# MAGIC     print("="*80)
# MAGIC     
# MAGIC     # OPTIMIZATION: Extract only minimal context needed for genie-based synthesis
# MAGIC     context = extract_synthesis_genie_context(state)
# MAGIC     print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
# MAGIC     
# MAGIC     # Get relevant spaces from state (already discovered by PlanningAgent)
# MAGIC     relevant_spaces = context.get("relevant_spaces", [])
# MAGIC     relevant_space_ids = [s.get("space_id") for s in relevant_spaces if s.get("space_id")]
# MAGIC     
# MAGIC     # Emit synthesis start event
# MAGIC     writer({"type": "sql_synthesis_start", "route": "genie", "spaces": relevant_space_ids})
# MAGIC     
# MAGIC     llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
# MAGIC     
# MAGIC     if not relevant_spaces:
# MAGIC         print("❌ No relevant_spaces found in state")
# MAGIC         # Return error update
# MAGIC         return {
# MAGIC             "synthesis_error": "No relevant spaces available for genie route"
# MAGIC         }
# MAGIC     
# MAGIC     # Use OOP agent - only creates Genie agents for relevant spaces
# MAGIC     sql_agent = SQLSynthesisGenieAgent(llm, relevant_spaces)
# MAGIC     
# MAGIC     # Use minimal context (already extracted)
# MAGIC     plan = context.get("plan", {})
# MAGIC     genie_route_plan = context.get("genie_route_plan") or plan.get("genie_route_plan", {})
# MAGIC     
# MAGIC     if not genie_route_plan:
# MAGIC         print("❌ No genie_route_plan found in plan")
# MAGIC         # Return error update
# MAGIC         return {
# MAGIC             "synthesis_error": "No routing plan available for genie route"
# MAGIC         }
# MAGIC     
# MAGIC     try:
# MAGIC         print(f"🤖 Querying {len(genie_route_plan)} Genie agents...")
# MAGIC         
# MAGIC         # Emit detailed start event
# MAGIC         writer({"type": "agent_thinking", "agent": "sql_synthesis_genie", "content": f"🧠 Starting SQL synthesis using {len(genie_route_plan)} Genie agents..."})
# MAGIC         writer({"type": "agent_step", "agent": "sql_synthesis_genie", "step": "preparing_genie_calls", "content": f"📋 Preparing to query {len(genie_route_plan)} Genie spaces"})
# MAGIC         
# MAGIC         # Emit detailed events for each Genie agent call with full context
# MAGIC         for idx, (space_id, query) in enumerate(genie_route_plan.items(), 1):
# MAGIC             space_title = next((s.get("space_title", space_id) for s in relevant_spaces if s.get("space_id") == space_id), space_id)
# MAGIC             writer({
# MAGIC                 "type": "genie_agent_call", 
# MAGIC                 "agent": "sql_synthesis_genie",
# MAGIC                 "space_id": space_id, 
# MAGIC                 "space_title": space_title,
# MAGIC                 "query": query,
# MAGIC                 "content": f"🤖 [{idx}/{len(genie_route_plan)}] Calling Genie agent '{space_title}' with query: {query[:100]}{'...' if len(query) > 100 else ''}"
# MAGIC             })
# MAGIC         
# MAGIC         # Emit execution strategy
# MAGIC         writer({"type": "agent_thinking", "agent": "sql_synthesis_genie", "content": "⚡ Executing Genie agents in parallel for optimal performance..."})
# MAGIC         
# MAGIC         # Call the agent
# MAGIC         result = sql_agent(plan)
# MAGIC         
# MAGIC         # Emit completion event
# MAGIC         writer({"type": "agent_step", "agent": "sql_synthesis_genie", "step": "combining_results", "content": "🔄 All Genie agents responded, combining SQL fragments..."})
# MAGIC         
# MAGIC         # Extract SQL and explanation
# MAGIC         sql_query = result.get("sql")
# MAGIC         explanation = result.get("explanation", "")
# MAGIC         has_sql = result.get("has_sql", False)
# MAGIC         
# MAGIC         # Update explicit state
# MAGIC         if has_sql and sql_query and explanation:
# MAGIC             print("✓ SQL fragments combined successfully")
# MAGIC             print(f"SQL Preview: {sql_query[:200]}...")
# MAGIC             if explanation:
# MAGIC                 print(f"Agent Explanation: {explanation[:200]}...")
# MAGIC             
# MAGIC             # Emit detailed success events
# MAGIC             writer({"type": "sql_generated", "agent": "sql_synthesis_genie", "query_preview": sql_query[:200], "content": f"💻 Combined SQL Query Generated ({len(sql_query)} chars)"})
# MAGIC             writer({"type": "agent_result", "agent": "sql_synthesis_genie", "result": "success", "content": f"✅ SQL synthesis complete: {explanation[:150]}..."})
# MAGIC             writer({"type": "agent_thinking", "agent": "sql_synthesis_genie", "content": f"🎯 Successfully combined SQL from {len(genie_route_plan)} Genie agents"})
# MAGIC             
# MAGIC             # Return updates for successful synthesis
# MAGIC             return {
# MAGIC                 "sql_query": sql_query,
# MAGIC                 "has_sql": has_sql,
# MAGIC                 "sql_synthesis_explanation": explanation,
# MAGIC                 "next_agent": "sql_execution",
# MAGIC                 "messages": [
# MAGIC                     AIMessage(content=f"SQL Synthesis (Genie Route):\n{explanation}")
# MAGIC                 ]
# MAGIC             }
# MAGIC         else:
# MAGIC             print("⚠ No SQL generated - agent explanation:")
# MAGIC             print(f"  {explanation}")
# MAGIC             
# MAGIC             # Emit detailed failure event
# MAGIC             writer({"type": "agent_result", "agent": "sql_synthesis_genie", "result": "no_sql", "content": f"⚠️ Could not generate SQL from Genie agents: {explanation[:150]}..."})
# MAGIC             
# MAGIC             # Return updates for failed synthesis
# MAGIC             return {
# MAGIC                 "synthesis_error": "Cannot generate SQL query from Genie agent fragments",
# MAGIC                 "sql_synthesis_explanation": explanation,
# MAGIC                 "next_agent": "summarize",
# MAGIC                 "messages": [
# MAGIC                     AIMessage(content=f"SQL Synthesis Failed (Genie Route):\n{explanation}")
# MAGIC                 ]
# MAGIC             }
# MAGIC         
# MAGIC     except Exception as e:
# MAGIC         print(f"❌ SQL synthesis failed: {e}")
# MAGIC         error_msg = str(e)
# MAGIC         # Return updates for exception
# MAGIC         return {
# MAGIC             "synthesis_error": error_msg,
# MAGIC             "sql_synthesis_explanation": error_msg,
# MAGIC             "messages": [
# MAGIC                 AIMessage(content=f"SQL Synthesis Failed (Genie Route):\n{error_msg}")
# MAGIC             ]
# MAGIC         }
# MAGIC
# MAGIC
# MAGIC def sql_execution_node(state: AgentState) -> dict:
# MAGIC     """
# MAGIC     SQL execution node wrapping SQLExecutionAgent class.
# MAGIC     Combines OOP modularity with explicit state management.
# MAGIC     
# MAGIC     OPTIMIZED: Uses minimal state extraction to reduce token usage
# MAGIC     
# MAGIC     Returns: Dictionary with only the state updates (for clean MLflow traces)
# MAGIC     """
# MAGIC     from langgraph.config import get_stream_writer
# MAGIC     
# MAGIC     writer = get_stream_writer()
# MAGIC     
# MAGIC     print("\n" + "="*80)
# MAGIC     print("🚀 SQL EXECUTION AGENT (Token Optimized)")
# MAGIC     print("="*80)
# MAGIC     
# MAGIC     # OPTIMIZATION: Extract only minimal context needed for execution
# MAGIC     context = extract_execution_context(state)
# MAGIC     print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
# MAGIC     
# MAGIC     sql_query = context.get("sql_query")
# MAGIC     
# MAGIC     if not sql_query:
# MAGIC         print("❌ No SQL query to execute")
# MAGIC         # Return error update
# MAGIC         return {
# MAGIC             "execution_error": "No SQL query provided"
# MAGIC         }
# MAGIC     
# MAGIC     # Emit validation start event
# MAGIC     writer({"type": "sql_validation_start", "query": sql_query[:200]})
# MAGIC     
# MAGIC     # Emit execution start event
# MAGIC     writer({"type": "sql_execution_start", "estimated_complexity": "standard"})
# MAGIC     
# MAGIC     # Use OOP agent with SQL Warehouse
# MAGIC     execution_agent = SQLExecutionAgent(warehouse_id=SQL_WAREHOUSE_ID)
# MAGIC     result = execution_agent(sql_query)
# MAGIC     
# MAGIC     # Prepare updates based on result
# MAGIC     updates = {
# MAGIC         "execution_result": result,
# MAGIC         "next_agent": "summarize",
# MAGIC         "messages": []
# MAGIC     }
# MAGIC     
# MAGIC     if result["success"]:
# MAGIC         print(f"✓ Query executed successfully!")
# MAGIC         print(f"📊 Rows returned: {result['row_count']}")
# MAGIC         print(f"📋 Columns: {', '.join(result['columns'])}")
# MAGIC         
# MAGIC         # Emit execution complete event
# MAGIC         writer({"type": "sql_execution_complete", "rows": result['row_count'], "columns": result['columns']})
# MAGIC         
# MAGIC         updates["messages"].append(
# MAGIC             SystemMessage(content=f"Execution successful: {result['row_count']} rows returned")
# MAGIC         )
# MAGIC     else:
# MAGIC         print(f"❌ SQL execution failed: {result.get('error', 'Unknown error')}")
# MAGIC         updates["execution_error"] = result.get("error")
# MAGIC         
# MAGIC         updates["messages"].append(
# MAGIC             SystemMessage(content=f"Execution failed: {result.get('error')}")
# MAGIC         )
# MAGIC     
# MAGIC     return updates
# MAGIC
# MAGIC
# MAGIC def summarize_node(state: AgentState) -> dict:
# MAGIC     """
# MAGIC     Result summarize node wrapping ResultSummarizeAgent class.
# MAGIC     
# MAGIC     This is the final node that all workflow paths go through.
# MAGIC     Generates a natural language summary AND preserves all workflow data.
# MAGIC     
# MAGIC     OPTIMIZED: Uses minimal state extraction to reduce token usage
# MAGIC     
# MAGIC     Returns: Dictionary with only the state updates (for clean MLflow traces)
# MAGIC     """
# MAGIC     from langgraph.config import get_stream_writer
# MAGIC     
# MAGIC     writer = get_stream_writer()
# MAGIC     
# MAGIC     print("\n" + "="*80)
# MAGIC     print("📝 RESULT SUMMARIZE AGENT (Token Optimized)")
# MAGIC     print("="*80)
# MAGIC     
# MAGIC     # OPTIMIZATION: Extract only minimal context needed for summarization
# MAGIC     context = extract_summarize_context(state)
# MAGIC     print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
# MAGIC     
# MAGIC     # Emit summary start event
# MAGIC     writer({"type": "summary_start", "content": "Generating comprehensive summary..."})
# MAGIC     
# MAGIC     # Create LLM for summarization (no max_tokens limit for comprehensive output)
# MAGIC     llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SUMMARIZE, temperature=0.1, max_tokens=2000)
# MAGIC     
# MAGIC     # Use OOP agent to generate summary (pass minimal context instead of full state)
# MAGIC     summarize_agent = ResultSummarizeAgent(llm)
# MAGIC     summary = summarize_agent(context)
# MAGIC     
# MAGIC     # Display what's being returned
# MAGIC     print(f"\n📦 State Fields Being Returned:")
# MAGIC     print(f"  ✓ final_summary: {len(summary)} chars")
# MAGIC     if context.get("sql_query"):
# MAGIC         print(f"  ✓ sql_query: {len(context['sql_query'])} chars")
# MAGIC     if context.get("execution_result"):
# MAGIC         exec_result = context["execution_result"]
# MAGIC         if exec_result.get("success"):
# MAGIC             print(f"  ✓ execution_result: {exec_result.get('row_count', 0)} rows")
# MAGIC         else:
# MAGIC             print(f"  ✓ execution_result: Failed - {exec_result.get('error', 'Unknown')[:50]}...")
# MAGIC     if context.get("sql_synthesis_explanation"):
# MAGIC         print(f"  ✓ sql_synthesis_explanation: {len(context['sql_synthesis_explanation'])} chars")
# MAGIC     if state.get("execution_plan"):
# MAGIC         print(f"  ✓ execution_plan: {state['execution_plan'][:80]}...")
# MAGIC     if state.get("synthesis_error"):
# MAGIC         print(f"  ⚠ synthesis_error: {state['synthesis_error'][:50]}...")
# MAGIC     if state.get("execution_error"):
# MAGIC         print(f"  ⚠ execution_error: {state['execution_error'][:50]}...")
# MAGIC     
# MAGIC     print("="*80)
# MAGIC     
# MAGIC     # Emit summary completion event
# MAGIC     writer({"type": "summary_complete", "content": f"✅ Summary generated ({len(summary)} chars)"})
# MAGIC     
# MAGIC     # Build a concise final message for AIMessage (avoid duplication with final_summary)
# MAGIC     # Only include execution results and errors (summary goes to final_summary field)
# MAGIC     final_message_parts = []
# MAGIC     
# MAGIC     # 1. Execution Results (if available)
# MAGIC     exec_result = state.get("execution_result")
# MAGIC     if exec_result and exec_result.get("success"):
# MAGIC         results = exec_result.get("result", [])
# MAGIC         if results:
# MAGIC             try:
# MAGIC                 import pandas as pd
# MAGIC                 df = pd.DataFrame(results)
# MAGIC                 
# MAGIC                 # Display DataFrame in notebook
# MAGIC                 print("\n" + "="*80)
# MAGIC                 print("📊 QUERY RESULTS (Pandas DataFrame)")
# MAGIC                 print("="*80)
# MAGIC                 try:
# MAGIC                     display(df)  # Use Databricks display() for interactive view
# MAGIC                 except:
# MAGIC                     print(df.to_string())  # Fallback to string representation
# MAGIC                 print("="*80 + "\n")
# MAGIC                 
# MAGIC                 # Add compact results info to message
# MAGIC                 final_message_parts.append(f"\n📊 **Query Results:** {df.shape[0]} rows × {df.shape[1]} columns")
# MAGIC                 final_message_parts.append(f"\nPreview (first 5 rows):\n```\n{df.head().to_string()}\n```")
# MAGIC                 
# MAGIC             except Exception as e:
# MAGIC                 final_message_parts.append(f"\n⚠️ Could not format results: {e}")
# MAGIC                 final_message_parts.append(f"Raw results (first 3): {results[:3]}")
# MAGIC     
# MAGIC     # 2. Error messages (if any) 
# MAGIC     if state.get("synthesis_error"):
# MAGIC         final_message_parts.append(f"\n❌ **SQL Synthesis Error:** {state['synthesis_error']}")
# MAGIC     if state.get("execution_error"):
# MAGIC         final_message_parts.append(f"\n❌ **Execution Error:** {state['execution_error']}")
# MAGIC     
# MAGIC     # Combine into final message (results/errors only - summary in final_summary field)
# MAGIC     # If no results or errors, use a simple completion message
# MAGIC     final_message = "\n".join(final_message_parts) if final_message_parts else "✅ Execution complete"
# MAGIC     
# MAGIC     print(f"\n✅ AIMessage created with results/errors ({len(final_message)} chars)")
# MAGIC     print(f"✅ Summary stored in final_summary field ({len(summary)} chars)")
# MAGIC     
# MAGIC     # Route to END via fixed edge (summarize → END)
# MAGIC     # Return: final_summary (displayed once) + AIMessage (results/errors only)
# MAGIC     return {
# MAGIC         "final_summary": summary,
# MAGIC         "messages": [
# MAGIC             AIMessage(content=final_message)
# MAGIC         ]
# MAGIC     }
# MAGIC
# MAGIC print("✓ All node wrappers defined (including summarize)")
# MAGIC
# MAGIC # ==============================================================================
# MAGIC # Business Logic Integration Hooks (NEW)
# MAGIC # ==============================================================================
# MAGIC
# MAGIC class BusinessLogicIntegration:
# MAGIC     """
# MAGIC     Example integration points for business logic using intent metadata.
# MAGIC     
# MAGIC     This demonstrates how to use the intent_metadata from intent detection
# MAGIC     for billing, analytics, routing, and personalization.
# MAGIC     """
# MAGIC     
# MAGIC     @staticmethod
# MAGIC     def calculate_usage_cost(state: AgentState) -> Dict[str, Any]:
# MAGIC         """Calculate usage cost based on intent type and complexity."""
# MAGIC         intent_metadata = state.get("intent_metadata", {})
# MAGIC         intent_type = intent_metadata.get("intent_type", "new_question")
# MAGIC         complexity = intent_metadata.get("complexity", "moderate")
# MAGIC         
# MAGIC         base_rates = {
# MAGIC             "new_question": 0.10,
# MAGIC             "refinement": 0.05,
# MAGIC             "continuation": 0.07,
# MAGIC             "clarification_response": 0.00
# MAGIC         }
# MAGIC         complexity_multipliers = {"simple": 1.0, "moderate": 1.5, "complex": 2.0}
# MAGIC         
# MAGIC         base_cost = base_rates.get(intent_type, 0.10)
# MAGIC         multiplier = complexity_multipliers.get(complexity, 1.5)
# MAGIC         
# MAGIC         return {
# MAGIC             "total_cost": base_cost * multiplier,
# MAGIC             "intent_type": intent_type,
# MAGIC             "complexity": complexity
# MAGIC         }
# MAGIC     
# MAGIC     @staticmethod
# MAGIC     def log_analytics_event(state: AgentState) -> Dict[str, Any]:
# MAGIC         """Log analytics event for conversation analysis."""
# MAGIC         intent_metadata = state.get("intent_metadata", {})
# MAGIC         current_turn = state.get("current_turn", {})
# MAGIC         turn_history = state.get("turn_history", [])
# MAGIC         
# MAGIC         event = {
# MAGIC             "event_type": "query_processed",
# MAGIC             "intent_type": intent_metadata.get("intent_type"),
# MAGIC             "complexity": intent_metadata.get("complexity"),
# MAGIC             "turn_count": len(turn_history),
# MAGIC             "clarification_requested": state.get("pending_clarification") is not None
# MAGIC         }
# MAGIC         print(f"📊 Analytics: {event['intent_type']} | {event['complexity']}")
# MAGIC         return event
# MAGIC
# MAGIC print("✓ Business logic integration hooks defined")
# MAGIC
# MAGIC def create_super_agent_hybrid():
# MAGIC     """
# MAGIC     Create the Hybrid Super Agent LangGraph workflow.
# MAGIC     
# MAGIC     Combines:
# MAGIC     - OOP agent classes for modularity
# MAGIC     - Explicit state management for observability
# MAGIC     """
# MAGIC     print("\n" + "="*80)
# MAGIC     print("🏗️ BUILDING HYBRID SUPER AGENT WORKFLOW")
# MAGIC     print("="*80)
# MAGIC     
# MAGIC     # Create the graph with explicit state
# MAGIC     workflow = StateGraph(AgentState)
# MAGIC     
# MAGIC     # Add nodes - SIMPLIFIED with unified node
# MAGIC     workflow.add_node("unified_intent_context_clarification", unified_intent_context_clarification_node)  # NEW: Unified node
# MAGIC     workflow.add_node("planning", planning_node)
# MAGIC     workflow.add_node("sql_synthesis_table", sql_synthesis_table_node)
# MAGIC     workflow.add_node("sql_synthesis_genie", sql_synthesis_genie_node)
# MAGIC     workflow.add_node("sql_execution", sql_execution_node)
# MAGIC     workflow.add_node("summarize", summarize_node)  # Final summarization node
# MAGIC     
# MAGIC     # Define routing logic based on explicit state
# MAGIC     def route_after_unified(state: AgentState) -> str:
# MAGIC         """Route after unified node: planning or END (clarification)"""
# MAGIC         if state.get("question_clear", False):
# MAGIC             return "planning"
# MAGIC         return END  # End if clarification needed
# MAGIC     
# MAGIC     def route_after_planning(state: AgentState) -> str:
# MAGIC         next_agent = state.get("next_agent", "summarize")
# MAGIC         if next_agent == "sql_synthesis_table":
# MAGIC             return "sql_synthesis_table"
# MAGIC         elif next_agent == "sql_synthesis_genie":
# MAGIC             return "sql_synthesis_genie"
# MAGIC         return "summarize"
# MAGIC     
# MAGIC     def route_after_synthesis(state: AgentState) -> str:
# MAGIC         next_agent = state.get("next_agent", "summarize")
# MAGIC         if next_agent == "sql_execution":
# MAGIC             return "sql_execution"
# MAGIC         return "summarize"  # Summarize if synthesis error
# MAGIC     
# MAGIC     # Add edges with conditional routing
# MAGIC     # NEW: Entry point is now unified node
# MAGIC     workflow.set_entry_point("unified_intent_context_clarification")
# MAGIC     
# MAGIC     # Route from unified node to planning or END (clarification)
# MAGIC     workflow.add_conditional_edges(
# MAGIC         "unified_intent_context_clarification",
# MAGIC         route_after_unified,
# MAGIC         {
# MAGIC             "planning": "planning",
# MAGIC             END: END
# MAGIC         }
# MAGIC     )
# MAGIC     
# MAGIC     workflow.add_conditional_edges(
# MAGIC         "planning",
# MAGIC         route_after_planning,
# MAGIC         {
# MAGIC             "sql_synthesis_table": "sql_synthesis_table",
# MAGIC             "sql_synthesis_genie": "sql_synthesis_genie",
# MAGIC             "summarize": "summarize"
# MAGIC         }
# MAGIC     )
# MAGIC     
# MAGIC     workflow.add_conditional_edges(
# MAGIC         "sql_synthesis_table",
# MAGIC         route_after_synthesis,
# MAGIC         {
# MAGIC             "sql_execution": "sql_execution",
# MAGIC             "summarize": "summarize"
# MAGIC         }
# MAGIC     )
# MAGIC     
# MAGIC     workflow.add_conditional_edges(
# MAGIC         "sql_synthesis_genie",
# MAGIC         route_after_synthesis,
# MAGIC         {
# MAGIC             "sql_execution": "sql_execution",
# MAGIC             "summarize": "summarize"
# MAGIC         }
# MAGIC     )
# MAGIC     
# MAGIC     # SQL execution always goes to summarize
# MAGIC     workflow.add_edge("sql_execution", "summarize")
# MAGIC     
# MAGIC     # Summarize is the final node before END
# MAGIC     workflow.add_edge("summarize", END)
# MAGIC     
# MAGIC     # NOTE: Workflow compiled WITHOUT checkpointer here
# MAGIC     # Checkpointer will be added at runtime in SuperAgentHybridResponsesAgent
# MAGIC     # This allows distributed Model Serving with CheckpointSaver
# MAGIC     app_graph = workflow
# MAGIC     
# MAGIC     print("✓ Workflow nodes added:")
# MAGIC     print("  1. Unified Intent+Context+Clarification Node (SIMPLIFIED - no kumc_poc imports)")
# MAGIC     print("  2. Planning Agent (OOP)")
# MAGIC     print("  3. SQL Synthesis Agent - Table Route (OOP)")
# MAGIC     print("  4. SQL Synthesis Agent - Genie Route (OOP)")
# MAGIC     print("  5. SQL Execution Agent (OOP)")
# MAGIC     print("  6. Result Summarize Agent (OOP) - FINAL NODE")
# MAGIC     print("\n✓ Single LLM call for intent + context + clarity (faster, cheaper)")
# MAGIC     print("✓ Smart clarification rate limiting (1 per 5 turns, sliding window)")
# MAGIC     print("✓ Self-contained implementation (no external imports)")
# MAGIC     print("✓ Conditional routing configured")
# MAGIC     print("✓ All paths route to summarize node before END")
# MAGIC     print("✓ Checkpointer will be added at runtime (distributed serving)")
# MAGIC     print("\n✅ Simplified Hybrid Super Agent workflow created successfully!")
# MAGIC     print("="*80)
# MAGIC     
# MAGIC     return app_graph
# MAGIC
# MAGIC # Create the Hybrid Super Agent
# MAGIC super_agent_hybrid = create_super_agent_hybrid()
# MAGIC class SuperAgentHybridResponsesAgent(ResponsesAgent):
# MAGIC     """
# MAGIC     Enhanced ResponsesAgent with both short-term and long-term memory for distributed Model Serving.
# MAGIC     
# MAGIC     Features:
# MAGIC     - Short-term memory (CheckpointSaver): Multi-turn conversations within a session
# MAGIC     - Long-term memory (DatabricksStore): User preferences across sessions with semantic search
# MAGIC     - Connection pooling and automatic credential rotation
# MAGIC     - Works seamlessly in distributed Model Serving (multiple instances)
# MAGIC     
# MAGIC     Memory Architecture:
# MAGIC     - Short-term: Stored per thread_id in Lakebase checkpoints table
# MAGIC     - Long-term: Stored per user_id in Lakebase store table with vector embeddings
# MAGIC     """
# MAGIC     
# MAGIC     def __init__(self, workflow: StateGraph):
# MAGIC         """
# MAGIC         Initialize the ResponsesAgent wrapper.
# MAGIC         
# MAGIC         Args:
# MAGIC             workflow: The uncompiled LangGraph StateGraph workflow
# MAGIC         """
# MAGIC         self.workflow = workflow
# MAGIC         self.lakebase_instance_name = LAKEBASE_INSTANCE_NAME
# MAGIC         self._store = None
# MAGIC         self._memory_tools = None
# MAGIC         print("✓ SuperAgentHybridResponsesAgent initialized with memory support")
# MAGIC     
# MAGIC     @property
# MAGIC     def store(self):
# MAGIC         """Lazy initialization of DatabricksStore for long-term memory."""
# MAGIC         if self._store is None:
# MAGIC             logger.info(f"Initializing DatabricksStore with instance: {self.lakebase_instance_name}")
# MAGIC             self._store = DatabricksStore(
# MAGIC                 instance_name=self.lakebase_instance_name,
# MAGIC                 embedding_endpoint=EMBEDDING_ENDPOINT,
# MAGIC                 embedding_dims=EMBEDDING_DIMS,
# MAGIC             )
# MAGIC             self._store.setup()  # Creates store table if not exists
# MAGIC             logger.info("✓ DatabricksStore initialized")
# MAGIC         return self._store
# MAGIC     
# MAGIC     @property
# MAGIC     def memory_tools(self):
# MAGIC         """Create memory tools for long-term memory access."""
# MAGIC         if self._memory_tools is None:
# MAGIC             logger.info("Creating memory tools for long-term memory")
# MAGIC             
# MAGIC             @tool
# MAGIC             def get_user_memory(query: str, config: RunnableConfig) -> str:
# MAGIC                 """Search for relevant user information using semantic search.
# MAGIC                 
# MAGIC                 Use this tool to retrieve previously saved information about the user,
# MAGIC                 such as their preferences, facts they've shared, or other personal details.
# MAGIC                 
# MAGIC                 Args:
# MAGIC                     query: The search query to find relevant memories
# MAGIC                     config: Runtime configuration containing user_id
# MAGIC                 """
# MAGIC                 user_id = config.get("configurable", {}).get("user_id")
# MAGIC                 if not user_id:
# MAGIC                     return "Memory not available - no user_id provided."
# MAGIC                 
# MAGIC                 namespace = ("user_memories", user_id.replace(".", "-"))
# MAGIC                 results = self.store.search(namespace, query=query, limit=5)
# MAGIC                 
# MAGIC                 if not results:
# MAGIC                     return "No memories found for this user."
# MAGIC                 
# MAGIC                 memory_items = [f"- [{item.key}]: {json.dumps(item.value)}" for item in results]
# MAGIC                 return f"Found {len(results)} relevant memories (ranked by similarity):\n" + "\n".join(memory_items)
# MAGIC             
# MAGIC             @tool
# MAGIC             def save_user_memory(memory_key: str, memory_data_json: str, config: RunnableConfig) -> str:
# MAGIC                 """Save information about the user to long-term memory.
# MAGIC                 
# MAGIC                 Use this tool to remember important information the user shares,
# MAGIC                 such as preferences, facts, or other personal details.
# MAGIC                 
# MAGIC                 Args:
# MAGIC                     memory_key: A descriptive key for this memory (e.g., "preferences", "favorite_visualization")
# MAGIC                     memory_data_json: JSON string with the information to remember. 
# MAGIC                         Example: '{"preferred_chart_type": "bar", "default_spaces": ["patient_data"]}'
# MAGIC                     config: Runtime configuration containing user_id
# MAGIC                 """
# MAGIC                 user_id = config.get("configurable", {}).get("user_id")
# MAGIC                 if not user_id:
# MAGIC                     return "Cannot save memory - no user_id provided."
# MAGIC                 
# MAGIC                 namespace = ("user_memories", user_id.replace(".", "-"))
# MAGIC                 
# MAGIC                 try:
# MAGIC                     memory_data = json.loads(memory_data_json)
# MAGIC                     if not isinstance(memory_data, dict):
# MAGIC                         return f"Failed: memory_data must be a JSON object, not {type(memory_data).__name__}"
# MAGIC                     self.store.put(namespace, memory_key, memory_data)
# MAGIC                     return f"Successfully saved memory with key '{memory_key}' for user"
# MAGIC                 except json.JSONDecodeError as e:
# MAGIC                     return f"Failed to save memory: Invalid JSON format - {str(e)}"
# MAGIC             
# MAGIC             @tool
# MAGIC             def delete_user_memory(memory_key: str, config: RunnableConfig) -> str:
# MAGIC                 """Delete a specific memory from the user's long-term memory.
# MAGIC                 
# MAGIC                 Use this when the user asks you to forget something or remove
# MAGIC                 a piece of information from their memory.
# MAGIC                 
# MAGIC                 Args:
# MAGIC                     memory_key: The key of the memory to delete
# MAGIC                     config: Runtime configuration containing user_id
# MAGIC                 """
# MAGIC                 user_id = config.get("configurable", {}).get("user_id")
# MAGIC                 if not user_id:
# MAGIC                     return "Cannot delete memory - no user_id provided."
# MAGIC                 
# MAGIC                 namespace = ("user_memories", user_id.replace(".", "-"))
# MAGIC                 self.store.delete(namespace, memory_key)
# MAGIC                 return f"Successfully deleted memory with key '{memory_key}' for user"
# MAGIC             
# MAGIC             self._memory_tools = [get_user_memory, save_user_memory, delete_user_memory]
# MAGIC             logger.info(f"✓ Created {len(self._memory_tools)} memory tools")
# MAGIC         
# MAGIC         return self._memory_tools
# MAGIC     
# MAGIC     def _get_or_create_thread_id(self, request: ResponsesAgentRequest) -> str:
# MAGIC         """Get thread_id from request or create a new one.
# MAGIC         
# MAGIC         Priority:
# MAGIC         1. Use thread_id from custom_inputs if present
# MAGIC         2. Use conversation_id from chat context if available
# MAGIC         3. Generate a new UUID
# MAGIC         """
# MAGIC         ci = dict(request.custom_inputs or {})
# MAGIC         
# MAGIC         if "thread_id" in ci:
# MAGIC             return ci["thread_id"]
# MAGIC         
# MAGIC         # Use conversation_id from ChatContext as thread_id
# MAGIC         if request.context and getattr(request.context, "conversation_id", None):
# MAGIC             return request.context.conversation_id
# MAGIC         
# MAGIC         # Generate new thread_id
# MAGIC         return str(uuid4())
# MAGIC     
# MAGIC     def _get_user_id(self, request: ResponsesAgentRequest) -> Optional[str]:
# MAGIC         """Extract user_id from request context.
# MAGIC         
# MAGIC         Priority:
# MAGIC         1. Use user_id from chat context (preferred for Model Serving)
# MAGIC         2. Use user_id from custom_inputs
# MAGIC         """
# MAGIC         if request.context and getattr(request.context, "user_id", None):
# MAGIC             return request.context.user_id
# MAGIC         
# MAGIC         if request.custom_inputs and "user_id" in request.custom_inputs:
# MAGIC             return request.custom_inputs["user_id"]
# MAGIC         
# MAGIC         return None
# MAGIC     
# MAGIC     def make_json_serializable(self, obj):
# MAGIC         """
# MAGIC         Convert LangChain objects and other non-serializable objects to JSON-serializable format.
# MAGIC         
# MAGIC         Args:
# MAGIC             obj: Object to convert
# MAGIC             
# MAGIC         Returns:
# MAGIC             JSON-serializable version of the object
# MAGIC         """
# MAGIC         from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, ToolMessage, AIMessageChunk
# MAGIC         from uuid import UUID
# MAGIC         
# MAGIC         # Handle None
# MAGIC         if obj is None:
# MAGIC             return None
# MAGIC         
# MAGIC         # Handle UUID objects
# MAGIC         if isinstance(obj, UUID):
# MAGIC             return str(obj)
# MAGIC         
# MAGIC         # Handle bytes
# MAGIC         if isinstance(obj, bytes):
# MAGIC             try:
# MAGIC                 return obj.decode('utf-8', errors='ignore')
# MAGIC             except:
# MAGIC                 return f"<bytes:{len(obj)}>"
# MAGIC         
# MAGIC         # Handle set
# MAGIC         if isinstance(obj, set):
# MAGIC             return [self.make_json_serializable(item) for item in obj]
# MAGIC         
# MAGIC         # Handle LangChain message objects
# MAGIC         if isinstance(obj, BaseMessage):
# MAGIC             msg_dict = {
# MAGIC                 "type": obj.__class__.__name__,
# MAGIC                 "content": str(obj.content) if obj.content else ""
# MAGIC             }
# MAGIC             if hasattr(obj, 'id') and obj.id:
# MAGIC                 msg_dict["id"] = str(obj.id)
# MAGIC             if hasattr(obj, 'name') and obj.name:
# MAGIC                 msg_dict["name"] = obj.name
# MAGIC             if hasattr(obj, 'tool_calls') and obj.tool_calls:
# MAGIC                 # Recursively serialize tool calls
# MAGIC                 msg_dict["tool_calls"] = [
# MAGIC                     self.make_json_serializable(tc) for tc in obj.tool_calls[:2]
# MAGIC                 ]  # Limit to 2 for brevity
# MAGIC             return msg_dict
# MAGIC         
# MAGIC         # Handle dictionaries recursively
# MAGIC         if isinstance(obj, dict):
# MAGIC             return {str(k): self.make_json_serializable(v) for k, v in obj.items()}
# MAGIC         
# MAGIC         # Handle lists and tuples recursively
# MAGIC         if isinstance(obj, (list, tuple)):
# MAGIC             return [self.make_json_serializable(item) for item in obj]
# MAGIC         
# MAGIC         # Handle primitives
# MAGIC         if isinstance(obj, (str, int, float, bool)):
# MAGIC             return obj
# MAGIC         
# MAGIC         # For anything else, convert to string representation
# MAGIC         try:
# MAGIC             return str(obj)
# MAGIC         except Exception:
# MAGIC             return f"<{type(obj).__name__}>"
# MAGIC     
# MAGIC     def format_custom_event(self, custom_data: dict) -> str:
# MAGIC         """
# MAGIC         Format custom streaming events for user-friendly display.
# MAGIC         
# MAGIC         Args:
# MAGIC             custom_data: Dictionary containing custom event data with 'type' key
# MAGIC             
# MAGIC         Returns:
# MAGIC             Formatted string with emoji and readable event description
# MAGIC         """
# MAGIC         event_type = custom_data.get("type", "unknown")
# MAGIC         
# MAGIC         formatters = {
# MAGIC             "agent_thinking": lambda d: f"💭 {d['agent'].upper()}: {d['content']}",
# MAGIC             "agent_start": lambda d: f"🚀 Starting {d['agent']} agent for: {d.get('query', '')[:50]}...",
# MAGIC             "intent_detection": lambda d: f"🎯 Intent: {d['result']} - {d.get('reasoning', '')}",
# MAGIC             "clarity_analysis": lambda d: f"✓ Query {'clear' if d['clear'] else 'unclear'}: {d.get('reasoning', '')}",
# MAGIC             "vector_search_start": lambda d: f"🔍 Searching vector index: {d['index']}",
# MAGIC             "vector_search_results": lambda d: f"📊 Found {d['count']} relevant spaces: {[s.get('space_id', 'unknown') for s in d.get('spaces', [])]}",
# MAGIC             "plan_formulation": lambda d: f"📋 Execution plan: {d.get('strategy', 'unknown')} strategy",
# MAGIC             "uc_function_call": lambda d: f"🔧 Calling UC function: {d['function']}",
# MAGIC             "sql_generated": lambda d: f"📝 SQL generated: {d.get('query_preview', '')}...",
# MAGIC             "sql_validation_start": lambda d: f"✅ Validating SQL query...",
# MAGIC             "sql_execution_start": lambda d: f"⚡ Executing SQL query...",
# MAGIC             "sql_execution_complete": lambda d: f"✓ Query complete: {d.get('rows', 0)} rows, {len(d.get('columns', []))} columns",
# MAGIC             "summary_start": lambda d: f"📄 Generating summary...",
# MAGIC             "genie_agent_call": lambda d: f"🤖 Calling Genie agent for space: {d.get('space_id', 'unknown')}",
# MAGIC         }
# MAGIC         
# MAGIC         # Bulletproof JSON fallback handler
# MAGIC         def json_fallback(obj):
# MAGIC             """Final fallback for json.dumps() - converts anything to string."""
# MAGIC             try:
# MAGIC                 return str(obj)
# MAGIC             except:
# MAGIC                 return f"<{type(obj).__name__}>"
# MAGIC         
# MAGIC         # Fallback formatter now uses make_json_serializable with json_fallback
# MAGIC         formatter = formatters.get(
# MAGIC             event_type,
# MAGIC             lambda d: f"ℹ️ {event_type}: {json.dumps(self.make_json_serializable(d), indent=2, default=json_fallback)}"
# MAGIC         )
# MAGIC         
# MAGIC         try:
# MAGIC             return formatter(custom_data)
# MAGIC         except Exception as e:
# MAGIC             logger.warning(f"Error formatting custom event {event_type}: {e}")
# MAGIC             # Enhanced error handling with serialization fallback
# MAGIC             try:
# MAGIC                 serialized = self.make_json_serializable(custom_data)
# MAGIC                 return f"ℹ️ {event_type}: {json.dumps(serialized, indent=2, default=json_fallback)}"
# MAGIC             except Exception as e2:
# MAGIC                 logger.warning(f"Error serializing custom event {event_type}: {e2}")
# MAGIC                 return f"ℹ️ {event_type}: {str(custom_data)}"
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
# MAGIC         Make a streaming prediction with both short-term and long-term memory.
# MAGIC         
# MAGIC         SIMPLIFIED API: All conversation turns use the same simple format.
# MAGIC         The agent auto-detects clarification responses and follow-ups from message history.
# MAGIC         
# MAGIC         Memory Systems:
# MAGIC         - Short-term (CheckpointSaver): Preserves conversation state across distributed instances
# MAGIC         - Long-term (DatabricksStore): User preferences accessible via memory tools
# MAGIC         
# MAGIC         Args:
# MAGIC             request: The request containing:
# MAGIC                 - input: List of messages (user query is the last message)
# MAGIC                 - context.conversation_id: Used as thread_id (preferred)
# MAGIC                 - context.user_id: Used for long-term memory (preferred)
# MAGIC                 - custom_inputs: Dict with optional keys:
# MAGIC                     - thread_id (str): Thread identifier override
# MAGIC                     - user_id (str): User identifier override
# MAGIC             
# MAGIC         Yields:
# MAGIC             ResponsesAgentStreamEvent for each step in the workflow
# MAGIC             
# MAGIC         Usage in Model Serving (ALL scenarios use same format):
# MAGIC             # First query in a conversation
# MAGIC             POST /invocations
# MAGIC             {
# MAGIC                 "messages": [{"role": "user", "content": "Show me patient data"}],
# MAGIC                 "context": {
# MAGIC                     "conversation_id": "session_001",
# MAGIC                     "user_id": "user@example.com"
# MAGIC                 }
# MAGIC             }
# MAGIC             
# MAGIC             # Clarification response (SIMPLIFIED - auto-detected!)
# MAGIC             POST /invocations
# MAGIC             {
# MAGIC                 "messages": [{"role": "user", "content": "Patient count by age group"}],
# MAGIC                 "context": {
# MAGIC                     "conversation_id": "session_001",  # Same thread_id
# MAGIC                     "user_id": "user@example.com"
# MAGIC                 }
# MAGIC             }
# MAGIC             
# MAGIC             # Follow-up query (agent remembers context automatically)
# MAGIC             POST /invocations
# MAGIC             {
# MAGIC                 "messages": [{"role": "user", "content": "Now show by gender"}],
# MAGIC                 "context": {
# MAGIC                     "conversation_id": "session_001",  # Same thread_id
# MAGIC                     "user_id": "user@example.com"
# MAGIC                 }
# MAGIC             }
# MAGIC         """
# MAGIC         # Get identifiers
# MAGIC         thread_id = self._get_or_create_thread_id(request)
# MAGIC         user_id = self._get_user_id(request)
# MAGIC         
# MAGIC         # Update custom_inputs with resolved identifiers
# MAGIC         ci = dict(request.custom_inputs or {})
# MAGIC         ci["thread_id"] = thread_id
# MAGIC         if user_id:
# MAGIC             ci["user_id"] = user_id
# MAGIC         request.custom_inputs = ci
# MAGIC         
# MAGIC         logger.info(f"Processing request - thread_id: {thread_id}, user_id: {user_id}")
# MAGIC         
# MAGIC         # Ensure MLflow tracing doesn't cause issues in streaming context
# MAGIC         # This safeguard prevents NonRecordingSpan context attribute errors
# MAGIC         try:
# MAGIC             import mlflow.tracing
# MAGIC             # Verify tracing is properly initialized, otherwise disable to prevent errors
# MAGIC             if not hasattr(mlflow.tracing, '_is_enabled') or not mlflow.tracing._is_enabled():
# MAGIC                 logger.debug("MLflow tracing not enabled, continuing without tracing")
# MAGIC         except Exception as e:
# MAGIC             logger.debug(f"MLflow tracing check skipped: {e}")
# MAGIC         
# MAGIC         # Convert request input to chat completions format
# MAGIC         cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
# MAGIC         
# MAGIC         # Get the latest user message
# MAGIC         latest_query = cc_msgs[-1]["content"] if cc_msgs else ""
# MAGIC         
# MAGIC         # Configure runtime with thread_id and user_id
# MAGIC         run_config = {"configurable": {"thread_id": thread_id}}
# MAGIC         if user_id:
# MAGIC             run_config["configurable"]["user_id"] = user_id
# MAGIC         
# MAGIC         # SIMPLIFIED: Unified state initialization for all scenarios
# MAGIC         # CheckpointSaver will restore previous conversation context automatically
# MAGIC         # The intent_detection_node runs first and creates current_turn
# MAGIC         initial_state = {
# MAGIC             **RESET_STATE_TEMPLATE,  # Reset all per-query execution fields
# MAGIC             "original_query": latest_query,
# MAGIC             "messages": [
# MAGIC                 SystemMessage(content="""You are a multi-agent Q&A analysis system.
# MAGIC Your role is to help users query and analyze cross-domain data.
# MAGIC
# MAGIC Guidelines:
# MAGIC - Always explain your reasoning and execution plan
# MAGIC - Validate SQL queries before execution
# MAGIC - Provide clear, comprehensive summaries
# MAGIC - If information is missing, ask for clarification (max once)
# MAGIC - Use UC functions and Genie agents to generate accurate SQL
# MAGIC - Return results with proper context and explanations"""),
# MAGIC                 HumanMessage(content=latest_query)
# MAGIC             ]
# MAGIC             # NOTE: current_turn, intent_metadata, turn_history are NOT in RESET_STATE_TEMPLATE
# MAGIC             # They are managed by unified_intent_context_clarification_node and persist via CheckpointSaver
# MAGIC         }
# MAGIC         
# MAGIC         # Add user_id to state for long-term memory access
# MAGIC         if user_id:
# MAGIC             initial_state["user_id"] = user_id
# MAGIC             initial_state["thread_id"] = thread_id
# MAGIC         
# MAGIC         first_message = True
# MAGIC         seen_ids = set()
# MAGIC         
# MAGIC         # Execute workflow with CheckpointSaver for distributed serving
# MAGIC         # CRITICAL: CheckpointSaver as context manager ensures all instances share state
# MAGIC         with CheckpointSaver(instance_name=self.lakebase_instance_name) as checkpointer:
# MAGIC             # Compile graph with checkpointer at runtime
# MAGIC             # This allows distributed Model Serving to access shared state
# MAGIC             app = self.workflow.compile(checkpointer=checkpointer)
# MAGIC             
# MAGIC             logger.info(f"Executing workflow with checkpointer (thread: {thread_id})")
# MAGIC             
# MAGIC             # Stream the workflow execution with enhanced visibility modes
# MAGIC             # CheckpointSaver will:
# MAGIC             # 1. Restore previous state from thread_id (if exists) from Lakebase
# MAGIC             # 2. Merge with initial_state (initial_state takes precedence)
# MAGIC             # 3. Preserve conversation history across distributed instances
# MAGIC             # Stream modes:
# MAGIC             # - updates: State changes after each node
# MAGIC             # - messages: LLM token-by-token streaming
# MAGIC             # - custom: Agent-specific events (thinking, decisions, progress)
# MAGIC             for event in app.stream(initial_state, run_config, stream_mode=["updates", "messages", "custom"]):
# MAGIC                 event_type = event[0]
# MAGIC                 event_data = event[1]
# MAGIC                 
# MAGIC                 # Handle streaming text deltas (messages mode)
# MAGIC                 if event_type == "messages":
# MAGIC                     try:
# MAGIC                         # Extract the message chunk
# MAGIC                         chunk = event_data[0] if isinstance(event_data, (list, tuple)) else event_data
# MAGIC                         
# MAGIC                         # Stream text content as deltas for real-time visibility in Playground
# MAGIC                         if isinstance(chunk, AIMessageChunk) and (content := chunk.content):
# MAGIC                             yield ResponsesAgentStreamEvent(
# MAGIC                                 **self.create_text_delta(delta=content, item_id=chunk.id),
# MAGIC                             )
# MAGIC                     except Exception as e:
# MAGIC                         logger.warning(f"Error processing message chunk: {e}")
# MAGIC                 
# MAGIC                 # Handle node updates (updates mode)
# MAGIC                 elif event_type == "updates":
# MAGIC                     events = event_data
# MAGIC                     new_msgs = [
# MAGIC                         msg
# MAGIC                         for v in events.values()
# MAGIC                         for msg in v.get("messages", [])
# MAGIC                         if hasattr(msg, 'id') and msg.id not in seen_ids
# MAGIC                     ]
# MAGIC                     
# MAGIC                     if first_message:
# MAGIC                         seen_ids.update(msg.id for msg in new_msgs[: len(cc_msgs)])
# MAGIC                         new_msgs = new_msgs[len(cc_msgs) :]
# MAGIC                         first_message = False
# MAGIC                     else:
# MAGIC                         seen_ids.update(msg.id for msg in new_msgs)
# MAGIC                         # Emit node name as a step indicator with enhanced details
# MAGIC                         if events:
# MAGIC                             node_name = tuple(events.keys())[0]
# MAGIC                             node_update = events[node_name]
# MAGIC                             updated_keys = [k for k in node_update.keys() if k != "messages"]
# MAGIC                             
# MAGIC                             # Enhanced step indicator with state keys
# MAGIC                             step_text = f"🔹 Step: {node_name}"
# MAGIC                             if updated_keys:
# MAGIC                                 step_text += f" | Keys updated: {', '.join(updated_keys)}"
# MAGIC                             
# MAGIC                             yield ResponsesAgentStreamEvent(
# MAGIC                                 type="response.output_item.done",
# MAGIC                                 item=self.create_text_output_item(
# MAGIC                                     text=step_text, id=str(uuid4())
# MAGIC                                 ),
# MAGIC                             )
# MAGIC                             
# MAGIC                             # Emit routing decision if next_agent changed
# MAGIC                             if "next_agent" in node_update:
# MAGIC                                 next_agent = node_update["next_agent"]
# MAGIC                                 yield ResponsesAgentStreamEvent(
# MAGIC                                     type="response.output_item.done",
# MAGIC                                     item=self.create_text_output_item(
# MAGIC                                         text=f"🔀 Routing decision: Next agent = {next_agent}",
# MAGIC                                         id=str(uuid4())
# MAGIC                                     ),
# MAGIC                                 )
# MAGIC                     
# MAGIC                     # Process messages for tool calls, tool results, and final text
# MAGIC                     for msg in new_msgs:
# MAGIC                         # Check if message has tool calls
# MAGIC                         if hasattr(msg, 'tool_calls') and msg.tool_calls:
# MAGIC                             # Emit function call items for tool invocations
# MAGIC                             for tool_call in msg.tool_calls:
# MAGIC                                 try:
# MAGIC                                     yield ResponsesAgentStreamEvent(
# MAGIC                                         type="response.output_item.done",
# MAGIC                                         item=self.create_function_call_item(
# MAGIC                                             id=str(uuid4()),
# MAGIC                                             call_id=tool_call.get("id", str(uuid4())),
# MAGIC                                             name=tool_call.get("name", "unknown"),
# MAGIC                                             arguments=json.dumps(tool_call.get("args", {})),
# MAGIC                                         ),
# MAGIC                                     )
# MAGIC                                 except Exception as e:
# MAGIC                                     logger.warning(f"Error emitting tool call: {e}")
# MAGIC                         # Handle ToolMessage for tool results
# MAGIC                         elif hasattr(msg, '__class__') and msg.__class__.__name__ == 'ToolMessage':
# MAGIC                             try:
# MAGIC                                 tool_name = getattr(msg, 'name', 'unknown')
# MAGIC                                 tool_content = str(msg.content)[:200] if msg.content else "No content"
# MAGIC                                 yield ResponsesAgentStreamEvent(
# MAGIC                                     type="response.output_item.done",
# MAGIC                                     item=self.create_text_output_item(
# MAGIC                                         text=f"🔨 Tool result ({tool_name}): {tool_content}...",
# MAGIC                                         id=str(uuid4())
# MAGIC                                     ),
# MAGIC                                 )
# MAGIC                             except Exception as e:
# MAGIC                                 logger.warning(f"Error emitting tool result: {e}")
# MAGIC                         else:
# MAGIC                             # Emit regular message content
# MAGIC                             yield from output_to_responses_items_stream([msg])
# MAGIC                 
# MAGIC                 # Handle custom mode (agent-specific events)
# MAGIC                 elif event_type == "custom":
# MAGIC                     try:
# MAGIC                         custom_data = event_data
# MAGIC                         formatted_text = self.format_custom_event(custom_data)
# MAGIC                         yield ResponsesAgentStreamEvent(
# MAGIC                             type="response.output_item.done",
# MAGIC                             item=self.create_text_output_item(
# MAGIC                                 text=formatted_text,
# MAGIC                                 id=str(uuid4())
# MAGIC                             ),
# MAGIC                         )
# MAGIC                     except Exception as e:
# MAGIC                         logger.warning(f"Error processing custom event: {e}")
# MAGIC         
# MAGIC         logger.info(f"Workflow execution completed (thread: {thread_id})")
# MAGIC
# MAGIC
# MAGIC # Create the deployable agent
# MAGIC AGENT = SuperAgentHybridResponsesAgent(super_agent_hybrid)
# MAGIC
# MAGIC print("\n" + "="*80)
# MAGIC print("✅ HYBRID SUPER AGENT RESPONSES AGENT CREATED")
# MAGIC print("="*80)
# MAGIC print("Architecture: OOP Agents + Explicit State Management")
# MAGIC print("Benefits:")
# MAGIC print("  ✓ Modular and testable agent classes")
# MAGIC print("  ✓ Full state observability for debugging")
# MAGIC print("  ✓ Production-ready with development-friendly design")
# MAGIC print("\nThis agent is now ready for:")
# MAGIC print("  1. Local testing with AGENT.predict()")
# MAGIC print("  2. Logging with mlflow.pyfunc.log_model()")
# MAGIC print("  3. Deployment to Databricks Model Serving")
# MAGIC print("\nMemory Features:")
# MAGIC print("  ✓ Short-term memory: Multi-turn conversations (CheckpointSaver)")
# MAGIC print("  ✓ Long-term memory: User preferences (DatabricksStore)")
# MAGIC print("  ✓ Works in distributed Model Serving (shared state via Lakebase)")
# MAGIC print("="*80)
# MAGIC print("\n🎉 Enhanced Granular Streaming Features:")
# MAGIC print("  ✓ Agent thinking and reasoning visibility")
# MAGIC print("  ✓ Intent detection (new question vs follow-up)")
# MAGIC print("  ✓ Clarity analysis with reasoning")
# MAGIC print("  ✓ Vector search progress and results")
# MAGIC print("  ✓ Execution plan formulation")
# MAGIC print("  ✓ UC function calls and Genie agent invocations")
# MAGIC print("  ✓ SQL generation progress")
# MAGIC print("  ✓ SQL validation and execution progress")
# MAGIC print("  ✓ Tool calls and tool results")
# MAGIC print("  ✓ Routing decisions between agents")
# MAGIC print("  ✓ Summary generation progress")
# MAGIC print("  ✓ Custom events for detailed execution tracking")
# MAGIC print("="*80)
# MAGIC
# MAGIC # Set the agent for MLflow tracking
# MAGIC # Enable autologging with run_tracer_inline for proper async context propagation
# MAGIC try:
# MAGIC     mlflow.langchain.autolog(run_tracer_inline=True)
# MAGIC     logger.info("✓ MLflow LangChain autologging enabled with async context support")
# MAGIC except Exception as e:
# MAGIC     logger.warning(f"⚠️ MLflow autolog initialization failed: {e}")
# MAGIC     logger.warning("Continuing without MLflow tracing...")
# MAGIC
# MAGIC mlflow.models.set_model(AGENT)

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

Stream modes enabled:
- updates: State changes after each node
- messages: LLM token-by-token streaming  
- custom: Agent-specific events (thinking, decisions, progress)
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
event_counts = {"custom": 0, "updates": 0, "messages": 0, "tool_calls": 0, "tool_results": 0, "routing": 0}

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
follow_up_msg =  "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? use Genie route"
# thread_id = f"test-streaming-{str(uuid4())[:8]}"
print("thread_id in use:", thread_id)
# follow up of thread from above, update here
# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": f"{thread_id}"}
))

# COMMAND ----------

follow_up_msg =  "now I want to include extra filter for members also diagnosed with CHF"
# thread_id = f"test-streaming-{str(uuid4())[:8]}"
print("thread_id in use:", thread_id)
# follow up of thread from above, update here
# First message
result1 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{follow_up_msg}"}],
    custom_inputs={"thread_id": f"{thread_id}"}
))

# COMMAND ----------

# DBTITLE 1,new thread new topic
follow_up_msg =  "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? use table route"
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
follow_up_msg =  "you decide"
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
for space_id in GENIE_SPACE_IDS:
    print(f"  - {space_id}")

# 2. SQL Warehouse ID (from config - already loaded at line 65)
print("\n[2/4] SQL Warehouse ID:")
print(f"  ✓ Using SQL Warehouse ID from config: {SQL_WAREHOUSE_ID}")
print(f"  Source: config.table_metadata.sql_warehouse_id")
print(f"  Configured via: .env file → SQL_WAREHOUSE_ID environment variable")
if not SQL_WAREHOUSE_ID:
    print("  ⚠️ WARNING: SQL_WAREHOUSE_ID is empty!")
    print("  Set it in .env file: SQL_WAREHOUSE_ID=your-warehouse-id")
    print("  Get warehouse ID from: SQL Warehouses UI → Click warehouse → Copy ID from URL or Details")

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
            f"databricks-sdk=={get_distribution('databricks-sdk').version}",  # Required for Config() unified auth
            f"databricks-sql-connector=={get_distribution('databricks-sql-connector').version}",  # Required for SQLExecutionAgent
            f"databricks-langchain[memory]=={get_distribution('databricks-langchain').version}",
            f"databricks-agents=={get_distribution('databricks-agents').version}",
            f"databricks-vectorsearch=={get_distribution('databricks-vectorsearch').version}",
            f"langgraph=={get_distribution('langgraph').version}",  # Required for StateGraph
            f"mlflow[databricks]=={mlflow.__version__}"
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
    
    # MLflow autologging is enabled globally at agent initialization
    # No need to call it again here to avoid context issues
    
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
# MAGIC - PlanningAgent
# MAGIC - SQLSynthesisTableAgent
# MAGIC - SQLSynthesisGenieAgent
# MAGIC - SQLExecutionAgent
# MAGIC - ResultSummarizeAgent
# MAGIC
# MAGIC Node Wrappers:
# MAGIC - unified_intent_context_clarification_node(state) → unified intent detection + context + clarity check
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

# DBTITLE 1,Test: Refactored SQLExecutionAgent with SQL Warehouse
# MAGIC %md
# MAGIC ### Test: SQLExecutionAgent with Databricks SQL Warehouse
# MAGIC
# MAGIC This test verifies the refactored SQLExecutionAgent works correctly with databricks-sql-connector.
# MAGIC The agent now uses SQL Warehouse instead of spark.sql, making it compatible with Model Serving.

# COMMAND ----------

# Test the refactored SQLExecutionAgent
print("="*80)
print("🧪 TESTING REFACTORED SQLExecutionAgent")
print("="*80)
print(f"Using SQL Warehouse ID: {SQL_WAREHOUSE_ID}")
print("="*80)

# Create test agent instance
test_agent = SQLExecutionAgent(warehouse_id=SQL_WAREHOUSE_ID)

# Test query - simple query to verify functionality
test_query = """
SELECT 
    'Test' as test_column,
    123 as number_column,
    CURRENT_DATE() as date_column
LIMIT 5
"""

print("\n📝 Test Query:")
print(test_query)
print("\n🔄 Executing test query...")

# Execute test query
result = test_agent.execute_sql(test_query, max_rows=5, return_format="dict")

# Verify results
print("\n" + "="*80)
print("📊 TEST RESULTS")
print("="*80)

if result["success"]:
    print("✅ SUCCESS: Query executed via SQL Warehouse")
    print(f"✓ Rows returned: {result['row_count']}")
    print(f"✓ Columns: {result['columns']}")
    print(f"✓ Result format: {type(result['result'])}")
    print(f"\n📄 Sample result:")
    if result['result']:
        print(f"   {result['result'][0]}")
    print("\n✅ SQLExecutionAgent refactoring SUCCESSFUL!")
    print("✅ Agent is now compatible with Model Serving")
else:
    print(f"❌ FAILED: {result.get('error', 'Unknown error')}")
    print("⚠️  Please check SQL Warehouse ID and permissions")

print("="*80)

# COMMAND ----------

# DBTITLE 1,Test: SQLExecutionAgent with Real Healthcare Query
# MAGIC %md
# MAGIC ### Test: Real Healthcare Query via SQL Warehouse
# MAGIC
# MAGIC Test with an actual healthcare data query to verify full functionality.

# COMMAND ----------

# Test with a real healthcare query
print("="*80)
print("🧪 TESTING WITH REAL HEALTHCARE QUERY")
print("="*80)

real_query = """
SELECT 
    COUNT(DISTINCT patient_id) as patient_count,
    COUNT(DISTINCT claim_id) as claim_count
FROM healthverity_claims_sample_patient_dataset.hv_claims_sample.diagnosis
LIMIT 10
"""

print("\n📝 Healthcare Query:")
print(real_query)
print("\n🔄 Executing via SQL Warehouse...")

result = test_agent.execute_sql(real_query, max_rows=10, return_format="dict")

print("\n" + "="*80)
print("📊 HEALTHCARE QUERY RESULTS")
print("="*80)

if result["success"]:
    print("✅ SUCCESS: Healthcare query executed")
    print(f"✓ Rows returned: {result['row_count']}")
    print(f"\n📄 Results:")
    for row in result['result']:
        print(f"   {row}")
    print("\n✅ FULL INTEGRATION TEST PASSED!")
else:
    print(f"❌ FAILED: {result.get('error', 'Unknown error')}")

print("="*80)

# COMMAND ----------

# DBTITLE 1,Production Deployment Guide for Model Serving
# MAGIC %md
# MAGIC ### 🚀 Production Deployment Guide: SQLExecutionAgent to Model Serving
# MAGIC
# MAGIC This guide shows how to deploy the SQLExecutionAgent to Databricks Model Serving with proper authentication configuration.
# MAGIC
# MAGIC #### Prerequisites
# MAGIC 1. SQL Warehouse ID configured in your `.env` file (for development) or Model Serving endpoint (for production)
# MAGIC 2. Databricks secrets scope created
# MAGIC 3. Access token stored in Databricks secrets
# MAGIC 4. SQL Warehouse ID obtained from: SQL Warehouses UI → Click warehouse → Copy ID from URL or Details
# MAGIC
# MAGIC #### Step 1: Store SQL Warehouse Access Token in Databricks Secrets
# MAGIC
# MAGIC ```bash
# MAGIC # Create a secret scope (if not exists)
# MAGIC databricks secrets create-scope sql_warehouse_scope
# MAGIC
# MAGIC # Store your access token
# MAGIC databricks secrets put-secret sql_warehouse_scope warehouse_token
# MAGIC # (you will be prompted to enter the token)
# MAGIC ```
# MAGIC
# MAGIC #### Step 2: Register Agent with Resources (Recommended - Automatic Authentication)
# MAGIC
# MAGIC **Best Practice: Use automatic authentication passthrough by registering resources**
# MAGIC
# MAGIC When you register resources during agent deployment, Databricks automatically handles authentication:
# MAGIC
# MAGIC ```python
# MAGIC from mlflow.models.resources import DatabricksSQLWarehouse
# MAGIC
# MAGIC resources = [
# MAGIC     DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
# MAGIC     # ... other resources (Vector Search, Genie Spaces, etc.)
# MAGIC ]
# MAGIC
# MAGIC mlflow.langchain.log_model(
# MAGIC     lc_model=agent,
# MAGIC     artifact_path="agent",
# MAGIC     resources=resources,  # Enables automatic authentication
# MAGIC     # ... other parameters
# MAGIC )
# MAGIC ```
# MAGIC
# MAGIC **What happens automatically:**
# MAGIC - Databricks creates a service principal for your agent
# MAGIC - OAuth credentials are injected into the Model Serving environment
# MAGIC - Credentials are automatically rotated (no manual management needed)
# MAGIC - Agent code uses `Config()` to read credentials transparently
# MAGIC
# MAGIC **No manual environment variable configuration needed!**
# MAGIC
# MAGIC #### Step 2 (Alternative): Manual Authentication Configuration (Legacy)
# MAGIC
# MAGIC If you're NOT using resource registration, manually configure environment variables:
# MAGIC
# MAGIC **Via Serving UI:**
# MAGIC 1. Navigate to: Serving → Your Endpoint → Configuration → Advanced configurations
# MAGIC 2. Click "Add environment variable"
# MAGIC 3. Add the following variables:
# MAGIC
# MAGIC | Variable Name | Value | Type | Required |
# MAGIC |---------------|-------|------|----------|
# MAGIC | `DATABRICKS_HOST` | `your-workspace.cloud.databricks.com` | Plain text | Yes* |
# MAGIC | `DATABRICKS_TOKEN` | `{{secrets/sql_warehouse_scope/warehouse_token}}` | Secret | Yes* |
# MAGIC | `SQL_WAREHOUSE_ID` | `your-production-warehouse-id` | Plain text | Optional** |
# MAGIC
# MAGIC \* Not required if using automatic authentication with registered resources
# MAGIC \** Only needed if production warehouse differs from development warehouse
# MAGIC
# MAGIC **Via REST API (manual auth):**
# MAGIC ```python
# MAGIC {
# MAGIC   "name": "super-agent-endpoint",
# MAGIC   "config": {
# MAGIC     "served_entities": [{
# MAGIC       "entity_name": "catalog.schema.super_agent_model",
# MAGIC       "entity_version": "1",
# MAGIC       "workload_size": "Small",
# MAGIC       "scale_to_zero_enabled": true,
# MAGIC       "environment_vars": {
# MAGIC         "DATABRICKS_HOST": "your-workspace.cloud.databricks.com",
# MAGIC         "DATABRICKS_TOKEN": "{{secrets/sql_warehouse_scope/warehouse_token}}",
# MAGIC         "SQL_WAREHOUSE_ID": "your-production-warehouse-id"  # Optional
# MAGIC       }
# MAGIC     }]
# MAGIC   }
# MAGIC }
# MAGIC ```
# MAGIC
# MAGIC #### Step 3: Package Model with MLflow
# MAGIC
# MAGIC When packaging your model for deployment:
# MAGIC
# MAGIC ```python
# MAGIC import mlflow
# MAGIC
# MAGIC class SuperAgentModel(mlflow.pyfunc.PythonModel):
# MAGIC     def load_context(self, context):
# MAGIC         # Initialize SQL Execution Agent with warehouse ID
# MAGIC         import os
# MAGIC         warehouse_id = os.getenv("SQL_WAREHOUSE_ID") or "your-default-warehouse-id"
# MAGIC         self.sql_agent = SQLExecutionAgent(warehouse_id=warehouse_id)
# MAGIC         # Authentication handled automatically via Config() - reads injected credentials
# MAGIC     
# MAGIC     def predict(self, context, model_input):
# MAGIC         # Use the agent - Config() transparently handles OAuth
# MAGIC         result = self.sql_agent.execute_sql(query)
# MAGIC         return result
# MAGIC
# MAGIC # Log model with resources for automatic authentication
# MAGIC from mlflow.models.resources import DatabricksSQLWarehouse
# MAGIC resources = [DatabricksSQLWarehouse(warehouse_id=warehouse_id)]
# MAGIC mlflow.pyfunc.log_model("model", python_model=SuperAgentModel(), resources=resources)
# MAGIC ```
# MAGIC
# MAGIC #### Step 4: Verify Deployment
# MAGIC
# MAGIC After deployment, test your endpoint:
# MAGIC
# MAGIC ```python
# MAGIC import requests
# MAGIC
# MAGIC response = requests.post(
# MAGIC     "https://your-workspace.cloud.databricks.com/serving-endpoints/super-agent-endpoint/invocations",
# MAGIC     headers={"Authorization": f"Bearer {token}"},
# MAGIC     json={"inputs": {"query": "SELECT * FROM table LIMIT 10"}}
# MAGIC )
# MAGIC print(response.json())
# MAGIC ```
# MAGIC
# MAGIC #### Security Best Practices
# MAGIC
# MAGIC ✅ **DO:**
# MAGIC - **Use automatic authentication passthrough with resource registration (recommended)**
# MAGIC - Implement least-privilege access controls on SQL Warehouse
# MAGIC - Use service principals (automatic with resource registration)
# MAGIC - Let Databricks handle credential rotation automatically
# MAGIC - Store sensitive credentials in Databricks secrets (if using manual auth)
# MAGIC
# MAGIC ❌ **DON'T:**
# MAGIC - Hard-code tokens in your model code
# MAGIC - Use plain text environment variables for sensitive data
# MAGIC - Share tokens across multiple applications
# MAGIC - Use personal access tokens in production
# MAGIC - Manually manage token rotation (use automatic passthrough instead)
# MAGIC
# MAGIC #### Troubleshooting
# MAGIC
# MAGIC **With Automatic Authentication (Resource Registration):**
# MAGIC
# MAGIC **Error: "Authentication failed" or "Access denied"**
# MAGIC - Verify SQL Warehouse was registered as a resource during model logging
# MAGIC - Check that the deploying user has access to the SQL Warehouse
# MAGIC - Ensure the agent's service principal has been granted necessary permissions
# MAGIC - Verify resources list includes: `DatabricksSQLWarehouse(warehouse_id=...)`
# MAGIC
# MAGIC **With Manual Authentication (Legacy):**
# MAGIC
# MAGIC **Error: Config() cannot find credentials**
# MAGIC - Set DATABRICKS_HOST and DATABRICKS_TOKEN environment variables
# MAGIC - Verify secret exists: `databricks secrets list-secrets sql_warehouse_scope`
# MAGIC - Verify environment variable uses correct syntax: `{{secrets/scope/key}}`
# MAGIC - Ensure endpoint creator has READ access to the secret
# MAGIC
# MAGIC **Error: "Authentication failed"**
# MAGIC - Verify token has not expired
# MAGIC - Check SQL Warehouse permissions for the token owner
# MAGIC - Verify workspace URL is correct (without https://)
# MAGIC
# MAGIC #### References
# MAGIC - [Agent Authentication (Automatic Passthrough)](https://docs.databricks.com/generative-ai/agent-framework/agent-authentication)
# MAGIC - [Configure access to resources from model serving endpoints](https://docs.databricks.com/machine-learning/model-serving/store-env-variable-model-serving.html)
# MAGIC - [Databricks unified authentication](https://docs.databricks.com/dev-tools/auth/unified-auth.html)
# MAGIC - [Model Serving endpoints API](https://docs.databricks.com/api/workspace/servingendpoints)

# COMMAND ----------


