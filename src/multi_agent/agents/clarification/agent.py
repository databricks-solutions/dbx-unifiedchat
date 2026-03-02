"""
ClarificationAgent: Graph-based sub-agent for intent, classification, and clarity.

Builds a compiled LangGraph sub-graph using AgentState directly so it shares
state keys with the parent graph without any type conversion.
"""

from functools import partial

from databricks_langchain import ChatDatabricks
from langgraph.graph import StateGraph, END

from ...core.base_agent import BaseAgent
from ...core.state import AgentState
from .nodes import (
    classify_intent_node,
    classify_query_type_node,
    handle_irrelevant_node,
    generate_meta_answer_node,
    check_clarity_node,
    handle_clear_node,
)


class ClarificationAgent(BaseAgent):
    """
    Clarification sub-agent implemented as a compiled LangGraph sub-graph.

    Sub-graph flow:
        classify_intent
            ↓
        classify_query_type
            ↓ route
            ├─ irrelevant → handle_irrelevant → END
            ├─ meta       → generate_meta_answer → END
            └─ regular    →
                   check_clarity
                      ↓ route
                      ├─ unclear → handle_clarification → END
                      └─ clear   → handle_clear         → END

    Each LLM-call node has a single focused responsibility.
    Handler nodes are pure Python (no LLM).
    """

    def __init__(self, llm_endpoint: str, table_name: str):
        """
        Build and compile the clarification sub-graph.

        Args:
            llm_endpoint: Databricks model serving endpoint for LLM calls
            table_name: Fully-qualified Delta table name for space context
        """
        super().__init__("clarification")
        self.llm_endpoint = llm_endpoint
        self.table_name = table_name
        self._subgraph = self._build_subgraph()

    def _build_subgraph(self):
        """Construct and compile the internal LangGraph sub-graph."""
        llm = ChatDatabricks(endpoint=self.llm_endpoint, temperature=0.1)

        # Bind runtime dependencies into each node via partial
        _classify_intent = partial(classify_intent_node, llm=llm, table_name=self.table_name)
        _classify_query_type = partial(classify_query_type_node, llm=llm, table_name=self.table_name)
        _generate_meta_answer = partial(generate_meta_answer_node, llm=llm, table_name=self.table_name)
        _check_clarity = partial(check_clarity_node, llm=llm)

        graph = StateGraph(AgentState)

        graph.add_node("classify_intent", _classify_intent)
        graph.add_node("classify_query_type", _classify_query_type)
        graph.add_node("handle_irrelevant", handle_irrelevant_node)
        graph.add_node("generate_meta_answer", _generate_meta_answer)
        graph.add_node("check_clarity", _check_clarity)
        graph.add_node("handle_clear", handle_clear_node)

        graph.set_entry_point("classify_intent")
        graph.add_edge("classify_intent", "classify_query_type")

        def route_after_query_type(state: AgentState) -> str:
            if state.get("is_irrelevant"):
                return "handle_irrelevant"
            if state.get("is_meta_question"):
                return "generate_meta_answer"
            return "check_clarity"

        graph.add_conditional_edges(
            "classify_query_type",
            route_after_query_type,
            {
                "handle_irrelevant": "handle_irrelevant",
                "generate_meta_answer": "generate_meta_answer",
                "check_clarity": "check_clarity",
            },
        )

        # check_clarity handles both the LLM call and the clarification response.
        # If question_clear=True after that node, proceed to handle_clear;
        # otherwise the clarification is already formatted — go to END.
        def route_after_clarity(state: AgentState) -> str:
            return "handle_clear" if state.get("question_clear", True) else END

        graph.add_conditional_edges(
            "check_clarity",
            route_after_clarity,
            {
                "handle_clear": "handle_clear",
                END: END,
            },
        )

        graph.add_edge("handle_irrelevant", END)
        graph.add_edge("generate_meta_answer", END)
        graph.add_edge("handle_clear", END)

        return graph.compile()

    def run(self, state: AgentState) -> dict:
        """
        Execute the clarification sub-graph and return state updates.

        Args:
            state: Current parent AgentState

        Returns:
            Dict of state updates to merge into the parent graph's state
        """
        self.track_agent_model_usage(self.llm_endpoint)
        return self._subgraph.invoke(state)
