from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml
from databricks.sdk import WorkspaceClient

from scripts.grant_lakebase_permissions import (
    DEFAULT_DATABASE_NAME,
    PermissionGrantConfig,
    apply_permission_grants,
    hydrate_config_from_bundle,
)


MANUAL_GRANT_NOTES = (
    "",
)


@dataclass
class NotebookDeployConfig:
    project_dir: Path
    target: str = "dev"
    profile: str | None = None
    deployment_context: str = "web_terminal"
    skip_bootstrap: bool | None = None
    start_app: bool = False
    sync_workspace: bool = False
    run_job: str | None = "full"
    bundle_app_key: str = "dbx_unifiedchat_agent_app"

    @property
    def app_name(self) -> str:
        return resolve_app_name(
            self.project_dir,
            target=self.target,
            bundle_app_key=self.bundle_app_key,
        )


@dataclass
class PreflightReport:
    settings: dict[str, str | None]
    effective_profile: str | None
    workspace_user: str | None
    app_exists: bool
    service_principal_client_id: str | None
    source_code_path: Path | None
    warnings: list[str]


def _workspace_client(profile: str | None) -> WorkspaceClient:
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def _profile_args(profile: str | None) -> list[str]:
    return ["--profile", profile] if profile else []


def _render_command(command: list[str]) -> str:
    return shlex.join(command)


def _resolved_skip_bootstrap(config: NotebookDeployConfig) -> bool:
    if config.skip_bootstrap is not None:
        return config.skip_bootstrap
    return config.deployment_context == "web_terminal"


def _append_deployment_context_args(
    command: list[str],
    config: NotebookDeployConfig,
) -> None:
    if config.deployment_context == "ci":
        command.append("--ci")
    if _resolved_skip_bootstrap(config):
        command.append("--skip-bootstrap")


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def load_bundle_config(project_dir: Path) -> dict:
    return load_yaml(project_dir / "databricks.yml")


def load_app_resource(project_dir: Path) -> dict:
    return load_yaml(project_dir / "resources" / "app.yml")


def resolve_app_name(project_dir: Path, *, target: str, bundle_app_key: str) -> str:
    app_resource = load_app_resource(project_dir)
    app_config = ((app_resource.get("resources") or {}).get("apps") or {}).get(
        bundle_app_key
    )
    raw_name = (app_config or {}).get("name")
    if not raw_name:
        return f"dbx-unifiedchat-app-{target}"
    if isinstance(raw_name, str):
        resolved_name = raw_name.replace("${bundle.target}", target)
        if resolved_name == "${var.app_name}":
            return resolve_bundle_var(project_dir, target, "app_name") or resolved_name
        return resolved_name
    return str(raw_name)


def resolve_bundle_var(project_dir: Path, target: str, var_name: str) -> str | None:
    config = load_bundle_config(project_dir)
    variables = config.get("variables", {})
    target_variables = ((config.get("targets") or {}).get(target) or {}).get(
        "variables", {}
    )

    value = target_variables.get(var_name)
    if value is None:
        value = (variables.get(var_name) or {}).get("default")
    if value is None:
        return None
    if isinstance(value, str):
        return value.replace("${bundle.target}", target)
    return str(value)


def bundle_settings(project_dir: Path, target: str) -> dict[str, str | None]:
    resolved = hydrate_config_from_bundle(
        PermissionGrantConfig(
            memory_type="langgraph-short-term",
            target=target,
            bundle_config_path=str(project_dir / "databricks.yml"),
            database_name=DEFAULT_DATABASE_NAME,
        )
    )
    return {
        "catalog_name": resolved.catalog_name,
        "schema_name": resolved.schema_name,
        "data_catalog_name": resolved.data_catalog_name,
        "data_schema_name": resolved.data_schema_name,
        "data_catalog_schemas": resolved.data_catalog_schemas,
        "sql_warehouse_id": resolved.warehouse_id,
        "app_logo_url": resolve_bundle_var(project_dir, target, "app_logo_url"),
        "lakebase_project": resolved.project,
        "lakebase_branch": resolved.branch,
        "lakebase_instance_name": resolved.instance_name,
        "database_name": resolved.database_name,
        "genie_space_ids": ",".join(resolved.genie_space_ids or []),
        "experiment_id": resolve_bundle_var(project_dir, target, "experiment_id"),
    }


def resolve_effective_profile(project_dir: Path, target: str, profile: str | None) -> str | None:
    if profile:
        return profile
    config = load_bundle_config(project_dir)
    return (((config.get("targets") or {}).get(target) or {}).get("workspace") or {}).get("profile")


