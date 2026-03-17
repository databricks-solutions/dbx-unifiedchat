# Databricks notebook source
# DBTITLE 1,Deploy Multi-Agent System to Model Serving
"""
Deployment notebook using modular code from src/multi_agent/.

This notebook:
1. Receives ALL configuration from DABs base_parameters (databricks.yml is single source of truth)
2. Generates a temp config YAML for MLflow ModelConfig
3. Imports modular agent code from ../src/multi_agent/
4. Deploys to Model Serving using MLflow with code_paths parameter

Original Super_Agent_hybrid.py (6,833 lines) archived in archive/ for reference.
"""

# COMMAND ----------

# DBTITLE 1,Install Packages
# MAGIC %pip install python-dotenv databricks-sdk==0.84.0 databricks-sql-connector==4.2.4 databricks-langchain[memory]==0.12.1 databricks-vectorsearch==0.63 databricks-agents==1.9.3 mlflow[databricks]>=3.6.0 pyyaml

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

# DBTITLE 1,autoreload local package
# MAGIC %load_ext autoreload
# MAGIC %autoreload 2

# COMMAND ----------

# DBTITLE 1,Load Configuration from DABs Variables
# All parameters are injected by DABs job via base_parameters.
# databricks.yml is the single source of truth — no separate config YAMLs.
#
# Flow: widgets → build_config_yaml() → temp YAML → get_config() → AgentConfig
#       One config system for notebooks AND serving.
#
# Defaults are empty sentinels. If run manually without DABs, the notebook
# will fail at validation with a clear error rather than using stale values.
import sys
import os

_WIDGET_KEYS = [
    "catalog_name", "schema_name", "sql_warehouse_id", "genie_space_ids",
    "volume_name", "enriched_docs_table", "source_table", "uc_function_names",
    "llm_endpoint", "llm_endpoint_clarification", "llm_endpoint_planning",
    "llm_endpoint_sql_synthesis_table", "llm_endpoint_sql_synthesis_genie",
    "llm_endpoint_execution", "llm_endpoint_summarize",
    "sample_size", "max_unique_values",
    "vs_endpoint_name", "embedding_model",
    "lakebase_instance_name", "lakebase_embedding_endpoint", "lakebase_embedding_dims",
    "model_name", "endpoint_name", "workload_size", "scale_to_zero",
]

for k in _WIDGET_KEYS:
    dbutils.widgets.text(k, "")

widget_params = {k: dbutils.widgets.get(k) for k in _WIDGET_KEYS}

_empty = [k for k, v in widget_params.items() if not v.strip()]
if _empty:
    print(f"⚠️  {len(_empty)} widget(s) have no value (expected when run via DABs): {', '.join(_empty[:5])}{'...' if len(_empty) > 5 else ''}")
    print("   If running manually, set values via notebook widgets or use DABs: databricks bundle run")

# Step 1: Generate temp YAML (needed for mlflow.pyfunc.log_model model_config)
from notebook_utils import build_config_yaml
config_yaml_path = build_config_yaml(widget_params, path="./agent_config.yaml")

# Step 2: Set env var BEFORE any src/ imports (responses_agent.py calls get_config() at load time)
os.environ["AGENT_CONFIG_FILE"] = config_yaml_path

