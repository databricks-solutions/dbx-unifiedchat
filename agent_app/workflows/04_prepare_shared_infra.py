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
import sys
from pathlib import Path
from typing import Optional

import mlflow
from databricks.sdk import WorkspaceClient
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

ensure_experiment(
    w,
    target=target,
    experiment_id=params["experiment_id"] or None,
    catalog_name=catalog_name,
    schema_name=schema_name,
    volume_name=volume_name,
)

print("\nShared infrastructure preparation complete.")
