"""
State management for the multi-agent system.

This module defines all state types and helper functions for managing
conversation state across agents.
"""

from typing import TypedDict, Optional, List, Dict, Any, Literal, Annotated
from datetime import datetime
import operator
import uuid as uuid_module


class ConversationTurn(TypedDict):
    """
    Represents a single conversation turn with all its context.
    """
    turn_id: str
    query: str
    intent_type: Literal["new_question", "refinement", "continuation", "clarification_response"]
    parent_turn_id: Optional[str]
    context_summary: Optional[str]
    timestamp: str  # ISO format datetime string
    triggered_clarification: Optional[bool]
    metadata: Optional[Dict[str, Any]]


class ClarificationRequest(TypedDict):
    """Unified clarification request object."""
    reason: str
    options: List[str]
    turn_id: str
    timestamp: str
    best_guess: Optional[str]
    best_guess_confidence: Optional[float]


class IntentMetadata(TypedDict):
    """Intent metadata for business logic."""
    intent_type: Literal["new_question", "refinement", "continuation", "clarification_response"]
    confidence: float
    reasoning: str
    topic_change_score: float
    domain: Optional[str]
    operation: Optional[str]
    complexity: Literal["simple", "moderate", "complex"]
    parent_turn_id: Optional[str]


class AgentState(TypedDict):
    """Simplified agent state using turn-based context management."""
    # Turn Management
    current_turn: Optional[ConversationTurn]
    turn_history: Annotated[List[ConversationTurn], operator.add]
    intent_metadata: Optional[IntentMetadata]
    
    # Clarification
    pending_clarification: Optional[ClarificationRequest]
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
    sql_queries: Optional[List[str]]  # Multi-part question: list of all SQL queries
    sql_query_labels: Optional[List[str]]  # Multi-part question: per-query labels
    sql_synthesis_explanation: Optional[str]
    synthesis_error: Optional[str]
    has_sql: Optional[bool]  # Whether SQL was successfully extracted
    
    # Execution
    execution_result: Optional[Dict[str, Any]]
    execution_results: Optional[List[Dict[str, Any]]]  # Multi-part question: list of all execution results
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
    intent_type: Literal["new_question", "refinement", "continuation", "clarification_response"],
    parent_turn_id: Optional[str] = None,
    context_summary: Optional[str] = None,
    triggered_clarification: bool = False,
    metadata: Optional[Dict[str, Any]] = None
) -> ConversationTurn:
    """
    Factory function to create a ConversationTurn with runtime validation.
    
    Args:
        query: User's query string
        intent_type: Must be one of: "new_question", "refinement", "continuation", "clarification_response"
        parent_turn_id: Optional parent turn ID for context
        context_summary: Optional summary of conversation context
        triggered_clarification: Whether this turn triggered clarification
        metadata: Optional additional metadata
        
    Returns:
        ConversationTurn with validated intent_type
        
    Raises:
        ValueError: If intent_type is not one of the allowed values
    """
    # Runtime validation to enforce type contract
    valid_intent_types = {"new_question", "refinement", "continuation", "clarification_response"}
    if intent_type not in valid_intent_types:
        raise ValueError(
            f"Invalid intent_type: '{intent_type}'. "
            f"Must be one of: {valid_intent_types}."
        )
    
    return ConversationTurn(
        turn_id=str(uuid_module.uuid4()),
        query=query,
        intent_type=intent_type,
        parent_turn_id=parent_turn_id,
        context_summary=context_summary,
        timestamp=datetime.utcnow().isoformat(),
        triggered_clarification=triggered_clarification,
        metadata=metadata or {}
    )


def create_clarification_request(
    reason: str,
    options: List[str],
    turn_id: str,
    best_guess: Optional[str] = None,
    best_guess_confidence: Optional[float] = None
) -> ClarificationRequest:
    """Factory function to create a ClarificationRequest."""
    return ClarificationRequest(
        reason=reason,
        options=options,
        turn_id=turn_id,
        timestamp=datetime.utcnow().isoformat(),
        best_guess=best_guess,
        best_guess_confidence=best_guess_confidence
    )


def format_clarification_message(clarification: ClarificationRequest) -> str:
    """Format a clarification request into a user-friendly message."""
    message = f"I need clarification: {clarification['reason']}\n\n"
    message += "Please choose one of the following options or provide your own clarification:\n"
    for i, option in enumerate(clarification['options'], 1):
        message += f"{i}. {option}\n"
    return message


def get_reset_state_template() -> Dict[str, Any]:
    """
    Get template for resetting per-query execution fields.
    
    This prevents stale data from persisting across queries when using CheckpointSaver.
    Used by both Model Serving and local testing.
    
    NOTE: Turn-based fields (current_turn, turn_history, intent_metadata) are NOT reset.
    They are managed by unified_intent_context_clarification_node and persist across queries.
    """
    return {
        # Clarification fields (per-query)
        "pending_clarification": None,
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
    """
    Create initial agent state.
    
    Args:
        user_id: Optional user identifier
        thread_id: Optional thread identifier
        messages: Optional initial messages
        
    Returns:
        Initial AgentState with reset template applied
    """
    state = AgentState(
        # Turn management
        current_turn=None,
        turn_history=[],
        intent_metadata=None,
        
        # User/conversation context
        user_id=user_id,
        thread_id=thread_id or str(uuid_module.uuid4()),
        user_preferences={},
        messages=messages or [],
        next_agent=None,
        
        # Apply reset template for per-query fields
        **RESET_STATE_TEMPLATE
    )
    
    return state


def reset_per_query_state(state: AgentState) -> Dict[str, Any]:
    """
    Reset per-query execution fields while preserving conversation context.
    
    Args:
        state: Current agent state
        
    Returns:
        Update dict with reset fields
    """
    return RESET_STATE_TEMPLATE.copy()