# Step 3: Add src to path
notebook_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
src_path = os.path.join(os.path.dirname(notebook_dir), "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Step 4: Single config system — same AgentConfig used by notebooks and serving
from multi_agent.core.config import get_config
cfg = get_config()

# Convenience aliases (used throughout the notebook)
CATALOG = cfg.unity_catalog.catalog_name
SCHEMA = cfg.unity_catalog.schema_name
TABLE_NAME = cfg.source_table_fq
VECTOR_SEARCH_INDEX = cfg.vs_index_fq
UC_FUNCTION_NAMES = cfg.unity_catalog.uc_function_names_fq
GENIE_SPACE_IDS = cfg.table_metadata.genie_space_ids
SQL_WAREHOUSE_ID = cfg.table_metadata.sql_warehouse_id
LAKEBASE_INSTANCE_NAME = cfg.lakebase.instance_name
EMBEDDING_ENDPOINT = cfg.lakebase.embedding_endpoint
EMBEDDING_DIMS = cfg.lakebase.embedding_dims
MODEL_NAME = cfg.model_serving.model_name
ENDPOINT_NAME = cfg.model_serving.endpoint_name
WORKLOAD_SIZE = cfg.model_serving.workload_size
SCALE_TO_ZERO = cfg.model_serving.scale_to_zero_enabled

cfg.print_summary()

# COMMAND ----------

# DBTITLE 1,Verify Modular Code Is Synced
"""
Verify that src/multi_agent/ code is synced to Databricks workspace.

The modular code must be uploaded before deployment.
"""

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

# DBTITLE 1,ONE-TIME SETUP: Initialize Lakebase Tables for State Management
"""
IMPORTANT: Run this cell ONCE to set up Lakebase instance and tables for state management.

This creates:
1. Lakebase Postgres instance (if it doesn't exist)
2. checkpoints table - For short-term memory (multi-turn conversations)
3. store table - For long-term memory (user preferences with semantic search)
"""

from databricks_langchain import CheckpointSaver, DatabricksStore
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.database import DatabaseInstance
import time

print("="*80)
print("LAKEBASE INSTANCE SETUP")
print("="*80)
print(f"Instance name: {LAKEBASE_INSTANCE_NAME}")

# Initialize Databricks workspace client
w = WorkspaceClient()

# Check if Lakebase instance exists, create if not
instance_exists = False
try:
    instance = w.database.get_database_instance(LAKEBASE_INSTANCE_NAME)
    print(f"✓ Lakebase instance '{LAKEBASE_INSTANCE_NAME}' already exists")
    instance_exists = True
except Exception as e:
    print(f"Instance not found. Creating new Lakebase instance...")
    try:
        # Create Lakebase instance with DatabaseInstance object
        instance = w.database.create_database_instance(
            DatabaseInstance(
                name=LAKEBASE_INSTANCE_NAME,
                capacity="CU_1"  # Start with smallest capacity (1 compute unit)
            )
        )
        print(f"✓ Lakebase instance '{LAKEBASE_INSTANCE_NAME}' creation initiated")
        print(f"  Capacity: CU_1")
        print("  Waiting for instance to become available...")
        
        # Wait for instance to be ready (can take 2-5 minutes)
        max_wait_time = 300  # 5 minutes
        wait_interval = 10  # Check every 10 seconds
        elapsed_time = 0
        
        while elapsed_time < max_wait_time:
            try:
                instance = w.database.get_database_instance(LAKEBASE_INSTANCE_NAME)
                # If we can get the instance without error, it's ready
                print(f"✓ Instance is now available (waited {elapsed_time}s)")
                instance_exists = True
                break
            except:
                time.sleep(wait_interval)
                elapsed_time += wait_interval
                print(f"  Still waiting... ({elapsed_time}s elapsed)")
        
        if not instance_exists:
            print(f"⚠️ Instance creation is taking longer than expected.")
            print(f"   Please wait a few more minutes and re-run this cell.")
            raise TimeoutError(f"Instance not ready after {max_wait_time}s")
            
    except Exception as create_error:
        print(f"❌ Error creating Lakebase instance: {create_error}")
        raise

if instance_exists:
    # Check if instance is in RUNNING state before proceeding
    print("\n" + "="*80)
    print("CHECKING INSTANCE STATUS")
    print("="*80)
    
    max_status_wait = 600  # 10 minutes max wait for RUNNING state
    status_check_interval = 60  # Check every 1 minute
    status_elapsed = 0
    
    while status_elapsed < max_status_wait:
        instance = w.database.get_database_instance(LAKEBASE_INSTANCE_NAME)
        instance_state = instance.state.value if instance.state else "UNKNOWN"
        
        print(f"Instance state: {instance_state}")
        
        if instance_state == "AVAILABLE":
            print("✓ Instance is AVAILABLE and ready for table setup")
            break
        elif instance_state in ["FAILED", "DELETED"]:
            raise RuntimeError(f"Instance is in {instance_state} state. Cannot proceed.")
        else:
            print(f"Instance {instance_state} not ready yet. Waiting 1 minute before rechecking...")
            time.sleep(status_check_interval)
            status_elapsed += status_check_interval
            print(f"  Total wait time: {status_elapsed}s")
    
    if status_elapsed >= max_status_wait:
        raise TimeoutError(f"Instance did not reach RUNNING state after {max_status_wait}s")

    print("\n" + "="*80)
    print("INITIALIZING LAKEBASE TABLES")
    print("="*80)

    # Setup checkpoint table for short-term memory
    print("Setting up checkpoint table...")
    with CheckpointSaver(instance_name=LAKEBASE_INSTANCE_NAME) as saver:
        saver.setup()
        print("✓ Checkpoint table created/verified")

    # Setup store table for long-term memory
    print("Setting up store table...")
    store = DatabricksStore(
        instance_name=LAKEBASE_INSTANCE_NAME,
        embedding_endpoint=EMBEDDING_ENDPOINT,
        embedding_dims=EMBEDDING_DIMS,
    )
    store.setup()
    print("✓ Store table created/verified")

    print("\n" + "="*80)
    print("✅ LAKEBASE SETUP COMPLETE!")
    print("="*80)
    print(f"Instance: {LAKEBASE_INSTANCE_NAME}")
    print("Tables: checkpoints, store")
    print("="*80)

# COMMAND ----------

# DBTITLE 1,ONE-TIME SETUP: Register Unity Catalog Functions for Metadata Querying
"""
Register UC functions that will be used as tools by the SQL Synthesis Agent.

These UC functions query different levels of the enriched genie docs chunks table:
1. get_space_summary: High-level space information
2. get_table_overview: Table-level metadata
3. get_column_detail: Column-level metadata
4. get_space_instructions: Extract raw SQL instructions JSON (any structure within instructions field) from space metadata (REQUIRED FINAL STEP)
5. get_space_details: Complete metadata (last resort - token intensive)

All functions use LANGUAGE SQL for better performance and compatibility.

IMPORTANT: This registration is now centralized in src/multi_agent/tools/uc_functions.py
and should be called before creating agents.
"""
from databricks_langchain import (
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
)
# Initialize UC Function Client
client = DatabricksFunctionClient()
set_uc_function_client(client)

# Import the registration function from the centralized module
from multi_agent.tools import register_uc_functions, check_uc_functions_exist

# Register all UC functions using the centralized registration module
result = register_uc_functions(
    catalog=CATALOG,
    schema=SCHEMA,
    table_name=TABLE_NAME
)

# # Check registration result
# if not result["success"]:
#     print("\n" + "=" * 80)
#     print("⚠️ WARNING: Some UC functions failed to register!")
#     print("=" * 80)
#     for error in result["errors"]:
#         print(f"  ✗ {error}")
#     print("=" * 80)
#     raise RuntimeError("Failed to register all UC functions")

# # Verify all functions exist
# print("\n🔍 Verifying UC functions...")
# check_result = check_uc_functions_exist(spark=spark, catalog=CATALOG, schema=SCHEMA, verbose=True)

# if not check_result["all_exist"]:
#     raise RuntimeError(f"Missing UC functions: {check_result['missing_functions']}")

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

# DBTITLE 1,mlflow logging model
"""
Deploy the agent to Model Serving with modular code.

KEY CHANGE: Uses code_paths parameter to package src/multi_agent/ code.
"""

import mlflow
from uuid import uuid4
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
    DatabricksServingEndpoint(cfg.llm.clarification_endpoint),
    DatabricksServingEndpoint(cfg.llm.planning_endpoint),
    DatabricksServingEndpoint(cfg.llm.sql_synthesis_table_endpoint),
    DatabricksServingEndpoint(cfg.llm.sql_synthesis_genie_endpoint),
    DatabricksServingEndpoint(cfg.llm.execution_endpoint),
    DatabricksServingEndpoint(cfg.llm.summarize_endpoint),
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
    *[DatabricksFunction(function_name=fn) for fn in UC_FUNCTION_NAMES],
]

