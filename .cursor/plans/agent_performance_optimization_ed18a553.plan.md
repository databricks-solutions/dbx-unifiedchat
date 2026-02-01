---
name: Agent Performance Optimization
overview: Comprehensive performance optimization plan to improve time to first token (TTFT) and total response time by addressing caching, parallelization, lazy loading, and architectural inefficiencies in the Super Agent.
todos:
  - id: p0-space-context-cache
    content: Implement space context caching with 30-min TTL to avoid repeated Spark queries (-1 to -2s)
    status: pending
  - id: p0-agent-instance-cache
    content: Add module-level agent instance caching (ClarificationAgent, PlanningAgent, etc.) (-500ms to -1s)
    status: pending
  - id: p0-genie-agent-pool
    content: Create Genie agent pool with lazy initialization to avoid recreating agents (-1 to -3s)
    status: pending
  - id: p1-streaming-llm-calls
    content: "Replace .invoke() with .stream() for immediate first token emission (TTFT: -2 to -5s)"
    status: pending
  - id: p1-fast-path-routing
    content: Add conditional intent detection skip for clear queries (-1 to -2s)
    status: pending
  - id: p1-vector-search-reuse
    content: Cache and reuse vector search results for refinement queries (-300 to -800ms)
    status: pending
  - id: p1-parallel-uc-calls
    content: Implement parallel UC function calls in SQL synthesis using ThreadPoolExecutor (-1 to -2s)
    status: pending
  - id: p2-optimize-spark-ops
    content: Use df.count() instead of len(collect()) and implement result streaming (-200 to -500ms)
    status: pending
  - id: p2-connection-pooling
    content: Implement LLM connection pooling to avoid repeated connection overhead (-500ms cumulative)
    status: pending
  - id: p3-clarification-fastpath
    content: Add fast-path for adaptive clarification strategy (-100 to -200ms)
    status: pending
  - id: p3-performance-monitoring
    content: Add instrumentation to measure TTFT, TTCL, cache hit rates, and per-node timing
    status: pending
isProject: false
---

# Agent Performance Optimization Plan

## Overview

Analysis of `[Notebooks/Super_Agent_hybrid.py](Notebooks/Super_Agent_hybrid.py)` reveals multiple performance bottlenecks affecting time to first token (TTFT) and total response time. This plan addresses 10 major optimization areas with specific, actionable improvements.

---

## Critical Performance Issues Identified

### 🔴 **ISSUE #1: Space Context Loading on Every Request**

**Location**: Lines 534-558, 676, 1906

**Problem**:

```python
def load_space_context(table_name: str) -> Dict[str, str]:
    df = spark.sql(f"SELECT space_id, searchable_content FROM {table_name} WHERE chunk_type = 'space_summary'")
    context = {row["space_id"]: row["searchable_content"] for row in df.collect()}  # ⚠️ Expensive!
    return context

# Called on EVERY clarification check
clarification_agent = ClarificationAgent.from_table(llm, TABLE_NAME)
```

**Impact**: 

- Spark query + `.collect()` executed for every clarification request
- Adds 500ms-2s latency per request
- Blocks first token generation

**Solution**: Implement caching with TTL

```python
from functools import lru_cache
from datetime import datetime, timedelta

_space_context_cache = {"data": None, "timestamp": None}
_CACHE_TTL = timedelta(minutes=30)

def load_space_context_cached(table_name: str) -> Dict[str, str]:
    now = datetime.now()
    if (_space_context_cache["data"] is None or 
        _space_context_cache["timestamp"] is None or
        now - _space_context_cache["timestamp"] > _CACHE_TTL):
        _space_context_cache["data"] = load_space_context(table_name)
        _space_context_cache["timestamp"] = now
    return _space_context_cache["data"]
```

---

### 🔴 **ISSUE #2: Agent Recreation on Every Request**

**Location**: Lines 637-759 (ClarificationAgent), 760-912 (PlanningAgent), etc.

**Problem**:

```python
def clarification_node(state):
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)  # ⚠️ New LLM every time
    clarification_agent = ClarificationAgent.from_table(llm, TABLE_NAME)  # ⚠️ New agent every time
```

**Impact**:

- Agent initialization overhead: 100-300ms per agent
- LLM client initialization overhead
- Repeated setup for unchanged components

**Solution**: Agent instance caching

```python
_agent_cache = {}

def get_cached_clarification_agent():
    if "clarification" not in _agent_cache:
        llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
        _agent_cache["clarification"] = ClarificationAgent.from_table(llm, TABLE_NAME)
    return _agent_cache["clarification"]
```

---

### 🔴 **ISSUE #3: Genie Agent Creation on Every Genie Route**

**Location**: Lines 1101-1156 in `_create_genie_agent_tools()`

**Problem**:

