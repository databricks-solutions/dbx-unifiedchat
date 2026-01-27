# Conversation Management Test Cases

This document provides comprehensive test cases for the new turn-based conversation management system with intent detection.

## Test Case 1: New Question (Simple)

**Scenario**: User asks a straightforward new question

```python
# First query in a new thread
query1 = "How many patients are in the dataset?"
result1 = invoke_super_agent_hybrid(query1, thread_id="test_new_question")

# Verify:
# - intent_type = "new_question"
# - complexity = "simple"
# - No clarification requested (simple query)
assert result1["intent_metadata"]["intent_type"] == "new_question"
assert result1["intent_metadata"]["complexity"] == "simple"
assert result1["pending_clarification"] is None
```

**Expected Intent Detection**:
- `intent_type`: `"new_question"`
- `confidence`: High (> 0.8)
- `topic_change_score`: 1.0 (no previous context)
- `complexity`: `"simple"`
- `domain`: `"patients"`
- `operation`: `"aggregate"`

## Test Case 2: Refinement

**Scenario**: User refines their previous query

```python
# Initial query
query1 = "Show me patient data"
result1 = invoke_super_agent_hybrid(query1, thread_id="test_refinement")

# Refinement - same topic, adding filter
query2 = "Only patients over 50 years old"
result2 = invoke_super_agent_hybrid(query2, thread_id="test_refinement")

# Verify:
# - intent_type = "refinement"
# - parent_turn_id points to previous turn
# - context_summary includes both queries
assert result2["intent_metadata"]["intent_type"] == "refinement"
assert result2["intent_metadata"]["parent_turn_id"] == result1["current_turn"]["turn_id"]
assert "patient data" in result2["current_turn"]["context_summary"].lower()
assert "50 years old" in result2["current_turn"]["context_summary"].lower()
```

**Expected Intent Detection**:
- `intent_type`: `"refinement"`
- `confidence`: High (> 0.8)
- `topic_change_score`: Low (< 0.3)
- `parent_turn_id`: Previous turn's ID
- `context_summary`: Includes both original query and refinement

## Test Case 3: Clarification Response

**Scenario**: Agent asks for clarification, user responds

```python
# Vague query that triggers clarification
query1 = "Show me the data"
result1 = invoke_super_agent_hybrid(query1, thread_id="test_clarification")

# Verify clarification was requested
assert result1["pending_clarification"] is not None
assert result1["question_clear"] == False

# User responds to clarification
query2 = "Patient count by age group"
result2 = invoke_super_agent_hybrid(query2, thread_id="test_clarification")

# Verify:
# - intent_type = "clarification_response"
# - Clarification was skipped (no double clarification)
# - context_summary includes original query + clarification + response
assert result2["intent_metadata"]["intent_type"] == "clarification_response"
assert result2["pending_clarification"] is None
assert result2["question_clear"] == True
assert "Show me the data" in result2["current_turn"]["context_summary"]
assert "Patient count" in result2["current_turn"]["context_summary"]
```

**Expected Intent Detection**:
- `intent_type`: `"clarification_response"`
- `confidence`: Very high (> 0.9) via fast-path detection
- `context_summary`: Structured with original query, clarification question, and answer

## Test Case 4: Continuation

**Scenario**: User explores same topic from different angle

```python
# Initial query
query1 = "Show me active plan members"
result1 = invoke_super_agent_hybrid(query1, thread_id="test_continuation")

# Continuation - related but different angle
query2 = "What about inactive members?"
result2 = invoke_super_agent_hybrid(query2, thread_id="test_continuation")

# Verify:
# - intent_type = "continuation"
# - Related to previous but different focus
assert result2["intent_metadata"]["intent_type"] == "continuation"
assert result2["intent_metadata"]["topic_change_score"] < 0.5
```

**Expected Intent Detection**:
- `intent_type`: `"continuation"`
- `confidence`: High (> 0.7)
- `topic_change_score`: Medium (0.3-0.5)
- `parent_turn_id`: Previous turn's ID

