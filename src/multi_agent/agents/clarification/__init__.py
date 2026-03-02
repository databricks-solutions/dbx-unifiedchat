"""
Clarification agent package.

Exports unified_intent_context_clarification_node — the same function
signature as the original clarification.py — so graph.py requires no changes.
"""

from typing import Optional

from ...core.state import AgentState
from .agent import ClarificationAgent


def unified_intent_context_clarification_node(
    state: AgentState,
    llm_endpoint: Optional[str] = None,
    table_name: Optional[str] = None,
) -> dict:
    """
    LangGraph node: unified intent detection, classification, and clarity check.

    Thin wrapper around ClarificationAgent.run() that loads config when
    llm_endpoint or table_name are not provided explicitly.

    Args:
        state: Current agent state (AgentState)
        llm_endpoint: Optional LLM endpoint override
        table_name: Optional enriched docs table name override

    Returns:
        Dict of state updates
    """
    if llm_endpoint is None or table_name is None:
        from ...core.config import get_config
        config = get_config()
        if llm_endpoint is None:
            llm_endpoint = config.llm.clarification_endpoint
        if table_name is None:
            table_name = (
                f"{config.unity_catalog.catalog_name}"
                f".{config.unity_catalog.schema_name}"
                f".enriched_genie_docs_chunks"
            )

    agent = ClarificationAgent(llm_endpoint=llm_endpoint, table_name=table_name)
    return agent.run(state)


__all__ = ["unified_intent_context_clarification_node"]
