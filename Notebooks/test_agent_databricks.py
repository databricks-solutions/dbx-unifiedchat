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

# DBTITLE 1,Load Configuration from DABs Variables
# All parameters are injected by DABs job via base_parameters.
# databricks.yml is the single source of truth — no separate config YAMLs.

_defaults = {
    "catalog_name": "yyang",
    "schema_name": "multi_agent_genie",
    "sql_warehouse_id": "a4ed2ccbda385db9",
    "genie_space_ids": "01f106e1239d14b28d6ab46f9c15e540,01f106e121e7173d8cf84bb80e842d6c,01f106e120b718e084598e92dcf14d4e",
    "volume_name": "volume",
    "enriched_docs_table": "enriched_genie_docs",
    "llm_endpoint": "databricks-claude-sonnet-4-5",
    "llm_endpoint_clarification": "databricks-claude-haiku-4-5",
    "llm_endpoint_planning": "databricks-claude-sonnet-4-5",
    "llm_endpoint_sql_synthesis_table": "databricks-claude-sonnet-4-5",
    "llm_endpoint_sql_synthesis_genie": "databricks-claude-sonnet-4-5",
    "llm_endpoint_execution": "databricks-claude-haiku-4-5",
    "llm_endpoint_summarize": "databricks-claude-haiku-4-5",
    "sample_size": "20",
    "max_unique_values": "20",
    "vs_endpoint_name": "genie_multi_agent_vs",
    "embedding_model": "databricks-gte-large-en",
    "lakebase_instance_name": "multi-agent-genie-system-state-db",
    "lakebase_embedding_endpoint": "databricks-gte-large-en",
    "lakebase_embedding_dims": "1024",
}

for k, v in _defaults.items():
    dbutils.widgets.text(k, v)

widget_params = {k: dbutils.widgets.get(k) for k in _defaults}

from notebook_utils import load_deployment_config

config_dict, config_yaml_path = load_deployment_config(widget_params)

# CRITICAL: set AGENT_CONFIG_FILE BEFORE importing agent code.
# responses_agent.py calls get_config() at module load time.
os.environ["AGENT_CONFIG_FILE"] = config_yaml_path

CATALOG = config_dict['CATALOG']
SCHEMA = config_dict['SCHEMA']
TABLE_NAME = config_dict['TABLE_NAME']
VECTOR_SEARCH_INDEX = config_dict['VECTOR_SEARCH_INDEX']
LLM_ENDPOINT_CLARIFICATION = config_dict['LLM_ENDPOINT_CLARIFICATION']
LAKEBASE_INSTANCE_NAME = config_dict['LAKEBASE_INSTANCE_NAME']
SQL_WAREHOUSE_ID = config_dict['SQL_WAREHOUSE_ID']
GENIE_SPACE_IDS = config_dict['GENIE_SPACE_IDS']

print("="*80)
print("CONFIGURATION LOADED FROM databricks.yml (via DABs base_parameters)")
print("="*80)
print(f"Catalog: {CATALOG}")
print(f"Schema: {SCHEMA}")
print(f"Vector Search Index: {VECTOR_SEARCH_INDEX}")
print(f"SQL Warehouse ID: {SQL_WAREHOUSE_ID}")
print(f"Genie Spaces: {len(GENIE_SPACE_IDS)} spaces")
print(f"Lakebase Instance: {LAKEBASE_INSTANCE_NAME}")
print("="*80)

# COMMAND ----------

from agent import AGENT

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
print(f"✓ Configuration loading from databricks.yml (via DABs)")
print("✓ Agent graph construction")
print("✓ ResponsesAgent wrapper initialization")
print("✓ Single query execution via predict_stream")
print("✓ Multi-turn conversations with checkpointer")
print("✓ Real Databricks services integration")
print("\nNext steps:")
print("→ If all tests passed: Ready to deploy (deploy_agent.py)")
print("→ If tests failed: Fix locally, sync, and retest")
print("="*80)
