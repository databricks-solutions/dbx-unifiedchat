"""
BaseAgent: Shared utilities for all agent nodes.

Consolidates duplicated code from clarification.py, planning.py,
sql_synthesis.py, and summarize.py into a single base class.

Usage:
    from ..core.base_agent import BaseAgent

    class MyAgent(BaseAgent):
        def __init__(self, ...):
            super().__init__("my_agent")

        def run(self, state):
            self.track_agent_model_usage("databricks-claude-haiku-4-5")
"""

from typing import Any, Dict


class BaseAgent:
    """
    Base class providing shared utilities for all agent nodes.

    Subclasses must call super().__init__(agent_name) to register their name,
    which is then used automatically by track_agent_model_usage.
    """

    # Shared across all BaseAgent subclasses — aggregates metrics for the
    # lifetime of the process
    _performance_metrics: Dict[str, Any] = {
        "node_timings": {},
        "cache_stats": {},
        "agent_model_usage": {},
    }

    def __init__(self, agent_name: str) -> None:
        """
        Args:
            agent_name: Short identifier for this agent (e.g. "clarification").
                        Used in metrics tracking without repeating the string
                        on every call.
        """
        self.agent_name = agent_name

    # ------------------------------------------------------------------
    # Performance tracking
    # ------------------------------------------------------------------

    def track_agent_model_usage(self, model_endpoint: str) -> None:
        """
        Record which LLM endpoint this agent used.

        Args:
            model_endpoint: Endpoint name (e.g. "databricks-claude-haiku-4-5")
        """
        usage = self._performance_metrics.setdefault("agent_model_usage", {})
        if self.agent_name not in usage:
            usage[self.agent_name] = {"model": model_endpoint, "invocations": 0}
        usage[self.agent_name]["invocations"] += 1
        print(f"[{self.agent_name}] using model: {model_endpoint}")

    @classmethod
    def record_cache_hit(cls, cache_type: str) -> None:
        """Increment the hit counter for the given cache type."""
        key = f"{cache_type}_hits"
        stats = cls._performance_metrics.setdefault("cache_stats", {})
        stats[key] = stats.get(key, 0) + 1

    @classmethod
    def record_cache_miss(cls, cache_type: str) -> None:
        """Increment the miss counter for the given cache type."""
        key = f"{cache_type}_misses"
        stats = cls._performance_metrics.setdefault("cache_stats", {})
        stats[key] = stats.get(key, 0) + 1

    @classmethod
    def get_cache_stats(cls) -> Dict[str, Any]:
        """Return the current performance metrics."""
        return dict(cls._performance_metrics)
