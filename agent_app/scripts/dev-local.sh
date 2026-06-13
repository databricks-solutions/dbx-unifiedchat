#!/usr/bin/env bash
# dev-local.sh — Set up and start the multi-agent Genie app locally.
#
# What this script does:
#   1. Resolves bundle target/profile from flags + databricks.yml
#   2. Clears conflicting shell-level Databricks env vars
#   3. Checks prerequisites (databricks CLI, jq, uv, node)
#   4. Verifies Databricks auth
#   5. Resolves PGHOST from the target's Lakebase instance
#   6. Syncs target-managed settings into .env
#   7. Frees stale ports
#   8. Starts the dev server (backend + frontend)
#
# Usage:
#   ./scripts/dev-local.sh
#   ./scripts/dev-local.sh --skip-migrate
#   ./scripts/dev-local.sh --target dev
#   ./scripts/dev-local.sh --target prod --profile my-profile
#   ./scripts/dev-local.sh --no-ui
#
# Defaults pulled from databricks.yml and remembered in .env:
#   Target   = LOCAL_DATABRICKS_TARGET, else bundle default target
#   Profile  = --profile, else target.workspace.profile
#   Lakebase = variables.lakebase_project + variables.lakebase_branch for the resolved target

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_PGDATABASE="databricks_postgres"
DEFAULT_PGPORT="5432"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
VENV_PYTHON="$APP_DIR/.venv/bin/python"

SKIP_MIGRATE=false
TARGET=""
PROFILE=""
EXTRA_ARGS=()

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-migrate) SKIP_MIGRATE=true; shift ;;
    --target|-t)    TARGET="$2"; shift 2 ;;
    --profile)      PROFILE="$2"; shift 2 ;;
    --no-ui)        EXTRA_ARGS+=("--no-ui"); shift ;;
    *)              EXTRA_ARGS+=("$1"); shift ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()    { echo "  $*"; }
success() { echo "✅ $*"; }
warn()    { echo "⚠️  $*"; }
error()   { echo "❌ $*" >&2; exit 1; }
section() { echo; echo "=== $* ==="; }

read_env_value() {
  local key="$1"
  if [[ ! -f "$ENV_FILE" ]]; then
    return 0
  fi

  grep -E "^${key}=.+" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true
}

set_env_value() {
  local key="$1" val="$2"
  touch "$ENV_FILE"
  sed -i.bak "/^#*[[:space:]]*${key}=/d" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  echo "${key}=${val}" >> "$ENV_FILE"
  success "  $key set to ${val}"
}

set_env_if_missing() {
  local key="$1" val="$2"
  local current
  current="$(read_env_value "$key" | tr -d '[:space:]')"
  local placeholder_pattern="your-|<your|your_|changeme|example\.com"
  if [[ -n "$current" ]] && ! echo "$current" | grep -qE "$placeholder_pattern"; then
    info "  $key already set — skipping"
  else
    set_env_value "$key" "$val"
  fi
}

free_port() {
  local port="$1"
  local pids
  pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill -9 2>/dev/null || true
    success "Killed stale process(es) on port $port (PID: $pids)"
  else
    info "Port $port is free"
  fi
}

require_local_venv() {
  if [[ -x "$VENV_PYTHON" ]]; then
    info "Using project virtualenv at $APP_DIR/.venv"
    return 0
  fi

  error "Project virtualenv not found at '$APP_DIR/.venv'. Run ./scripts/deploy.sh first to bootstrap the local uv environment."
}

