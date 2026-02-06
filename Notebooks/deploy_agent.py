# Databricks notebook source
# DBTITLE 1,Deploy Multi-Agent System to Model Serving
"""
Simplified deployment notebook using modular code from src/multi_agent/.

This notebook:
1. Loads configuration from prod_config.yaml
2. Imports modular agent code from ../src/multi_agent/
3. Deploys to Model Serving using MLflow with code_paths parameter

Original Super_Agent_hybrid.py (6,833 lines) archived in archive/ for reference.
"""

# COMMAND ----------

# DBTITLE 1,Install Packages
%pip install python-dotenv databricks-sdk==0.84.0 databricks-sql-connector==4.2.4 databricks-langchain[memory]==0.12.1 databricks-vectorsearch==0.63 databricks-agents==1.9.3 mlflow[databricks]>=3.6.0 pyyaml

# COMMAND ----------

%restart_python

# COMMAND ----------

# DBTITLE 1,Load Configuration from YAML
"""
Load configuration from prod_config.yaml.

For deployment, we use YAML configuration which gets packaged with the model.
For local development, use config.py + .env instead.
"""

import yaml
import os

# Load prod_config.yaml
config_path = "../prod_config.yaml"
with open(config_path, 'r') as f:
    yaml_config = yaml.safe_load(f)

# Extract configuration values
CATALOG = yaml_config['catalog_name']
SCHEMA = yaml_config['schema_name']
TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"
VECTOR_SEARCH_INDEX = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks_vs_index"

# LLM Endpoints - Diversified by Agent Role
LLM_ENDPOINT_CLARIFICATION = yaml_config.get('llm_endpoint_clarification', yaml_config['llm_endpoint'])
LLM_ENDPOINT_PLANNING = yaml_config.get('llm_endpoint_planning', yaml_config['llm_endpoint'])
LLM_ENDPOINT_SQL_SYNTHESIS_TABLE = yaml_config.get('llm_endpoint_sql_synthesis_table', yaml_config['llm_endpoint'])
LLM_ENDPOINT_SQL_SYNTHESIS_GENIE = yaml_config.get('llm_endpoint_sql_synthesis_genie', yaml_config['llm_endpoint'])
LLM_ENDPOINT_EXECUTION = yaml_config.get('llm_endpoint_execution', yaml_config['llm_endpoint'])
LLM_ENDPOINT_SUMMARIZE = yaml_config.get('llm_endpoint_summarize', yaml_config['llm_endpoint'])

# Lakebase configuration
LAKEBASE_INSTANCE_NAME = yaml_config['lakebase_instance_name']
EMBEDDING_ENDPOINT = yaml_config['lakebase_embedding_endpoint']
EMBEDDING_DIMS = yaml_config['lakebase_embedding_dims']

# SQL Warehouse
SQL_WAREHOUSE_ID = yaml_config['sql_warehouse_id']

# Genie Spaces
GENIE_SPACE_IDS = yaml_config['genie_space_ids']

# Validate SQL_WAREHOUSE_ID
if not SQL_WAREHOUSE_ID:
    raise ValueError("SQL_WAREHOUSE_ID must be configured in prod_config.yaml")

print("="*80)
print("CONFIGURATION LOADED FROM prod_config.yaml")
print("="*80)
print(f"Catalog: {CATALOG}")
print(f"Schema: {SCHEMA}")
print(f"Vector Search Index: {VECTOR_SEARCH_INDEX}")
print(f"SQL Warehouse ID: {SQL_WAREHOUSE_ID}")
print(f"Genie Spaces: {len(GENIE_SPACE_IDS)} spaces")
print(f"Lakebase Instance: {LAKEBASE_INSTANCE_NAME}")
print("\nLLM Endpoints (Diversified by Agent):")
print(f"  Clarification: {LLM_ENDPOINT_CLARIFICATION}")
print(f"  Planning: {LLM_ENDPOINT_PLANNING}")
print(f"  SQL Synthesis Table: {LLM_ENDPOINT_SQL_SYNTHESIS_TABLE}")
print(f"  SQL Synthesis Genie: {LLM_ENDPOINT_SQL_SYNTHESIS_GENIE}")
print(f"  Execution: {LLM_ENDPOINT_EXECUTION}")
print(f"  Summarize: {LLM_ENDPOINT_SUMMARIZE}")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Verify Modular Code Is Synced
"""
Verify that src/multi_agent/ code is synced to Databricks workspace.

The modular code must be uploaded before deployment.
"""

