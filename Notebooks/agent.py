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
import logging
import mlflow

logger = logging.getLogger(__name__)

# Add src to path to import modular code
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))


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
###################
# Create the Hybrid Super Agent workflow from modular code, pure langchain/graph object
###################
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
    # mlflow.langchain.autolog(run_tracer_inline=True)
    # logger.info("✓ MLflow LangChain autologging enabled with sync context support: setting run_tracer_inline=True forces MLflow to process the traces synchronously (inline with your main code execution).")

    mlflow.langchain.autolog(run_tracer_inline=False)
    logger.info("✓ MLflow LangChain autologging enabled with async context support")
except Exception as e:
    logger.warning(f"⚠️ MLflow autolog initialization failed: {e}")
    logger.warning("Continuing without MLflow tracing...")

mlflow.models.set_model(AGENT)

print("✓ Agent graph created from modular code")
print("✓ ResponsesAgent wrapper initialized")
print("✓ Ready for testing with real Databricks services")
