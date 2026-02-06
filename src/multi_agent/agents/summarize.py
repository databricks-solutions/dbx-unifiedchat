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
        "execution_result": state.get("execution_result"),
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
            llm = ChatDatabricks(endpoint=llm_endpoint, temperature=0.1, max_tokens=5000)
        except ImportError:
            raise ImportError(
                "databricks_langchain is required. Install with: pip install databricks-langchain"
            )
        
        # Create ResultSummarizeAgent
        try:
            from ..agents.summarize_agent import ResultSummarizeAgent
            get_cached_summarize_agent._cached_agent = ResultSummarizeAgent(llm)
        except ImportError:
            # Fallback: Create a simple wrapper
            get_cached_summarize_agent._cached_agent = _SimpleSummarizeAgent(llm)
        
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
            
            if exec_result.get('success'):
                row_count = exec_result.get('row_count', 0)
                columns = exec_result.get('columns', [])
                result = exec_result.get('result', [])
                
                # Sample results for prompt
                MAX_PREVIEW_ROWS = 20
                result_preview = result[:MAX_PREVIEW_ROWS] if len(result) > MAX_PREVIEW_ROWS else result
                
                import json
                result_json = json.dumps(result_preview, indent=2, default=str)
                if len(result_json) > 2000:
                    result_json = result_json[:2000] + f'\n... (truncated)'
                
                prompt += f"""**Execution:** ✅ Successful
**Rows:** {row_count} rows returned{f' (showing first {MAX_PREVIEW_ROWS})' if row_count > MAX_PREVIEW_ROWS else ''}
**Columns:** {', '.join(columns[:10])}{'...' if len(columns) > 10 else ''}

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
4. Print out SQL synthesis explanation if any SQL was generated
5. Print out SQL if any SQL was generated; make it the code block. If multiple SQL queries were generated, print out them in separate code blocks.
6. Print out the result itself (markdown formatted as a table). If multiple result sets were generated, print out them in separate tables.
7. Summarize the insights from the result itself (markdown formatted as a list of bullets).
8. If multiple result sets were generated, summarize the insights from each result set in a separate list of bullets.

Keep it concise and user-friendly. 
"""
        
        return prompt


@measure_node_time("summarize")
def summarize_node(state: AgentState) -> dict:
    """
    Result summarize node wrapping ResultSummarizeAgent class.
    
    This is the final node that all workflow paths go through.
    Generates a natural language summary AND preserves all workflow data.
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("📝 RESULT SUMMARIZE AGENT (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for summarization
    context = extract_summarize_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    # Emit summary start event
    writer({"type": "summary_start", "content": "Generating comprehensive summary..."})
    
    # OPTIMIZATION: Use cached agent instance
    summarize_agent = get_cached_summarize_agent()
    config = get_config()
    track_agent_model_usage("summarize", config.llm.summarize_endpoint)
    
    # Add original_query to context if not present (needed for summary)
    if 'original_query' not in context:
        context['original_query'] = state.get('original_query', 'N/A')
    
    summary = summarize_agent(context)
    
    # Display what's being returned
    print(f"\n📦 State Fields Being Returned:")
    print(f"  ✓ final_summary: {len(summary)} chars")
    if context.get("sql_query"):
        print(f"  ✓ sql_query: {len(context['sql_query'])} chars")
    if context.get("execution_result"):
        exec_result = context["execution_result"]
        if exec_result.get("success"):
            print(f"  ✓ execution_result: {exec_result.get('row_count', 0)} rows")
        else:
            print(f"  ✓ execution_result: Failed - {exec_result.get('error', 'Unknown')[:50]}...")
    if context.get("sql_synthesis_explanation"):
        print(f"  ✓ sql_synthesis_explanation: {len(context['sql_synthesis_explanation'])} chars")
    if state.get("execution_plan"):
        print(f"  ✓ execution_plan: {state['execution_plan'][:80]}...")
    if state.get("synthesis_error"):
        print(f"  ⚠ synthesis_error: {state['synthesis_error'][:50]}...")
    if state.get("execution_error"):
        print(f"  ⚠ execution_error: {state['execution_error'][:50]}...")
    
    print("="*80)
    
    # Emit summary completion event
    writer({"type": "summary_complete", "content": f"✅ Summary generated ({len(summary)} chars)"})
    
    # Build a concise final message for AIMessage (avoid duplication with final_summary)
    # Only include execution results and errors (summary goes to final_summary field)
    final_message_parts = []
    
    # 1. Execution Results (if available)
    exec_result = state.get("execution_result")
    if exec_result and exec_result.get("success"):
        results = exec_result.get("result", [])
        if results:
            try:
                import pandas as pd
                df = pd.DataFrame(results)
                
                # Display DataFrame
                print("\n" + "="*80)
                print("📊 QUERY RESULTS (Pandas DataFrame)")
                print("="*80)
                try:
                    # Try to use display() if available (Databricks notebook)
                    display(df)
                except NameError:
                    # Fallback to string representation
                    print(df.to_string())
                print("="*80 + "\n")
                
                # Add compact results info to message
                final_message_parts.append(f"\n📊 **Query Results:** {df.shape[0]} rows × {df.shape[1]} columns")
                
                # Show top 100 rows in markdown table format
                display_rows = min(100, df.shape[0])
                df_preview = df.head(display_rows)
                
                # Convert to markdown table
                markdown_table = df_preview.to_markdown(index=False)
                
                final_message_parts.append(f"\n### Results Table (Top {display_rows} rows)\n\n{markdown_table}")
                
                # Add note if more rows exist
                if df.shape[0] > display_rows:
                    final_message_parts.append(f"\n*Showing {display_rows} of {df.shape[0]} total rows*")
                
            except Exception as e:
                final_message_parts.append(f"\n⚠️ Could not format results: {e}")
                final_message_parts.append(f"Raw results (first 3): {results[:3]}")
    
    # 2. Error messages (if any)
    if state.get("synthesis_error"):
        final_message_parts.append(f"\n❌ **SQL Synthesis Error:** {state['synthesis_error']}")
    if state.get("execution_error"):
        final_message_parts.append(f"\n❌ **Execution Error:** {state['execution_error']}")
    
    # Combine into final message (results/errors only - summary in final_summary field)
    # If no results or errors, use a simple completion message
    final_message = "\n".join(final_message_parts) if final_message_parts else "✅ Execution complete"
    
    print(f"\n✅ AIMessage created with results/errors ({len(final_message)} chars)")
    print(f"✅ Summary stored in final_summary field ({len(summary)} chars)")
    
    # Route to END via fixed edge (summarize → END)
    # Return: final_summary (displayed once) + AIMessage (results/errors only)
    return {
        "final_summary": summary,
        "messages": [
            AIMessage(content=final_message)
        ]
    }
