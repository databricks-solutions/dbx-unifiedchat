"""
Result Summarize Agent Node

This module provides the result summarization node for the multi-agent system.
It wraps the ResultSummarizeAgent class and generates natural language summaries
of workflow execution results.

This is the final node that all workflow paths go through.
The node is optimized to use minimal state extraction to reduce token usage.
"""

from typing import Dict, Any, List
from langgraph.config import get_stream_writer
from langchain_core.messages import AIMessage, SystemMessage

from ..core.state import AgentState
from ..core.config import get_config


def truncate_message_history(
    messages: List,
    max_turns: int = 5,
    keep_system: bool = True
) -> List:
    """
    Keep only recent turns + system messages.
    
    Args:
        messages: Full message history
        max_turns: Number of recent turns to keep (default 5)
        keep_system: Whether to preserve all SystemMessage instances
        
    Returns:
        Truncated message list
    """
    if not messages:
        return []
    
    # Separate system messages from conversation
    system_msgs = []
    conversation_msgs = []
    
    for msg in messages:
        if isinstance(msg, SystemMessage) and keep_system:
            system_msgs.append(msg)
        else:
            conversation_msgs.append(msg)
    
    # Keep only last N turns (each turn = HumanMessage + AIMessage pair)
    recent_msgs = conversation_msgs[-(max_turns * 2):] if len(conversation_msgs) > max_turns * 2 else conversation_msgs
    
    return system_msgs + recent_msgs


def extract_summarize_context(state: AgentState) -> dict:
    """
    Extract minimal context for result summarization.
    
    OPTIMIZED: Applies message history truncation
    """
    messages = state.get("messages", [])
    
    return {
        "messages": truncate_message_history(messages, max_turns=5),
        "sql_query": state.get("sql_query"),
        "sql_queries": state.get("sql_queries", []),
        "sql_query_labels": state.get("sql_query_labels", []),
        "execution_result": state.get("execution_result"),
        "execution_results": state.get("execution_results", []),
        "execution_error": state.get("execution_error"),
        "sql_synthesis_explanation": state.get("sql_synthesis_explanation"),
        "synthesis_error": state.get("synthesis_error"),
        # For logging: track original size
        "_original_message_count": len(messages)
    }


def get_cached_summarize_agent():
    """
    Get or create cached ResultSummarizeAgent instance.
    Expected gain: -100ms to -300ms per request
    """
    # Module-level cache
    if not hasattr(get_cached_summarize_agent, "_cached_agent"):
        print("⚡ Creating ResultSummarizeAgent (first use)...")
        config = get_config()
        llm_endpoint = config.llm.summarize_endpoint
        
        # Create LLM instance
        try:
            from databricks_langchain import ChatDatabricks
            from .summarize_agent import ResultSummarizeAgent
            llm = ChatDatabricks(endpoint=llm_endpoint, temperature=0.1, max_tokens=5000)
            get_cached_summarize_agent._cached_agent = ResultSummarizeAgent(llm)
        except ImportError:
            # Fallback: Create a simple wrapper
            llm = ChatDatabricks(endpoint=llm_endpoint, temperature=0.1, max_tokens=5000)
            get_cached_summarize_agent._cached_agent = _SimpleSummarizeAgent(llm)
        except Exception as e:
             raise ImportError(
                f"databricks_langchain is required or Error: {e}. Install with: pip install databricks-langchain"
            )
        print("✓ ResultSummarizeAgent cached")
    else:
        print("✓ Using cached ResultSummarizeAgent")
    
    return get_cached_summarize_agent._cached_agent


def track_agent_model_usage(agent_name: str, model_endpoint: str):
    """
    Track which LLM model is used by each agent for monitoring and cost analysis.
    
    Args:
        agent_name: Name of the agent (e.g., "summarize")
        model_endpoint: LLM endpoint being used (e.g., "databricks-claude-haiku-4-5")
    """
    print(f"📊 Agent '{agent_name}' using model: {model_endpoint}")


def measure_node_time(node_name: str):
    """
    Decorator to measure node execution time.
    Expected use: Track per-node performance for optimization.
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            import time
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                print(f"⏱️  {node_name}: {elapsed:.3f}s")
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                print(f"⏱️  {node_name}: {elapsed:.3f}s (FAILED)")
                raise
        return wrapper
    return decorator


class _SimpleSummarizeAgent:
    """
    Simple fallback summarize agent implementation.
    
    In production, use the full ResultSummarizeAgent class.
    """
    def __init__(self, llm):
        self.name = "ResultSummarize"
        self.llm = llm
    
    def __call__(self, state: dict) -> str:
        """Generate summary from state."""
        # Build prompt from state
        prompt = self._build_summary_prompt(state)
        
        # Stream LLM response
        print("🤖 Streaming summary generation...")
        summary = ""
        for chunk in self.llm.stream(prompt):
            if chunk.content:
                summary += chunk.content
        
        summary = summary.strip()
        print(f"✓ Summary stream complete ({len(summary)} chars)")
        
        return summary
    
    def _build_summary_prompt(self, state: dict) -> str:
        """Build the prompt for summary generation based on state."""
        original_query = state.get('original_query', 'N/A')
        sql_query = state.get('sql_query')
        sql_explanation = state.get('sql_synthesis_explanation')
        exec_result = state.get('execution_result', {})
        synthesis_error = state.get('synthesis_error')
        execution_error = state.get('execution_error')
        
        prompt = f"""You are a result summarization agent. Generate a concise, natural language summary of what this multi-agent workflow accomplished.

**Original User Query:** {original_query}

