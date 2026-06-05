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
import uuid
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
# Genie space edit helpers (export -> modify -> update round-trip)
#
# The Genie REST API has NO granular mutation endpoints. Every edit goes through
# the full `serialized_space` blob: GET (include_serialized_space=true) -> modify
# the relevant section -> PATCH with the JSON string and the current `etag`.
#
# Correctness rules (the API rejects violations):
#   - Text fields are LISTS OF STRINGS on the wire (wrap on write, join on read).
#   - benchmarks.questions / instructions.example_question_sqls /
#     instructions.text_instructions must be sorted by `id`; column_configs by
#     `column_name`.
#   - Items carry 32-hex ids: generate for new items, preserve existing ones.
# ---------------------------------------------------------------------------

def _wrap_lines(s):
    """Wrap a string as the list-of-strings the Genie API expects on the wire."""
    if s is None:
        return None
    return [str(s).replace("\r\n", "\n").replace("\r", "\n")]


def _join_lines(v):
    """Join the API's list-of-strings (or a plain string) into one string."""
    if v is None:
        return None
    if isinstance(v, list):
        s = "".join(str(x) for x in v)
    elif isinstance(v, str):
        s = v
    else:
        s = str(v)
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _new_id() -> str:
    """Generate a fresh 32-hex id matching Genie's id format."""
    return uuid.uuid4().hex


def _iter_space_tables(serialized: dict) -> list:
    """Return the tables list from serialized_space (data_sources is a dict)."""
    return ((serialized.get("data_sources") or {}).get("tables")) or []


def _parse_serialized(obj: dict) -> dict:
    """Parse the serialized_space JSON string from a space GET response."""
    serialized = obj.get("serialized_space")
    if isinstance(serialized, str):
        try:
            serialized = json.loads(serialized)
        except json.JSONDecodeError:
            serialized = {}
    if not isinstance(serialized, dict):
        serialized = {}
    return serialized


def _get_space(space_id: str):
    """Fetch a space and return (serialized_space dict, etag, full object)."""
    host, headers = _get_auth()
    obj = _get_space_full(host, headers, space_id)
    return _parse_serialized(obj), obj.get("etag", ""), obj


class _SpaceEditAbort(Exception):
    """Raised by a mutator to abort an edit without PATCHing (e.g. no match).

    Carries the JSON string the tool should return verbatim.
    """

    def __init__(self, payload: str):
        super().__init__(payload)
        self.payload = payload


def _sort_space_lists(serialized: dict) -> None:
    """Sort the id/name-ordered lists the API requires to be sorted (in place)."""
    benchmarks = serialized.get("benchmarks")
    if isinstance(benchmarks, dict) and isinstance(benchmarks.get("questions"), list):
        benchmarks["questions"] = sorted(
            benchmarks["questions"], key=lambda x: x.get("id", "")
        )
    instructions = serialized.get("instructions")
    if isinstance(instructions, dict):
        for key in ("example_question_sqls", "text_instructions"):
            if isinstance(instructions.get(key), list):
                instructions[key] = sorted(
                    instructions[key], key=lambda x: x.get("id", "")
                )
    for tbl in _iter_space_tables(serialized):
        if isinstance(tbl.get("column_configs"), list):
            tbl["column_configs"] = sorted(
                tbl["column_configs"], key=lambda c: c.get("column_name", "")
            )


