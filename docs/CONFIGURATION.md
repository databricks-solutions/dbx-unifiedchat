# Configuration Guide

This repository now has one maintained shared configuration source:

- `agent_app/databricks.yml` for Databricks deployment targets and shared app settings used by both deploy flows and local bootstrap

Local development still materializes and reads:

- `agent_app/.env` for local runtime state, auth context, resolved database connection details, and local-only overrides

## Active Configuration Layers

| Layer | File | Purpose |
|------|------|---------|
| Shared config | `agent_app/databricks.yml` | Canonical dev/prod targets plus shared ETL, app, Lakebase, warehouse, MLflow, and Genie settings |
| Local runtime overlay | `agent_app/.env` | Local development auth, ports, PG connection, persisted target/profile selection, and optional local-only overrides |

## Shared Config Model

`agent_app/databricks.yml` is the canonical source for shared, target-aware
configuration. Both `deploy.sh` and the local dev scripts resolve target and
bundle variables from it.

That said, local development is not fully `.env`-free:

- the local dev scripts create or update `agent_app/.env`
- they copy bundle-managed values from `agent_app/databricks.yml` into `.env`
- they also store machine/runtime-specific values in `.env`, such as
  `DATABRICKS_CONFIG_PROFILE`, `LOCAL_DATABRICKS_TARGET`, `PGHOST`, and `PGUSER`
- the Python and Node local runtimes load `.env` directly at startup

So the accurate model is:

- `agent_app/databricks.yml` is the committed, public-safe baseline consumed by
  local bootstrap and bundle commands
- `agent_app/databricks.local.yml` is the gitignored private copy of your real
  values; copy what you need from there back into `agent_app/databricks.yml`
  before local dev or deployment
- `agent_app/.env` is the local runtime overlay derived from `databricks.yml`
  plus local machine-specific state

## Canonical Bundle Variables

`agent_app/databricks.yml` now uses canonical deployment variable names for the
shared app + ETL flow:

- `catalog_name`
- `schema_name`
- `data_catalog_name`
- `data_schema_name`
- `data_catalog_schemas` for multi-catalog source grants, using
  `catalog_1:schema1, schema2; catalog_2:schema_a, schema_b`
- `sql_warehouse_id`
- `genie_space_ids`
- `lakebase_project`
- `lakebase_branch`
- `experiment_id`
- `vs_endpoint_name`
- `embedding_model`
- `pipeline_type`

## Targets

The app bundle defines `dev` and `prod` targets under `agent_app/databricks.yml`.

Target responsibilities:

- workspace profile selection
- environment-specific catalog/schema overrides
- workspace-specific warehouse and Genie IDs
- app-specific Lakebase and MLflow experiment settings

Default behavior:

- `dev` is the default target for the app bundle
- `prod` must be selected explicitly with `--target prod`

## Local Development Config

For local development:

1. run `agent_app/scripts/dev-local.sh` or `agent_app/scripts/dev-local-hot-reload.sh`
2. let those scripts sync bundle-managed values from `agent_app/databricks.yml` into `agent_app/.env`

Important distinction:

- shared environment-aware settings belong in `agent_app/databricks.yml`
- machine/user-specific runtime settings belong in `agent_app/.env`
- local app startup still reads `agent_app/.env` directly

In practice, edit:

- `agent_app/databricks.yml` when changing shared dev/prod targets or app/ETL settings
- `agent_app/.env` only for local machine/runtime concerns that should not be shared

## How Deploy Scripts Resolve Config

### `agent_app/scripts/deploy.sh`

Reads from:

- bundle target passed by `--target`
- bundle profile passed by `--profile` or target workspace profile
- `agent_app/databricks.yml`

Does not read:

- `agent_app/.env`

### `agent_app/scripts/deploy_notebook.py`

Reads from:

- notebook widgets
- `agent_app/databricks.yml`

It prints the canonical `./scripts/deploy.sh ...` command and should be treated
as a workspace operator control plane, not a second config system.

### Local dev scripts

`dev-local.sh` and `dev-local-hot-reload.sh` read:

- `agent_app/databricks.yml` for bundle defaults
- `agent_app/.env` for remembered target/profile selection and local runtime values

They also write back into `agent_app/.env` so the local Python and Node
processes can start with a concrete environment file.

## Industry-Friendly Defaults

The deployment simplification work is opinionated in a few ways:

- the app bundle, not the root bundle, is the deployment center
- target defaults are portable and no longer tied to a single user-specific workspace path
- CI and local operators use the same deploy contract
- ETL and app prep settings live next to the app deploy bundle

## Security Notes

- keep secrets out of `agent_app/databricks.yml`
- prefer Databricks auth via profiles or CI environment variables
- keep `agent_app/.env` uncommitted
- separate `dev` and `prod` values for warehouses, Genie spaces, and catalogs

## Troubleshooting

### Bundle variables look wrong

- run `cd agent_app && databricks bundle validate -t <target>`
- check the target overrides in `agent_app/databricks.yml`

### Local app uses stale values

- inspect `agent_app/.env`
- rerun `agent_app/scripts/dev-local.sh` so bundle values are rehydrated
- verify the target/profile you are using matches the bundle target you expect

### CI deploys a different shape than local

- it should not anymore
- check `.github/workflows/ci-cd.yml` and confirm it runs from `agent_app/`
- compare the flags passed to `agent_app/scripts/deploy.sh`

## SQL synthesis and Databricks SQL dialect

The **SQL Synthesis** agents (table and Genie routes) append a **static Databricks
SQL skill** at runtime from `agent_server/multi_agent/prompts/databricks_sql_synthesis_skill.md`
so generated SQL targets **Databricks SQL (Spark SQL)**. No extra config keys are
required for that behavior.

To attach **optional** workspace tooling (for example **external MCP** servers
registered in Unity Catalog for documentation or other aids), see
`agent_app/scripts/discover_tools.py` and Databricks documentation on **MCP**
and **Model Context Protocol** integration. That is separate from the static
prompt skill, which must remain the baseline for the shipped agent.

## See Also

- [Deployment Guide](DEPLOYMENT.md)
- [Local Development Guide](LOCAL_DEVELOPMENT.md)
- [Architecture](ARCHITECTURE.md)
