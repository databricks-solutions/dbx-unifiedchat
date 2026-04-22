#!/usr/bin/env bash
# deploy.sh — Canonical deployment entrypoint for the Databricks App bundle.
#
# Local / CI workflow:
#   Use this script for local terminals, GitHub Actions, and the Databricks
#   web-terminal handoff printed by `scripts/deploy_notebook.py`.
#
# Recommended usage:
#   ./scripts/deploy.sh --target dev --run-job full --start-app
#     Validate, deploy, run the full post-deploy job graph, then start the app.
#
#   ./scripts/deploy.sh --target dev --run-job prep
#     Validate, deploy, then run the prep post-deploy job graph.
#
#   ./scripts/deploy.sh --target dev --list-jobs
#     Show bundle job keys and descriptions for the selected target, then exit.
#
#   ./scripts/deploy.sh --target dev --run-job val
#     Validate, deploy, then run the app validation job alias.
#
#   ./scripts/deploy.sh --target prod --sync-workspace --run-job full --ci --skip-bootstrap
#     CI-friendly deploy using an already prepared runner.
#
# Flags:
#   --target, -t         Bundle target. Defaults to the bundle default target.
#   --profile, -p        Databricks CLI profile override.
#   --sync-workspace     Sync local bundle files to the workspace bundle folder
#                        for workspace-side development. This does not change
#                        deployment behavior on its own.
#   --skip-shared-infra  Skip the automatic shared-infra reconciliation job that
#                        normally runs after deploy.
#   --list-jobs          List available bundle jobs and their purpose, then exit.
#   --run-job <meta|infra|prep|val|full|job_key>
#                        Run one post-deploy bundle job. Use a short alias or
#                        a bundle job key from `--list-jobs`.
#   --start-app          Start the deployed app after deploy and any optional
#                        post-deploy job.
#   --bootstrap-local    Ensure local Python tooling is ready via `uv sync --dev`.
#   --skip-bootstrap     Skip local bootstrap checks and dependency sync.
#   --skip-preflight     Skip the workspace resource preflight check. Also
#                        honored via SKIP_PREFLIGHT=1 in the environment.
#   --strict-preflight   Treat preflight warnings as fatal.
#   --ci                 Non-interactive CI mode; implies no opportunistic bootstrap.
#   --help, -h           Show this help text and exit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TARGET=""
PROFILE=""
START_APP=false
SYNC_WORKSPACE=false
SKIP_SHARED_INFRA=false
LIST_JOBS_ONLY=false
POST_DEPLOY_JOB=""
CI_MODE=false
SKIP_BOOTSTRAP=false
FORCE_BOOTSTRAP=false
SKIP_PREFLIGHT="${SKIP_PREFLIGHT:-false}"
STRICT_PREFLIGHT=false
DEPRECATION_WARNINGS=()

