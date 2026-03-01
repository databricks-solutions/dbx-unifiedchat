# Databricks notebook source
# MAGIC %md
# MAGIC # Genie Room Export / Import Migration Tool
# MAGIC
# MAGIC Exports Genie spaces (rooms) from a **source** workspace and imports them
# MAGIC into a **target** workspace, preserving the `serialized_space` configuration
# MAGIC so that the same underlying tables and instructions are recreated exactly.
# MAGIC
# MAGIC ## Supported Actions
# MAGIC | Action   | Description |
# MAGIC |----------|-------------|
# MAGIC | `export` | Export Genie spaces to JSON files (local or UC Volume) |
# MAGIC | `import` | Import exported spaces into a target workspace |
# MAGIC | `update` | Patch an existing space with an exported configuration |
# MAGIC | `list`   | List all accessible Genie spaces on a workspace |
# MAGIC
# MAGIC ## Running Modes
# MAGIC - **Databricks Notebook / Job** — parameters via widgets (editable in the UI or set as job parameters)
# MAGIC - **Local CLI** — `python scripts/migrate_genie_rooms.py export --space-ids id1,id2`
# MAGIC
# MAGIC ---

# COMMAND ----------

# MAGIC %md
# MAGIC ## Setup & Dependencies

# COMMAND ----------

# MAGIC %pip install python-dotenv requests --quiet

# COMMAND ----------

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_a, **_kw):
        pass

# ---------------------------------------------------------------------------
# Detect runtime: Databricks notebook vs local CLI
# ---------------------------------------------------------------------------
IS_DATABRICKS = False
try:
    _ = dbutils  # noqa: F821 — injected by Databricks runtime
    IS_DATABRICKS = True
except NameError:
    pass

if not IS_DATABRICKS:
    load_dotenv()

print(f"Runtime: {'Databricks notebook/job' if IS_DATABRICKS else 'Local CLI'}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parameters (Widgets / CLI)

# COMMAND ----------

# DBTITLE 1,Configure Parameters
def _get_databricks_native_token() -> str:
    """Retrieve the current notebook's auth token from Databricks context."""
    try:
        return dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()  # noqa: F821
    except Exception:
        return ""


def _get_databricks_host() -> str:
    """Retrieve the workspace host URL from Databricks context."""
    try:
        host = (
            "https://"
            + dbutils.notebook.entry_point.getDbutils().notebook().getContext().browserHostName().get()  # noqa: F821
        )
        return host.rstrip("/")
    except Exception:
        return ""


# -- Databricks mode: create widgets ----------------------------------------
if IS_DATABRICKS:
    dbutils.widgets.removeAll()  # noqa: F821

    # Action selector (maps to Databricks Job parameter "action")
    dbutils.widgets.dropdown("action", "export", ["export", "import", "update", "list"], "Action")  # noqa: F821

    # Source workspace
    _native_host = _get_databricks_host()
    _native_token = _get_databricks_native_token()
    dbutils.widgets.text("source_host", os.getenv("DATABRICKS_HOST", _native_host), "Source Host")  # noqa: F821
    dbutils.widgets.text("source_token", os.getenv("DATABRICKS_TOKEN", _native_token), "Source Token")  # noqa: F821

    # Target workspace (defaults to source if not set)
    _default_target_host = os.getenv("TARGET_DATABRICKS_HOST", "")
    _default_target_token = os.getenv("TARGET_DATABRICKS_TOKEN", "")
    dbutils.widgets.text("target_host", _default_target_host, "Target Host (blank = same as source)")  # noqa: F821
    dbutils.widgets.text("target_token", _default_target_token, "Target Token (blank = same as source)")  # noqa: F821

    # Export parameters
    dbutils.widgets.text(  # noqa: F821
        "genie_space_ids",
        os.getenv("GENIE_SPACE_IDS", ""),
        "Genie Space IDs (comma-separated)",
    )
    dbutils.widgets.text("export_dir", "/tmp/genie_exports", "Export Directory")  # noqa: F821

    # Import parameters
    dbutils.widgets.text(  # noqa: F821
        "target_warehouse_id",
        os.getenv("TARGET_SQL_WAREHOUSE_ID", os.getenv("SQL_WAREHOUSE_ID", "")),
        "Target SQL Warehouse ID (blank = auto-detect/create)",
    )
    dbutils.widgets.text(  # noqa: F821
        "parent_path",
        os.getenv("GENIE_IMPORT_PARENT_PATH", ""),
        "Import Parent Path (blank = auto-detect)",
    )
    dbutils.widgets.text("title_override", "", "Title Override (single import only)")  # noqa: F821

    # Update parameters
    dbutils.widgets.text("update_space_id", "", "Target Space ID (for update)")  # noqa: F821
    dbutils.widgets.text("import_file", "", "Single Export File Path (optional)")  # noqa: F821

    # Volume-based export dir (alternative to /tmp)
    _default_volume = os.getenv("GENIE_EXPORTS_VOLUME", "")
    dbutils.widgets.text("volume_export_dir", _default_volume, "UC Volume for Exports (e.g. catalog.schema.volume)")  # noqa: F821

# COMMAND ----------

# MAGIC %md
# MAGIC ## Resolve Parameters

# COMMAND ----------

# DBTITLE 1,Resolve Parameters into Config Dict
import argparse


def _clean_host(host: str) -> str:
    """Ensure host starts with https:// and has no trailing slash."""
    host = host.strip().rstrip("/")
    if not host.startswith("https://"):
        host = f"https://{host}"
    return host


def _mask_token(token: str) -> str:
    if len(token) > 8:
        return f"{token[:4]}...{token[-4:]}"
    return "****"


def _safe_name(s: str) -> str:
    """Convert string to filesystem-safe name."""
    s = s.strip() or "untitled"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:120]


