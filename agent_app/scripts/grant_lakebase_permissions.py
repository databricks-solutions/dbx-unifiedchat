"""Grant Lakebase and Unity Catalog permissions to a Databricks Apps service principal.

After deploying the app, run this script to grant the app's SP access to the
Lakebase schemas/tables used by the agent's memory, plus the Unity Catalog
catalog/schema that contain the app's data assets.

Usage:
    # Provisioned instance:
    uv run python scripts/grant_lakebase_permissions.py <sp-client-id> --memory-type <type> --instance-name <name>
    uv run python scripts/grant_lakebase_permissions.py --app-name <app-name> --memory-type <type> --instance-name <name>

    # Autoscaling instance:
    uv run python scripts/grant_lakebase_permissions.py <sp-client-id> --memory-type <type> --project <project> --branch <branch>
    uv run python scripts/grant_lakebase_permissions.py --app-name <app-name> --memory-type <type> --project <project> --branch <branch>

    # Memory types: langgraph-short-term, langgraph-long-term, openai-short-term
"""

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import yaml

# Per-memory-type table definitions for public schema.
MEMORY_TYPE_TABLES: dict[str, list[str]] = {
    "langgraph-short-term": [
        "checkpoint_migrations",
        "checkpoint_writes",
        "checkpoints",
        "checkpoint_blobs",
    ],
    "langgraph-long-term": [
        "store_migrations",
        "store",
        "store_vectors",
        "vector_migrations",
    ],
    "openai-short-term": [
        "agent_sessions",
        "agent_messages",
    ],
}

# Schemas that need sequence privileges for all app variants.
# ai_chatbot.__drizzle_migrations uses a sequence-backed id column.
SHARED_SEQUENCE_SCHEMAS = {"ai_chatbot"}

# Memory types that need sequence privileges on memory tables.
MEMORY_TYPE_SEQUENCE_SCHEMAS = {
    "openai-short-term": {"public"},
}

# Shared schemas granted for all memory types (chat UI persistence)
SHARED_SCHEMAS: dict[str, list[str]] = {
    "ai_chatbot": ["Chat", "Message", "User", "Vote", "__drizzle_migrations"],
}

DEFAULT_DATABASE_NAME = "databricks_postgres"
DEFAULT_BUNDLE_CONFIG_PATH = Path(__file__).resolve().parents[1] / "databricks.yml"


class CatalogSchemaTarget(NamedTuple):
    catalog_name: str
    schema_name: str


@dataclass
class PermissionGrantConfig:
    memory_type: str
    sp_client_id: str | None = None
    app_name: str | None = None
    profile: str | None = None
    target: str | None = None
    bundle_config_path: str | None = None
    catalog_name: str | None = None
    schema_name: str | None = None
    data_catalog_name: str | None = None
    data_schema_name: str | None = None
    data_catalog_schemas: str | None = None
    volume_name: str | None = None
    instance_name: str | None = None
    database_name: str | None = None
    genie_space_ids: list[str] | None = None
    project: str | None = None
    branch: str | None = None
    warehouse_id: str | None = None


def build_workspace_client(profile: str | None):
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def _parse_csv(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [part.strip() for part in raw_value.split(",") if part.strip()]


def parse_catalog_schema_targets(raw_value: str | None) -> list[CatalogSchemaTarget]:
    """Parse 'catalog:schema1, schema2; other:schema_a' into target pairs."""
    if not raw_value:
        return []

    targets: list[CatalogSchemaTarget] = []
    seen: set[CatalogSchemaTarget] = set()
    for group in raw_value.split(";"):
        group = group.strip()
        if not group:
            continue
        if ":" not in group:
            raise ValueError(
                "Invalid data_catalog_schemas entry "
                f"{group!r}. Expected format: catalog:schema1, schema2; other_catalog:schema_a."
            )

        catalog_name, schemas_raw = group.split(":", 1)
        catalog_name = catalog_name.strip()
        schema_names = [schema.strip() for schema in schemas_raw.split(",") if schema.strip()]
        if not catalog_name or not schema_names:
            raise ValueError(
                "Invalid data_catalog_schemas entry "
                f"{group!r}. Each catalog must include at least one schema."
            )

        for schema_name in schema_names:
            target = CatalogSchemaTarget(catalog_name, schema_name)
            if target not in seen:
                targets.append(target)
                seen.add(target)

    return targets


def resolve_data_uc_targets(config: PermissionGrantConfig) -> list[CatalogSchemaTarget]:
    targets = parse_catalog_schema_targets(config.data_catalog_schemas)
    if targets:
        return targets

    seen = set(targets)
    if config.data_catalog_name and config.data_schema_name:
        legacy_target = CatalogSchemaTarget(config.data_catalog_name, config.data_schema_name)
        if legacy_target not in seen:
            targets.append(legacy_target)
    return targets


def _load_bundle_config(path: str | None) -> dict:
    config_path = Path(path) if path else DEFAULT_BUNDLE_CONFIG_PATH
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text()) or {}


