"""
Migrated multi-agent Genie system — async @invoke/@stream entry point.

Converted from SuperAgentHybridResponsesAgent (Model Serving) to
MLflow GenAI Server decorated functions (Databricks Apps).
"""

import json
import logging
import time
from typing import AsyncGenerator
from uuid import uuid4

import litellm
import mlflow
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    output_to_responses_items_stream,
    to_chat_completions_input,
)

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

from agent_server.utils import get_session_id
from agent_server.multi_agent.core.graph import create_super_agent_hybrid
from agent_server.multi_agent.core.state import RESET_STATE_TEMPLATE

mlflow.langchain.autolog(run_tracer_inline=False)

logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)
litellm.suppress_debug_info = True

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensure DATABRICKS_HOST/TOKEN env vars are set for libraries that don't use
# the SDK auth chain (e.g. databricks-vectorsearch).
# ---------------------------------------------------------------------------
import os

_is_databricks_apps = bool(os.environ.get("DATABRICKS_CLIENT_ID"))
if not _is_databricks_apps and not os.environ.get("DATABRICKS_TOKEN"):
    try:
        from databricks.sdk import WorkspaceClient as _WsClient
        _w = _WsClient()
        _cfg_auth = _w.config
        if _cfg_auth.host and not os.environ.get("DATABRICKS_HOST"):
            os.environ["DATABRICKS_HOST"] = _cfg_auth.host
        _auth_result = _cfg_auth.authenticate()
        if isinstance(_auth_result, dict):
            _bearer = _auth_result.get("Authorization", "")
        elif callable(_auth_result):
            _bearer = _auth_result().get("Authorization", "")
        else:
            _bearer = ""
        if _bearer.startswith("Bearer "):
            os.environ["DATABRICKS_TOKEN"] = _bearer[7:]
            os.environ.pop("DATABRICKS_CONFIG_PROFILE", None)
            logger.info("Resolved DATABRICKS_HOST/TOKEN from SDK auth chain")
    except Exception as _e:
        logger.warning(f"Could not resolve Databricks auth from SDK: {_e}")

# ---------------------------------------------------------------------------
# Module-level setup (replaces __init__ of SuperAgentHybridResponsesAgent)
# ---------------------------------------------------------------------------
_workflow = create_super_agent_hybrid()

LAKEBASE_INSTANCE_NAME = None
EMBEDDING_ENDPOINT = None
EMBEDDING_DIMS = None
try:
    from agent_server.multi_agent.core.config import get_config
    _cfg = get_config()
    LAKEBASE_INSTANCE_NAME = _cfg.lakebase.instance_name
    EMBEDDING_ENDPOINT = _cfg.lakebase.embedding_endpoint
    EMBEDDING_DIMS = _cfg.lakebase.embedding_dims
except Exception as e:
    logger.warning(f"Failed to load config at import time: {e}")

_store = None


def _get_store():
    """Lazy initialization of DatabricksStore for long-term memory."""
    global _store
    if _store is None and LAKEBASE_INSTANCE_NAME:
        from databricks_langchain import DatabricksStore
        logger.info(f"Initializing DatabricksStore with instance: {LAKEBASE_INSTANCE_NAME}")
        _store = DatabricksStore(
            instance_name=LAKEBASE_INSTANCE_NAME,
            embedding_endpoint=EMBEDDING_ENDPOINT,
            embedding_dims=EMBEDDING_DIMS,
        )
        _store.setup()
        logger.info("DatabricksStore initialized")
    return _store


# ---------------------------------------------------------------------------
# Helper: extract thread_id / user_id from request
# ---------------------------------------------------------------------------

def _get_or_create_thread_id(request: ResponsesAgentRequest) -> str:
    ci = dict(request.custom_inputs or {})
    if "thread_id" in ci:
        return ci["thread_id"]
    if request.context and getattr(request.context, "conversation_id", None):
        return request.context.conversation_id
    return str(uuid4())


def _get_user_id(request: ResponsesAgentRequest):
    if request.context and getattr(request.context, "user_id", None):
        return request.context.user_id
    if request.custom_inputs and "user_id" in request.custom_inputs:
        return request.custom_inputs["user_id"]
    return None


# ---------------------------------------------------------------------------
# Helper: format custom streaming events
# ---------------------------------------------------------------------------

