"""Unit tests for the new AgentRx surfaces.

Covers:
- LLMConfig picks up LLM_ENDPOINT_AGENT_RX
- genie_space_manager parses the Genie API response
- knowledge_base_manager does SQL via Statement Execution and triggers the
  bundle-deployed incremental job (prefix-matched)
- etl_trigger.trigger_vector_search_sync uses the VS client when present
- AgentRxAgent constructs with the expected tool set
- mlflow_feedback gracefully returns a warning when experiment_id is missing

These mirror the heart of the 14 fork tests but target the new agent_app
layout. They mock all external services so the suite runs without a
Databricks workspace.
"""

import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# Fake creds so the Databricks SDK's lazy Config validators are happy.
os.environ.setdefault("DATABRICKS_HOST", "https://fake.databricks.example")
os.environ.setdefault("DATABRICKS_TOKEN", "dapi-fake-token-for-unit-tests")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Short-circuit the eager `uc_functions` import in agent_server.multi_agent.tools.__init__
# (uc_functions pulls in databricks.sdk.runtime → dbutils → live Config). For
# unit tests we don't need uc_functions at all; the new AgentRx modules only
# touch the sibling tool modules.
_fake_uc = types.ModuleType("agent_server.multi_agent.tools.uc_functions")
_fake_uc.register_uc_functions = lambda *a, **kw: None  # type: ignore[attr-defined]
_fake_uc.check_uc_functions_exist = lambda *a, **kw: True  # type: ignore[attr-defined]
sys.modules.setdefault("agent_server.multi_agent.tools.uc_functions", _fake_uc)

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_llm_config_picks_up_agent_rx_endpoint(monkeypatch):
    from agent_server.multi_agent.core.config import LLMConfig

    monkeypatch.setenv("LLM_ENDPOINT_AGENT_RX", "databricks-test-agent-rx")
    cfg = LLMConfig.from_env()
    assert cfg.agent_rx_endpoint == "databricks-test-agent-rx"


def test_llm_config_agent_rx_falls_back_to_default(monkeypatch):
    from agent_server.multi_agent.core.config import LLMConfig

    monkeypatch.delenv("LLM_ENDPOINT_AGENT_RX", raising=False)
    monkeypatch.setenv("LLM_ENDPOINT", "fallback-endpoint")
    cfg = LLMConfig.from_env()
    assert cfg.agent_rx_endpoint == "fallback-endpoint"


# ---------------------------------------------------------------------------
# Genie space discovery
# ---------------------------------------------------------------------------

