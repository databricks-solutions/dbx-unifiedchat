"""
Conversation Models for Turn-Based Context Management

This module defines the core data models for managing multi-turn conversations
with explicit turn tracking, intent detection, and clarification handling.

Design Principles:
- Explicit turn tracking instead of global state variables
- Intent detection as first-class citizen
- Simplified clarification state (single object vs 7+ fields)
- Clear separation of concerns for maintainability
"""

from typing import TypedDict, Optional, List, Dict, Any, Literal, Annotated
from datetime import datetime
import operator


# ==============================================================================
# Core Conversation Models
# ==============================================================================

class ConversationTurn(TypedDict):
    """
    Represents a single conversation turn with all its context.
    
    This replaces the need for:
    - clarification_count (use turn_history)
    - last_clarified_query (use turn_history with pending_clarification)
    - combined_query_context (use context_summary)
    
    Attributes:
        turn_id: Unique identifier for this turn (UUID)
        query: User's query for this turn
        intent_type: Classification of query intent
        parent_turn_id: Links to previous turn if related (for refinements/follow-ups)
        context_summary: LLM-generated summary of relevant conversation history
        timestamp: When this turn occurred
        triggered_clarification: Whether this turn resulted in a clarification request
        metadata: Additional turn-specific metadata (extensible)
    """
    turn_id: str
    query: str
    intent_type: Literal["new_question", "refinement", "clarification_response", "continuation"]
    parent_turn_id: Optional[str]
    context_summary: Optional[str]
    timestamp: str  # ISO format datetime string for JSON serialization
    triggered_clarification: Optional[bool]
    metadata: Optional[Dict[str, Any]]


class ClarificationRequest(TypedDict):
    """
    All clarification data in one unified object.
    
    This replaces the need for:
    - clarification_needed (str)
    - clarification_options (List[str])
    - clarification_message (str)
    - clarification_count (int)
    
    Attributes:
        reason: Why clarification is needed
        options: List of suggested clarification options for the user
        turn_id: Which turn triggered this clarification
        timestamp: When clarification was requested
        best_guess: Agent's best interpretation if user doesn't clarify
        best_guess_confidence: Confidence in best_guess (0.0-1.0)
    """
    reason: str
    options: List[str]
    turn_id: str
    timestamp: str  # ISO format datetime string
    best_guess: Optional[str]
    best_guess_confidence: Optional[float]


class IntentMetadata(TypedDict):
    """
    Business logic metadata from intent detection service.
    
    Enables:
    - Billing (track new questions vs refinements)
    - Analytics (conversation patterns, topic changes)
    - Routing (route complex queries differently)
    - Personalization (adapt based on user patterns)
    
    Attributes:
        intent_type: Classification of query intent
        confidence: Confidence in intent classification (0.0-1.0)
        reasoning: Brief explanation of classification
        topic_change_score: How much topic changed from previous turn (0.0-1.0)
        domain: Data domain (patients, claims, providers, etc.)
        operation: Type of operation (aggregate, filter, compare, etc.)
        complexity: Query complexity level
        parent_turn_id: ID of related previous turn (if applicable)
    """
    intent_type: Literal["new_question", "refinement", "clarification_response", "continuation"]
    confidence: float
    reasoning: str
    topic_change_score: float
    domain: Optional[str]
    operation: Optional[str]
    complexity: Literal["simple", "moderate", "complex"]
    parent_turn_id: Optional[str]


# ==============================================================================
# Agent State (Simplified)
# ==============================================================================