print_help() {
  awk '
    NR == 2, /^set -euo pipefail$/ {
      if ($0 ~ /^set -euo pipefail$/) {
        exit
      }
      sub(/^# ?/, "")
      print
    }
  ' "$0"
}

info()    { echo "  $*"; }
success() { echo "✅ $*"; }
warn()    { echo "⚠️  $*"; }
error()   { echo "❌ $*" >&2; exit 1; }
section() { echo; echo "=== $* ==="; }

set_post_deploy_job() {
  local next_job="$1"
  if [[ -n "$POST_DEPLOY_JOB" ]]; then
    error "Choose only one of --run-job, --prep-only, --full-deploy, or --job."
  fi
  POST_DEPLOY_JOB="$next_job"
}

record_deprecation() {
  local old_flag="$1"
  local new_flag="$2"
  DEPRECATION_WARNINGS+=("$old_flag is deprecated; use $new_flag instead.")
}

require_value() {
  local flag="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || error "$flag requires a value."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target|-t)
      require_value "$1" "${2:-}"
      TARGET="$2"
      shift 2
      ;;
    --profile|-p)
      require_value "$1" "${2:-}"
      PROFILE="$2"
      shift 2
      ;;
    --sync-workspace)    SYNC_WORKSPACE=true; shift ;;
    --skip-shared-infra) SKIP_SHARED_INFRA=true; shift ;;
    --list-jobs)         LIST_JOBS_ONLY=true; shift ;;
    --run-job)
      require_value "$1" "${2:-}"
      set_post_deploy_job "$2"
      shift 2
      ;;
    --start-app)         START_APP=true; shift ;;
    --run)
      START_APP=true
      record_deprecation "--run" "--start-app"
      shift
      ;;
    --sync)
      SYNC_WORKSPACE=true
      record_deprecation "--sync" "--sync-workspace"
      shift
      ;;
    --prep-only)
      set_post_deploy_job "prep"
      record_deprecation "--prep-only" "--run-job prep"
      shift
      ;;
    --full-deploy)
      set_post_deploy_job "full"
      record_deprecation "--full-deploy" "--run-job full"
      shift
      ;;
    --job|-j)
      require_value "$1" "${2:-}"
      set_post_deploy_job "$2"
      record_deprecation "--job" "--run-job $2"
      shift 2
      ;;
    --bootstrap-local)   FORCE_BOOTSTRAP=true; shift ;;
    --skip-bootstrap)    SKIP_BOOTSTRAP=true; shift ;;
    --skip-preflight)    SKIP_PREFLIGHT=true; shift ;;
    --strict-preflight)  STRICT_PREFLIGHT=true; shift ;;
    --ci)                CI_MODE=true; shift ;;
    --help|-h)           print_help; exit 0 ;;
    *)                   error "Unknown argument: $1" ;;
  esac
done

if [[ "$LIST_JOBS_ONLY" == true ]]; then
  if [[ -n "$POST_DEPLOY_JOB" || "$START_APP" == true || "$SYNC_WORKSPACE" == true ]]; then
    error "--list-jobs cannot be combined with --run-job, --start-app, or --sync-workspace."
  fi
fi

resolve_bundle_context() {
  # Use `uv run --with pyyaml` so we don't depend on the system Python having
  # pyyaml pre-installed. uv is already a hard dependency of this script
  # (see `bootstrap_local_env`). `--no-project` skips pyproject.toml discovery
  # so this works before the project venv exists.
  command -v uv >/dev/null 2>&1 || { echo "❌ uv not found (required for bundle context resolution)" >&2; exit 1; }
  uv run --with pyyaml --no-project --quiet python3 - "$APP_DIR" "${TARGET:-}" "${PROFILE:-}" <<'PY'
import pathlib
import shlex
import sys

import yaml

app_dir = pathlib.Path(sys.argv[1])
explicit_target = sys.argv[2].strip()
explicit_profile = sys.argv[3].strip()

config = yaml.safe_load((app_dir / "databricks.yml").read_text()) or {}
targets = config.get("targets") or {}

if not targets:
    raise SystemExit("No bundle targets found in databricks.yml.")

if explicit_target and explicit_target not in targets:
    raise SystemExit(f"Bundle target '{explicit_target}' not found in databricks.yml.")


def resolve_target() -> str:
    if explicit_target:
        return explicit_target
    for target_name, target_config in targets.items():
        if (target_config or {}).get("default") is True:
            return target_name
    return next(iter(targets))


resolved_target = resolve_target()
workspace = ((targets.get(resolved_target) or {}).get("workspace") or {})
resolved_profile = explicit_profile or (workspace.get("profile") or "").strip()

print(f"RESOLVED_TARGET={shlex.quote(resolved_target)}")
print(f"RESOLVED_PROFILE={shlex.quote(resolved_profile)}")
PY
}

eval "$(resolve_bundle_context)"

[[ -z "$RESOLVED_TARGET" ]] && error "Unable to resolve bundle target."
TARGET="$RESOLVED_TARGET"
if [[ -z "$PROFILE" ]]; then
  PROFILE="$RESOLVED_PROFILE"
fi

PROFILE_ARGS=()
if [[ -n "$PROFILE" ]]; then
  PROFILE_ARGS=("--profile" "$PROFILE")
fi

cd "$APP_DIR"

should_bootstrap_local() {
  if [[ "$LIST_JOBS_ONLY" == true ]]; then
    return 1
  fi
  if [[ "$SKIP_BOOTSTRAP" == true ]]; then
    return 1
  fi
  if [[ "$FORCE_BOOTSTRAP" == true ]]; then
    return 0
  fi
  if [[ "$CI_MODE" == true ]]; then
    return 1
  fi
  return 0
}

