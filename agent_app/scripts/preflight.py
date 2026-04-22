"""Deployment preflight check for the Databricks App bundle.

Verifies workspace-scoped resources referenced by the bundle exist in the
target workspace *before* `databricks bundle deploy` attempts to use them.

Exit codes:
    0  all checks ok (or only warnings, unless --strict)
    1  at least one FATAL check failed
    2  preflight itself could not run (bad args, yaml parse error, etc.)

Usage:
    python scripts/preflight.py --target dev
    python scripts/preflight.py --target dev --profile dbx-unifiedchat-dev
    python scripts/preflight.py --target dev --strict     # warn => fatal
    python scripts/preflight.py --target dev --format json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from scripts.notebook_deploy_lib import (  # noqa: E402
    CheckStatus,
    ResourceCheck,
    check_workspace_resources,
    resolve_effective_profile,
    summarize_checks,
)


_STATUS_GLYPH = {
    CheckStatus.OK: "✅",
    CheckStatus.WARN: "⚠️ ",
    CheckStatus.FATAL: "❌",
}

_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
}


def _color(text: str, code: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_ANSI[code]}{text}{_ANSI['reset']}"


def _print_text(
    checks: list[ResourceCheck],
    *,
    target: str,
    profile: str | None,
    color: bool,
) -> None:
    title = _color(f"Preflight · target={target} · profile={profile or '<ambient>'}", "bold", enabled=color)
    print(title)
    print()

    categories = ["connectivity", "deploy-blocking", "shared-infra", "runtime"]
    seen = {c.category for c in checks}
    for category in categories:
        if category not in seen:
            continue
        header = _color(f"[{category}]", "cyan", enabled=color)
        print(header)
        for c in [x for x in checks if x.category == category]:
            glyph = _STATUS_GLYPH[c.status]
            status_color = {
                CheckStatus.OK: "green",
                CheckStatus.WARN: "yellow",
                CheckStatus.FATAL: "red",
            }[c.status]
            line = f"  {glyph} {c.name:<30} {c.identifier}"
            print(_color(line, status_color, enabled=color))
            print(f"       {c.message}")
            if c.yaml_ref:
                print(_color(f"       source: {c.yaml_ref}", "dim", enabled=color))
            if c.fix_hint and c.status != CheckStatus.OK:
                hint_lines = c.fix_hint.splitlines()
                for i, ln in enumerate(hint_lines):
                    prefix = "       fix: " if i == 0 else "            "
                    print(_color(f"{prefix}{ln}", "dim", enabled=color))
        print()

    counts = summarize_checks(checks)
    summary = (
        f"Summary: {counts['ok']} ok · "
        f"{counts['warn']} warn · "
        f"{counts['fatal']} fatal"
    )
    if counts["fatal"]:
        print(_color(summary, "red", enabled=color))
    elif counts["warn"]:
        print(_color(summary, "yellow", enabled=color))
    else:
        print(_color(summary, "green", enabled=color))


def _print_json(
    checks: list[ResourceCheck],
    *,
    target: str,
    profile: str | None,
) -> None:
    payload = {
        "target": target,
        "profile": profile,
        "checks": [
            {
                "name": c.name,
                "category": c.category,
                "identifier": c.identifier,
                "status": c.status.value,
                "message": c.message,
                "yaml_ref": c.yaml_ref,
                "fix_hint": c.fix_hint,
            }
            for c in checks
        ],
        "summary": summarize_checks(checks),
    }
    print(json.dumps(payload, indent=2))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--target", "-t", required=True, help="Bundle target (e.g. dev, prod)")
    p.add_argument("--profile", "-p", default=None, help="Override Databricks CLI profile")
    p.add_argument(
        "--project-dir",
        default=str(APP_DIR),
        help="Path to the bundle directory (default: agent_app).",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as fatal (exit 1 if any warn or fatal).",
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    project_dir = Path(args.project_dir).resolve()
    if not (project_dir / "databricks.yml").exists():
        print(
            f"ERROR: databricks.yml not found in {project_dir}. "
            "Pass --project-dir pointing at the bundle root.",
            file=sys.stderr,
        )
        return 2

    try:
        effective_profile = resolve_effective_profile(project_dir, args.target, args.profile)
        checks, _settings = check_workspace_resources(
            project_dir, args.target, effective_profile
        )
    except Exception as e:
        print(f"ERROR: preflight failed to run ({type(e).__name__}): {e}", file=sys.stderr)
        return 2

    color = not args.no_color and sys.stdout.isatty() and args.format == "text"
    if args.format == "json":
        _print_json(checks, target=args.target, profile=effective_profile)
    else:
        _print_text(checks, target=args.target, profile=effective_profile, color=color)

    counts = summarize_checks(checks)
    if counts["fatal"] > 0:
        return 1
    if args.strict and counts["warn"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