def _resolve_bundle_target(bundle_config: dict, target: str | None, profile: str | None) -> str | None:
    targets = bundle_config.get("targets") or {}
    if target:
        if target not in targets:
            raise ValueError(f"Bundle target '{target}' not found in databricks.yml.")
        return target

    if profile:
        for target_name, target_config in targets.items():
            workspace_profile = ((target_config or {}).get("workspace") or {}).get("profile")
            if workspace_profile == profile:
                return target_name

    for target_name, target_config in targets.items():
        if (target_config or {}).get("default") is True:
            return target_name

    return next(iter(targets), None)


def _resolve_bundle_variable(bundle_config: dict, target: str | None, variable_name: str):
    value = None
    if target:
        value = (((bundle_config.get("targets") or {}).get(target) or {}).get("variables") or {}).get(variable_name)

    if value is None:
        value = ((bundle_config.get("variables") or {}).get(variable_name) or {}).get("default")

    if isinstance(value, str) and target:
        value = value.replace("${bundle.target}", target)

    return value


def hydrate_config_from_bundle(config: PermissionGrantConfig) -> PermissionGrantConfig:
    bundle_config = _load_bundle_config(config.bundle_config_path)
    if not bundle_config:
        config.database_name = config.database_name or DEFAULT_DATABASE_NAME
        config.genie_space_ids = config.genie_space_ids or []
        return config

    target = _resolve_bundle_target(bundle_config, config.target, config.profile)
    config.target = config.target or target

    if not config.profile and target:
        config.profile = (((bundle_config.get("targets") or {}).get(target) or {}).get("workspace") or {}).get("profile")

    config.catalog_name = config.catalog_name or _resolve_bundle_variable(
        bundle_config, target, "catalog_name"
    )
    config.schema_name = config.schema_name or _resolve_bundle_variable(
        bundle_config, target, "schema_name"
    )
    config.data_catalog_name = config.data_catalog_name or _resolve_bundle_variable(
        bundle_config, target, "data_catalog_name"
    )
    config.data_schema_name = config.data_schema_name or _resolve_bundle_variable(
        bundle_config, target, "data_schema_name"
    )
    config.data_catalog_schemas = config.data_catalog_schemas or _resolve_bundle_variable(
        bundle_config, target, "data_catalog_schemas"
    )
    config.volume_name = config.volume_name or _resolve_bundle_variable(
        bundle_config, target, "volume_name"
    )
    config.project = config.project or _resolve_bundle_variable(bundle_config, target, "lakebase_project")
    config.branch = config.branch or _resolve_bundle_variable(bundle_config, target, "lakebase_branch")
    config.instance_name = config.instance_name or _resolve_bundle_variable(
        bundle_config, target, "lakebase_instance_name"
    )
    config.warehouse_id = config.warehouse_id or _resolve_bundle_variable(
        bundle_config, target, "sql_warehouse_id"
    )
    config.database_name = config.database_name or DEFAULT_DATABASE_NAME

    if not config.genie_space_ids:
        genie_space_ids = _parse_csv(_resolve_bundle_variable(bundle_config, target, "genie_space_ids"))
        if not genie_space_ids:
            for variable_name in ("genie_space_id_1", "genie_space_id_2", "genie_space_id_3"):
                value = _resolve_bundle_variable(bundle_config, target, variable_name)
                if isinstance(value, str) and value.strip():
                    genie_space_ids.append(value.strip())
        config.genie_space_ids = genie_space_ids

    return config


def quote_ident(identifier: str) -> str:
    """Quote a Postgres identifier safely."""
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def quote_sql_ident(identifier: str) -> str:
    """Quote a Databricks SQL identifier safely."""
    return f"`{identifier.replace('`', '``')}`"


def format_privileges(privileges) -> str:
    return ", ".join(privilege.value for privilege in privileges)


def grant_schema_direct(client, grantee: str, schema: str, privileges) -> None:
    client.execute(
        f"GRANT {format_privileges(privileges)} ON SCHEMA {quote_ident(schema)} "
        f"TO {quote_ident(grantee)}"
    )


def grant_tables_direct(
    client, grantee: str, schema: str, tables: list[str], privileges
) -> None:
    if not tables:
        return

    qualified_tables = ", ".join(
        f"{quote_ident(schema)}.{quote_ident(table)}" for table in tables
    )
    client.execute(
        f"GRANT {format_privileges(privileges)} ON TABLE {qualified_tables} "
        f"TO {quote_ident(grantee)}"
    )


