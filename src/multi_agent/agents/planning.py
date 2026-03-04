"""
Planning agent node for the multi-agent system.

This module contains the planning_node function which is responsible for:
- Analyzing user queries
- Searching for relevant Genie spaces using vector search
- Creating execution plans with join strategies
- Determining the next agent in the workflow

The planning node uses turn-based context management and includes optimizations
for vector search result caching and token usage reduction.

Dependencies:
- PlanningAgent class: Must be available via get_cached_planning_agent()
- Configuration: VECTOR_SEARCH_INDEX and LLM_ENDPOINT_PLANNING must be set
  (either imported from config module or set directly)

Example usage:
    from src.multi_agent.agents.planning import planning_node
    from src.multi_agent.core.state import AgentState
    
    # Set configuration
    from src.multi_agent.agents.planning import VECTOR_SEARCH_INDEX, LLM_ENDPOINT_PLANNING
    VECTOR_SEARCH_INDEX = "catalog.schema.index_name"
    LLM_ENDPOINT_PLANNING = "databricks-claude-sonnet-4-5"
    
    # Use the planning node
    result = planning_node(state)
"""

import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
from functools import wraps

from langchain_core.messages import SystemMessage
from langgraph.config import get_stream_writer

from ..core.state import AgentState


# ==============================================================================
# Module-level caches and configuration
# ==============================================================================

# Vector search result cache (for refinement queries)
# Format: {thread_id: {"query": str, "results": List, "timestamp": datetime}}
_vector_search_cache: Dict[str, Dict[str, Any]] = {}
_VECTOR_SEARCH_CACHE_TTL = timedelta(minutes=10)  # Shorter TTL for conversation-specific cache

# Performance metrics tracking
_performance_metrics: Dict[str, Any] = {
    "cache_stats": {},
    "node_timings": {},
    "agent_model_usage": {}
}


# ==============================================================================
# Configuration constants (should be imported from config module)
# ==============================================================================

# These should be imported from a config module in production
# For now, they are placeholders that need to be set
VECTOR_SEARCH_INDEX: Optional[str] = None
LLM_ENDPOINT_PLANNING: Optional[str] = None


# ==============================================================================
# Helper Functions
# ==============================================================================

# Agent cache (module-level)
_agent_cache = {}

def get_cached_planning_agent():
    """
    Get or create cached PlanningAgent instance.
    Expected gain: -500ms to -1s per request
    """
    global LLM_ENDPOINT_PLANNING, VECTOR_SEARCH_INDEX
    
    # Lazy load config if not set
    if LLM_ENDPOINT_PLANNING is None or VECTOR_SEARCH_INDEX is None:
        try:
            from ..core.config import get_config
            config = get_config()
            if LLM_ENDPOINT_PLANNING is None:
                LLM_ENDPOINT_PLANNING = config.llm.planning_endpoint
            if VECTOR_SEARCH_INDEX is None:
                # Construct the full index name
                VECTOR_SEARCH_INDEX = f"{config.unity_catalog.catalog_name}.{config.unity_catalog.schema_name}.{'enriched_genie_docs_chunks_vs_index'}"
        except Exception as e:
            print(f"⚠️ Failed to load config: {e}")
            
    if "planning" not in _agent_cache:
        record_cache_miss("agent_cache")
        print("⚡ Creating PlanningAgent (first use)...")
        
        try:
            from databricks_langchain import ChatDatabricks
            from .planning_agent import PlanningAgent
            
            if LLM_ENDPOINT_PLANNING is None:
                raise ValueError("LLM_ENDPOINT_PLANNING must be configured")
            if VECTOR_SEARCH_INDEX is None:
                raise ValueError("VECTOR_SEARCH_INDEX must be configured")
                
            llm = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING, temperature=0.1)
            _agent_cache["planning"] = PlanningAgent(llm, VECTOR_SEARCH_INDEX)
            print("✓ PlanningAgent cached")
        except ImportError:
            raise ImportError(
                "Failed to import required dependencies (ChatDatabricks or PlanningAgent)."
            )
    else:
        record_cache_hit("agent_cache")
        print("✓ Using cached PlanningAgent")
        
    return _agent_cache["planning"]

