"""
ETL Pipeline Trigger Tools for AgentRx.

Provides LangChain-compatible tools to refresh metadata and vector search
after Genie Space modifications. Supports both lightweight syncs and full
ETL pipeline re-runs.

Operations:
- Trigger Vector Search index sync (lightweight)
- Trigger the full ETL pipeline job via Databricks Jobs API
- Invalidate the in-process space context cache

Authentication uses the Databricks SDK Config() for unified credential resolution
across Model Serving (OAuth/service principal), notebooks, and local development.
"""

import os
import json
import time
import requests
from typing import Optional

from langchain_core.tools import tool


def _get_auth() -> tuple[str, dict]:
    """Resolve Databricks host and auth headers.

    Credential resolution order:
      1. DATABRICKS_HOST + DATABRICKS_TOKEN env vars (explicit / local dev)
      2. databricks.sdk.core.Config() — auto-detects Model Serving OAuth,
         notebook context, and ~/.databrickscfg profiles
      3. PySpark / REPL context (legacy notebook fallback)
    """
    _CONTENT_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}

    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "")

    if host and token:
        if not host.startswith("https://"):
            host = f"https://{host.lstrip('/')}"
        return host, {"Authorization": f"Bearer {token}", **_CONTENT_HEADERS}

    try:
        from databricks.sdk.core import Config
        cfg = Config()
        host = (cfg.host or "").rstrip("/")
        auth_headers = cfg.authenticate()
        return host, {**auth_headers, **_CONTENT_HEADERS}
    except Exception:
        pass

    if not host:
        try:
            from pyspark.sql import SparkSession
            spark = SparkSession.builder.getOrCreate()
            workspace_url = spark.conf.get("spark.databricks.workspaceUrl")
            host = "https://" + workspace_url
        except Exception:
            pass

    if not token:
        try:
            from dbruntime.databricks_repl_context import get_context  # type: ignore[import]
            token = get_context().apiToken
        except Exception:
            pass

    if not host or not token:
        raise RuntimeError(
            "Cannot resolve Databricks credentials. "
            "Set DATABRICKS_HOST and DATABRICKS_TOKEN environment variables, "
            "or ensure the Databricks SDK can auto-detect credentials."
        )

    if not host.startswith("https://"):
        host = f"https://{host.lstrip('/')}"

    return host, {"Authorization": f"Bearer {token}", **_CONTENT_HEADERS}


@tool
def trigger_vector_search_sync(vs_endpoint_name: Optional[str] = None, vs_index_name: Optional[str] = None) -> str:
    """Trigger a sync on the Vector Search Delta Sync index to pick up table changes.

    Uses the VectorSearchClient SDK when available, falling back to REST API.

    Args:
        vs_endpoint_name: Vector search endpoint name. Auto-detected from config if omitted.
        vs_index_name: Fully qualified index name. Auto-detected from config if omitted.
    """
    if vs_endpoint_name is None or vs_index_name is None:
        try:
            from ..core.config import get_config
            config = get_config()
            vs_endpoint_name = vs_endpoint_name or config.vector_search.endpoint_name
            vs_index_name = vs_index_name or config.vs_index_fq
        except Exception:
            pass

    if not vs_index_name:
        return json.dumps({"status": "error", "message": "Could not determine vector search index name. Provide vs_index_name explicitly."})

    try:
        from databricks.vector_search.client import VectorSearchClient
        vsc = VectorSearchClient()
        idx = vsc.get_index(endpoint_name=vs_endpoint_name, index_name=vs_index_name)
        idx.sync()
        return json.dumps({
            "status": "success",
            "message": f"Vector search sync triggered for index '{vs_index_name}'. Changes will be reflected shortly.",
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to trigger vector search sync: {e}"})


@tool
def trigger_full_etl_pipeline(job_name: Optional[str] = None) -> str:
    """Trigger the full ETL pipeline Databricks job (export → enrich → vector search).

    This is useful after creating a new Genie space or making significant changes.
    The job runs asynchronously; this tool returns immediately with the run ID.

    Args:
        job_name: Name (or prefix) of the Databricks job. Defaults to the
            bundle-deployed metadata-refresh job
            ('multi-agent-genie-app-metadata-refresh' — matches the
            ``-<target>``-suffixed name).
    """
    host, headers = _get_auth()

    if job_name is None:
        job_name = "multi-agent-genie-app-metadata-refresh"

    # Jobs ``?name=`` is exact-match, so we paginate the unfiltered list and
    # filter client-side by substring (handles ``[dev <user>] `` prefixes).
    jobs: list[dict] = []
    page_token: Optional[str] = None
    while True:
        params: dict = {"limit": 25}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(
            f"{host}/api/2.1/jobs/list",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        for j in data.get("jobs", []):
            if job_name in j.get("settings", {}).get("name", ""):
                jobs.append(j)
        page_token = data.get("next_page_token")
        if not page_token or jobs:
            break

    if not jobs:
        return json.dumps({"status": "error", "message": f"No job found whose name contains '{job_name}'."})

    job_id = jobs[0]["job_id"]

    resp = requests.post(
        f"{host}/api/2.1/jobs/run-now",
        headers=headers,
        json={"job_id": job_id},
        timeout=30,
    )
    resp.raise_for_status()
    run_id = resp.json().get("run_id", "unknown")

    return json.dumps({
        "status": "success",
        "message": f"ETL pipeline job '{job_name}' triggered (run ID: {run_id}). The pipeline will export Genie spaces, enrich metadata, and rebuild the vector search index.",
        "job_id": job_id,
        "run_id": run_id,
    })


@tool
def invalidate_space_context_cache() -> str:
    """Invalidate the in-process space context cache used by the clarification agent.

    Call this after modifying Genie spaces so the next query picks up changes.
    """
    try:
        from ..agents.clarification import _space_context_cache
        _space_context_cache["data"] = None
        _space_context_cache["timestamp"] = None
        _space_context_cache["table_name"] = None
        return json.dumps({"status": "success", "message": "Space context cache invalidated. Next query will reload from database."})
    except Exception as e:
        return json.dumps({"status": "warning", "message": f"Cache invalidation attempted but may not be effective in this runtime: {e}"})


ALL_ETL_TOOLS = [
    trigger_vector_search_sync,
    trigger_full_etl_pipeline,
    invalidate_space_context_cache,
]