def grant_sequences_direct(client, grantee: str, schema: str, privileges) -> None:
    client.execute(
        f"GRANT {format_privileges(privileges)} ON ALL SEQUENCES IN SCHEMA "
        f"{quote_ident(schema)} TO {quote_ident(grantee)}"
    )


def _execute_grant(use_direct: bool, sdk_fn, direct_fn, label: str) -> None:
    """Run a grant via SDK or direct SQL, with unified error handling."""
    try:
        if use_direct:
            direct_fn()
        else:
            sdk_fn()
    except Exception as e:
        error_text = str(e).lower()
        if use_direct and "role" in error_text and "does not exist" in error_text:
            raise RuntimeError(
                "The app service principal role is not ready in Postgres yet. "
                "Start the Databricks App once so it connects to Lakebase, then "
                "re-run the grant step."
            ) from e
        print(f"  Warning: {label} grant failed (may not exist yet): {e}")


def resolve_app_sp_client_id(
    app_name: str,
    profile: str | None = None,
    workspace_client=None,
) -> str:
    workspace_client = workspace_client or build_workspace_client(profile)
    app = workspace_client.apps.get(app_name)
    sp_client_id = getattr(app, "service_principal_client_id", None)
    if not sp_client_id:
        raise RuntimeError(
            f"Databricks app '{app_name}' did not return service_principal_client_id."
        )
    return sp_client_id


def grant_uc_permissions(
    workspace_client,
    grantee: str,
    catalog_name: str,
    schema_name: str,
    uc_catalog,
    schema_privileges,
    warehouse_id: str | None,
) -> None:
    if warehouse_id:
        print(
            "Granting Unity Catalog privileges via SQL GRANT "
            f"using warehouse '{warehouse_id}'..."
        )
        grant_uc_permissions_via_sql(
            workspace_client=workspace_client,
            warehouse_id=warehouse_id,
            grantee=grantee,
            catalog_name=catalog_name,
            schema_name=schema_name,
            schema_privileges=schema_privileges,
        )
        print(
            "  Granted Unity Catalog privileges on "
            f"'{catalog_name}.{schema_name}'."
        )
        return

    schema_full_name = f"{catalog_name}.{schema_name}"
    grants = [
        (
            "catalog",
            uc_catalog.SecurableType.CATALOG,
            catalog_name,
            [uc_catalog.Privilege.USE_CATALOG],
        ),
        (
            "schema",
            uc_catalog.SecurableType.SCHEMA,
            schema_full_name,
            schema_privileges,
        ),
    ]

    for label, securable_type, full_name, privileges in grants:
        print(f"Granting Unity Catalog {label} privileges on '{full_name}'...")
        try:
            workspace_client.grants.update(
                securable_type=securable_type,
                full_name=full_name,
                changes=[
                    uc_catalog.PermissionsChange(
                        add=privileges,
                        principal=grantee,
                    )
                ],
            )
        except Exception as e:
            print(f"  Warning: Unity Catalog {label} grant failed: {e}")


def _execute_sql_statement(workspace_client, warehouse_id: str, statement: str) -> None:
    from databricks.sdk.service.sql import StatementState

    response = workspace_client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="30s",
    )
    status = response.status
    while status and status.state in {StatementState.PENDING, StatementState.RUNNING}:
        time.sleep(1)
        response = workspace_client.statement_execution.get_statement(response.statement_id)
        status = response.status

    if status and status.state.name == "FAILED":
        error = status.error.message if status.error else "Unknown SQL execution failure"
        raise RuntimeError(error)
    if status and status.state.name in {"CANCELED", "CLOSED"}:
        raise RuntimeError(f"SQL execution ended with state {status.state.name}")


def grant_uc_permissions_via_sql(
    workspace_client,
    warehouse_id: str,
    grantee: str,
    catalog_name: str,
    schema_name: str,
    schema_privileges,
) -> None:
    catalog_stmt = (
        f"GRANT USE CATALOG ON CATALOG {quote_sql_ident(catalog_name)} "
        f"TO {quote_sql_ident(grantee)}"
    )
    schema_privilege_sql = ", ".join(privilege.value for privilege in schema_privileges)
    schema_stmt = (
        f"GRANT {schema_privilege_sql} ON SCHEMA "
        f"{quote_sql_ident(catalog_name)}.{quote_sql_ident(schema_name)} "
        f"TO {quote_sql_ident(grantee)}"
    )
    print(f"  SQL: {catalog_stmt}")
    _execute_sql_statement(workspace_client, warehouse_id, catalog_stmt)
    print(f"  SQL: {schema_stmt}")
    _execute_sql_statement(workspace_client, warehouse_id, schema_stmt)


