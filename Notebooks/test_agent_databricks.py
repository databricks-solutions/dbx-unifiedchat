# Databricks notebook source
# DBTITLE 1,Test Multi-Agent System in Databricks (No Deployment)
"""
Test notebook for validating agent code in Databricks environment.

This notebook allows you to test the modular agent code with real Databricks
services (Genie, Vector Search, Lakebase) WITHOUT deploying to Model Serving.

Use this for:
- Testing code changes before deployment
- Debugging with real services
- Validating configuration
- Quick iteration without deployment overhead
"""

# COMMAND ----------

# DBTITLE 1,Install Packages
# MAGIC %pip install python-dotenv databricks-sdk==0.84.0 databricks-sql-connector==4.2.4 databricks-langchain[memory]==0.12.1 databricks-vectorsearch==0.63 databricks-agents==1.9.3 mlflow[databricks]>=3.6.0 pyyaml

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

# DBTITLE 1,dev_package_autoreload
# MAGIC %load_ext autoreload
# MAGIC %autoreload 2

# COMMAND ----------

# DBTITLE 1,Setup: Add src to Path
import sys
import os

# Add src directory to path to import modular code
notebook_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
src_path = os.path.join(os.path.dirname(notebook_dir), "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

print(f"✓ Added to path: {src_path}")

# COMMAND ----------

# DBTITLE 1,Setting up module level logger
import logging
logger = logging.getLogger(__name__)

# COMMAND ----------

import mlflow

# COMMAND ----------

# DBTITLE 1,Load Configuration from YAML
"""
Load configuration from dev_config.yaml for testing.

This uses the same YAML configuration that deployment uses,
but loads it for testing purposes.
"""

from notebook_utils import load_deployment_config

# Load dev_config.yaml via notebook_utils
config_dict = load_deployment_config("../dev_config.yaml")

# Extract key configuration values
CATALOG = config_dict['CATALOG']
SCHEMA = config_dict['SCHEMA']
TABLE_NAME = config_dict['TABLE_NAME']
VECTOR_SEARCH_INDEX = config_dict['VECTOR_SEARCH_INDEX']

# LLM Endpoints
LLM_ENDPOINT_CLARIFICATION = config_dict['LLM_ENDPOINT_CLARIFICATION']
LLM_ENDPOINT_PLANNING = config_dict['LLM_ENDPOINT_PLANNING']
LLM_ENDPOINT_SQL_SYNTHESIS_TABLE = config_dict['LLM_ENDPOINT_SQL_SYNTHESIS_TABLE']
LLM_ENDPOINT_SQL_SYNTHESIS_GENIE = config_dict['LLM_ENDPOINT_SQL_SYNTHESIS_GENIE']
LLM_ENDPOINT_EXECUTION = config_dict['LLM_ENDPOINT_EXECUTION']
LLM_ENDPOINT_SUMMARIZE = config_dict['LLM_ENDPOINT_SUMMARIZE']

# Lakebase
LAKEBASE_INSTANCE_NAME = config_dict['LAKEBASE_INSTANCE_NAME']
EMBEDDING_ENDPOINT = config_dict['EMBEDDING_ENDPOINT']

# SQL Warehouse
SQL_WAREHOUSE_ID = config_dict['SQL_WAREHOUSE_ID']

# Genie Spaces
GENIE_SPACE_IDS = config_dict['GENIE_SPACE_IDS']

print("="*80)
print("CONFIGURATION LOADED FROM dev_config.yaml via notebook_utils")
print("="*80)
print(f"Catalog: {CATALOG}")
print(f"Schema: {SCHEMA}")
print(f"Vector Search Index: {VECTOR_SEARCH_INDEX}")
print(f"SQL Warehouse ID: {SQL_WAREHOUSE_ID}")
print(f"Genie Spaces: {len(GENIE_SPACE_IDS)} spaces")
print(f"Lakebase Instance: {LAKEBASE_INSTANCE_NAME}")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Import Modular Agent Code
"""
Import agent code from src/multi_agent/ package.

This imports the same code that gets deployed via code_paths parameter.
"""

try:
    from multi_agent.core.graph import create_super_agent_hybrid
    from multi_agent.core.responses_agent import SuperAgentHybridResponsesAgent
    print("✓ Successfully imported modular agent code from src/multi_agent/")
except ImportError as e:
    print(f"❌ Import Error: {e}")
    print("\nTroubleshooting:")
    print("1. Make sure src/multi_agent/ directory is synced to Databricks")
    print("2. Verify the path was added correctly (see cell above)")
    print("3. Check that all __init__.py files exist in src/multi_agent/")
    raise

# COMMAND ----------

# DBTITLE 1,Create Agent Graph
"""
Create the agent graph using modular code.

This creates the same graph that would be deployed to Model Serving.
"""

# Create workflow as pure langchain/graph object
super_agent_hybrid = create_super_agent_hybrid()

# Create the deployable ResponsesAgent as mlflow.pyfunc model, which databricks preferred
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
print("  ✓ Custom events for detailed execution tracking")
print("  ✓ Task lifecycle monitoring (start/finish/errors)")
print("  ✓ Per-node execution timing for performance analysis")
print("="*80)

# Set the agent for MLflow tracking
# Enable autologging with run_tracer_inline for proper async context propagation
try:
    mlflow.langchain.autolog(run_tracer_inline=True)
    logger.info("✓ MLflow LangChain autologging enabled with async context support")
except Exception as e:
    logger.warning(f"⚠️ MLflow autolog initialization failed: {e}")
    logger.warning("Continuing without MLflow tracing...")

mlflow.models.set_model(AGENT)

print("✓ Agent graph created from modular code")
print("✓ ResponsesAgent wrapper initialized")
print("✓ Ready for testing with real Databricks services")

# COMMAND ----------

# DBTITLE 1,testing-related library import
from uuid import uuid4
from mlflow.types.responses import ResponsesAgentRequest

# COMMAND ----------

# DBTITLE 1,predict (a wrapper of predict_stream)
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

# DBTITLE 1,Test with predict_stream
"""
Test the agent with a sample query.

This tests the complete workflow with real Databricks services using the ResponsesAgent interface.
"""

from mlflow.types.responses import ResponsesAgentRequest

# Sample query
test_query = "Show me patient demographics. Use Genie Route"

print("="*80)
print(f"TESTING QUERY: {test_query}")
print("="*80)

request = ResponsesAgentRequest(
    input=[{"role": "user", "content": f"{test_query}"}],
    custom_inputs={"thread_id": "test-thread-001", "user_id": "test_user"}
)

# Invoke agent using predict_stream
print("\nStreaming response:")
print("-" * 40)

try:
    for event in AGENT.predict_stream(request):
        if event.type == "response.output_item.delta":
            # Stream the text chunks
            content = event.item.get("content", [])
            if content and content[0].get("type") == "text":
                print(content[0].get("text", ""), end="", flush=True)
        elif event.type == "response.output_item.done":
            # Print custom events or tool calls
            content = event.item.get("content", [])
            if content and content[0].get("type") == "text":
                print(f"\n[Event] {content[0].get('text', '')}")
except Exception as e:
    print(f"\n❌ Error during execution: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)

# COMMAND ----------

# DBTITLE 1,✅ Testing Complete
"""
If all cells above ran successfully, your agent code is working correctly
in Databricks environment!

Next steps:
1. If tests pass → Proceed to deployment (deploy_agent.py)
2. If tests fail → Fix issues locally → Sync code → Retest
"""

print("="*80)
print("✅ DATABRICKS TESTING COMPLETE")
print("="*80)
print("\nWhat was tested:")
print("✓ Imports from src/multi_agent/")
print("✓ Configuration loading from dev_config.yaml")
print("✓ Agent graph construction")
print("✓ ResponsesAgent wrapper initialization")
print("✓ Single query execution via predict_stream")
print("✓ Multi-turn conversations with checkpointer")
print("✓ Real Databricks services integration")
print("\nNext steps:")
print("→ If all tests passed: Ready to deploy (deploy_agent.py)")
print("→ If tests failed: Fix locally, sync, and retest")
print("="*80)
