from dotenv import load_dotenv
from mlflow.genai.agent_server import AgentServer, setup_mlflow_git_based_version_tracking
import logging

# Load env vars from .env before importing the agent for proper auth
load_dotenv(dotenv_path=".env", override=True)

# Need to import the agent to register the functions with the server
from agent_server import agent as agent_module  # noqa: E402

agent_server = AgentServer("ResponsesAgent", enable_chat_proxy=True)
logger = logging.getLogger(__name__)

# Define the app as a module level variable to enable multiple workers
app = agent_server.app  # noqa: F841
setup_mlflow_git_based_version_tracking()

# Mount the /api/rechart endpoint for the "Ask chart" UI feature
from agent_server.rechart_api import router as rechart_router  # noqa: E402
app.include_router(rechart_router)

# Mount the /api/tabular endpoints for embedded tabular LTM (TabICLv2) inference
from agent_server.tabular_api import router as tabular_router  # noqa: E402
app.include_router(tabular_router)


def main():
    try:
        prewarm_results = agent_module.prewarm_agent_resources_from_env()
        if prewarm_results:
            logger.info("Startup prewarm complete: %s", prewarm_results)
        agent_module.start_background_keep_warm()
    except Exception as exc:
        logger.warning("Agent prewarm setup failed: %s", exc)
    try:
        from agent_server.multi_agent.tools.tabular_ltm import warmup_tabular_ltm

        ltm_status = warmup_tabular_ltm()
        if ltm_status.get("enabled"):
            logger.info("Tabular LTM warmup: %s", ltm_status)
    except Exception as exc:
        logger.warning("Tabular LTM warmup setup failed: %s", exc)
    agent_server.run(app_import_string="agent_server.start_server:app")
