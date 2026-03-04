"""
ClarificationAgent: Graph-based sub-agent for intent, classification, and clarity.

Sub-graph flow (classify_intent and classify_query_type run in parallel):

    START
      ├── classify_intent          (structured output)
      └── classify_query_type      (structured output)
              ↓ fan-in
          merge_classification
              ↓ route
              ├─ is_irrelevant=True    → handle_irrelevant    → END
              ├─ is_meta_question=True → generate_meta_answer → END
              └─ else                 → check_clarity
                                            ↓
                                            ├─ unclear → interrupt() ← waits for user
                                            │           resumes with user_response
                                            │                ↓
                                            │         confirm_continuation
                                            │           ├─ answering clarification → handle_clear → END
                                            │           └─ new question → classify_intent (loop back)
                                            └─ clear   → confirm_continuation → handle_clear → END
"""

import json
from datetime import datetime, timedelta
from typing import List, Literal, Optional

from databricks_langchain import ChatDatabricks
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from ..core.base_agent import BaseAgent
from ..core.state import (
    AgentState,
    ConversationTurn,
    IntentMetadata,
    create_conversation_turn,
)


# ---------------------------------------------------------------------------
# Schemas for structured LLM output (TypedDict — consistent with state.py)
# ---------------------------------------------------------------------------

class IntentClassification(TypedDict):
    intent_type: Literal["new_question", "refinement", "continuation", "clarification_response"]
    confidence: float
    context_summary: str
    domain: str
    complexity: Literal["simple", "moderate", "complex"]
    topic_change_score: float


class QueryTypeClassification(TypedDict):
    is_irrelevant: bool
    is_meta_question: bool


class ClarityCheck(TypedDict):
    question_clear: bool
    clarification_reason: Optional[str]
    clarification_options: Optional[List[str]]


class ContinuationCheck(TypedDict):
    is_clarification_response: bool
    reasoning: str


# ---------------------------------------------------------------------------
# Module-level helpers (stateless, no class dependency)
# ---------------------------------------------------------------------------

_space_context_cache: dict = {"data": None, "timestamp": None, "table_name": None}
_SPACE_CONTEXT_CACHE_TTL = timedelta(minutes=30)


def load_space_context(table_name: str) -> dict:
    """Load Genie space summaries from Delta with 30-minute TTL caching."""
    global _space_context_cache
    now = datetime.now()

    if (
        _space_context_cache["data"] is not None
        and _space_context_cache["table_name"] == table_name
        and _space_context_cache["timestamp"] is not None
        and now - _space_context_cache["timestamp"] < _SPACE_CONTEXT_CACHE_TTL
    ):
        age = (now - _space_context_cache["timestamp"]).total_seconds()
        print(f"[space_context] cache hit ({len(_space_context_cache['data'])} spaces, age: {age:.1f}s)")
        return _space_context_cache["data"]

    print("[space_context] loading from database")
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
    print(f"[space_context] loaded {len(context)} spaces")
    return context


# ---------------------------------------------------------------------------
# ClarificationAgent
# ---------------------------------------------------------------------------