def extract_planning_context(state: AgentState) -> dict:
    """Extract minimal context for planning."""
    return {
        "current_turn": state.get("current_turn"),
        "intent_metadata": state.get("intent_metadata"),
        "original_query": state.get("original_query")  # Backward compat
    }


def measure_node_time(node_name: str):
    """
    Decorator to measure node execution time.
    Expected use: Track per-node performance for optimization.
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import time
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


def track_agent_model_usage(agent_name: str, model_endpoint: str):
    """
    Track which LLM model is used by each agent for monitoring and cost analysis.
    
    Args:
        agent_name: Name of the agent (e.g., "clarification", "planning")
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


def record_cache_hit(cache_type: str):
    """Record a cache hit for monitoring."""
    key = f"{cache_type}_hits"
    if key not in _performance_metrics["cache_stats"]:
        _performance_metrics["cache_stats"][key] = 0
    _performance_metrics["cache_stats"][key] += 1


def record_cache_miss(cache_type: str):
    """Record a cache miss for monitoring."""
    key = f"{cache_type}_misses"
    if key not in _performance_metrics["cache_stats"]:
        _performance_metrics["cache_stats"][key] = 0
    _performance_metrics["cache_stats"][key] += 1


# ==============================================================================
# Planning Node Function
# ==============================================================================

