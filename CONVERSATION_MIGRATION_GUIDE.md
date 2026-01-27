# Conversation Management System Migration Guide

This guide documents the migration from the legacy clarification/conversation management system to the new turn-based system with intent detection.

## Table of Contents

1. [Overview of Changes](#overview-of-changes)
2. [Breaking Changes](#breaking-changes)
3. [Migration Steps](#migration-steps)
4. [Updated API](#updated-api)
5. [Backward Compatibility](#backward-compatibility)
6. [Troubleshooting](#troubleshooting)

## Overview of Changes

### What Changed?

The conversation management system has been completely overhauled to use:

1. **Turn-Based Context Model**: Each conversation turn is explicitly tracked with metadata
2. **Intent Detection as First-Class Service**: Dedicated node for classifying user intent
3. **Adaptive Clarification Strategy**: Multi-factor decision making instead of hard count limits
4. **Simplified State**: From 20+ fields to ~15 core fields

### Why the Changes?

**Problems with Old System:**
- 7+ clarification-related state fields were hard to maintain
- Manual message parsing was fragile (`find_most_recent_clarification_context()`)
- Intent detection was a side effect, not usable for business logic
- Hard-coded clarification limits (max 1) were inflexible
- Manual context assembly with string templates

**Benefits of New System:**
- **Simpler**: Fewer state fields, cleaner code
- **More Powerful**: Intent detection enables billing, analytics, routing
- **More Flexible**: Adaptive clarification strategy based on context
- **More Maintainable**: Separate concerns, testable components
- **Business-Ready**: Intent metadata for billing/analytics out of the box

## Breaking Changes

### 1. State Field Removals

**REMOVED Fields:**
```python
# Old state fields that no longer exist
"clarification_count"  # Replaced by adaptive strategy + turn history
"last_clarified_query"  # Replaced by turn_history
"combined_query_context"  # Replaced by current_turn.context_summary
"clarification_needed"  # Replaced by pending_clarification.reason
"clarification_options"  # Replaced by pending_clarification.options
"clarification_message"  # Generated from pending_clarification
"original_query"  # Replaced by messages[-1].content or current_turn.query
```

**NEW Fields:**
```python
# New turn-based fields
"current_turn": ConversationTurn  # Current turn with all context
"turn_history": List[ConversationTurn]  # Historical turns
"intent_metadata": IntentMetadata  # Intent classification + business metadata
"pending_clarification": Optional[ClarificationRequest]  # Unified clarification object
```

### 2. Helper Function Removals

**REMOVED Functions:**
- `find_most_recent_clarification_context(messages)` → Use intent detection
- `is_new_question(query, messages, llm)` → Use `intent_detection_node`

**NEW Components:**
- `IntentDetectionAgent` class in `kumc_poc/intent_detection_service.py`
- `intent_detection_node()` function (runs before clarification)
- `adaptive_clarification_strategy()` function (multi-factor decision)

### 3. Workflow Changes

**OLD Entry Point:**
```python
workflow.set_entry_point("clarification")
```

**NEW Entry Point:**
```python
workflow.set_entry_point("intent_detection")
workflow.add_edge("intent_detection", "clarification")
```

### 4. Clarification Logic Changes

**OLD Approach:**
```python
# Check clarification_count
if clarification_count >= 1:
    # Max attempts reached
    proceed_anyway()
else:
    # Ask for clarification
    clarification_count += 1
```

**NEW Approach:**
```python
# Use adaptive strategy
if should_skip_clarification_for_intent(intent_type):
    skip_clarification()
elif adaptive_clarification_strategy(clarity_result, intent_metadata, turn_history):
    request_clarification()
else:
    proceed_with_best_guess()
```

## Migration Steps

### Step 1: Update Dependencies

Add the new modules to your imports:

```python
# Add to agent.py imports
from kumc_poc.conversation_models import (
    ConversationTurn,
    ClarificationRequest,
    IntentMetadata,
    create_conversation_turn,
    create_clarification_request,
    find_turn_by_id,
    format_clarification_message,
    get_reset_state_template
)
from kumc_poc.intent_detection_service import (
    IntentDetectionAgent,
    create_intent_metadata_from_result,
    should_skip_clarification_for_intent
)
```

### Step 2: Update AgentState TypedDict

**OLD:**
```python
class AgentState(TypedDict):
    original_query: str
    clarification_needed: Optional[str]
    clarification_options: Optional[List[str]]
    clarification_count: Optional[int]
    last_clarified_query: Optional[str]
    combined_query_context: Optional[str]
    # ... other fields
```

**NEW:**
```python
class AgentState(TypedDict):
    # Turn tracking (NEW)
    current_turn: ConversationTurn
    turn_history: Annotated[List[ConversationTurn], operator.add]
    intent_metadata: IntentMetadata
    
    # Clarification (SIMPLIFIED)
    pending_clarification: Optional[ClarificationRequest]
    question_clear: bool
    
    # ... other fields unchanged
```

### Step 3: Update RESET_STATE_TEMPLATE

**OLD:**
```python
RESET_STATE_TEMPLATE = {
    "clarification_needed": None,
    "clarification_options": None,
    "combined_query_context": None,
    # ... other per-query fields
}
```

**NEW:**
```python
# Use the shared template from conversation_models
RESET_STATE_TEMPLATE = get_reset_state_template()

# Note: Turn-based fields (current_turn, turn_history, intent_metadata)
# are NOT reset - they're managed by intent_detection_node
```

### Step 4: Add Intent Detection Node

```python
def intent_detection_node(state: AgentState) -> dict:
    """Detect intent before clarification."""
    current_query = state["messages"][-1].content
    turn_history = state.get("turn_history", [])
    messages = state["messages"]
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
    intent_agent = IntentDetectionAgent(llm)
    
    intent_result = intent_agent.detect_intent(
        current_query=current_query,
        turn_history=turn_history,
        messages=messages
    )
    
    turn = create_conversation_turn(
        query=current_query,
        intent_type=intent_result["intent_type"],
        parent_turn_id=intent_result.get("parent_turn_id"),
        context_summary=intent_result.get("context_summary"),
        triggered_clarification=False
    )
    
    intent_metadata = create_intent_metadata_from_result(intent_result)
    
    return {
        "current_turn": turn,
        "turn_history": [turn],
        "intent_metadata": intent_metadata,
        "next_agent": "clarification"
    }
```

### Step 5: Update Clarification Node

**Key Changes:**
- Get `current_turn` and `intent_type` from state (set by intent_detection_node)
- Skip clarification for `clarification_response` intent
- Use `adaptive_clarification_strategy()` instead of count-based logic
- Create `ClarificationRequest` object instead of separate fields

```python
def clarification_node(state: AgentState) -> dict:
    current_turn = state.get("current_turn")
    intent_type = current_turn.get("intent_type")
    
    # Auto-skip for clarification responses
    if intent_type == "clarification_response":
        return {"question_clear": True, "next_agent": "planning"}
    
    # Check clarity
    clarity_result = clarification_agent.check_clarity(...)
    
    if clarity_result["question_clear"]:
        return {"question_clear": True, "next_agent": "planning"}
    
    # Use adaptive strategy
    should_clarify = adaptive_clarification_strategy(
        clarity_result=clarity_result,
        intent_metadata=state["intent_metadata"],
        turn_history=state["turn_history"]
    )
    
    if not should_clarify:
        # Proceed with best guess
        return {"question_clear": True, "next_agent": "planning"}
    
    # Request clarification
    clarification = create_clarification_request(...)
    return {"pending_clarification": clarification, "question_clear": False}
```

### Step 6: Update Planning Node

**Key Changes:**
- Use `current_turn.context_summary` instead of `combined_query_context`
- Access query from `current_turn.query`

```python
def planning_node(state: AgentState) -> dict:
    current_turn = state.get("current_turn")
    query = current_turn["query"]
    context_summary = current_turn.get("context_summary")
    
    # Use context_summary if available (LLM-generated, not template-based)
    planning_query = context_summary or query
    
    # Rest of planning logic...
    plan = planning_agent(planning_query, relevant_spaces)
    return {"plan": plan, ...}
```

### Step 7: Update Workflow Graph

```python
# Add intent detection node
workflow.add_node("intent_detection", intent_detection_node)

# Change entry point
workflow.set_entry_point("intent_detection")

# Add edge from intent detection to clarification
workflow.add_edge("intent_detection", "clarification")

# Rest of workflow unchanged
```

### Step 8: Remove Deprecated Code

Remove these functions and their calls:
- `find_most_recent_clarification_context()`
- `is_new_question()`

## Updated API

### Using the Agent (Unchanged)

```python
# API remains the same!
result = invoke_super_agent_hybrid(
    query="Show me patient data",
    thread_id="user_123"
)

# Check if clarification needed
if result.get("pending_clarification"):
    print(result["pending_clarification"]["reason"])
    print(result["pending_clarification"]["options"])

# Access intent metadata (NEW)
intent_type = result["intent_metadata"]["intent_type"]
complexity = result["intent_metadata"]["complexity"]
```

### Accessing New Features

```python
# Get current turn info
current_turn = result["current_turn"]
print(f"Intent: {current_turn['intent_type']}")
print(f"Context: {current_turn['context_summary']}")

# Get turn history
for turn in result["turn_history"]:
    print(f"Turn {turn['turn_id']}: {turn['query']} ({turn['intent_type']})")

# Business logic integration
cost = BusinessLogicIntegration.calculate_usage_cost(result)
analytics = BusinessLogicIntegration.log_analytics_event(result)
```

## Backward Compatibility

### For Existing Conversations

**In-Flight Conversations**: The new system maintains backward compatibility by:
1. Checking for `current_turn` field - if missing, uses legacy fallback
2. Creating turn on-the-fly from `original_query` if needed
3. Defaulting intent to `"new_question"` for legacy state

```python
# Fallback logic in nodes
current_turn = state.get("current_turn")
if not current_turn:
    # Legacy fallback
    query = state.get("original_query", "")
    current_turn = {"query": query, "intent_type": "new_question"}
```

### For External Integrations

**Old Field Access**: If external code accesses removed fields:

```python
# OLD CODE (will break):
combined_context = state["combined_query_context"]
clarification_count = state["clarification_count"]

# NEW CODE (migration):
combined_context = state["current_turn"]["context_summary"]
clarification_count = len([t for t in state["turn_history"] if t.get("triggered_clarification")])
```

**Compatibility Shim** (temporary):

```python
# Add to state initialization for backward compatibility
def add_legacy_fields(state: AgentState) -> AgentState:
    """Add legacy fields for backward compatibility."""
    state["original_query"] = state.get("current_turn", {}).get("query", "")
    state["combined_query_context"] = state.get("current_turn", {}).get("context_summary")
    state["clarification_count"] = len([t for t in state.get("turn_history", []) if t.get("triggered_clarification")])
    return state
```

## Troubleshooting

### Issue 1: "current_turn" KeyError

**Symptom**: `KeyError: 'current_turn'` in clarification or planning nodes

**Cause**: Intent detection node not running or state not propagating

**Fix**:
1. Verify intent_detection_node is added to workflow
2. Verify entry point is set to "intent_detection"
3. Check that intent_detection_node returns `current_turn` in updates dict

### Issue 2: Intent Detection Always Returns "new_question"

**Symptom**: All queries classified as new questions, no refinements detected

**Cause**: Turn history not persisting or empty

**Fix**:
1. Verify `turn_history` uses `operator.add` reducer in AgentState
2. Check that CheckpointSaver is properly configured
3. Verify same `thread_id` is used across conversation

### Issue 3: Clarification Requested Too Often/Never

**Symptom**: Adaptive strategy not working as expected

**Cause**: Missing or incorrect intent_metadata

**Fix**:
1. Verify intent_metadata is set by intent_detection_node
2. Check that clarity_result includes `ambiguity_score` and `best_guess_confidence`
3. Adjust adaptive_clarification_strategy thresholds if needed

### Issue 4: Context Summary Not Generated

**Symptom**: `current_turn.context_summary` is None or empty

**Cause**: Intent detection LLM not generating summary

**Fix**:
1. Check LLM endpoint is responding
2. Verify prompt includes `context_summary` in output format
3. Check JSON parsing in IntentDetectionAgent

### Issue 5: Performance Degradation

**Symptom**: Queries take longer with new system

**Cause**: Additional LLM call for intent detection

**Expected**: ~1-2 second increase for intent detection
**Fix**: If longer, check:
1. LLM endpoint latency
2. Fast-path detection working for clarification responses
3. Consider caching for repeated patterns

## Migration Checklist

- [ ] Update imports to include `conversation_models` and `intent_detection_service`
- [ ] Update `AgentState` TypedDict with new fields
- [ ] Update `RESET_STATE_TEMPLATE` to use `get_reset_state_template()`
- [ ] Add `intent_detection_node` to codebase
- [ ] Add `adaptive_clarification_strategy` function
- [ ] Update `clarification_node` to use turn-based context
- [ ] Update `planning_node` to use `current_turn.context_summary`
- [ ] Update workflow graph (entry point, add intent node)
- [ ] Remove deprecated functions (`find_most_recent_clarification_context`, `is_new_question`)
- [ ] Test all intent types (new_question, refinement, clarification_response, continuation)
- [ ] Test adaptive clarification strategy
- [ ] Test business logic integration (billing, analytics)
- [ ] Update external integrations that access removed fields
- [ ] Run comprehensive test suite
- [ ] Monitor production metrics (clarification rate, intent accuracy, latency)

## Support

For questions or issues:
- See test cases in `CONVERSATION_MANAGEMENT_TEST_CASES.md`
- Review architecture in plan file
- Check code examples in `kumc_poc/conversation_models.py` and `kumc_poc/intent_detection_service.py`

## Version History

- **v2.0.0** (2026-01-27): Complete overhaul with turn-based context and intent detection
- **v1.0.0**: Legacy system with count-based clarification