import sys

# Add src to path
notebook_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
src_path = os.path.join(os.path.dirname(notebook_dir), "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Try importing to verify code is available
try:
    from multi_agent.core.graph import create_agent_graph
    from multi_agent.core.state import AgentState
    print("✓ Successfully imported modular agent code from src/multi_agent/")
    print(f"✓ Source path: {src_path}")
except ImportError as e:
    print(f"❌ Import Error: {e}")
    print("\n⚠️ CRITICAL: Modular code not found!")
    print("\nYou must sync src/multi_agent/ to Databricks before deploying:")
    print("  databricks workspace import-dir src/multi_agent /Workspace/src/multi_agent --overwrite")
    print("\nOr use Databricks Repos for automatic syncing.")
    raise

# COMMAND ----------

# DBTITLE 1,Discover Deployment Resources
"""
Discover all required resources for deployment.
"""

from databricks.sdk import WorkspaceClient
from pyspark.sql.functions import from_json, col
from pyspark.sql.types import MapType, StringType

print("="*80)
print("DISCOVERING DEPLOYMENT RESOURCES")
print("="*80)

# 1. Genie Space IDs
print("\n[1/4] Genie Space IDs:")
for space_id in GENIE_SPACE_IDS:
    print(f"  - {space_id}")

# 2. SQL Warehouse ID
print("\n[2/4] SQL Warehouse ID:")
print(f"  ✓ {SQL_WAREHOUSE_ID}")

# 3. Query underlying tables
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
    
    print("Underlying Tables:")
    for t in UNDERLYING_TABLES:
        print(f"  - {t}")
except Exception as e:
    print(f"Warning: Could not query tables: {e}")
    UNDERLYING_TABLES = []

print("\n[4/4] Resources discovered successfully")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Deploy Agent to Model Serving
"""
Deploy the agent to Model Serving with modular code.

KEY CHANGE: Uses code_paths parameter to package src/multi_agent/ code.
"""

import mlflow
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

# Declare all resources the agent needs
resources = [
    # LLM endpoints - Diversified by Agent Role
    DatabricksServingEndpoint(LLM_ENDPOINT_CLARIFICATION),
    DatabricksServingEndpoint(LLM_ENDPOINT_PLANNING),
    DatabricksServingEndpoint(LLM_ENDPOINT_SQL_SYNTHESIS_TABLE),
    DatabricksServingEndpoint(LLM_ENDPOINT_SQL_SYNTHESIS_GENIE),
    DatabricksServingEndpoint(LLM_ENDPOINT_EXECUTION),
    DatabricksServingEndpoint(LLM_ENDPOINT_SUMMARIZE),
    DatabricksServingEndpoint(EMBEDDING_ENDPOINT),
    
    # Lakebase for state management (CRITICAL!)
    DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME),
    
    # Vector Search Index
    DatabricksVectorSearchIndex(index_name=VECTOR_SEARCH_INDEX),
    
    # SQL Warehouse (required for Genie spaces and UC functions)
    DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
    
    # Genie Spaces (IMPORTANT: Must declare all Genie spaces used!)
    *[DatabricksGenieSpace(genie_space_id=space_id) for space_id in GENIE_SPACE_IDS],
    
    # Tables (metadata enrichment table + underlying Genie tables)
    DatabricksTable(table_name=TABLE_NAME),
    *[DatabricksTable(table_name=table) for table in UNDERLYING_TABLES],
    
    # UC Functions (metadata querying tools)
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_summary"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_table_overview"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_column_detail"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_details"),
]

# Input example for schema inference
input_example = {
    "input": [{"role": "user", "content": "Show me patient data"}],
    "custom_inputs": {"thread_id": "example-123"},
    "context": {"conversation_id": "sess-001", "user_id": "user@example.com"}
}

