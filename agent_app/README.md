# multi-agent-genie-app-dev

## Deploy First

Run `./scripts/deploy.sh` before local development if `agent_app/.venv` does not
already exist. The deploy entrypoint is the supported bootstrap path for local
and CI usage because it:

- verifies Databricks auth and bundle context
- creates or reuses `agent_app/.venv`
- syncs Python dependencies with `uv sync --dev` unless you explicitly pass
  `--skip-bootstrap` or `--ci`

Typical local bootstrap / deploy examples:

```bash
cd agent_app
./scripts/deploy.sh --target dev --run-job prep
```

```bash
cd agent_app
./scripts/deploy.sh --target dev --run-job full --start-app
```

To discover available raw job keys:

```bash
cd agent_app
./scripts/deploy.sh --target dev --list-jobs
```

To refresh the workspace bundle folder before a normal deploy, add:

```bash
cd agent_app
./scripts/deploy.sh --target dev --sync-workspace --run-job prep
```

`--sync-workspace` only syncs local bundle files into the Databricks workspace
bundle folder before the normal deploy flow continues. It is useful for
workspace-side development, but it does not change deployment behavior on its
own.

Use `--skip-bootstrap` only when the local environment is already prepared.

After every bundle redeploy, `deploy.sh` now runs the shared infra reconciliation
job by default so the Databricks App service principal keeps the expected
Lakebase, Unity Catalog, and related runtime permissions. Use
`--skip-shared-infra` only when you intentionally want to bypass that automatic
reconciliation step.

## Ways To Deploy

- Local terminal: run `./scripts/deploy.sh ...` from `agent_app`
- Databricks web terminal: use the handoff printed by `scripts/deploy_notebook.py`
  and typically include `--skip-bootstrap`
- CI: run `./scripts/deploy.sh ... --ci`; this skips local bootstrap and passes
  `--auto-approve` to `databricks bundle deploy`. The CI runner must install
  required tools such as `python3`, Databricks CLI, and any Python dependencies
  before executing the deploy step. Add `--skip-bootstrap` only when using a
  non-CI context with a pre-prepared environment.

The bundle inputs committed to the repo are:

- `databricks.yml`
- `resources/*.yml`

`app.yaml` is intentionally kept empty/commented and is not a maintained deploy
manifest for this project. Direct Databricks Apps deployment via `databricks apps deploy`
or the App UI Deploy button is not the recommended deployment path here; use the
bundle-driven `./scripts/deploy.sh` flow instead.

## Configuration Model

Use these two layers intentionally:

- `databricks.yml` is the committed, public-safe baseline used by local dev
  scripts and bundle commands
- `databricks.local.yml` is your gitignored private copy of real workspace-
  specific values
- `.env` is the local runtime overlay used by the Python and Node processes

The local dev scripts resolve target-aware values from `databricks.yml`, then
write the materialized runtime values into `.env` before launching the app.
`databricks.local.yml` is not read automatically by the scripts or Databricks
bundle commands in this repo. Treat it as your private source of truth: create
it from `databricks.local.yml.example`, keep your real values there, and copy
the values you need into `databricks.yml` before local development or
deployment. After pulling future repo updates, re-check `databricks.yml` and
copy your private values from `databricks.local.yml` back into it as needed.
Reserve `.env` for local-only auth, runtime, and machine-specific overrides.

## Local Development Best Practice

Use exactly one of the local dev entrypoints after the project virtualenv exists:

- `./scripts/dev-local.sh`
  - one-shot local startup
  - good for general verification and normal local use
- `./scripts/dev-local-hot-reload.sh`
  - hot-reload workflow for active development
  - backend and frontend changes are reflected automatically

You do not need to run `dev-local.sh` before `dev-local-hot-reload.sh`.
Both scripts are intended to be standalone entrypoints, but they now expect the
project virtualenv to already exist. If `.venv` is missing, they stop early and
tell you to run `./scripts/deploy.sh` first.
