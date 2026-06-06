"""
Tabular LTM (Large Tabular Model) inference contract.

Defines the provider-agnostic request/response schema and the abstract
provider interface used to embed an open-source tabular foundation model
(default: TabICLv2) inside the UnifiedChat app container.

Tabular foundation models such as TabICL use in-context learning: predictions
are produced in a single forward pass over the labeled training rows plus the
unlabeled rows to score, i.e. ``y_pred = model(X_train, y_train, X_predict)``.
The request therefore carries both a labeled ``train_rows`` context and the
``predict_rows`` to score.

Built with PriorLabs-TabPFN derived ecosystem components (TabICL); see provider
modules for attribution details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

# Task identifiers kept as plain strings so the contract stays serializable and
# does not couple callers to an enum import.
TASK_CLASSIFICATION = "classification"
TASK_REGRESSION = "regression"
_VALID_TASKS = (TASK_CLASSIFICATION, TASK_REGRESSION)


class TabularLTMError(Exception):
    """Base error for tabular LTM inference failures."""


class ModelUnavailableError(TabularLTMError):
    """Raised when the configured provider/checkpoint cannot be loaded."""


class InvalidInferenceRequest(TabularLTMError):
    """Raised when a request violates the inference contract or budget."""


class TabularPredictRequest(BaseModel):
    """A single in-context tabular prediction request.

    ``train_rows`` are labeled examples (each row must include ``target_column``)
    that form the in-context "training" set. ``predict_rows`` are the unlabeled
    rows to score (``target_column`` is ignored if present).
    """

    task: str = Field(..., description="Either 'classification' or 'regression'.")
    feature_columns: List[str] = Field(
        ..., min_length=1, description="Ordered feature column names used as model inputs."
    )
    target_column: str = Field(..., min_length=1, description="Name of the label column.")
    train_rows: List[Dict[str, Any]] = Field(
        ..., min_length=1, description="Labeled in-context rows (must contain target_column)."
    )
    predict_rows: List[Dict[str, Any]] = Field(
        ..., min_length=1, description="Unlabeled rows to score (features only)."
    )
    provider: Optional[str] = Field(
        default=None, description="Override the default provider (e.g. 'tabicl')."
    )
    return_probabilities: bool = Field(
        default=False, description="Classification only: return per-class probabilities."
    )

    @model_validator(mode="after")
    def _validate_contract(self) -> "TabularPredictRequest":
        if self.task not in _VALID_TASKS:
            raise ValueError(f"task must be one of {_VALID_TASKS}, got {self.task!r}")
        if self.target_column in self.feature_columns:
            raise ValueError("target_column must not also appear in feature_columns")
        missing_target = [
            i for i, row in enumerate(self.train_rows) if self.target_column not in row
        ]
        if missing_target:
            raise ValueError(
                f"target_column '{self.target_column}' missing from train_rows at indices "
                f"{missing_target[:5]}"
            )
        if self.task == TASK_REGRESSION and self.return_probabilities:
            raise ValueError("return_probabilities is only valid for classification tasks")
        return self


class TabularPrediction(BaseModel):
    """One predicted value, optionally with class probabilities."""

    prediction: Any = Field(..., description="Predicted label (classification) or value (regression).")
    probabilities: Optional[Dict[str, float]] = Field(
        default=None, description="Per-class probabilities when requested (classification)."
    )


class TabularPredictResponse(BaseModel):
    """Result of a tabular LTM inference call."""

    success: bool
    task: Optional[str] = None
    provider: Optional[str] = None
    model_checkpoint: Optional[str] = None
    n_train: int = 0
    n_predict: int = 0
    predictions: List[TabularPrediction] = Field(default_factory=list)
    error: Optional[str] = None


class LTMProvider(ABC):
    """Abstract embedded tabular model provider.

    Implementations load model weights once (lazily) and run inference fully
    in-process. ``X_*`` arguments are row-oriented lists of feature dicts and
    ``y_train`` is the aligned list of labels for ``X_train``.
    """

    #: Stable provider key used for selection/config (e.g. "tabicl").
    name: str = "base"

    @abstractmethod
    def predict_classification(
        self,
        feature_columns: List[str],
        X_train: List[Dict[str, Any]],
        y_train: List[Any],
        X_predict: List[Dict[str, Any]],
        *,
        return_probabilities: bool = False,
    ) -> List[TabularPrediction]:
        """Predict discrete class labels for ``X_predict``."""

    @abstractmethod
    def predict_regression(
        self,
        feature_columns: List[str],
        X_train: List[Dict[str, Any]],
        y_train: List[Any],
        X_predict: List[Dict[str, Any]],
    ) -> List[TabularPrediction]:
        """Predict continuous target values for ``X_predict``."""

    def active_checkpoint(self, task: str) -> Optional[str]:
        """Return the checkpoint identifier used for a task, if known."""
        return None