def test_list_genie_spaces_paginates(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager

    page1 = MagicMock(status_code=200)
    page1.json.return_value = {
        "spaces": [{"space_id": "s1", "title": "Sales", "description": ""}],
        "next_page_token": "tok",
    }
    page1.raise_for_status = lambda: None
    page2 = MagicMock(status_code=200)
    page2.json.return_value = {
        "spaces": [{"space_id": "s2", "title": "Ops", "description": ""}],
    }
    page2.raise_for_status = lambda: None

    with patch.object(genie_space_manager, "_get_auth", return_value=("https://h", {"H": "1"})):
        with patch.object(genie_space_manager.requests, "get", side_effect=[page1, page2]):
            out = json.loads(genie_space_manager.list_genie_spaces.invoke({}))

    assert [s["space_id"] for s in out] == ["s1", "s2"]


# ---------------------------------------------------------------------------
# Knowledge base manager
# ---------------------------------------------------------------------------

def test_remove_space_from_index_runs_deletes_and_sync(monkeypatch):
    from agent_server.multi_agent.tools import knowledge_base_manager

    executed_sql: list[str] = []

    def fake_sql(statement: str) -> dict:
        executed_sql.append(statement)
        return {"status": {"state": "SUCCEEDED"}}

    fake_list_resp = MagicMock(status_code=200)
    fake_list_resp.json.return_value = {"contents": [{"name": "abc__foo.space.json"}]}
    fake_del_resp = MagicMock(status_code=204)

    fake_vs_sync = MagicMock()
    fake_vs_sync.invoke.return_value = json.dumps({"status": "success"})
    fake_cache_invalidate = MagicMock()
    fake_cache_invalidate.invoke.return_value = json.dumps({"status": "success"})

    with patch.object(knowledge_base_manager, "_execute_sql_via_api", side_effect=fake_sql), \
         patch.object(knowledge_base_manager, "_get_table_names", return_value={
             "enriched_docs": "c.s.enriched_genie_docs",
             "chunks": "c.s.enriched_genie_docs_chunks",
             "volume_path": "/Volumes/c/s/volume/genie_exports",
         }), \
         patch("agent_server.multi_agent.tools.genie_space_manager._get_auth",
               return_value=("https://h", {"H": "1"})), \
         patch.object(knowledge_base_manager.requests, "get", return_value=fake_list_resp), \
         patch.object(knowledge_base_manager.requests, "delete", return_value=fake_del_resp), \
         patch("agent_server.multi_agent.tools.etl_trigger.trigger_vector_search_sync",
               new=fake_vs_sync), \
         patch("agent_server.multi_agent.tools.etl_trigger.invalidate_space_context_cache",
               new=fake_cache_invalidate):
        out = json.loads(knowledge_base_manager.remove_space_from_index.invoke({"space_id": "abc"}))

    assert out["status"] == "success"
    assert any("DELETE FROM c.s.enriched_genie_docs" in s for s in executed_sql)
    assert any("DELETE FROM c.s.enriched_genie_docs_chunks" in s for s in executed_sql)


def test_trigger_incremental_job_prefix_matches(monkeypatch):
    from agent_server.multi_agent.tools import knowledge_base_manager as kb

    list_resp = MagicMock(status_code=200)
    list_resp.json.return_value = {
        "jobs": [
            {"job_id": 42, "settings": {"name": "multi-agent-genie-app-incremental-space-index-dev"}},
            {"job_id": 99, "settings": {"name": "some-other-job"}},
        ]
    }
    list_resp.raise_for_status = lambda: None
    run_resp = MagicMock(status_code=200)
    run_resp.json.return_value = {"run_id": 12345}
    run_resp.raise_for_status = lambda: None

    with patch.object(kb.requests, "get", return_value=list_resp), \
         patch.object(kb.requests, "post", return_value=run_resp):
        result = kb._trigger_incremental_job("https://h", {"H": "1"}, "abc")

    assert result == {"job_id": 42, "run_id": 12345}


# ---------------------------------------------------------------------------
# ETL trigger
# ---------------------------------------------------------------------------

def test_trigger_vector_search_sync_uses_vs_client():
    from agent_server.multi_agent.tools import etl_trigger

    mock_index = MagicMock()
    mock_index.sync = MagicMock()
    mock_client = MagicMock()
    mock_client.get_index.return_value = mock_index

    with patch("databricks.vector_search.client.VectorSearchClient", return_value=mock_client):
        out = json.loads(
            etl_trigger.trigger_vector_search_sync.invoke({
                "vs_endpoint_name": "ep",
                "vs_index_name": "c.s.idx",
            })
        )

    assert out["status"] == "success"
    mock_index.sync.assert_called_once()


# ---------------------------------------------------------------------------
# Agent class + tool wiring
# ---------------------------------------------------------------------------

def test_agent_rx_agent_binds_expected_tools():
    from agent_server.multi_agent.agents.agent_rx_agent import AgentRxAgent
    from agent_server.multi_agent.tools.genie_space_manager import ALL_GENIE_TOOLS
    from agent_server.multi_agent.tools.knowledge_base_manager import ALL_KB_TOOLS
    from agent_server.multi_agent.tools.etl_trigger import ALL_ETL_TOOLS
    from agent_server.multi_agent.tools.mlflow_feedback import ALL_FEEDBACK_TOOLS

    fake_llm = MagicMock()
    with patch("langgraph.prebuilt.create_react_agent", return_value=MagicMock()) as create:
        agent = AgentRxAgent(llm=fake_llm)
        create.assert_called_once()

    expected = {
        t.name for t in ALL_GENIE_TOOLS + ALL_KB_TOOLS + ALL_ETL_TOOLS + ALL_FEEDBACK_TOOLS
    }
    assert {t.name for t in agent.tools} == expected


# ---------------------------------------------------------------------------
# MLflow feedback (read-only)
# ---------------------------------------------------------------------------

def test_summarise_recent_feedback_warns_without_experiment(monkeypatch):
    from agent_server.multi_agent.tools import mlflow_feedback

    monkeypatch.delenv("MLFLOW_EXPERIMENT_ID", raising=False)
    with patch.object(mlflow_feedback, "_resolve_experiment_id", return_value=""):
        out = json.loads(mlflow_feedback.summarise_recent_feedback.invoke({"days": 1}))
    assert out["status"] == "warning"


# ---------------------------------------------------------------------------
# Genie space editing tools
# ---------------------------------------------------------------------------

def _fake_space_get(serialized: dict, etag: str = "etag-1"):
    """Build a fake GET response carrying the given serialized_space."""
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "space_id": "sp",
        "title": "Test",
        "etag": etag,
        "serialized_space": json.dumps(serialized),
    }
    resp.raise_for_status = lambda: None
    return resp


