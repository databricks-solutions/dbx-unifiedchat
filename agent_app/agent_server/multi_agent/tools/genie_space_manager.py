"""
Genie Space Discovery Tools for AgentRx.

Provides read-only LangChain-compatible tools for discovering Databricks
Genie Spaces via the REST API.  These are used by AgentRx for exploration
only; actual knowledge base management (add/remove from index) lives in
knowledge_base_manager.py.

Supported operations:
- List all accessible Genie spaces on the workspace
- Get full space configuration (tables, instructions, etc.)

Authentication uses the Databricks SDK Config() for unified credential resolution
across Model Serving (OAuth/service principal), notebooks, and local development.
"""

import os
import json
import requests

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


def _get_space_full(host: str, headers: dict, space_id: str) -> dict:
    """Fetch full space object including serialized_space."""
    resp = requests.get(
        f"{host}/api/2.0/genie/spaces/{space_id}",
        headers=headers,
        params={"include_serialized_space": "true"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# LangChain tools (read-only discovery)
# ---------------------------------------------------------------------------

@tool
def list_genie_spaces() -> str:
    """List all accessible Genie spaces. Returns JSON array with space_id, title, and description for each space."""
    host, headers = _get_auth()
    spaces = []
    params: dict = {}

    while True:
        resp = requests.get(
            f"{host}/api/2.0/genie/spaces",
            headers=headers,
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        for s in data.get("spaces", []):
            spaces.append({
                "space_id": s.get("space_id") or s.get("id"),
                "title": s.get("title", ""),
                "description": s.get("description", ""),
            })
        next_token = data.get("next_page_token") or data.get("page_token")
        if not next_token:
            break
        params = {"page_token": next_token}

    return json.dumps(spaces, indent=2)


@tool
def get_genie_space_config(space_id: str) -> str:
    """Get full configuration of a Genie space including its tables and instructions.

    Args:
        space_id: The ID of the Genie space to inspect.
    """
    host, headers = _get_auth()
    obj = _get_space_full(host, headers, space_id)

    serialized = obj.get("serialized_space")
    if serialized and isinstance(serialized, str):
        try:
            serialized = json.loads(serialized)
        except json.JSONDecodeError:
            pass

    tables = []
    instructions = None
    if isinstance(serialized, dict):
        for ds in serialized.get("data_sources", []):
            for tbl in ds.get("tables", []):
                tables.append(tbl.get("identifier", tbl.get("name", "unknown")))
        instructions = serialized.get("instructions")

    summary = {
        "space_id": space_id,
        "title": obj.get("title", ""),
        "description": obj.get("description", ""),
        "warehouse_id": obj.get("warehouse_id", ""),
        "tables": tables,
        "instructions": instructions,
    }
    return json.dumps(summary, indent=2)


ALL_GENIE_TOOLS = [
    list_genie_spaces,
    get_genie_space_config,
]
