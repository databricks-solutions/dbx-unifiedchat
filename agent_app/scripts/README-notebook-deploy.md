# Notebook Deploy

This directory includes the Databricks-native operator companion for the canonical
deployment flow:

- `deploy.sh`: canonical local / CI deployment entrypoint
- `destroy.sh`: explicit bundle teardown entrypoint
- `deploy_notebook.py`: workspace-side operator handoff
- `notebook_deploy_lib.py`: shared preflight and verification helpers

## Purpose

Use the notebook path when you want to stay inside Databricks while still using
the same deployment contract as local terminals and CI.

The notebook flow is for:

- resolving target-scoped bundle settings
- checking workspace auth and current app state
- printing the exact `./scripts/deploy.sh ...` command to run in the web terminal
- verifying the app surface after the terminal command finishes

It is intentionally not a second deployment system.

## Files

- `scripts/deploy_notebook.py`
  - repo-backed Databricks notebook source
  - provides widgets for `project_dir`, `target`, `deployment_context`, `skip_bootstrap`, `run_job`, `sync_workspace`, and `start_app`
  - organized into preflight, deploy, and verification sections
  - executes `deploy.sh` automatically when `deployment_context=ci`
  - prints both a deploy handoff and a separate destroy handoff

- `scripts/notebook_deploy_lib.py`
  - resolves bundle settings from `agent_app/databricks.yml`
  - inspects the current app deployment surface
  - renders the canonical `./scripts/deploy.sh ...` command

## Source Of Truth

- deployment settings come from `agent_app/databricks.yml`
- the notebook does not read `.env`
- `deploy.sh` remains the supported execution entrypoint
- `agent_app/app.yaml` is intentionally kept empty/commented because direct
  Databricks Apps deploy or App UI Deploy is not the recommended path for this repo

## Ways To Deploy

Use one of these entrypoints depending on where you are operating:

- local terminal
  - run `./scripts/deploy.sh ...` from `agent_app`
  - this is the normal first-run path because it bootstraps `agent_app/.venv`
- Databricks web terminal
  - use the command printed by `scripts/deploy_notebook.py`
  - use `deployment_context=web_terminal`; this includes `--skip-bootstrap` by
    default because the workspace-side handoff is for an already prepared
    terminal environment
- CI
  - use `deployment_context=ci` to run `./scripts/deploy.sh ... --ci` from the notebook
  - add `--skip-bootstrap` only when the runner was already prepared earlier in
    the job
  - `deploy.sh --ci` automatically passes `--auto-approve` to
    `databricks bundle deploy`

## Operational Jobs

The bundle exposes split operational jobs so metadata refresh, shared infra,
and validation can be run independently:

- `agent_app_metadata_refresh_job`
  - runs ETL `01` -> `02` -> `03`
  - use this when source metadata changes and you need to rebuild the retrieval surface
- `agent_app_shared_infra_job`
  - runs workflow `04`
  - use this when app-facing infra, permissions, UC functions, or experiment wiring needs to be reconciled
- `agent_app_validate_app_job`
  - runs workflow `05`
  - use this to smoke-check the deployed app surface without rerunning ETL
- `agent_app_preps_job`
  - wrapper job that runs metadata refresh and then shared infra setup
  - this remains the `prep` alias behind `./scripts/deploy.sh --run-job prep`
- `agent_app_full_deploy_job`
  - wrapper job that runs prep and then validation
  - this remains the `full` alias behind `./scripts/deploy.sh --run-job full`

Discover available job keys through the canonical entrypoint:

```bash
./scripts/deploy.sh --target dev --list-jobs
```

You can also run any one of those bundle jobs through the canonical entrypoint:

```bash
./scripts/deploy.sh --target dev --run-job agent_app_validate_app_job
```

The `--run-job` flag accepts `prep`, `full`, or a bundle job key from `--list-jobs`.

## Prerequisites

Before running the printed `./scripts/deploy.sh ...` command in a terminal, make sure:

- the repo is available in the Databricks web terminal and you can `cd` into `agent_app`
- `python3` is installed and on `PATH`
- the Databricks CLI is installed and on `PATH`
- `uv` is installed and on `PATH` for the default bootstrap flow
- Databricks authentication is set up for the target workspace/profile you plan to use

For profile-based auth, a typical setup step is:

