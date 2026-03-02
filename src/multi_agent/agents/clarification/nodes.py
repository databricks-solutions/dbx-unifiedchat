"""
Node functions for the clarification sub-graph.

Each node has a single, focused responsibility and makes at most one LLM call.
All nodes read/write AgentState directly so the sub-graph shares state with
the parent graph without any intermediate TypedDict.

Sub-graph flow:
    classify_intent
        ↓
    classify_query_type
        ↓ route
        ├─ is_irrelevant=True  → handle_irrelevant → END
        ├─ is_meta_question=True → generate_meta_answer → END
        └─ else                →
               check_clarity
                  ↓ route
                  ├─ unclear (not rate-limited) → handle_clarification → END
                  └─ clear (or rate-limited)    → handle_clear        → END
"""

import json
import traceback
from datetime import timedelta
from typing import List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from ...core.state import (
    AgentState,
    ConversationTurn,
    IntentMetadata,
    create_conversation_turn,
    create_clarification_request,
)


# ---------------------------------------------------------------------------
# Space context loading (used by classify_query_type for meta-question answers)
# ---------------------------------------------------------------------------

_space_context_cache: dict = {"data": None, "timestamp": None, "table_name": None}
_SPACE_CONTEXT_CACHE_TTL = timedelta(minutes=30)


def load_space_context(table_name: str) -> dict:
    """
    Load Genie space summaries from Delta with 30-minute TTL caching.

    Returns:
        Dict mapping space_id → searchable_content
    """
    from datetime import datetime

    global _space_context_cache
    now = datetime.now()

    if (
        _space_context_cache["data"] is not None
        and _space_context_cache["table_name"] == table_name
        and _space_context_cache["timestamp"] is not None
        and now - _space_context_cache["timestamp"] < _SPACE_CONTEXT_CACHE_TTL
    ):
        age = (now - _space_context_cache["timestamp"]).total_seconds()
        print(f"✓ Using cached space context ({len(_space_context_cache['data'])} spaces, age: {age:.1f}s)")
        return _space_context_cache["data"]

    print("⚡ Loading space context from database...")
    try:
        from databricks.connect import DatabricksSession
        spark = DatabricksSession.builder.serverless().getOrCreate()
    except ImportError:
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()

    df = spark.sql(f"""
        SELECT space_id, searchable_content
        FROM {table_name}
        WHERE chunk_type = 'space_summary'
    """)
    context = {row["space_id"]: row["searchable_content"] for row in df.collect()}

    _space_context_cache.update({"data": context, "timestamp": now, "table_name": table_name})
    print(f"✓ Loaded {len(context)} Genie spaces into cache")
    return context


# ---------------------------------------------------------------------------
# Rate limit helper (pure Python)
# ---------------------------------------------------------------------------

def check_clarification_rate_limit(
    turn_history: List[ConversationTurn], window_size: int = 5
) -> bool:
    """
    Return True if clarification was already triggered within the last N turns.

    Args:
        turn_history: List of previous ConversationTurns
        window_size: How many recent turns to inspect

    Returns:
        True → rate limited, skip clarification; False → ok to clarify
    """
    if not turn_history:
        return False
    if turn_history[-1].get("triggered_clarification", False):
        return True
    if len(turn_history) < 2:
        return False
    for turn in turn_history[max(0, len(turn_history) - window_size):-1]:
        if turn.get("triggered_clarification", False):
            return True
    return False


# ---------------------------------------------------------------------------
# Node 1: classify_intent
# ---------------------------------------------------------------------------