# Input example for schema inference
input_example = {
    "input": [{"role": "user", "content": "What is the average medical cost of diabetes patients? Use Table route"}],
    "custom_inputs": {"thread_id": f"test-streaming-{str(uuid4())[:8]}"},
    "context": {"conversation_id": "sess-001", "user_id": "user@example.com"}
}

# Setup experiment path — derive target from endpoint name suffix
_target = "dev" if ENDPOINT_NAME.endswith("-dev") else "prod"
os.makedirs("/Workspace/Shared/dbx-unifiedchat/", exist_ok=True)
mlflow.set_tracking_uri("databricks")
mlflow.set_experiment(f"/Shared/dbx-unifiedchat/{_target}-traces")

# Deploy with MLflow
with mlflow.start_run():
    # Log LLM model choices for monitoring
    mlflow.log_param("llm_endpoint_clarification", cfg.llm.clarification_endpoint)
    mlflow.log_param("llm_endpoint_planning", cfg.llm.planning_endpoint)
    mlflow.log_param("llm_endpoint_sql_synthesis_table", cfg.llm.sql_synthesis_table_endpoint)
    mlflow.log_param("llm_endpoint_sql_synthesis_genie", cfg.llm.sql_synthesis_genie_endpoint)
    mlflow.log_param("llm_endpoint_execution", cfg.llm.execution_endpoint)
    mlflow.log_param("llm_endpoint_summarize", cfg.llm.summarize_endpoint)
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
        model_config=config_yaml_path,  # Auto-generated from DABs variables
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
    print(f"✓ Configuration: {config_yaml_path} (auto-generated from DABs variables)")
    print(f"✓ Modular code packaged from: ../src/multi_agent/")

