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
        Generate a natural language summary of the workflow execution.
        
        Args:
            state: The complete workflow state
            
        Returns:
            String containing natural language summary
        """
        # Build context from state
        summary_prompt = self._build_summary_prompt(state)
        
        # Stream LLM response for immediate first token emission
        print("🤖 Streaming summary generation...")
        summary = ""
        for chunk in self.llm.stream(summary_prompt):
            if chunk.content:
                summary += chunk.content
        
        summary = summary.strip()
        print(f"✓ Summary stream complete ({len(summary)} chars)")
        
        # Append Option B downloadable tables if query execution was successful
        exec_result = state.get('execution_result', {})
        if exec_result and exec_result.get('success'):
            columns = exec_result.get('columns', [])
            result = exec_result.get('result', [])
            
            if columns and result:
                option_b_tables = self._format_option_b_tables(columns, result, display_rows=100)
                summary += option_b_tables
                print(f"✓ Appended Option B downloadable tables ({len(option_b_tables)} chars)")
        
        return summary
    
    def _format_option_b_tables(
        self,
        columns: List[str],
        data: List[Dict[str, Any]],
        display_rows: int = 100
    ) -> str:
        """
        Generate Option B downloadable table formats for Databricks Playground:
        - Single scrollable markdown table (all rows in one table)
        - Full JSON export (all rows in collapsible section)
        
        Args:
            columns: List of column names
            data: List of row dictionaries
            display_rows: Number of rows to display (default 100)
            
        Returns:
            Formatted markdown string with collapsible sections
        """
        if not data or not columns:
            return ""
        
        # Limit to display_rows
        display_data = data[:display_rows]
        total_rows = len(data)
        
        markdown = "\n\n---\n\n## 📥 Downloadable Results\n\n"
        
        # Part 1: Single Scrollable Markdown Table
        markdown += "### Markdown Table (Scrollable)\n\n"
        markdown += f"<details>\n<summary>📄 View Full Table ({len(display_data)} rows) - Click to expand</summary>\n\n"
        
        # Generate single markdown table with all rows
        markdown += "| " + " | ".join(columns) + " |\n"
        markdown += "| " + " | ".join(["---"] * len(columns)) + " |\n"
        
        for row in display_data:
            row_values = [str(row.get(col, "")) for col in columns]
            markdown += "| " + " | ".join(row_values) + " |\n"
        
        markdown += "\n</details>\n\n"
        
        # Part 2: Full JSON Export
        markdown += "### JSON Format (All Rows)\n\n"
        markdown += "<details>\n<summary>📋 JSON Export (click to expand)</summary>\n\n"
        markdown += "```json\n"
        markdown += self._safe_json_dumps({
            "columns": columns,
            "data": display_data,
            "row_count": len(display_data)
        }, indent=2)
        markdown += "\n```\n\n"
        markdown += "</details>\n\n"
        
        if total_rows > display_rows:
            markdown += f"*Note: Showing top {display_rows} of {total_rows} total rows in downloadable format above.*\n"
        
        return markdown
    
    def _build_summary_prompt(self, state: AgentState) -> str:
        """Build the prompt for summary generation based on state."""
        
        original_query = state.get('original_query', 'N/A')
        question_clear = state.get('question_clear', False)
        pending_clarification = state.get('pending_clarification')
        execution_plan = state.get('execution_plan')
        join_strategy = state.get('join_strategy')
        sql_query = state.get('sql_query')
        sql_explanation = state.get('sql_synthesis_explanation')
        exec_result = state.get('execution_result', {})
        synthesis_error = state.get('synthesis_error')
        execution_error = state.get('execution_error')
        
        prompt = f"""You are a result summarization agent. Generate a concise, natural language summary of what this multi-agent workflow accomplished.

**Original User Query:** {original_query}

**Workflow Execution Details:**

"""
        
        # Add clarification info
        if not question_clear and pending_clarification:
            clarification_reason = pending_clarification.get('reason', 'Query needs clarification')
            prompt += f"""**Status:** Query needs clarification
