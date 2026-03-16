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
        Safely serialize objects to JSON, converting dates/datetime to strings.
        
        Args:
            obj: Object to serialize
            indent: JSON indentation level
            
        Returns:
            JSON string with date/datetime objects converted to ISO format strings
        """
        def default_handler(o):
            if isinstance(o, (date, datetime)):
                return o.isoformat()
            elif isinstance(o, Decimal):
                return float(o)
            else:
                raise TypeError(f'Object of type {o.__class__.__name__} is not JSON serializable')
        
        return json.dumps(obj, indent=indent, default=default_handler)
    
    def generate_summary(self, state: AgentState) -> str:
        """
        Generate a clean natural language summary (no SQL, no workflow sections).
        Chart blocks and SQL downloads are appended by summarize_node().
        """
        summary_prompt = self._build_summary_prompt(state)

        print("🤖 Streaming summary generation...")
        summary = ""
        for chunk in self.llm.stream(summary_prompt):
            if chunk.content:
                summary += chunk.content

        summary = summary.strip()
        print(f"✓ Summary stream complete ({len(summary)} chars)")
        return summary
    
    @staticmethod
    def format_sql_download(sql_queries: List[str], labels: List[str] | None = None) -> str:
        """Collapsible SQL section with small data-URI download link."""
        if not sql_queries:
            return ""
        import base64

        parts: list[str] = ["\n\n---\n\n<details><summary>Show SQL</summary>\n"]
        for idx, sql in enumerate(sql_queries):
            if labels and idx < len(labels) and labels[idx]:
                parts.append(f"\n**{labels[idx]}**\n")
            parts.append(f"\n```sql\n{sql}\n```\n")
            encoded = base64.b64encode(sql.encode()).decode()
            fname = f"query{'_' + str(idx + 1) if len(sql_queries) > 1 else ''}.sql"
            parts.append(f'\n<a href="data:text/sql;base64,{encoded}" download="{fname}">Download {fname}</a>\n')
        parts.append("\n</details>\n")
        return "".join(parts)
    
    def _build_summary_prompt(self, state: AgentState) -> str:
        """Build a prompt that produces a clean narrative summary.

        The LLM should NOT emit SQL blocks or workflow details — those are
        appended by summarize_node() as collapsible sections / chart blocks.
        """
        original_query = state.get('original_query', 'N/A')
        question_clear = state.get('question_clear', False)
        pending_clarification = state.get('pending_clarification')
        synthesis_error = state.get('synthesis_error')
        execution_error = state.get('execution_error')

        prompt = f"""You are a result summarization agent. Produce a clean, reader-friendly markdown summary.

**User Question:** {original_query}

"""
        if not question_clear and pending_clarification:
            reason = pending_clarification.get('reason', 'Query needs clarification')
            prompt += f"**Status:** Needs clarification — {reason}\n"
            prompt += "\nGenerate a short message explaining what additional information is needed.\n"
            return prompt

        if synthesis_error:
            prompt += f"**SQL Generation Failed:** {synthesis_error}\n"
        if execution_error:
            prompt += f"**Execution Failed:** {execution_error}\n"

        sql_queries = state.get('sql_queries', [])
        if not sql_queries and state.get('sql_query'):
            sql_queries = [state['sql_query']]
        execution_results = state.get('execution_results', [])
        if not execution_results and state.get('execution_result'):
            execution_results = [state['execution_result']]

        MAX_PREVIEW = 20
        MAX_JSON = 2000

        for i, result in enumerate(execution_results):
            if not result or not result.get('success'):
                prompt += f"\n**Query {i+1}:** Failed — {result.get('error', 'unknown')}\n"
                continue
            row_count = result.get('row_count', 0)
            columns = result.get('columns', [])
            data = result.get('result', [])
            preview = data[:MAX_PREVIEW]
            preview_json = self._safe_json_dumps(preview, indent=2)
            if len(preview_json) > MAX_JSON:
                preview_json = preview_json[:MAX_JSON] + "\n..."

            label = ""
            labels = state.get('sql_query_labels', [])
            if labels and i < len(labels):
                label = f" — {labels[i]}"

            prompt += f"""
**Query {i+1}{label} Result:** {row_count} rows, columns: {', '.join(columns[:12])}{'...' if len(columns) > 12 else ''}
Data preview:
{preview_json}
"""

        prompt += """
**Instructions — follow strictly:**
1. Start with a descriptive ## title for the analysis
2. Write a concise narrative answering the user's question with formatted numbers ($X,XXX,XXX.XX for currency, commas for counts)
3. Present results in a well-formatted markdown table (include ALL data rows if <=30, otherwise top 20)
4. If columns contain raw codes (ICD, CPT, etc.) without descriptions, add a description column with human-readable meanings
5. Add a ### Key Insights section with 2-4 bullet points

**DO NOT include:**
- SQL queries or code blocks (those are shown separately)
- Workflow/planning details
- Emoji prefixes
- JSON dumps
"""
        return prompt
    
    def __call__(self, state: AgentState) -> str:
        """Make agent callable."""
        return self.generate_summary(state)