```python
def _create_genie_agent_tools(self):
    for space in self.relevant_spaces:
        genie_agent = GenieAgent(  # ⚠️ Created fresh every time
            genie_space_id=space_id,
            genie_agent_name=genie_agent_name,
            description=description,
            include_context=True,
            message_processor=lambda msgs: enforce_limit(msgs, n=5)
        )
```

**Impact**:

- Creating 3-5 Genie agents: 1-3 seconds
- Each agent initialization includes API calls and setup
- Blocks SQL synthesis completely

**Solution**: Lazy initialization with caching

```python
_genie_agent_pool = {}

def get_or_create_genie_agent(space_id: str, space_title: str, description: str):
    if space_id not in _genie_agent_pool:
        _genie_agent_pool[space_id] = GenieAgent(
            genie_space_id=space_id,
            genie_agent_name=f"Genie_{space_title}",
            description=description,
            include_context=True
        )
    return _genie_agent_pool[space_id]
```

---

### 🟡 **ISSUE #4: Sequential LLM Calls (No Parallelization)**

**Location**: Throughout workflow nodes

**Problem**:

```python
# Entirely sequential - no parallelization
clarity_result = clarification_agent.check_clarity(query)  # ~1-2s
plan = planning_agent.create_execution_plan(query, spaces)  # ~2-3s  
result = sql_agent.synthesize_sql(plan)  # ~3-5s
summary = summarize_agent.generate_summary(state)  # ~2-3s
```

**Impact**:

- Total time = sum of all operations (8-13 seconds)
- Could overlap independent operations
- No async/await usage at all

**Solution**: Strategic parallelization where safe

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Example: Parallel UC function calls in SQL synthesis
async def synthesize_sql_parallel(plan):
    with ThreadPoolExecutor() as executor:
        tasks = [
            executor.submit(uc_toolkit.get_table_overview, space_id)
            for space_id in plan["relevant_space_ids"]
        ]
        results = [task.result() for task in tasks]
```

**Note**: Be careful with LangGraph state management - only parallelize read-only operations

---

### 🟡 **ISSUE #5: Intent Detection Always Runs First**

**Location**: Lines 1643-1720, workflow entry point line 2608

**Problem**:

```python
workflow.set_entry_point("intent_detection")  # ⚠️ Always runs first
workflow.add_edge("intent_detection", "clarification")

def intent_detection_node(state):
    # LLM call for every request (1-2 seconds)
    intent_result = intent_agent.detect_intent(current_query, turn_history, messages)
```

**Impact**:

- Adds 1-2 seconds before first token
- Runs even for simple, clear queries
- No early exit for obvious cases

**Solution**: Fast-path routing with heuristics

```python
def should_skip_intent_detection(query: str, turn_history: List) -> bool:
    # Simple heuristics for obvious cases
    if len(turn_history) == 0 and len(query.split()) > 10:
        return True  # First query, reasonably detailed
    if len(turn_history) > 0 and query.lower().startswith(("show", "get", "list")):
        return True  # Follow-up with clear intent
    return False

# In workflow - conditional entry
if should_skip_intent_detection(query, history):
    workflow.set_entry_point("clarification")
else:
    workflow.set_entry_point("intent_detection")
```

---

### 🟡 **ISSUE #6: Vector Search on Every Planning Request**

**Location**: Line 2062 in `planning_node()`

**Problem**:

```python
def planning_node(state):
    planning_agent = PlanningAgent(llm, VECTOR_SEARCH_INDEX)
    relevant_spaces_full = planning_agent.search_relevant_spaces(planning_query)  # ⚠️ Always searches
```

**Impact**:

- Vector search: 300-800ms per request
- Runs even for refinements where spaces are already known
- Unnecessary for clarification responses

**Solution**: Reuse spaces from turn history

```python
def planning_node(state):
    intent_type = state.get("intent_metadata", {}).get("intent_type")
    
    # Reuse spaces for refinements
    if intent_type in ["refinement", "clarification_response"]:
        prev_turn = find_most_recent_turn_with_spaces(state.get("turn_history", []))
        if prev_turn and prev_turn.get("relevant_spaces"):
            relevant_spaces_full = prev_turn["relevant_spaces"]
            print("✓ Reusing spaces from previous turn (skipped vector search)")
        else:
            relevant_spaces_full = planning_agent.search_relevant_spaces(planning_query)
    else:
        relevant_spaces_full = planning_agent.search_relevant_spaces(planning_query)
```

---

### 🟡 **ISSUE #7: Spark DataFrame `.collect()` Operations**

**Location**: Lines 555, 1418, 1428

**Problem**:

```python
# SQL execution agent
df = spark.sql(extracted_sql)
results_list = df.collect()  # ⚠️ Brings all data to driver
row_count = len(results_list)
```

**Impact**:

- Large result sets (>1000 rows) cause memory issues
- Unnecessary data transfer for row counting
- Blocks response generation

**Solution**: Optimize data collection

```python
# Get count without collecting
row_count = df.count()  # Much faster than len(collect())

