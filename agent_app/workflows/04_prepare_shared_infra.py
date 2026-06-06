# Databricks notebook source
# DBTITLE 1,Prepare Shared App Infrastructure
"""
Prepare shared runtime infrastructure for the Databricks App deployment.

This notebook is intentionally app-centric:
- it assumes the bundle deploy has already created or updated the app resource
- it ensures Lakebase and the MLflow experiment exist
- it bootstraps Lakebase and Unity Catalog permissions for the app SP
- it registers Unity Catalog functions for metadata retrieval
"""

# COMMAND ----------

# MAGIC %pip install -q databricks-sdk==0.102.0 databricks-ai-bridge[memory]==0.17.0

# COMMAND ----------

import os
import shutil
import sys
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

import mlflow
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import catalog as uc_catalog
from databricks.sdk.service.postgres import Branch, BranchSpec, Project, ProjectSpec


def _notebook_dir() -> Path:
    if "__file__" in globals():
        return Path(__file__).resolve().parent
    return Path(os.getcwd()).resolve()


APP_DIR = _notebook_dir().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
TOOLS_DIR = APP_DIR / "agent_server" / "multi_agent" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from uc_functions import register_uc_functions

from scripts.grant_lakebase_permissions import PermissionGrantConfig, apply_permission_grants


_WIDGET_DEFAULTS = {
    "app_name": "",
    "target": "dev",
    "catalog_name": "",
    "schema_name": "",
    "volume_name": "",
    "data_catalog_name": "",
    "data_schema_name": "",
    "data_catalog_schemas": "",
    "sql_warehouse_id": "",
    "lakebase_project": "",
    "lakebase_branch": "",
    "source_table": "",
    "experiment_id": "",
    "ltm_enabled": "false",
    "ltm_provider": "tabicl",
    "ltm_checkpoint_path": "",
    "ltm_classifier_checkpoint": "tabicl-classifier-v2-20260212.ckpt",
    "ltm_regressor_checkpoint": "tabicl-regressor-v2-20260212.ckpt",
}

for key, default in _WIDGET_DEFAULTS.items():
    dbutils.widgets.text(key, default)

params = {key: dbutils.widgets.get(key).strip() for key in _WIDGET_DEFAULTS}

app_name = params["app_name"] or f"dbx-unifiedchat-app-{params['target'] or 'dev'}"
target = params["target"] or "dev"
catalog_name = params["catalog_name"] or None
schema_name = params["schema_name"] or None
volume_name = params["volume_name"] or None
data_catalog_name = params["data_catalog_name"] or None
data_schema_name = params["data_schema_name"] or None
data_catalog_schemas = params["data_catalog_schemas"] or None
sql_warehouse_id = params["sql_warehouse_id"] or None
lakebase_project = params["lakebase_project"] or None
lakebase_branch = params["lakebase_branch"] or None
source_table = params["source_table"] or "enriched_genie_docs_chunks"
ltm_enabled = params["ltm_enabled"].lower() == "true"
ltm_provider = params["ltm_provider"] or "tabicl"
ltm_checkpoint_path = params["ltm_checkpoint_path"] or None
ltm_classifier_checkpoint = params["ltm_classifier_checkpoint"] or "tabicl-classifier-v2-20260212.ckpt"
ltm_regressor_checkpoint = params["ltm_regressor_checkpoint"] or "tabicl-regressor-v2-20260212.ckpt"

_TABICL_HF_REPO_ID = "jingang/TabICL"
_TABICL_HF_BASE_URL = f"https://huggingface.co/{_TABICL_HF_REPO_ID}/resolve/main"