def _resolve_config_databricks() -> dict:
    """Build config dict from Databricks widgets."""
    action = dbutils.widgets.get("action")  # noqa: F821

    source_host = dbutils.widgets.get("source_host").strip()  # noqa: F821
    source_token = dbutils.widgets.get("source_token").strip()  # noqa: F821
    target_host = dbutils.widgets.get("target_host").strip() or source_host  # noqa: F821
    target_token = dbutils.widgets.get("target_token").strip() or source_token  # noqa: F821

    # Export dir: prefer UC Volume path if set, else widget value
    volume_ref = dbutils.widgets.get("volume_export_dir").strip()  # noqa: F821
    if volume_ref:
        # Convert "catalog.schema.volume" -> "/Volumes/catalog/schema/volume/genie_exports"
        parts = volume_ref.split(".")
        if len(parts) == 3:
            export_dir = f"/Volumes/{parts[0]}/{parts[1]}/{parts[2]}/genie_exports"
        else:
            export_dir = volume_ref  # assume user gave a full path
    else:
        export_dir = dbutils.widgets.get("export_dir").strip() or "/tmp/genie_exports"  # noqa: F821

    return {
        "action": action,
        "source_host": source_host,
        "source_token": source_token,
        "target_host": target_host,
        "target_token": target_token,
        "space_ids": dbutils.widgets.get("genie_space_ids").strip(),  # noqa: F821
        "export_dir": export_dir,
        "warehouse_id": dbutils.widgets.get("target_warehouse_id").strip(),  # noqa: F821
        "parent_path": dbutils.widgets.get("parent_path").strip(),  # noqa: F821
        "title_override": dbutils.widgets.get("title_override").strip() or None,  # noqa: F821
        "update_space_id": dbutils.widgets.get("update_space_id").strip(),  # noqa: F821
        "import_file": dbutils.widgets.get("import_file").strip() or None,  # noqa: F821
    }