## Test Case 5: Topic Change (New Question Mid-Conversation)

**Scenario**: User asks completely different question in same thread

```python
# Initial query about patients
query1 = "How many patients are over 50?"
result1 = invoke_super_agent_hybrid(query1, thread_id="test_topic_change")

# Completely different topic
query2 = "Show me medication costs by drug type"
result2 = invoke_super_agent_hybrid(query2, thread_id="test_topic_change")

# Verify:
# - intent_type = "new_question" (even though thread continues)
# - topic_change_score is high
# - No parent_turn_id (new topic)
assert result2["intent_metadata"]["intent_type"] == "new_question"
assert result2["intent_metadata"]["topic_change_score"] > 0.7
assert result2["intent_metadata"]["parent_turn_id"] is None
```

**Expected Intent Detection**:
- `intent_type`: `"new_question"`
- `confidence`: High (> 0.8)
- `topic_change_score`: High (> 0.7)
- `parent_turn_id`: `None`

## Test Case 6: Adaptive Clarification Strategy

**Scenario**: Test that clarification requests are adaptive

```python
# Test Factor 1: Low ambiguity - skip clarification
query1 = "Show patient count"  # Clear enough
result1 = invoke_super_agent_hybrid(query1, thread_id="test_adaptive_1")
assert result1["pending_clarification"] is None  # No clarification

# Test Factor 2: Too many recent clarifications
thread_id = "test_adaptive_2"
for i in range(3):
    # Ask vague questions that trigger clarification
    result = invoke_super_agent_hybrid(f"Show data {i}", thread_id=thread_id)
    # Respond to clarification
    invoke_super_agent_hybrid(f"Patient data for query {i}", thread_id=thread_id)

# Next vague query should NOT trigger clarification (too many recent)
result_final = invoke_super_agent_hybrid("Show info", thread_id=thread_id)
# Adaptive strategy should skip clarification
assert result_final["question_clear"] == True  # Proceeded despite ambiguity

# Test Factor 3: Simple complexity - skip clarification
query2 = "Count rows"  # Simple query, even if slightly vague
result2 = invoke_super_agent_hybrid(query2, thread_id="test_adaptive_3")
# Likely no clarification for simple query
```

## Test Case 7: Business Logic Integration

**Scenario**: Verify intent metadata can be used for billing/analytics

```python
query1 = "Complex multi-space query requiring joins"
result1 = invoke_super_agent_hybrid(query1, thread_id="test_billing")

# Calculate cost using business logic
cost = BusinessLogicIntegration.calculate_usage_cost(result1)
assert cost["intent_type"] == "new_question"
assert cost["complexity"] == "complex"
assert cost["total_cost"] == 0.10 * 2.0  # base * complexity multiplier

# Log analytics
analytics = BusinessLogicIntegration.log_analytics_event(result1)
assert analytics["intent_type"] == "new_question"
assert analytics["complexity"] == "complex"
assert analytics["turn_count"] == 1

# Refinement should cost less
query2 = "Only for 2024"
result2 = invoke_super_agent_hybrid(query2, thread_id="test_billing")

cost2 = BusinessLogicIntegration.calculate_usage_cost(result2)
assert cost2["intent_type"] == "refinement"
assert cost2["total_cost"] < cost["total_cost"]  # Cheaper than new question
```

## Test Case 8: Turn History and Context

**Scenario**: Verify turn history is properly tracked

```python
thread_id = "test_turn_history"

# Turn 1
query1 = "Show patients"
result1 = invoke_super_agent_hybrid(query1, thread_id=thread_id)
assert len(result1["turn_history"]) == 1
assert result1["turn_history"][0]["query"] == query1

# Turn 2
query2 = "Over 50 years old"
result2 = invoke_super_agent_hybrid(query2, thread_id=thread_id)
assert len(result2["turn_history"]) == 2
assert result2["turn_history"][-1]["query"] == query2
assert result2["turn_history"][-1]["intent_type"] == "refinement"

# Turn 3
query3 = "Show medication costs instead"
result3 = invoke_super_agent_hybrid(query3, thread_id=thread_id)
assert len(result3["turn_history"]) == 3
assert result3["turn_history"][-1]["intent_type"] == "new_question"
```