def _patch_capture():
    """A fake PATCH that records the JSON body and returns a 200."""
    captured = {}

    def _patch(url, headers=None, json=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["body"] = json
        resp = MagicMock(status_code=200)
        resp.content = b"{}"
        resp.json.return_value = {}
        resp.raise_for_status = lambda: None
        return resp

    return _patch, captured


def test_add_benchmark_question_wraps_and_ids(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    get_resp = _fake_space_get({"version": 2, "data_sources": {"tables": []}})
    patch_fn, captured = _patch_capture()

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        out = json.loads(g.add_benchmark_question.invoke({
            "space_id": "sp", "question": "avg fare?", "sql": "SELECT 1",
        }))

    assert out["status"] == "success"
    sent = json.loads(captured["body"]["serialized_space"])
    q = sent["benchmarks"]["questions"][0]
    # list-of-strings wrapping
    assert q["question"] == ["avg fare?"]
    assert q["answer"][0] == {"format": "SQL", "content": ["SELECT 1"]}
    # 32-hex id generated
    assert len(q["id"]) == 32
    # etag echoed back for optimistic concurrency
    assert captured["body"]["etag"] == "etag-1"


def test_remove_benchmark_question_by_text_and_sorts(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {
        "version": 2,
        "data_sources": {"tables": []},
        "benchmarks": {"questions": [
            {"id": "bbb", "question": ["keep me"], "answer": []},
            {"id": "aaa", "question": ["remove me"], "answer": []},
        ]},
    }
    get_resp = _fake_space_get(serialized)
    patch_fn, captured = _patch_capture()

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        out = json.loads(g.remove_benchmark_question.invoke({
            "space_id": "sp", "id_or_question": "remove me",
        }))

    assert out["status"] == "success"
    remaining = json.loads(captured["body"]["serialized_space"])["benchmarks"]["questions"]
    assert [q["id"] for q in remaining] == ["bbb"]


def test_remove_benchmark_question_no_match_warns(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    get_resp = _fake_space_get({"benchmarks": {"questions": [{"id": "x", "question": ["q"]}]}})

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=AssertionError("should not patch")):
        out = json.loads(g.remove_benchmark_question.invoke({
            "space_id": "sp", "id_or_question": "nope",
        }))

    assert out["status"] == "warning"


def test_set_column_visibility_hide_sorts_and_excludes(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {
        "version": 2,
        "data_sources": {"tables": [{
            "identifier": "c.s.t",
            "column_configs": [
                {"column_name": "zeta"},
                {"column_name": "alpha", "enable_format_assistance": True},
            ],
        }]},
    }
    get_resp = _fake_space_get(serialized)
    patch_fn, captured = _patch_capture()

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        out = json.loads(g.set_column_visibility.invoke({
            "space_id": "sp", "table": "c.s.t", "columns": "alpha", "hidden": True,
        }))

    assert out["status"] == "success"
    cfgs = json.loads(captured["body"]["serialized_space"])["data_sources"]["tables"][0]["column_configs"]
    # sorted by column_name
    assert [c["column_name"] for c in cfgs] == ["alpha", "zeta"]
    alpha = next(c for c in cfgs if c["column_name"] == "alpha")
    assert alpha["exclude"] is True


def test_set_column_visibility_show_drops_now_empty_config(monkeypatch):
    """Showing a column whose only setting was `exclude` should remove the config
    entirely (net-zero), not leave a residual {column_name} stub."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {
        "data_sources": {"tables": [{
            "identifier": "c.s.t",
            "column_configs": [{"column_name": "alpha", "exclude": True}],
        }]},
    }
    get_resp = _fake_space_get(serialized)
    patch_fn, captured = _patch_capture()

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        out = json.loads(g.set_column_visibility.invoke({
            "space_id": "sp", "table": "c.s.t", "columns": "alpha", "hidden": False,
        }))

    assert out["status"] == "success"
    cfgs = json.loads(captured["body"]["serialized_space"])["data_sources"]["tables"][0]["column_configs"]
    assert cfgs == []


def test_set_column_visibility_show_preserves_other_settings(monkeypatch):
    """Showing a column that has other settings keeps them, only dropping exclude."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {
        "data_sources": {"tables": [{
            "identifier": "c.s.t",
            "column_configs": [{"column_name": "alpha", "exclude": True, "enable_format_assistance": True}],
        }]},
    }
    get_resp = _fake_space_get(serialized)
    patch_fn, captured = _patch_capture()

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        out = json.loads(g.set_column_visibility.invoke({
            "space_id": "sp", "table": "c.s.t", "columns": "alpha", "hidden": False,
        }))

    assert out["status"] == "success"
    alpha = json.loads(captured["body"]["serialized_space"])["data_sources"]["tables"][0]["column_configs"][0]
    assert "exclude" not in alpha
    assert alpha["enable_format_assistance"] is True


def test_set_column_visibility_show_already_visible_is_noop(monkeypatch):
    """Showing an already-visible (unconfigured) column must NOT PATCH at all."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {"data_sources": {"tables": [{"identifier": "c.s.t", "column_configs": []}]}}
    get_resp = _fake_space_get(serialized)

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=AssertionError("should not patch")):
        out = json.loads(g.set_column_visibility.invoke({
            "space_id": "sp", "table": "c.s.t", "columns": "alpha", "hidden": False,
        }))

    assert out["status"] == "success"
    assert out["columns"] == []


def test_set_column_visibility_ambiguous_table_errors(monkeypatch):
    """A short table name matching multiple tables must error, not guess."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {"data_sources": {"tables": [
        {"identifier": "cat.s1.orders"},
        {"identifier": "cat.s2.orders"},
    ]}}
    get_resp = _fake_space_get(serialized)

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=AssertionError("should not patch")):
        out = json.loads(g.set_column_visibility.invoke({
            "space_id": "sp", "table": "orders", "columns": "x", "hidden": True,
        }))

    assert out["status"] == "error"
    assert "ambiguous" in out["message"].lower()