```bash
databricks auth login --profile prod
```

You do not need to manually create `agent_app/.venv` for the normal local deploy
path. `deploy.sh` creates or reuses the project virtual environment with
`uv sync --dev` unless you explicitly use `--skip-bootstrap` or `--ci`.

After each bundle redeploy, `deploy.sh` also runs the shared infra
reconciliation job by default so the app service principal keeps the expected
Lakebase and related runtime permissions. Use `--skip-shared-infra` only when
you intentionally need to bypass that automatic reconciliation step.

Recommended first local command:

```bash
cd agent_app
./scripts/deploy.sh --target dev --run-job prep
```

Specific job example:

```bash
cd agent_app
./scripts/deploy.sh --target dev --run-job agent_app_metadata_refresh_job
```

Fresh terminal example:

```bash
cd agent_app
databricks auth login --profile prod
./scripts/deploy.sh --target prod --skip-bootstrap --run-job full --start-app
```

## Local Development Best Practice

After `./scripts/deploy.sh` has created `agent_app/.venv`, use one of the local
development entrypoints:

- `./scripts/dev-local.sh`
  - standard local startup
  - good for normal validation and less chatty workflows
- `./scripts/dev-local-hot-reload.sh`
  - watch-mode local development
  - preferred when actively editing backend or frontend code

You do not need to run `dev-local.sh` before `dev-local-hot-reload.sh`.
They are alternative standalone entrypoints.

Both local dev scripts now assume the project virtualenv already exists. If
`.venv` is missing, they stop early and print an actionable message telling you
to run `./scripts/deploy.sh` first.

## How To Use

1. Open `scripts/deploy_notebook.py` from the repo in Databricks.
2. Set widgets:
   - `project_dir`: path to the `agent_app` folder
   - `target`: `dev` or `prod`
   - `profile`: optional Databricks CLI profile override
   - `deployment_context`: `web_terminal`, `ci`, or `local`
   - `skip_bootstrap`: `auto`, `true`, or `false`; `auto` means true only for `web_terminal`
   - `run_job`: blank for deploy-only, or one of `meta`, `infra`, `prep`, `val`, `full`
   - `sync_workspace`: `true` or `false`
   - `start_app`: `true` or `false`
3. Run the preflight cell.
4. Run the deploy cell.
   - For `deployment_context=ci`, the cell executes the deploy script directly.
   - For interactive contexts, copy the printed deploy handoff into the terminal and run it from the `agent_app` directory.
5. Run the verification cell after deployment completes.

## Handoff Examples

The notebook deploy handoff reflects the current widget values. With
`deployment_context=web_terminal`, it includes `--skip-bootstrap`.

Example deploy handoff:

```bash
cd /Workspace/Users/you@example.com/path/to/agent_app
./scripts/deploy.sh --target prod --skip-bootstrap --profile prod --run-job full --start-app
```

If `sync_workspace=true`, the handoff also includes `--sync-workspace`.

With `deployment_context=ci`, the notebook executes the command directly. The
command includes `--ci`, and `deploy.sh` passes `--auto-approve` to
`databricks bundle deploy`. `deploy.sh --ci` skips local bootstrap, so the CI
runner must install required tools such as `python3`, Databricks CLI, and any
Python dependencies before executing the notebook/deploy step. `skip_bootstrap=true`
is only needed when using non-CI contexts with a pre-prepared environment:

```bash
cd /path/to/agent_app
./scripts/deploy.sh --target prod --ci --profile prod --run-job full --start-app
```

If you need to discover raw job keys first:

```bash
cd /Workspace/Users/you@example.com/path/to/agent_app
./scripts/deploy.sh --target prod --skip-bootstrap --profile prod --list-jobs
```

The notebook also prints a separate destroy handoff for teardown:

```bash
cd /Workspace/Users/you@example.com/path/to/agent_app
./scripts/destroy.sh --target prod --profile prod
```

Destroy warning:

- `destroy.sh` removes bundle-managed resources for the selected target
- review the resolved target and profile before running it
- add `--auto-approve` only for an already reviewed non-interactive teardown

## Notes

- the notebook is a control plane, not the executor
- bundle commands should run in the Databricks web terminal, not in notebook cells
- this keeps workspace operators, local terminals, and CI aligned on one deploy path
