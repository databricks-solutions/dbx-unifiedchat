# Databricks notebook source
# DBTITLE 1,Interactive Agent Experiment Notebook
"""
Interactive notebook for experimenting with the agent on Databricks.

Reads config directly from databricks.yml in the repo — no DABs job needed.
Widgets are pre-populated with resolved values so you can override any
parameter visually in the notebook UI without editing code.

How it works:
  1. Reads databricks.yml (already synced to workspace by DABs)
  2. Flattens variables + target overrides into a flat dict
  3. Creates widgets pre-filled with those values (edit in the UI to override)
  4. Injects AgentConfig singleton in memory (no file I/O)
  5. Imports the agent — get_config() finds config already loaded

To change a parameter (e.g., swap a model):
  - Option A: Edit the widget value in the notebook UI bar, then re-run
              from the "Apply Widget Overrides" cell onward.
  - Option B: Edit the _code_overrides dict in the config cell,
              %restart_python, and re-run from the top.

To switch targets (dev/prod):
  Change TARGET = "dev" to TARGET = "prod"
"""

# COMMAND ----------

# DBTITLE 1,Install Packages
# MAGIC %pip install python-dotenv databricks-sdk==0.84.0 databricks-sql-connector==4.2.4 databricks-langchain[memory]==0.12.1 databricks-vectorsearch==0.63 databricks-agents==1.9.3 mlflow[databricks]>=3.6.0 pyyaml

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

# DBTITLE 1,Autoreload for dev iteration
# MAGIC %load_ext autoreload
# MAGIC %autoreload 2

# COMMAND ----------

# DBTITLE 1,Setup Path + Load Defaults from databricks.yml
import sys
import os
import yaml
import logging

logger = logging.getLogger(__name__)

notebook_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
repo_root = os.path.dirname(notebook_dir)
src_path = os.path.join(repo_root, "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

print(f"Repo root: {repo_root}")
print(f"Source path: {src_path}")

# --- Read databricks.yml (single source of truth) ---
TARGET = "dev"

bundle_yml_path = os.path.join(repo_root, "databricks.yml")
with open(bundle_yml_path) as f:
    bundle = yaml.safe_load(f)

params = {k: v.get("default") for k, v in bundle["variables"].items()}
target_overrides = bundle.get("targets", {}).get(TARGET, {}).get("variables", {})
params.update(target_overrides)

print(f"Loaded {len(params)} parameters from databricks.yml (target={TARGET})")

# --- Create widgets pre-filled with databricks.yml values ---
# Edit any widget in the notebook UI bar to override before running the next cell.
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
    default_val = params.get(k, "")
    if isinstance(default_val, list):
        default_val = ",".join(str(x) for x in default_val)
    dbutils.widgets.text(k, str(default_val))

print(f"Created {len(_WIDGET_KEYS)} widgets (pre-filled from databricks.yml)")
print("Edit any widget in the UI bar above, then run the next cell to apply.")

# COMMAND ----------

# DBTITLE 1,Apply Widget Overrides + Initialize Config
# Read back widget values — picks up any UI edits the user made.
widget_values = {k: dbutils.widgets.get(k) for k in _WIDGET_KEYS}

# Detect which values the user changed from the databricks.yml defaults.
_widget_overrides = {}
for k in _WIDGET_KEYS:
    original = params.get(k, "")
    if isinstance(original, list):
        original = ",".join(str(x) for x in original)
    if widget_values[k] != str(original):
        _widget_overrides[k] = widget_values[k]

# Merge: databricks.yml defaults < widget UI edits < code overrides
final_params = dict(params)
final_params.update(widget_values)

# Code-level overrides take highest priority (uncomment to use)
_code_overrides = {
    # "llm_endpoint_planning": "databricks-claude-sonnet-4-5",
    # "llm_endpoint_clarification": "databricks-claude-haiku-4-5",
}
final_params.update(_code_overrides)

if _widget_overrides:
    print(f"Widget overrides detected: {_widget_overrides}")
if _code_overrides:
    print(f"Code overrides applied: {list(_code_overrides.keys())}")
if not _widget_overrides and not _code_overrides:
    print("No overrides — using databricks.yml defaults as-is.")

# --- Inject config singleton directly (no YAML file write) ---
import multi_agent.core.config as _cfg_mod
from multi_agent.core.config import AgentConfig

_mc_shim = type("MC", (), {"get": lambda self, k, d=None: final_params.get(k, d)})()
_cfg_mod._config = AgentConfig.from_model_config(_mc_shim)
_cfg_mod._config.validate()
_cfg_mod._config.print_summary()

# COMMAND ----------

# DBTITLE 1,Create Agent
import mlflow

from agent import AGENT

# COMMAND ----------

# DBTITLE 1,Test: predict
from uuid import uuid4
from mlflow.types.responses import ResponsesAgentRequest

query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? use table route"
thread_id = f"exp-{str(uuid4())[:8]}"
print(f"thread_id: {thread_id}")
print(f"query: {query}")
print("=" * 80)

result = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": query}],
    custom_inputs={"thread_id": thread_id}
))

# COMMAND ----------

# DBTITLE 1,Test: predict_stream
from uuid import uuid4
from mlflow.types.responses import ResponsesAgentRequest

query_stream = "Show me patient demographics. Use Genie Route"
thread_id_stream = f"exp-stream-{str(uuid4())[:8]}"

print(f"thread_id: {thread_id_stream}")
print(f"query: {query_stream}")
print("=" * 80)

request = ResponsesAgentRequest(
    input=[{"role": "user", "content": query_stream}],
    custom_inputs={"thread_id": thread_id_stream, "user_id": "test_user"}
)

print("\nStreaming response:")
print("-" * 40)

try:
    for event in AGENT.predict_stream(request):
        if event.type == "response.output_item.delta":
            content = event.item.get("content", [])
            if content and content[0].get("type") == "text":
                print(content[0].get("text", ""), end="", flush=True)
        elif event.type == "response.output_item.done":
            content = event.item.get("content", [])
            if content and content[0].get("type") == "text":
                print(f"\n[Event] {content[0].get('text', '')}")
except Exception as e:
    print(f"\nError during execution: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)

# COMMAND ----------

# DBTITLE 1,Test: Multi-turn follow-up (reuse thread_id from predict cell)
from mlflow.types.responses import ResponsesAgentRequest

followup = "Now break that down by age group"
print(f"thread_id (reusing): {thread_id}")
print(f"follow-up query: {followup}")
print("=" * 80)

result2 = AGENT.predict(ResponsesAgentRequest(
    input=[{"role": "user", "content": followup}],
    custom_inputs={"thread_id": thread_id}
))