def _edit_space(space_id: str, mutator):
    """Apply an edit via GET -> mutator(serialized) -> PATCH, safely under concurrency.

    `mutator` receives the freshly-read serialized_space dict, mutates it in place,
    and returns a result object the caller can use to build its response. On an
    etag conflict (409) the space is re-read and the mutator is re-applied to the
    fresh snapshot before retrying — so a concurrent edit is never clobbered.

    A mutator may raise `_SpaceEditAbort(payload)` to abort without PATCHing (e.g.
    when nothing matched); `_edit_space` re-raises it for the tool to surface.
    """
    host, headers = _get_auth()
    attempts = 3
    for attempt in range(attempts):
        obj = _get_space_full(host, headers, space_id)
        serialized = _parse_serialized(obj)
        result = mutator(serialized)  # may raise _SpaceEditAbort
        _sort_space_lists(serialized)
        body = {"serialized_space": json.dumps(serialized)}
        etag = obj.get("etag", "")
        if etag:
            body["etag"] = etag
        resp = requests.patch(
            f"{host}/api/2.0/genie/spaces/{space_id}",
            headers=headers,
            json=body,
            timeout=120,
        )
        if resp.status_code == 409:
            if attempt < attempts - 1:
                # Stale etag: loop to re-read and re-apply onto fresh state.
                continue
            break  # Persistent conflict: fall through to the clear error below.
        resp.raise_for_status()
        return result
    # Exhausted retries on persistent conflict.
    raise RuntimeError(
        f"Could not apply edit to space {space_id}: repeated etag conflicts "
        "(the space is being modified concurrently). Please retry."
    )


def _split_by_match(items: list, id_or_text: str):
    """Split items into (keep, removed) by matching id or exact question text."""
    target = (id_or_text or "").strip().lower()
    keep, removed = [], []
    for it in items:
        iid = (it.get("id") or "").strip().lower()
        qtext = (_join_lines(it.get("question")) or "").strip().lower()
        if target and (target == iid or target == qtext):
            removed.append(it)
        else:
            keep.append(it)
    return keep, removed


def _get_uc_columns(host: str, headers: dict, full_name: str):
    """Look up a UC table's column names.

    Returns (names, ok). `ok` is False when the lookup failed (non-200 or
    exception) so callers can distinguish "table genuinely has no columns" from
    "couldn't read UC metadata" instead of silently treating both as empty.
    """
    try:
        resp = requests.get(
            f"{host}/api/2.1/unity-catalog/tables/{requests.utils.quote(full_name, safe='')}",
            headers=headers,
            timeout=60,
        )
        if resp.status_code != 200:
            return [], False
        return [c.get("name") for c in resp.json().get("columns", []) if c.get("name")], True
    except Exception:
        return [], False


def _err(message: str) -> str:
    return json.dumps({"status": "error", "message": message})


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
        for tbl in _iter_space_tables(serialized):
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


# ---------------------------------------------------------------------------
# LangChain tools (Genie space editing)
# ---------------------------------------------------------------------------

@tool
def list_space_tables_and_columns(space_id: str) -> str:
    """List all tables and their columns in a Genie space.

    For each column, reports whether it is hidden (excluded from Genie) and any
    configured Genie settings (format assistance, entity matching, synonyms). Use
    this before hiding/showing columns to see the exact table identifier and
    column names.

    Args:
        space_id: The ID of the Genie space to inspect.
    """
    try:
        host, headers = _get_auth()
        serialized, _etag, _obj = _get_space(space_id)
        tables_out = []
        for tbl in _iter_space_tables(serialized):
            ident = tbl.get("identifier") or tbl.get("name") or "unknown"
            cfg_by_name = {
                c.get("column_name"): c
                for c in (tbl.get("column_configs") or [])
                if c.get("column_name")
            }
            uc_cols, uc_ok = _get_uc_columns(host, headers, ident)
            if uc_cols:
                names, source = uc_cols, "unity_catalog"
            elif cfg_by_name:
                names, source = list(cfg_by_name.keys()), "space_config_only"
            elif not uc_ok:
                names, source = [], "unavailable_uc_lookup_failed"
            else:
                names, source = [], "unity_catalog"
            columns = []
            for name in names:
                cfg = cfg_by_name.get(name, {})
                columns.append({
                    "column_name": name,
                    "hidden": bool(cfg.get("exclude", False)),
                    "enable_format_assistance": cfg.get("enable_format_assistance"),
                    "enable_entity_matching": cfg.get("enable_entity_matching"),
                    "synonyms": cfg.get("synonyms"),
                    "configured": name in cfg_by_name,
                })
            table_entry = {
                "table": ident,
                "column_count": len(columns),
                "columns_source": source,
                "columns": columns,
            }
            if source == "unavailable_uc_lookup_failed":
                table_entry["note"] = (
                    "Could not read column metadata from Unity Catalog and the space "
                    "has no per-column config; column list may be incomplete."
                )
            tables_out.append(table_entry)
        return json.dumps({
            "status": "success",
            "space_id": space_id,
            "table_count": len(tables_out),
            "tables": tables_out,
        }, indent=2)
    except Exception as e:
        return _err(f"Failed to list tables and columns: {e}")