def _make_json_serializable(obj):
    from langchain_core.messages import BaseMessage
    from uuid import UUID

    if obj is None:
        return None
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8", errors="ignore")
        except Exception:
            return f"<bytes:{len(obj)}>"
    if isinstance(obj, set):
        return [_make_json_serializable(item) for item in obj]
    if isinstance(obj, BaseMessage):
        msg_dict = {"type": obj.__class__.__name__, "content": str(obj.content) if obj.content else ""}
        if hasattr(obj, "id") and obj.id:
            msg_dict["id"] = str(obj.id)
        if hasattr(obj, "tool_calls") and obj.tool_calls:
            msg_dict["tool_calls"] = [_make_json_serializable(tc) for tc in obj.tool_calls[:2]]
        return msg_dict
    if isinstance(obj, dict):
        return {str(k): _make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_serializable(item) for item in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    try:
        return str(obj)
    except Exception:
        return f"<{type(obj).__name__}>"


_CUSTOM_FORMATTERS = {
    "agent_thinking": lambda d: f"{d['agent'].upper()}: {d['content']}",
    "agent_start": lambda d: f"Starting {d['agent']} agent for: {d.get('query', '')[:50]}...",
    "intent_detection": lambda d: f"Intent: {d['result']} - {d.get('reasoning', '')}",
    "clarity_analysis": lambda d: f"Query {'clear' if d['clear'] else 'unclear'}: {d.get('reasoning', '')}",
    "vector_search_start": lambda d: f"Searching vector index: {d['index']}",
    "vector_search_results": lambda d: f"Found {d['count']} relevant spaces",
    "plan_formulation": lambda d: f"Execution plan: {d.get('strategy', 'unknown')} strategy",
    "sql_generated": lambda d: f"SQL generated: {d.get('query_preview', '')}...",
    "sql_validation_start": lambda d: "Validating SQL query...",
    "sql_execution_start": lambda d: "Executing SQL query...",
    "sql_execution_complete": lambda d: f"Query complete: {d.get('rows', 0)} rows",
    "summary_start": lambda d: "Generating summary...",
    "genie_agent_call": lambda d: f"Calling Genie agent for space: {d.get('space_id', 'unknown')}",
    "meta_answer_content": lambda d: f"\n\n{d.get('content', '')}",
    "clarification_content": lambda d: f"\n\n{d.get('content', '')}",
}


def _format_custom_event(custom_data: dict) -> str:
    event_type = custom_data.get("type", "unknown")
    formatter = _CUSTOM_FORMATTERS.get(
        event_type,
        lambda d: f"info {event_type}: {json.dumps(_make_json_serializable(d), indent=2, default=str)}",
    )
    try:
        return formatter(custom_data)
    except Exception:
        return f"info {event_type}: {str(custom_data)}"


def _create_text_output_item(text: str, id: str):
    return {
        "type": "message",
        "id": id,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _create_text_delta(delta: str, id: str):
    return {
        "type": "response.output_text.delta",
        "item_id": id,
        "content_index": 0,
        "delta": delta,
    }


def _create_function_call_item(id: str, call_id: str, name: str, arguments: str):
    return {
        "type": "function_call",
        "id": id,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


# ---------------------------------------------------------------------------
# @invoke / @stream entry points
# ---------------------------------------------------------------------------


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    """Collect all streaming events and return the final response."""
    outputs = [
        event.item
        async for event in stream_handler(request)
        if event.type == "response.output_item.done"
    ]
    return ResponsesAgentResponse(output=outputs, custom_outputs=request.custom_inputs)


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    """Stream the multi-agent workflow — async wrapper around sync LangGraph execution."""
    if session_id := get_session_id(request):
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})

    thread_id = _get_or_create_thread_id(request)
    user_id = _get_user_id(request)

    ci = dict(request.custom_inputs or {})
    ci["thread_id"] = thread_id
    if user_id:
        ci["user_id"] = user_id
    request.custom_inputs = ci

    logger.info(f"Processing request - thread_id: {thread_id}, user_id: {user_id}")

    workflow_start_time = time.time()
    first_token_time = None

    cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
    latest_query = cc_msgs[-1]["content"] if cc_msgs else ""

    run_config = {"configurable": {"thread_id": thread_id}}
    if user_id:
        run_config["configurable"]["user_id"] = user_id

    initial_state = {
        **RESET_STATE_TEMPLATE,
        "original_query": latest_query,
        "messages": [
            SystemMessage(content="""You are a multi-agent Q&A analysis system.
Your role is to help users query and analyze cross-domain data.

Guidelines:
- Always explain your reasoning and execution plan
- Validate SQL queries before execution
- Provide clear, comprehensive summaries
- If information is missing, ask for clarification (max once)
- Use UC functions and Genie agents to generate accurate SQL
- Return results with proper context and explanations"""),
            HumanMessage(content=latest_query),
        ],
    }
    if user_id:
        initial_state["user_id"] = user_id
        initial_state["thread_id"] = thread_id

    first_message = True
    seen_ids: set = set()

    # Run the sync LangGraph workflow in a thread to keep the async entry point non-blocking
    import asyncio

    def _run_workflow():
        """Execute the sync LangGraph workflow inside a CheckpointSaver context manager."""
        from databricks_langchain import CheckpointSaver

        with CheckpointSaver(instance_name=LAKEBASE_INSTANCE_NAME) as checkpointer:
            app = _workflow.compile(checkpointer=checkpointer)
            logger.info(f"Executing workflow with checkpointer (thread: {thread_id})")
            events = list(
                app.stream(
                    initial_state,
                    run_config,
                    stream_mode=["updates", "messages", "custom", "tasks"],
                )
            )
        return events

    loop = asyncio.get_event_loop()
    events = await loop.run_in_executor(None, _run_workflow)

    progress_steps: list[str] = []
    details_emitted = False
    in_summarize = False

    for event in events:
        event_type = event[0]
        event_data = event[1]

        # ── tasks: detect errors ──
        if event_type == "tasks":
            try:
                ev = event_data if isinstance(event_data, dict) else {}
                if ev.get("event") == "error":
                    node_name = ev.get("name", "unknown")
                    error = ev.get("error", "Unknown error")
                    logger.error(f"Task failed: {node_name} - {error}")
                    yield ResponsesAgentStreamEvent(
                        type="response.output_item.done",
                        item=_create_text_output_item(text=f"Error in {node_name}: {error}", id=str(uuid4())),
                    )
            except Exception as e:
                logger.warning(f"Error processing task event: {e}")
            continue

        # ── custom: collect progress, pass through clarification/meta/error ──
        if event_type == "custom":
            try:
                et = event_data.get("type", "") if isinstance(event_data, dict) else ""
                if et in ("meta_answer_content", "clarification_content", "clarification_requested"):
                    yield ResponsesAgentStreamEvent(
                        type="response.output_item.done",
                        item=_create_text_output_item(text=_format_custom_event(event_data), id=str(uuid4())),
                    )
                elif et.endswith("_error") or et == "error":
                    yield ResponsesAgentStreamEvent(
                        type="response.output_item.done",
                        item=_create_text_output_item(text=_format_custom_event(event_data), id=str(uuid4())),
                    )
                elif et == "summary_start" and not details_emitted:
                    details_emitted = True
                    in_summarize = True
                    if progress_steps:
                        block = "<details>\n<summary>Processing Steps</summary>\n\n"
                        for step in progress_steps:
                            block += f"- {step}\n"
                        block += "\n</details>\n\n"
                        yield ResponsesAgentStreamEvent(
                            type="response.output_item.done",
                            item=_create_text_output_item(text=block, id=str(uuid4())),
                        )
                else:
                    progress_steps.append(_format_custom_event(event_data))
            except Exception as e:
                logger.warning(f"Error processing custom event: {e}")
            continue

        # ── messages: stream LLM deltas (only during summarize for clean output) ──
        if event_type == "messages":
            try:
                chunk = event_data[0] if isinstance(event_data, (list, tuple)) else event_data
                if isinstance(chunk, AIMessageChunk) and (content := chunk.content):
                    if in_summarize:
                        if first_token_time is None:
                            first_token_time = time.time()
                            logger.info(f"TTFT: {first_token_time - workflow_start_time:.3f}s")
                        yield ResponsesAgentStreamEvent(**_create_text_delta(delta=content, id=chunk.id))
            except Exception as e:
                logger.warning(f"Error processing message chunk: {e}")
            continue

        # ── updates: collect progress, emit final AIMessage from summarize ──
        if event_type == "updates":
            events_dict = event_data
            new_msgs = [
                msg for v in events_dict.values()
                for msg in v.get("messages", [])
                if hasattr(msg, "id") and msg.id not in seen_ids
            ]

            seen_ids.update(msg.id for msg in new_msgs)
            if first_message:
                first_message = False
                if events_dict:
                    node = tuple(events_dict.keys())[0]
                    update = events_dict[node]
                    if node != "summarize":
                        step = f"Step: {node}"
                        extra = [k for k in update if k != "messages"]
                        if extra:
                            step += f" ({', '.join(extra)})"
                        progress_steps.append(step)
                        if "next_agent" in update:
                            progress_steps.append(f"Routing → {update['next_agent']}")

            for msg in new_msgs:
                if in_summarize or isinstance(msg, AIMessage):
                    for se in output_to_responses_items_stream([msg]):
                        yield se
            continue

    ttcl = time.time() - workflow_start_time
    logger.info(
        f"Workflow completed (thread: {thread_id}) "
        f"TTFT={first_token_time - workflow_start_time if first_token_time else 'N/A'}s, TTCL={ttcl:.3f}s"
    )