def test_set_column_visibility_rejects_unknown_column(monkeypatch):
    """Hiding a column that doesn't exist on the table must error, not persist junk."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {"data_sources": {"tables": [{"identifier": "c.s.t", "column_configs": []}]}}
    get_resp = _fake_space_get(serialized)

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g, "_get_uc_columns", return_value=(["alpha", "beta"], True)), \
         patch.object(g.requests, "patch", side_effect=AssertionError("should not patch")):
        out = json.loads(g.set_column_visibility.invoke({
            "space_id": "sp", "table": "c.s.t", "columns": "ghost", "hidden": True,
        }))

    assert out["status"] == "error"
    assert "not found" in out["message"].lower()


def test_set_column_visibility_allows_configured_column_when_uc_uncertain(monkeypatch):
    """A column already in column_configs is allowed even if UC lookup can't confirm it."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {"data_sources": {"tables": [{
        "identifier": "c.s.t",
        "column_configs": [{"column_name": "renamed_col"}],
    }]}}
    get_resp = _fake_space_get(serialized)
    patch_fn, captured = _patch_capture()

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g, "_get_uc_columns", return_value=(["other"], True)), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        out = json.loads(g.set_column_visibility.invoke({
            "space_id": "sp", "table": "c.s.t", "columns": "renamed_col", "hidden": True,
        }))

    assert out["status"] == "success"