## Test Case 9: Context Summary Quality

**Scenario**: Verify LLM-generated context summaries are useful

```python
# Create a complex conversation
thread_id = "test_context_summary"

query1 = "Show me patient demographics"
result1 = invoke_super_agent_hybrid(query1, thread_id=thread_id)

query2 = "Filter to California residents"
result2 = invoke_super_agent_hybrid(query2, thread_id=thread_id)

query3 = "Only those over 65"
result3 = invoke_super_agent_hybrid(query3, thread_id=thread_id)

# Check context summary includes all refinements
context = result3["current_turn"]["context_summary"]
assert context is not None
assert "patient demographics" in context.lower()
assert "california" in context.lower()
assert "65" in context or "over 65" in context.lower()

# Verify planning agent receives this context
assert result3["plan"] is not None  # Planning succeeded with context
```

## Test Case 10: Mixed Scenario (Real-World Conversation)

**Scenario**: Complex multi-turn conversation with various intent types

```python
thread_id = "test_mixed_scenario"

# Turn 1: New question (vague, triggers clarification)
query1 = "Show me the data"
result1 = invoke_super_agent_hybrid(query1, thread_id=thread_id)
assert result1["intent_metadata"]["intent_type"] == "new_question"
assert result1["pending_clarification"] is not None

# Turn 2: Clarification response
query2 = "Patient count by state"
result2 = invoke_super_agent_hybrid(query2, thread_id=thread_id)
assert result2["intent_metadata"]["intent_type"] == "clarification_response"
assert result2["pending_clarification"] is None

# Turn 3: Refinement
query3 = "Only for Texas and California"
result3 = invoke_super_agent_hybrid(query3, thread_id=thread_id)
assert result3["intent_metadata"]["intent_type"] == "refinement"

# Turn 4: Continuation (different angle on same topic)
query4 = "What about by age group instead of state?"
result4 = invoke_super_agent_hybrid(query4, thread_id=thread_id)
assert result4["intent_metadata"]["intent_type"] in ["continuation", "refinement"]

# Turn 5: New question (topic change)
query5 = "Show me medication costs for diabetes treatments"
result5 = invoke_super_agent_hybrid(query5, thread_id=thread_id)
assert result5["intent_metadata"]["intent_type"] == "new_question"
assert result5["intent_metadata"]["topic_change_score"] > 0.7

# Verify turn history
assert len(result5["turn_history"]) == 5
```

## Running the Tests

### Manual Testing

```python
# Run in Databricks notebook after loading the agent
for test_case in [test_case_1, test_case_2, ...]:
    try:
        test_case()
        print(f"✅ {test_case.__name__} passed")
    except AssertionError as e:
        print(f"❌ {test_case.__name__} failed: {e}")
```

### Automated Testing

```python
import unittest

class TestConversationManagement(unittest.TestCase):
    def setUp(self):
        self.test_thread_id = f"test_{uuid.uuid4()}"
    
    def test_new_question(self):
        # Test Case 1 implementation
        pass
    
    def test_refinement(self):
        # Test Case 2 implementation
        pass
    
    # ... more tests

if __name__ == "__main__":
    unittest.main()
```

## Success Criteria

1. **Intent Detection Accuracy**: > 85% correct classification
2. **Context Summary Quality**: Planning agent can use context to generate correct SQL
3. **Clarification Rate**: < 20% of queries trigger clarification (with adaptive strategy)
4. **Turn History**: Correctly tracks all turns with proper parent relationships
5. **Business Logic**: Cost calculations and analytics work correctly
6. **Performance**: Intent detection adds < 2 seconds latency per query

## Metrics to Track

- Intent type distribution
- Clarification request rate
- Topic change frequency
- Average turns per conversation
- Cost per query type
- User satisfaction (manual feedback)