resolve_bundle_context() {
  local env_target env_profile
  env_target="$(read_env_value "LOCAL_DATABRICKS_TARGET" | tr -d '[:space:]')"
  env_profile="$(read_env_value "DATABRICKS_CONFIG_PROFILE" | tr -d '[:space:]')"

  "$VENV_PYTHON" - "$APP_DIR" "${TARGET:-}" "${PROFILE:-}" "${env_target:-}" "${env_profile:-}" <<'PY'
import pathlib
import re
import shlex
import sys

import yaml

app_dir = pathlib.Path(sys.argv[1])
explicit_target = sys.argv[2].strip()
explicit_profile = sys.argv[3].strip()
env_target = sys.argv[4].strip()
env_profile = sys.argv[5].strip()

config = yaml.safe_load((app_dir / "databricks.yml").read_text()) or {}
targets = config.get("targets") or {}
variables = config.get("variables") or {}

if not targets:
    raise SystemExit("No bundle targets found in databricks.yml.")

if explicit_target and explicit_target not in targets:
    raise SystemExit(f"Bundle target '{explicit_target}' not found in databricks.yml.")

def resolve_target() -> str:
    if explicit_target:
        return explicit_target
    if env_target and env_target in targets:
        return env_target
    if explicit_profile:
        for target_name, target_config in targets.items():
            workspace_profile = ((target_config or {}).get("workspace") or {}).get("profile")
            if workspace_profile == explicit_profile:
                return target_name
    for target_name, target_config in targets.items():
        if (target_config or {}).get("default") is True:
            return target_name
    return next(iter(targets))

resolved_target = resolve_target()
target_config = targets.get(resolved_target) or {}
workspace_profile = ((target_config.get("workspace") or {}).get("profile") or "").strip()
resolved_profile = explicit_profile or workspace_profile or env_profile

def _raw_bundle_var(name: str):
    target_value = (target_config.get("variables") or {}).get(name)
    return target_value if target_value is not None else (variables.get(name) or {}).get("default")

def resolve_bundle_var(name: str, seen: set[str] | None = None) -> str:
    seen = seen or set()
    if name in seen:
        return ""
    seen.add(name)
    value = _raw_bundle_var(name)
    if isinstance(value, str):
        value = value.replace("${bundle.target}", resolved_target)
        value = re.sub(
            r"\$\{var\.([A-Za-z0-9_]+)\}",
            lambda match: resolve_bundle_var(match.group(1), set(seen)),
            value,
        )
    return "" if value is None else str(value)

context = {
    "RESOLVED_TARGET": resolved_target,
    "RESOLVED_PROFILE": resolved_profile,
    "BUNDLE_LAKEBASE_PROJECT": resolve_bundle_var("lakebase_project"),
    "BUNDLE_LAKEBASE_BRANCH": resolve_bundle_var("lakebase_branch"),
    "BUNDLE_LAKEBASE_INSTANCE": resolve_bundle_var("lakebase_instance_name"),
    "BUNDLE_CATALOG_NAME": resolve_bundle_var("catalog_name"),
    "BUNDLE_SCHEMA_NAME": resolve_bundle_var("schema_name"),
    "BUNDLE_DATA_CATALOG_NAME": resolve_bundle_var("data_catalog_name"),
    "BUNDLE_DATA_SCHEMA_NAME": resolve_bundle_var("data_schema_name"),
    "BUNDLE_DATA_CATALOG_SCHEMAS": resolve_bundle_var("data_catalog_schemas"),
    "BUNDLE_UC_FUNCTION_NAMES": resolve_bundle_var("uc_function_names"),
    "BUNDLE_SQL_WAREHOUSE_ID": resolve_bundle_var("sql_warehouse_id"),
    "BUNDLE_GENIE_SPACE_IDS": resolve_bundle_var("genie_space_ids"),
    "BUNDLE_APP_LOGO_URL": resolve_bundle_var("app_logo_url"),
    "BUNDLE_EXPERIMENT_ID": resolve_bundle_var("experiment_id"),
    "BUNDLE_MLFLOW_TRACKING_URI": resolve_bundle_var("mlflow_tracking_uri"),
    "BUNDLE_MLFLOW_REGISTRY_URI": resolve_bundle_var("mlflow_registry_uri"),
    "BUNDLE_LLM_ENDPOINT": resolve_bundle_var("llm_endpoint"),
    "BUNDLE_LLM_ENDPOINT_CLARIFICATION": resolve_bundle_var("llm_endpoint_clarification"),
    "BUNDLE_LLM_ENDPOINT_PLANNING": resolve_bundle_var("llm_endpoint_planning"),
    "BUNDLE_LLM_ENDPOINT_SQL_SYNTHESIS_TABLE": resolve_bundle_var("llm_endpoint_sql_synthesis_table"),
    "BUNDLE_LLM_ENDPOINT_SQL_SYNTHESIS_GENIE": resolve_bundle_var("llm_endpoint_sql_synthesis_genie"),
    "BUNDLE_LLM_ENDPOINT_EXECUTION": resolve_bundle_var("llm_endpoint_execution"),
    "BUNDLE_LLM_ENDPOINT_SUMMARIZE": resolve_bundle_var("llm_endpoint_summarize"),
    "BUNDLE_LLM_ENDPOINT_CHART": resolve_bundle_var("llm_endpoint_chart"),
    "BUNDLE_LLM_ENDPOINT_DETECT_CODE_LOOKUP": resolve_bundle_var("llm_endpoint_detect_code_lookup"),
    "BUNDLE_LTM_ENABLED": resolve_bundle_var("ltm_enabled"),
    "BUNDLE_LTM_PROVIDER": resolve_bundle_var("ltm_provider"),
    "BUNDLE_LTM_CHECKPOINT_PATH": resolve_bundle_var("ltm_checkpoint_path"),
    "BUNDLE_LTM_CLASSIFIER_CHECKPOINT": resolve_bundle_var("ltm_classifier_checkpoint"),
    "BUNDLE_LTM_REGRESSOR_CHECKPOINT": resolve_bundle_var("ltm_regressor_checkpoint"),
    "BUNDLE_LTM_DEVICE": resolve_bundle_var("ltm_device"),
    "BUNDLE_LTM_MAX_CONTEXT_ROWS": resolve_bundle_var("ltm_max_context_rows"),
    "BUNDLE_LTM_N_ESTIMATORS": resolve_bundle_var("ltm_n_estimators"),
    "BUNDLE_LTM_ALLOW_AUTO_DOWNLOAD": resolve_bundle_var("ltm_allow_auto_download"),
    "BUNDLE_LTM_NEXUS_ENDPOINT": resolve_bundle_var("ltm_nexus_endpoint"),
    "BUNDLE_LTM_NEXUS_REGION": resolve_bundle_var("ltm_nexus_region"),
}

for key, value in context.items():
    print(f"{key}={shlex.quote(value)}")
PY
}