def resolve_source_code_path(
    project_dir: Path,
    *,
    bundle_app_key: str,
) -> tuple[str | None, Path | None]:
    app_resource = load_app_resource(project_dir)
    app_config = ((app_resource.get("resources") or {}).get("apps") or {}).get(
        bundle_app_key
    )
    if not app_config:
        raise RuntimeError(
            f"Unable to locate apps.{bundle_app_key} in resources/app.yml."
        )

    raw_path = app_config.get("source_code_path")
    if not raw_path:
        return None, None
    resolved = (project_dir / "resources" / raw_path).resolve()
    return str(raw_path), resolved


def get_workspace_user(profile: str | None) -> str:
    user = _workspace_client(profile).current_user.me()
    user_name = getattr(user, "user_name", None)
    if not user_name:
        raise RuntimeError("Workspace auth succeeded but current user name was empty.")
    return user_name


def get_app_info(app_name: str, profile: str | None) -> tuple[bool, str | None]:
    try:
        app = _workspace_client(profile).apps.get(app_name)
    except Exception:
        return False, None
    return True, getattr(app, "service_principal_client_id", None)


def collect_preflight_report(config: NotebookDeployConfig) -> PreflightReport:
    warnings: list[str] = []
    effective_profile = resolve_effective_profile(
        config.project_dir, config.target, config.profile
    )
    workspace_user = None
    try:
        workspace_user = get_workspace_user(effective_profile)
    except Exception as e:
        warnings.append(f"Workspace auth check failed: {e}")

    settings = bundle_settings(config.project_dir, config.target)
    app_exists, sp_client_id = get_app_info(config.app_name, effective_profile)

    raw_source_code_path = None
    source_code_path = None
    try:
        raw_source_code_path, source_code_path = resolve_source_code_path(
            config.project_dir,
            bundle_app_key=config.bundle_app_key,
        )
    except Exception as e:
        warnings.append(str(e))

    if source_code_path and not source_code_path.exists():
        warnings.append(f"Resolved source_code_path does not exist: {source_code_path}")
    if raw_source_code_path == "../":
        warnings.append(
            "App source_code_path resolves to the bundle root directory. Run bundle "
            "commands from agent_app so Databricks packages the intended bundle content."
        )

    return PreflightReport(
        settings=settings,
        effective_profile=effective_profile,
        workspace_user=workspace_user,
        app_exists=app_exists,
        service_principal_client_id=sp_client_id,
        source_code_path=source_code_path,
        warnings=warnings,
    )


def print_preflight_report(config: NotebookDeployConfig, report: PreflightReport) -> None:
    print("Notebook deploy configuration")
    print(f"  project_dir: {config.project_dir}")
    print(f"  target: {config.target}")
    print(f"  profile: {config.profile or '<workspace auth>'}")
    print(f"  effective_profile: {report.effective_profile or '<workspace auth>'}")
    print(f"  deployment_context: {config.deployment_context}")
    print(f"  skip_bootstrap: {_resolved_skip_bootstrap(config)}")
    print(f"  app_name: {config.app_name}")
    print(f"  run_job: {config.run_job or '<none>'}")
    print(f"  sync_workspace: {config.sync_workspace}")
    print(f"  start_app: {config.start_app}")
    print()

    print("Resolved bundle settings")
    for key, value in report.settings.items():
        print(f"  {key}: {value or '<unset>'}")
    print()

    print("Workspace preflight")
    print(f"  workspace_user: {report.workspace_user or '<unavailable>'}")
    print(f"  app_exists: {report.app_exists}")
    print(
        "  service_principal_client_id: "
        f"{report.service_principal_client_id or '<not available yet>'}"
    )
    print(f"  source_code_path: {report.source_code_path or '<unresolved>'}")
    if report.warnings:
        print()
        print("Warnings")
        for warning in report.warnings:
            print(f"  - {warning}")


def build_deploy_command_args(config: NotebookDeployConfig) -> list[str]:
    command = ["./scripts/deploy.sh", "--target", config.target]
    _append_deployment_context_args(command, config)
    if config.profile:
        command.extend(["--profile", config.profile])
    if config.sync_workspace:
        command.append("--sync-workspace")
    if config.run_job:
        command.extend(["--run-job", config.run_job])
    if config.start_app:
        command.append("--start-app")
    return command


def build_deploy_command(config: NotebookDeployConfig) -> str:
    command = build_deploy_command_args(config)
    return _render_command(command)


def build_destroy_command(config: NotebookDeployConfig) -> str:
    command = ["./scripts/destroy.sh", "--target", config.target]
    if config.profile:
        command.extend(["--profile", config.profile])
    return _render_command(command)