def validate_permission_config(config: PermissionGrantConfig) -> None:
    if config.memory_type not in MEMORY_TYPE_TABLES:
        raise ValueError(
            f"Unsupported memory_type '{config.memory_type}'. "
            f"Expected one of: {', '.join(sorted(MEMORY_TYPE_TABLES))}."
        )

    has_provisioned = bool(config.instance_name)
    has_autoscaling = bool(config.project and config.branch)

    if not has_provisioned and not has_autoscaling:
        raise ValueError(
            "Lakebase connection is required. Provide either "
            "--instance-name or both --project and --branch."
        )

    if bool(config.sp_client_id) == bool(config.app_name):
        raise ValueError("Provide exactly one of sp_client_id or app_name.")

    if bool(config.catalog_name) != bool(config.schema_name):
        raise ValueError(
            "Provide both catalog_name and schema_name together, or omit both."
        )

    if bool(config.data_catalog_name) != bool(config.data_schema_name):
        raise ValueError(
            "Provide both data_catalog_name and data_schema_name together, or omit both."
        )

    parse_catalog_schema_targets(config.data_catalog_schemas)

    if config.volume_name and not (config.catalog_name and config.schema_name):
        raise ValueError(
            "Provide catalog_name and schema_name when volume_name is configured."
        )


def _postgres_branch_name(config: PermissionGrantConfig) -> str | None:
    if not (config.project and config.branch):
        return None
    return f"projects/{config.project}/branches/{config.branch}"


def _postgres_database_name(config: PermissionGrantConfig) -> str | None:
    branch_name = _postgres_branch_name(config)
    if not branch_name:
        return None
    return f"{branch_name}/databases/{config.database_name or DEFAULT_DATABASE_NAME}"


def _sdk_supports_postgres_app_resource(apps_service) -> bool:
    return all(
        hasattr(apps_service, attr)
        for attr in (
            "AppResourcePostgres",
            "AppResourcePostgresPostgresPermission",
        )
    )


def _resolve_postgres_database_resource_name(
    config: PermissionGrantConfig,
    workspace_client,
    *,
    wait_timeout_seconds: int = 30,
    poll_interval_seconds: int = 2,
) -> str | None:
    branch_name = _postgres_branch_name(config)
    if not branch_name:
        return None

    postgres_database_name = config.database_name or DEFAULT_DATABASE_NAME
    deadline = time.time() + wait_timeout_seconds

    while True:
        response = workspace_client.api_client.do(
            "GET",
            f"/api/2.0/postgres/{branch_name}/databases",
        )
        databases = list((response or {}).get("databases") or [])
        for database in databases:
            status = database.get("status") or {}
            if status.get("postgres_database") == postgres_database_name:
                return database.get("name")

        if time.time() >= deadline:
            return None

        time.sleep(poll_interval_seconds)


def _require_postgres_database_resource_name(config: PermissionGrantConfig, workspace_client) -> str | None:
    branch_name = _postgres_branch_name(config)
    if not branch_name:
        return None

    postgres_database_resource_name = _resolve_postgres_database_resource_name(config, workspace_client)
    if postgres_database_resource_name:
        return postgres_database_resource_name

    postgres_database_name = config.database_name or DEFAULT_DATABASE_NAME
    raise RuntimeError(
        "Lakebase autoscaling database resource did not become available within 30 seconds: "
        f"branch={branch_name}, postgres_database={postgres_database_name}. "
        "Cannot grant app postgres CAN_CONNECT_AND_CREATE permission."
    )


