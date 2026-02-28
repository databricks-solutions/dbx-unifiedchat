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

# Create workflow
super_agent_hybrid = create_super_agent_hybrid()

# Create the deployable ResponsesAgent
agent = SuperAgentHybridResponsesAgent(super_agent_hybrid)

print("✓ Agent graph created from modular code")
print("✓ ResponsesAgent wrapper initialized")
print("✓ Ready for testing with real Databricks services")

# COMMAND ----------

# DBTITLE 1,Test with Sample Query
"""
Test the agent with a sample query.

This tests the complete workflow with real Databricks services using the ResponsesAgent interface.
"""

from mlflow.types.responses import ResponsesAgentRequest
from mlflow.types.llm import ChatMessage

# Sample query
test_query = "Show me patient demographics"

print("="*80)
print(f"TESTING QUERY: {test_query}")
print("="*80)

request = ResponsesAgentRequest(
    input=[ChatMessage(role="user", content=test_query)],
    custom_inputs={"thread_id": "test-thread-001", "user_id": "test_user"}
)

# Invoke agent using predict_stream
print("\nStreaming response:")
print("-" * 40)

try:
    for event in agent.predict_stream(request):
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

# DBTITLE 1,Test with Your Own Query
"""
Run your own test queries here.

Change the query below and run this cell to test different scenarios.
"""

# YOUR QUERY HERE
my_query = "Show me patients with high blood pressure and their medications"

print(f"Query: {my_query}\n")

request = ResponsesAgentRequest(
    input=[ChatMessage(role="user", content=my_query)],
    custom_inputs={"thread_id": "test-thread-002", "user_id": "test_user"}
)

print("\nStreaming response:")
print("-" * 40)

for event in agent.predict_stream(request):
    if event.type == "response.output_item.delta":
        content = event.item.get("content", [])
        if content and content[0].get("type") == "text":
            print(content[0].get("text", ""), end="", flush=True)
    elif event.type == "response.output_item.done":
        content = event.item.get("content", [])
        if content and content[0].get("type") == "text":
            print(f"\n[Event] {content[0].get('text', '')}")

# COMMAND ----------

# DBTITLE 1,Test Multi-Turn Conversation
"""
Test multi-turn conversation with checkpointer.

This tests conversation state persistence across requests using the same thread_id.
"""

# Thread ID for conversation
thread_id = "multi-turn-test-123"

# First turn
print("="*80)
print("MULTI-TURN CONVERSATION TEST")
print("="*80)

print("\n👤 Turn 1: Show me patients")
request1 = ResponsesAgentRequest(
    input=[ChatMessage(role="user", content="Show me patients")],
    custom_inputs={"thread_id": thread_id, "user_id": "test_user"}
)

print("🤖 Agent: ", end="")
for event in agent.predict_stream(request1):
    if event.type == "response.output_item.delta":
        content = event.item.get("content", [])
        if content and content[0].get("type") == "text":
            print(content[0].get("text", ""), end="", flush=True)
print()

# Second turn (follow-up)
print("\n👤 Turn 2: What about their medications?")
request2 = ResponsesAgentRequest(
    input=[ChatMessage(role="user", content="What about their medications?")],
    custom_inputs={"thread_id": thread_id, "user_id": "test_user"}
)

print("🤖 Agent: ", end="")
for event in agent.predict_stream(request2):
    if event.type == "response.output_item.delta":
        content = event.item.get("content", [])
        if content and content[0].get("type") == "text":
            print(content[0].get("text", ""), end="", flush=True)
print()

print("\n✓ Multi-turn conversation test complete")

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