def print_terminal_handoff(config: NotebookDeployConfig) -> None:
    print("Deploy command handoff")
    print(f"  cd {shlex.quote(str(config.project_dir))}")
    print(f"  {build_deploy_command(config)}")
    print()
    print("Notes")
    print(f"  - deployment_context widget -> {config.deployment_context}")
    print(f"  - skip_bootstrap widget     -> {_resolved_skip_bootstrap(config)}")
    print(f"  - run_job widget        -> {config.run_job or '<none>'}")
    print(f"  - sync_workspace widget -> {config.sync_workspace}")
    print(f"  - start_app widget      -> {config.start_app}")
    print("  - web_terminal adds --skip-bootstrap by default")
    print("  - ci adds --ci for non-interactive CI/CD runners")
    print("  - use `meta`, `infra`, `prep`, `val`, or `full` for `run_job`")
    list_jobs_command = ["./scripts/deploy.sh", "--target", config.target]
    _append_deployment_context_args(list_jobs_command, config)
    if config.profile:
        list_jobs_command.extend(["--profile", config.profile])
    list_jobs_command.append("--list-jobs")
    print(
        "  - discover exact job keys and descriptions with: "
        f"{_render_command(list_jobs_command)}"
    )
    print()
    print("Destroy handoff")
    print("  WARNING: This removes bundle-managed resources for the selected target.")
    print("  WARNING: Review the target/profile carefully before running it.")
    print("  Usage:")
    print(f"    cd {shlex.quote(str(config.project_dir))}")
    print(f"    {build_destroy_command(config)}")
    print("  To skip the confirmation prompt only after review, add: --auto-approve")
    print()
    print("After the deploy terminal command finishes, rerun the verification cells.")


def run_deploy_command(config: NotebookDeployConfig) -> None:
    command = build_deploy_command_args(config)
    print("Executing deploy command")
    print(f"  cwd: {config.project_dir}")
    print(f"  command: {_render_command(command)}")
    print()
    subprocess.run(command, cwd=config.project_dir, check=True)
    print()
    print("Deploy command completed.")


def bootstrap_lakebase_role(
    config: NotebookDeployConfig,
    *,
    phase: str,
    fail_ok: bool,
) -> list[tuple[str, bool, str | None]]:
    settings = bundle_settings(config.project_dir, config.target)
    project = settings["lakebase_project"]
    branch = settings["lakebase_branch"]
    instance_name = settings["lakebase_instance_name"]
    if not (instance_name or (project and branch)):
        print("Skipping Lakebase bootstrap: no Lakebase connection resolved.")
        return []

    effective_profile = resolve_effective_profile(
        config.project_dir, config.target, config.profile
    )
    app_exists, _ = get_app_info(config.app_name, effective_profile)
    if not app_exists:
        print(
            f"Skipping Lakebase bootstrap ({phase}): app '{config.app_name}' does not exist yet."
        )
        return []

    if project and branch:
        print(f"Bootstrapping Lakebase role ({phase}) in project={project}, branch={branch}...")
    else:
        print(f"Bootstrapping Lakebase role ({phase}) in {instance_name}...")
    workspace_client = _workspace_client(effective_profile)
    results: list[tuple[str, bool, str | None]] = []
    for memory_type in ("langgraph-short-term", "langgraph-long-term"):
        try:
            apply_permission_grants(
                PermissionGrantConfig(
                    memory_type=memory_type,
                    app_name=config.app_name,
                    profile=effective_profile,
                    target=config.target,
                    bundle_config_path=str(config.project_dir / "databricks.yml"),
                ),
                workspace_client=workspace_client,
            )
            results.append((memory_type, True, None))
        except Exception as e:
            if fail_ok:
                print(
                    f"WARNING: Lakebase bootstrap ({phase}, {memory_type}) failed; "
                    f"continuing. {e}"
                )
                results.append((memory_type, False, str(e)))
            else:
                raise

    if results:
        print(f"✅ Lakebase role bootstrap complete ({phase})")
    print()
    return results


def print_bootstrap_results(
    phase: str,
    results: list[tuple[str, bool, str | None]],
) -> None:
    if not results:
        return
    print(f"Bootstrap summary ({phase})")
    for memory_type, success, message in results:
        status = "ok" if success else "failed"
        suffix = f" - {message}" if message else ""
        print(f"  {memory_type}: {status}{suffix}")


def verify_deployment(config: NotebookDeployConfig) -> None:
    effective_profile = resolve_effective_profile(
        config.project_dir, config.target, config.profile
    )
    app_exists, sp_client_id = get_app_info(config.app_name, effective_profile)
    print("Post-deploy verification")
    print(f"  app_exists: {app_exists}")
    print(f"  service_principal_client_id: {sp_client_id or '<not available yet>'}")
    if app_exists:
        app = _workspace_client(effective_profile).apps.get(config.app_name)
        print(f"  url: {getattr(app, 'url', None) or '<not available yet>'}")
        print(
            "  compute_status: "
            f"{getattr(app, 'compute_status', None) or '<not available yet>'}"
        )
    if MANUAL_GRANT_NOTES:
        print()
        print("Manual follow-up")
        for note in MANUAL_GRANT_NOTES:
            print(f"  - {note}")


def locate_project_dir(default: str | None = None) -> Path:
    if default:
        return Path(default).expanduser().resolve()
    return Path.cwd().resolve()