# Limit collection for preview
preview_results = df.limit(100).collect()  # Only what's needed

# For full results, use iterator
if row_count > 1000:
    result_data = df.toLocalIterator()  # Memory-efficient streaming
else:
    result_data = [row.asDict() for row in df.collect()]
```

---

### 🟡 **ISSUE #8: No Streaming for First Token**

**Location**: Lines 732, 864, 1022, 1273, 1527 - all `.invoke()` calls

**Problem**:

```python
response = self.llm.invoke(clarity_prompt)  # ⚠️ Waits for complete response
content = response.content.strip()
```

**Impact**:

- User waits for entire LLM response before seeing anything
- TTFT = total generation time (2-5 seconds)
- Poor user experience

**Solution**: Use `.stream()` for immediate feedback

```python
def check_clarity_streaming(self, query: str):
    from langgraph.config import get_stream_writer
    writer = get_stream_writer()
    
    full_response = ""
    for chunk in self.llm.stream(clarity_prompt):
        if chunk.content:
            full_response += chunk.content
            writer({"type": "clarity_thinking", "content": chunk.content})
    
    # Parse full response
    clarity_result = json.loads(full_response)
    return clarity_result
```

---

### 🟢 **ISSUE #9: Adaptive Clarification Strategy Overhead**

**Location**: Lines 1728-1805

**Problem**:

```python
def adaptive_clarification_strategy(clarity_result, intent_metadata, turn_history):
    # Complex evaluation with 6 factors
    ambiguity_score = clarity_result.get("ambiguity_score", 0.5)
    recent_turns = turn_history[-5:]
    recent_clarifications = sum(1 for t in recent_turns if t.get("triggered_clarification"))
    # ... more processing
```

**Impact**:

- Moderate (100-200ms)
- Runs after clarity check, delays routing decision
- Could be simplified for common cases

**Solution**: Fast-path for obvious cases

```python
def adaptive_clarification_strategy_fast(clarity_result, intent_metadata, turn_history):
    # Fast path for clear queries
    if clarity_result.get("question_clear", True):
        return False
    
    # Fast path for clarification responses
    if should_skip_clarification_for_intent(intent_metadata.get("intent_type")):
        return False
    
    # Full evaluation only when needed
    return adaptive_clarification_strategy_full(clarity_result, intent_metadata, turn_history)
```

---

### 🟢 **ISSUE #10: No Request Batching or Connection Pooling**

**Location**: Throughout - multiple independent LLM/API calls

**Problem**:

- Each LLM call creates new connection
- No connection reuse across requests
- No batching of similar operations

**Impact**:

- Connection overhead: 50-200ms per call
- Cumulative across 5-10 LLM calls per request

**Solution**: Implement connection pooling

```python
# In agent initialization
_llm_pool = {}

def get_pooled_llm(endpoint_name: str):
    if endpoint_name not in _llm_pool:
        _llm_pool[endpoint_name] = ChatDatabricks(
            endpoint=endpoint_name,
            max_retries=2,
            timeout=30
        )
    return _llm_pool[endpoint_name]