def _sync_app_resource_permissions_raw(config: PermissionGrantConfig, workspace_client) -> None:
    resources_payload = workspace_client.api_client.do("GET", f"/api/2.0/apps/{config.app_name}")
    resources = list((resources_payload or {}).get("resources") or [])
    updated_resources: list[dict] = []

    volume_full_name = None
    if config.catalog_name and config.schema_name and config.volume_name:
        volume_full_name = f"{config.catalog_name}.{config.schema_name}.{config.volume_name}"

    postgres_branch_name = _postgres_branch_name(config)
    postgres_database_resource_name = _require_postgres_database_resource_name(config, workspace_client)

    for resource in resources:
        name = resource.get("name")
        if name == "database" and (config.instance_name or postgres_branch_name):
            continue
        if name == "postgres" and postgres_branch_name:
            continue
        if config.genie_space_ids and resource.get("genie_space") is not None:
            continue

        uc_securable = resource.get("uc_securable")
        if (
            volume_full_name
            and uc_securable is not None
            and uc_securable.get("securable_type") == "VOLUME"
            and uc_securable.get("securable_full_name") == volume_full_name
        ):
            continue
        updated_resources.append(resource)

    if config.instance_name:
        print(
            "Ensuring app database resource grants "
            f"{config.database_name}@{config.instance_name} with CAN_CONNECT_AND_CREATE..."
        )
        updated_resources.append(
            {
                "name": "database",
                "database": {
                    "instance_name": config.instance_name,
                    "database_name": config.database_name or DEFAULT_DATABASE_NAME,
                    "permission": "CAN_CONNECT_AND_CREATE",
                },
            }
        )

    if postgres_branch_name and postgres_database_resource_name:
        print(
            "Ensuring app autoscaling Lakebase resource grants "
            f"CAN_CONNECT_AND_CREATE on {postgres_database_resource_name}..."
        )
        updated_resources.append(
            {
                "name": "postgres",
                "postgres": {
                    "branch": postgres_branch_name,
                    "database": postgres_database_resource_name,
                    "permission": "CAN_CONNECT_AND_CREATE",
                },
            }
        )
    if config.genie_space_ids:
        print(f"Ensuring app Genie space grants for {len(config.genie_space_ids)} space(s)...")
        for index, space_id in enumerate(config.genie_space_ids, start=1):
            resource_name = f"genie-space-{index}"
            updated_resources.append(
                {
                    "name": resource_name,
                    "genie_space": {
                        "name": resource_name,
                        "space_id": space_id,
                        "permission": "CAN_RUN",
                    },
                }
            )

    if volume_full_name:
        print(
            "Ensuring app UC volume resource grants "
            f"WRITE_VOLUME on {volume_full_name}..."
        )
        updated_resources.append(
            {
                "name": "trace-volume-write",
                "uc_securable": {
                    "securable_full_name": volume_full_name,
                    "securable_type": "VOLUME",
                    "permission": "WRITE_VOLUME",
                },
            }
        )

    workspace_client.api_client.do(
        "PATCH",
        f"/api/2.0/apps/{config.app_name}",
        body={"name": config.app_name, "resources": updated_resources},
    )


def _is_placeholder_space_id(space_id: str) -> bool:
    """True for unreplaced bundle template placeholders like '<...>' or empty."""
    s = (space_id or "").strip()
    return not s or (s.startswith("<") and s.endswith(">"))


def grant_genie_space_manage(config: PermissionGrantConfig, sp_id: str, workspace_client) -> list[dict]:
    """Grant the app service principal CAN_MANAGE on each configured Genie space.

    The app's `genie_space` resource only confers CAN_RUN, which is enough to
    query a space but not to edit it. AgentRx's space-editing tools (benchmark /
    sample questions, column visibility) require CAN_MANAGE, so grant it directly
    via the permissions API. Idempotent.

    Each grant is verified by reading the ACL back, because a swallowed PATCH
    failure (e.g. the deploying identity lacks rights, or a stale space id) would
    otherwise let the deploy report success while the editing tools 403 at
    runtime. Returns a per-space result list and prints a prominent summary so a
    silent failure is visible in the shared-infra job log.
    """
    space_ids = [s for s in (config.genie_space_ids or []) if not _is_placeholder_space_id(s)]
    skipped = [s for s in (config.genie_space_ids or []) if _is_placeholder_space_id(s)]
    if skipped:
        print(
            f"  Note: skipping {len(skipped)} unconfigured/placeholder Genie space id(s); "
            "AgentRx space-editing is disabled for those."
        )
    if not space_ids:
        print("  No Genie spaces configured for CAN_MANAGE grant (AgentRx editing disabled).")
        return []

    results: list[dict] = []
    for space_id in space_ids:
        result = {"space_id": space_id, "granted": False, "error": None}
        try:
            workspace_client.api_client.do(
                "PATCH",
                f"/api/2.0/permissions/genie/{space_id}",
                body={
                    "access_control_list": [
                        {"service_principal_name": sp_id, "permission_level": "CAN_MANAGE"}
                    ]
                },
            )
            # Verify the grant actually took effect.
            acls = (workspace_client.api_client.do(
                "GET", f"/api/2.0/permissions/genie/{space_id}"
            ) or {}).get("access_control_list", [])
            levels = [
                p.get("permission_level")
                for entry in acls
                if entry.get("service_principal_name") == sp_id
                for p in entry.get("all_permissions", [])
            ]
            if "CAN_MANAGE" in levels:
                result["granted"] = True
                print(f"  ✅ Verified CAN_MANAGE on Genie space {space_id} for {sp_id}.")
            else:
                result["error"] = f"grant not present after PATCH (levels={levels})"
                print(f"  ❌ CAN_MANAGE NOT confirmed on Genie space {space_id} (levels={levels}).")
        except Exception as e:  # noqa: BLE001
            result["error"] = str(e)
            print(f"  ❌ Failed to grant CAN_MANAGE on Genie space {space_id}: {e}")
        results.append(result)

    failed = [r for r in results if not r["granted"]]
    if failed:
        print(
            f"\n  ⚠️  AGENTRX GENIE GRANT INCOMPLETE: {len(failed)}/{len(results)} space(s) "
            f"did NOT get CAN_MANAGE: {[r['space_id'] for r in failed]}. "
            "AgentRx space-editing tools will return 403 for those spaces until this is "
            "resolved (ensure the deploying identity has CAN_MANAGE / the space ids are valid)."
        )
    return results


