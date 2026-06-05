"""
Knowledge Base Manager Tools for AgentRx.

Provides LangChain-compatible tools for managing the agent's knowledge base:
the enriched metadata tables and Vector Search index that the Planning Agent
uses to discover relevant Genie Spaces.

"Adding/removing access" to a Genie Space means adding/removing its metadata
from the ETL-produced tables and Vector Search index, NOT modifying the
actual Genie Space itself.

SQL execution uses databricks-sql-connector with unified authentication
(same pattern as SQLExecutionAgent). Volume file operations use the
Databricks Files REST API.
"""

import json
import re
import requests

from langchain_core.tools import tool


_SPACE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_space_id(space_id: str) -> str:
    """Sanitise and validate a Genie space ID."""
    space_id = space_id.strip()
    if not space_id or not _SPACE_ID_RE.match(space_id):
        raise ValueError(f"Invalid space_id format: '{space_id}'")
    return space_id


def _get_sql_connection():
    """Create a SQL Warehouse connection using the same pattern as SQLExecutionAgent."""
    from databricks import sql
    from databricks.sdk.core import Config
    from ..core.config import get_config

    cfg = Config()
    config = get_config()
    warehouse_id = config.table_metadata.sql_warehouse_id

    return sql.connect(
        server_hostname=cfg.host,
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        credentials_provider=lambda: cfg.authenticate,
    )