@tool
def list_benchmark_questions(space_id: str) -> str:
    """List the benchmark (evaluation) questions configured in a Genie space.

    Args:
        space_id: The ID of the Genie space to inspect.
    """
    try:
        serialized, _etag, _obj = _get_space(space_id)
        questions = (serialized.get("benchmarks") or {}).get("questions") or []
        out = [{
            "id": q.get("id"),
            "question": _join_lines(q.get("question")),
            "answers": [
                {"format": a.get("format"), "content": _join_lines(a.get("content"))}
                for a in (q.get("answer") or [])
            ],
        } for q in questions]
        return json.dumps({
            "status": "success",
            "space_id": space_id,
            "count": len(out),
            "benchmark_questions": out,
        }, indent=2)
    except Exception as e:
        return _err(f"Failed to list benchmark questions: {e}")


@tool
def add_benchmark_question(space_id: str, question: str, sql: str) -> str:
    """Add a benchmark (evaluation) question with its expected SQL answer to a Genie space.

    Benchmark questions are ground-truth Q&A pairs used to evaluate the space's
    query quality.

    Args:
        space_id: The Genie space ID.
        question: The benchmark question text.
        sql: The expected/ground-truth SQL answer for the question.
    """
    new_id = _new_id()

    def mutate(serialized):
        questions = serialized.setdefault("benchmarks", {}).setdefault("questions", [])
        questions.append({
            "id": new_id,
            "question": _wrap_lines(question),
            "answer": [{"format": "SQL", "content": _wrap_lines(sql)}],
        })
        return {"total": len(questions)}

    try:
        res = _edit_space(space_id, mutate)
        return json.dumps({
            "status": "success",
            "message": f"Added benchmark question to space {space_id}.",
            "id": new_id,
            "total_benchmark_questions": res["total"],
        }, indent=2)
    except _SpaceEditAbort as abort:
        return abort.payload
    except Exception as e:
        return _err(f"Failed to add benchmark question: {e}")


@tool
def remove_benchmark_question(space_id: str, id_or_question: str) -> str:
    """Remove a benchmark question from a Genie space, matched by its id or exact question text.

    Args:
        space_id: The Genie space ID.
        id_or_question: The benchmark question's id, or its exact question text.
    """
    def mutate(serialized):
        questions = (serialized.get("benchmarks") or {}).get("questions") or []
        keep, removed = _split_by_match(questions, id_or_question)
        if not removed:
            raise _SpaceEditAbort(json.dumps({
                "status": "warning",
                "message": f"No benchmark question matched '{id_or_question}'.",
                "count": len(questions),
            }))
        serialized.setdefault("benchmarks", {})["questions"] = keep
        return {"removed_ids": [q.get("id") for q in removed], "remaining": len(keep)}

    try:
        res = _edit_space(space_id, mutate)
        return json.dumps({
            "status": "success",
            "message": f"Removed {len(res['removed_ids'])} benchmark question(s) from space {space_id}.",
            "removed_ids": res["removed_ids"],
            "remaining": res["remaining"],
        }, indent=2)
    except _SpaceEditAbort as abort:
        return abort.payload
    except Exception as e:
        return _err(f"Failed to remove benchmark question: {e}")