def sync_app_resource_permissions(config: PermissionGrantConfig, workspace_client) -> None:
    from databricks.sdk.service import apps as apps_service

    if not config.app_name:
        print(
            "App resource grants require --app-name; "
            "skipping database, volume, and Genie resource sync."
        )
        return

    if config.project and config.branch and not _sdk_supports_postgres_app_resource(apps_service):
        _sync_app_resource_permissions_raw(config, workspace_client)
        return

    current_app = workspace_client.apps.get(config.app_name)
    resources = list(current_app.resources or [])
    updated_resources: list[apps_service.AppResource] = []
    volume_full_name = None
    if config.catalog_name and config.schema_name and config.volume_name:
        volume_full_name = f"{config.catalog_name}.{config.schema_name}.{config.volume_name}"

    for resource in resources:
        if resource.name == "database" and (config.instance_name or (config.project and config.branch)):
            continue
        if resource.name == "postgres" and config.project and config.branch:
            continue
        if config.genie_space_ids and getattr(resource, "genie_space", None) is not None:
            continue
        uc_securable = getattr(resource, "uc_securable", None)
        if (
            volume_full_name
            and uc_securable is not None
            and getattr(uc_securable, "securable_type", None)
            == apps_service.AppResourceUcSecurableUcSecurableType.VOLUME
            and getattr(uc_securable, "securable_full_name", None) == volume_full_name
        ):
            continue
        updated_resources.append(resource)

    if config.instance_name:
        print(
            "Ensuring app database resource grants "
            f"{config.database_name}@{config.instance_name} with CAN_CONNECT_AND_CREATE..."
        )
        updated_resources.append(
            apps_service.AppResource(
                name="database",
                database=apps_service.AppResourceDatabase(
                    instance_name=config.instance_name,
                    database_name=config.database_name or DEFAULT_DATABASE_NAME,
                    permission=apps_service.AppResourceDatabaseDatabasePermission.CAN_CONNECT_AND_CREATE,
                ),
            )
        )

    postgres_branch_name = _postgres_branch_name(config)
    postgres_database_resource_name = _require_postgres_database_resource_name(config, workspace_client)
    if postgres_branch_name and postgres_database_resource_name:
        print(
            "Ensuring app autoscaling Lakebase resource grants "
            f"CAN_CONNECT_AND_CREATE on {postgres_database_resource_name}..."
        )
        updated_resources.append(
            apps_service.AppResource(
                name="postgres",
                postgres=apps_service.AppResourcePostgres(
                    branch=postgres_branch_name,
                    database=postgres_database_resource_name,
                    permission=apps_service.AppResourcePostgresPostgresPermission.CAN_CONNECT_AND_CREATE,
                ),
            )
        )
    if config.genie_space_ids:
        print(f"Ensuring app Genie space grants for {len(config.genie_space_ids)} space(s)...")
        for index, space_id in enumerate(config.genie_space_ids, start=1):
            resource_name = f"genie-space-{index}"
            updated_resources.append(
                apps_service.AppResource(
                    name=resource_name,
                    genie_space=apps_service.AppResourceGenieSpace(
                        name=resource_name,
                        space_id=space_id,
                        permission=apps_service.AppResourceGenieSpaceGenieSpacePermission.CAN_RUN,
                    ),
                )
            )

    if volume_full_name:
        print(
            "Ensuring app UC volume resource grants "
            f"WRITE_VOLUME on {volume_full_name}..."
        )
        updated_resources.append(
            apps_service.AppResource(
                name="trace-volume-write",
                uc_securable=apps_service.AppResourceUcSecurable(
                    securable_full_name=volume_full_name,
                    securable_type=apps_service.AppResourceUcSecurableUcSecurableType.VOLUME,
                    permission=apps_service.AppResourceUcSecurableUcSecurablePermission.WRITE_VOLUME,
                ),
            )
        )

    workspace_client.apps.update(
        config.app_name,
        apps_service.App(name=config.app_name, resources=updated_resources),
    )


