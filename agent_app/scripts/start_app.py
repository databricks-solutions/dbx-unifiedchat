#!/usr/bin/env python3
"""
Start script for the multi-agent Genie app.

Runs the backend (AgentServer via uvicorn) and, unless --no-ui is passed,
the Next.js chatbot frontend.  Exits when either process exits.

Usage:
    start-app [--no-ui] [--port PORT] [BACKEND_ARGS ...]
"""

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

CHATBOT_DIR = Path("e2e-chatbot-app-next")
GRANT_SCRIPT = Path("scripts/grant_lakebase_permissions.py")


def _run(cmd, *, cwd=None, label="command", check=True):
    """Run a command, stream output, and optionally exit on failure."""
    result = subprocess.run(cmd, cwd=cwd)
    if check and result.returncode != 0:
        print(f"ERROR: {label} exited with code {result.returncode}")
        sys.exit(result.returncode)
    return result.returncode


def grant_lakebase_permissions():
    """Bootstrap Lakebase roles for the app service principal (best effort)."""
    app_name = os.environ.get("DATABRICKS_APP_NAME")
    instance_name = os.environ.get("LAKEBASE_INSTANCE_NAME")
    project = os.environ.get("LAKEBASE_PROJECT") or os.environ.get("LAKEBASE_AUTOSCALING_PROJECT")
    branch = os.environ.get("LAKEBASE_BRANCH") or os.environ.get("LAKEBASE_AUTOSCALING_BRANCH")
    if not app_name or not GRANT_SCRIPT.exists():
        return
    if not instance_name and not (project and branch):
        return

    extra_args = []
    catalog_name = os.environ.get("CATALOG_NAME")
    schema_name = os.environ.get("SCHEMA_NAME")
    data_catalog_name = os.environ.get("DATA_CATALOG_NAME")
    data_schema_name = os.environ.get("DATA_SCHEMA_NAME")
    data_catalog_schemas = os.environ.get("DATA_CATALOG_SCHEMAS")
    warehouse_id = os.environ.get("SQL_WAREHOUSE_ID")
    if catalog_name and schema_name:
        extra_args.extend(["--catalog-name", catalog_name, "--schema-name", schema_name])
    if data_catalog_name and data_schema_name:
        extra_args.extend(
            [
                "--data-catalog-name",
                data_catalog_name,
                "--data-schema-name",
                data_schema_name,
            ]
        )
    if data_catalog_schemas:
        extra_args.extend(["--data-catalog-schemas", data_catalog_schemas])
    if warehouse_id:
        extra_args.extend(["--warehouse-id", warehouse_id])

    for memory_type in ("langgraph-short-term", "langgraph-long-term"):
        lakebase_args = ["--instance-name", instance_name] if instance_name else ["--project", project, "--branch", branch]
        rc = _run(
            [
                "uv",
                "run",
                "python",
                str(GRANT_SCRIPT),
                "--app-name",
                app_name,
                "--memory-type",
                memory_type,
                *lakebase_args,
                *extra_args,
            ],
            label=f"Lakebase grant ({memory_type})",
            check=False,
        )
        if rc != 0:
            print(
                f"WARNING: Lakebase grant ({memory_type}) did not complete during "
                "startup; continuing so the app can finish booting."
            )


def run_database_migrations():
    """Run chatbot DB migrations if the frontend directory exists."""
    if not CHATBOT_DIR.exists():
        return
    _run(["npm", "config", "get", "registry"], cwd=CHATBOT_DIR, label="npm registry", check=False)
    _run(
        ["npm", "config", "get", "replace-registry-host"],
        cwd=CHATBOT_DIR,
        label="npm replace-registry-host",
        check=False,
    )
    _run(["npm", "install"], cwd=CHATBOT_DIR, label="npm install")
    rc = _run(["npm", "run", "db:migrate"], cwd=CHATBOT_DIR, label="db:migrate", check=False)
    if rc != 0:
        print(
            "WARNING: db:migrate did not complete successfully; continuing app startup. "
            "Database-backed chat features may be degraded until permissions and migrations are fixed."
        )


def main():
    parser = argparse.ArgumentParser(description="Start agent app")
    parser.add_argument("--no-ui", action="store_true", help="Backend only")
    parser.add_argument("--port", type=int, default=8000)
    args, extra = parser.parse_known_args()

    load_dotenv(dotenv_path=".env", override=True)

    # Pre-flight
    grant_lakebase_permissions()
    run_database_migrations()

    # Backend
    backend_cmd = ["uv", "run", "start-server", "--port", str(args.port)] + extra
    backend = subprocess.Popen(backend_cmd)

    # Frontend
    frontend = None
    if not args.no_ui and CHATBOT_DIR.exists():
        os.environ["API_PROXY"] = f"http://localhost:{args.port}/invocations"
        _run(["npm", "run", "build"], cwd=CHATBOT_DIR, label="npm build")
        frontend = subprocess.Popen(["npm", "run", "start"], cwd=CHATBOT_DIR)

    # Wait for either process to exit, then tear down the other.
    def shutdown(signum=None, frame=None):
        for p in (backend, frontend):
            if p and p.poll() is None:
                p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    procs = [p for p in (backend, frontend) if p]
    while True:
        for p in procs:
            ret = p.poll()
            if ret is not None:
                shutdown()


if __name__ == "__main__":
    main()