require_local_venv
eval "$(resolve_bundle_context)"

[[ -z "$RESOLVED_TARGET" ]] && error "Unable to resolve bundle target from databricks.yml."
[[ -z "$RESOLVED_PROFILE" ]] && error "Unable to resolve Databricks profile for target '$RESOLVED_TARGET'. Pass --profile explicitly."

TARGET="$RESOLVED_TARGET"
PROFILE="$RESOLVED_PROFILE"

# ---------------------------------------------------------------------------
# 0. Clear conflicting shell-level Databricks env vars
# ---------------------------------------------------------------------------
section "Clearing conflicting shell environment variables"

for var in DATABRICKS_CONFIG_PROFILE DATABRICKS_HOST DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET; do
  if [[ -n "${!var:-}" ]]; then
    warn "Unsetting shell-level $var='${!var}' before applying target/profile selection"
    unset "$var"
  else
    info "$var not set in shell — ok"
  fi
done

export DATABRICKS_CONFIG_PROFILE="$PROFILE"
success "Using target '$TARGET' with Databricks profile '$PROFILE'"

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
section "Checking prerequisites"

for cmd in databricks jq uv node npm; do
  if command -v "$cmd" &>/dev/null; then
    success "$cmd found ($(command -v "$cmd"))"
  else
    error "$cmd not found. Please install it first."
  fi
done

NODE_VERSION=$(node --version | tr -d 'v' | cut -d. -f1)
if [[ "$NODE_VERSION" -lt 18 ]]; then
  error "Node.js 18+ required (found v$NODE_VERSION). Run: nvm use 20"
fi

# ---------------------------------------------------------------------------
# 2. Databricks auth
# ---------------------------------------------------------------------------
section "Verifying Databricks authentication"

AUTH_JSON=$(databricks auth describe --profile "$PROFILE" --output json 2>/dev/null) || \
  error "Not authenticated. Run: databricks auth login --profile $PROFILE"

PGUSER=$(echo "$AUTH_JSON" | jq -r '.username // empty')
[[ -z "$PGUSER" ]] && error "Could not determine username from databricks auth describe."

success "Authenticated as: $PGUSER"

# ---------------------------------------------------------------------------
# 3. Resolve PGHOST from Lakebase
# ---------------------------------------------------------------------------
section "Resolving Lakebase connection details"

LAKEBASE_PROJECT="${BUNDLE_LAKEBASE_PROJECT:-}"
LAKEBASE_BRANCH="${BUNDLE_LAKEBASE_BRANCH:-}"
LAKEBASE_INSTANCE="${BUNDLE_LAKEBASE_INSTANCE:-}"