def apply_permission_grants(
    config: PermissionGrantConfig,
    *,
    workspace_client=None,
) -> str:
    from databricks_ai_bridge.lakebase import (
        LakebaseClient,
        SchemaPrivilege,
        SequencePrivilege,
        TablePrivilege,
    )
    from databricks.sdk.service import catalog as uc_catalog

    config = hydrate_config_from_bundle(config)
    validate_permission_config(config)

    workspace_client = workspace_client or build_workspace_client(config.profile)
    client = LakebaseClient(
        instance_name=config.instance_name or None,
        project=config.project or None,
        branch=config.branch or None,
        workspace_client=workspace_client,
    )
    sp_id = config.sp_client_id
    if config.app_name:
        sp_id = resolve_app_sp_client_id(
            config.app_name,
            profile=config.profile,
            workspace_client=workspace_client,
        )
        print(
            f"Resolved app '{config.app_name}' to service principal client ID: {sp_id}"
        )

    if not sp_id:
        raise RuntimeError("Unable to resolve service principal client ID.")

    if config.instance_name:
        print(f"Using provisioned instance: {config.instance_name}")
    else:
        print(f"Using autoscaling project: {config.project}, branch: {config.branch}")
    print(f"Memory type: {config.memory_type}")
    if config.catalog_name and config.schema_name:
        print(f"Unity Catalog target: {config.catalog_name}.{config.schema_name}")
    else:
        print("Unity Catalog target: not provided, skipping UC grants")
    if config.volume_name:
        print(f"Unity Catalog volume target: {config.volume_name}")
    else:
        print("Unity Catalog volume target: not provided, skipping volume grants")
    if config.data_catalog_name and config.data_schema_name:
        print(
            f"Source data Unity Catalog target: "
            f"{config.data_catalog_name}.{config.data_schema_name}"
        )
    data_uc_targets = resolve_data_uc_targets(config)
    if data_uc_targets:
        targets_text = ", ".join(
            f"{target.catalog_name}.{target.schema_name}" for target in data_uc_targets
        )
        print(f"Source data Unity Catalog targets: {targets_text}")

    schema_tables: dict[str, list[str]] = {
        "public": MEMORY_TYPE_TABLES[config.memory_type],
        **SHARED_SCHEMAS,
    }

    print(f"Creating role for SP {sp_id}...")
    use_direct_grants = False
    try:
        client.create_role(sp_id, "SERVICE_PRINCIPAL")
        print("  Role created.")
    except Exception as e:
        error_text = str(e).lower()
        if "already exists" in error_text:
            print("  Role already exists, skipping.")
        elif (
            "insufficient privilege" in error_text
            or "permission denied to create role" in error_text
            or "can manage" in error_text
        ):
            print(
                "  Warning: unable to create role with the current identity. "
                "Continuing and assuming the service principal role already exists."
            )
        elif "identity" in error_text and "not found" in error_text:
            print(
                "  Warning: service principal could not be resolved via workspace "
                "identity lookup. Falling back to direct SQL grants."
            )
            use_direct_grants = True
        else:
            raise

    schema_privileges = [SchemaPrivilege.USAGE, SchemaPrivilege.CREATE]
    table_privileges = [
        TablePrivilege.SELECT,
        TablePrivilege.INSERT,
        TablePrivilege.UPDATE,
        TablePrivilege.DELETE,
    ]

    for schema, tables in schema_tables.items():
        print(f"Granting schema privileges on '{schema}'...")
        _execute_grant(
            use_direct_grants,
            sdk_fn=lambda s=schema: client.grant_schema(
                grantee=sp_id, schemas=[s], privileges=schema_privileges
            ),
            direct_fn=lambda s=schema: grant_schema_direct(
                client, sp_id, s, schema_privileges
            ),
            label="schema",
        )

        qualified_tables = [f"{schema}.{t}" for t in tables]
        print(f"  Granting table privileges on {qualified_tables}...")
        _execute_grant(
            use_direct_grants,
            sdk_fn=lambda qt=qualified_tables: client.grant_table(
                grantee=sp_id, tables=qt, privileges=table_privileges
            ),
            direct_fn=lambda s=schema, t=tables: grant_tables_direct(
                client, sp_id, s, t, table_privileges
            ),
            label="table",
        )

    sequence_schemas = set(SHARED_SEQUENCE_SCHEMAS)
    sequence_schemas.update(MEMORY_TYPE_SEQUENCE_SCHEMAS.get(config.memory_type, set()))
    if sequence_schemas:
        sequence_privileges = [
            SequencePrivilege.USAGE,
            SequencePrivilege.SELECT,
            SequencePrivilege.UPDATE,
        ]
        for schema in sorted(sequence_schemas):
            print(f"Granting sequence privileges on '{schema}' schema...")
            _execute_grant(
                use_direct_grants,
                sdk_fn=lambda s=schema: client.grant_all_sequences_in_schema(
                    grantee=sp_id, schemas=[s], privileges=sequence_privileges
                ),
                direct_fn=lambda s=schema: grant_sequences_direct(
                    client, sp_id, s, sequence_privileges
                ),
                label="sequence",
            )

    if config.catalog_name and config.schema_name:
        grant_uc_permissions(
            workspace_client=workspace_client,
            grantee=sp_id,
            catalog_name=config.catalog_name,
            schema_name=config.schema_name,
            uc_catalog=uc_catalog,
            schema_privileges=[
                uc_catalog.Privilege.USE_SCHEMA,
                uc_catalog.Privilege.SELECT,
                uc_catalog.Privilege.EXECUTE,
            ],
            warehouse_id=config.warehouse_id,
        )
    for data_target in data_uc_targets:
        if (
            data_target.catalog_name == config.catalog_name
            and data_target.schema_name == config.schema_name
        ):
            print("Source data Unity Catalog target matches primary target, skipping.")
            continue
        grant_uc_permissions(
            workspace_client=workspace_client,
            grantee=sp_id,
            catalog_name=data_target.catalog_name,
            schema_name=data_target.schema_name,
            uc_catalog=uc_catalog,
            schema_privileges=[
                uc_catalog.Privilege.USE_SCHEMA,
                uc_catalog.Privilege.SELECT,
            ],
            warehouse_id=config.warehouse_id,
        )

    sync_app_resource_permissions(config, workspace_client)
    grant_genie_space_manage(config, sp_id, workspace_client)

    print(
        "\nPermission grants complete. If some grants failed because tables don't "
        "exist yet, that's expected on a fresh branch — they'll be created on first "
        "agent usage. Re-run this script after the first run to grant remaining permissions."
    )
    return sp_id