def ensure_lakebase_branch(
    workspace_client: WorkspaceClient,
    *,
    project: Optional[str],
    branch: Optional[str],
):
    if not project or not branch:
        print("\nNo Lakebase autoscaling project/branch configured; skipping branch validation.")
        return None

    project_name = f"projects/{project}"
    branch_name = f"{project_name}/branches/{branch}"
    print("\nEnsuring Lakebase autoscaling project/branch exist...")
    print(f"  project: {project}")
    print(f"  branch: {branch}")

    project_created = False
    try:
        workspace_client.postgres.get_project(name=project_name)
        print(f"  ✓ Project '{project_name}' already exists")
    except Exception as exc:
        print(f"  Project '{project_name}' not found. Creating it now...")
        try:
            workspace_client.postgres.create_project(
                project=Project(spec=ProjectSpec(display_name=project)),
                project_id=project,
            ).wait()
            project_created = True
            print(f"  ✓ Project '{project_name}' created")
        except Exception as create_exc:
            raise RuntimeError(
                f"Lakebase autoscaling project '{project_name}' could not be created."
            ) from create_exc

    if branch == "production" and project_created:
        print(f"  ✓ Branch '{branch_name}' is available (auto-created with project)")
    else:
        try:
            workspace_client.postgres.get_branch(name=branch_name)
            print(f"  ✓ Branch '{branch_name}' already exists")
        except Exception as exc:
            if branch == "production":
                raise RuntimeError(
                    f"Lakebase autoscaling branch '{branch_name}' could not be resolved."
                ) from exc

            print(f"  Branch '{branch_name}' not found. Creating it now...")
            try:
                workspace_client.postgres.create_branch(
                    parent=project_name,
                    branch=Branch(
                        spec=BranchSpec(
                            source_branch=f"{project_name}/branches/production",
                            no_expiry=True,
                        )
                    ),
                    branch_id=branch,
                ).wait()
                print(f"  ✓ Branch '{branch_name}' created")
            except Exception as create_exc:
                raise RuntimeError(
                    f"Lakebase autoscaling branch '{branch_name}' could not be created."
                ) from create_exc

    try:
        endpoints = list(workspace_client.postgres.list_endpoints(parent=branch_name))
    except Exception as exc:
        raise RuntimeError(
            f"Lakebase autoscaling endpoints for '{branch_name}' could not be resolved."
        ) from exc

    if not endpoints:
        print(f"  ✓ Branch '{branch_name}' exists (no endpoints reported yet)")
        return {"branch": branch_name, "host": None}

    first_endpoint = endpoints[0]
    endpoint_name = getattr(first_endpoint, "name", None) or "<unknown>"
    endpoint_status = getattr(first_endpoint, "status", None)
    endpoint_hosts = getattr(endpoint_status, "hosts", None)
    host = getattr(endpoint_hosts, "host", None) if endpoint_hosts else None
    print(f"  ✓ Branch '{branch_name}' is available via endpoint {endpoint_name}")
    if host:
        print(f"  host: {host}")
    return {"branch": branch_name, "host": host}


def ensure_experiment(
    workspace_client: WorkspaceClient,
    *,
    target: str,
    experiment_id: Optional[str],
    catalog_name: Optional[str],
    schema_name: Optional[str],
    volume_name: Optional[str],
):
    artifact_location = None
    if catalog_name and schema_name and volume_name:
        artifact_location = f"dbfs:/Volumes/{catalog_name}/{schema_name}/{volume_name}"

    if experiment_id:
        experiment = mlflow.get_experiment(experiment_id)
        if experiment is not None:
            print(
                "\nResolved MLflow experiment: "
                f"{experiment.name} ({experiment.experiment_id})"
            )
            if getattr(experiment, "artifact_location", None):
                print(f"  artifact_location: {experiment.artifact_location}")
            return experiment
        print(
            "\nConfigured MLflow experiment "
            f"'{experiment_id}' could not be resolved. Creating a fallback experiment."
        )

    current_user = workspace_client.current_user.me()
    user_name = getattr(current_user, "user_name", None)
    if not user_name:
        raise RuntimeError("Unable to resolve current workspace user for experiment creation.")

    experiment_name = f"/Users/{user_name}/multi-agent-genie-{target}"
    experiment = mlflow.get_experiment_by_name(experiment_name)
    if experiment is None:
        create_kwargs = {}
        if artifact_location:
            create_kwargs["artifact_location"] = artifact_location
        created_id = mlflow.create_experiment(experiment_name, **create_kwargs)
        experiment = mlflow.get_experiment(created_id)
        print(
            "\nCreated MLflow experiment: "
            f"{experiment.name} ({experiment.experiment_id})"
        )
        if getattr(experiment, "artifact_location", None):
            print(f"  artifact_location: {experiment.artifact_location}")
    else:
        print(
            "\nResolved fallback MLflow experiment: "
            f"{experiment.name} ({experiment.experiment_id})"
        )
        if getattr(experiment, "artifact_location", None):
            print(f"  artifact_location: {experiment.artifact_location}")
    return experiment