if [[ -n "$LAKEBASE_PROJECT" && -n "$LAKEBASE_BRANCH" ]]; then
  info "Autoscaling project: $LAKEBASE_PROJECT"
  info "Autoscaling branch: $LAKEBASE_BRANCH"
  PGHOST=$(databricks api get "/api/2.0/postgres/projects/${LAKEBASE_PROJECT}/branches/${LAKEBASE_BRANCH}/endpoints" \
           --profile "$PROFILE" \
           --output json 2>/dev/null | jq -r '.endpoints[0].status.hosts.host // empty') || true
elif [[ -n "$LAKEBASE_INSTANCE" ]]; then
  info "Provisioned instance: $LAKEBASE_INSTANCE"
  PGHOST=$(databricks database get-database-instance "$LAKEBASE_INSTANCE" \
           --profile "$PROFILE" \
           2>/dev/null | jq -r '.read_write_dns // empty') || true
else
  error "No Lakebase project/branch or legacy instance could be resolved for target '$TARGET'."
fi

if [[ -z "$PGHOST" || "$PGHOST" == "null" ]]; then
  warn "Could not resolve PGHOST for the configured Lakebase connection."
  warn "The chat UI will start in ephemeral mode (no persistent chat history)."
  PGHOST=""
else
  success "PGHOST resolved: $PGHOST"
fi

# ---------------------------------------------------------------------------
# 4. Write .env
# ---------------------------------------------------------------------------
section "Configuring .env"

cd "$APP_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  touch "$ENV_FILE"
  info "Created empty .env (values will be hydrated from databricks.yml)"
fi

