#!/bin/bash
# Run DAB jobs on the dev workspace.
#
# HOW TO USE:
#   Comment out all run commands except the one you want, then execute:
#     ./dab_run_dev.sh
#
# Available jobs:
#   full_pipeline          — Full end-to-end: ETL → deploy agent → validate (all 5 tasks)
#   etl_pipeline           — ETL only: export Genie spaces → enrich metadata → build VS index
#   agent_deploy           — Agent only: deploy to Model Serving → validate live endpoint
#   agent_integration_test — Standalone integration test against deployed endpoint (no deploy)
#
# Requires:
#   - Databricks CLI installed and authenticated
#   - Profile "dev" pointing to the dev workspace
#   - Bundle already deployed (run ./dab_deploy_dev.sh first)

set -e

PROFILE="dev"
TARGET="dev"

echo "=========================================="
echo "  DAB Run — Target: $TARGET"
echo "=========================================="
echo ""
echo "  Workspace : https://fevm-serverless-dbx-unifiedchat-dev.cloud.databricks.com/"
echo "  Bundle    : dbx-unifiedchat"
echo ""

# ===========================================================================
# SELECT THE JOB TO RUN — comment out all except the one you want
# ===========================================================================

# ---------------------------------------------------------------------------
# Option 1 — Full pipeline (ETL → deploy agent → validate)
#   Tasks: export_genie_spaces → enrich_table_metadata →
#          build_vector_search_index → deploy_agent → validate_agent
#   Use when: full refresh of data + redeploy + smoke test in one shot
# ---------------------------------------------------------------------------
JOB="full_pipeline"

# ---------------------------------------------------------------------------
# Option 2 — ETL only (no agent deploy)
#   Tasks: export_genie_spaces → enrich_table_metadata → build_vector_search_index
#   Use when: Genie spaces or metadata changed, but agent code is unchanged
# ---------------------------------------------------------------------------
# JOB="etl_pipeline"

# ---------------------------------------------------------------------------
# Option 3 — Agent deploy + validate (no ETL)
#   Tasks: deploy_agent → validate_agent
#   Use when: agent code changed, vector index is already up to date
# ---------------------------------------------------------------------------
#JOB="agent_deploy"

# ---------------------------------------------------------------------------
# Option 4 — Integration test only (no deploy)
#   Tasks: validate_agent (runs test_agent_databricks.py against live endpoint)
#   Use when: spot-checking a running endpoint without redeploying anything
# ---------------------------------------------------------------------------
# JOB="agent_integration_test"

# ===========================================================================

echo "  Running job : $JOB"
echo ""

databricks bundle run "$JOB" -t "$TARGET" -p "$PROFILE"

echo ""
echo "=========================================="
echo "  Job run complete: $JOB"
echo "=========================================="
echo ""
