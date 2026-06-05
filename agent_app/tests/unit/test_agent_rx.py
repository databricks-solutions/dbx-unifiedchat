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


if __name__ == "__main__":  # pragma: no cover - manual run helper
    pytest.main([__file__, "-v"])
