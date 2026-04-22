# Deployment Checklist

Operator runbook for deploying `dbx-unifiedchat` to a Databricks workspace. Use this alongside:

- [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) — reference for `deploy.sh` flags and modes.
- [`docs/PREFLIGHT.md`](PREFLIGHT.md) — reference for the automated workspace-resource preflight.

**How to use this file**

Work top-down. Sections marked *(one-time)* only need to be done once per machine or workspace. Sections marked *(every deploy)* are the mandatory path. Skip *(first-time workspace seed)* if you've successfully deployed to this workspace before. Skip *(prod)* for dev deploys.

---

## A. Local environment — one-time per machine

- [ ] **Git repo** — cloned at `~/sandbox/dbx-unifiedchat`, on the correct branch, no uncommitted changes that shouldn't ship.
  ```bash
  git status
  git log --oneline -3
  ```
- [ ] **Shell** — bash or zsh (macOS/Linux). On Windows, Git Bash or WSL is required; `cmd.exe`/PowerShell does not work.
- [ ] **Python 3.12** — installed or auto-downloadable via `uv`. The project pins `3.12` via `agent_app/.python-version`.
- [ ] **`uv`** — installed. `curl -LsSf https://astral.sh/uv/install.sh | sh`
- [ ] **Node.js 20+** + `npm` — installed. `node --version`
- [ ] **Databricks CLI ≥ 0.294** — `databricks --version`. Install: `brew tap databricks/tap && brew install databricks`
- [ ] **`make`** — preinstalled on macOS with Xcode CLT; `sudo apt install make` on Linux.
- [ ] **VPN (Databricks corporate)** — required for `pypi-proxy.dev.databricks.com` (Jamf `/etc/hosts` blackholes public pypi). `curl -fsS https://pypi-proxy.dev.databricks.com/simple/` should return 200.
- [ ] **One-shot environment check** — `make doctor` reports everything as OK.

## B. Workspace auth — one-time per profile

- [ ] **Profile name in `agent_app/databricks.yml`** matches your local profile.
  ```bash
  grep -n 'profile:' agent_app/databricks.yml
  ```
- [ ] **`databricks auth login --profile <profile>`** succeeds. You will be sent to the browser to authenticate.
  ```bash
  databricks auth login --profile dbx-unifiedchat-dev
  ```
- [ ] **`databricks auth describe --profile <profile>`** shows `host`, `user`, and `Authenticated with:` lines with no errors. Token is fresh.
- [ ] **Profile token is not stale** — if last used >24h ago, re-run `databricks auth login --profile <profile>` before deploying.

## C. Target selection & config review — every deploy

- [ ] **Target decided** — `dev` or `prod`. For prod, see Section H below.
- [ ] **Git branch is correct** for this target.
  ```bash
  git branch --show-current
  ```
- [ ] **`agent_app/databricks.yml` reviewed** — especially the `targets.<target>.variables` block. Confirm these refer to resources in the target workspace and not a previous one:
  - `experiment_id` (line 119 for dev, 134 for prod)
  - `sql_warehouse_id`
  - `catalog_name`, `data_catalog_name`, `data_schema_name`
  - `genie_space_ids` (comma-separated)
- [ ] **No uncommitted changes to `databricks.yml` and/or `resources/*.yml`** unless intentional.
  ```bash
  git diff agent_app/databricks.yml agent_app/resources/
  ```
- [ ] **`.env` at repo root** — exists and has `DATABRICKS_HOST` / `DATABRICKS_TOKEN` (only used by local dev; deploy reads from CLI profile, but `.env` is referenced by some helper scripts and tests).
  ```bash
  ls .env
  ```

## D. Automated preflight — every deploy

- [ ] **`make preflight`** passes with no fatal checks.
  ```bash
  make preflight                 # defaults to dev
  TARGET=prod make preflight     # or the prod variant via `make preflight-prod`
  ```
  Fatal = MLflow experiment and/or SQL warehouse missing. See [`docs/PREFLIGHT.md`](PREFLIGHT.md) for the full list and fix hints. Do not skip with `--skip-preflight` unless you understand what terraform will reject.

## E. First-time workspace seed — only if the workspace is fresh

You need this block if *any* of these are true for the target workspace:

- `make preflight` flags the UC catalog (app) as missing.
- `make preflight` flags Genie spaces as `PermissionDenied` / not found.
- You have never successfully run `make app-deploy-dev` against this workspace.

Steps:

- [ ] **UC catalog (app)** — ensure `catalog_name` (e.g. `serverless_dbx_unifiedchat_dev_catalog`) exists. If not, create it and grant `USE_CATALOG` to your identity:
  ```sql
  -- Run in Databricks SQL
  CREATE CATALOG IF NOT EXISTS serverless_dbx_unifiedchat_dev_catalog;
  ```
  The schema and volume inside it are created by the shared-infra workflow on first deploy — no action needed here.
- [ ] **UC catalog (data, Delta Sharing)** — the source data catalog (e.g. `healthverity_claims_sample_patient_dataset_dev`) must already be shared with your account. If not, the account admin has to add the share. Grants to the app service principal are a separate step (below, after first deploy).
- [ ] **MLflow experiment** — create one in the target workspace and update `experiment_id` in `databricks.yml`:
  ```bash
  databricks experiments create-experiment \
    --name "/Users/<you>@databricks.com/multi_agent_<target>" \
    --profile <profile>
  # copy the returned experiment_id into databricks.yml targets.<target>.variables.experiment_id
  ```