# Deploy with MLflow
with mlflow.start_run():
    # Log LLM model choices for monitoring
    mlflow.log_param("llm_endpoint_clarification", LLM_ENDPOINT_CLARIFICATION)
    mlflow.log_param("llm_endpoint_planning", LLM_ENDPOINT_PLANNING)
    mlflow.log_param("llm_endpoint_sql_synthesis_table", LLM_ENDPOINT_SQL_SYNTHESIS_TABLE)
    mlflow.log_param("llm_endpoint_sql_synthesis_genie", LLM_ENDPOINT_SQL_SYNTHESIS_GENIE)
    mlflow.log_param("llm_endpoint_execution", LLM_ENDPOINT_EXECUTION)
    mlflow.log_param("llm_endpoint_summarize", LLM_ENDPOINT_SUMMARIZE)
    mlflow.log_param("embedding_endpoint", EMBEDDING_ENDPOINT)
    
    # Log agent configuration strategy
    mlflow.set_tag("llm_diversification", "enabled")
    mlflow.set_tag("agent_config_version", "v2.0_modular")
    mlflow.set_tag("code_structure", "modular")
    
    # Log model with MODULAR CODE via code_paths
    logged_agent_info = mlflow.pyfunc.log_model(
        name="super_agent_hybrid_with_memory",
        python_model="./agent.py",  # MLflow wrapper
        code_paths=["../src/multi_agent"],  # 🎯 KEY: Package modular code
        input_example=input_example,
        resources=resources,
        model_config="../prod_config.yaml",  # Production configuration
        pip_requirements=[
            f"databricks-sdk=={get_distribution('databricks-sdk').version}",
            f"databricks-sql-connector=={get_distribution('databricks-sql-connector').version}",
            f"databricks-langchain[memory]=={get_distribution('databricks-langchain').version}",
            f"databricks-agents=={get_distribution('databricks-agents').version}",
            f"databricks-vectorsearch=={get_distribution('databricks-vectorsearch').version}",
            f"langgraph=={get_distribution('langgraph').version}",
            f"mlflow[databricks]=={mlflow.__version__}",
            "pyyaml>=6.0",  # For YAML config loading
        ]
    )
    print(f"✓ Model logged: {logged_agent_info.model_uri}")
    print(f"✓ Configuration: prod_config.yaml")
    print(f"✓ Modular code packaged from: ../src/multi_agent/")

# Register to Unity Catalog
mlflow.set_registry_uri("databricks-uc")
UC_MODEL_NAME = f"{CATALOG}.{SCHEMA}.super_agent_hybrid"

uc_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri,
    name=UC_MODEL_NAME
)
print(f"✓ Model registered: {UC_MODEL_NAME} version {uc_model_info.version}")

# Deploy to Model Serving
from databricks import agents

deployment_info = agents.deploy(
    UC_MODEL_NAME,
    uc_model_info.version,
    scale_to_zero=True,      # Cost optimization
    workload_size="Small",   # Start small, can scale up later
)

print(f"✓ Deployed to Model Serving: {deployment_info.endpoint_name}")
print("\n" + "="*80)
print("✅ DEPLOYMENT COMPLETE")
print("="*80)
print(f"Model: {UC_MODEL_NAME} v{uc_model_info.version}")
print(f"Endpoint: {deployment_info.endpoint_name}")
print(f"Configuration: prod_config.yaml (packaged with model)")
print(f"Code: ../src/multi_agent/ (modular, {sum(1 for _ in os.walk('../src/multi_agent'))} modules)")
print("\nMemory Features Enabled:")
print("  ✓ Short-term: Multi-turn conversations via CheckpointSaver")
print("  ✓ Long-term: User preferences via DatabricksStore")
print("  ✓ Distributed serving: State shared across all instances")
print("\nAdvantages of Modular Approach:")
print("  ✓ Single source of truth (same code for local dev & deployment)")
print("  ✓ Easier to maintain (<500 lines per module)")
print("  ✓ Better testing (test individual components)")
print("  ✓ MLflow native support via code_paths parameter")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Test Deployed Endpoint
"""
Test the deployed endpoint with a sample query.
"""

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

# Test query
test_input = {
    "input": [{"role": "user", "content": "Show me patient demographics"}],
    "custom_inputs": {"thread_id": "test-123"}
}

# Query endpoint
try:
    print("Testing endpoint...")
    response = w.serving_endpoints.query(
        name=deployment_info.endpoint_name,
        inputs=[test_input]
    )
    print("\n✓ Endpoint responding successfully")
    print(f"Response preview: {str(response)[:200]}...")