def classify_intent_node(state: AgentState, llm, table_name: str) -> dict:
    """
    LLM call 1: Determine intent type and generate a context summary.

    Writes: current_turn, intent_metadata, turn_history (partial — no clarification flag yet)
    """
    writer = get_stream_writer()

    messages = state.get("messages", [])
    turn_history = state.get("turn_history", [])
    human_messages = [m for m in messages if isinstance(m, HumanMessage)]
    current_query = human_messages[-1].content if human_messages else ""

    writer({"type": "agent_start", "agent": "classify_intent", "query": current_query})
    print(f"\n[classify_intent] Query: {current_query!r}")

    # Format recent conversation context
    if turn_history:
        lines = []
        for i, turn in enumerate(turn_history[-5:], 1):
            label = turn["intent_type"].replace("_", " ").title()
            lines.append(f"{i}. [{label}] {turn['query']}")
            if turn.get("context_summary"):
                lines.append(f"   Context: {turn['context_summary'][:120]}...")
        conversation_context = "Previous conversation:\n" + "\n".join(lines)
    else:
        conversation_context = "No previous conversation (first query)."

    prompt = f"""Classify the intent of the user's query and generate a concise context summary.

Current Query: {current_query}

Conversation History:
{conversation_context}

## Task: Classify Intent
Choose ONE of:
- "new_question": Completely different topic from previous queries
- "refinement": Narrowing/filtering/modifying the previous query on the same topic
- "continuation": Follow-up exploring the same topic from a different angle
- "clarification_response": User is answering a previous clarification request

## Task: Generate Context Summary
Write 2-3 sentences that:
- Synthesize the conversation history
- State clearly what the user wants to know
- Are actionable for SQL query planning

Return ONLY valid JSON:
{{
  "intent_type": "new_question" | "refinement" | "continuation" | "clarification_response",
  "confidence": 0.0-1.0,
  "context_summary": "2-3 sentence summary for the planning agent",
  "metadata": {{
    "domain": "patients | claims | providers | medications | system | other",
    "complexity": "simple | moderate | complex",
    "topic_change_score": 0.0-1.0
  }}
}}
"""

    try:
        content = ""
        for chunk in llm.stream(prompt):
            if chunk.content:
                content += chunk.content

        # Strip optional markdown fences
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        result = json.loads(content.strip())
        intent_type = result["intent_type"].lower()
        confidence = result["confidence"]
        context_summary = result["context_summary"]
        metadata = result.get("metadata", {})

        print(f"[classify_intent] intent={intent_type} confidence={confidence:.2f}")

        turn = create_conversation_turn(
            query=current_query,
            intent_type=intent_type,
            context_summary=context_summary,
            triggered_clarification=False,
            metadata=metadata,
        )
        intent_metadata = IntentMetadata(
            intent_type=intent_type,
            confidence=confidence,
            reasoning=f"classify_intent: {intent_type}",
            topic_change_score=metadata.get("topic_change_score", 0.5),
            domain=metadata.get("domain"),
            operation=None,
            complexity=metadata.get("complexity", "moderate"),
            parent_turn_id=None,
        )

        writer({"type": "intent_detected", "intent_type": intent_type, "confidence": confidence})

        return {
            "current_turn": turn,
            "intent_metadata": intent_metadata,
            # turn_history updated in handle_* nodes after clarification flag is known
        }

    except Exception as e:
        print(f"[classify_intent] Error: {e}")
        traceback.print_exc()
        turn = create_conversation_turn(
            query=current_query,
            intent_type="new_question",
            context_summary=f"Query: {current_query}",
            triggered_clarification=False,
            metadata={},
        )
        return {
            "current_turn": turn,
            "intent_metadata": IntentMetadata(
                intent_type="new_question",
                confidence=0.5,
                reasoning=f"Error fallback: {e}",
                topic_change_score=1.0,
                domain=None,
                operation=None,
                complexity="moderate",
                parent_turn_id=None,
            ),
        }


# ---------------------------------------------------------------------------
# Node 2: classify_query_type
# ---------------------------------------------------------------------------

