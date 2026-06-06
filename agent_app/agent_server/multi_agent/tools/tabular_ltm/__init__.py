"""
Embedded tabular LTM (Large Tabular Model) inference.

Provides a provider-agnostic contract, a TabICLv2 provider, and a service layer
that loads the model in-process and runs predictions. Designed to deploy inside
the UnifiedChat app container.

Built with PriorLabs-TabPFN (the default TabICL provider incorporates
TabPFN-derived components).
"""

from .contract import (
    InvalidInferenceRequest,
    LTMProvider,
    ModelUnavailableError,
    TabularLTMError,
    TabularPrediction,
    TabularPredictRequest,
    TabularPredictResponse,
    TASK_CLASSIFICATION,
    TASK_REGRESSION,
)
from .service import (
    get_provider,
    get_tabular_prediction_tool,
    run_tabular_prediction,
    warmup_tabular_ltm,
)

__all__ = [
    "InvalidInferenceRequest",
    "LTMProvider",
    "ModelUnavailableError",
    "TabularLTMError",
    "TabularPrediction",
    "TabularPredictRequest",
    "TabularPredictResponse",
    "TASK_CLASSIFICATION",
    "TASK_REGRESSION",
    "get_provider",
    "get_tabular_prediction_tool",
    "run_tabular_prediction",
    "warmup_tabular_ltm",
]