def ensure_uc_volume(
    *,
    catalog_name: Optional[str],
    schema_name: Optional[str],
    volume_name: Optional[str],
) -> None:
    if not (catalog_name and schema_name and volume_name):
        return

    print("\nEnsuring Unity Catalog volume exists...")
    print(f"  volume: {catalog_name}.{schema_name}.{volume_name}")
    spark.sql(  # noqa: F821 - provided by Databricks notebook runtime
        f"CREATE VOLUME IF NOT EXISTS `{catalog_name}`.`{schema_name}`.`{volume_name}`"
    )
    print("  ✓ Volume ready")


def ensure_ltm_checkpoints(
    *,
    enabled: bool,
    provider: str,
    checkpoint_path: Optional[str],
    classifier_checkpoint: str,
    regressor_checkpoint: str,
) -> None:
    if not enabled:
        print("\nTabular LTM is disabled; skipping checkpoint staging.")
        return
    if provider != "tabicl":
        print(
            "\nTabular LTM provider is not TabICL "
            f"({provider!r}); skipping TabICLv2 checkpoint staging."
        )
        return
    if not checkpoint_path:
        raise RuntimeError(
            "ltm_enabled=true requires ltm_checkpoint_path so checkpoints can be staged."
        )

    target_dir = Path(checkpoint_path)
    print("\nEnsuring TabICLv2 checkpoints are staged...")
    print(f"  target_dir: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)

    for checkpoint in (classifier_checkpoint, regressor_checkpoint):
        target_file = target_dir / checkpoint
        if target_file.exists() and target_file.stat().st_size > 0:
            print(f"  ✓ {checkpoint} already exists ({target_file.stat().st_size} bytes)")
            continue

        download_url = f"{_TABICL_HF_BASE_URL}/{checkpoint}"
        tmp_file = target_file.with_suffix(target_file.suffix + ".tmp")
        print(f"  Downloading {checkpoint} from {_TABICL_HF_REPO_ID}...")
        try:
            with urlopen(download_url, timeout=120) as response, tmp_file.open("wb") as out:
                shutil.copyfileobj(response, out)
            tmp_file.replace(target_file)
        except Exception as exc:
            if tmp_file.exists():
                tmp_file.unlink()
            raise RuntimeError(
                f"Failed to download TabICLv2 checkpoint '{checkpoint}' to "
                f"'{target_file}'. Ensure the prep job has outbound internet or "
                "pre-stage the checkpoint in the configured UC Volume."
            ) from exc

        print(f"  ✓ staged {checkpoint} ({target_file.stat().st_size} bytes)")


def grant_ltm_volume_read_access(
    workspace_client: WorkspaceClient,
    *,
    app_name: str,
    sp_client_id: str,
    catalog_name: Optional[str],
    schema_name: Optional[str],
    volume_name: Optional[str],
    enabled: bool,
) -> None:
    if not enabled:
        return
    if not (catalog_name and schema_name and volume_name):
        raise RuntimeError(
            "ltm_enabled=true requires catalog_name, schema_name, and volume_name "
            "to grant the app read access to the checkpoint volume."
        )

    volume_full_name = f"{catalog_name}.{schema_name}.{volume_name}"
    print("\nGranting app read access to TabICLv2 checkpoint volume...")
    print(f"  volume: {volume_full_name}")

    workspace_client.grants.update(
        securable_type=uc_catalog.SecurableType.CATALOG,
        full_name=catalog_name,
        changes=[
            uc_catalog.PermissionsChange(
                add=[uc_catalog.Privilege.USE_CATALOG],
                principal=sp_client_id,
            )
        ],
    )
    workspace_client.grants.update(
        securable_type=uc_catalog.SecurableType.SCHEMA,
        full_name=f"{catalog_name}.{schema_name}",
        changes=[
            uc_catalog.PermissionsChange(
                add=[uc_catalog.Privilege.USE_SCHEMA],
                principal=sp_client_id,
            )
        ],
    )
    workspace_client.grants.update(
        securable_type=uc_catalog.SecurableType.VOLUME,
        full_name=volume_full_name,
        changes=[
            uc_catalog.PermissionsChange(
                add=[uc_catalog.Privilege.READ_VOLUME],
                principal=sp_client_id,
            )
        ],
    )

    # Databricks Apps also track UC dependencies as app resources. Upsert a
    # read-only volume resource so the app does not need WRITE_VOLUME at runtime.
    resources_payload = workspace_client.api_client.do("GET", f"/api/2.0/apps/{app_name}")
    resources = list((resources_payload or {}).get("resources") or [])
    updated_resources = [
        resource
        for resource in resources
        if resource.get("name") != "ltm-volume-read"
    ]
    updated_resources.append(
        {
            "name": "ltm-volume-read",
            "uc_securable": {
                "securable_full_name": volume_full_name,
                "securable_type": "VOLUME",
                "permission": "READ_VOLUME",
            },
        }
    )
    workspace_client.api_client.do(
        "PATCH",
        f"/api/2.0/apps/{app_name}",
        body={"name": app_name, "resources": updated_resources},
    )
    print("  ✓ READ_VOLUME granted for app runtime")


print("=" * 80)
print("PREPARE SHARED INFRA")
print("=" * 80)
for key, value in params.items():
    print(f"{key}: {value or '<unset>'}")

w = WorkspaceClient()
app = w.apps.get(app_name)
sp_client_id = getattr(app, "service_principal_client_id", None)
if not sp_client_id:
    raise RuntimeError(
        f"App '{app_name}' exists but no service principal client ID is available yet."
    )

print(f"\nResolved app service principal: {sp_client_id}")

ensure_lakebase_branch(
    w,
    project=lakebase_project,
    branch=lakebase_branch,
)

for memory_type in ("langgraph-short-term", "langgraph-long-term"):
    print(f"\nBootstrapping {memory_type} permissions...")
    apply_permission_grants(
        PermissionGrantConfig(
            memory_type=memory_type,
            app_name=app_name,
            target=target,
            catalog_name=catalog_name,
            schema_name=schema_name,
            data_catalog_name=data_catalog_name,
            data_schema_name=data_schema_name,
            data_catalog_schemas=data_catalog_schemas,
            warehouse_id=sql_warehouse_id,
            project=lakebase_project,
            branch=lakebase_branch,
            bundle_config_path=str(APP_DIR / "databricks.yml"),
        ),
        workspace_client=w,
    )

if catalog_name and schema_name and source_table:
    source_table_fqn = f"{catalog_name}.{schema_name}.{source_table}"
    print("\nRegistering Unity Catalog functions...")
    register_uc_functions(catalog_name, schema_name, source_table_fqn)
else:
    print(
        "\nSkipping UC function registration because one or more required values are unset: "
        f"catalog_name={catalog_name!r}, schema_name={schema_name!r}, source_table={source_table!r}"
    )

ensure_uc_volume(
    catalog_name=catalog_name,
    schema_name=schema_name,
    volume_name=volume_name,
)

ensure_ltm_checkpoints(
    enabled=ltm_enabled,
    provider=ltm_provider,
    checkpoint_path=ltm_checkpoint_path,
    classifier_checkpoint=ltm_classifier_checkpoint,
    regressor_checkpoint=ltm_regressor_checkpoint,
)

grant_ltm_volume_read_access(
    w,
    app_name=app_name,
    sp_client_id=sp_client_id,
    catalog_name=catalog_name,
    schema_name=schema_name,
    volume_name=volume_name,
    enabled=ltm_enabled,
)

ensure_experiment(
    w,
    target=target,
    experiment_id=params["experiment_id"] or None,
    catalog_name=catalog_name,
    schema_name=schema_name,
    volume_name=volume_name,
)

print("\nShared infrastructure preparation complete.")