def _execute_sql_via_api(statement: str) -> dict:
    """Execute SQL via the Statement Execution REST API.

    Uses the same auth headers as the Genie Space REST API, which avoids
    identity differences that can occur with databricks.sql.connect in
    Model Serving environments.
    """
    from .genie_space_manager import _get_auth
    from ..core.config import get_config

    host, headers = _get_auth()
    config = get_config()
    warehouse_id = config.table_metadata.sql_warehouse_id

    resp = requests.post(
        f"{host}/api/2.0/sql/statements",
        headers=headers,
        json={
            "warehouse_id": warehouse_id,
            "statement": statement,
            "wait_timeout": "30s",
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    state = data.get("status", {}).get("state", "UNKNOWN")
    if state == "FAILED":
        error = data.get("status", {}).get("error", {})
        raise RuntimeError(
            f"SQL statement failed: {error.get('message', 'unknown error')}"
        )
    return data


def _get_table_names() -> dict:
    """Return fully qualified table names and volume path from config."""
    from ..core.config import get_config
    config = get_config()
    uc = config.unity_catalog
    tm = config.table_metadata
    return {
        "enriched_docs": config.enriched_docs_table_fq,
        "chunks": config.source_table_fq,
        "volume_path": f"/Volumes/{uc.catalog_name}/{uc.schema_name}/{tm.volume_name}/genie_exports",
    }


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

@tool
def list_indexed_spaces() -> str:
    """List all Genie Spaces currently indexed in the agent's knowledge base.

    Returns space_id, space_title, and chunk_count for every space that the
    Planning Agent can discover via the Vector Search index.
    """
    try:
        tables = _get_table_names()
        with _get_sql_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT space_id, space_title, COUNT(*) AS chunk_count "
                    f"FROM {tables['chunks']} "
                    f"GROUP BY space_id, space_title "
                    f"ORDER BY space_title"
                )
                columns = [d[0] for d in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return json.dumps({
            "status": "success",
            "indexed_spaces": rows,
            "total_spaces": len(rows),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to list indexed spaces: {e}"})


@tool
def remove_space_from_index(space_id: str) -> str:
    """Remove a Genie Space from the agent's knowledge base.

    Deletes all metadata rows for the space from the enriched tables, removes
    exported JSON files from the UC volume, triggers a Vector Search sync, and
    invalidates the space context cache.

    IMPORTANT: This does NOT delete the actual Genie Space — it only removes
    the agent's knowledge of it. If the space ID is still listed in the
    configured genie_space_ids, a future full ETL run will re-add it.

    Args:
        space_id: The Genie Space ID to remove from the knowledge base.
    """
    try:
        space_id = _validate_space_id(space_id)
        tables = _get_table_names()
        operations: list[str] = []

        _execute_sql_via_api(
            f"DELETE FROM {tables['enriched_docs']} WHERE space_id = '{space_id}'"
        )
        operations.append(f"Deleted rows from {tables['enriched_docs']}")

        _execute_sql_via_api(
            f"DELETE FROM {tables['chunks']} WHERE space_id = '{space_id}'"
        )
        operations.append(f"Deleted rows from {tables['chunks']}")

        try:
            from .genie_space_manager import _get_auth
            host, headers = _get_auth()
            volume_path = tables["volume_path"].lstrip("/")
            list_resp = requests.get(
                f"{host}/api/2.0/fs/directories/{volume_path}",
                headers=headers,
                timeout=30,
            )
            if list_resp.status_code == 200:
                contents = list_resp.json().get("contents", [])
                deleted_files: list[str] = []
                for entry in contents:
                    fname = entry.get("name", "") or entry.get("path", "").rsplit("/", 1)[-1]
                    if fname.startswith(f"{space_id}__"):
                        del_resp = requests.delete(
                            f"{host}/api/2.0/fs/files/{volume_path}/{fname}",
                            headers=headers,
                            timeout=30,
                        )
                        if del_resp.status_code in (200, 204):
                            deleted_files.append(fname)
                if deleted_files:
                    operations.append(f"Deleted {len(deleted_files)} export file(s) from volume")
                else:
                    operations.append("No export files found for this space on volume")
            else:
                operations.append(f"Could not list volume directory (HTTP {list_resp.status_code})")
        except Exception as ve:
            operations.append(f"Volume cleanup skipped: {ve}")

        from .etl_trigger import trigger_vector_search_sync, invalidate_space_context_cache
        vs_result = json.loads(trigger_vector_search_sync.invoke({}))
        operations.append(f"Vector Search sync: {vs_result.get('status', 'unknown')}")

        cache_result = json.loads(invalidate_space_context_cache.invoke({}))
        operations.append(f"Cache invalidation: {cache_result.get('status', 'unknown')}")

        return json.dumps({
            "status": "success",
            "message": f"Space '{space_id}' removed from agent's knowledge base.",
            "operations": operations,
            "note": (
                "If this space ID is still in the configured genie_space_ids, "
                "it will reappear after the next full ETL pipeline run."
            ),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to remove space from index: {e}"})


_INCREMENTAL_JOB_NAME_TOKEN = "multi-agent-genie-app-incremental-space-index"


def _trigger_incremental_job(host: str, headers: dict, space_id: str) -> dict:
    """Look up the incremental indexing job and trigger it with space_id.

    The bundle-deployed name is
    ``multi-agent-genie-app-incremental-space-index-<target>``, but
    `databricks bundle deploy` in ``mode: development`` prepends
    ``[dev <user>] `` to every job name. The Jobs ``?name=`` filter is
    exact-match, so we list pages and filter client-side by substring.
    """
    jobs: list[dict] = []
    page_token: str | None = None
    while True:
        params = {"limit": 25}
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
            if _INCREMENTAL_JOB_NAME_TOKEN in j.get("settings", {}).get("name", ""):
                jobs.append(j)
        page_token = data.get("next_page_token")
        if not page_token or jobs:
            # Stop once we have at least one match (jobs are typically <25 anyway).
            break

    if not jobs:
        raise RuntimeError(
            f"No job found whose name contains '{_INCREMENTAL_JOB_NAME_TOKEN}'. "
            f"Deploy the bundle first: bash agent_app/scripts/deploy.sh --target <target>"
        )

    job_id = jobs[0]["job_id"]
    run_resp = requests.post(
        f"{host}/api/2.1/jobs/run-now",
        headers=headers,
        json={"job_id": job_id, "job_parameters": {"space_id": space_id}},
        timeout=30,
    )
    run_resp.raise_for_status()
    return {
        "job_id": job_id,
        "run_id": run_resp.json().get("run_id", "unknown"),
    }


@tool
def add_space_to_index(space_id: str) -> str:
    """Add a Genie Space to the agent's knowledge base.

    Fetches the space metadata from the Genie API, writes it to the UC export
    volume, then triggers an async incremental workflow that enriches only
    this space (with LLM-enhanced descriptions) and syncs the Vector Search
    index.  The workflow runs asynchronously; the space will become available
    to the Planning Agent after it completes (typically 2-5 minutes).

    Args:
        space_id: The Genie Space ID to add to the knowledge base.
    """
    try:
        space_id = _validate_space_id(space_id)
        from .genie_space_manager import _get_auth
        host, headers = _get_auth()
        tables = _get_table_names()
        operations: list[str] = []

        resp = requests.get(
            f"{host}/api/2.0/genie/spaces/{space_id}",
            headers=headers,
            params={"include_serialized_space": "true"},
            timeout=120,
        )
        resp.raise_for_status()
        space_data = resp.json()
        title = space_data.get("title", space_id)
        operations.append(f"Fetched metadata for space '{title}'")

        safe_title = re.sub(r"[^a-zA-Z0-9 _-]", "_", title).strip().replace(" ", "_")
        filename = f"{space_id}__{safe_title}.space.json"
        volume_path = tables["volume_path"].lstrip("/")
        file_path = f"{volume_path}/{filename}"

        upload_headers = {k: v for k, v in headers.items()}
        upload_headers["Content-Type"] = "application/octet-stream"
        put_resp = requests.put(
            f"{host}/api/2.0/fs/files/{file_path}",
            headers=upload_headers,
            data=json.dumps(space_data, indent=2).encode("utf-8"),
            timeout=120,
        )
        if put_resp.status_code in (200, 201, 204):
            operations.append(f"Exported space JSON to volume: {filename}")
        else:
            operations.append(
                f"Volume upload returned HTTP {put_resp.status_code}: "
                f"{put_resp.text[:200]}"
            )

        job_info = _trigger_incremental_job(host, headers, space_id)
        operations.append(
            f"Triggered incremental indexing job (run ID: {job_info['run_id']})"
        )

        from .etl_trigger import invalidate_space_context_cache
        cache_result = json.loads(invalidate_space_context_cache.invoke({}))
        operations.append(f"Cache invalidation: {cache_result.get('status', 'unknown')}")

        return json.dumps({
            "status": "success",
            "message": (
                f"Space '{title}' (ID: {space_id}) exported and incremental "
                f"indexing started (run ID: {job_info['run_id']}). The space "
                f"will be available after enrichment completes (~2-5 min)."
            ),
            "space_id": space_id,
            "run_id": job_info["run_id"],
            "operations": operations,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to add space to index: {e}"})


@tool
def get_indexed_space_details(space_id: str) -> str:
    """Get detailed information about a Genie Space's indexed metadata.

    Shows chunk types, table coverage, and chunk counts for a specific space
    in the agent's knowledge base.

    Args:
        space_id: The Genie Space ID to inspect.
    """
    try:
        space_id = _validate_space_id(space_id)
        tables = _get_table_names()

        with _get_sql_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT chunk_type, COUNT(*) AS count "
                    f"FROM {tables['chunks']} "
                    f"WHERE space_id = '{space_id}' "
                    f"GROUP BY chunk_type ORDER BY chunk_type"
                )
                chunk_summary = [
                    dict(zip([d[0] for d in cursor.description], row))
                    for row in cursor.fetchall()
                ]

                cursor.execute(
                    f"SELECT DISTINCT table_name "
                    f"FROM {tables['chunks']} "
                    f"WHERE space_id = '{space_id}' AND table_name IS NOT NULL "
                    f"ORDER BY table_name"
                )
                indexed_tables = [row[0] for row in cursor.fetchall()]

                cursor.execute(
                    f"SELECT DISTINCT space_title "
                    f"FROM {tables['chunks']} "
                    f"WHERE space_id = '{space_id}' LIMIT 1"
                )
                title_row = cursor.fetchone()
                space_title = title_row[0] if title_row else "Unknown"

        if not chunk_summary:
            return json.dumps({
                "status": "not_found",
                "message": f"Space '{space_id}' is not in the agent's knowledge base.",
            })

        return json.dumps({
            "status": "success",
            "space_id": space_id,
            "space_title": space_title,
            "chunk_summary": chunk_summary,
            "total_chunks": sum(c["count"] for c in chunk_summary),
            "indexed_tables": indexed_tables,
            "table_count": len(indexed_tables),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to get space details: {e}"})


ALL_KB_TOOLS = [
    list_indexed_spaces,
    remove_space_from_index,
    add_space_to_index,
    get_indexed_space_details,
]
