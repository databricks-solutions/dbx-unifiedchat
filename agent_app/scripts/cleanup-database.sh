#!/usr/bin/env bash
# cleanup-database.sh — List and optionally delete Lakebase database instances.
#
# Usage:
#   ./scripts/cleanup-database.sh                  # interactive mode
#   ./scripts/cleanup-database.sh --delete NAME    # delete a specific instance (with confirmation)
#   ./scripts/cleanup-database.sh --list           # list only, no prompts

set -euo pipefail

ACTION="interactive"
DELETE_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --delete) DELETE_NAME="$2"; ACTION="delete"; shift 2 ;;
    --list)   ACTION="list"; shift ;;
    *)        echo "Unknown argument: $1"; exit 1 ;;
  esac
done

section() { echo; echo "=== $* ==="; }

# ---------------------------------------------------------------------------
# List instances
# ---------------------------------------------------------------------------
list_instances() {
  echo "Fetching Lakebase database instances..."
  echo
  INSTANCES=$(databricks database list-database-instances --output json 2>/dev/null || echo "[]")

  COUNT=$(echo "$INSTANCES" | jq 'length')
  if [[ "$COUNT" == "0" ]]; then
    echo "No database instances found."
    return 1
  fi

  echo "Found $COUNT instance(s):"
  echo
  echo "$INSTANCES" | jq -r '.[] | "  \(.name)  (state: \(.state // "unknown"), capacity: \(.capacity // "unknown"))"'
  echo
  return 0
}

# ---------------------------------------------------------------------------
# Delete a specific instance
# ---------------------------------------------------------------------------
delete_instance() {
  local name="$1"
  echo "⚠️  WARNING: This will permanently delete the Lakebase instance '$name'"
  echo "   All data (chat history, agent memory) will be lost."
  echo
  read -p "Type the instance name to confirm deletion: " CONFIRM

  if [[ "$CONFIRM" != "$name" ]]; then
    echo "Deletion cancelled (name did not match)."
    exit 1
  fi

  echo "Deleting instance '$name'..."
  if databricks database delete-database-instance "$name" 2>/dev/null; then
    echo "✅ Instance '$name' deleted successfully."
  else
    echo "❌ Failed to delete instance '$name'." >&2
    echo "   It may not exist or you may lack permissions." >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "$ACTION" in
  list)
    list_instances
    ;;
  delete)
    delete_instance "$DELETE_NAME"
    ;;
  interactive)
    section "Lakebase Database Instances"
    if ! list_instances; then
      exit 0
    fi

    read -p "Enter instance name to delete (or press Enter to skip): " TARGET
    if [[ -z "$TARGET" ]]; then
      echo "No instance selected. Exiting."
      exit 0
    fi

    delete_instance "$TARGET"
    ;;
esac
