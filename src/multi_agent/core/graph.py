"""
LangGraph workflow construction for the multi-agent system.

This module defines the graph structure, routing logic, and workflow compilation.
"""

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from typing import Optional

from ..core.state import AgentState
from ..agents.clarification import ClarificationAgent
from ..agents.planning import planning_node
from ..agents.sql_synthesis import sql_synthesis_table_node, sql_synthesis_genie_node
from ..agents.sql_execution import sql_execution_node
from ..agents.summarize import summarize_node


def create_super_agent_hybrid(config=None) -> StateGraph:
    """
    Create the Hybrid Super Agent LangGraph workflow.

    Returns:
        StateGraph: Uncompiled LangGraph workflow
    """
    if config is None:
        from .config import get_config
        config = get_config()

    table_name = (
        f"{config.unity_catalog.catalog_name}"
        f".{config.unity_catalog.schema_name}"
        f".enriched_genie_docs_chunks"
    )
    clarification_agent = ClarificationAgent(
        llm_endpoint=config.llm.clarification_endpoint,
        table_name=table_name,
    )

    workflow = StateGraph(AgentState)

    # The clarification subgraph shares AgentState so it is passed directly
    # as a node per the LangGraph subgraph pattern — no wrapper needed.
    workflow.add_node("unified_intent_context_clarification", clarification_agent.subgraph)
    workflow.add_node("planning", planning_node)
    workflow.add_node("sql_synthesis_table", sql_synthesis_table_node)
    workflow.add_node("sql_synthesis_genie", sql_synthesis_genie_node)
    workflow.add_node("sql_execution", sql_execution_node)
    workflow.add_node("summarize", summarize_node)
    
    # Define routing logic based on explicit state
    def route_after_unified(state: AgentState) -> str:
        """Route after unified node: planning or END (meta-question/irrelevant).

        Clarification no longer routes to END here — unclear queries pause inside
        the subgraph via interrupt() and resume directly into planning.
        """
        if state.get("is_irrelevant", False):
            return END
        if state.get("is_meta_question", False):
            return END
        return "planning"
    
    def route_after_planning(state: AgentState) -> str:
        """Route after planning: determine SQL synthesis route or direct summarize"""
        next_agent = state.get("next_agent", "summarize")
        if next_agent == "sql_synthesis_table":
            return "sql_synthesis_table"
        elif next_agent == "sql_synthesis_genie":
            return "sql_synthesis_genie"
        return "summarize"
    
    def route_after_synthesis(state: AgentState) -> str:
        """Route after SQL synthesis: execution or summarize (if error)"""
        next_agent = state.get("next_agent", "summarize")
        if next_agent == "sql_execution":
            return "sql_execution"
        return "summarize"  # Summarize if synthesis error
    
    # Add edges with conditional routing
    # Entry point is unified node
    workflow.set_entry_point("unified_intent_context_clarification")
    
    # Route from unified node to planning or END (clarification)
    workflow.add_conditional_edges(
        "unified_intent_context_clarification",
        route_after_unified,
        {
            "planning": "planning",
            END: END
        }
    )
    
    workflow.add_conditional_edges(
        "planning",
        route_after_planning,
        {
            "sql_synthesis_table": "sql_synthesis_table",
            "sql_synthesis_genie": "sql_synthesis_genie",
            "summarize": "summarize"
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_table",
        route_after_synthesis,
        {
            "sql_execution": "sql_execution",
            "summarize": "summarize"
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_genie",
        route_after_synthesis,
        {
            "sql_execution": "sql_execution",
            "summarize": "summarize"
        }
    )
    
    # SQL execution always goes to summarize
    workflow.add_edge("sql_execution", "summarize")
    
    # Summarize is the final node before END
    workflow.add_edge("summarize", END)
    
    return workflow


def create_agent_graph(config=None, with_checkpointer: bool = False):
    """
    Create and compile the agent graph.

    interrupt() requires a checkpointer to persist paused state. When
    with_checkpointer=False (local dev), MemorySaver is used so interrupt()
    works without Lakebase. When with_checkpointer=True (Databricks),
    DatabricksCheckpointSaver is used for durable cross-request persistence.

    Args:
        config: Optional configuration object (uses default if None)
        with_checkpointer: Use DatabricksCheckpointSaver instead of MemorySaver

    Returns:
        CompiledStateGraph
    """
    workflow = create_super_agent_hybrid(config)

    if with_checkpointer:
        from databricks.sdk import WorkspaceClient
        from databricks_langchain.checkpoint import DatabricksCheckpointSaver

        if config is None:
            from .config import get_config
            config = get_config()

        w = WorkspaceClient()
        checkpointer = DatabricksCheckpointSaver(
            w.lakebase, database_instance_name=config.lakebase.instance_name
        )
    else:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    return workflow.compile(checkpointer=checkpointer)


