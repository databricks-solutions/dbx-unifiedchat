"""
Standalone /api/tabular endpoints exposing the embedded tabular LTM (TabICLv2)
for direct, deterministic inference from the UI or other services.

Mounted onto the MLflow AgentServer's FastAPI app in start_server.py, mirroring
the rechart_api.py pattern.
"""

import logging

from fastapi import APIRouter

from agent_server.multi_agent.tools.tabular_ltm import (
    TabularPredictRequest,
    TabularPredictResponse,
    run_tabular_prediction,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tabular")


@router.post("/predict", response_model=TabularPredictResponse)
async def predict(request: TabularPredictRequest) -> TabularPredictResponse:
    """Run an in-context tabular prediction with the embedded LTM."""
    return run_tabular_prediction(request)


@router.get("/health")
async def health() -> dict:
    """Report whether the embedded tabular LTM is enabled and loadable."""
    try:
        from agent_server.multi_agent.core.config import get_config

        ltm_cfg = get_config().tabular_ltm
        return {
            "enabled": ltm_cfg.enabled,
            "provider": ltm_cfg.provider,
            "checkpoint_dir": ltm_cfg.checkpoint_dir or None,
            "max_context_rows": ltm_cfg.max_context_rows,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tabular LTM health check failed: %s", exc)
        return {"enabled": False, "error": str(exc)}
