"""
SQL Synthesis Agent Nodes

This module provides two SQL synthesis strategies for the multi-agent system:

1. Table Route (sql_synthesis_table_node):
   - Fast SQL synthesis using Unity Catalog (UC) function tools
   - Queries metadata directly from UC functions (get_space_summary, get_table_overview, etc.)
   - Optimized for single-space or simple multi-space queries
   - Uses cached SQLSynthesisTableAgent instance for performance

2. Genie Route (sql_synthesis_genie_node):
   - Slower but more powerful SQL synthesis using Genie agents as tools
   - Orchestrates multiple Genie agents in parallel to gather SQL fragments
   - Best for complex queries requiring coordination across multiple spaces
   - Uses dedicated SQL_SYNTHESIS_GENIE endpoint for stronger reasoning

Both functions:
- Use minimal state extraction to reduce token usage
- Emit streaming events for observability
- Return state updates as dictionaries for clean MLflow traces
- Handle errors gracefully with appropriate error messages
"""

import json
import time
from functools import wraps
from typing import Dict, List, Optional, Any, Callable

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

# Type imports
from ..core.state import AgentState

# Agent class imports
from .sql_synthesis_agents import (
    SQLSynthesisTableAgent,
    SQLSynthesisGenieAgent
)

# SQL extraction utilities for multi-query support
from ..utils.sql_extraction import extract_sql_queries_from_agent_result

# LLM and utility imports
try:
    from databricks_langchain import ChatDatabricks
except ImportError:
    ChatDatabricks = None  # type: ignore

# Configuration constants - these should be imported from config or passed as parameters
# For standalone usage, these should be provided via function parameters or environment
# They can be initialized using initialize_config() function below
LLM_ENDPOINT_SQL_SYNTHESIS_TABLE = None  # Should be set from config
LLM_ENDPOINT_SQL_SYNTHESIS_GENIE = None  # Should be set from config
CATALOG = None  # Should be set from config
SCHEMA = None  # Should be set from config


def initialize_config(
    llm_endpoint_sql_synthesis_table: str,
    llm_endpoint_sql_synthesis_genie: str,
    catalog: str,
    schema: str
):
    """
    Initialize module-level configuration constants.
    
    This function should be called before using the SQL synthesis nodes,
    or the constants can be set directly.
    
    Args:
        llm_endpoint_sql_synthesis_table: LLM endpoint for table route SQL synthesis
        llm_endpoint_sql_synthesis_genie: LLM endpoint for genie route SQL synthesis
        catalog: Unity Catalog catalog name
        schema: Unity Catalog schema name
    """
    global LLM_ENDPOINT_SQL_SYNTHESIS_TABLE
    global LLM_ENDPOINT_SQL_SYNTHESIS_GENIE
    global CATALOG
    global SCHEMA
    
    LLM_ENDPOINT_SQL_SYNTHESIS_TABLE = llm_endpoint_sql_synthesis_table
    LLM_ENDPOINT_SQL_SYNTHESIS_GENIE = llm_endpoint_sql_synthesis_genie
    CATALOG = catalog
    SCHEMA = schema


def initialize_config_from_module():
    """
    Initialize configuration from the config module.
    
    Attempts to load configuration from config.get_config().
    This is useful when running in environments where config.py is available.
    """
    try:
        from config import get_config
        config = get_config()
        
        initialize_config(
            llm_endpoint_sql_synthesis_table=config.llm.sql_synthesis_table_endpoint,
            llm_endpoint_sql_synthesis_genie=config.llm.sql_synthesis_genie_endpoint,
            catalog=config.unity_catalog.catalog_name,
            schema=config.unity_catalog.schema_name
        )
        print("✓ Configuration initialized from config module")
    except ImportError:
        print("⚠️ config module not available. Set configuration manually using initialize_config()")
    except Exception as e:
        print(f"⚠️ Failed to initialize config: {e}. Set configuration manually using initialize_config()")

# Performance metrics storage (module-level)
_performance_metrics = {
    "node_timings": {},
    "agent_model_usage": {},
    "cache_stats": {}
}

# Agent cache (module-level)
_agent_cache = {}

# LLM connection pool (module-level)
_llm_connection_pool = {}


# ==============================================================================
# Helper Functions
# ==============================================================================