@tool
def list_sample_questions(space_id: str) -> str:
    """List the sample/example questions (curated example queries) in a Genie space.

    Args:
        space_id: The ID of the Genie space to inspect.
    """
    try:
        serialized, _etag, _obj = _get_space(space_id)
        eqs = (serialized.get("instructions") or {}).get("example_question_sqls") or []
        out = [{
            "id": q.get("id"),
            "question": _join_lines(q.get("question")),
            "sql": _join_lines(q.get("sql")),
            "usage_guidance": _join_lines(q.get("usage_guidance")),
        } for q in eqs]
        return json.dumps({
            "status": "success",
            "space_id": space_id,
            "count": len(out),
            "sample_questions": out,
        }, indent=2)
    except Exception as e:
        return _err(f"Failed to list sample questions: {e}")


@tool
def add_sample_question(space_id: str, question: str, sql: str = "", usage_guidance: str = "") -> str:
    """Add a sample/example question (a curated example query) to a Genie space.

    Sample questions are example Q&A pairs that guide and demonstrate the space's
    capabilities. A SQL answer is strongly recommended.

    Args:
        space_id: The Genie space ID.
        question: The sample question text.
        sql: The example SQL for the question (recommended).
        usage_guidance: Optional guidance on when/how to use this example.
    """
    new_id = _new_id()

    def mutate(serialized):
        eqs = serialized.setdefault("instructions", {}).setdefault("example_question_sqls", [])
        entry = {"id": new_id, "question": _wrap_lines(question)}
        if sql:
            entry["sql"] = _wrap_lines(sql)
        if usage_guidance:
            entry["usage_guidance"] = _wrap_lines(usage_guidance)
        eqs.append(entry)
        return {"total": len(eqs)}

    try:
        res = _edit_space(space_id, mutate)
        return json.dumps({
            "status": "success",
            "message": f"Added sample question to space {space_id}.",
            "id": new_id,
            "total_sample_questions": res["total"],
        }, indent=2)
    except _SpaceEditAbort as abort:
        return abort.payload
    except Exception as e:
        return _err(f"Failed to add sample question: {e}")


@tool
def remove_sample_question(space_id: str, id_or_question: str) -> str:
    """Remove a sample/example question from a Genie space, matched by id or exact question text.

    Args:
        space_id: The Genie space ID.
        id_or_question: The sample question's id, or its exact question text.
    """
    def mutate(serialized):
        eqs = (serialized.get("instructions") or {}).get("example_question_sqls") or []
        keep, removed = _split_by_match(eqs, id_or_question)
        if not removed:
            raise _SpaceEditAbort(json.dumps({
                "status": "warning",
                "message": f"No sample question matched '{id_or_question}'.",
                "count": len(eqs),
            }))
        serialized.setdefault("instructions", {})["example_question_sqls"] = keep
        return {"removed_ids": [q.get("id") for q in removed], "remaining": len(keep)}

    try:
        res = _edit_space(space_id, mutate)
        return json.dumps({
            "status": "success",
            "message": f"Removed {len(res['removed_ids'])} sample question(s) from space {space_id}.",
            "removed_ids": res["removed_ids"],
            "remaining": res["remaining"],
        }, indent=2)
    except _SpaceEditAbort as abort:
        return abort.payload
    except Exception as e:
        return _err(f"Failed to remove sample question: {e}")


@tool
def list_space_instructions(space_id: str) -> str:
    """List the free-text instructions (system guidance) configured in a Genie space.

    Args:
        space_id: The ID of the Genie space to inspect.
    """
    try:
        serialized, _etag, _obj = _get_space(space_id)
        ti = (serialized.get("instructions") or {}).get("text_instructions") or []
        out = [{"id": t.get("id"), "content": _join_lines(t.get("content"))} for t in ti]
        return json.dumps({
            "status": "success",
            "space_id": space_id,
            "count": len(out),
            "instructions": out,
        }, indent=2)
    except Exception as e:
        return _err(f"Failed to list instructions: {e}")


