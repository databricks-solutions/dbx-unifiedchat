# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# dependencies = [
#   "pyyaml",
#   "databricks-sdk",
#   "databricks-ai-bridge[memory]",
# ]
# ///
# MAGIC %md
# MAGIC # Multi-Agent Genie Deploy
# MAGIC
# MAGIC Use this notebook as a Databricks-native operator companion for the
# MAGIC canonical `agent_app/scripts/deploy.sh` flow.
# MAGIC
# MAGIC What this notebook does:
# MAGIC - resolves target-specific settings from `agent_app/databricks.yml`
# MAGIC - checks workspace auth and current app state
# MAGIC - runs the canonical `./scripts/deploy.sh ... --ci` flow automatically
# MAGIC   when `deployment_context=ci`
# MAGIC - prints the exact `./scripts/deploy.sh ...` command for interactive
# MAGIC   web terminal or local terminal use
# MAGIC - verifies the deployed app surface after deployment
# MAGIC
# MAGIC What it does not do:
# MAGIC - it does not replace `deploy.sh` for local or CI automation

# COMMAND ----------

# MAGIC %pip install pyyaml=6.0.2 databricks-sdk==0.102.0 databricks-ai-bridge[memory]==0.17.0
# MAGIC %restart_python

# COMMAND ----------

import importlib.util
import os
import sys
from pathlib import Path


def _widget(name: str, default: str, *, choices: list[str] | None = None) -> str:
    if "dbutils" not in globals():
        return default
    try:
        if choices:
            dbutils.widgets.dropdown(name, default, choices)
        else:
            dbutils.widgets.text(name, default)
    except Exception:
        pass
    return dbutils.widgets.get(name)


def _remove_widget(name: str) -> None:
    if "dbutils" not in globals():
        return
    try:
        dbutils.widgets.remove(name)
    except Exception:
        pass


initial_project_dir = Path(os.getcwd()).expanduser().resolve().parent
for legacy_widget in ("deploy_mode", "run_after", "sync_first", "job_to_run"):
    _remove_widget(legacy_widget)

project_dir_value = _widget("project_dir", str(initial_project_dir))
project_dir = Path(project_dir_value).expanduser().resolve()
target = _widget("target", "dev", choices=["dev", "prod"])
profile = _widget("profile", "")
deployment_context = _widget(
    "deployment_context",
    default="web_terminal",
    choices=["web_terminal", "ci", "local"],
)
skip_bootstrap_value = _widget(
    "skip_bootstrap",
    default="auto",
    choices=["auto", "false", "true"],
)
skip_bootstrap = None
if skip_bootstrap_value != "auto":
    skip_bootstrap = skip_bootstrap_value == "true"
run_job = _widget(
    "run_job",
    default="full",
    choices=["full", "prep", "meta", "infra", "val"],
).strip() or None
start_app = _widget("start_app", "false", choices=["false", "true"]) == "true"
sync_workspace = (
    _widget("sync_workspace", "false", choices=["false", "true"]) == "true"
)

if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

lib_path = project_dir / "scripts" / "notebook_deploy_lib.py"
spec = importlib.util.spec_from_file_location("notebook_deploy_lib", lib_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load notebook deploy library from {lib_path}")

module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

NotebookDeployConfig = module.NotebookDeployConfig
collect_preflight_report = module.collect_preflight_report
print_preflight_report = module.print_preflight_report
print_terminal_handoff = module.print_terminal_handoff
run_deploy_command = module.run_deploy_command
verify_deployment = module.verify_deployment

config = NotebookDeployConfig(
    project_dir=project_dir,
    target=target,
    profile=profile or None,
    deployment_context=deployment_context,
    skip_bootstrap=skip_bootstrap,
    start_app=start_app,
    sync_workspace=sync_workspace,
    run_job=run_job,
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Preflight
# MAGIC
# MAGIC Run this cell first to resolve target-scoped settings, verify workspace auth,
# MAGIC and inspect whether the app already exists.

# COMMAND ----------

preflight = collect_preflight_report(config)
print_preflight_report(config, preflight)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Deploy
# MAGIC
# MAGIC This cell reflects the current widget values:
# MAGIC - `deployment_context=ci` runs `deploy.sh` automatically inside the notebook
# MAGIC - `deployment_context=web_terminal` prints a web-terminal command with
# MAGIC   `--skip-bootstrap` by default
# MAGIC - `deployment_context=local` prints the local terminal command
# MAGIC
# MAGIC Interactive contexts also print a separate destroy handoff with warnings
# MAGIC and usage.

# COMMAND ----------

if deployment_context == "ci":
    run_deploy_command(config)
else:
    print_terminal_handoff(config)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Verification
# MAGIC
# MAGIC Confirm the app exists and review any remaining manual follow-up.

# COMMAND ----------

verify_deployment(config)