def extract_synthesis_table_context(state: AgentState) -> dict:
    """Extract minimal context for table-based SQL synthesis."""
    return {
        "plan": state.get("plan", {}),
        "relevant_space_ids": state.get("relevant_space_ids", [])
    }


def extract_synthesis_genie_context(state: AgentState) -> dict:
    """Extract minimal context for genie-based SQL synthesis."""
    return {
        "plan": state.get("plan", {}),
        "relevant_spaces": state.get("relevant_spaces", []),
        "genie_route_plan": state.get("genie_route_plan")
    }


def record_cache_hit(cache_type: str):
    """Record a cache hit for monitoring."""
    key = f"{cache_type}_hits"
    if key in _performance_metrics["cache_stats"]:
        _performance_metrics["cache_stats"][key] += 1
    else:
        _performance_metrics["cache_stats"][key] = 1


def record_cache_miss(cache_type: str):
    """Record a cache miss for monitoring."""
    key = f"{cache_type}_misses"
    if key in _performance_metrics["cache_stats"]:
        _performance_metrics["cache_stats"][key] += 1
    else:
        _performance_metrics["cache_stats"][key] = 1


def get_pooled_llm(endpoint_name: str, temperature: float = 0.1, max_tokens: int = None):
    """
    Get or create a pooled LLM connection.
    Reuses connections across requests to avoid connection overhead.
    
    Args:
        endpoint_name: Name of the LLM endpoint
        temperature: Temperature for generation (default 0.1)
        max_tokens: Maximum tokens to generate (default None)
    
    Returns:
        ChatDatabricks instance from pool
    """
    if ChatDatabricks is None:
        raise ImportError("ChatDatabricks is not available. Install databricks-langchain.")
    
    # Create a cache key that includes temperature and max_tokens
    cache_key = f"{endpoint_name}_{temperature}_{max_tokens}"
    
    if cache_key not in _llm_connection_pool:
        record_cache_miss("llm_pool")
        print(f"⚡ Creating pooled LLM connection: {endpoint_name} (temperature={temperature})")
        kwargs = {"endpoint": endpoint_name, "temperature": temperature}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        _llm_connection_pool[cache_key] = ChatDatabricks(**kwargs)
        print(f"✓ LLM connection pooled: {cache_key}")
    else:
        record_cache_hit("llm_pool")
        print(f"♻️ Reusing pooled LLM connection: {cache_key} (-50ms to -200ms)")
    
    return _llm_connection_pool[cache_key]


def track_agent_model_usage(agent_name: str, model_endpoint: str):
    """
    Track which LLM model is used by each agent for monitoring and cost analysis.
    
    Args:
        agent_name: Name of the agent (e.g., "sql_synthesis_table", "sql_synthesis_genie")
        model_endpoint: LLM endpoint being used (e.g., "databricks-claude-haiku-4-5")
    """
    if "agent_model_usage" not in _performance_metrics:
        _performance_metrics["agent_model_usage"] = {}
    
    if agent_name not in _performance_metrics["agent_model_usage"]:
        _performance_metrics["agent_model_usage"][agent_name] = {
            "model": model_endpoint,
            "invocations": 0
        }
    
    _performance_metrics["agent_model_usage"][agent_name]["invocations"] += 1
    print(f"📊 Agent '{agent_name}' using model: {model_endpoint}")