def _build_config_from_args(args) -> PermissionGrantConfig:
    return PermissionGrantConfig(
        memory_type=args.memory_type,
        sp_client_id=args.sp_client_id,
        app_name=args.app_name,
        profile=args.profile,
        target=args.target,
        bundle_config_path=args.bundle_config_path,
        catalog_name=args.catalog_name,
        schema_name=args.schema_name,
        data_catalog_name=args.data_catalog_name,
        data_schema_name=args.data_schema_name,
        data_catalog_schemas=args.data_catalog_schemas,
        volume_name=args.volume_name,
        instance_name=args.instance_name,
        database_name=args.database_name,
        genie_space_ids=_parse_csv(args.genie_space_ids),
        project=args.project,
        branch=args.branch,
        warehouse_id=args.warehouse_id,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Grant Lakebase and Unity Catalog permissions to an app service principal."
    )
    parser.add_argument(
        "sp_client_id",
        nargs="?",
        help="Service principal client ID (UUID). Get it via: "
        "databricks apps get <app-name> --output json "
        "| jq -r '.service_principal_client_id'",
    )
    parser.add_argument(
        "--app-name",
        help="Databricks App name. If provided, the script resolves the app "
        "service_principal_client_id automatically.",
    )
    parser.add_argument(
        "--profile",
        help="Databricks profile to use for app lookup, Lakebase, and Unity Catalog grants. "
        "If omitted, the script uses ambient Databricks auth.",
    )
    parser.add_argument(
        "--target",
        help="Optional bundle target name to resolve defaults from databricks.yml.",
    )
    parser.add_argument(
        "--bundle-config-path",
        default=str(DEFAULT_BUNDLE_CONFIG_PATH),
        help="Path to databricks.yml used for resolving default values.",
    )
    parser.add_argument(
        "--catalog-name",
        help="Unity Catalog catalog name for app data access.",
    )
    parser.add_argument(
        "--schema-name",
        help="Unity Catalog schema name for app data access.",
    )
    parser.add_argument(
        "--data-catalog-name",
        help="Optional second Unity Catalog catalog name for source data access.",
    )
    parser.add_argument(
        "--data-schema-name",
        help="Optional second Unity Catalog schema name for source data access.",
    )
    parser.add_argument(
        "--data-catalog-schemas",
        help=(
            "Optional source data access mapping. Format: "
            "catalog_1:schema1, schema2; catalog_2:schema_a, schema_b."
        ),
    )
    parser.add_argument(
        "--volume-name",
        help="Optional Unity Catalog volume name for app trace artifact access.",
    )
    parser.add_argument(
        "--memory-type",
        required=True,
        choices=list(MEMORY_TYPE_TABLES.keys()),
        help="Memory type to grant permissions for",
    )
    parser.add_argument(
        "--instance-name",
        help="Lakebase instance name for provisioned instances.",
    )
    parser.add_argument(
        "--database-name",
        help=f"Lakebase database name for app resource grants (default: {DEFAULT_DATABASE_NAME}).",
    )
    parser.add_argument(
        "--genie-space-ids",
        help="Comma-separated Genie space IDs for app CAN_RUN grants.",
    )
    parser.add_argument(
        "--project",
        help="Lakebase autoscaling project name.",
    )
    parser.add_argument(
        "--branch",
        help="Lakebase autoscaling branch name.",
    )
    parser.add_argument(
        "--warehouse-id",
        help="SQL warehouse ID used for Unity Catalog SQL GRANT fallback.",
    )
    args = parser.parse_args()

    try:
        apply_permission_grants(_build_config_from_args(args))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
