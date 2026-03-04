"""
State management for the multi-agent system.

This module defines all state types and helper functions for managing
conversation state across agents.
"""

from typing import TypedDict, Optional, List, Dict, Any, Annotated
from datetime import datetime
import operator
import uuid as uuid_module


class ConversationTurn(TypedDict):
    """Represents a single conversation turn with all its context."""
    turn_id: str
    query: str
    parent_turn_id: Optional[str]
    context_summary: Optional[str]
    timestamp: str  # ISO format datetime string
    triggered_clarification: Optional[bool]
    metadata: Optional[Dict[str, Any]]


class GraphInput(TypedDict):
    """Minimal input schema exposed to LangGraph Studio / API callers."""
    messages: Annotated[List[Any], operator.add]


class AgentState(TypedDict):
    """Simplified agent state using turn-based context management."""
    # Turn Management
    current_turn: Optional[ConversationTurn]
    turn_history: Annotated[List[ConversationTurn], operator.add]

    # Clarification
    question_clear: bool

    # Meta-question handling
    is_meta_question: Optional[bool]
    meta_answer: Optional[str]

    # Irrelevant question handling
    is_irrelevant: Optional[bool]

    # Deprecated (kept for backward compatibility)
    original_query: Optional[str]

    # Planning
    plan: Optional[Dict[str, Any]]
    sub_questions: Optional[List[str]]
    requires_multiple_spaces: Optional[bool]
    relevant_space_ids: Optional[List[str]]
    relevant_spaces: Optional[List[Dict[str, Any]]]
    vector_search_relevant_spaces_info: Optional[List[Dict[str, str]]]
    requires_join: Optional[bool]
    join_strategy: Optional[str]
    execution_plan: Optional[str]
    genie_route_plan: Optional[Dict[str, str]]

    # SQL Synthesis
    sql_query: Optional[str]
    sql_queries: Optional[List[str]]
    sql_query_labels: Optional[List[str]]
    sql_synthesis_explanation: Optional[str]
    synthesis_error: Optional[str]
    has_sql: Optional[bool]

    # Execution
    execution_result: Optional[Dict[str, Any]]
    execution_results: Optional[List[Dict[str, Any]]]
    execution_error: Optional[str]

    # Summary
    final_summary: Optional[str]

    # Conversation Management
    user_id: Optional[str]
    thread_id: Optional[str]
    user_preferences: Optional[Dict[str, Any]]

    # Control Flow
    next_agent: Optional[str]
    messages: Annotated[List, operator.add]


# Helper Functions

def create_conversation_turn(
    query: str,
    parent_turn_id: Optional[str] = None,
    context_summary: Optional[str] = None,
    triggered_clarification: bool = False,
    metadata: Optional[Dict[str, Any]] = None
) -> ConversationTurn:
    """Factory function to create a ConversationTurn."""
    return ConversationTurn(
        turn_id=str(uuid_module.uuid4()),
        query=query,
        parent_turn_id=parent_turn_id,
        context_summary=context_summary,
        timestamp=datetime.utcnow().isoformat(),
        triggered_clarification=triggered_clarification,
        metadata=metadata or {}
    )


def get_reset_state_template() -> Dict[str, Any]:
    """
    Get template for resetting per-query execution fields.

    Prevents stale data from persisting across queries when using CheckpointSaver.
    NOTE: Turn-based fields (current_turn, turn_history) are NOT reset — they are
    managed by unified_intent_context_clarification and persist across queries.
    """
    return {
        # Clarification fields (per-query)
        "question_clear": False,
        "is_meta_question": False,
        "meta_answer": None,
        "is_irrelevant": False,

        # Planning fields (per-query)
        "plan": None,
        "sub_questions": None,
        "requires_multiple_spaces": None,
        "relevant_space_ids": None,
        "relevant_spaces": None,
        "vector_search_relevant_spaces_info": None,
        "requires_join": None,
        "join_strategy": None,
        "execution_plan": None,
        "genie_route_plan": None,

        # SQL fields (per-query)
        "sql_query": None,
        "sql_queries": None,
        "sql_query_labels": None,
        "sql_synthesis_explanation": None,
        "synthesis_error": None,
        "has_sql": None,

        # Execution fields (per-query)
        "execution_result": None,
        "execution_results": None,
        "execution_error": None,

        # Summary (per-query)
        "final_summary": None,
    }


# Reset state template - singleton
RESET_STATE_TEMPLATE = get_reset_state_template()


def get_initial_state(
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    messages: Optional[List] = None
) -> AgentState:
    """Create initial agent state."""
    return AgentState(
        current_turn=None,
        turn_history=[],
        user_id=user_id,
        thread_id=thread_id or str(uuid_module.uuid4()),
        user_preferences={},
        messages=messages or [],
        next_agent=None,
        **RESET_STATE_TEMPLATE
    )


def reset_per_query_state(state: AgentState) -> Dict[str, Any]:
    """Reset per-query execution fields while preserving conversation context."""
    return RESET_STATE_TEMPLATE.copy()