require_command() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || error "$cmd not found."
}

check_databricks_cli_version() {
  local version
  version="$(python3 - "$(databricks --version 2>/dev/null)" <<'PY'
import re
import sys

text = sys.argv[1]
match = re.search(r"(\d+\.\d+\.\d+)", text)
print(match.group(1) if match else "")
PY
)"
  [[ -z "$version" ]] && error "Unable to determine Databricks CLI version."
  python3 - "$version" <<'PY'
import sys

def parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))

current = parse(sys.argv[1])
required = parse("0.294.0")
if current < required:
    raise SystemExit(
        f"Databricks CLI {'.'.join(map(str, required))}+ required, "
        f"found {'.'.join(map(str, current))}."
    )
PY
  success "Databricks CLI version OK ($version)"
}

bootstrap_local_env() {
  section "Bootstrapping local Python environment"
  require_command uv
  if [[ ! -d ".venv" ]]; then
    info "Creating local virtual environment via uv"
  else
    info "Reusing existing .venv"
  fi
  env -u VIRTUAL_ENV uv sync --dev >/dev/null
  success "Local Python dependencies synced"
}

verify_auth() {
  section "Verifying Databricks authentication"
  if [[ -n "$PROFILE" ]]; then
    databricks auth describe "${PROFILE_ARGS[@]}" --output json >/dev/null
    success "Authenticated with profile '$PROFILE'"
  else
    databricks auth describe --output json >/dev/null
    success "Authenticated with ambient Databricks credentials"
  fi
}

BUNDLE_VALIDATE_OUTPUT=""
bundle_validate_output() {
  if [[ -z "$BUNDLE_VALIDATE_OUTPUT" ]]; then
    if ! BUNDLE_VALIDATE_OUTPUT="$(databricks bundle validate -t "$TARGET" "${PROFILE_ARGS[@]}" --output json)"; then
      if [[ -n "$PROFILE" ]]; then
        error "Bundle validation failed. See the Databricks CLI output above. If the profile token is stale, run: databricks auth login --profile $PROFILE"
      fi
      error "Bundle validation failed. See the Databricks CLI output above. If workspace auth expired, re-authenticate and retry."
    fi
  fi
  printf '%s\n' "$BUNDLE_VALIDATE_OUTPUT"
}

resolve_bundle_metadata() {
  BUNDLE_VALIDATE_JSON="$(bundle_validate_output)" POST_DEPLOY_JOB_REQUESTED="$POST_DEPLOY_JOB" python3 - <<'PY'
import json
import os

config = json.loads(os.environ["BUNDLE_VALIDATE_JSON"])
apps = (config.get("resources") or {}).get("apps") or {}
jobs = (config.get("resources") or {}).get("jobs") or {}
post_deploy_job_requested = os.environ.get("POST_DEPLOY_JOB_REQUESTED", "").strip()
job_aliases = {
    "meta": "agent_app_metadata_refresh_job",
    "infra": "agent_app_shared_infra_job",
    "prep": "agent_app_preps_job",
    "val": "agent_app_validate_app_job",
    "full": "agent_app_full_deploy_job",
}

app_key = next(iter(apps.keys()), "")
app_name = ""
if app_key:
    app_name = (apps.get(app_key) or {}).get("name") or ""

resolved_post_deploy_job = job_aliases.get(post_deploy_job_requested, post_deploy_job_requested)
if resolved_post_deploy_job and resolved_post_deploy_job not in jobs:
    available = ", ".join(sorted(jobs)) or "<none>"
    raise SystemExit(
        f"Bundle job key '{resolved_post_deploy_job}' not found. "
        f"Available job keys: {available}"
    )

job_summaries = []
for job_key, job_config in sorted(jobs.items()):
    description = (job_config or {}).get("description") or ""
    job_summaries.append(
        {
            "key": job_key,
            "name": (job_config or {}).get("name") or "",
            "description": " ".join(str(description).split()),
        }
    )


job_alias_summaries = [
    {"alias": alias, "key": key}
    for alias, key in job_aliases.items()
    if key in jobs
]

for key, value in {
    "APP_KEY": app_key,
    "APP_NAME": app_name,
    "SHARED_INFRA_JOB_KEY": "agent_app_shared_infra_job" if "agent_app_shared_infra_job" in jobs else "",
    "PREP_JOB_KEY": "agent_app_preps_job" if "agent_app_preps_job" in jobs else "",
    "FULL_JOB_KEY": "agent_app_full_deploy_job" if "agent_app_full_deploy_job" in jobs else "",
    "RESOLVED_POST_DEPLOY_JOB": resolved_post_deploy_job,
    "JOB_ALIASES_JSON": json.dumps(job_alias_summaries),
    "JOB_SUMMARIES_JSON": json.dumps(job_summaries),
}.items():
    print(f"{key}={json.dumps(value)}")
PY
}