def _build_cli_parser() -> argparse.ArgumentParser:
    """Build argparse parser for local CLI usage."""
    parser = argparse.ArgumentParser(
        description="Genie Room Export / Import Migration Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python scripts/migrate_genie_rooms.py export
  python scripts/migrate_genie_rooms.py export --space-ids id1,id2
  python scripts/migrate_genie_rooms.py import --export-dir ./genie_exports
  python scripts/migrate_genie_rooms.py import --file ./genie_exports/abc.export.json --warehouse-id wh123
  python scripts/migrate_genie_rooms.py update --space-id sid --file ./genie_exports/abc.export.json
  python scripts/migrate_genie_rooms.py list
  python scripts/migrate_genie_rooms.py list --target
""",
    )
    parser.add_argument("--source-host", default=os.getenv("DATABRICKS_HOST", ""))
    parser.add_argument("--source-token", default=os.getenv("DATABRICKS_TOKEN", ""))
    parser.add_argument("--target-host", default=os.getenv("TARGET_DATABRICKS_HOST", os.getenv("DATABRICKS_HOST", "")))
    parser.add_argument("--target-token", default=os.getenv("TARGET_DATABRICKS_TOKEN", os.getenv("DATABRICKS_TOKEN", "")))

    sub = parser.add_subparsers(dest="action", help="Available commands")

    p_export = sub.add_parser("export", help="Export Genie spaces to local JSON files")
    p_export.add_argument("--space-ids", default=None)
    p_export.add_argument("--export-dir", default="./genie_exports")

    p_import = sub.add_parser("import", help="Import Genie spaces into target workspace")
    p_import.add_argument("--file", default=None)
    p_import.add_argument("--export-dir", default="./genie_exports")
    p_import.add_argument("--warehouse-id", default=os.getenv("TARGET_SQL_WAREHOUSE_ID", os.getenv("SQL_WAREHOUSE_ID", "")))
    p_import.add_argument("--parent-path", default=os.getenv("GENIE_IMPORT_PARENT_PATH", ""))
    p_import.add_argument("--title", default=None)

    p_update = sub.add_parser("update", help="Update an existing Genie space")
    p_update.add_argument("--space-id", required=True)
    p_update.add_argument("--file", required=True)
    p_update.add_argument("--title", default=None)
    p_update.add_argument("--warehouse-id", default=None)

    p_list = sub.add_parser("list", help="List Genie spaces")
    p_list.add_argument("--target", action="store_true")

    return parser


def _resolve_config_cli() -> dict:
    """Build config dict from CLI arguments."""
    parser = _build_cli_parser()
    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        sys.exit(0)

    cfg = {
        "action": args.action,
        "source_host": args.source_host,
        "source_token": args.source_token,
        "target_host": args.target_host,
        "target_token": args.target_token,
        "space_ids": getattr(args, "space_ids", None) or os.getenv("GENIE_SPACE_IDS", ""),
        "export_dir": getattr(args, "export_dir", "./genie_exports"),
        "warehouse_id": getattr(args, "warehouse_id", "") or "",
        "parent_path": getattr(args, "parent_path", "") or "",
        "title_override": getattr(args, "title", None),
        "update_space_id": getattr(args, "space_id", "") or "",
        "import_file": getattr(args, "file", None),
    }

    # For list --target, swap source/target semantics
    if args.action == "list" and getattr(args, "target", False):
        cfg["_list_target"] = True
    else:
        cfg["_list_target"] = False

    return cfg


# Resolve config based on runtime
if IS_DATABRICKS:
    CFG = _resolve_config_databricks()
    CFG["_list_target"] = False  # not applicable in notebook mode; use target_host widget directly
else:
    CFG = _resolve_config_cli()

print(f"Action : {CFG['action']}")
print(f"Source : {_clean_host(CFG['source_host']) if CFG['source_host'] else '(not set)'}")
print(f"Target : {_clean_host(CFG['target_host']) if CFG['target_host'] else '(same as source)'}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Core API Helpers

# COMMAND ----------

def _api_get(host: str, path: str, token: str, params: Optional[dict] = None,
             timeout: int = 120) -> requests.Response:
    url = f"{host}{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp


def _api_post(host: str, path: str, token: str, payload: dict,
              timeout: int = 120) -> requests.Response:
    url = f"{host}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp


def _api_patch(host: str, path: str, token: str, payload: dict,
               timeout: int = 120) -> requests.Response:
    url = f"{host}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.patch(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp


def _ensure_workspace_dir(host: str, token: str, path: str) -> None:
    """Create a workspace directory (mkdirs) if it doesn't already exist."""
    try:
        _api_post(host, "/api/2.0/workspace/mkdirs", token, {"path": path})
        print(f"  Ensured parent directory exists: {path}")
    except requests.HTTPError as exc:
        # 409 = already exists, which is fine
        if exc.response.status_code != 409:
            print(f"  WARNING: Could not create parent directory '{path}': {exc}")
    except Exception as exc:
        print(f"  WARNING: Could not create parent directory '{path}': {exc}")


def _resolve_parent_path(host: str, token: str, explicit_path: str) -> str:
    """Resolve the parent path for import.

    If not explicitly provided, auto-detect via SCIM /Me endpoint.
    Also ensures the directory exists on the target workspace.
    """
    if explicit_path:
        _ensure_workspace_dir(host, token, explicit_path)
        return explicit_path
    try:
        resp = _api_get(host, "/api/2.0/preview/scim/v2/Me", token, timeout=30)
        username = resp.json().get("userName", "")
        if username:
            path = f"/Workspace/Users/{username}/Genie Spaces"
            print(f"  Auto-detected parent_path: {path}")
            _ensure_workspace_dir(host, token, path)
            return path
    except Exception as exc:
        print(f"  Could not auto-detect parent_path: {exc}")
    fallback = "/Workspace/Genie Spaces"
    _ensure_workspace_dir(host, token, fallback)
    return fallback

# COMMAND ----------

# MAGIC %md
# MAGIC ## SQL Warehouse Resolution
# MAGIC
# MAGIC If no `TARGET_SQL_WAREHOUSE_ID` is provided, the script will:
# MAGIC 1. List all SQL warehouses on the target workspace
# MAGIC 2. Try to reuse an existing **running** or **stopped** serverless warehouse
# MAGIC 3. If none found, create a new **Small serverless** SQL warehouse

# COMMAND ----------

# DBTITLE 1,Warehouse Auto-Detect / Create
_WAREHOUSE_NAME_FOR_MIGRATION = "genie-migration-warehouse"


def _list_warehouses(host: str, token: str) -> List[dict]:
    """List all SQL warehouses on the workspace via GET /api/2.0/sql/warehouses."""
    try:
        resp = _api_get(host, "/api/2.0/sql/warehouses", token, timeout=60)
        return resp.json().get("warehouses", [])
    except requests.HTTPError as exc:
        print(f"    WARNING: Could not list warehouses ({exc.response.status_code}): {exc}")
        return []
    except Exception as exc:
        print(f"    WARNING: Could not list warehouses: {exc}")
        return []


def _validate_warehouse(host: str, token: str, warehouse_id: str) -> bool:
    """Check if a specific warehouse ID exists on the workspace."""
    try:
        resp = _api_get(host, f"/api/2.0/sql/warehouses/{warehouse_id}", token, timeout=30)
        wh = resp.json()
        name = wh.get("name", "")
        state = wh.get("state", "UNKNOWN")
        wh_type = wh.get("warehouse_type", "UNKNOWN")
        print(f"    Validated warehouse: {name} (id={warehouse_id}, type={wh_type}, state={state})")
        return True
    except requests.HTTPError as exc:
        if exc.response.status_code == 404:
            print(f"    Warehouse {warehouse_id} not found on target workspace.")
        else:
            print(f"    Could not validate warehouse {warehouse_id}: {exc}")
        return False
    except Exception as exc:
        print(f"    Could not validate warehouse {warehouse_id}: {exc}")
        return False


def _find_existing_serverless_warehouse(host: str, token: str) -> Optional[str]:
    """Find an existing serverless SQL warehouse that can be reused."""
    warehouses = _list_warehouses(host, token)
    if not warehouses:
        return None

    # Prefer RUNNING serverless, then STOPPED serverless, then any PRO
    preference_order = []
    for wh in warehouses:
        wh_type = wh.get("warehouse_type", "")
        state = wh.get("state", "")
        wh_id = wh.get("id", "")
        name = wh.get("name", "")
        if wh_type == "SERVERLESS" and state == "RUNNING":
            preference_order.insert(0, (wh_id, name, wh_type, state))
        elif wh_type == "SERVERLESS" and state == "STOPPED":
            preference_order.append((wh_id, name, wh_type, state))
        elif wh_type == "PRO" and state in ("RUNNING", "STOPPED"):
            preference_order.append((wh_id, name, wh_type, state))

    if preference_order:
        chosen = preference_order[0]
        print(f"    Found existing warehouse: {chosen[1]} (id={chosen[0]}, type={chosen[2]}, state={chosen[3]})")
        return chosen[0]

    return None


def _create_serverless_warehouse(host: str, token: str) -> Optional[str]:
    """Create a new Small serverless SQL warehouse on the target workspace.

    POST /api/2.0/sql/warehouses
    """
    payload = {
        "name": _WAREHOUSE_NAME_FOR_MIGRATION,
        "cluster_size": "Small",
        "min_num_clusters": 1,
        "max_num_clusters": 1,
        "auto_stop_mins": 10,
        "warehouse_type": "SERVERLESS",
        "enable_serverless_compute": True,
        "enable_photon": True,
        "tags": {
            "custom_tags": [
                {"key": "created_by", "value": "genie_migration_script"},
            ]
        },
    }

    print(f"    Creating new Small serverless SQL warehouse '{_WAREHOUSE_NAME_FOR_MIGRATION}' ...")
    try:
        resp = _api_post(host, "/api/2.0/sql/warehouses", token, payload)
        result = resp.json()
        wh_id = result.get("id", "")
        print(f"    SUCCESS - warehouse created: {wh_id}")
        print(f"    The warehouse will auto-start. Auto-stop after 10 min idle.")
        return wh_id
    except requests.HTTPError as exc:
        print(f"    FAILED to create warehouse ({exc.response.status_code}): {exc}")
        try:
            print(f"    Response: {exc.response.text[:500]}")
        except Exception:
            pass
        return None
    except Exception as exc:
        print(f"    FAILED to create warehouse: {exc}")
        return None


def _resolve_warehouse_id(host: str, token: str, warehouse_id: str) -> str:
    """Resolve a valid warehouse ID on the target workspace.

    Logic:
      1. If warehouse_id is provided and valid on target -> use it
      2. If warehouse_id is provided but NOT found -> fall through to auto-detect
      3. Try to find an existing serverless/PRO warehouse -> use it
      4. Create a new Small serverless warehouse -> use it
      5. If all fails -> raise error
    """
    print("\n  Resolving SQL warehouse on target workspace ...")

    # Step 1: Validate explicitly provided warehouse ID
    if warehouse_id:
        if _validate_warehouse(host, token, warehouse_id):
            return warehouse_id
        print(f"    Provided warehouse_id '{warehouse_id}' not found on target. Auto-detecting ...")

    # Step 2: Try to find an existing suitable warehouse
    print("    Searching for existing serverless warehouses ...")
    existing = _find_existing_serverless_warehouse(host, token)
    if existing:
        return existing

    # Step 3: Create a new one
    print("    No suitable existing warehouse found. Creating a new one ...")
    created = _create_serverless_warehouse(host, token)
    if created:
        return created

    raise ValueError(
        "Could not resolve a SQL warehouse on the target workspace. "
        "Please set TARGET_SQL_WAREHOUSE_ID in .env or create a warehouse manually."
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Action: Export

# COMMAND ----------

# DBTITLE 1,Export Genie Spaces
def export_space(host: str, token: str, space_id: str, export_dir: Path) -> Optional[Path]:
    """Export a single Genie space to a JSON file.

    The export file contains everything needed for re-import:
      - space metadata (title, description, warehouse_id)
      - serialized_space (full configuration blob)
    """
    print(f"\n  Fetching space {space_id} ...")
    try:
        resp = _api_get(
            host,
            f"/api/2.0/genie/spaces/{space_id}",
            token,
            params={"include_serialized_space": "true"},
        )
    except requests.HTTPError as exc:
        print(f"    FAILED ({exc.response.status_code}): {exc}")
        return None
    except Exception as exc:
        print(f"    FAILED: {exc}")
        return None

    data = resp.json()
    title = data.get("title", space_id)
    print(f"    Title       : {title}")
    print(f"    Warehouse   : {data.get('warehouse_id', 'N/A')}")
    has_serialized = "serialized_space" in data and data["serialized_space"]
    print(f"    Serialized  : {'yes' if has_serialized else 'NO - migration will be incomplete'}")

    export_payload = {
        "_exported_at": datetime.now(tz=__import__('datetime').timezone.utc).isoformat(),
        "_source_host": host,
        "_source_space_id": space_id,
        "space_id": space_id,
        "title": title,
        "description": data.get("description", ""),
        "warehouse_id": data.get("warehouse_id", ""),
        "serialized_space": data.get("serialized_space", ""),
    }

    filename = f"{space_id}__{_safe_name(title)}.export.json"
    out_path = export_dir / filename
    out_path.write_text(json.dumps(export_payload, indent=2), encoding="utf-8")
    print(f"    Saved       : {out_path}")
    return out_path


def run_export(cfg: dict) -> None:
    host = _clean_host(cfg["source_host"])
    token = cfg["source_token"]

    if not host or not token:
        raise ValueError("Source host and token are required for export. Set DATABRICKS_HOST / DATABRICKS_TOKEN.")

    space_ids_raw = cfg.get("space_ids", "") or os.getenv("GENIE_SPACE_IDS", "")
    space_ids = [s.strip() for s in space_ids_raw.split(",") if s.strip()]
    if not space_ids:
        raise ValueError("No space IDs provided. Set genie_space_ids widget or GENIE_SPACE_IDS env var.")

    export_dir = Path(cfg["export_dir"])
    export_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("GENIE SPACE EXPORT")
    print("=" * 72)
    print(f"  Source host  : {host}")
    print(f"  Token        : {_mask_token(token)}")
    print(f"  Space IDs    : {len(space_ids)}")
    print(f"  Export dir   : {export_dir}")
    print("=" * 72)

    success, failed = [], []
    for i, sid in enumerate(space_ids, 1):
        print(f"\n[{i}/{len(space_ids)}] Exporting {sid}")
        result = export_space(host, token, sid, export_dir)
        (success if result else failed).append(sid)

    print("\n" + "=" * 72)
    print("EXPORT SUMMARY")
    print("=" * 72)
    print(f"  Exported : {len(success)}/{len(space_ids)}")
    if failed:
        print(f"  Failed   : {', '.join(failed)}")
    print(f"  Location : {export_dir}")
    print("=" * 72)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Action: Import

# COMMAND ----------

# DBTITLE 1,Import Genie Spaces
def import_space(host: str, token: str, export_data: dict,
                 warehouse_id: str, parent_path: str,
                 title_override: Optional[str] = None) -> Optional[str]:
    """Import a Genie space from export data. Returns the new space_id."""
    title = title_override or export_data.get("title", "Imported Space")
    serialized = export_data.get("serialized_space", "")
    description = export_data.get("description", "")

    if not serialized:
        print(f"    WARNING: No serialized_space - the imported room will be empty.")

    source_info = export_data.get("_source_host", "unknown")
    source_id = export_data.get("_source_space_id", "unknown")
    if description:
        description += f"\n\n[Migrated from {source_info} | original space: {source_id}]"
    else:
        description = f"Migrated from {source_info} | original space: {source_id}"

    payload = {
        "title": title,
        "description": description,
        "warehouse_id": warehouse_id,
        "parent_path": parent_path,
        "serialized_space": serialized,
    }

    print(f"    Creating '{title}' on {host} ...")
    try:
        resp = _api_post(host, "/api/2.0/genie/spaces", token, payload)
    except requests.HTTPError as exc:
        print(f"    FAILED ({exc.response.status_code}): {exc}")
        try:
            print(f"    Response body: {exc.response.text[:500]}")
        except Exception:
            pass
        return None
    except Exception as exc:
        print(f"    FAILED: {exc}")
        return None

    result = resp.json()
    new_id = result.get("space_id", result.get("id", "unknown"))
    print(f"    SUCCESS - new space_id: {new_id}")
    return new_id


def run_import(cfg: dict) -> None:
    host = _clean_host(cfg["target_host"])
    token = cfg["target_token"]

    if not host or not token:
        raise ValueError("Target host and token are required for import.")

    # Resolve warehouse: validate, auto-detect, or create
    warehouse_id = _resolve_warehouse_id(host, token, cfg.get("warehouse_id", ""))

    parent_path = _resolve_parent_path(host, token, cfg["parent_path"])

    # Collect export files
    export_files: List[Path] = []
    if cfg.get("import_file"):
        fp = Path(cfg["import_file"])
        if not fp.is_file():
            raise FileNotFoundError(f"Import file not found: {fp}")
        export_files.append(fp)
    else:
        d = Path(cfg["export_dir"])
        if not d.is_dir():
            raise FileNotFoundError(f"Export directory not found: {d}")
        export_files = sorted(d.glob("*.export.json"))

    if not export_files:
        raise FileNotFoundError("No .export.json files found.")

    print("=" * 72)
    print("GENIE SPACE IMPORT")
    print("=" * 72)
    print(f"  Target host  : {host}")
    print(f"  Token        : {_mask_token(token)}")
    print(f"  Warehouse ID : {warehouse_id}")
    print(f"  Parent path  : {parent_path}")
    print(f"  Files        : {len(export_files)}")
    print("=" * 72)

    results = []
    for i, fp in enumerate(export_files, 1):
        print(f"\n[{i}/{len(export_files)}] Importing from {fp.name}")
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"    FAILED to read file: {exc}")
            results.append({"file": fp.name, "status": "read_error"})
            continue

        title = cfg.get("title_override")
        new_id = import_space(host, token, data, warehouse_id, parent_path, title)
        results.append({
            "file": fp.name,
            "original_space_id": data.get("space_id", ""),
            "original_title": data.get("title", ""),
            "new_space_id": new_id,
            "status": "success" if new_id else "failed",
        })

    # Summary
    print("\n" + "=" * 72)
    print("IMPORT SUMMARY")
    print("=" * 72)
    ok = [r for r in results if r["status"] == "success"]
    fail = [r for r in results if r["status"] != "success"]
    print(f"  Imported : {len(ok)}/{len(results)}")
    for r in ok:
        print(f"    {r['original_title']}")
        print(f"      old: {r['original_space_id']}  ->  new: {r['new_space_id']}")
    if fail:
        print(f"\n  Failed   : {len(fail)}")
        for r in fail:
            print(f"    {r['file']}: {r['status']}")

    # Write mapping file
    mapping_dir = Path(cfg.get("import_file") or cfg["export_dir"]).parent if cfg.get("import_file") else Path(cfg["export_dir"])
    mapping_path = mapping_dir / "migration_mapping.json"
    mapping_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n  Mapping file : {mapping_path}")
    print("=" * 72)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Action: Update

# COMMAND ----------

# DBTITLE 1,Update Existing Genie Space
def run_update(cfg: dict) -> None:
    host = _clean_host(cfg["target_host"])
    token = cfg["target_token"]
    space_id = cfg.get("update_space_id", "")

    if not host or not token:
        raise ValueError("Target host and token are required for update.")
    if not space_id:
        raise ValueError("update_space_id is required for update.")

    # Locate the export file
    fp = None
    if cfg.get("import_file"):
        fp = Path(cfg["import_file"])
    else:
        # Try to find a matching file in export_dir
        d = Path(cfg["export_dir"])
        candidates = sorted(d.glob("*.export.json"))
        if len(candidates) == 1:
            fp = candidates[0]
            print(f"  Auto-selected export file: {fp.name}")
        elif len(candidates) > 1:
            raise ValueError(
                f"Multiple .export.json files found in {d}. "
                "Use import_file widget or --file to specify which one."
            )

    if not fp or not fp.is_file():
        raise FileNotFoundError(f"Export file not found: {fp}")

    data = json.loads(fp.read_text(encoding="utf-8"))

    payload: Dict = {}
    if data.get("serialized_space"):
        payload["serialized_space"] = data["serialized_space"]
    if cfg.get("title_override"):
        payload["title"] = cfg["title_override"]
    elif data.get("title"):
        payload["title"] = data["title"]
    if cfg.get("warehouse_id"):
        resolved_wh = _resolve_warehouse_id(host, token, cfg["warehouse_id"])
        payload["warehouse_id"] = resolved_wh
    if data.get("description"):
        payload["description"] = data["description"]

    if not payload:
        raise ValueError("Nothing to update - export file has no usable fields.")

    print("=" * 72)
    print("GENIE SPACE UPDATE")
    print("=" * 72)
    print(f"  Target host  : {host}")
    print(f"  Space ID     : {space_id}")
    print(f"  Source file   : {fp.name}")
    print(f"  Fields       : {', '.join(payload.keys())}")
    print("=" * 72)

    try:
        resp = _api_patch(host, f"/api/2.0/genie/spaces/{space_id}", token, payload)
        result = resp.json()
        print(f"\n  SUCCESS - space updated: {result.get('space_id', space_id)}")
    except requests.HTTPError as exc:
        print(f"\n  FAILED ({exc.response.status_code}): {exc}")
        try:
            print(f"  Response: {exc.response.text[:500]}")
        except Exception:
            pass
        raise
    except Exception as exc:
        print(f"\n  FAILED: {exc}")
        raise

# COMMAND ----------

# MAGIC %md
# MAGIC ## Action: List

# COMMAND ----------

# DBTITLE 1,List Genie Spaces
def run_list(cfg: dict) -> None:
    # In CLI mode, --target flag swaps to target workspace
    if cfg.get("_list_target"):
        host = _clean_host(cfg["target_host"])
        token = cfg["target_token"]
        label = "TARGET"
    else:
        host = _clean_host(cfg["source_host"])
        token = cfg["source_token"]
        label = "SOURCE"

    if not host or not token:
        raise ValueError(f"{label} host and token are required for list.")

    print("=" * 72)
    print(f"GENIE SPACES ON {label} WORKSPACE")
    print("=" * 72)
    print(f"  Host  : {host}")
    print(f"  Token : {_mask_token(token)}")
    print("=" * 72)

    spaces: List[dict] = []
    params: dict = {}
    while True:
        try:
            resp = _api_get(host, "/api/2.0/genie/spaces", token, params=params, timeout=60)
            data = resp.json()
            spaces.extend(data.get("spaces", []))
            next_token = data.get("next_page_token") or data.get("page_token")
            if not next_token:
                break
            params = {"page_token": next_token}
        except requests.HTTPError as exc:
            print(f"\n  API error ({exc.response.status_code}): {exc}")
            raise
        except Exception as exc:
            print(f"\n  Error: {exc}")
            raise

    if not spaces:
        print("\n  No Genie spaces found.")
        return

    print(f"\n  Found {len(spaces)} Genie space(s):\n")
    print(f"  {'#':<4} {'Space ID':<36} {'Title'}")
    print(f"  {'---':<4} {'---':<36} {'---':<40}")
    for i, sp in enumerate(spaces, 1):
        sid = sp.get("space_id", sp.get("id", "?"))
        title = sp.get("title", "(untitled)")
        print(f"  {i:<4} {sid:<36} {title}")

    print(f"\n  Total: {len(spaces)} spaces")
    print("=" * 72)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Execute Selected Action

# COMMAND ----------

# DBTITLE 1,Run Migration Action
ACTION_MAP = {
    "export": run_export,
    "import": run_import,
    "update": run_update,
    "list": run_list,
}

action = CFG["action"]
if action not in ACTION_MAP:
    raise ValueError(f"Unknown action: '{action}'. Must be one of: {', '.join(ACTION_MAP.keys())}")

print(f"\n>>> Running action: {action}\n")
ACTION_MAP[action](CFG)
print(f"\n>>> Action '{action}' completed.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Local CLI Entry Point

# COMMAND ----------

# This block is only reached when running as `python scripts/migrate_genie_rooms.py` directly.
# In Databricks notebook mode the cells above already executed everything.
# The `if __name__` guard is technically never True in notebook mode (cells run at module level),
# but we keep it for clarity and to prevent double-execution if someone imports this module.

if __name__ == "__main__" and not IS_DATABRICKS:
    # Already executed above via the ACTION_MAP dispatch cell.
    # Nothing additional needed — the script is fully driven by the cells above.
    pass