**Workflow Execution Details:**

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
        
        if sql_queries:
            if len(sql_queries) == 1:
                label = query_labels[0] if query_labels else ""
                label_display = f" — {label}" if label else ""
                prompt += f"""**SQL Generation:** ✅ Successful{label_display}
**SQL Query:** 
```sql
{sql_queries[0]}
```

"""
            else:
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
            
            MAX_PREVIEW_ROWS = 20
            MAX_JSON_CHARS = 2000
            
            if execution_results:
                if len(execution_results) == 1:
                    result = execution_results[0]
                    if result.get('success'):
                        row_count = result.get('row_count', 0)
                        columns = result.get('columns', [])
                        result_data = result.get('result', [])
                        result_preview = result_data[:MAX_PREVIEW_ROWS] if len(result_data) > MAX_PREVIEW_ROWS else result_data
                        
                        import json as _json
                        result_json = _json.dumps(result_preview, indent=2, default=str)
                        if len(result_json) > MAX_JSON_CHARS:
                            result_json = result_json[:MAX_JSON_CHARS] + f'\n... (truncated)'
                        
                        prompt += f"""**Execution:** ✅ Successful
**Rows:** {row_count} rows returned{f' (showing first {MAX_PREVIEW_ROWS})' if row_count > MAX_PREVIEW_ROWS else ''}
**Columns:** {', '.join(columns[:10])}{'...' if len(columns) > 10 else ''}

**Result Preview:** 
{result_json}
{f'... and {row_count - MAX_PREVIEW_ROWS} more rows' if row_count > MAX_PREVIEW_ROWS else ''}
"""
                    else:
                        prompt += f"""**Execution:** ❌ Failed
**Error:** {result.get('error', 'Unknown error')}

"""
                else:
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
                    
                    for i, result in enumerate(execution_results, 1):
                        if result.get('success'):
                            row_count = result.get('row_count', 0)
                            result_data = result.get('result', [])
                            result_preview = result_data[:MAX_PREVIEW_ROWS] if len(result_data) > MAX_PREVIEW_ROWS else result_data
                            
                            import json as _json
                            result_json = _json.dumps(result_preview, indent=2, default=str)
                            if len(result_json) > MAX_JSON_CHARS:
                                result_json = result_json[:MAX_JSON_CHARS] + f'\n... (truncated)'
                            
                            prompt += f"""**Query {i} Result:**
- Rows: {row_count}
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
5. States the outcome (success with X rows, error, needs clarification, etc.)

Use markdown formatting for readability. Keep it clear and user-friendly. 
"""
        
        return prompt


def _get_cached_chart_generator():
    """Lazily create and cache a ChartGenerator instance."""
    if not hasattr(_get_cached_chart_generator, "_instance"):
        config = get_config()
        try:
            from databricks_langchain import ChatDatabricks
            from .chart_generator import ChartGenerator
            llm = ChatDatabricks(endpoint=config.llm.chart_endpoint, temperature=0, max_tokens=1000)
            _get_cached_chart_generator._instance = ChartGenerator(llm)
            print(f"✓ ChartGenerator cached (endpoint={config.llm.chart_endpoint})")
        except Exception as e:
            print(f"⚠ ChartGenerator init failed: {e}")
            _get_cached_chart_generator._instance = None
    return _get_cached_chart_generator._instance


@measure_node_time("summarize")
def summarize_node(state: AgentState) -> dict:
    """
    Orchestrates: LLM summary -> chart generation -> SQL download.
    This is the final node that all workflow paths go through.
    """
    import json as _json

    writer = get_stream_writer()

    print("\n" + "=" * 80)
    print("RESULT SUMMARIZE NODE")
    print("=" * 80)

    context = extract_summarize_context(state)
    writer({"type": "summary_start", "content": "Generating summary..."})

    summarize_agent = get_cached_summarize_agent()
    config = get_config()
    track_agent_model_usage("summarize", config.llm.summarize_endpoint)

    if "original_query" not in context:
        context["original_query"] = state.get("original_query", "N/A")

    # --- 1. LLM text summary (streams to user) ---
    summary = summarize_agent(context)

    # --- 2. Chart generation per result set ---
    execution_results = state.get("execution_results", [])
    exec_result = state.get("execution_result")
    if not execution_results and exec_result:
        execution_results = [exec_result]

    chart_gen = _get_cached_chart_generator()
    original_query = state.get("original_query", "")

    for idx, result_item in enumerate(execution_results):
        if not result_item or not result_item.get("success"):
            continue
        columns = result_item.get("columns", [])
        data = result_item.get("result", [])
        if not columns or not data:
            continue

        if chart_gen:
            try:
                payload = chart_gen.generate_chart(columns, data, original_query)
                if payload:
                    chart_json = _json.dumps(payload, default=str)
                    summary += f"\n\n```echarts-chart\n{chart_json}\n```\n"
                    print(f"✓ Chart block inserted for result {idx} ({len(chart_json)} bytes)")
            except Exception as e:
                print(f"⚠ Chart generation failed for result {idx}: {e}")

    # --- 3. SQL download (collapsible) ---
    sql_queries = state.get("sql_queries", [])
    if not sql_queries and state.get("sql_query"):
        sql_queries = [state["sql_query"]]
    labels = state.get("sql_query_labels", [])

    if sql_queries:
        from .summarize_agent import ResultSummarizeAgent
        summary += ResultSummarizeAgent.format_sql_download(sql_queries, labels)

    writer({"type": "summary_complete", "content": f"Summary generated ({len(summary)} chars)"})

    print(f"✓ final_summary: {len(summary)} chars")
    print("=" * 80)

    return {
        "final_summary": summary,
        "messages": [AIMessage(content=summary)],
    }