load_bundle_metadata() {
  eval "$(RESOLVED_METADATA="$(resolve_bundle_metadata)" python3 - <<'PY'
import json
import os
import shlex

for line in os.environ["RESOLVED_METADATA"].splitlines():
    key, raw = line.rstrip("\n").split("=", 1)
    print(f"{key}={shlex.quote(json.loads(raw))}")
PY
)"

  if [[ -z "${APP_KEY:-}" || -z "${APP_NAME:-}" ]]; then
    error "Failed to resolve app metadata from bundle validate output."
  fi
}

run_bundle_job_if_requested() {
  [[ -z "${RESOLVED_POST_DEPLOY_JOB:-}" ]] && return 0

  local requested_label="$POST_DEPLOY_JOB"
  if [[ "$POST_DEPLOY_JOB" != "$RESOLVED_POST_DEPLOY_JOB" ]]; then
    requested_label="$POST_DEPLOY_JOB ($RESOLVED_POST_DEPLOY_JOB)"
  fi

  section "Running post-deploy job: $requested_label"
  databricks bundle run "$RESOLVED_POST_DEPLOY_JOB" -t "$TARGET" "${PROFILE_ARGS[@]}"
  success "Post-deploy job completed: $requested_label"
}

should_run_shared_infra_job() {
  if [[ "$SKIP_SHARED_INFRA" == true ]]; then
    return 1
  fi

  [[ -n "${SHARED_INFRA_JOB_KEY:-}" ]] || error "Shared infra job key not found in bundle resources. Use --skip-shared-infra to bypass."

  if [[ -z "${RESOLVED_POST_DEPLOY_JOB:-}" ]]; then
    return 0
  fi

  if [[ "$RESOLVED_POST_DEPLOY_JOB" == "$SHARED_INFRA_JOB_KEY" ]]; then
    return 1
  fi

  if [[ -n "${PREP_JOB_KEY:-}" && "$RESOLVED_POST_DEPLOY_JOB" == "$PREP_JOB_KEY" ]]; then
    return 1
  fi

  if [[ -n "${FULL_JOB_KEY:-}" && "$RESOLVED_POST_DEPLOY_JOB" == "$FULL_JOB_KEY" ]]; then
    return 1
  fi

  return 0
}

run_shared_infra_if_needed() {
  if ! should_run_shared_infra_job; then
    return 0
  fi

  section "Running automatic shared-infra reconciliation"
  databricks bundle run "$SHARED_INFRA_JOB_KEY" -t "$TARGET" "${PROFILE_ARGS[@]}"
  success "Shared infra reconciliation completed"
}

list_bundle_jobs() {
  JOB_ALIASES_JSON="${JOB_ALIASES_JSON:-[]}" JOB_SUMMARIES_JSON="${JOB_SUMMARIES_JSON:-[]}" TARGET="$TARGET" python3 - <<'PY'
import json
import os
import textwrap

target = os.environ["TARGET"]
job_aliases = json.loads(os.environ["JOB_ALIASES_JSON"])
job_summaries = json.loads(os.environ["JOB_SUMMARIES_JSON"])


print(f"Bundle jobs for target '{target}':")
print()

print("Aliases:")
for alias in job_aliases:
    print(f"- {alias['alias']}: {alias['key']}")

print()
print("Jobs:")
for job in job_summaries:
    job_key = job["key"]
    name = job.get("name") or "<unnamed>"
    description = job.get("description") or "<no description>"
    print(f"job_key: {job_key}")
    print(f"- job_name: {name}")
    print("- description:")
    print(textwrap.fill(description, width=76, initial_indent="  ", subsequent_indent="  "))
    print()
PY
}

