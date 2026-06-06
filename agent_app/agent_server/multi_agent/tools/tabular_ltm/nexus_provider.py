"""
Fundamental NEXUS provider stub (DISABLED by default, no credentials).

NEXUS is a proprietary commercial Large Tabular Model consumed as a managed
Amazon SageMaker endpoint (SageMaker JumpStart / Marketplace), NOT an in-process
embedded model. This provider therefore implements the shared ``LTMProvider``
interface but calls a remote endpoint instead of loading weights locally.

It is intentionally pre-wired but inert:
  - It is only selected when LTM_PROVIDER=nexus.
  - It raises a clear ``ModelUnavailableError`` until an endpoint name and AWS
    credentials/region are configured.

IMPORTANT: The exact request/response payload schema for the NEXUS SageMaker
endpoint is vendor-specific and must be confirmed against Fundamental's SDK /
JumpStart model card before enabling. The (de)serialization below is a clearly
marked placeholder so the integration point exists without guessing the wire
format.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .contract import (
    LTMProvider,
    ModelUnavailableError,
    TabularPrediction,
    TASK_CLASSIFICATION,
    TASK_REGRESSION,
)

logger = logging.getLogger(__name__)


class NexusProvider(LTMProvider):
    """Remote Fundamental NEXUS provider (stub).

    Reuses the same predict_classification / predict_regression contract as the
    embedded providers so callers (service, FastAPI route, LangGraph tool) need
    no changes when switching ``LTM_PROVIDER`` to ``nexus``.
    """

    name = "nexus"

    def __init__(
        self,
        *,
        endpoint_name: Optional[str] = None,
        region: Optional[str] = None,
        content_type: str = "application/json",
    ) -> None:
        self._endpoint_name = endpoint_name
        self._region = region
        self._content_type = content_type
        self._client = None

    # -- connectivity -----------------------------------------------------------

    def _require_configured(self) -> None:
        if not self._endpoint_name:
            raise ModelUnavailableError(
                "NEXUS provider is not configured. Set LTM_NEXUS_ENDPOINT (and AWS "
                "credentials/region) to enable the managed Fundamental NEXUS endpoint. "
                "NEXUS is a remote SageMaker model, not an in-container embed."
            )

    def _get_client(self):
        """Lazily build a boto3 sagemaker-runtime client.

        Imports are deferred so the app never requires boto3 unless NEXUS is
        actually selected and configured.
        """
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - only without boto3
                raise ModelUnavailableError(
                    "boto3 is required for the NEXUS provider. Add 'boto3' to dependencies "
                    "to call the managed SageMaker endpoint."
                ) from exc
            self._client = boto3.client("sagemaker-runtime", region_name=self._region)
        return self._client

    def warmup(self) -> None:
        """No local weights to load; validate configuration only (no network)."""
        self._require_configured()

    def active_checkpoint(self, task: str) -> Optional[str]:
        return self._endpoint_name

    # -- payload (de)serialization — PLACEHOLDER --------------------------------

    def _build_payload(
        self,
        task: str,
        feature_columns: List[str],
        X_train: List[Dict[str, Any]],
        y_train: List[Any],
        X_predict: List[Dict[str, Any]],
        return_probabilities: bool,
    ) -> Dict[str, Any]:
        # TODO(nexus): Confirm the exact request schema against Fundamental's
        # SageMaker JumpStart model card / SDK before enabling in production.
        return {
            "task": task,
            "feature_columns": feature_columns,
            "train_rows": X_train,
            "train_labels": y_train,
            "predict_rows": X_predict,
            "return_probabilities": return_probabilities,
        }

    def _invoke(self, payload: Dict[str, Any]) -> Any:
        client = self._get_client()
        response = client.invoke_endpoint(
            EndpointName=self._endpoint_name,
            ContentType=self._content_type,
            Body=json.dumps(payload).encode("utf-8"),
        )
        body = response["Body"].read()
        return json.loads(body)

    def _parse_predictions(
        self, raw: Any, return_probabilities: bool
    ) -> List[TabularPrediction]:
        # TODO(nexus): Map the real NEXUS response shape. Assumes a list of
        # {"prediction": ..., "probabilities": {...}} objects as a placeholder.
        records = raw.get("predictions", raw) if isinstance(raw, dict) else raw
        results: List[TabularPrediction] = []
        for rec in records:
            if isinstance(rec, dict):
                results.append(
                    TabularPrediction(
                        prediction=rec.get("prediction"),
                        probabilities=rec.get("probabilities") if return_probabilities else None,
                    )
                )
            else:
                results.append(TabularPrediction(prediction=rec))
        return results

    # -- inference --------------------------------------------------------------

    def predict_classification(
        self,
        feature_columns: List[str],
        X_train: List[Dict[str, Any]],
        y_train: List[Any],
        X_predict: List[Dict[str, Any]],
        *,
        return_probabilities: bool = False,
    ) -> List[TabularPrediction]:
        self._require_configured()
        payload = self._build_payload(
            TASK_CLASSIFICATION, feature_columns, X_train, y_train, X_predict, return_probabilities
        )
        raw = self._invoke(payload)
        return self._parse_predictions(raw, return_probabilities)

    def predict_regression(
        self,
        feature_columns: List[str],
        X_train: List[Dict[str, Any]],
        y_train: List[Any],
        X_predict: List[Dict[str, Any]],
    ) -> List[TabularPrediction]:
        self._require_configured()
        payload = self._build_payload(
            TASK_REGRESSION, feature_columns, X_train, y_train, X_predict, False
        )
        raw = self._invoke(payload)
        return self._parse_predictions(raw, return_probabilities=False)