class AgentState(TypedDict):
    """
    Simplified agent state with turn-based context management.
    
    SIMPLIFIED: Reduced from 20+ fields to ~15 core fields by consolidating:
    - 7 clarification fields → 1 (pending_clarification)
    - 3 intent/context fields → 3 (current_turn, turn_history, intent_metadata)
    
    State Categories:
    1. Turn Management (NEW)
    2. Clarification (SIMPLIFIED)
    3. Planning
    4. SQL Synthesis
    5. Execution
    6. Summary
    7. Conversation Management
    8. Control Flow
    """
    
    # -------------------------------------------------------------------------
    # Turn Management (NEW - replaces clarification_count, last_clarified_query, 
    #                       combined_query_context)
    # -------------------------------------------------------------------------
    # NOTE: current_turn and intent_metadata are Optional because intent_detection_node
    # (the workflow entry point) creates them. If they were required fields, initial_state
    # would need to include them, but that would prevent proper state updates from propagating.
    current_turn: Optional[ConversationTurn]
    turn_history: Annotated[List[ConversationTurn], operator.add]  # Append-only with reducer
    intent_metadata: Optional[IntentMetadata]
    
    # -------------------------------------------------------------------------
    # Deprecated (Backward Compatibility)
    # -------------------------------------------------------------------------
    original_query: Optional[str]  # DEPRECATED: Use messages array or current_turn.query instead
    
    # -------------------------------------------------------------------------
    # Clarification (SIMPLIFIED from 7 fields to 2)
    # -------------------------------------------------------------------------
    pending_clarification: Optional[ClarificationRequest]
    question_clear: bool  # Legacy field for routing logic
    
    # -------------------------------------------------------------------------
    # Meta-question handling
    # -------------------------------------------------------------------------
    is_meta_question: Optional[bool]
    meta_answer: Optional[str]
    
    # -------------------------------------------------------------------------
    # Irrelevant question handling
    # -------------------------------------------------------------------------
    is_irrelevant: Optional[bool]
    
    # -------------------------------------------------------------------------
    # Planning
    # -------------------------------------------------------------------------
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
    
    # -------------------------------------------------------------------------
    # SQL Synthesis
    # -------------------------------------------------------------------------
    sql_query: Optional[str]
    sql_queries: Optional[List[str]]  # Multi-part question: list of all SQL queries
    sql_query_labels: Optional[List[str]]  # Multi-part question: per-query labels
    sql_synthesis_explanation: Optional[str]
    synthesis_error: Optional[str]
    has_sql: Optional[bool]  # Whether SQL was successfully extracted
    
    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------
    execution_result: Optional[Dict[str, Any]]
    execution_results: Optional[List[Dict[str, Any]]]  # Multi-part question: list of all execution results
    execution_error: Optional[str]
    
    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    final_summary: Optional[str]
    
    # -------------------------------------------------------------------------
    # Conversation Management (for distributed serving and long-term memory)
    # -------------------------------------------------------------------------
    user_id: Optional[str]
    thread_id: Optional[str]
    user_preferences: Optional[Dict[str, Any]]
    
    # -------------------------------------------------------------------------
    # Control Flow
    # -------------------------------------------------------------------------
    next_agent: Optional[str]
    messages: Annotated[List, operator.add]


# ==============================================================================
# State Reset Template
# ==============================================================================