set_env_value "LOCAL_DATABRICKS_TARGET" "$TARGET"
set_env_value "DATABRICKS_CONFIG_PROFILE" "$PROFILE"
[[ -n "$BUNDLE_CATALOG_NAME" ]] && set_env_value "CATALOG_NAME" "$BUNDLE_CATALOG_NAME"
[[ -n "$BUNDLE_SCHEMA_NAME" ]] && set_env_value "SCHEMA_NAME" "$BUNDLE_SCHEMA_NAME"
[[ -n "$BUNDLE_DATA_CATALOG_NAME" ]] && set_env_value "DATA_CATALOG_NAME" "$BUNDLE_DATA_CATALOG_NAME"
[[ -n "$BUNDLE_DATA_SCHEMA_NAME" ]] && set_env_value "DATA_SCHEMA_NAME" "$BUNDLE_DATA_SCHEMA_NAME"
[[ -n "$BUNDLE_DATA_CATALOG_SCHEMAS" ]] && set_env_value "DATA_CATALOG_SCHEMAS" "$BUNDLE_DATA_CATALOG_SCHEMAS"
[[ -n "$BUNDLE_UC_FUNCTION_NAMES" ]] && set_env_value "UC_FUNCTION_NAMES" "$BUNDLE_UC_FUNCTION_NAMES"
[[ -n "$BUNDLE_SQL_WAREHOUSE_ID" ]] && set_env_value "SQL_WAREHOUSE_ID" "$BUNDLE_SQL_WAREHOUSE_ID"
[[ -n "$BUNDLE_GENIE_SPACE_IDS" ]] && set_env_value "GENIE_SPACE_IDS" "$BUNDLE_GENIE_SPACE_IDS"
[[ -n "$BUNDLE_APP_LOGO_URL" ]] && set_env_value "APP_LOGO_URL" "$BUNDLE_APP_LOGO_URL"
[[ -n "$BUNDLE_MLFLOW_TRACKING_URI" ]] && set_env_value "MLFLOW_TRACKING_URI" "$BUNDLE_MLFLOW_TRACKING_URI"
[[ -n "$BUNDLE_MLFLOW_REGISTRY_URI" ]] && set_env_value "MLFLOW_REGISTRY_URI" "$BUNDLE_MLFLOW_REGISTRY_URI"
[[ -n "$BUNDLE_LLM_ENDPOINT" ]] && set_env_value "LLM_ENDPOINT" "$BUNDLE_LLM_ENDPOINT"
[[ -n "$BUNDLE_LLM_ENDPOINT_CLARIFICATION" ]] && set_env_value "LLM_ENDPOINT_CLARIFICATION" "$BUNDLE_LLM_ENDPOINT_CLARIFICATION"
[[ -n "$BUNDLE_LLM_ENDPOINT_PLANNING" ]] && set_env_value "LLM_ENDPOINT_PLANNING" "$BUNDLE_LLM_ENDPOINT_PLANNING"
[[ -n "$BUNDLE_LLM_ENDPOINT_SQL_SYNTHESIS_TABLE" ]] && set_env_value "LLM_ENDPOINT_SQL_SYNTHESIS_TABLE" "$BUNDLE_LLM_ENDPOINT_SQL_SYNTHESIS_TABLE"
[[ -n "$BUNDLE_LLM_ENDPOINT_SQL_SYNTHESIS_GENIE" ]] && set_env_value "LLM_ENDPOINT_SQL_SYNTHESIS_GENIE" "$BUNDLE_LLM_ENDPOINT_SQL_SYNTHESIS_GENIE"
[[ -n "$BUNDLE_LLM_ENDPOINT_EXECUTION" ]] && set_env_value "LLM_ENDPOINT_EXECUTION" "$BUNDLE_LLM_ENDPOINT_EXECUTION"
[[ -n "$BUNDLE_LLM_ENDPOINT_SUMMARIZE" ]] && set_env_value "LLM_ENDPOINT_SUMMARIZE" "$BUNDLE_LLM_ENDPOINT_SUMMARIZE"
[[ -n "$BUNDLE_LLM_ENDPOINT_CHART" ]] && set_env_value "LLM_ENDPOINT_CHART" "$BUNDLE_LLM_ENDPOINT_CHART"
[[ -n "$BUNDLE_LLM_ENDPOINT_DETECT_CODE_LOOKUP" ]] && set_env_value "LLM_ENDPOINT_DETECT_CODE_LOOKUP" "$BUNDLE_LLM_ENDPOINT_DETECT_CODE_LOOKUP"
[[ -n "$BUNDLE_LTM_ENABLED" ]] && set_env_value "LTM_ENABLED" "$BUNDLE_LTM_ENABLED"
[[ -n "$BUNDLE_LTM_PROVIDER" ]] && set_env_value "LTM_PROVIDER" "$BUNDLE_LTM_PROVIDER"
[[ -n "$BUNDLE_LTM_CHECKPOINT_PATH" ]] && set_env_value "LTM_CHECKPOINT_PATH" "$BUNDLE_LTM_CHECKPOINT_PATH"
[[ -n "$BUNDLE_LTM_CLASSIFIER_CHECKPOINT" ]] && set_env_value "LTM_CLASSIFIER_CHECKPOINT" "$BUNDLE_LTM_CLASSIFIER_CHECKPOINT"
[[ -n "$BUNDLE_LTM_REGRESSOR_CHECKPOINT" ]] && set_env_value "LTM_REGRESSOR_CHECKPOINT" "$BUNDLE_LTM_REGRESSOR_CHECKPOINT"
[[ -n "$BUNDLE_LTM_DEVICE" ]] && set_env_value "LTM_DEVICE" "$BUNDLE_LTM_DEVICE"
[[ -n "$BUNDLE_LTM_MAX_CONTEXT_ROWS" ]] && set_env_value "LTM_MAX_CONTEXT_ROWS" "$BUNDLE_LTM_MAX_CONTEXT_ROWS"
[[ -n "$BUNDLE_LTM_N_ESTIMATORS" ]] && set_env_value "LTM_N_ESTIMATORS" "$BUNDLE_LTM_N_ESTIMATORS"
# Local dev cannot mount UC Volumes (/Volumes only exists on Databricks compute,
# and is a root-owned system dir on macOS). Always let TabICL download checkpoints
# into its own local cache so the embedded LTM works without /Volumes access.
set_env_value "LTM_ALLOW_AUTO_DOWNLOAD" "true"
[[ -n "$BUNDLE_LTM_NEXUS_ENDPOINT" ]] && set_env_value "LTM_NEXUS_ENDPOINT" "$BUNDLE_LTM_NEXUS_ENDPOINT"
[[ -n "$BUNDLE_LTM_NEXUS_REGION" ]] && set_env_value "LTM_NEXUS_REGION" "$BUNDLE_LTM_NEXUS_REGION"
if [[ -n "$BUNDLE_LAKEBASE_PROJECT" && -n "$BUNDLE_LAKEBASE_BRANCH" ]]; then
  set_env_value "LAKEBASE_AUTOSCALING_PROJECT" "$BUNDLE_LAKEBASE_PROJECT"
  set_env_value "LAKEBASE_AUTOSCALING_BRANCH" "$BUNDLE_LAKEBASE_BRANCH"
  set_env_value "LAKEBASE_INSTANCE_NAME" ""
