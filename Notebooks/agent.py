"""
MLflow agent wrapper for Model Serving deployment.

This file is referenced by deploy_agent.py in mlflow.pyfunc.log_model().
It imports the modular agent code from src/multi_agent/ which gets packaged
via the code_paths parameter.

The agent is deployed as a ResponsesAgent following Databricks best practices.
"""
###########################
## loading libraries
###########################
import sys
import os

# Add src to path to import modular code
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

from multi_agent.core.graph import create_super_agent_hybrid
from multi_agent.core.responses_agent import SuperAgentHybridResponsesAgent

###################
# Create the Hybrid Super Agent workflow from modular code, pure langchain/graph object
###################
super_agent_hybrid = create_super_agent_hybrid()

# Create the deployable agent
AGENT = SuperAgentHybridResponsesAgent(super_agent_hybrid)

print("\n" + "="*80)
print("✅ HYBRID SUPER AGENT RESPONSES AGENT CREATED")
print("="*80)
print("Architecture: OOP Agents + Explicit State Management")
print("Benefits:")
print("  ✓ Modular and testable agent classes")
print("  ✓ Full state observability for debugging")
print("  ✓ Predictable execution flow")
print("  ✓ Memory support (short-term & long-term)")
print("="*80)