```

---

## Optimization Priority Matrix


| Issue                      | Impact    | Effort | Priority | Expected Gain     |
| -------------------------- | --------- | ------ | -------- | ----------------- |
| #1: Space Context Loading  | 🔴 High   | Low    | **P0**   | -1 to -2s         |
| #2: Agent Recreation       | 🔴 High   | Low    | **P0**   | -500ms to -1s     |
| #3: Genie Agent Creation   | 🔴 High   | Medium | **P0**   | -1 to -3s         |
| #4: Sequential LLM Calls   | 🟡 Medium | High   | **P1**   | -2 to -4s         |
| #5: Intent Detection       | 🟡 Medium | Medium | **P1**   | -1 to -2s         |
| #6: Vector Search          | 🟡 Medium | Low    | **P1**   | -300 to -800ms    |
| #7: Spark Collect          | 🟡 Medium | Low    | **P2**   | -200 to -500ms    |
| #8: No Streaming           | 🟡 Medium | Medium | **P1**   | TTFT: -2 to -5s   |
| #9: Clarification Strategy | 🟢 Low    | Low    | **P3**   | -100 to -200ms    |
| #10: Connection Pooling    | 🟢 Low    | Medium | **P2**   | -500ms cumulative |


---

## Implementation Approach

### Phase 1: Quick Wins (P0 - Caching)

**Estimated total gain: -3 to -6 seconds**

1. Implement space context caching with TTL
2. Add agent instance caching at module level
3. Create Genie agent pool with lazy initialization
4. Update all nodes to use cached instances

**Files to modify**:

- Lines 534-558: `load_space_context()` → add caching
- Lines 1906, 2052, 2128, 2217: Use cached agents
- Lines 1101-1156: Implement Genie agent pool

### Phase 2: TTFT Optimization (P1 - Streaming & Routing)

**Estimated TTFT gain: -3 to -7 seconds**

1. Replace `.invoke()` with `.stream()` for first token emission
2. Implement fast-path routing to skip intent detection
3. Add vector search result caching for refinements
4. Stream custom events for immediate user feedback

**Files to modify**:

- Lines 732, 864, 1022, 1273, 1527: Add streaming
- Line 2608: Conditional entry point logic
- Lines 2062: Cache vector search results

### Phase 3: Advanced Parallelization (P1 - Async)

**Estimated total gain: -2 to -4 seconds**

1. Identify safe parallelization points (read-only operations)
2. Implement async UC function calls in SQL synthesis
3. Parallel Genie agent queries where applicable
4. Add ThreadPoolExecutor for independent operations

**Note**: Be cautious with LangGraph state mutations

### Phase 4: Polish (P2-P3)

**Estimated total gain: -800ms to -1.2s**

1. Optimize Spark operations (`.count()` vs `.collect()`)
2. Implement connection pooling for LLMs
3. Simplify adaptive clarification strategy
4. Add performance monitoring/instrumentation

---

## Expected Performance Improvements

### Current Performance (Estimated)

- **TTFT**: 3-7 seconds (waiting for clarity check + intent detection)
- **Total Time**: 10-20 seconds (full workflow)
- **Genie Route**: 15-25 seconds (with Genie agent creation)

### After Phase 1 (Caching)

- **TTFT**: 2-5 seconds (-1 to -2s)
- **Total Time**: 7-14 seconds (-3 to -6s)
- **Genie Route**: 12-18 seconds (-3 to -7s)

### After Phase 2 (Streaming + Routing)

- **TTFT**: 0.5-2 seconds (-1.5 to -3s additional) ⚡
- **Total Time**: 5-11 seconds (-2 to -3s additional)
- **Genie Route**: 9-14 seconds (-3 to -4s additional)

### After Phase 3 (Parallelization)

- **TTFT**: 0.5-2 seconds (maintained)
- **Total Time**: 3-7 seconds (-2 to -4s additional)
- **Genie Route**: 6-10 seconds (-3 to -4s additional)

### After Phase 4 (Polish)

- **TTFT**: 0.3-1.5 seconds (-200 to -500ms additional)
- **Total Time**: 2.2-6.2 seconds (-800ms to -1.2s additional)
- **Genie Route**: 5-9 seconds (-1 to -1s additional)

---

## Monitoring & Validation

### Add Performance Instrumentation

```python
import time
from functools import wraps

def measure_time(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        print(f"⏱️ {func.__name__}: {elapsed:.2f}s")
        return result
    return wrapper

# Apply to all agent methods
@measure_time
def check_clarity(self, query: str):
    ...
```

### Key Metrics to Track

1. **TTFT** (time to first token): Agent start → first streaming output
2. **TTCL** (time to completion): Total workflow execution time
3. **Cache Hit Rate**: % of requests using cached data
4. **Node Execution Times**: Per-node performance breakdown
5. **LLM Call Latency**: Individual LLM endpoint response times

---

## Risks & Mitigation

### Risk 1: Stale Cache Data

- **Impact**: Users see outdated space metadata
- **Mitigation**: Use TTL (30 min) + manual cache invalidation endpoint

### Risk 2: State Consistency with Caching

- **Impact**: Cached agents might have stale state
- **Mitigation**: Only cache stateless components; clear cache on config changes

### Risk 3: Parallel Execution Bugs

- **Impact**: Race conditions, state corruption
- **Mitigation**: Only parallelize provably independent operations; extensive testing

### Risk 4: Memory Usage with Caching

- **Impact**: Cached agents and data consume memory
- **Mitigation**: Monitor memory usage; implement LRU eviction if needed

---

## Testing Strategy

1. **Unit Tests**: Cache behavior, agent initialization
2. **Performance Tests**: Benchmark each optimization phase
3. **Integration Tests**: End-to-end workflow with various query types
4. **Load Tests**: Concurrent requests to validate caching under load
5. **A/B Testing**: Compare optimized vs. original in production

---

## Additional Considerations

### Code Quality

- Add type hints for cached functions
- Document cache invalidation strategy
- Add logging for cache hits/misses
- Create performance debugging utilities

### Backward Compatibility

- Keep original functions as fallbacks
- Feature flag for new optimizations
- Gradual rollout strategy

### Future Optimizations (Beyond Scope)

- Model quantization for faster inference
- Prompt compression to reduce token count
- Result caching for common queries
- Precompute frequent vector searches