**Clarification Needed:** {clarification_reason}
**Summary:** The query was too vague or ambiguous. Requested user clarification before proceeding.
"""
        else:
            # Add planning info
            if execution_plan:
                prompt += f"""**Planning:** {execution_plan}
**Strategy:** {join_strategy or 'N/A'}

"""
            
            # Add SQL synthesis info
            if sql_query:
                prompt += f"""**SQL Generation:** ✅ Successful
**SQL Query:** 
```sql
{sql_query}
```

"""
                if sql_explanation:
                    prompt += f"""**SQL Synthesis Explanation:** {sql_explanation[:2000]}{'...' if len(sql_explanation) > 2000 else ''}

"""
                
                # Add execution info
                if exec_result.get('success'):
                    row_count = exec_result.get('row_count', 0)
                    columns = exec_result.get('columns', [])
                    result = exec_result.get('result', [])
                    
                    # TOKEN PROTECTION: Sample results to prevent huge prompts
                    # - Max 10 rows
                    # - Max 10 columns per row
                    # - Max 5000 characters for JSON
                    MAX_PREVIEW_ROWS = 20
                    MAX_PREVIEW_COLS = 20
                    MAX_JSON_CHARS = 2000
                    
                    # Sample rows
                    result_preview = result[:MAX_PREVIEW_ROWS] if len(result) > MAX_PREVIEW_ROWS else result
                    
                    # Sample columns (if result has too many columns)
                    if result_preview and len(columns) > MAX_PREVIEW_COLS:
                        # Keep only first MAX_PREVIEW_COLS columns
                        sampled_cols = columns[:MAX_PREVIEW_COLS]
                        result_preview = [
                            {k: v for k, v in row.items() if k in sampled_cols}
                            for row in result_preview
                        ]
                        col_display = ', '.join(sampled_cols) + f'... (+{len(columns) - MAX_PREVIEW_COLS} more columns)'
                    else:
                        col_display = ', '.join(columns[:10]) + ('...' if len(columns) > 10 else '')
                    
                    # Serialize to JSON
                    result_json = self._safe_json_dumps(result_preview, indent=2)
                    
                    # Truncate JSON if too large
                    if len(result_json) > MAX_JSON_CHARS:
                        result_json = result_json[:MAX_JSON_CHARS] + f'\n... (truncated, {len(result_json) - MAX_JSON_CHARS} chars omitted)'
                    
                    prompt += f"""**Execution:** ✅ Successful
**Rows:** {row_count} rows returned{f' (showing first {MAX_PREVIEW_ROWS})' if row_count > MAX_PREVIEW_ROWS else ''}
**Columns:** {col_display}

**Result Preview:** 
{result_json}
{f'... and {row_count - MAX_PREVIEW_ROWS} more rows' if row_count > MAX_PREVIEW_ROWS else ''}
"""
                elif execution_error:
                    prompt += f"""**Execution:** ❌ Failed
**Error:** {execution_error}

"""
            elif synthesis_error:
                prompt += f"""**SQL Generation:** ❌ Failed
**Error:** {synthesis_error}
**Explanation:** {sql_explanation or 'N/A'}

"""
        
        prompt += """
**Task:** Generate a detailed summary in natural language that:
1. Describes what the user asked for
2. Explains what the system did (planning, SQL generation, execution)
3. States the outcome (success with X rows, error, needs clarification, etc.)
4. print out SQL synthesis explanation if any SQL was generated
5. print out SQL if any SQL was generated; make it the code block. If multiple SQL queries were generated, print out them in separate code blocks.
6. print out the result itself (markdown formatted as a table). If multiple result sets were generated, print out them in separate tables.
7. summarize the insights from the result itself (markdown formatted as a list of bullets).
8. if multiple result sets were generated, summarize the insights from each result set in a separate list of bullets.


Keep it concise and user-friendly. 
"""
        
        return prompt
    
    def __call__(self, state: AgentState) -> str:
        """Make agent callable."""
        return self.generate_summary(state)
