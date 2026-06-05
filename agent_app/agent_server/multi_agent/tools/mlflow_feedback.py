"""
MLflow Feedback Tools for AgentRx.

Read-only tools that surface user feedback collected through the chat UI
(thumbs-up / thumbs-down) and the underlying MLflow trace assessments. These
let AgentRx *analyse and recommend* improvements but never mutate the system —
the admin approval / orchestration layer is out of scope for now.

Operations:
- Summarise recent feedback (counts + thumbs-down rate over a time window)
- Pull a sample of failing traces (most recent thumbs-down, with intent / spaces)

Uses the MLflow client against the configured experiment_id. Falls back
gracefully when running outside Databricks or without the experiment configured.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from langchain_core.tools import tool


def _resolve_experiment_id() -> str:
    """Resolve the active MLflow experiment id from config or env."""
    try:
        from ..core.config import get_config
        exp = getattr(get_config(), "mlflow", None)
        if exp and getattr(exp, "experiment_id", None):
            return str(exp.experiment_id)
    except Exception:
        pass
    return os.getenv("MLFLOW_EXPERIMENT_ID", "").strip()


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


@tool
def summarise_recent_feedback(days: int = 7) -> str:
    """Summarise user feedback collected in the last ``days`` days.

    Returns total trace count, thumbs-up / thumbs-down counts, and the
    thumbs-down rate. Useful for spotting recent regressions before
    recommending a knowledge-base change.

    Args:
        days: Look-back window in days. Defaults to 7.
    """
    experiment_id = _resolve_experiment_id()
    if not experiment_id:
        return json.dumps({
            "status": "warning",
            "message": "MLFLOW_EXPERIMENT_ID is not configured; feedback summary unavailable.",
        })

    try:
        import mlflow

        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        # search_traces returns the most-recent traces; we filter by start time client-side
        # because filter syntax for trace-level fields varies across MLflow versions.
        traces = mlflow.search_traces(
            experiment_ids=[experiment_id],
            max_results=1000,
            return_type="list",
        )

        thumbs_up = 0
        thumbs_down = 0
        examined = 0
        for tr in traces:
            start = getattr(tr.info, "timestamp_ms", None)
            if start is not None and start < cutoff.timestamp() * 1000:
                continue
            examined += 1
            assessments = getattr(tr.info, "assessments", None) or []
            for a in assessments:
                value = getattr(getattr(a, "feedback", None), "value", None)
                if value in (True, "thumbs_up", "up", 1):
                    thumbs_up += 1
                elif value in (False, "thumbs_down", "down", 0):
                    thumbs_down += 1

        total_votes = thumbs_up + thumbs_down
        rate = (thumbs_down / total_votes) if total_votes else 0.0

        return json.dumps({
            "status": "success",
            "window_days": days,
            "traces_examined": examined,
            "thumbs_up": thumbs_up,
            "thumbs_down": thumbs_down,
            "thumbs_down_rate": round(rate, 3),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to summarise feedback: {e}"})


@tool
def sample_failing_traces(days: int = 7, limit: int = 5) -> str:
    """Return a sample of recent traces that received negative feedback.

    For each trace, surface the user's question, the matched Genie spaces (if
    available from the trace tags), and the trace id so an admin can click through.

    Args:
        days: Look-back window in days. Defaults to 7.
        limit: Maximum number of traces to return. Defaults to 5.
    """
    experiment_id = _resolve_experiment_id()
    if not experiment_id:
        return json.dumps({
            "status": "warning",
            "message": "MLFLOW_EXPERIMENT_ID is not configured; trace lookup unavailable.",
        })

    try:
        import mlflow

        cutoff_ms = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).timestamp() * 1000
        traces = mlflow.search_traces(
            experiment_ids=[experiment_id],
            max_results=500,
            return_type="list",
        )

        out: list[dict] = []
        for tr in traces:
            start = getattr(tr.info, "timestamp_ms", None)
            if start is not None and start < cutoff_ms:
                continue
            assessments = getattr(tr.info, "assessments", None) or []
            negative = any(
                getattr(getattr(a, "feedback", None), "value", None)
                in (False, "thumbs_down", "down", 0)
                for a in assessments
            )
            if not negative:
                continue

            tags = dict(getattr(tr.info, "tags", {}) or {})
            out.append({
                "trace_id": getattr(tr.info, "trace_id", None) or getattr(tr.info, "request_id", None),
                "timestamp": getattr(tr.info, "timestamp_ms", None),
                "user_question": tags.get("mlflow.traceInputs") or tags.get("input.summary") or "",
                "matched_spaces": tags.get("planning.matched_spaces", ""),
                "execution_status": tags.get("execution.status", ""),
            })
            if len(out) >= limit:
                break

        return json.dumps({
            "status": "success",
            "window_days": days,
            "returned": len(out),
            "traces": out,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to sample failing traces: {e}"})


ALL_FEEDBACK_TOOLS = [
    summarise_recent_feedback,
    sample_failing_traces,
]
