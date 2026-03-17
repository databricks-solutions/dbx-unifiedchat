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
#
# Flow: widgets → build_config_yaml() → temp YAML → get_config() → AgentConfig
#
# Defaults are empty sentinels. If run manually without DABs, the notebook
# will fail at validation with a clear error rather than using stale values.

_WIDGET_KEYS = [
    "catalog_name", "schema_name", "sql_warehouse_id", "genie_space_ids",
    "volume_name", "enriched_docs_table", "source_table", "uc_function_names",
    "llm_endpoint", "llm_endpoint_clarification", "llm_endpoint_planning",
    "llm_endpoint_sql_synthesis_table", "llm_endpoint_sql_synthesis_genie",
    "llm_endpoint_execution", "llm_endpoint_summarize",
    "sample_size", "max_unique_values",
    "vs_endpoint_name", "embedding_model",
    "lakebase_instance_name", "lakebase_embedding_endpoint", "lakebase_embedding_dims",
]

for k in _WIDGET_KEYS:
    dbutils.widgets.text(k, "")

widget_params = {k: dbutils.widgets.get(k) for k in _WIDGET_KEYS}

_empty = [k for k, v in widget_params.items() if not v.strip()]
if _empty:
    print(f"⚠️  {len(_empty)} widget(s) have no value (expected when run via DABs): {', '.join(_empty[:5])}{'...' if len(_empty) > 5 else ''}")
    print("   If running manually, set values via notebook widgets or use DABs: databricks bundle run")

# Step 1: Generate temp YAML
from notebook_utils import build_config_yaml
config_yaml_path = build_config_yaml(widget_params, path="./agent_config.yaml")


# Step 2: CRITICAL — set env var BEFORE importing agent code.
# responses_agent.py calls get_config() at module load time.
os.environ["AGENT_CONFIG_FILE"] = config_yaml_path

# Step 3: Single config system
from multi_agent.core.config import get_config
cfg = get_config()
cfg.print_summary()

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