def measure_node_time(node_name: str):
    """
    Decorator to measure node execution time.
    Expected use: Track per-node performance for optimization.
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                
                # Record timing
                if node_name not in _performance_metrics["node_timings"]:
                    _performance_metrics["node_timings"][node_name] = []
                _performance_metrics["node_timings"][node_name].append(elapsed)
                
                # Print timing
                print(f"⏱️  {node_name}: {elapsed:.3f}s")
                
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                print(f"⏱️  {node_name}: {elapsed:.3f}s (FAILED)")
                raise
        return wrapper
    return decorator


def get_cached_sql_table_agent():
    """
    Get or create cached SQLSynthesisTableAgent instance.
    Expected gain: -500ms to -1s per request
    """
    if SQLSynthesisTableAgent is None:
        raise ImportError("SQLSynthesisTableAgent is not available. Import it from the appropriate module.")
    
    if LLM_ENDPOINT_SQL_SYNTHESIS_TABLE is None:
        raise ValueError("LLM_ENDPOINT_SQL_SYNTHESIS_TABLE must be configured")
    
    if CATALOG is None or SCHEMA is None:
        raise ValueError("CATALOG and SCHEMA must be configured")
    
    if "sql_table" not in _agent_cache:
        record_cache_miss("agent_cache")
        print("⚡ Creating SQLSynthesisTableAgent (first use)...")
        llm = get_pooled_llm(LLM_ENDPOINT_SQL_SYNTHESIS_TABLE)
        _agent_cache["sql_table"] = SQLSynthesisTableAgent(llm, CATALOG, SCHEMA)
        print("✓ SQLSynthesisTableAgent cached")
    else:
        record_cache_hit("agent_cache")
        print("✓ Using cached SQLSynthesisTableAgent")
    return _agent_cache["sql_table"]


# ==============================================================================
# SQL Synthesis Node Functions
# ==============================================================================

@measure_node_time("sql_synthesis_table")
def sql_synthesis_table_node(state: AgentState) -> dict:
    """
    Fast SQL synthesis node wrapping SQLSynthesisTableAgent class.
    Combines OOP modularity with explicit state management.
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("⚡ SQL SYNTHESIS AGENT - TABLE ROUTE (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for table-based synthesis
    context = extract_synthesis_table_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    plan = context.get("plan", {})
    relevant_space_ids = context.get("relevant_space_ids", [])
    
    # Emit synthesis start event
    writer({"type": "sql_synthesis_start", "route": "table", "spaces": relevant_space_ids})
    
    # OPTIMIZATION: Use cached agent instance
    sql_agent = get_cached_sql_table_agent()
    track_agent_model_usage("sql_synthesis_table", LLM_ENDPOINT_SQL_SYNTHESIS_TABLE)
    
    print("plan loaded from state is:", plan)
    print(json.dumps(plan, indent=2))
    
    try:
        print("🤖 Invoking SQL synthesis agent...")
        
        # Emit detailed start event
        writer({"type": "agent_thinking", "agent": "sql_synthesis_table", "content": "🧠 Starting SQL synthesis using UC function tools..."})
        writer({"type": "agent_step", "agent": "sql_synthesis_table", "step": "analyzing_plan", "content": f"📋 Analyzing execution plan for {len(relevant_space_ids)} relevant spaces"})
        
        # Emit tool preparation event
        uc_functions = ["get_space_summary", "get_table_overview", "get_column_detail", "get_space_details"]
        writer({"type": "tools_available", "agent": "sql_synthesis_table", "tools": uc_functions, "content": f"🔧 Available UC functions: {', '.join(uc_functions)}"})
        
        # Emit query strategy
        writer({"type": "agent_thinking", "agent": "sql_synthesis_table", "content": f"🎯 Strategy: Query metadata for spaces {relevant_space_ids} then synthesize SQL"})
        
        # Call the agent
        result = sql_agent(plan)
        
        # Emit tool completion event
        writer({"type": "agent_step", "agent": "sql_synthesis_table", "step": "metadata_gathered", "content": "✅ Metadata collection complete, synthesizing SQL query..."})
        
        # Extract SQL and explanation
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        # Extract all SQL queries using helper function
        sql_queries, query_labels = extract_sql_queries_from_agent_result(result, "sql_synthesis_table")
        
        if sql_queries:
            # Multi-query support
            print(f"✓ Extracted {len(sql_queries)} SQL quer{'y' if len(sql_queries) == 1 else 'ies'}")
            for i, query in enumerate(sql_queries, 1):
                label_info = f" [{query_labels[i-1]}]" if i <= len(query_labels) and query_labels[i-1] else ""
                print(f"  Query {i}{label_info} preview: {query[:100]}...")
            
            if explanation:
                print(f"Agent Explanation: {explanation[:200]}...")
            
            # Emit detailed success events
            writer({"type": "sql_generated", "agent": "sql_synthesis_table", "query_preview": sql_queries[0][:200], "content": f"💻 {len(sql_queries)} SQL Quer{'y' if len(sql_queries) == 1 else 'ies'} Generated"})
            writer({"type": "agent_result", "agent": "sql_synthesis_table", "result": "success", "content": f"✅ SQL synthesis complete: {explanation[:150]}..."})
            
            # Return updates for successful synthesis
            return {
                "sql_queries": sql_queries,
                "sql_query_labels": query_labels,
                "sql_query": sql_queries[0],  # For backward compatibility
                "has_sql": True,
                "sql_synthesis_explanation": explanation,
                "next_agent": "sql_execution",
                "messages": [
                    AIMessage(content=f"SQL Synthesis (Table Route):\n{explanation}")
                ]
            }
        else:
            print("⚠ No SQL generated - agent explanation:")
            print(f"  {explanation}")
            
            # Emit failure event
            writer({"type": "agent_result", "agent": "sql_synthesis_table", "result": "no_sql", "content": f"⚠️ Could not generate SQL: {explanation[:150]}..."})
            
            # Return updates for failed synthesis
            return {
                "synthesis_error": "Cannot generate SQL query",
                "sql_synthesis_explanation": explanation,
                "next_agent": "summarize",
                "messages": [
                    AIMessage(content=f"SQL Synthesis Failed (Table Route):\n{explanation}")
                ]
            }
        
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        error_msg = str(e)
        # Return updates for exception
        return {
            "synthesis_error": error_msg,
            "sql_synthesis_explanation": error_msg,
            "messages": [
                AIMessage(content=f"SQL Synthesis Failed (Table Route):\n{error_msg}")
            ]
        }


@measure_node_time("sql_synthesis_genie")
def sql_synthesis_genie_node(state: AgentState) -> dict:
    """
    Slow SQL synthesis node wrapping SQLSynthesisGenieAgent class.
    Combines OOP modularity with explicit state management.
    
    Uses relevant_spaces from PlanningAgent (no need to re-query all spaces).
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("🐢 SQL SYNTHESIS AGENT - GENIE ROUTE (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for genie-based synthesis
    context = extract_synthesis_genie_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    # Get relevant spaces from state (already discovered by PlanningAgent)
    relevant_spaces = context.get("relevant_spaces", [])
    relevant_space_ids = [s.get("space_id") for s in relevant_spaces if s.get("space_id")]
    
    # Emit synthesis start event
    writer({"type": "sql_synthesis_start", "route": "genie", "spaces": relevant_space_ids})
    
    # Use dedicated SQL_SYNTHESIS_GENIE endpoint for orchestrating multiple Genie agents
    # This agent requires stronger reasoning for complex coordination
    if LLM_ENDPOINT_SQL_SYNTHESIS_GENIE is None:
        raise ValueError("LLM_ENDPOINT_SQL_SYNTHESIS_GENIE must be configured")
    
    llm = get_pooled_llm(LLM_ENDPOINT_SQL_SYNTHESIS_GENIE, temperature=0.1)
    
    if not relevant_spaces:
        print("❌ No relevant_spaces found in state")
        # Return error update
        return {
            "synthesis_error": "No relevant spaces available for genie route"
        }
    
    # Use OOP agent - only creates Genie agents for relevant spaces
    if SQLSynthesisGenieAgent is None:
        raise ImportError("SQLSynthesisGenieAgent is not available. Import it from the appropriate module.")
    
    sql_agent = SQLSynthesisGenieAgent(llm, relevant_spaces)
    track_agent_model_usage("sql_synthesis_genie", LLM_ENDPOINT_SQL_SYNTHESIS_GENIE)
    
    # Use minimal context (already extracted)
    plan = context.get("plan", {})
    genie_route_plan = context.get("genie_route_plan") or plan.get("genie_route_plan", {})
    
    if not genie_route_plan:
        print("❌ No genie_route_plan found in plan")
        # Return error update
        return {
            "synthesis_error": "No routing plan available for genie route"
        }
    
    try:
        print(f"🤖 Querying {len(genie_route_plan)} Genie agents...")
        
        # Emit detailed start event
        writer({"type": "agent_thinking", "agent": "sql_synthesis_genie", "content": f"🧠 Starting SQL synthesis using {len(genie_route_plan)} Genie agents..."})
        writer({"type": "agent_step", "agent": "sql_synthesis_genie", "step": "preparing_genie_calls", "content": f"📋 Preparing to query {len(genie_route_plan)} Genie spaces"})
        
        # Emit detailed events for each Genie agent call with full context
        for idx, (space_id, query) in enumerate(genie_route_plan.items(), 1):
            space_title = next((s.get("space_title", space_id) for s in relevant_spaces if s.get("space_id") == space_id), space_id)
            writer({
                "type": "genie_agent_call", 
                "agent": "sql_synthesis_genie",
                "space_id": space_id, 
                "space_title": space_title,
                "query": query,
                "content": f"🤖 [{idx}/{len(genie_route_plan)}] Calling Genie agent '{space_title}' with query: {query[:100]}{'...' if len(query) > 100 else ''}"
            })
        
        # Emit execution strategy
        writer({"type": "agent_thinking", "agent": "sql_synthesis_genie", "content": "⚡ Executing Genie agents in parallel for optimal performance..."})
        
        # Call the agent
        result = sql_agent(plan)
        
        # Emit completion event
        writer({"type": "agent_step", "agent": "sql_synthesis_genie", "step": "combining_results", "content": "🔄 All Genie agents responded, combining SQL fragments..."})
        
        # Extract SQL and explanation
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        # Extract all SQL queries using helper function
        sql_queries, query_labels = extract_sql_queries_from_agent_result(result, "sql_synthesis_genie")
        
        if sql_queries:
            # Multi-query support
            print(f"✓ Extracted {len(sql_queries)} SQL quer{'y' if len(sql_queries) == 1 else 'ies'}")
            for i, query in enumerate(sql_queries, 1):
                label_info = f" [{query_labels[i-1]}]" if i <= len(query_labels) and query_labels[i-1] else ""
                print(f"  Query {i}{label_info} preview: {query[:100]}...")
            
            if explanation:
                print(f"Agent Explanation: {explanation[:200]}...")
            
            # Emit detailed success events
            writer({"type": "sql_generated", "agent": "sql_synthesis_genie", "query_preview": sql_queries[0][:200], "content": f"💻 {len(sql_queries)} SQL Quer{'y' if len(sql_queries) == 1 else 'ies'} Generated"})
            writer({"type": "agent_result", "agent": "sql_synthesis_genie", "result": "success", "content": f"✅ SQL synthesis complete: {explanation[:150]}..."})
            writer({"type": "agent_thinking", "agent": "sql_synthesis_genie", "content": f"🎯 Successfully extracted {len(sql_queries)} SQL queries from {len(genie_route_plan)} Genie agents"})
            
            # Return updates for successful synthesis
            return {
                "sql_queries": sql_queries,
                "sql_query_labels": query_labels,
                "sql_query": sql_queries[0],  # For backward compatibility
                "has_sql": True,
                "sql_synthesis_explanation": explanation,
                "next_agent": "sql_execution",
                "messages": [
                    AIMessage(content=f"SQL Synthesis (Genie Route):\n{explanation}")
                ]
            }
        else:
            print("⚠ No SQL generated - agent explanation:")
            print(f"  {explanation}")
            
            # Emit detailed failure event
            writer({"type": "agent_result", "agent": "sql_synthesis_genie", "result": "no_sql", "content": f"⚠️ Could not generate SQL from Genie agents: {explanation[:150]}..."})
            
            # Return updates for failed synthesis
            return {
                "synthesis_error": "Cannot generate SQL query from Genie agent fragments",
                "sql_synthesis_explanation": explanation,
                "next_agent": "summarize",
                "messages": [
                    AIMessage(content=f"SQL Synthesis Failed (Genie Route):\n{explanation}")
                ]
            }
        
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        error_msg = str(e)
        # Return updates for exception
        return {
            "synthesis_error": error_msg,
            "sql_synthesis_explanation": error_msg,
            "messages": [
                AIMessage(content=f"SQL Synthesis Failed (Genie Route):\n{error_msg}")
            ]
        }


# Export both functions and configuration helpers
__all__ = [
    "sql_synthesis_table_node",
    "sql_synthesis_genie_node",
    "extract_synthesis_table_context",
    "extract_synthesis_genie_context",
    "get_cached_sql_table_agent",
    "get_pooled_llm",
    "track_agent_model_usage",
    "measure_node_time",
    "initialize_config",
    "initialize_config_from_module",
]
