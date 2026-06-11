"""
TabICLv2 provider for embedded tabular inference.

TabICLv2 is an open-source tabular foundation model (BSD-3-Clause, with some
Apache-2.0 components derived from TabPFN-TS). It performs in-context learning:
``fit(X_train, y_train)`` loads the checkpoint and stores the context, then
``predict(X_predict)`` scores rows in a single forward pass.

Heavy dependencies (``tabicl``, ``torch``, ``pandas``, ``numpy``) are imported
lazily so the app starts even when the LTM feature is disabled or the packages
are absent. Estimator instances are cached per task to avoid re-reading the
checkpoint file on every request.

Built with PriorLabs-TabPFN (TabICL incorporates TabPFN-derived components).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional

from .contract import (
    LTMProvider,
    ModelUnavailableError,
    TabularPrediction,
    TASK_CLASSIFICATION,
    TASK_REGRESSION,
)

logger = logging.getLogger(__name__)


class TabICLProvider(LTMProvider):
    """Embedded TabICLv2 classifier/regressor provider."""

    name = "tabicl"

    def __init__(
        self,
        *,
        classifier_path: Optional[str] = None,
        regressor_path: Optional[str] = None,
        classifier_checkpoint: str = "tabicl-classifier-v2-20260212.ckpt",
        regressor_checkpoint: str = "tabicl-regressor-v2-20260212.ckpt",
        device: Optional[str] = None,
        n_estimators: int = 8,
        allow_auto_download: bool = False,
    ) -> None:
        self._classifier_path = classifier_path
        self._regressor_path = regressor_path
        self._classifier_checkpoint = classifier_checkpoint
        self._regressor_checkpoint = regressor_checkpoint
        self._device = device
        self._n_estimators = n_estimators
        self._allow_auto_download = allow_auto_download

        # Cached estimator instances (hold loaded weights). Guarded by a lock so
        # concurrent FastAPI/agent requests do not build duplicate models.
        self._classifier = None
        self._regressor = None
        self._lock = threading.Lock()

    # -- estimator construction -------------------------------------------------

    def _resolve_checkpoint(self, checkpoint_path: Optional[str], checkpoint_name: str) -> Optional[str]:
        """Resolve a checkpoint to a locally-readable file path.

        Staging checkpoints into a UC Volume is the prep job's responsibility
        (``04_prepare_shared_infra.py``); the app only holds read access.
        Resolution order:

        1. Configured path exists on the local filesystem -> use it directly
           (clusters/notebooks/local dev where the path is reachable).
        2. Path is a ``/Volumes/...`` UC path that is not mounted (Databricks
           Apps do not FUSE-mount Volumes) -> download it via the Files API into
           a local cache and use that copy.
        3. Still unavailable but ``allow_auto_download`` -> return ``None`` so
           TabICL fetches the checkpoint into its own local cache.
        4. Otherwise -> raise with remediation guidance.
        """
        if not checkpoint_path:
            return None

        path = Path(checkpoint_path)
        try:
            exists = path.exists()
        except OSError:
            # e.g. an unreachable /Volumes path on a local machine; treat as
            # missing rather than propagating the OS error.
            exists = False

        if exists:
            return str(path)

        # Databricks Apps (and other non-FUSE runtimes) do not mount UC Volumes
        # as a local filesystem path, so a "/Volumes/..." path never "exists"
        # even when the app's service principal has READ_VOLUME. Pull it down via
        # the Files API and load the local copy instead.
        if checkpoint_path.startswith("/Volumes/"):
            local_path = self._download_from_volume(checkpoint_path, checkpoint_name)
            if local_path:
                return local_path

        if self._allow_auto_download:
            logger.info(
                "TabICL checkpoint %s not found at %s; falling back to TabICL's "
                "local auto-download cache.",
                checkpoint_name,
                checkpoint_path,
            )
            return None

        raise ModelUnavailableError(
            f"TabICL checkpoint '{checkpoint_name}' not found at '{checkpoint_path}'. "
            "Stage checkpoints by running the shared-infra prep job "
            "(04_prepare_shared_infra.py). On Databricks Apps the Volume is read "
            "via the Files API, which requires the app's service principal to hold "
            "READ_VOLUME (plus USE_CATALOG/USE_SCHEMA); alternatively set "
            "LTM_ALLOW_AUTO_DOWNLOAD=true to download the checkpoint into the local cache."
        )

    def _local_cache_dir(self) -> Path:
        base = os.getenv("LTM_LOCAL_CACHE_DIR", "").strip() or os.path.join(
            tempfile.gettempdir(), "tabicl_checkpoints"
        )
        return Path(base)

    def _download_from_volume(
        self, volume_path: str, checkpoint_name: str
    ) -> Optional[str]:
        """Download a UC Volume checkpoint to a local cache via the Files API.

        Returns the local file path, or ``None`` if the download is not possible
        (so the caller can fall back to auto-download or raise). The download is
        cached on local disk so subsequent model builds in the same container
        reuse the staged copy.
        """
        cache_dir = self._local_cache_dir()
        local_file = cache_dir / checkpoint_name

        # Reuse a previously staged copy.
        try:
            if local_file.exists() and local_file.stat().st_size > 0:
                return str(local_file)
        except OSError:
            pass

        try:
            from databricks.sdk import WorkspaceClient

            cache_dir.mkdir(parents=True, exist_ok=True)
            client = WorkspaceClient()
            logger.info(
                "Downloading TabICL checkpoint %s from UC Volume %s via Files API...",
                checkpoint_name,
                volume_path,
            )
            response = client.files.download(volume_path)
            tmp_file = local_file.with_name(local_file.name + ".part")
            with open(tmp_file, "wb") as handle:
                shutil.copyfileobj(response.contents, handle)
            tmp_file.replace(local_file)
            logger.info(
                "Staged TabICL checkpoint %s -> %s (%d bytes)",
                checkpoint_name,
                local_file,
                local_file.stat().st_size,
            )
            return str(local_file)
        except Exception as exc:  # noqa: BLE001 - fall back to other strategies
            logger.warning(
                "Failed to download TabICL checkpoint %s from %s via Files API: %s",
                checkpoint_name,
                volume_path,
                exc,
            )
            return None

    def _build_classifier(self):
        try:
            from tabicl import TabICLClassifier
        except ImportError as exc:  # pragma: no cover - exercised only without dep
            raise ModelUnavailableError(
                "tabicl is not installed. Add 'tabicl' to dependencies to enable the "
                "embedded tabular model."
            ) from exc

        classifier_path = self._resolve_checkpoint(
            self._classifier_path,
            self._classifier_checkpoint,
        )
        return TabICLClassifier(
            n_estimators=self._n_estimators,
            model_path=classifier_path,
            checkpoint_version=self._classifier_checkpoint,
            allow_auto_download=self._allow_auto_download if classifier_path is None else False,
            device=self._device,
            random_state=42,
        )

    def _build_regressor(self):
        try:
            from tabicl import TabICLRegressor
        except ImportError as exc:  # pragma: no cover - exercised only without dep
            raise ModelUnavailableError(
                "tabicl is not installed. Add 'tabicl' to dependencies to enable the "
                "embedded tabular model."
            ) from exc

        regressor_path = self._resolve_checkpoint(
            self._regressor_path,
            self._regressor_checkpoint,
        )
        return TabICLRegressor(
            n_estimators=self._n_estimators,
            model_path=regressor_path,
            checkpoint_version=self._regressor_checkpoint,
            allow_auto_download=self._allow_auto_download if regressor_path is None else False,
            device=self._device,
            random_state=42,
        )

    def _get_classifier(self):
        if self._classifier is None:
            with self._lock:
                if self._classifier is None:
                    logger.info(
                        "Loading TabICL classifier (path=%s, checkpoint=%s, device=%s)",
                        self._classifier_path,
                        self._classifier_checkpoint,
                        self._device or "auto",
                    )
                    self._classifier = self._build_classifier()
        return self._classifier

    def _get_regressor(self):
        if self._regressor is None:
            with self._lock:
                if self._regressor is None:
                    logger.info(
                        "Loading TabICL regressor (path=%s, checkpoint=%s, device=%s)",
                        self._regressor_path,
                        self._regressor_checkpoint,
                        self._device or "auto",
                    )
                    self._regressor = self._build_regressor()
        return self._regressor

    def warmup(self) -> None:
        """Eagerly construct both estimators so weights load once at startup."""
        self._get_classifier()
        self._get_regressor()

    def active_checkpoint(self, task: str) -> Optional[str]:
        if task == TASK_CLASSIFICATION:
            return self._classifier_path or self._classifier_checkpoint
        if task == TASK_REGRESSION:
            return self._regressor_path or self._regressor_checkpoint
        return None

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _to_frame(feature_columns: List[str], rows: List[Dict[str, Any]]):
        import pandas as pd

        # Reindex guarantees consistent column order and fills absent features
        # with NaN, which TabICL handles natively.
        return pd.DataFrame(rows).reindex(columns=feature_columns)

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
        clf = self._get_classifier()
        X_train_df = self._to_frame(feature_columns, X_train)
        X_predict_df = self._to_frame(feature_columns, X_predict)

        with self._lock:
            clf.fit(X_train_df, y_train)
            labels = clf.predict(X_predict_df)
            proba = clf.predict_proba(X_predict_df) if return_probabilities else None
            classes = list(getattr(clf, "classes_", [])) if return_probabilities else []

        results: List[TabularPrediction] = []
        for i, label in enumerate(labels):
            prob_map = None
            if return_probabilities and proba is not None:
                prob_map = {
                    str(cls): float(proba[i][j]) for j, cls in enumerate(classes)
                }
            results.append(
                TabularPrediction(prediction=_native(label), probabilities=prob_map)
            )
        return results

    def predict_regression(
        self,
        feature_columns: List[str],
        X_train: List[Dict[str, Any]],
        y_train: List[Any],
        X_predict: List[Dict[str, Any]],
    ) -> List[TabularPrediction]:
        reg = self._get_regressor()
        X_train_df = self._to_frame(feature_columns, X_train)
        X_predict_df = self._to_frame(feature_columns, X_predict)

        with self._lock:
            reg.fit(X_train_df, y_train)
            preds = reg.predict(X_predict_df)

        return [TabularPrediction(prediction=_native(p)) for p in preds]


def _native(value: Any) -> Any:
    """Convert numpy scalars to JSON-serializable Python natives."""
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            return value
    return value
