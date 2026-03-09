#!/bin/bash
# Destroy the Databricks Asset Bundle on the dev workspace.
# Removes all deployed resources (jobs, files) from dev — irreversible.
#
# Requires:
#   - Databricks CLI installed and authenticated
#   - Profile "dev" pointing to the dev workspace

set -e

PROFILE="dev"
TARGET="dev"

echo "=========================================="
echo "  DAB Destroy — Target: $TARGET"
echo "=========================================="
echo ""
echo "  Workspace : https://fevm-serverless-dbx-unifiedchat-dev.cloud.databricks.com/"
echo "  Bundle    : dbx-unifiedchat"
echo ""
echo "  WARNING: This will permanently remove all deployed resources on dev."
echo ""

# Prompt for confirmation unless --auto-approve is passed
if [[ "$1" != "--auto-approve" ]]; then
    read -r -p "  Type 'yes' to confirm destruction: " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo ""
        echo "  Aborted."
        exit 0
    fi
fi

echo ""
echo "Destroying bundle on $TARGET..."
databricks bundle destroy -t "$TARGET" -p "$PROFILE" --auto-approve

echo ""
echo "=========================================="
echo "  Destroy complete."
echo "=========================================="
echo ""
