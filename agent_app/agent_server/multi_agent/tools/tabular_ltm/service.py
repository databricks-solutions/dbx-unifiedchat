"""
Service layer for embedded tabular LTM inference.

Resolves configuration (``TabularLTMConfig`` from the central app config),
lazily builds and caches a single provider instance, enforces the in-context
row budget, and dispatches requests to the provider. Both the FastAPI route and
the LangGraph tool call :func:`run_tabular_prediction`.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from .contract import (
    InvalidInferenceRequest,
    LTMProvider,
    ModelUnavailableError,
    TabularLTMError,
    TabularPredictRequest,
    TabularPredictResponse,
    TASK_CLASSIFICATION,
)

logger = logging.getLogger(__name__)

_provider: Optional[LTMProvider] = None
_provider_lock = threading.Lock()


def _build_provider(ltm_cfg) -> LTMProvider:
    """Instantiate the configured provider. Only TabICL is wired today."""
    provider_name = (ltm_cfg.provider or "tabicl").lower()
    if provider_name != "tabicl":
        raise ModelUnavailableError(
            f"Unsupported LTM provider '{provider_name}'. Only 'tabicl' is currently embedded."
        )

    from .tabicl_provider import TabICLProvider

    return TabICLProvider(
        classifier_path=ltm_cfg.classifier_path,
        regressor_path=ltm_cfg.regressor_path,
        classifier_checkpoint=ltm_cfg.classifier_checkpoint,
        regressor_checkpoint=ltm_cfg.regressor_checkpoint,
        device=ltm_cfg.device_or_none,
        n_estimators=ltm_cfg.n_estimators,
        allow_auto_download=ltm_cfg.allow_auto_download,
    )


def _get_ltm_config():
    from agent_server.multi_agent.core.config import get_config

    return get_config().tabular_ltm


def get_provider(force_reload: bool = False) -> LTMProvider:
    """Return the cached provider, building it on first use."""
    global _provider
    if _provider is None or force_reload:
        with _provider_lock:
            if _provider is None or force_reload:
                _provider = _build_provider(_get_ltm_config())
    return _provider


def warmup_tabular_ltm() -> Dict[str, Any]:
    """Eagerly load model weights at startup when the feature is enabled.

    Returns a small status dict; never raises so app startup is not blocked by
    an optional model failing to load.
    """
    status: Dict[str, Any] = {"enabled": False, "loaded": False}
    try:
        ltm_cfg = _get_ltm_config()
        status["enabled"] = ltm_cfg.enabled
        if not ltm_cfg.enabled:
            return status
        provider = get_provider()
        warmup = getattr(provider, "warmup", None)
        if callable(warmup):
            warmup()
        status["loaded"] = True
        status["provider"] = provider.name
    except Exception as exc:
        logger.warning("Tabular LTM warmup skipped: %s", exc)
        status["error"] = str(exc)
    return status


def _enforce_budget(request: TabularPredictRequest, max_context_rows: int) -> None:
    if max_context_rows and len(request.train_rows) > max_context_rows:
        raise InvalidInferenceRequest(
            f"train_rows={len(request.train_rows)} exceeds LTM_MAX_CONTEXT_ROWS={max_context_rows}. "
            "Reduce the in-context training set or raise the configured budget."
        )


def run_tabular_prediction(request: TabularPredictRequest) -> TabularPredictResponse:
    """Validate, dispatch, and run a tabular LTM inference request."""
    n_train = len(request.train_rows)
    n_predict = len(request.predict_rows)
    try:
        ltm_cfg = _get_ltm_config()
        if not ltm_cfg.enabled:
            raise ModelUnavailableError(
                "Tabular LTM is disabled. Set LTM_ENABLED=true to enable embedded inference."
            )
        _enforce_budget(request, ltm_cfg.max_context_rows)

        if request.provider and request.provider.lower() != ltm_cfg.provider.lower():
            raise ModelUnavailableError(
                f"Requested provider '{request.provider}' is not the configured provider "
                f"'{ltm_cfg.provider}'."
            )

        provider = get_provider()
        y_train = [row.get(request.target_column) for row in request.train_rows]

        if request.task == TASK_CLASSIFICATION:
            predictions = provider.predict_classification(
                request.feature_columns,
                request.train_rows,
                y_train,
                request.predict_rows,
                return_probabilities=request.return_probabilities,
            )
        else:
            predictions = provider.predict_regression(
                request.feature_columns,
                request.train_rows,
                y_train,
                request.predict_rows,
            )

        return TabularPredictResponse(
            success=True,
            task=request.task,
            provider=provider.name,
            model_checkpoint=provider.active_checkpoint(request.task),
            n_train=n_train,
            n_predict=n_predict,
            predictions=predictions,
        )
    except TabularLTMError as exc:
        logger.warning("Tabular LTM request rejected: %s", exc)
        return TabularPredictResponse(
            success=False, task=request.task, error=str(exc), n_train=n_train, n_predict=n_predict
        )
    except Exception as exc:  # noqa: BLE001 - surface any model error as a clean response
        logger.exception("Tabular LTM inference failed")
        return TabularPredictResponse(
            success=False, task=request.task, error=str(exc), n_train=n_train, n_predict=n_predict
        )


def get_tabular_prediction_tool():
    """Build a LangChain/LangGraph StructuredTool wrapping the LTM service.

    Returns ``None`` if langchain is unavailable so the import never breaks the
    agent graph in minimal environments.
    """
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not build tabular prediction tool: %s", exc)
        return None

    def _predict_tabular(
        task: str,
        feature_columns: List[str],
        target_column: str,
        train_rows: List[Dict[str, Any]],
        predict_rows: List[Dict[str, Any]],
        return_probabilities: bool = False,
    ) -> Dict[str, Any]:
        request = TabularPredictRequest(
            task=task,
            feature_columns=feature_columns,
            target_column=target_column,
            train_rows=train_rows,
            predict_rows=predict_rows,
            return_probabilities=return_probabilities,
        )
        return run_tabular_prediction(request).model_dump()

    return StructuredTool.from_function(
        func=_predict_tabular,
        name="tabular_predict",
        description=(
            "Predict labels or values for tabular rows using the embedded tabular "
            "foundation model (TabICLv2). Provide labeled 'train_rows' (each containing "
            "the target_column) as in-context examples and 'predict_rows' to score. "
            "Use task='classification' for categorical targets or task='regression' for "
            "numeric targets. Set return_probabilities=true to get per-class probabilities."
        ),
    )