- [ ] **SQL warehouse** — identify (or create) a warehouse and put its ID in `databricks.yml`:
  ```bash
  databricks warehouses list --profile <profile>
  # copy an id into databricks.yml targets.<target>.variables.sql_warehouse_id
  ```
- [ ] **Genie spaces (3 required)** — these must exist in the target workspace and your user must have access. Create them in the Databricks Genie UI, or reuse existing ones. Update `genie_space_ids` (comma-separated) in `databricks.yml`.
- [ ] **Foundation Model endpoints** — `databricks-gpt-5-4-mini`, `databricks-claude-sonnet-4-5`, `databricks-claude-haiku-4-5`, `databricks-gpt-5-4`, `databricks-gte-large-en`. `make preflight` will flag any that are missing from the workspace/region. If flagged, file an ask with workspace admin or switch `llm_endpoint_*` variables to available endpoints.
- [ ] **Vector Search endpoint** — `make preflight` will flag if missing. The metadata refresh ETL job creates the *index* inside it, but the *endpoint* itself may need admin creation depending on workspace type:
  ```bash
  databricks vector-search-endpoints create-endpoint \
    --name genie_multi_agent_vs --endpoint-type STANDARD --profile <profile>
  ```
- [ ] **Re-run `make preflight`** — everything should be ✅ or only ⚠️ for Genie spaces and UC data catalog (those are handled later).

## F. Deploy — every deploy

- [ ] **Deploy bundle + shared-infra + start** — one command does it all:
  ```bash
  make app-deploy-dev-run        # validates, preflight, bundle deploy, shared infra, start app
  ```
  Or if you want staged execution:
  ```bash
  make dab-validate              # validates yaml
  make app-deploy-dev            # validate → preflight → deploy → shared infra (no app start)
  ```
- [ ] **Deploy log shows no terraform errors**. Watch for `Bundle deploy complete` and `Shared infra reconciliation completed`.
- [ ] **App resource reported** — the deploy log prints the app's URL and service-principal client ID in the smoke-verify step.

## G. Post-deploy verification — every deploy

- [ ] **Smoke verify output shows `status: RUNNING`** and a non-empty `url`.
- [ ] **Delta Sharing grants to app SP (one-time per data catalog / per app)** — the app's service principal needs explicit grants on the read-only data catalog. This cannot be done by bundles (Delta Sharing catalogs are read-only from the consumer side). Ask the catalog owner to run, once:
  ```sql
  GRANT USE_SCHEMA ON SCHEMA healthverity_claims_sample_patient_dataset_dev.hv_claims_sample
    TO `<app-service-principal-client-id>`;
  GRANT SELECT ON SCHEMA healthverity_claims_sample_patient_dataset_dev.hv_claims_sample
    TO `<app-service-principal-client-id>`;
  ```
  (Reference: `agent_app/resources/schemas.yml:18-23`.)
- [ ] **App URL loads** — open it in a browser. Auth should pass through and the chat UI should render.
- [ ] **Smoke query** — ask a simple question through the UI and confirm you get a streamed response.
- [ ] **App logs are clean on startup** — no repeated auth failures, no config errors:
  ```bash
  databricks apps logs dbx-unifiedchat-app-dev --profile <profile> | tail -100
  ```
- [ ] **MLflow experiment receiving traces** — visit the experiment in the workspace UI; confirm a new run appears after your smoke query.

## H. Prod-only extras

Run these *in addition to* sections A-G when the target is `prod`.

- [ ] **Change approval** obtained (per your team's process).
- [ ] **Deploying from `main`** — prod should never deploy from a feature branch unless there's a documented reason.
  ```bash
  git branch --show-current   # expect: main
  git log --oneline origin/main..HEAD   # expect: empty
  ```
- [ ] **Communicated** to stakeholders (Slack, email, etc.) before the deploy window.
- [ ] **Rollback plan** in mind — know the last-known-good bundle commit hash (`git log` on main) and how to redeploy it.
- [ ] **`app-deploy-prod-run`** — confirms twice before deploying:
  ```bash
  make app-deploy-prod-run
  ```
- [ ] **Post-deploy monitoring** — watch app logs and MLflow for ~15 min after the deploy completes. Be ready to roll back.

## I. Rollback / destroy

- [ ] **Roll forward to the previous commit** — preferred over `destroy`. Check out the last-known-good commit, run the same deploy command.
  ```bash
  git checkout <last-good-sha>
  make app-deploy-dev-run
  ```
- [ ] **Full teardown (dev only)** — only if rolling forward is impossible. This removes the app + bundle-managed jobs:
  ```bash
  make dab-destroy-dev
  ```
  Destroy does NOT remove Lakebase data, UC volumes, or the MLflow experiment. If you need to wipe those, do it manually via the workspace UI/CLI.
- [ ] **Never `destroy` prod** without a documented incident and sign-off.

---

## Appendix — quick one-liner pre-flight

For someone who has done this before and wants to confirm everything in 10 seconds:

```bash
cd ~/sandbox/dbx-unifiedchat \
  && git status -s \
  && databricks auth describe --profile dbx-unifiedchat-dev >/dev/null \
  && make preflight \
  && echo "✅ ready to deploy"
```

If that prints `✅ ready to deploy`, you can run `make app-deploy-dev-run`. If it fails, the failure points at what to fix and this checklist tells you how.
