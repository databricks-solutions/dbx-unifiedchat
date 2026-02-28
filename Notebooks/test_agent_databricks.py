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

import yaml
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
    from multi_agent.core.graph import create_agent_graph
    from multi_agent.core.state import get_initial_state, AgentState
    from multi_agent.agents import (
        unified_intent_context_clarification_node,
        planning_node,
        sql_synthesis_table_node,
        sql_synthesis_genie_node,
        sql_execution_node,
        summarize_node
    )
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

from langgraph.graph import StateGraph, END

# Create workflow (matching structure in src/multi_agent/core/graph.py)
workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("unified_intent_context_clarification", unified_intent_context_clarification_node)
workflow.add_node("planning", planning_node)
workflow.add_node("sql_synthesis_table", sql_synthesis_table_node)
workflow.add_node("sql_synthesis_genie", sql_synthesis_genie_node)
workflow.add_node("sql_execution", sql_execution_node)
workflow.add_node("summarize", summarize_node)

# Define routing logic
def route_after_unified(state: AgentState) -> str:
    if state.get("is_meta_question", False):
        return END
    if state.get("question_clear", False):
        return "planning"
    return END

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
    return "summarize"

# Add routing
workflow.set_entry_point("unified_intent_context_clarification")
workflow.add_conditional_edges("unified_intent_context_clarification", route_after_unified, {"planning": "planning", END: END})
workflow.add_conditional_edges("planning", route_after_planning, {"sql_synthesis_table": "sql_synthesis_table", "sql_synthesis_genie": "sql_synthesis_genie", "summarize": "summarize"})
workflow.add_conditional_edges("sql_synthesis_table", route_after_synthesis, {"sql_execution": "sql_execution", "summarize": "summarize"})
workflow.add_conditional_edges("sql_synthesis_genie", route_after_synthesis, {"sql_execution": "sql_execution", "summarize": "summarize"})
workflow.add_edge("sql_execution", "summarize")
workflow.add_edge("summarize", END)

# Compile WITHOUT checkpointer for simple testing
agent = workflow.compile()

print("✓ Agent graph created from modular code")
print("✓ Ready for testing with real Databricks services")

# COMMAND ----------

# DBTITLE 1,Test with Sample Query
"""
Test the agent with a sample query.

This tests the complete workflow with real Databricks services.
"""

# Sample query
test_query = "Show me patient demographics"

# Create initial state
initial_state = get_initial_state()
initial_state["messages"] = [{"role": "user", "content": test_query}]

print("="*80)
print(f"TESTING QUERY: {test_query}")
print("="*80)

# Invoke agent
response = agent.invoke(initial_state)

# Display response
if response.get("final_response"):
    print("\n✓ RESPONSE:")
    print(response["final_response"])
elif response.get("meta_answer"):
    print("\n✓ META ANSWER:")
    print(response["meta_answer"])
elif response.get("pending_clarification"):
    clarification = response["pending_clarification"]
    print(f"\n✓ CLARIFICATION NEEDED:")
    print(clarification["reason"])
    print("\nOptions:")
    for i, option in enumerate(clarification["options"], 1):
        print(f"  {i}. {option}")
else:
    print("\n⚠️ No response generated")

print("\n" + "="*80)

# COMMAND ----------

# DBTITLE 1,Test with Your Own Query
"""
Run your own test queries here.

Change the query below and run this cell to test different scenarios.
"""

# YOUR QUERY HERE
my_query = "Show me patients with high blood pressure and their medications"

# Run query
initial_state = get_initial_state()
initial_state["messages"] = [{"role": "user", "content": my_query}]

print(f"Query: {my_query}\n")
response = agent.invoke(initial_state)

# Display response
if response.get("final_response"):
    print(response["final_response"])
elif response.get("pending_clarification"):
    print("Clarification needed:", response["pending_clarification"]["reason"])

# COMMAND ----------

# DBTITLE 1,Test Multi-Turn Conversation
"""
Test multi-turn conversation with checkpointer.

This tests conversation state persistence.
"""

from databricks_langchain.checkpoint import DatabricksCheckpointSaver
from databricks.sdk import WorkspaceClient

# Create agent with checkpointer
w = WorkspaceClient()
checkpointer = DatabricksCheckpointSaver(w.lakebase, database_instance_name=LAKEBASE_INSTANCE_NAME)
agent_with_memory = workflow.compile(checkpointer=checkpointer)

# Thread ID for conversation
thread_id = "test-thread-123"

# First turn
print("="*80)
print("MULTI-TURN CONVERSATION TEST")
print("="*80)

turn1_state = get_initial_state(thread_id=thread_id)
turn1_state["messages"] = [{"role": "user", "content": "Show me patients"}]

print("\n👤 Turn 1: Show me patients")
response1 = agent_with_memory.invoke(turn1_state, config={"configurable": {"thread_id": thread_id}})
print(f"🤖 Agent: {response1.get('final_response', 'No response')[:200]}...")

# Second turn (follow-up)
turn2_state = get_initial_state(thread_id=thread_id)
turn2_state["messages"] = [{"role": "user", "content": "What about their medications?"}]

print("\n👤 Turn 2: What about their medications?")
response2 = agent_with_memory.invoke(turn2_state, config={"configurable": {"thread_id": thread_id}})
print(f"🤖 Agent: {response2.get('final_response', 'No response')[:200]}...")

print("\n✓ Multi-turn conversation test complete")

# COMMAND ----------

# DBTITLE 1,Inspect Agent State (Debugging)
"""
Inspect the agent state for debugging.

Use this to see what's in the state at any point.
"""

# Run a query and inspect the response
test_state = get_initial_state()
test_state["messages"] = [{"role": "user", "content": "test query"}]
result = agent.invoke(test_state)

# Show state keys
print("State keys:")
for key in sorted(result.keys()):
    value = result[key]
    if value is not None:
        value_str = str(value)[:100] if len(str(value)) > 100 else str(value)
        print(f"  {key}: {value_str}")

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
print("✓ Single query execution")
print("✓ Multi-turn conversations with checkpointer")
print("✓ Real Databricks services integration")
print("\nNext steps:")
print("→ If all tests passed: Ready to deploy (deploy_agent.py)")
print("→ If tests failed: Fix locally, sync, and retest")
print("="*80)