class ClarificationAgent(BaseAgent):
    """
    Clarification sub-agent implemented as a compiled LangGraph sub-graph.

    Node methods are private to this class — they access LLMs and config via
    self rather than being threaded through partial().
    """

    def __init__(self, llm_endpoint: str, table_name: str):
        super().__init__("clarification")
        self.table_name = table_name
        self.llm_endpoint = llm_endpoint

        base_llm = ChatDatabricks(endpoint=llm_endpoint, temperature=0.1)
        self.intent_llm = base_llm.with_structured_output(IntentClassification)
        self.query_type_llm = base_llm.with_structured_output(QueryTypeClassification)
        self.clarity_llm = base_llm.with_structured_output(ClarityCheck)
        self.continuation_llm = base_llm.with_structured_output(ContinuationCheck)
        self.base_llm = base_llm

        self.subgraph = self._build_subgraph()

    # -----------------------------------------------------------------------
    # Graph construction
    # -----------------------------------------------------------------------

    def _build_subgraph(self):
        graph = StateGraph(AgentState)

        graph.add_node("classify_intent", self._classify_intent)
        graph.add_node("classify_query_type", self._classify_query_type)
        graph.add_node("merge_classification", self._merge_classification)
        graph.add_node("handle_irrelevant", self._handle_irrelevant)
        graph.add_node("generate_meta_answer", self._generate_meta_answer)
        graph.add_node("check_clarity", self._check_clarity)
        graph.add_node("confirm_continuation", self._confirm_continuation)
        graph.add_node("handle_clear", self._handle_clear)

        # Parallel fan-out
        graph.add_edge(START, "classify_intent")
        graph.add_edge(START, "classify_query_type")

        # Fan-in
        graph.add_edge("classify_intent", "merge_classification")
        graph.add_edge("classify_query_type", "merge_classification")

        def route_after_classification(state: AgentState) -> str:
            if state.get("is_irrelevant"):
                return "handle_irrelevant"
            if state.get("is_meta_question"):
                return "generate_meta_answer"
            return "check_clarity"

        graph.add_conditional_edges(
            "merge_classification",
            route_after_classification,
            {
                "handle_irrelevant": "handle_irrelevant",
                "generate_meta_answer": "generate_meta_answer",
                "check_clarity": "check_clarity",
            },
        )

        # check_clarity proceeds to confirm_continuation — unclear queries pause
        # via interrupt() inside the node and resume; confirm_continuation then
        # decides whether the response is an answer or a brand-new question.
        graph.add_edge("check_clarity", "confirm_continuation")

        def route_after_continuation(state: AgentState):
            # New question: fan-out to both parallel classification nodes so
            # the full sub-graph restarts (irrelevant/meta/clarity all available).
            if not state.get("question_clear", True):
                return ["classify_intent", "classify_query_type"]
            return "handle_clear"

        graph.add_conditional_edges(
            "confirm_continuation",
            route_after_continuation,
        )

        graph.add_edge("handle_irrelevant", END)
        graph.add_edge("generate_meta_answer", END)
        graph.add_edge("handle_clear", END)

        return graph.compile()
        
    # -----------------------------------------------------------------------
    # Node methods
    # -----------------------------------------------------------------------

    def _classify_intent(self, state: AgentState) -> dict:
        """Structured LLM call: intent type + context summary. Runs in parallel."""
        writer = get_stream_writer()
        messages = state.get("messages", [])
        human_messages = [m for m in messages if isinstance(m, HumanMessage)]
        current_query = human_messages[-1].content if human_messages else ""
        print(f"[classify_intent] query={current_query!r}")
        writer({"type": "agent_start", "agent": "unified_intent_context_clarification"})

        system_prompt = SystemMessage(content="""Classify the intent of the most recent user query and generate a concise context summary.

Intent types:
- new_question: Completely different topic from previous queries
- refinement: Narrowing/filtering/modifying the previous query on the same topic
- continuation: Follow-up exploring the same topic from a different angle
- clarification_response: User is answering a previous clarification request

Context summary: a sentence summary that will (1) synthesize the conversation history
(2) states clearly what the user wants and (3) is actionable for SQL query planning""")

        try:
            result: IntentClassification = self.intent_llm.invoke([system_prompt, *messages])
            print(f"[classify_intent] intent={result['intent_type']} confidence={result['confidence']:.2f}")
            writer({"type": "intent_detected", "intent_type": result["intent_type"]})

            turn = create_conversation_turn(
                query=current_query,
                intent_type=result["intent_type"],
                context_summary=result["context_summary"],
                triggered_clarification=False,
                metadata={
                    "domain": result["domain"],
                    "complexity": result["complexity"],
                    "topic_change_score": result["topic_change_score"],
                },
            )
            return {
                "current_turn": turn,
                "intent_metadata": IntentMetadata(
                    intent_type=result["intent_type"],
                    confidence=result["confidence"],
                    reasoning=f"classify_intent: {result['intent_type']}",
                    topic_change_score=result["topic_change_score"],
                    domain=result["domain"],
                    operation=None,
                    complexity=result["complexity"],
                    parent_turn_id=None,
                ),
            }
        except Exception as e:
            print(f"[classify_intent] error: {e} — falling back to new_question")
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
                    reasoning=f"error fallback: {e}",
                    topic_change_score=1.0,
                    domain=None,
                    operation=None,
                    complexity="moderate",
                    parent_turn_id=None,
                ),
            }

    def _classify_query_type(self, state: AgentState) -> dict:
        """Structured LLM call: is_irrelevant + is_meta_question. Runs in parallel."""
        messages = state.get("messages", [])
        human_messages = [m for m in messages if isinstance(m, HumanMessage)]
        current_query = human_messages[-1].content if human_messages else ""
        space_context = load_space_context(self.table_name)

        prompt = f"""You are screening a user query before routing it to a data analytics system.

User Query: {current_query}

Available Data Sources:
{json.dumps(space_context, indent=2)}

Most queries are regular data questions and should pass through with both flags set to False.
Only set a flag to True when the query clearly and unambiguously matches the description below.

is_irrelevant=True ONLY IF: the query is completely unrelated to data analytics — e.g. greetings,
small talk, weather, sports, politics, recipes, personal advice, or creative writing.

is_meta_question=True ONLY IF: the user is asking about the system itself rather than querying data —
e.g. "what tables are available?", "what can you do?", "show me example questions", or "what data sources exist?".

If the query is a normal data or business intelligence question (even a vague one), set both to False.
"""

        try:
            result: QueryTypeClassification = self.query_type_llm.invoke(prompt)
            print(f"[classify_query_type] irrelevant={result['is_irrelevant']} meta={result['is_meta_question']}")
            return {"is_irrelevant": result["is_irrelevant"], "is_meta_question": result["is_meta_question"]}
        except Exception as e:
            print(f"[classify_query_type] error: {e} — defaulting to regular query")
            return {"is_irrelevant": False, "is_meta_question": False}

    def _merge_classification(self, state: AgentState) -> dict:
        """Fan-in point after parallel classification nodes. No-op."""
        return {}

    def _handle_irrelevant(self, state: AgentState) -> dict:
        """Pure Python. Return a polite refusal."""
        print("[handle_irrelevant] returning refusal")
        turn = dict(state.get("current_turn") or {})
        turn.setdefault("metadata", {})["is_irrelevant"] = True

        refusal = (
            "I'm a data analytics assistant focused on helping you analyze and query "
            "the available data sources.\n\n"
            "I can help with questions about the data. To see what's available, try:\n"
            '- "What data sources are available?"\n'
            '- "What tables can I query?"\n'
            '- "Show me example questions I can ask"\n\n'
            "Could you rephrase your question to focus on analyzing the available data?"
        )
        return {
            "current_turn": turn,
            "turn_history": [turn] if turn else [],
            "question_clear": True,
            "is_irrelevant": True,
            "is_meta_question": False,
            "messages": [AIMessage(content=refusal)],
        }

    def _generate_meta_answer(self, state: AgentState) -> dict:
        """Streaming LLM call: markdown answer about available data."""
        messages = state.get("messages", [])
        human_messages = [m for m in messages if isinstance(m, HumanMessage)]
        current_query = human_messages[-1].content if human_messages else ""
        space_context = load_space_context(self.table_name)

        prompt = f"""The user is asking about what data or capabilities are available.

User Query: {current_query}

Available Data Sources:
{json.dumps(space_context, indent=2)}

Provide a clear, informative markdown answer about what's available.
Use ## headings, **bold** keywords, and bullet lists. Be professional and helpful.
"""
        writer = get_stream_writer()
        print("[generate_meta_answer] generating")
        try:
            content = ""
            for chunk in self.base_llm.stream(prompt):
                if chunk.content:
                    content += chunk.content
                    writer({"type": "clarification_chunk", "content": chunk.content})
            answer = content.strip()
        except Exception as e:
            print(f"[generate_meta_answer] error: {e}")
            answer = "## Available Data Sources\n\nSorry, I encountered an error retrieving the data source information."

        turn = dict(state.get("current_turn") or {})
        turn.setdefault("metadata", {})["is_meta_question"] = True

        return {
            "current_turn": turn,
            "turn_history": [turn] if turn else [],
            "question_clear": True,
            "is_meta_question": True,
            "meta_answer": answer,
            "messages": [AIMessage(content=answer)],
        }

    def _check_clarity(self, state: AgentState) -> dict:
        """Structured LLM call: clarity check. If unclear, interrupt() for user input."""
        messages = state.get("messages", [])
        human_messages = [m for m in messages if isinstance(m, HumanMessage)]
        current_query = human_messages[-1].content if human_messages else ""
        turn = state.get("current_turn") or {}
        context_summary = turn.get("context_summary", "")

        prompt = f"""Determine if the query is clear enough to generate a SQL query.

User Query: {current_query}
Context Summary: {context_summary}

Be lenient — only mark as unclear if CRITICAL information is missing.
If unclear, provide 2-3 specific clarification options.
"""
        try:
            result: ClarityCheck = self.clarity_llm.invoke(prompt)
            question_clear = result["question_clear"]
            clarification_reason = result.get("clarification_reason") or "Query needs more specificity"
            clarification_options = result.get("clarification_options") or []
            print(f"[check_clarity] clear={question_clear}")
        except Exception as e:
            print(f"[check_clarity] error: {e} — defaulting to clear")
            question_clear = True
            clarification_reason = ""
            clarification_options = []

        if question_clear:
            return {
                "current_turn": turn,
                "turn_history": [turn] if turn else [],
                "question_clear": True,
            }

        # Stream the clarification question to the client before pausing
        writer = get_stream_writer()
        markdown = f"### Clarification Needed\n\n{clarification_reason}\n\n"
        if clarification_options:
            markdown += "**Please choose from the following options:**\n\n"
            for i, opt in enumerate(clarification_options, 1):
                markdown += f"{i}. {opt}\n\n"
        writer({"type": "clarification_chunk", "content": markdown.strip()})

        print("[check_clarity] pausing via interrupt()")
        # Graph pauses here — state is checkpointed. Resumes when client sends
        # Command(resume=user_response) with the same thread_id.
        user_response = interrupt({
            "type": "clarification_request",
            "reason": clarification_reason,
            "options": clarification_options,
            "markdown": markdown.strip(),
        })

        # Resumed — user_response is the user's answer
        print(f"[check_clarity] resumed with: {user_response!r}")
        turn = dict(turn)
        turn["triggered_clarification"] = True
        # Update context_summary to incorporate the clarification so planning
        # receives the full intent: original query + what was asked + user's answer
        original_summary = turn.get("context_summary") or current_query
        turn["context_summary"] = (
            f"{original_summary} — "
            f"Clarification asked: {clarification_reason} — "
            f"User answered: {user_response}"
        )
        print(f"[check_clarity] updated context_summary for planning: {turn['context_summary'][:100]}...")
        return {
            "current_turn": turn,
            "question_clear": True,
            "messages": [HumanMessage(content=user_response)],
        }

    def _confirm_continuation(self, state: AgentState) -> dict:
        """Check if the user's response actually answers the clarification or is a new question.

        Pass-through when no clarification was triggered (question was already clear).
        If the user responded with a new, unrelated question, set question_clear=False
        so the subgraph loops back to classify_intent for full re-classification.
        """
        turn = state.get("current_turn") or {}
        if not turn.get("triggered_clarification"):
            # No clarification was asked — proceed directly to handle_clear
            return {"question_clear": True}

        messages = state.get("messages", [])
        human_messages = [m for m in messages if isinstance(m, HumanMessage)]
        # The most recent HumanMessage is the user's clarification response
        user_response = human_messages[-1].content if human_messages else ""
        original_query = turn.get("context_summary", "")

        prompt = f"""A user was asked a clarification question while answering a data analytics query.
Determine whether their response directly answers the clarification or is a brand-new, unrelated question.

Original context / clarification asked: {original_query}
User's response: {user_response}

is_clarification_response=True  → they answered the clarification (even loosely)
is_clarification_response=False → they changed the subject or asked something entirely new
"""
        try:
            result: ContinuationCheck = self.continuation_llm.invoke(prompt)
            is_continuation = result["is_clarification_response"]
            print(f"[confirm_continuation] is_continuation={is_continuation} reason={result['reasoning']!r}")
        except Exception as e:
            print(f"[confirm_continuation] error: {e} — assuming continuation")
            is_continuation = True

        if is_continuation:
            return {"question_clear": True}

        # New question — reset clarification flag and let the graph re-classify
        print("[confirm_continuation] user asked a new question — looping back to classify_intent")
        turn = dict(turn)
        turn["triggered_clarification"] = False
        return {
            "current_turn": turn,
            "question_clear": False,
        }

    def _handle_clear(self, state: AgentState) -> dict:
        """Pure Python. Confirm clarity and forward to planning."""
        print("[handle_clear] query is clear")
        turn = state.get("current_turn", {})
        intent_type = (state.get("intent_metadata") or {}).get("intent_type", "new_question")
        return {
            "current_turn": turn,
            "turn_history": [turn] if turn else [],
            "question_clear": True,
            "messages": [SystemMessage(content=f"Intent: {intent_type}, proceeding to planning")],
        }

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    def run(self, state: AgentState) -> dict:
        self.track_agent_model_usage(self.llm_endpoint)
        return self.subgraph.invoke(state)
