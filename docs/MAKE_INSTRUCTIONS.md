# Make Instructions

This guide covers the `make`-based developer workflow for **DBX-UnifiedChat**. The Makefile is a thin wrapper around the canonical deployment surface — `agent_app/scripts/deploy.sh` and `agent_app/scripts/dev-local*.sh` — so you do not need to memorize the underlying CLI flags.

For deeper reference:

- [`README.md`](../README.md) — project overview
- [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) — `deploy.sh` reference
- [`docs/DEPLOY_CHECKLIST.md`](DEPLOY_CHECKLIST.md) — operator runbook
- [`docs/PREFLIGHT.md`](PREFLIGHT.md) — preflight check reference
- [`docs/LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md) — local development guide
- [`docs/CONFIGURATION.md`](CONFIGURATION.md) — configuration model (`databricks.yml` vs `.env`)

---

## TL;DR

```bash
make doctor                             # check prerequisites
make setup                              # install deps + seed databricks.local.yml
databricks auth login --profile <profile>
make dev-local                          # one-time local bootstrap
make dev-hot-reload                     # iterate locally
# OR, to deploy to dev and start the app:
make deploy
```

---

## Prerequisites

Run `make doctor` to verify everything on your machine. It prints OS-specific install hints for anything missing.

- Python 3.11+
- Node.js 20+
- `uv` (recommended) or `pip`
- Databricks CLI v0.294+
- `git`, `make`, `jq`, `npm`
- Databricks auth profile matching the profile in `agent_app/databricks.yml`
- VPN access to `pypi-proxy.dev.databricks.com` (corp machines only)

---

## First-Time Setup

```bash
make doctor          # verifies prerequisites
make setup           # installs Python + frontend deps, seeds databricks.local.yml
databricks auth login --profile <profile>
make dev-local       # creates agent_app/.env from databricks.yml; starts backend + UI
```

What `make setup` does:

1. Copies `agent_app/databricks.local.yml.example` → `agent_app/databricks.local.yml` if missing.
2. Installs Python deps from `agent_app/pyproject.toml` (`uv sync --dev` or `pip install -e .[dev]`).
3. Installs frontend deps via `npm install`.
4. Installs `pre-commit` hooks if `pre-commit` is on PATH.

After `make dev-local` runs once, edit `agent_app/.env` only for local-only overrides (auth, ports, PG host).

---

## Deployment Recipes

All deployment targets delegate to `agent_app/scripts/deploy.sh`, which runs preflight + bundle deploy + shared-infra reconciliation.

| Goal | Command | Underlying flags |
|---|---|---|
| Routine code-only deploy (dev) | `make app-deploy-dev-run` | `--target dev --start-app` |
| Canonical full deploy (dev) | `make app-deploy-dev-full` | `--target dev --run-job full --start-app` |
| Code-only deploy (prod) | `make app-deploy-prod-run` | `--target prod --start-app` |
| Canonical full deploy (prod) | `make app-deploy-prod-full` | `--target prod --run-job full --start-app` |
| ETL prep only | `make etl-prep` | `--target $TARGET --run-job prep` |
| CI deploy | `TARGET=prod make app-deploy-ci` | `--sync-workspace --run-job full --ci --skip-bootstrap --start-app` |
| List bundle jobs | `make app-list-jobs` | `--list-jobs` |
| Workspace operator path | `make deploy-notebook` | prints notebook path + steps |
| One-command dev shortcut | `make deploy` | `make doctor` + `make app-deploy-dev-run` |

### Routine vs. full deploy

- **Routine code-only** (`*-run`): re-deploys app code only. Skips the ETL prep + validation job graph. Use for normal iteration.
- **Canonical full** (`*-full`): runs the ETL prep + validation job graph in addition to the bundle deploy. Use when:
  - Genie space contents changed
  - Source table schemas changed
  - Vector index needs rebuilding
  - First-time deploy to a workspace
- **ETL prep only** (`etl-prep`): refreshes metadata + vector index without redeploying the app. Pairs with a routine code-only deploy when only ETL needs to run.

### Preflight

Preflight runs automatically before every deploy via `deploy.sh`. To run it standalone:

```bash
make preflight              # dev (default)
make preflight-prod         # prod
TARGET=prod make preflight  # alternate prod form
```

See [`docs/PREFLIGHT.md`](PREFLIGHT.md) for the full check list and severity model.

---

## Local Development

| Goal | Command |
|---|---|
| One-time local bootstrap (creates `agent_app/.env`, starts backend + UI) | `make dev-local` |
| Iterative hot-reload loop | `make dev-hot-reload` |
| Frontend dev server only | `make fe-dev` |
| Frontend production build | `make fe-build` |

`make dev-local` and `make dev-hot-reload` both hydrate `agent_app/.env` from `agent_app/databricks.yml` before starting the runtime.

---

## Testing

```bash
make python-test-unit          # unit tests (no Databricks needed)
make python-test-integration   # integration tests (-m integration; needs Databricks auth)
make python-test               # all Python tests
make fe-test                   # frontend Playwright tests
```

Under `uv` (default), these run via `uv run pytest`. Under `pip`, they fall back to bare `pytest`.

---

## Code Quality

```bash
make fmt    # auto-format Python (black + isort) and frontend (Biome)
make lint   # check Python and frontend formatting
make check  # pre-push: Python lint + unit tests
```

---

## Database (Drizzle ORM, frontend-side)

```bash
make db-migrate    # apply migrations
make db-generate   # generate migration files from schema changes
make db-studio     # open Drizzle Studio
make db-reset      # destructive reset (confirms first)
```

---

## Bundle and Workspace Utilities

```bash
make dab-validate           # validate databricks.yml without deploying
make app-list-jobs          # list bundle jobs (TARGET=dev|prod, default dev)
make deploy-notebook        # print path + steps for the workspace-native notebook
make dab-destroy-dev        # tear down dev bundle resources (confirms first)
```

### Advanced — raw bundle deploy

These bypass `deploy.sh` and therefore skip preflight, local bootstrap, and shared-infra reconciliation. Use only if you are debugging terraform or bundle behavior directly:

```bash
make dab-deploy-dev-raw
make dab-deploy-prod-raw
```

---

## Maintenance

```bash
make info        # environment snapshot (OS, installer, Python, Databricks CLI, .env state)
make clean       # remove Python caches and build artifacts
make clean-all   # also remove .venv and node_modules (confirms first)
```

---

## Troubleshooting

| Symptom | Try |
|---|---|
| `make python-install` fails on corp machine | Confirm VPN; `curl -v $UV_INDEX_URL` should return 200 |
| `agent_app/.env` not present | Run `make dev-local` once |
| Databricks auth errors | `databricks auth login --profile <profile>` then `databricks auth describe --profile <profile>` |
| Preflight fails on `experiment_id` / `sql_warehouse_id` | Update `targets.<target>.variables` in `agent_app/databricks.yml` |
| Genie spaces flagged in preflight | Verify IDs in `genie_space_ids` and that your user has access |
| App deployed but unusable | Re-run with `make app-deploy-dev-full` (or prod equivalent) so prep + validation run |
| Wrong workspace | Check `targets.<target>.workspace.profile` in `agent_app/databricks.yml`; cross-reference with `databricks auth describe --profile <profile>` |
| `make help` shows old targets | `git pull` to ensure your Makefile is current |

For deeper troubleshooting, see [`docs/DEPLOYMENT.md`](DEPLOYMENT.md#troubleshooting) and [`docs/PREFLIGHT.md`](PREFLIGHT.md).

---

## `make` ↔ `deploy.sh` Cross-Reference

If you are more comfortable with the underlying script, here is the equivalence:

| `make` target | `deploy.sh` invocation |
|---|---|
| `make app-deploy-dev` | `./scripts/deploy.sh --target dev` |
| `make app-deploy-dev-run` | `./scripts/deploy.sh --target dev --start-app` |
| `make app-deploy-dev-full` | `./scripts/deploy.sh --target dev --run-job full --start-app` |
| `make app-deploy-prod` | `./scripts/deploy.sh --target prod` |
| `make app-deploy-prod-run` | `./scripts/deploy.sh --target prod --start-app` |
| `make app-deploy-prod-full` | `./scripts/deploy.sh --target prod --run-job full --start-app` |
| `make etl-prep` | `./scripts/deploy.sh --target $TARGET --run-job prep` |
| `make app-deploy-ci` | `./scripts/deploy.sh --target $TARGET --sync-workspace --run-job full --ci --skip-bootstrap --start-app` |
| `make app-list-jobs` | `./scripts/deploy.sh --target $TARGET --list-jobs` |
| `make dev-local` | `./scripts/dev-local.sh` |
| `make dev-hot-reload` | `./scripts/dev-local-hot-reload.sh` |

---

## Where to Insert Into README

When linking from `README.md`, add an entry under **Documentation → Getting Started**:

```markdown
* [**Make Instructions**](docs/MAKE_INSTRUCTIONS.md) — `make`-based developer workflow (deploy, local dev, tests)
```