def classify_query_type_node(state: AgentState, llm, table_name: str) -> dict:
    """
    LLM call 2: Determine if the query is irrelevant or a meta-question.

    Writes: is_irrelevant, is_meta_question
    """
    writer = get_stream_writer()

    messages = state.get("messages", [])
    human_messages = [m for m in messages if isinstance(m, HumanMessage)]
    current_query = human_messages[-1].content if human_messages else ""

    space_context = load_space_context(table_name)

    prompt = f"""Classify whether this query is irrelevant to data analytics or a meta-question about the system.

User Query: {current_query}

Available Data Sources:
{json.dumps(space_context, indent=2)}

## Irrelevant Question
Set is_irrelevant=true for: greetings, small talk, weather, sports, politics, recipes, personal questions,
creative writing, role-playing, or anything completely unrelated to data analytics and business intelligence.

Examples: "What's the weather?", "Tell me a joke", "How do I make pasta?"

## Meta-Question
Set is_meta_question=true for: questions about available tables/data sources/schemas, system capabilities,
what data is available, or requests for example queries.

Examples: "What data do you have?", "What tables can I query?", "Give me example questions I can ask"

Return ONLY valid JSON:
{{
  "is_irrelevant": true | false,
  "is_meta_question": true | false
}}
"""

    try:
        content = ""
        for chunk in llm.stream(prompt):
            if chunk.content:
                content += chunk.content

        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        result = json.loads(content.strip())
        is_irrelevant = result.get("is_irrelevant", False)
        is_meta_question = result.get("is_meta_question", False)

        print(f"[classify_query_type] irrelevant={is_irrelevant} meta={is_meta_question}")
        writer({"type": "query_type_classified", "is_irrelevant": is_irrelevant, "is_meta_question": is_meta_question})

        return {"is_irrelevant": is_irrelevant, "is_meta_question": is_meta_question}

    except Exception as e:
        print(f"[classify_query_type] Error: {e}")
        return {"is_irrelevant": False, "is_meta_question": False}


# ---------------------------------------------------------------------------
# Node 3a: handle_irrelevant (Python only)
# ---------------------------------------------------------------------------

def handle_irrelevant_node(state: AgentState) -> dict:
    """
    No LLM call. Format a polite refusal and mark the turn in history.
    """
    writer = get_stream_writer()
    writer({"type": "irrelevant_question_detected"})
    print("[handle_irrelevant] Returning polite refusal")

    turn = state.get("current_turn", {})
    if turn:
        turn = dict(turn)
        turn.setdefault("metadata", {})["is_irrelevant"] = True

    refusal = (
        "I'm a data analytics assistant focused on helping you analyze and query "
        "the available data sources.\n\n"
        "I can help with questions about the data. To see what's available, try:\n"
        "- \"What data sources are available?\"\n"
        "- \"What tables can I query?\"\n"
        "- \"Show me example questions I can ask\"\n\n"
        "Could you rephrase your question to focus on analyzing the available data?"
    )

    return {
        "current_turn": turn,
        "turn_history": [turn] if turn else [],
        "question_clear": True,
        "is_irrelevant": True,
        "is_meta_question": False,
        "pending_clarification": None,
        "messages": [AIMessage(content=refusal)],
    }


# ---------------------------------------------------------------------------
# Node 3b: generate_meta_answer (LLM call)
# ---------------------------------------------------------------------------

def generate_meta_answer_node(state: AgentState, llm, table_name: str) -> dict:
    """
    LLM call 3 (meta path): Generate a helpful answer about available data.

    Writes: meta_answer, messages
    """
    writer = get_stream_writer()

    messages = state.get("messages", [])
    human_messages = [m for m in messages if isinstance(m, HumanMessage)]
    current_query = human_messages[-1].content if human_messages else ""

    space_context = load_space_context(table_name)

    prompt = f"""The user is asking about what data or capabilities are available.

User Query: {current_query}

Available Data Sources:
{json.dumps(space_context, indent=2)}

Provide a clear, informative markdown answer about what's available.
Use ## headings, **bold** keywords, and bullet lists.
Be professional and helpful.
"""

    writer({"type": "meta_question_detected"})
    print("[generate_meta_answer] Generating meta answer")

    try:
        content = ""
        for chunk in llm.stream(prompt):
            if chunk.content:
                content += chunk.content

        answer = content.strip()
    except Exception as e:
        print(f"[generate_meta_answer] Error: {e}")
        answer = "## Available Data Sources\n\nSorry, I encountered an error retrieving the data source information."

    turn = state.get("current_turn", {})
    if turn:
        turn = dict(turn)
        turn.setdefault("metadata", {})["is_meta_question"] = True

    return {
        "current_turn": turn,
        "turn_history": [turn] if turn else [],
        "question_clear": True,
        "is_meta_question": True,
        "meta_answer": answer,
        "pending_clarification": None,
        "messages": [AIMessage(content=answer)],
    }


# ---------------------------------------------------------------------------
# Node 4: check_clarity (LLM call + clarification handling)
#
# Merged with the former handle_clarification node to avoid passing temporary
# intermediate fields through AgentState. The LLM result is consumed in the
# same node that acts on it.
# ---------------------------------------------------------------------------

