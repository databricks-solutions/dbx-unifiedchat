"""Unit tests for the embedded tabular LTM contract, config, and service.

These tests do not require torch/tabicl: the provider is faked so they run
fast and verify the contract, budget guardrail, and graceful failure handling.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_server.multi_agent.core.config import TabularLTMConfig
from agent_server.multi_agent.tools.tabular_ltm import (
    ModelUnavailableError,
    TabularPrediction,
    TabularPredictRequest,
)
from agent_server.multi_agent.tools.tabular_ltm import service as ltm_service


# --- Contract validation ---------------------------------------------------


def _valid_kwargs(**overrides):
    base = dict(
        task="classification",
        feature_columns=["age", "income"],
        target_column="label",
        train_rows=[{"age": 30, "income": 50000, "label": "yes"}],
        predict_rows=[{"age": 40, "income": 80000}],
    )
    base.update(overrides)
    return base


def test_request_rejects_unknown_task():
    with pytest.raises(ValueError):
        TabularPredictRequest(**_valid_kwargs(task="ranking"))


def test_request_rejects_target_in_features():
    with pytest.raises(ValueError):
        TabularPredictRequest(**_valid_kwargs(feature_columns=["age", "label"]))


def test_request_rejects_missing_target_in_train_rows():
    with pytest.raises(ValueError):
        TabularPredictRequest(
            **_valid_kwargs(train_rows=[{"age": 30, "income": 50000}])
        )


def test_request_rejects_probabilities_for_regression():
    with pytest.raises(ValueError):
        TabularPredictRequest(**_valid_kwargs(task="regression", return_probabilities=True))


# --- Config -----------------------------------------------------------------


def test_config_from_env_defaults(monkeypatch):
    for var in (
        "LTM_ENABLED",
        "LTM_PROVIDER",
        "LTM_CHECKPOINT_PATH",
        "LTM_DEVICE",
        "LTM_ALLOW_AUTO_DOWNLOAD",
    ):
        monkeypatch.delenv(var, raising=False)

    cfg = TabularLTMConfig.from_env()

    assert cfg.enabled is False
    assert cfg.provider == "tabicl"
    assert cfg.classifier_path is None  # no dir set
    assert cfg.device_or_none is None


def test_config_builds_checkpoint_paths(monkeypatch):
    monkeypatch.setenv("LTM_CHECKPOINT_PATH", "/Volumes/cat/sch/ltm_models/tabicl")
    cfg = TabularLTMConfig.from_env()

    assert cfg.classifier_path == "/Volumes/cat/sch/ltm_models/tabicl/" + cfg.classifier_checkpoint
    assert cfg.regressor_path == "/Volumes/cat/sch/ltm_models/tabicl/" + cfg.regressor_checkpoint


# --- Service dispatch -------------------------------------------------------


class _FakeProvider:
    name = "tabicl"

    def __init__(self):
        self.last_call = None

    def predict_classification(self, feature_columns, X_train, y_train, X_predict, *, return_probabilities=False):
        self.last_call = ("classification", len(X_train), len(X_predict))
        out = []
        for _ in X_predict:
            probs = {"yes": 0.7, "no": 0.3} if return_probabilities else None
            out.append(TabularPrediction(prediction="yes", probabilities=probs))
        return out

    def predict_regression(self, feature_columns, X_train, y_train, X_predict):
        self.last_call = ("regression", len(X_train), len(X_predict))
        return [TabularPrediction(prediction=1.23) for _ in X_predict]

    def active_checkpoint(self, task):
        return "fake.ckpt"


def _enabled_cfg(**overrides):
    base = dict(
        enabled=True,
        provider="tabicl",
        checkpoint_dir="/Volumes/cat/sch/ltm_models/tabicl",
        classifier_checkpoint="tabicl-classifier-v2-20260212.ckpt",
        regressor_checkpoint="tabicl-regressor-v2-20260212.ckpt",
        device="",
        max_context_rows=100000,
        n_estimators=8,
        allow_auto_download=False,
        nexus_endpoint="",
        nexus_region="",
    )
    base.update(overrides)
    return TabularLTMConfig(**base)


def test_classification_dispatch_shape(monkeypatch):
    fake = _FakeProvider()
    monkeypatch.setattr(ltm_service, "_get_ltm_config", lambda: _enabled_cfg())
    monkeypatch.setattr(ltm_service, "get_provider", lambda *a, **k: fake)

    req = TabularPredictRequest(**_valid_kwargs(return_probabilities=True))
    resp = ltm_service.run_tabular_prediction(req)

    assert resp.success is True
    assert resp.task == "classification"
    assert resp.provider == "tabicl"
    assert resp.n_train == 1 and resp.n_predict == 1
    assert len(resp.predictions) == 1
    assert resp.predictions[0].probabilities == {"yes": 0.7, "no": 0.3}
    assert fake.last_call == ("classification", 1, 1)


def test_regression_dispatch_shape(monkeypatch):
    fake = _FakeProvider()
    monkeypatch.setattr(ltm_service, "_get_ltm_config", lambda: _enabled_cfg())
    monkeypatch.setattr(ltm_service, "get_provider", lambda *a, **k: fake)

    req = TabularPredictRequest(
        **_valid_kwargs(
            task="regression",
            train_rows=[{"age": 30, "income": 50000, "label": 1.0}],
        )
    )
    resp = ltm_service.run_tabular_prediction(req)

    assert resp.success is True
    assert resp.task == "regression"
    assert resp.predictions[0].prediction == 1.23


def test_disabled_returns_clean_error(monkeypatch):
    monkeypatch.setattr(ltm_service, "_get_ltm_config", lambda: _enabled_cfg(enabled=False))

    resp = ltm_service.run_tabular_prediction(TabularPredictRequest(**_valid_kwargs()))

    assert resp.success is False
    assert "disabled" in resp.error.lower()


def test_budget_guardrail(monkeypatch):
    monkeypatch.setattr(ltm_service, "_get_ltm_config", lambda: _enabled_cfg(max_context_rows=1))
    monkeypatch.setattr(ltm_service, "get_provider", lambda *a, **k: _FakeProvider())

    req = TabularPredictRequest(
        **_valid_kwargs(
            train_rows=[
                {"age": 30, "income": 50000, "label": "yes"},
                {"age": 31, "income": 51000, "label": "no"},
            ]
        )
    )
    resp = ltm_service.run_tabular_prediction(req)

    assert resp.success is False
    assert "exceeds" in resp.error.lower()


def test_model_unavailable_is_graceful(monkeypatch):
    def _raise(*_a, **_k):
        raise ModelUnavailableError("tabicl is not installed.")

    monkeypatch.setattr(ltm_service, "_get_ltm_config", lambda: _enabled_cfg())
    monkeypatch.setattr(ltm_service, "get_provider", _raise)

    resp = ltm_service.run_tabular_prediction(TabularPredictRequest(**_valid_kwargs()))

    assert resp.success is False
    assert "not installed" in resp.error.lower()


def test_warmup_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(ltm_service, "_get_ltm_config", lambda: _enabled_cfg(enabled=False))

    status = ltm_service.warmup_tabular_ltm()

    assert status["enabled"] is False
    assert status["loaded"] is False


# --- Nexus provider stub (pre-wired, disabled) ------------------------------


def test_nexus_provider_selected_by_factory():
    from agent_server.multi_agent.tools.tabular_ltm.nexus_provider import NexusProvider

    provider = ltm_service._build_provider(_enabled_cfg(provider="nexus"))

    assert isinstance(provider, NexusProvider)
    assert provider.name == "nexus"


def test_nexus_unconfigured_is_graceful():
    from agent_server.multi_agent.tools.tabular_ltm.nexus_provider import NexusProvider

    provider = NexusProvider(endpoint_name=None)
    with pytest.raises(ModelUnavailableError):
        provider.predict_classification(["x"], [{"x": 1}], ["a"], [{"x": 2}])


def test_nexus_via_service_returns_clean_error(monkeypatch):
    monkeypatch.setattr(
        ltm_service, "_get_ltm_config", lambda: _enabled_cfg(provider="nexus", nexus_endpoint="")
    )
    # Force a fresh provider build so the nexus branch is exercised.
    monkeypatch.setattr(ltm_service, "_provider", None, raising=False)

    resp = ltm_service.run_tabular_prediction(TabularPredictRequest(**_valid_kwargs()))

    assert resp.success is False
    assert "not configured" in resp.error.lower()


def test_unsupported_provider_raises():
    with pytest.raises(ModelUnavailableError):
        ltm_service._build_provider(_enabled_cfg(provider="bogus"))
