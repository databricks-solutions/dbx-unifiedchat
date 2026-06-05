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

Genie Space editing (writes — direct edits to the space configuration):
- List tables/columns, benchmark questions, sample questions, instructions
- Add / remove benchmark questions and sample questions
- Hide / show columns within a space

ETL & refresh:
- Trigger the metadata-refresh job (export → enrich → VS rebuild)
- Trigger a Vector Search index sync
- Invalidate the in-process space-context cache

Feedback analysis (read-only — foundations for the admin feedback loop):
- Summarise thumbs-up / thumbs-down counts over a recent window
- Sample failing traces to recommend KB or instruction changes

AgentRx can edit a Genie Space's own configuration (benchmark questions, sample
questions, column visibility). It does NOT create or delete Genie Spaces, and it
does not auto-apply feedback-driven recommendations.
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

### Genie Space Editing (writes — edits the actual Genie Space configuration)
These edit the Genie Space itself on the workspace (distinct from the knowledge-base index above):
- **list_space_tables_and_columns** — list every table and column in a space, with each column's visibility (hidden?) and Genie settings
- **list_benchmark_questions** / **add_benchmark_question** / **remove_benchmark_question** — manage evaluation (benchmark) questions; adding requires the question text and an expected SQL answer; removing matches by id or exact question text
- **list_sample_questions** / **add_sample_question** / **remove_sample_question** — manage curated example (sample) questions; a SQL answer is recommended when adding
- **list_space_instructions** — view a space's free-text instructions
- **set_column_visibility(space_id, table, columns, hidden)** — hide (hidden=true) or show (hidden=false) columns; `table` is the table identifier and `columns` is a COMMA-SEPARATED string of column names (not a list)

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
- For editing a Genie Space (benchmark/sample questions, column visibility): the user must give a target space_id (use **list_genie_spaces** to resolve a name to an id if needed). Before hiding/showing a column, call **list_space_tables_and_columns** to confirm the exact table identifier and column name. These edits apply immediately to the Genie Space itself. If the user wants to see the before/after state, call the matching **list_*** tool before and after the edit (the edit tools themselves return only the change summary, not a full before snapshot — do not fabricate one). Distinguish clearly between editing a space's config (these tools) and adding/removing a space from the knowledge-base index (add_space_to_index / remove_space_from_index).
- The Genie space write tools (add/remove question, set_column_visibility) report the authoritative result of the change they just applied (e.g. the new id, or the remaining count) — trust that. The Genie read API is eventually consistent: a `list_*` call issued immediately after a write may briefly return stale data, so do NOT re-list right after a write to "confirm" it and do NOT report a write as failed just because an immediate re-read still shows the old state. If a user explicitly wants confirmation, note that the change was applied and that a fresh listing may take a moment to reflect it.
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

    @staticmethod
    def _to_messages(conversation) -> List[Dict[str, str]]:
        """Normalise a string OR a list of {role, content} turns into chat messages.

        Passing prior turns gives the ReAct loop true conversation memory: the
        agent sees earlier user questions and its own answers, so follow-ups like
        "now remove that one" resolve against the established context.
        """
        if isinstance(conversation, str):
            return [{"role": "user", "content": conversation}]
        messages: List[Dict[str, str]] = []
        for turn in conversation or []:
            role = (turn.get("role") or "user").strip()
            content = turn.get("content")
            if role not in ("user", "assistant", "system") or not content:
                continue
            messages.append({"role": role, "content": str(content)})
        if not messages:
            raise ValueError("AgentRx requires at least one non-empty message.")
        return messages

    def invoke(self, conversation) -> Dict[str, Any]:
        """Run the agent on a request (string) or full conversation (list of turns)."""
        result = self._agent.invoke(
            {"messages": self._to_messages(conversation)}
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

    def stream(self, conversation):
        """Yield intermediate ReAct steps for a request (string) or conversation (list).

        Emits dicts shaped as:
          - {"type": "tool_call", "tool": <name>, "args": <dict>}
          - {"type": "tool_result", "tool": <name>, "result": <str>}
          - {"type": "final", "content": <markdown>}
        Suitable for adapting into MLflow ResponsesAgent stream events.
        """
        input_messages = self._to_messages(conversation)
        stream_iter = self._agent.stream(
            {"messages": input_messages},
            stream_mode="values",
        )

        # Skip the prior turns we sent in (history) so we only stream new steps.
        seen_msgs = len(input_messages)
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
