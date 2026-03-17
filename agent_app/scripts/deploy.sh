#!/usr/bin/env bash
# deploy.sh — Deploy the multi-agent Genie app to Databricks Apps via DAB.
#
# Usage:
#   ./scripts/deploy.sh                       # deploy to dev target (default)
#   ./scripts/deploy.sh --target prod          # deploy to prod target
#   ./scripts/deploy.sh --profile my-profile   # use a specific Databricks profile
#   ./scripts/deploy.sh --run                  # deploy + start the app
#   ./scripts/deploy.sh --sync                 # sync files first, then deploy
#
# This is a convenience wrapper around 'databricks bundle deploy' and
# 'databricks bundle run'.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$APP_DIR/.env"

TARGET="dev"
PROFILE=""
RUN_AFTER=false
SYNC_FIRST=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target|-t)  TARGET="$2"; shift 2 ;;
    --profile|-p) PROFILE="$2"; shift 2 ;;
    --run)        RUN_AFTER=true; shift ;;
    --sync)       SYNC_FIRST=true; shift ;;
    *)            echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# Resolve profile from .env if not passed
if [[ -z "$PROFILE" && -f "$ENV_FILE" ]]; then
  PROFILE=$(grep -E "^DATABRICKS_CONFIG_PROFILE=.+" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)
fi

PROFILE_ARGS=()
if [[ -n "$PROFILE" ]]; then
  PROFILE_ARGS=("--profile" "$PROFILE")
fi

cd "$APP_DIR"

echo "=== Deploy: multi-agent-genie-app-dev ==="
echo "  Target  : $TARGET"
echo "  Profile : ${PROFILE:-<default>}"
echo

# Optional: sync files to workspace first
if [[ "$SYNC_FIRST" == true ]]; then
  echo "Syncing files to workspace..."
  databricks bundle sync -t "$TARGET" "${PROFILE_ARGS[@]}"
  echo "✅ Sync complete"
  echo
fi

# Deploy
echo "Deploying bundle (target: $TARGET)..."
databricks bundle deploy -t "$TARGET" "${PROFILE_ARGS[@]}"
echo "✅ Deploy complete"

# Optional: run (start) the app
if [[ "$RUN_AFTER" == true ]]; then
  echo
  BUNDLE_NAME=$(awk '/^  apps:$/ {getline; if ($0 ~ /^    [a-zA-Z0-9_-]+:$/) {sub(/:$/, "", $1); print $1}}' databricks.yml)
  if [[ -z "$BUNDLE_NAME" ]]; then
    echo "⚠️  Could not determine bundle name from databricks.yml"
    echo "  Run manually: databricks bundle run <name> -t $TARGET ${PROFILE_ARGS[*]:-}"
  else
    echo "Starting app ($BUNDLE_NAME)..."
    databricks bundle run "$BUNDLE_NAME" -t "$TARGET" "${PROFILE_ARGS[@]}"
    echo "✅ App started"
  fi
fi

echo
echo "=== Done ==="