except Exception as e:
    print(f"⚠️ Error testing endpoint: {e}")
    print("The endpoint may still be initializing. Check Model Serving UI.")

# COMMAND ----------

# DBTITLE 1,Deployment Information
"""
Deployment summary and next steps.
"""

print("="*80)
print("DEPLOYMENT SUMMARY")
print("="*80)
print(f"\n📦 Model:")
print(f"  Name: {UC_MODEL_NAME}")
print(f"  Version: {uc_model_info.version}")
print(f"  URI: {logged_agent_info.model_uri}")

print(f"\n🚀 Endpoint:")
print(f"  Name: {deployment_info.endpoint_name}")
print(f"  Workload: Small (scale-to-zero enabled)")

print(f"\n⚙️ Configuration:")
print(f"  Source: prod_config.yaml")
print(f"  Catalog: {CATALOG}")
print(f"  Schema: {SCHEMA}")
print(f"  Genie Spaces: {len(GENIE_SPACE_IDS)}")

print(f"\n💻 Code:")
print(f"  Structure: Modular (from src/multi_agent/)")
print(f"  Packaging: MLflow code_paths parameter")
print(f"  Wrapper: agent.py")

print(f"\n📊 Next Steps:")
print("  1. Monitor endpoint in Model Serving UI")
print("  2. Test in AI Playground")
print("  3. Check MLflow traces")
print("  4. Review inference tables")
print("  5. Monitor costs and performance")

print("\n🔗 Links:")
print(f"  Model Serving UI: <workspace-url>/ml/endpoints")
print(f"  MLflow Run: {logged_agent_info.model_uri}")
print(f"  Deployment docs: ../docs/DEPLOYMENT.md")

print("="*80)

# COMMAND ----------

# DBTITLE 1,Rollback Instructions (If Needed)
"""
If deployment has issues, you can rollback to a previous version.
"""

print("="*80)
print("ROLLBACK INSTRUCTIONS")
print("="*80)
print("\nIf you need to rollback to a previous version:")
print(f"\n1. List model versions:")
print(f"   versions = mlflow.search_model_versions(f\"name='{UC_MODEL_NAME}'\")")
print(f"   for v in versions:")
print(f"       print(f\"Version {{v.version}}: {{v.tags}}\")")
print(f"\n2. Deploy previous version:")
print(f"   agents.deploy('{UC_MODEL_NAME}', previous_version_number)")
print("\n3. Or via UI:")
print(f"   Model Serving → {deployment_info.endpoint_name} → Versions → Select previous")
print("="*80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 📝 Notes
# MAGIC 
# MAGIC ### What Changed from Original Super_Agent_hybrid.py
# MAGIC 
# MAGIC **Before** (Original - 6,833 lines):
# MAGIC - All agent code embedded in notebook
# MAGIC - Hard to maintain and test
# MAGIC - Difficult to iterate locally
# MAGIC 
# MAGIC **After** (This notebook - ~250 lines):
# MAGIC - Imports from `../src/multi_agent/`
# MAGIC - Uses `code_paths` parameter to package modular code
# MAGIC - Same functionality, cleaner structure
# MAGIC - Easy to test locally before deploying
# MAGIC 
# MAGIC ### Benefits
# MAGIC 
# MAGIC 1. **Single Source of Truth**: Same code for local dev and deployment
# MAGIC 2. **Easier Testing**: Test components individually
# MAGIC 3. **Better Maintainability**: Small modules (<500 lines each)
# MAGIC 4. **Faster Iteration**: Develop locally, test in Databricks, deploy
# MAGIC 
# MAGIC ### Related Files
# MAGIC 
# MAGIC - `agent.py`: MLflow wrapper (imports from src/multi_agent/)
# MAGIC - `test_agent_databricks.py`: Test before deploying
# MAGIC - `src/multi_agent/`: All agent code (single source of truth)
# MAGIC - `prod_config.yaml`: Production configuration
# MAGIC - `archive/Super_Agent_hybrid_original.py`: Original 6,833-line version (for reference)
# MAGIC 
# MAGIC ### Documentation
# MAGIC 
# MAGIC - [Deployment Guide](../docs/DEPLOYMENT.md)
# MAGIC - [Configuration Guide](../docs/CONFIGURATION.md)
# MAGIC - [Architecture](../docs/ARCHITECTURE.md)