# Register to Unity Catalog
mlflow.set_registry_uri("databricks-uc")
UC_MODEL_NAME = f"{CATALOG}.{SCHEMA}.{MODEL_NAME}"

uc_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri,
    name=UC_MODEL_NAME
)
print(f"✓ Model registered: {UC_MODEL_NAME} version {uc_model_info.version}")

# COMMAND ----------

# DBTITLE 1,Deploy Agent to Model Serving
# Deploy to Model Serving
from databricks import agents
import os

deployment_info = agents.deploy(
    UC_MODEL_NAME,
    uc_model_info.version,
    endpoint_name=ENDPOINT_NAME,
    scale_to_zero=SCALE_TO_ZERO,
    workload_size=WORKLOAD_SIZE,
)

print(f"✓ Deployed to Model Serving: {deployment_info.endpoint_name}")
print("\n" + "="*80)
print("✅ DEPLOYMENT COMPLETE")
print("="*80)
print(f"Model: {UC_MODEL_NAME} v{uc_model_info.version}")
print(f"Endpoint: {deployment_info.endpoint_name}")
print(f"Configuration: databricks.yml (auto-generated YAML packaged with model)")
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

# DBTITLE 1,Test Deployed Endpoint with Readiness Check
from databricks.sdk import WorkspaceClient
import time

w = WorkspaceClient()

# Wait for endpoint to be ready
print("Waiting for endpoint to be ready...")
max_wait_time = 1800
wait_interval = 30
elapsed_time = 0

while elapsed_time < max_wait_time:
    try:
        endpoint_status = w.serving_endpoints.get(name=deployment_info.endpoint_name)
        state = endpoint_status.state.config_update if endpoint_status.state.config_update else endpoint_status.state.ready
        state = state.value

        print(f"  Endpoint state: {state} (elapsed: {elapsed_time}s)")
        
        if state in ["READY", "NOT_UPDATING"]:
            print("\n✓ Endpoint is ready!")
            break
        elif state in ["UPDATE_FAILED", "CRASHED"]:
            print(f"\n✗ Endpoint deployment failed with state: {state}")
            raise RuntimeError(f"Endpoint failed to deploy: {state}")
        
        time.sleep(wait_interval)
        elapsed_time += wait_interval
        
    except Exception as e:
        if elapsed_time == 0:
            print(f"  Initial status check failed: {e}")
            print(f"  This is normal for new deployments. Waiting...")
        time.sleep(wait_interval)
        elapsed_time += wait_interval

if elapsed_time >= max_wait_time:
    print(f"\n⚠ Timeout: Endpoint not ready after {max_wait_time}s")
    print("Check Model Serving UI for details.")
    raise TimeoutError(f"Endpoint not ready after {max_wait_time}s")

# COMMAND ----------

# DBTITLE 1,Test Deployed Endpoint with Sample Query

from mlflow.deployments import get_deploy_client
from uuid import uuid4

print("\nTesting endpoint with sample query...")

client = get_deploy_client("databricks")

test_input = {
    "input": [{"role": "user", "content": "What is the average medical cost of diabetes patients? Use Genie route"}],
    "custom_inputs": {"thread_id": f"test-streaming-{str(uuid4())[:8]}"},
    "context": {"conversation_id": "sess-001", "user_id": "user@example.com"}
}

try:
    response = client.predict(
        endpoint=deployment_info.endpoint_name,
        inputs=test_input,
    )
    print("\n✓ Endpoint responding successfully")
    print(f"Response preview: {str(response)[:500]}...")
except Exception as e:
    print(f"\n✗ Error querying endpoint: {e}")
    print("Check Model Serving UI and logs for details.")

# COMMAND ----------

response

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
print(f"  Workload: {WORKLOAD_SIZE} (scale-to-zero={'enabled' if SCALE_TO_ZERO else 'disabled'})")

print(f"\n⚙️ Configuration:")
print(f"  Source: databricks.yml (single source of truth)")
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
# MAGIC - `databricks.yml`: Single source of truth for all configuration
# MAGIC - `archive/Super_Agent_hybrid_original.py`: Original 6,833-line version (for reference)
# MAGIC
# MAGIC ### Documentation
# MAGIC
# MAGIC - [Deployment Guide](../docs/DEPLOYMENT.md)
# MAGIC - [Configuration Guide](../docs/CONFIGURATION.md)
# MAGIC - [Architecture](../docs/ARCHITECTURE.md)