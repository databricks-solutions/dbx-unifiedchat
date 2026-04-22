# Deployment Preflight Check

This doc describes the preflight check that runs automatically before every `databricks bundle deploy` initiated via `scripts/deploy.sh` (and `make app-deploy-dev` / `make deploy`).

## Why

The bundle references several workspace-scoped resources by ID or name (MLflow experiment, SQL warehouse, Genie spaces, Foundation Model endpoints, Unity Catalog catalogs, Vector Search endpoint). When any of these are missing — typically after switching the CLI profile to a different workspace — `databricks bundle deploy` fails deep inside `terraform apply` with cryptic errors like:

```
Error: failed to create app
Node ID 4357851413903846 does not exist.
```

Preflight hits the Databricks SDK for each referenced resource *before* terraform runs, turning those cryptic failures into this:

```
❌ MLflow experiment              4357851413903846
     not found in workspace (ResourceDoesNotExist)
     source: databricks.yml variables.experiment_id
     fix: Create a new experiment in the target workspace and update
          `experiment_id` under `targets.<target>.variables` in databricks.yml.
```

## How it runs

| Entry point | Behavior |
| --- | --- |
| `make app-deploy-dev`, `make deploy`, `bash scripts/deploy.sh --target <t>` | Auto-runs preflight after `bundle validate`, before `bundle deploy`. Exits non-zero on any **fatal** check. |
| `make preflight` (or `make preflight-prod`) | Run preflight on its own, without deploying. |
| `python agent_app/scripts/preflight.py --target <t>` | Same, direct. Add `--strict` to fail on warnings. Add `--format json` for CI. |

## What it checks

Each check is tagged with a **severity**:

- **Fatal** — blocks `deploy.sh`. The bundle references this resource directly; terraform will fail without it.
- **Warn** — surfaces the issue and continues. The post-deploy job or the running app will hit this.
- **Strict warn** — same as warn, but upgraded to fatal with `--strict-preflight` on `deploy.sh` or `--strict` on the standalone script.

### Connectivity

| Check | Severity | What it verifies |
| --- | --- | --- |
| Workspace auth | Fatal | `w.current_user.me()` succeeds with the resolved profile. Prevents misleading failures later. |

### Deploy-blocking (terraform apply will fail without these)

| Check | Severity | Source in `databricks.yml` | SDK call |
| --- | --- | --- | --- |
| MLflow experiment | Fatal | `variables.experiment_id` (per-target override allowed) | `w.experiments.get_experiment(id)` |
| SQL warehouse | Fatal | `variables.sql_warehouse_id` | `w.warehouses.get(id)` |

Both are referenced by `agent_app/resources/app.yml` (`experiment_id` at line 80, `sql_warehouse_id` at line 85).

### Shared-infra (post-deploy `agent_app_shared_infra_job` will fail without these)

| Check | Severity | Source in `databricks.yml` | SDK call |
| --- | --- | --- | --- |
| UC catalog (app) | Warn | `variables.catalog_name` | `w.catalogs.get(name)` |
| UC catalog (data / Delta Sharing) | Warn | `variables.data_catalog_name` | `w.catalogs.get(name)` |

The app-owned catalog must exist before the shared-infra workflow can create the schema and volume inside it. The data catalog is a read-only Delta Sharing catalog that grants are applied to manually (see `agent_app/resources/schemas.yml` comments).

### Runtime (app will fail at query time)

| Check | Severity | Source in `databricks.yml` | SDK call |
| --- | --- | --- | --- |
| Genie space (each ID) | Warn | `variables.genie_space_ids` (comma-separated) | `w.genie.get_space(id)` |
| Vector Search endpoint | Warn | `variables.vs_endpoint_name` | `w.vector_search_endpoints.get_endpoint(name)` |
| Serving endpoint — planning | Warn | `variables.llm_endpoint_planning` | `w.serving_endpoints.get(name)` |
| Serving endpoint — clarification | Warn | `variables.llm_endpoint_clarification` | ⎯⎯ |
| Serving endpoint — SQL synthesis (table) | Warn | `variables.llm_endpoint_sql_synthesis_table` | ⎯⎯ |
| Serving endpoint — SQL synthesis (genie) | Warn | `variables.llm_endpoint_sql_synthesis_genie` | ⎯⎯ |
| Serving endpoint — execution | Warn | `variables.llm_endpoint_execution` | ⎯⎯ |
| Serving endpoint — summarize | Warn | `variables.llm_endpoint_summarize` | ⎯⎯ |
| Serving endpoint — detect code lookup | Warn | `variables.llm_endpoint_detect_code_lookup` | ⎯⎯ |
| Serving endpoint — default LLM | Warn | `variables.llm_endpoint` | ⎯⎯ |
| Serving endpoint — embedding | Warn | `variables.embedding_model` | ⎯⎯ |

Duplicate endpoint names (e.g. `llm_endpoint` and `llm_endpoint_clarification` both pointing at `databricks-gpt-5-4-mini`) are de-duplicated — each distinct endpoint is only queried once.

## Interpreting the output

```
Preflight · target=dev · profile=dbx-unifiedchat-dev

[connectivity]
  ✅ Workspace auth                 ranjan.prasad@databricks.com
       authenticated as ranjan.prasad@databricks.com

[deploy-blocking]
  ❌ MLflow experiment              4357851413903846
       not found in workspace (ResourceDoesNotExist)
       source: databricks.yml variables.experiment_id
       fix: ...

Summary: 7 ok · 5 warn · 2 fatal
```

- `✅` — resource resolved.
- `⚠️ ` — resource missing or inaccessible; deploy continues; affected features will fail later.
- `❌` — blocker; `deploy.sh` will exit 1.

## Bypass / override

| Need | How |
| --- | --- |
| Skip preflight entirely (CI, iteration) | `bash scripts/deploy.sh --target dev --skip-preflight`, or `SKIP_PREFLIGHT=1 make app-deploy-dev` |
| Treat warnings as fatal | `bash scripts/deploy.sh --target dev --strict-preflight` |
| Run preflight without deploying | `make preflight` or `python agent_app/scripts/preflight.py --target dev` |
| Machine-readable output (CI) | `python agent_app/scripts/preflight.py --target dev --format json` |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | All checks OK, or only warnings (without `--strict`). |
| 1 | At least one fatal check failed (or `--strict` with warnings). |
| 2 | Preflight itself could not run (bad args, yaml parse error, path not found). |

## Implementation

- Check functions live in `agent_app/scripts/notebook_deploy_lib.py` (`_check_*`).
- Orchestrator: `check_workspace_resources(project_dir, target, profile)` in the same file.
- CLI: `agent_app/scripts/preflight.py`.
- Integration in `agent_app/scripts/deploy.sh`: `run_preflight` is invoked immediately before `databricks bundle deploy`.
- Variable resolution reuses existing helpers: `resolve_bundle_var`, `resolve_effective_profile` (also in `notebook_deploy_lib.py`).

## Adding a new check

1. Add a `_check_xxx(w, value, ...) -> ResourceCheck` helper in `notebook_deploy_lib.py`.
2. Append its key to `_PREFLIGHT_VAR_KEYS` so the resolver returns it.
3. Call it from `check_workspace_resources(...)` and assign an appropriate `category` (`connectivity`, `deploy-blocking`, `shared-infra`, or `runtime`).
4. Pick a severity that matches the failure mode: does the bundle deploy fail on missing, or only the app runtime?
5. Add a row to the checks table above.
