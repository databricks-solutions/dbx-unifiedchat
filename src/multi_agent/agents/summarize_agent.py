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
        # Support multiple results
        execution_results = state.get('execution_results', [])
        exec_result = state.get('execution_result', {})
        
        if not execution_results and exec_result:
            execution_results = [exec_result]
        
        for idx, result_item in enumerate(execution_results):
            if result_item and result_item.get('success'):
                columns = result_item.get('columns', [])
                result = result_item.get('result', [])
                
                if columns and result:
                    label_suffix = f" (Query {idx + 1})" if len(execution_results) > 1 else ""
                    option_b_tables = self._format_option_b_tables(columns, result, display_rows=100)
                    if len(execution_results) > 1:
                        option_b_tables = option_b_tables.replace("## 📥 Downloadable Results", f"## 📥 Downloadable Results{label_suffix}")
                    summary += option_b_tables
                    print(f"✓ Appended Option B downloadable tables{label_suffix} ({len(option_b_tables)} chars)")
        
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
            
            # NEW: Check for multiple SQL queries and results
            sql_queries = state.get('sql_queries', [])
            query_labels = state.get('sql_query_labels', [])
            execution_results = state.get('execution_results', [])
            
            # Fallback to single query/result for backward compatibility
            if not sql_queries and sql_query:
                sql_queries = [sql_query]
            if not execution_results and exec_result:
                execution_results = [exec_result]
            
            # Add SQL synthesis info
            if sql_queries:
                if len(sql_queries) == 1:
                    # Single query (original behavior)
                    label = query_labels[0] if query_labels else ""
                    label_display = f" — {label}" if label else ""
                    prompt += f"""**SQL Generation:** ✅ Successful{label_display}
**SQL Query:** 
```sql
{sql_queries[0]}
```

"""
                else:
                    # Multiple queries
                    prompt += f"""**SQL Generation:** ✅ Successful ({len(sql_queries)} queries for multi-part question)

"""
                    for i, query in enumerate(sql_queries, 1):
                        label = query_labels[i-1] if i <= len(query_labels) and query_labels[i-1] else ""
                        label_display = f" — {label}" if label else ""
                        prompt += f"""**SQL Query {i}{label_display}:** 
```sql
{query}
```

"""
                
                if sql_explanation:
                    prompt += f"""**SQL Synthesis Explanation:** {sql_explanation[:2000]}{'...' if len(sql_explanation) > 2000 else ''}

"""
                
                # TOKEN PROTECTION: Sample results to prevent huge prompts
                MAX_PREVIEW_ROWS = 20
                MAX_PREVIEW_COLS = 20
                MAX_JSON_CHARS = 2000
                
                # Add execution info (single or multiple results)
                if execution_results:
                    if len(execution_results) == 1:
                        # Single result (original behavior with token protection)
                        result = execution_results[0]
                        if result.get('success'):
                            row_count = result.get('row_count', 0)
                            columns = result.get('columns', [])
                            result_data = result.get('result', [])
                            
                            # Sample rows
                            result_preview = result_data[:MAX_PREVIEW_ROWS] if len(result_data) > MAX_PREVIEW_ROWS else result_data
                            
                            # Sample columns (if result has too many columns)
                            if result_preview and len(columns) > MAX_PREVIEW_COLS:
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
                        else:
                            prompt += f"""**Execution:** ❌ Failed
**Error:** {result.get('error', 'Unknown error')}

"""
                    else:
                        # Multiple results
                        all_successful = all(r.get('success') for r in execution_results)
                        total_rows = sum(r.get('row_count', 0) for r in execution_results if r.get('success'))
                        
                        if all_successful:
                            prompt += f"""**Execution:** ✅ All {len(execution_results)} queries executed successfully
**Total Rows Returned:** {total_rows}

"""
                        else:
                            failed_count = sum(1 for r in execution_results if not r.get('success'))
                            prompt += f"""**Execution:** ⚠️ Partial success ({len(execution_results) - failed_count} succeeded, {failed_count} failed)

"""
                        
                        # Add details for each result
                        for i, result in enumerate(execution_results, 1):
                            if result.get('success'):
                                row_count = result.get('row_count', 0)
                                columns = result.get('columns', [])
                                result_data = result.get('result', [])
                                
                                # Token protection per result
                                result_preview = result_data[:MAX_PREVIEW_ROWS] if len(result_data) > MAX_PREVIEW_ROWS else result_data
                                
                                if result_preview and len(columns) > MAX_PREVIEW_COLS:
                                    sampled_cols = columns[:MAX_PREVIEW_COLS]
                                    result_preview = [
                                        {k: v for k, v in row.items() if k in sampled_cols}
                                        for row in result_preview
                                    ]
                                    col_display = ', '.join(sampled_cols) + f'... (+{len(columns) - MAX_PREVIEW_COLS} more columns)'
                                else:
                                    col_display = ', '.join(columns[:10]) + ('...' if len(columns) > 10 else '')
                                
                                result_json = self._safe_json_dumps(result_preview, indent=2)
                                if len(result_json) > MAX_JSON_CHARS:
                                    result_json = result_json[:MAX_JSON_CHARS] + f'\n... (truncated)'
                                
                                prompt += f"""**Query {i} Result:**
- Rows: {row_count}{f' (showing first {MAX_PREVIEW_ROWS})' if row_count > MAX_PREVIEW_ROWS else ''}
- Columns: {col_display}
- Data: {result_json}

"""
                            else:
                                prompt += f"""**Query {i} Result:**
- Status: ❌ Failed
- Error: {result.get('error', 'Unknown error')}

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
**Task:** Generate a comprehensive summary in natural language that:
1. Describes what the user asked for
2. Explains what the system did (planning, SQL generation, execution)
3. For multi-part questions with multiple queries:
   - Explain each sub-question that was addressed
   - Show each SQL query in its own code block with a clear label
   - Present each query's results in a clear, readable format (preferably as a markdown table)
   - Provide insights and analysis for each result
   - Synthesize an overall conclusion combining insights from all queries
4. For single queries:
   - Print out SQL synthesis explanation if any SQL was generated
   - Print out the SQL query in a code block
   - Print out the result in a readable format (preferably as a markdown table)
   - Provide insights and analysis for the result
5. **Code Annotation for Human Readability:**
   - For each result table, scan the columns for raw codes (e.g., diagnosis_code, procedure_code, ICD codes, CPT codes, not limited to medical domain)
   - If you find columns containing raw codes WITHOUT corresponding human-readable description columns:
     * Add a new column with a descriptive name like "{code_column}_description" 
     * Populate it with human-readable descriptions/meanings of those codes
     * Use your knowledge base to translate common codes (ICD-10, CPT, etc.) into plain language
     * Example: diagnosis_code "I10" → diagnosis_code_description "Essential (primary) hypertension"
     * Example: procedure_code "99213" → procedure_code_description "Office visit, established patient, 20-29 minutes"
   - Present the enhanced table with both the original codes and the new description columns
   - This makes the results more interpretable for non-technical users
6. States the outcome (success with X rows, error, needs clarification, etc.)

Use markdown formatting for readability. Keep it clear and user-friendly. 
"""
        
        return prompt
    
    def __call__(self, state: AgentState) -> str:
        """Make agent callable."""
        return self.generate_summary(state)
