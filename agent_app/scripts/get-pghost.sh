#!/usr/bin/env bash
# get-pghost.sh — Resolve the read/write DNS for a Lakebase instance.
#
# Usage:
#   ./scripts/get-pghost.sh                                   # uses default instance name
#   ./scripts/get-pghost.sh my-lakebase-instance               # uses specified instance name
#   PGHOST=$(./scripts/get-pghost.sh)                          # capture into a variable

set -euo pipefail

DEFAULT_INSTANCE="multi-agent-genie-system-state-db"
INSTANCE="${1:-$DEFAULT_INSTANCE}"

PGHOST=$(databricks database get-database-instance "$INSTANCE" 2>/dev/null \
  | jq -r '.read_write_dns // empty')

if [[ -z "$PGHOST" || "$PGHOST" == "null" ]]; then
  echo "ERROR: Could not resolve PGHOST for instance '$INSTANCE'" >&2
  echo "Check that:" >&2
  echo "  1. You are authenticated (databricks auth describe)" >&2
  echo "  2. The instance '$INSTANCE' exists" >&2
  echo "  3. You have permission to access it" >&2
  exit 1
fi

echo "$PGHOST"