run_preflight() {
  if [[ "$SKIP_PREFLIGHT" == "true" || "$SKIP_PREFLIGHT" == "1" ]]; then
    section "Skipping workspace preflight (--skip-preflight)"
    return 0
  fi
  section "Running workspace preflight"
  local preflight_args=(--target "$TARGET")
  [[ -n "$PROFILE" ]] && preflight_args+=(--profile "$PROFILE")
  [[ "$STRICT_PREFLIGHT" == true ]] && preflight_args+=(--strict)
  if ! uv run --quiet python scripts/preflight.py "${preflight_args[@]}"; then
    echo
    error "Workspace preflight failed. Fix the issues above, or rerun with --skip-preflight to bypass (not recommended)."
  fi
  success "Preflight passed"
}

smoke_verify_app() {
  section "Smoke verifying deployed app"
  local app_json
  app_json="$(databricks apps get "$APP_NAME" "${PROFILE_ARGS[@]}" --output json)"
  APP_JSON="$app_json" python3 - <<'PY'
import json
import os

app = json.loads(os.environ["APP_JSON"])
sp_id = app.get("service_principal_client_id") or ""
url = app.get("url") or ""
compute_status = app.get("compute_status") or ""
status = app.get("status") or ""

print(f"  url: {url or '<missing>'}")
print(f"  service_principal_client_id: {sp_id or '<missing>'}")
print(f"  compute_status: {compute_status or '<missing>'}")
print(f"  status: {status or '<missing>'}")

if not sp_id:
    raise SystemExit("Missing service_principal_client_id on deployed app.")
if not url:
    raise SystemExit("Missing url on deployed app.")
PY
  success "App smoke verification passed"
}

section "Checking prerequisites"
require_command python3
require_command databricks
check_databricks_cli_version
if should_bootstrap_local; then
  bootstrap_local_env
else
  info "Skipping local bootstrap"
fi

verify_auth

for warning_message in "${DEPRECATION_WARNINGS[@]:-}"; do
  [[ -n "$warning_message" ]] && warn "$warning_message"
done

if [[ "$SYNC_WORKSPACE" == true ]]; then
  section "Syncing workspace files"
  databricks bundle sync -t "$TARGET" "${PROFILE_ARGS[@]}"
  success "Workspace sync complete"
fi

section "Validating bundle"
bundle_validate_output >/dev/null
success "Bundle validation passed"
load_bundle_metadata

if [[ "$LIST_JOBS_ONLY" == true ]]; then
  section "Available bundle jobs"
  list_bundle_jobs
  echo
  echo "=== Done ==="
  exit 0
fi

section "Deployment context"
info "App        : $APP_NAME"
info "Target     : $TARGET"
info "Profile    : ${PROFILE:-<ambient auth>}"
info "Sync       : $SYNC_WORKSPACE"
info "Shared infra permissions grant to app SP after deploy : $([[ "$SKIP_SHARED_INFRA" == true ]] && echo "disabled" || echo "enabled")"
info "Requested job to run after deploy : ${POST_DEPLOY_JOB:-<none>}"
info "Start app  : $START_APP"

run_preflight

section "Deploying bundle"
databricks bundle deploy -t "$TARGET" "${PROFILE_ARGS[@]}"
success "Bundle deploy complete"

# it is important to run the shared infra job after every deploy bundle so the app can use the shared 
# infra (granted permissions for resources can exceed 20 resources here, which is super nice to have!)
run_shared_infra_if_needed

# run other bundle jobs if requested
run_bundle_job_if_requested

# start the app if requested
if [[ "$START_APP" == true ]]; then
  section "Starting app"
  databricks bundle run "$APP_KEY" -t "$TARGET" "${PROFILE_ARGS[@]}"
  success "App start command completed"
fi

# smoke verify the app status if requested
smoke_verify_app

echo
echo "=== Done ==="