def test_list_sample_questions_joins(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {"instructions": {"example_question_sqls": [
        {"id": "s1", "question": ["how ", "many?"], "sql": ["SELECT ", "1"], "usage_guidance": ["use"]},
    ]}}
    get_resp = _fake_space_get(serialized)

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp):
        out = json.loads(g.list_sample_questions.invoke({"space_id": "sp"}))

    assert out["count"] == 1
    sq = out["sample_questions"][0]
    assert sq["question"] == "how many?" and sq["sql"] == "SELECT 1" and sq["usage_guidance"] == "use"


def test_list_space_instructions_joins(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {"instructions": {"text_instructions": [
        {"id": "i1", "content": ["You are ", "an analyst."]},
    ]}}
    get_resp = _fake_space_get(serialized)

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp):
        out = json.loads(g.list_space_instructions.invoke({"space_id": "sp"}))

    assert out["count"] == 1
    assert out["instructions"][0]["content"] == "You are an analyst."


def test_get_genie_space_config_handles_dict_data_sources(monkeypatch):
    """data_sources is a dict with 'tables'; the config summary must extract identifiers."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {"version": 2, "data_sources": {"tables": [
        {"identifier": "c.s.t1"}, {"identifier": "c.s.t2"},
    ]}}
    get_resp = _fake_space_get(serialized)

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp):
        out = json.loads(g.get_genie_space_config.invoke({"space_id": "sp"}))

    assert out["tables"] == ["c.s.t1", "c.s.t2"]


def test_add_sample_question_optional_args(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    get_resp = _fake_space_get({"version": 2, "data_sources": {"tables": []}})
    patch_fn, captured = _patch_capture()

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        # No SQL, no usage_guidance.
        out = json.loads(g.add_sample_question.invoke({"space_id": "sp", "question": "how many?"}))

    assert out["status"] == "success"
    eq = json.loads(captured["body"]["serialized_space"])["instructions"]["example_question_sqls"][0]
    assert eq["question"] == ["how many?"]
    assert len(eq["id"]) == 32
    assert "sql" not in eq            # omitted when empty
    assert "usage_guidance" not in eq  # omitted when empty


def test_add_sample_question_wraps_sql_and_guidance(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    get_resp = _fake_space_get({"data_sources": {"tables": []}})
    patch_fn, captured = _patch_capture()

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        out = json.loads(g.add_sample_question.invoke({
            "space_id": "sp", "question": "q", "sql": "SELECT 1", "usage_guidance": "use it",
        }))

    assert out["status"] == "success"
    eq = json.loads(captured["body"]["serialized_space"])["instructions"]["example_question_sqls"][0]
    assert eq["sql"] == ["SELECT 1"]
    assert eq["usage_guidance"] == ["use it"]


def test_remove_sample_question_targets_example_list(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {"instructions": {"example_question_sqls": [
        {"id": "keep", "question": ["stay"], "sql": ["x"]},
        {"id": "drop", "question": ["go"], "sql": ["y"]},
    ]}}
    get_resp = _fake_space_get(serialized)
    patch_fn, captured = _patch_capture()

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        out = json.loads(g.remove_sample_question.invoke({"space_id": "sp", "id_or_question": "drop"}))

    assert out["status"] == "success"
    remaining = json.loads(captured["body"]["serialized_space"])["instructions"]["example_question_sqls"]
    assert [q["id"] for q in remaining] == ["keep"]


def test_update_space_retries_on_etag_conflict(monkeypatch):
    """A 409 (stale etag) must re-read and re-apply the edit, then succeed —
    without doubling the appended item."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    get_resp = _fake_space_get({"version": 2, "data_sources": {"tables": []}}, etag="e1")

    bodies = []
    resp409 = MagicMock(status_code=409)
    resp409.raise_for_status = lambda: (_ for _ in ()).throw(AssertionError("409 should not raise"))
    resp200 = MagicMock(status_code=200)
    resp200.content = b"{}"
    resp200.json.return_value = {}
    resp200.raise_for_status = lambda: None

    def patch_fn(url, headers=None, json=None, timeout=None):  # noqa: A002
        bodies.append(json)
        return resp409 if len(bodies) == 1 else resp200

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=patch_fn):
        out = json.loads(g.add_benchmark_question.invoke({
            "space_id": "sp", "question": "q", "sql": "SELECT 1",
        }))

    assert out["status"] == "success"
    assert len(bodies) == 2  # retried once
    # The re-applied edit must contain exactly ONE benchmark (not duplicated).
    final = json.loads(bodies[1]["serialized_space"])
    assert len(final["benchmarks"]["questions"]) == 1


