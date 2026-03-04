"""
Local dev entrypoint for LangGraph Studio.

Loads dev_config.yaml and compiles the agent graph with MemorySaver so
interrupt() works locally without a Databricks Lakebase connection.

LangGraph Studio / `langgraph dev` picks this up via langgraph.json:
    "graphs": { "agent": "./bin/create_graph.py:graph" }
"""

import os
import sys

# Allow imports from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.multi_agent.core.graph import create_super_agent_hybrid
from src.multi_agent.core.config import get_config

config = get_config()
# LangGraph Studio/API injects its own checkpointer — do not pass one here.
# interrupt() will work via the platform's built-in persistence.
graph = create_super_agent_hybrid(config=config).compile()
