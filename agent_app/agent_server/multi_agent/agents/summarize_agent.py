"""
Result Summarize Agent

This module provides the ResultSummarizeAgent class for generating final summaries
of workflow execution results.

The agent analyzes the entire workflow state and produces a natural language summary
of what was accomplished, whether successful or not.

OOP design for clean summarization logic.
"""

import json
from typing import Dict, Any, List
from datetime import date, datetime
from decimal import Decimal

from langchain_core.runnables import Runnable

from ..core.state import AgentState


class ResultSummarizeAgent:
    """
    Agent responsible for generating a final summary of the workflow execution.
    
    Analyzes the entire workflow state and produces a natural language summary
    of what was accomplished, whether successful or not.
    
    OOP design for clean summarization logic.
    """
    
    def __init__(self, llm: Runnable):
        """
        Initialize Result Summarize Agent.
        
        Args:
            llm: LangChain Runnable LLM instance for generating summaries
        """
        self.name = "ResultSummarize"
        self.llm = llm
    
    @staticmethod
    def _safe_json_dumps(obj: Any, indent: int = 2) -> str:
        """
        Safely serialize objects to JSON, normalizing common non-JSON types.
        
        Args:
            obj: Object to serialize
            indent: JSON indentation level
            
        Returns:
            JSON string with common non-JSON types converted to serializable values
        """
        def normalize(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(k): normalize(v) for k, v in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, tuple):
                return [normalize(item) for item in value]
            if isinstance(value, set):
                return [normalize(item) for item in value]
            if isinstance(value, (date, datetime)):
                return value.isoformat()
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")

            try:
                import numpy as np  # type: ignore

                if isinstance(value, np.ndarray):
                    return normalize(value.tolist())
                if isinstance(value, np.generic):
                    return normalize(value.item())
            except ImportError:
                pass

            if hasattr(value, "tolist") and callable(value.tolist):
                try:
                    return normalize(value.tolist())
                except Exception:
                    pass

            if hasattr(value, "item") and callable(value.item):
                try:
                    return normalize(value.item())
                except Exception:
                    pass

            if hasattr(value, "isoformat") and callable(value.isoformat):
                try:
                    return value.isoformat()
                except Exception:
                    pass

            if isinstance(value, (str, int, float, bool)) or value is None:
                return value

            raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")

        return json.dumps(normalize(obj), indent=indent)
    
    def generate_summary(self, state: AgentState, writer=None) -> str:
        """
        Generate a clean natural language summary (no SQL, no workflow sections).
        Chart blocks and SQL downloads are appended by summarize_node().
        When *writer* is provided, each token chunk is emitted as a text_delta
        custom event for real-time streaming to the frontend.
        """
        summary_prompt = self._build_summary_prompt(state)

        print("🤖 Streaming summary generation...")
        summary = ""
        for chunk in self.llm.stream(summary_prompt):
            if chunk.content:
                summary += chunk.content
                if writer:
                    writer({"type": "text_delta", "content": chunk.content})

        summary = summary.strip()
        print(f"✓ Summary stream complete ({len(summary)} chars)")
        return summary
    
    @staticmethod
    def format_sql_download(
        artifact_entries: List[dict],
        total_entries: int | None = None,
    ) -> str:
        """Collapsible SQL section with small data-URI download link."""
        if not artifact_entries:
            return ""
        import base64

        parts: list[str] = ['\n\n<details name="sql-accordion"><summary>Show SQL</summary>\n\n<div class="accordion-content">\n\n']
        total_entries = total_entries or len(artifact_entries)
        for entry in artifact_entries:
            sql = ResultSummarizeAgent.resolve_sql_download_text(entry)
            label = entry.get("label") or ""
            fname = ResultSummarizeAgent.get_sql_download_filename(
                entry,
                total_entries=total_entries,
            )
            encoded = base64.b64encode(sql.encode()).decode()
            # Use a custom language tag so the frontend renders copy+download buttons.
            # Format: ```sql-download:filename:base64data\n{sql}\n```
            meta = f"{fname}:{encoded}"
            parts.append(f"\n{'**' + label + '**' + chr(10) if label else ''}"
                         f"```sql-download:{meta}\n{sql}\n```\n")
        parts.append("\n\n</div>\n</details>\n")
        return "".join(parts)

    @staticmethod
    def resolve_sql_download_text(entry: dict) -> str:
        sql = entry.get("sql") or ""
        status = entry.get("status", "success")
        if sql:
            return sql
        if status == "skipped":
            return (
                "-- No SQL generated for this planned query.\n"
                f"-- {entry.get('skip_reason', 'Skipped because the question was already covered.')}"
            )
        return "-- No SQL captured for this query."

    @staticmethod
    def get_sql_download_filename(entry: dict, total_entries: int = 1) -> str:
        entry_index = entry.get("index")
        if isinstance(entry_index, int) and total_entries > 1:
            return f"query_{entry_index + 1}.sql"
        return "query.sql"

    @staticmethod
    def _normalize_markdown_block(text: str) -> str:
        import re

        normalized = (text or "").replace("\r\n", "\n").strip()
        normalized = re.sub(r"^\s*---\s*$", "", normalized, flags=re.MULTILINE)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        normalized = re.sub(r"[ \t]+\n", "\n", normalized)
        return normalized.strip()

    @staticmethod
    def _format_attempt_context(entry: dict, attempt_number: int) -> str:
        route = entry.get("route", "unknown")
        loop_reason = entry.get("loop_reason")
        retry_count = entry.get("retry_count", 0)
        sequential_step = entry.get("sequential_step", 0)
        synthesis_error = entry.get("synthesis_error")
        query_labels = entry.get("query_labels") or []

        route_label = "Table Route" if route == "table" else "Genie Route" if route == "genie" else route.title()
        mode_label = "Initial synthesis"
        if loop_reason == "retry":
            mode_label = f"Retry attempt {retry_count + 1}"
        elif loop_reason == "sequential_next":
            mode_label = f"Sequential step {sequential_step + 1}"

        lines = [f"### Attempt {attempt_number}", "", f"- **Route:** {route_label}", f"- **Context:** {mode_label}"]
        if query_labels:
            lines.append(f"- **Queries:** {', '.join(label for label in query_labels if label)}")
        if synthesis_error:
            lines.append(f"- **Status:** Synthesis issue detected")
        return "\n".join(lines)

    @staticmethod
    def _format_artifact_context(entry: dict, query_number: int) -> str:
        label = entry.get("label") or f"Query {query_number}"
        status = entry.get("status", "success")
        lines = [f"### Query {query_number} — {label}", ""]
        if status == "skipped":
            lines.append("- **Status:** Skipped / already covered")
        elif status == "failed":
            lines.append("- **Status:** Failed")
        else:
            lines.append("- **Status:** Executed")

        skip_reason = entry.get("skip_reason")
        if skip_reason:
            lines.append(f"- **Reason:** {skip_reason}")
        return "\n".join(lines)

    @staticmethod
    def format_sql_explanation(
        artifact_entries: List[dict] | None = None,
    ) -> str:
        """Collapsible SQL explanation section with clean markdown formatting."""
        entries = list(artifact_entries or [])
        relevant = [e for e in entries if e.get("sql_explanation") or e.get("skip_reason")]
        if not relevant:
            return ""

        normalize = ResultSummarizeAgent._normalize_markdown_block
        format_ctx = ResultSummarizeAgent._format_artifact_context

        parts: list[str] = [
            '\n\n<details name="sql-accordion"><summary>SQL Explanation</summary>'
            '\n\n<div class="accordion-content">\n\n'
        ]

        if len(relevant) > 1:
            per_entry_text = [
                normalize(e.get("sql_explanation") or e.get("skip_reason") or "")
                for e in relevant
            ]
            from collections import Counter
            counts = Counter(t for t in per_entry_text if t)
            shared_texts = {t for t, c in counts.items() if c > 1}

            if shared_texts:
                for shared in shared_texts:
                    parts.append(shared)
                    parts.append("\n\n")
                for idx, (entry, text) in enumerate(zip(relevant, per_entry_text), 1):
                    parts.append(format_ctx(entry, idx))
                    parts.append("\n\n")
                    if text and text not in shared_texts:
                        parts.append(text)
                        parts.append("\n\n")
            else:
                for idx, (entry, text) in enumerate(zip(relevant, per_entry_text), 1):
                    parts.append(format_ctx(entry, idx))
                    parts.append("\n\n")
                    if text:
                        parts.append(text)
                        parts.append("\n\n")
        else:
            entry = relevant[0]
            parts.append(format_ctx(entry, 1))
            parts.append("\n\n")
            explanation = entry.get("sql_explanation") or entry.get("skip_reason") or ""
            normalized = normalize(explanation)
            if normalized:
                parts.append(normalized)
                parts.append("\n\n")

        parts.append("\n\n</div>\n</details>\n")
        return "".join(parts)

    @staticmethod
    def format_plan_executed(plan: dict) -> str:
        """Collapsible plan section with JSON code block (copy + download)."""
        if not plan:
            return ""
        import base64

        plan_json = ResultSummarizeAgent._safe_json_dumps(plan, indent=2)
        encoded = base64.b64encode(plan_json.encode()).decode()
        meta = f"plan.json:{encoded}"
        return (
            '\n\n<details name="sql-accordion"><summary>Plan Executed</summary>\n\n<div class="accordion-content">\n\n'
            f"```json-download:{meta}\n{plan_json}\n```\n"
            "\n</div>\n</details>\n"
        )

    def _build_summary_prompt(self, state: AgentState) -> str:
        """Build a prompt that produces a clean narrative summary.

        The LLM should NOT emit SQL blocks or workflow details — those are
        appended by summarize_node() as collapsible sections / chart blocks.
        """
        original_query = state.get('original_query', 'N/A')
        synthesis_error = state.get('synthesis_error')
        execution_error = state.get('execution_error')

        prompt = f"""You are a result summarization agent. Produce a clean, reader-friendly markdown summary.

**User Question:** {original_query}

"""
        if synthesis_error:
            prompt += f"**SQL Generation Failed:** {synthesis_error}\n"
        if execution_error:
            prompt += f"**Execution Failed:** {execution_error}\n"

        execution_results = state.get('execution_results') or []
        if not execution_results and state.get('execution_result'):
            execution_results = [state['execution_result']]

        MAX_PREVIEW = 200
        MAX_JSON = 20000
        successful_results = [result for result in execution_results if result and result.get("success")]
        skipped_results = [result for result in execution_results if result and result.get("status") == "skipped"]
        failed_results = [
            result
            for result in execution_results
            if result and not result.get("success") and result.get("status") != "skipped"
        ]

        prompt += (
            f"**Result sets:** {len(execution_results)} total, "
            f"{len(successful_results)} successful, "
            f"{len(skipped_results)} skipped, "
            f"{len(failed_results)} failed\n"
        )

        for i, result in enumerate(execution_results):
            status = result.get("status")
            label = result.get("query_label")
            label_suffix = f" — {label}" if label else ""
            if status == "skipped":
                prompt += (
                    f"\n**Query {i+1}{label_suffix}:** Skipped — "
                    f"{result.get('skip_reason', 'already covered by prior results')}\n"
                )
                continue
            if not result or not result.get('success'):
                prompt += f"\n**Query {i+1}{label_suffix}:** Failed — {result.get('error', 'unknown')}\n"
                continue
            row_count = result.get('row_count', 0)
            columns = result.get('columns', [])
            data = result.get('result', [])
            preview = data[:MAX_PREVIEW]
            preview_json = self._safe_json_dumps(preview, indent=2)
            if len(preview_json) > MAX_JSON:
                preview_json = preview_json[:MAX_JSON] + "\n..."
            prompt += f"""
**Query {i+1}{label_suffix} Result:** {row_count} rows, columns: {', '.join(columns[:12])}{'...' if len(columns) > 12 else ''}
Data preview:
{preview_json}
"""

        prompt += """
**Instructions — follow strictly:**
1. Start with a descriptive ## title for the analysis
2. Write a concise narrative answering the user's question with formatted numbers ($X,XXX,XXX.XX for currency, commas for counts)
3. If there is more than one result set, create one `###` subsection per result set in query order. Use the query label when available.
4. For each successful result set, include a markdown table for that result set only. If the result has <=30 rows, include all rows. Otherwise include the top 20 most relevant rows and keep it clearly labeled as a preview.
5. Keep result sets separate. Do not merge multiple result sets into one markdown table.
6. **IMPORTANT — Safe inference:** Only make claims directly supported by the provided rows/columns. If a result may contain repeated entities (for example multiple coverage rows per member, multiple benefit types per patient, or repeated diagnosis rows), do NOT infer distinct member counts, payer counts, or cohort composition unless the result explicitly includes distinct counts.
7. **IMPORTANT — Code annotation:** If ANY column contains coded identifiers rather than plain text (e.g., NDC drug codes, ICD/CPT/HCPCS medical codes, NPI numbers, taxonomy codes, NAICS/SIC industry codes, MCC merchant codes, CUSIP/ISIN/ticker symbols, FIPS/ZIP codes, currency codes, tax form codes, GL account codes, or ANY other standardized code system), you MUST add a "Description" column with the human-readable name/meaning for each code. Use your domain knowledge to decode every code. Never present a table with coded columns that lack descriptions. If you cannot confidently decode a specific value, simply write "Unknown" or leave it blank. Do not write long disclaimers.
8. Add a ### Key Insights section with 2-4 bullet points grounded in the result sets

**DO NOT include:**
- SQL queries or code blocks (those are shown separately)
- Workflow/planning details
- Emoji prefixes
- JSON dumps
- Statements about how many charts, downloadable tables, or accordion sections are shown; those are rendered separately
"""
        return prompt
    
    def __call__(self, state: AgentState) -> str:
        """Make agent callable."""
        return self.generate_summary(state)