def test_list_benchmark_questions_joins_multiline(monkeypatch):
    """list_* tools must join the API's list-of-strings back into one string."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    serialized = {"benchmarks": {"questions": [
        {"id": "x", "question": ["line1\n", "line2"], "answer": [{"format": "SQL", "content": ["SELECT\n", "1"]}]},
    ]}}
    get_resp = _fake_space_get(serialized)

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp):
        out = json.loads(g.list_benchmark_questions.invoke({"space_id": "sp"}))

    q = out["benchmark_questions"][0]
    assert q["question"] == "line1\nline2"
    assert q["answers"][0]["content"] == "SELECT\n1"


def test_set_column_visibility_unknown_table_errors(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    get_resp = _fake_space_get({"data_sources": {"tables": [{"identifier": "c.s.t"}]}})

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g.requests, "patch", side_effect=AssertionError("should not patch")):
        out = json.loads(g.set_column_visibility.invoke({
            "space_id": "sp", "table": "c.s.other", "columns": "x", "hidden": True,
        }))

    assert out["status"] == "error"


def test_list_space_tables_and_columns_merges_uc(monkeypatch):
    from agent_server.multi_agent.tools import genie_space_manager as g

    get_resp = _fake_space_get({"data_sources": {"tables": [{
        "identifier": "c.s.t",
        "column_configs": [{"column_name": "a", "exclude": True}],
    }]}})

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g, "_get_uc_columns", return_value=(["a", "b"], True)):
        out = json.loads(g.list_space_tables_and_columns.invoke({"space_id": "sp"}))

    assert out["status"] == "success"
    assert out["tables"][0]["columns_source"] == "unity_catalog"
    cols = {c["column_name"]: c for c in out["tables"][0]["columns"]}
    assert cols["a"]["hidden"] is True and cols["a"]["configured"] is True
    assert cols["b"]["hidden"] is False and cols["b"]["configured"] is False


def test_list_space_tables_and_columns_flags_uc_failure(monkeypatch):
    """When UC lookup fails and the space has no per-column config, the listing
    must surface that columns are unavailable rather than silently report none."""
    from agent_server.multi_agent.tools import genie_space_manager as g

    get_resp = _fake_space_get({"data_sources": {"tables": [{"identifier": "c.s.t"}]}})

    with patch.object(g, "_get_auth", return_value=("https://h", {"H": "1"})), \
         patch.object(g.requests, "get", return_value=get_resp), \
         patch.object(g, "_get_uc_columns", return_value=([], False)):
        out = json.loads(g.list_space_tables_and_columns.invoke({"space_id": "sp"}))

    assert out["status"] == "success"
    assert out["tables"][0]["columns_source"] == "unavailable_uc_lookup_failed"
    assert "note" in out["tables"][0]


if __name__ == "__main__":  # pragma: no cover - manual run helper
    pytest.main([__file__, "-v"])
