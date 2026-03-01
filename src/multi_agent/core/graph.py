"""
LangGraph workflow construction for the multi-agent system.

This module defines the graph structure, routing logic, and workflow compilation.
"""

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from typing import Optional

from ..core.state import AgentState
from ..agents.clarification import unified_intent_context_clarification_node
from ..agents.planning import planning_node
from ..agents.sql_synthesis import sql_synthesis_table_node, sql_synthesis_genie_node
from ..agents.sql_execution import sql_execution_node
from ..agents.summarize import summarize_node


def create_super_agent_hybrid() -> StateGraph:
    """
    Create the Hybrid Super Agent LangGraph workflow.
    
    Combines:
    - Function-based agent nodes for flexibility
    - Explicit state management for observability
    - Conditional routing for dynamic workflows
    
    Returns:
        StateGraph: Uncompiled LangGraph workflow
    """
    print("\n" + "="*80)
    print("🏗️ BUILDING HYBRID SUPER AGENT WORKFLOW")
    print("="*80)
    
    # Create the graph with explicit state
    workflow = StateGraph(AgentState)
    
    # Add nodes - SIMPLIFIED with unified node
    workflow.add_node("unified_intent_context_clarification", unified_intent_context_clarification_node)
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
    
    print("✓ Workflow nodes added:")
    print("  1. Unified Intent+Context+Clarification Node")
    print("  2. Planning Agent")
    print("  3. SQL Synthesis Agent - Table Route")
    print("  4. SQL Synthesis Agent - Genie Route")
    print("  5. SQL Execution Agent")
    print("  6. Result Summarize Agent - FINAL NODE")
    print("\n✓ Conditional routing configured")
    print("✓ All paths route to summarize node before END")
    print("\n✅ Hybrid Super Agent workflow created successfully!")
    print("="*80)
    
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
    workflow = create_super_agent_hybrid()
    
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


# For backwards compatibility
super_agent_hybrid = create_super_agent_hybrid()