@measure_node_time("planning")
def planning_node(state: AgentState) -> dict:
    """
    Planning node wrapping PlanningAgent class using turn-based context.
    
    IMPROVEMENTS:
    - Uses current_turn.context_summary (LLM-generated) instead of manual combined_query_context
    - Intent-aware planning (different strategies for refinements vs new questions)
    - Clean separation from clarification logic
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    
    Note: This function requires PlanningAgent class and get_cached_planning_agent function
    to be available. These should be imported from appropriate modules or defined elsewhere.
    """
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("📋 PLANNING AGENT (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for planning
    context = extract_planning_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    # Get current turn and intent from state
    current_turn = context.get("current_turn")
    if not current_turn:
        # Fallback for backward compatibility
        print("⚠ No current_turn found, falling back to legacy behavior")
        query = context.get("original_query", "")
        is_followup = False
        context_summary = None
    else:
        query = current_turn["query"]
        is_followup = len(state.get("turn_history", [])) > 0
        context_summary = current_turn.get("context_summary")

    # Use context_summary if available (LLM-generated from check_clarity)
    planning_query = context_summary or query

    # Emit agent start event
    writer({"type": "agent_start", "agent": "planning", "query": planning_query[:100]})

    print(f"Query: {query}")
    print(f"Is followup: {is_followup}")
    if context_summary:
        print(f"✓ Using context summary from clarification")
        print(f"  Summary: {context_summary[:200]}...")
    else:
        print(f"✓ Using query directly (no context needed)")
    
    # OPTIMIZATION: Use cached agent instance
    # NOTE: get_cached_planning_agent must be imported or defined elsewhere
    # This function should return a cached PlanningAgent instance
    try:
        planning_agent = get_cached_planning_agent()
    except Exception as e:
        # Fallback: PlanningAgent and get_cached_planning_agent need to be provided
        # This is a placeholder - in production, these should be properly imported
        raise ImportError(
            f"Failed to get planning agent: {e}"
        )
    track_agent_model_usage("planning", LLM_ENDPOINT_PLANNING)
    
    # PHASE 2 OPTIMIZATION: Vector search result caching for refinements
    thread_id = state.get("thread_id", "default")
    can_reuse_cache = is_followup
    
    relevant_spaces_full = None
    cache_hit = False
    
    if can_reuse_cache and thread_id in _vector_search_cache:
        cache_entry = _vector_search_cache[thread_id]
        cache_age = datetime.now() - cache_entry["timestamp"]
        
        if cache_age < _VECTOR_SEARCH_CACHE_TTL:
            record_cache_hit("vector_search")
            relevant_spaces_full = cache_entry["results"]
            cache_hit = True
            print(f"🚀 VECTOR SEARCH CACHE HIT (thread: {thread_id}, age: {cache_age.seconds}s)")
            print(f"   Reusing {len(relevant_spaces_full)} spaces for follow-up query")
            print(f"   Expected gain: -300 to -800ms")
            
            writer({
                "type": "vector_search_cache_hit",
                "thread_id": thread_id,
                "is_followup": is_followup,
                "space_count": len(relevant_spaces_full)
            })
        else:
            print(f"⚠️ Vector search cache expired (age: {cache_age.seconds}s > {_VECTOR_SEARCH_CACHE_TTL.seconds}s)")
    
    # If no cache hit, perform vector search
    if relevant_spaces_full is None:
        record_cache_miss("vector_search")
        # Emit vector search start event
        writer({"type": "vector_search_start", "index": VECTOR_SEARCH_INDEX})
        
        # Get relevant spaces with full metadata (for Genie agents)
        # Use planning_query which includes context_summary if available
        print(f"🔍 Performing vector search (cache miss or new question)...")
        relevant_spaces_full = planning_agent.search_relevant_spaces(planning_query)
        
        # Cache results for future refinements
        _vector_search_cache[thread_id] = {
            "query": planning_query,
            "results": relevant_spaces_full,
            "timestamp": datetime.now()
        }
        print(f"✓ Cached vector search results for thread: {thread_id}")
    
    # Emit vector search results
    writer({"type": "vector_search_results", "spaces": relevant_spaces_full, "count": len(relevant_spaces_full)})
    
    # Emit plan formulation start
    writer({"type": "agent_thinking", "agent": "planning", "content": "Creating execution plan..."})
    
    # Create execution plan
    # IMPORTANT: Use planning_query (with context_summary) not just query
    # Pass original_query so it can be shown in the prompt before context_summary
    plan = planning_agent.create_execution_plan(planning_query, relevant_spaces_full, original_query=query)
    
    # Extract plan components
    join_strategy = plan.get("join_strategy")
    
    # Emit plan formulation result
    writer({"type": "plan_formulation", "strategy": join_strategy, "requires_join": plan.get("requires_join", False)})
    
    # Determine next agent
    if join_strategy == "genie_route":
        print("✓ Plan complete - using GENIE ROUTE (Genie agents)")
        next_agent = "sql_synthesis_genie"
    else:
        print("✓ Plan complete - using TABLE ROUTE (direct SQL synthesis)")
        next_agent = "sql_synthesis_table"
    
    # Return only updates (no in-place modifications)
    return {
        "plan": plan,
        "sub_questions": plan.get("sub_questions", []),
        "requires_multiple_spaces": plan.get("requires_multiple_spaces", False),
        "relevant_space_ids": plan.get("relevant_space_ids", []),
        "requires_join": plan.get("requires_join", False),
        "join_strategy": join_strategy,
        "execution_plan": plan.get("execution_plan", ""),
        "genie_route_plan": plan.get("genie_route_plan"),
        "vector_search_relevant_spaces_info": plan.get("vector_search_relevant_spaces_info", []),
        "relevant_spaces": relevant_spaces_full,
        "next_agent": next_agent,
        "messages": [
            SystemMessage(content=f"Execution plan: {json.dumps(plan, indent=2)}")
        ]
    }


# ==============================================================================
# Cache Management Functions
# ==============================================================================

def clear_vector_search_cache(thread_id: str = None):
    """Clear vector search cache for a specific thread or all threads."""
    global _vector_search_cache
    if thread_id:
        if thread_id in _vector_search_cache:
            del _vector_search_cache[thread_id]
            print(f"✓ Vector search cache cleared for thread: {thread_id}")
    else:
        _vector_search_cache = {}
        print("✓ All vector search caches cleared")


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics for monitoring."""
    return {
        "vector_search_cache_size": len(_vector_search_cache),
        "vector_search_threads": list(_vector_search_cache.keys()),
        "performance_metrics": _performance_metrics
    }
