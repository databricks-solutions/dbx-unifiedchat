"""
AgentRx — Knowledge-base management + feedback-analysis agent.

AgentRx lives alongside the main super-agent in the same Python process but
runs on its own graph (a LangGraph ReAct loop). It is invoked through a
separate backend route (`POST /api/agent-rx`), not through the super-agent's
clarification → planning workflow.

Capabilities
------------
Knowledge base management (writes — bundle-deployed jobs do the work):
- List / inspect indexed Genie Spaces
- Add a space to the index (incremental async job)
- Remove a space from the index (DELETE + volume cleanup + VS sync)

Discovery (read-only):
- List all Genie Spaces accessible to the workspace
- Inspect a Genie Space's tables + instructions

ETL & refresh:
- Trigger the metadata-refresh job (export → enrich → VS rebuild)
- Trigger a Vector Search index sync
- Invalidate the in-process space-context cache

Feedback analysis (read-only — foundations for the admin feedback loop):
- Summarise thumbs-up / thumbs-down counts over a recent window
- Sample failing traces to recommend KB or instruction changes

AgentRx does NOT mutate Genie spaces themselves, nor does it auto-apply
recommended changes. Admin approval / orchestrator is out of scope.
"""

from typing import Any, Dict, List, Optional

from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from ..tools.genie_space_manager import ALL_GENIE_TOOLS
from ..tools.knowledge_base_manager import ALL_KB_TOOLS
from ..tools.etl_trigger import ALL_ETL_TOOLS
from ..tools.mlflow_feedback import ALL_FEEDBACK_TOOLS


AGENT_RX_SYSTEM_PROMPT = """You are AgentRx, a knowledge-base management and feedback-analysis assistant for a Databricks multi-agent system.

The system answers data questions by looking up relevant Genie Spaces in an enriched metadata index (Vector Search). Your job is to (1) manage which Genie Spaces are included in that index — i.e. what the agent "has access to" — and (2) help administrators understand user feedback so they can decide what to change.

## Key Concept
"Adding" or "removing access" to a Genie Space means adding or removing its metadata from the enriched tables and Vector Search index that the Planning Agent uses. It does NOT mean creating, deleting, or modifying the actual Genie Space on the workspace.

## Capabilities

### Knowledge Base Management
1. **List indexed spaces** — show which Genie Spaces the agent currently knows about (from the enriched metadata tables)
2. **Get indexed space details** — inspect chunk types, table coverage, and chunk counts for a specific indexed space
3. **Remove a space from the index** — delete all metadata for a space from the enriched tables, remove exported files, sync the Vector Search index, and invalidate caches. The space itself is untouched.
4. **Add a space to the index** — export a Genie Space's metadata, trigger the ETL pipeline to enrich it and rebuild the search index

### Discovery (read-only)
5. **List all Genie Spaces** on the workspace — browse available spaces, including ones not yet indexed
6. **Inspect a Genie Space's configuration** — view tables, instructions, warehouse of any space on the workspace

### ETL & Refresh
7. **Trigger a Vector Search sync** — lightweight refresh after table-level changes
8. **Trigger the full ETL pipeline** — export → enrich → rebuild index (runs asynchronously)
9. **Invalidate the space context cache** — force the next query to reload from database

### Feedback Analysis (read-only)
10. **Summarise recent feedback** — counts and thumbs-down rate over a configurable window. Use this to detect regressions.
11. **Sample failing traces** — surface up to a handful of recent thumbs-down traces with the user question and matched spaces, so you can spot patterns (e.g. one space is consistently missing context).

## Workflow Guidelines

- When the user asks to "remove" data, a space, or a topic: use **remove_space_from_index** (not a Genie Space deletion).
- When the user asks to "add" a new data source or space: use **add_space_to_index**.
- Before removing, call **list_indexed_spaces** to identify the correct space_id.
- After removal, the Vector Search sync and cache invalidation happen automatically inside the tool.
- If the user asks what data is currently accessible, use **list_indexed_spaces**.
- For "why is the agent failing" or "what's wrong recently", start with **summarise_recent_feedback**, then drill in with **sample_failing_traces**. Recommend KB changes (add/remove space, refresh metadata) but DO NOT auto-apply them — leave the action to the admin unless they explicitly tell you to run it.
- Always report the outcome of each operation clearly in markdown.
- If an operation fails, include the error details and suggest corrective actions.

## Response Format
Provide your final response as well-structured markdown with:
- A summary of what was requested
- The operations performed and their results (or, for read-only analysis, the findings)
- Any follow-up actions recommended
"""


class AgentRxAgent:
    """Knowledge-base management + feedback-analysis agent.

    Wraps Genie discovery, knowledge base management, ETL trigger, and MLflow
    feedback-analysis tools into a single LangGraph ReAct tool-calling agent.
    """

    def __init__(self, llm: Runnable, tools: Optional[List[BaseTool]] = None):
        self.llm = llm
        self.tools = tools or (
            ALL_GENIE_TOOLS + ALL_KB_TOOLS + ALL_ETL_TOOLS + ALL_FEEDBACK_TOOLS
        )
        self.name = "AgentRx"
        self._agent = self._build_agent()

    def _build_agent(self):
        from langgraph.prebuilt import create_react_agent

        return create_react_agent(
            model=self.llm,
            tools=self.tools,
            prompt=AGENT_RX_SYSTEM_PROMPT,
        )

    def invoke(self, user_request: str) -> Dict[str, Any]:
        """Run the agent on a single admin request and return the rolled-up result."""
        result = self._agent.invoke(
            {"messages": [{"role": "user", "content": user_request}]}
        )

        messages = result.get("messages", [])
        final_response = ""
        tool_calls: list[dict] = []
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append({"tool": tc.get("name", ""), "args": tc.get("args", {})})
            if hasattr(msg, "content") and msg.content:
                final_response = msg.content

        return {
            "response": final_response,
            "tool_calls": tool_calls,
        }

    def stream(self, user_request: str):
        """Yield intermediate ReAct steps for the request.

        Emits dicts shaped as:
          - {"type": "tool_call", "tool": <name>, "args": <dict>}
          - {"type": "tool_result", "tool": <name>, "result": <str>}
          - {"type": "final", "content": <markdown>}
        Suitable for adapting into MLflow ResponsesAgent stream events.
        """
        stream_iter = self._agent.stream(
            {"messages": [{"role": "user", "content": user_request}]},
            stream_mode="values",
        )

        seen_msgs = 0
        last_content = ""
        for chunk in stream_iter:
            messages = chunk.get("messages", [])
            for msg in messages[seen_msgs:]:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        yield {
                            "type": "tool_call",
                            "tool": tc.get("name", ""),
                            "args": tc.get("args", {}),
                        }
                # ToolMessage carries `name` + `content` for the tool result
                if msg.__class__.__name__ == "ToolMessage":
                    yield {
                        "type": "tool_result",
                        "tool": getattr(msg, "name", ""),
                        "result": getattr(msg, "content", ""),
                    }
                if hasattr(msg, "content") and msg.content:
                    last_content = msg.content
            seen_msgs = len(messages)

        if last_content:
            yield {"type": "final", "content": last_content}

    def __call__(self, user_request: str) -> Dict[str, Any]:
        return self.invoke(user_request)
