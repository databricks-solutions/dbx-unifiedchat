"""
Standalone /api/agent-rx endpoint that exposes AgentRxAgent for the
knowledge-base management UI.

AgentRx is a sibling agent to the super-agent ResponsesAgent registered by
``AgentServer`` in ``start_server.py``. It runs in the same FastAPI process
but on its own ReAct graph, with its own tools (KB management, ETL triggers,
Genie discovery, MLflow feedback analysis). It is *not* invoked through the
super-agent's clarification → planning workflow.

The route streams Server-Sent Events with `tool_call`, `tool_result`, and
`final` event types so the Next.js frontend can render the ReAct trace.
"""

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class AgentRxRequest(BaseModel):
    message: str = Field(..., min_length=1)
    # Reserved for future per-user context (admin id, etc.); currently unused.
    user_id: Optional[str] = None


class AgentRxResponse(BaseModel):
    response: str
    tool_calls: list[Dict[str, Any]]


_agent_rx_singleton = None


def _get_agent_rx():
    """Lazy-construct the AgentRxAgent once per process."""
    global _agent_rx_singleton
    if _agent_rx_singleton is not None:
        return _agent_rx_singleton

    from databricks_langchain import ChatDatabricks

    from .multi_agent.agents.agent_rx_agent import AgentRxAgent
    from .multi_agent.core.config import get_config

    config = get_config()
    llm = ChatDatabricks(endpoint=config.llm.agent_rx_endpoint)
    _agent_rx_singleton = AgentRxAgent(llm=llm)
    logger.info(
        "AgentRx initialised (endpoint=%s, tools=%d)",
        config.llm.agent_rx_endpoint,
        len(_agent_rx_singleton.tools),
    )
    return _agent_rx_singleton


@router.post("/agent-rx", response_model=AgentRxResponse)
async def invoke_agent_rx(request: AgentRxRequest) -> AgentRxResponse:
    """Non-streaming AgentRx invocation. Returns the final response and tool history."""
    try:
        agent = _get_agent_rx()
        result = agent.invoke(request.message)
        return AgentRxResponse(
            response=result.get("response", ""),
            tool_calls=result.get("tool_calls", []),
        )
    except Exception as exc:
        logger.exception("AgentRx invocation failed")
        return AgentRxResponse(
            response=f"AgentRx failed: {exc}",
            tool_calls=[],
        )


@router.post("/agent-rx/stream")
async def stream_agent_rx(request: AgentRxRequest):
    """Streaming AgentRx invocation. Emits SSE events:

      data: {"type": "tool_call", "tool": ..., "args": {...}}
      data: {"type": "tool_result", "tool": ..., "result": "..."}
      data: {"type": "final", "content": "..."}
      data: [DONE]
    """

    def event_stream():
        try:
            agent = _get_agent_rx()
            for event in agent.stream(request.message):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.exception("AgentRx stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