@tool
def set_column_visibility(space_id: str, table: str, columns: str, hidden: bool) -> str:
    """Hide or show one or more columns in a Genie space table.

    Hiding a column (hidden=true) excludes it from Genie so it cannot be queried.
    Showing it (hidden=false) makes it available again. Use
    list_space_tables_and_columns first to get the exact table identifier and
    column names. Note: Genie may, server-side, clear a column's other settings
    (e.g. format assistance) when it is excluded; showing it does not restore them.

    Args:
        space_id: The Genie space ID.
        table: The table identifier (e.g. 'catalog.schema.table') or its short name.
        columns: Comma-separated column name(s) to update.
        hidden: True to hide/exclude the column(s); False to show them.
    """
    target_cols = [c.strip() for c in columns.split(",") if c.strip()]
    if not target_cols:
        return _err("No column names provided.")

    host, headers = _get_auth()

    def mutate(serialized):
        tables = _iter_space_tables(serialized)
        # Prefer an exact identifier match; otherwise fall back to short-name (leaf)
        # match, but refuse to guess when the leaf name is ambiguous.
        tbl = next((t for t in tables if (t.get("identifier") or t.get("name")) == table), None)
        if tbl is None:
            leaf = table.split(".")[-1]
            matches = [t for t in tables if (t.get("identifier") or t.get("name") or "").split(".")[-1] == leaf]
            if len(matches) > 1:
                raise _SpaceEditAbort(_err(
                    f"Table name '{table}' is ambiguous in space {space_id} "
                    f"({len(matches)} tables share that name). Pass the full identifier."
                ))
            tbl = matches[0] if matches else None
        if tbl is None:
            raise _SpaceEditAbort(_err(
                f"Table '{table}' not found in space {space_id}. "
                "Use list_space_tables_and_columns to see available tables."
            ))

        cc = tbl.setdefault("column_configs", [])
        cfg_by_name = {c.get("column_name"): c for c in cc if c.get("column_name")}

        # Reject unknown column names so a typo/hallucination doesn't silently
        # persist a bogus config. Only enforce when UC metadata is readable; a
        # column already in column_configs is always allowed (handles renames /
        # non-UC tables where the lookup can't confirm).
        ident = tbl.get("identifier") or tbl.get("name") or ""
        uc_cols, uc_ok = _get_uc_columns(host, headers, ident)
        if uc_ok and uc_cols:
            valid = set(uc_cols) | set(cfg_by_name)
            unknown = [c for c in target_cols if c not in valid]
            if unknown:
                raise _SpaceEditAbort(_err(
                    f"Column(s) not found in {ident}: {', '.join(unknown)}. "
                    "Use list_space_tables_and_columns to see available columns."
                ))

        updated = []
        for name in target_cols:
            cfg = cfg_by_name.get(name)
            if hidden:
                if cfg is None:
                    cfg = {"column_name": name}
                    cc.append(cfg)
                cfg["exclude"] = True
            else:
                # Showing: nothing to do if there's no config to begin with.
                if cfg is None:
                    continue
                cfg.pop("exclude", None)
                # Drop a now-empty config so "show" leaves no spurious residue.
                if list(cfg.keys()) == ["column_name"]:
                    cc.remove(cfg)
            updated.append(name)
        if not updated:
            # Nothing to change (e.g. showing already-visible columns): abort
            # before PATCH so the space is left byte-for-byte unchanged.
            raise _SpaceEditAbort(json.dumps({
                "status": "success",
                "message": f"No change: the requested column(s) in {table} were already visible.",
                "table": table,
                "columns": [],
                "hidden": hidden,
            }, indent=2))
        return {"updated": updated}

    try:
        res = _edit_space(space_id, mutate)
        action = "Hid" if hidden else "Showed"
        updated = res["updated"]
        return json.dumps({
            "status": "success",
            "message": f"{action} {len(updated)} column(s) in {table}.",
            "table": table,
            "columns": updated,
            "hidden": hidden,
        }, indent=2)
    except _SpaceEditAbort as abort:
        return abort.payload
    except Exception as e:
        return _err(f"Failed to set column visibility: {e}")


ALL_GENIE_TOOLS = [
    list_genie_spaces,
    get_genie_space_config,
    list_space_tables_and_columns,
    list_benchmark_questions,
    add_benchmark_question,
    remove_benchmark_question,
    list_sample_questions,
    add_sample_question,
    remove_sample_question,
    list_space_instructions,
    set_column_visibility,
]
