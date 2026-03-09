#!/bin/bash
# Validate then deploy the Databricks Asset Bundle to the dev workspace.
#
# Steps:
#   1. Validate bundle config (catches YAML/schema errors before deploying)
#   2. Deploy all resources to dev
#
# Requires:
#   - Databricks CLI installed and authenticated
#   - Profile "dev" pointing to the dev workspace

set -e

PROFILE="dev"
TARGET="dev"

echo "=========================================="
echo "  DAB Deploy — Target: $TARGET"
echo "=========================================="
echo ""
echo "  Workspace : https://fevm-serverless-dbx-unifiedchat-dev.cloud.databricks.com/"
echo "  Bundle    : dbx-unifiedchat"
echo ""

# ---------------------------------------------------------------------------
# Step 1 — Validate
# ---------------------------------------------------------------------------
echo "Step 1/2 — Validating bundle..."
if databricks bundle validate -t "$TARGET" -p "$PROFILE"; then
    echo "✓ Validation passed"
else
    echo "✗ Validation failed — aborting deploy"
    exit 1
fi

echo ""

# ---------------------------------------------------------------------------
# Step 2 — Deploy
# ---------------------------------------------------------------------------
echo "Step 2/2 — Deploying to $TARGET..."
databricks bundle deploy -t "$TARGET" -p "$PROFILE" --auto-approve

echo ""
echo "=========================================="
echo "  Deploy complete."
echo "=========================================="
echo ""
echo "  To run a job, use: ./dab_run_dev.sh"
echo ""
