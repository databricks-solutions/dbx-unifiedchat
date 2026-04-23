from pathlib import Path
import sys

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_server.multi_agent.core.state import AgentState, RESET_STATE_TEMPLATE
from agent_server.multi_agent.agents import clarification


def test_same_thread_new_turn_clears_space_context_before_reload():
    load_calls: list[dict[str, str]] = []

    def load_space_context_like_clarification(state: AgentState):
        if state.get("space_context") is not None:
            return {"final_summary": "reused_existing_state"}

        value = {"loaded_on_call": str(len(load_calls) + 1)}
        load_calls.append(value)
        return {
            "space_context": value,
            "final_summary": "loaded_fresh",
        }

    builder = StateGraph(AgentState)
    builder.add_node("load", load_space_context_like_clarification)
    builder.add_edge(START, "load")
    builder.add_edge("load", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": "same-thread-space-context-reset"}}

    first_turn = graph.invoke({**RESET_STATE_TEMPLATE, "messages": []}, config=config)
    second_turn = graph.invoke({**RESET_STATE_TEMPLATE, "messages": []}, config=config)

    assert first_turn["final_summary"] == "loaded_fresh"
    assert second_turn["final_summary"] == "loaded_fresh"
    assert first_turn["space_context"] != second_turn["space_context"]
    assert load_calls == [
        {"loaded_on_call": "1"},
        {"loaded_on_call": "2"},
    ]


def test_same_thread_new_turn_reloads_through_ttl_cache_instead_of_warehouse(monkeypatch):
    loader_calls: list[str] = []
    warehouse_calls: list[tuple[str, str, bool]] = []

    monkeypatch.setattr(
        clarification,
        "_space_context_cache",
        {"data": None, "timestamp": None, "table_name": None},
    )

    def fake_query_space_context_via_warehouse(
        table_name: str, warehouse_id: str, *, record_trace: bool = True
    ):
        warehouse_calls.append((table_name, warehouse_id, record_trace))
        return {"space-a": "cached summary"}

    monkeypatch.setattr(
        clarification,
        "_query_space_context_via_warehouse",
        fake_query_space_context_via_warehouse,
    )

    def load_space_context_node(state: AgentState):
        if state.get("space_context") is not None:
            return {"final_summary": "reused_existing_state"}

        loader_calls.append("load_space_context")
        return {
            "space_context": clarification.load_space_context(
                table_name="catalog.schema.source_table",
                warehouse_id="warehouse-123",
                record_trace=False,
            ),
            "final_summary": "loaded_via_loader",
        }

    builder = StateGraph(AgentState)
    builder.add_node("load", load_space_context_node)
    builder.add_edge(START, "load")
    builder.add_edge("load", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": "same-thread-space-context-cache"}}

    first_turn = graph.invoke({**RESET_STATE_TEMPLATE, "messages": []}, config=config)
    second_turn = graph.invoke({**RESET_STATE_TEMPLATE, "messages": []}, config=config)

    assert first_turn["final_summary"] == "loaded_via_loader"
    assert second_turn["final_summary"] == "loaded_via_loader"
    assert first_turn["space_context"] == {"space-a": "cached summary"}
    assert second_turn["space_context"] == {"space-a": "cached summary"}
    assert loader_calls == ["load_space_context", "load_space_context"]
    assert warehouse_calls == [
        ("catalog.schema.source_table", "warehouse-123", False),
    ]