def get_reset_state_template() -> Dict[str, Any]:
    """
    Get template for resetting per-query execution fields.
    
    This prevents stale data from persisting across queries when using 
    CheckpointSaver in distributed serving scenarios.
    
    Fields intentionally NOT reset:
    - current_turn, turn_history: Managed by intent detection node
    - intent_metadata: Set per turn by intent detection
    - messages: Managed by operator.add, persists across turns
    - user_id, thread_id, user_preferences: Identity/context, persists
    - original_query: Set in initial_state, not included in reset (deprecated)
    
    Returns:
        Dictionary with per-query fields reset to None/default values
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


# ==============================================================================
# Helper Functions
# ==============================================================================

def create_conversation_turn(
    query: str,
    intent_type: Literal["new_question", "refinement", "clarification_response", "continuation"],
    parent_turn_id: Optional[str] = None,
    context_summary: Optional[str] = None,
    triggered_clarification: bool = False,
    metadata: Optional[Dict[str, Any]] = None
) -> ConversationTurn:
    """
    Factory function to create a ConversationTurn with proper defaults.
    
    Args:
        query: User's query for this turn
        intent_type: Classification of query intent
        parent_turn_id: Links to previous turn if related
        context_summary: LLM-generated summary of relevant history
        triggered_clarification: Whether this turn resulted in clarification request
        metadata: Additional turn-specific metadata
    
    Returns:
        Properly initialized ConversationTurn object
    """
    import uuid
    
    return ConversationTurn(
        turn_id=str(uuid.uuid4()),
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
    """
    Factory function to create a ClarificationRequest with proper defaults.
    
    Args:
        reason: Why clarification is needed
        options: List of suggested clarification options
        turn_id: Which turn triggered this clarification
        best_guess: Agent's best interpretation if user doesn't clarify
        best_guess_confidence: Confidence in best_guess (0.0-1.0)
    
    Returns:
        Properly initialized ClarificationRequest object
    """
    return ClarificationRequest(
        reason=reason,
        options=options,
        turn_id=turn_id,
        timestamp=datetime.utcnow().isoformat(),
        best_guess=best_guess,
        best_guess_confidence=best_guess_confidence
    )


def find_turn_by_id(turn_history: List[ConversationTurn], turn_id: Optional[str]) -> Optional[ConversationTurn]:
    """
    Find a specific turn in the history by its ID.
    
    Args:
        turn_history: List of conversation turns
        turn_id: ID of turn to find
    
    Returns:
        ConversationTurn if found, None otherwise
    """
    if not turn_id:
        return None
    
    for turn in reversed(turn_history):  # Search from most recent
        if turn["turn_id"] == turn_id:
            return turn
    
    return None


def format_clarification_message(clarification: ClarificationRequest) -> str:
    """
    Format a clarification request into a user-friendly message.
    
    Args:
        clarification: ClarificationRequest object
    
    Returns:
        Formatted message string
    """
    message = f"I need clarification: {clarification['reason']}\n\n"
    message += "Please choose one of the following options or provide your own clarification:\n"
    
    for i, option in enumerate(clarification['options'], 1):
        message += f"{i}. {option}\n"
    
    return message


def get_recent_turn_summary(turn_history: List[ConversationTurn], max_turns: int = 5) -> str:
    """
    Generate a summary of recent conversation turns.
    
    Args:
        turn_history: List of conversation turns
        max_turns: Maximum number of recent turns to include
    
    Returns:
        Formatted summary string
    """
    if not turn_history:
        return "No previous conversation history."
    
    recent_turns = turn_history[-max_turns:]
    
    summary = "Recent Conversation:\n"
    for i, turn in enumerate(recent_turns, 1):
        intent = turn['intent_type'].replace('_', ' ').title()
        summary += f"{i}. [{intent}] {turn['query']}\n"
    
    return summary


def get_topic_root(turn_history: List[ConversationTurn], turn: ConversationTurn) -> ConversationTurn:
    """
    Find the root new_question for a given turn by traversing parent_turn_id.
    
    This enables topic isolation by identifying where a conversation topic started.
    
    Logic:
    - new_question: Returns itself (it is the root)
    - refinement/continuation: Traverses up parent_turn_id chain to find new_question
    - clarification_response: Traverses to find the parent that triggered clarification
    
    Args:
        turn_history: List of all conversation turns
        turn: The turn to find the root for
    
    Returns:
        The root ConversationTurn (always a new_question)
    
    Example:
        Turn 1: "Show patients" [new_question]
        Turn 2: "Age 50+" [refinement, parent=Turn1]
        Turn 3: "By state" [refinement, parent=Turn1]
        
        get_topic_root(history, Turn3) -> Turn1
    """
    # If this is a new_question, it is the root
    if turn['intent_type'] == 'new_question':
        return turn
    
    # Traverse up the parent chain to find the root new_question
    current = turn
    visited = set()  # Prevent infinite loops
    
    while current.get('parent_turn_id'):
        # Prevent infinite loops
        if current['turn_id'] in visited:
            print(f"⚠ Circular parent reference detected for turn {current['turn_id']}")
            break
        visited.add(current['turn_id'])
        
        # Find parent turn
        parent = find_turn_by_id(turn_history, current['parent_turn_id'])
        
        if not parent:
            # Parent not found, return current as root
            print(f"⚠ Parent turn {current['parent_turn_id']} not found, using current as root")
            break
        
        # If parent is new_question, we found the root
        if parent['intent_type'] == 'new_question':
            return parent
        
        # Continue traversing up
        current = parent
    
    # Fallback: return the current turn if no root found
    return current


def get_current_topic_turns(
    turn_history: List[ConversationTurn],
    current_turn: ConversationTurn,
    max_recent: int = 3
) -> List[ConversationTurn]:
    """
    Get turns from current topic only (strict topic isolation).
    
    Strategy: Root question + last N recent turns from same topic
    - Find root new_question via parent_turn_id traversal
    - Collect all descendant turns (refinements, clarifications, continuations)
    - Return: [root] + recent_descendants[-max_recent:]
    
    This ensures strict isolation: Question 1 and Question 2 contexts never mix.
    
    Args:
        turn_history: List of all conversation turns
        current_turn: The current turn to get context for
        max_recent: Maximum number of recent descendant turns to include (default: 3)
    
    Returns:
        List of turns scoped to current topic [root, ...recent_descendants]
    
    Example with max_recent=3:
        Turn 1: "Show patients" [new_question]
        Turn 2: "Age 50+" [refinement, parent=Turn1]
        Turn 3: "By state" [refinement, parent=Turn1]
        Turn 4: "Show medications" [new_question] ← NEW TOPIC
        Turn 5: "Diabetes filter" [refinement, parent=Turn4]
        
        get_current_topic_turns(history, Turn5, max_recent=3)
        Returns: [Turn 4 (root), Turn 5 (current)]
        
        Turn 1-3 are from Question 1 → EXCLUDED (strict isolation)
    """
    if not turn_history:
        return []
    
    # Find the root new_question for this turn
    root_turn = get_topic_root(turn_history, current_turn)
    root_turn_id = root_turn['turn_id']
    
    # Collect all turns that belong to this topic
    # (turns with parent chain leading back to this root)
    topic_turns = []
    
    for turn in turn_history:
        # Check if this turn belongs to the current topic
        if turn['turn_id'] == root_turn_id:
            # This is the root itself
            topic_turns.append(turn)
        elif turn['intent_type'] == 'new_question':
            # This is a different topic root, skip
            continue
        else:
            # Check if this turn's root matches our root
            turn_root = get_topic_root(turn_history, turn)
            if turn_root['turn_id'] == root_turn_id:
                topic_turns.append(turn)
    
    # Strategy: Root + last N recent descendants
    if len(topic_turns) <= 1:
        # Only root or empty
        return topic_turns
    
    # Split into root and descendants
    root = [topic_turns[0]]  # First turn is always root
    descendants = topic_turns[1:]
    
    # Take last max_recent descendants
    recent_descendants = descendants[-max_recent:] if len(descendants) > max_recent else descendants
    
    # Return root + recent descendants
    return root + recent_descendants