def check_clarity_node(state: AgentState, llm) -> dict:
    """
    LLM call 3 (regular path): Determine if the query is clear enough for SQL.
    If unclear, also applies the rate limit check and formats the clarification.

    Writes: question_clear, pending_clarification (if requesting clarification),
            current_turn, turn_history, messages
    """
    writer = get_stream_writer()

    messages = state.get("messages", [])
    human_messages = [m for m in messages if isinstance(m, HumanMessage)]
    current_query = human_messages[-1].content if human_messages else ""
    turn = state.get("current_turn") or {}
    turn_history = state.get("turn_history", [])
    context_summary = turn.get("context_summary", "")

    prompt = f"""Determine if the query is clear enough to generate a SQL query.

User Query: {current_query}
Context Summary: {context_summary}

## Task: Check Clarity
- Is the question clear and answerable as-is? (BE LENIENT — default to true)
- Only mark as unclear if CRITICAL information is missing
- If unclear, provide 2-3 specific clarification options
- Never mark a clarification_response as unclear

Return ONLY valid JSON:
{{
  "question_clear": true | false,
  "clarification_reason": "reason if unclear, otherwise null",
  "clarification_options": ["option 1", "option 2"] | null
}}
"""

    try:
        content = ""
        for chunk in llm.stream(prompt):
            if chunk.content:
                content += chunk.content

        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        result = json.loads(content.strip())
        question_clear = result.get("question_clear", True)
        clarification_reason = result.get("clarification_reason") or "Query needs more specificity"
        clarification_options = result.get("clarification_options") or []

        print(f"[check_clarity] clear={question_clear}")
        writer({"type": "clarity_analysis", "clear": question_clear})

    except Exception as e:
        print(f"[check_clarity] Error (defaulting to clear): {e}")
        question_clear = True
        clarification_reason = "Query needs more specificity"
        clarification_options = []

    if question_clear:
        return {
            "current_turn": turn,
            "turn_history": [turn] if turn else [],
            "question_clear": True,
            "pending_clarification": None,
        }

    # Unclear — check rate limit
    is_rate_limited = check_clarification_rate_limit(turn_history, window_size=5)

    if is_rate_limited:
        print("[check_clarity] Rate limited — proceeding with best effort")
        writer({"type": "clarification_skipped", "reason": "Rate limited (1 per 5 turns)"})
        return {
            "current_turn": turn,
            "turn_history": [turn] if turn else [],
            "question_clear": True,
            "pending_clarification": None,
            "messages": [SystemMessage(content=f"Clarification rate limited. Proceeding with: {context_summary}")],
        }

    # Request clarification
    print("[check_clarity] Requesting clarification")
    writer({"type": "clarification_requested"})

    turn = dict(turn)
    turn["triggered_clarification"] = True

    clarification_request = create_clarification_request(
        reason=clarification_reason,
        options=clarification_options,
        turn_id=turn.get("turn_id", ""),
        best_guess=context_summary,
        best_guess_confidence=(state.get("intent_metadata") or {}).get("confidence"),
    )

    markdown = f"### Clarification Needed\n\n{clarification_reason}\n\n"
    if clarification_options:
        markdown += "**Please choose from the following options:**\n\n"
        for i, opt in enumerate(clarification_options, 1):
            markdown += f"{i}. {opt}\n\n"

    return {
        "current_turn": turn,
        "turn_history": [turn] if turn else [],
        "question_clear": False,
        "pending_clarification": clarification_request,
        "messages": [AIMessage(content=markdown.strip())],
    }


# ---------------------------------------------------------------------------
# Node 5: handle_clear (Python only)
# ---------------------------------------------------------------------------

def handle_clear_node(state: AgentState) -> dict:
    """
    No LLM call. Confirm the query is clear and forward to planning.
    """
    writer = get_stream_writer()
    writer({"type": "clarity_analysis", "clear": True, "reasoning": "Query is clear and answerable"})
    print("[handle_clear] Query is clear — proceeding to planning")

    turn = state.get("current_turn", {})
    intent_type = (state.get("intent_metadata") or {}).get("intent_type", "new_question")

    return {
        "current_turn": turn,
        "turn_history": [turn] if turn else [],
        "question_clear": True,
        "pending_clarification": None,
        "messages": [SystemMessage(content=f"Intent: {intent_type}, proceeding to planning")],
    }
