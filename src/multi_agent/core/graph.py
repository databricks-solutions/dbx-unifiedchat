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
        """Route after unified node: planning or END (clarification/meta-question/irrelevant)"""
        # Check if irrelevant question - go directly to END with refusal
        if state.get("is_irrelevant", False):
            return END
        
        # Check if meta-question - go directly to END with answer
        if state.get("is_meta_question", False):
            return END
        
        # Check if question is clear - proceed to planning
        if state.get("question_clear", False):
            return "planning"
        
        # Otherwise, end for clarification
        return END
    
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
    Create and optionally compile the agent graph.
    
    Args:
        config: Optional configuration object (uses default if None)
        with_checkpointer: Whether to compile with checkpointer
        
    Returns:
        StateGraph or CompiledStateGraph depending on with_checkpointer
    """
    workflow = create_super_agent_hybrid(config)
    
    if with_checkpointer:
        # Import checkpointer only if needed
        from databricks_langchain.checkpoint import DatabricksCheckpointSaver
        from databricks_langchain.store import DatabricksStore
        from databricks.sdk import WorkspaceClient
        
        # Get Lakebase instance name from config
        if config:
            lakebase_instance = config.lakebase.instance_name
            embedding_endpoint = config.lakebase.embedding_endpoint
            embedding_dims = config.lakebase.embedding_dims
        else:
            # Use defaults
            from .config import get_config
            cfg = get_config()
            lakebase_instance = cfg.lakebase.instance_name
            embedding_endpoint = cfg.lakebase.embedding_endpoint
            embedding_dims = cfg.lakebase.embedding_dims
        
        # Create checkpointer
        w = WorkspaceClient()
        checkpointer = DatabricksCheckpointSaver(w.lakebase, database_instance_name=lakebase_instance)
        
        # Compile with checkpointer
        return workflow.compile(checkpointer=checkpointer)
    else:
        return workflow.compile()