elif [[ -n "$BUNDLE_LAKEBASE_INSTANCE" ]]; then
  set_env_value "LAKEBASE_INSTANCE_NAME" "$BUNDLE_LAKEBASE_INSTANCE"
  set_env_value "LAKEBASE_AUTOSCALING_PROJECT" ""
  set_env_value "LAKEBASE_AUTOSCALING_BRANCH" ""
fi
[[ -n "$BUNDLE_EXPERIMENT_ID" ]] && set_env_value "MLFLOW_EXPERIMENT_ID" "$BUNDLE_EXPERIMENT_ID"

if [[ -n "$PGHOST" ]]; then
  set_env_value "PGUSER"     "$PGUSER"
  set_env_value "PGHOST"     "$PGHOST"
  set_env_value "PGDATABASE" "$DEFAULT_PGDATABASE"
  set_env_value "PGPORT"     "$DEFAULT_PGPORT"
else
  warn "Skipping database vars (PGHOST unavailable — ephemeral mode)"
fi

EXPERIMENT_ID="$(read_env_value "MLFLOW_EXPERIMENT_ID" | tr -d '[:space:]')"
if [[ -n "$EXPERIMENT_ID" ]]; then
  success "MLFLOW_EXPERIMENT_ID=$EXPERIMENT_ID (feedback enabled)"
else
  warn "MLFLOW_EXPERIMENT_ID not set — feedback widget will be disabled"
  warn "Set MLFLOW_EXPERIMENT_ID in databricks.yml or .env to enable feedback"
fi

success ".env configured at $ENV_FILE"

# ---------------------------------------------------------------------------
# 5. Frontend setup (npm install + optional migrate)
# ---------------------------------------------------------------------------
FRONTEND_DIR="$APP_DIR/e2e-chatbot-app-next"
if [[ -d "$FRONTEND_DIR" ]]; then
  section "Setting up frontend"
  cd "$FRONTEND_DIR"
  npm install
  success "Frontend dependencies installed"

  if [[ -n "$PGHOST" && "$SKIP_MIGRATE" == false ]]; then
    section "Running database migrations"
    info "Applying Drizzle migrations to ai_chatbot schema..."
    PGHOST="$PGHOST" PGUSER="$PGUSER" PGDATABASE="$DEFAULT_PGDATABASE" PGPORT="$DEFAULT_PGPORT" \
      npm run db:migrate
    success "Migrations complete"
  elif [[ "$SKIP_MIGRATE" == true ]]; then
    info "Skipping migrations (--skip-migrate flag set)"
  else
    info "Skipping migrations (no database configured)"
  fi
  cd "$APP_DIR"
else
  info "Frontend not cloned yet — start-app will handle it"
fi

# ---------------------------------------------------------------------------
# 6. Free stale ports
# ---------------------------------------------------------------------------
section "Clearing stale ports"

BACKEND_PORT=8000
FRONTEND_PORT=3000
if [[ -f "$ENV_FILE" ]]; then
  FRONTEND_PORT_ENV=$(grep -E "^CHAT_APP_PORT=.+" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)
  [[ -n "$FRONTEND_PORT_ENV" ]] && FRONTEND_PORT="$FRONTEND_PORT_ENV"
fi

free_port "$BACKEND_PORT"
free_port "$FRONTEND_PORT"

# ---------------------------------------------------------------------------
# 7. Start dev server
# ---------------------------------------------------------------------------
section "Starting development server"
echo
echo "  Backend  → http://localhost:$BACKEND_PORT"
if [[ ${#EXTRA_ARGS[@]} -eq 0 ]] || [[ ! " ${EXTRA_ARGS[*]} " =~ " --no-ui " ]]; then
  echo "  Frontend → http://localhost:$FRONTEND_PORT  ← Open this in your browser"
fi
echo
echo "  Target   : $TARGET"
echo "  Profile  : $PROFILE"
echo "  Lakebase : $LAKEBASE_INSTANCE"
if [[ -n "$PGHOST" ]]; then
  echo "  Database : persistent mode (PGHOST=$PGHOST)"
else
  echo "  Database : ephemeral mode (no PGHOST)"
fi
if [[ -n "$EXPERIMENT_ID" ]]; then
  echo "  Feedback : enabled (experiment $EXPERIMENT_ID)"
else
  echo "  Feedback : disabled"
fi
echo
echo "  Press Ctrl+C to stop."
echo

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  uv run start-app "${EXTRA_ARGS[@]}"
else
  uv run start-app
fi
