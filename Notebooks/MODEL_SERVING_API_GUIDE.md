# Model Serving API Guide: SuperAgentHybridResponsesAgent

**Date:** January 17, 2026  
**Deployed Agent:** `SuperAgentHybridResponsesAgent`  
**Interface:** MLflow ResponsesAgent compatible with Databricks Model Serving

---

## Overview

The deployed `SuperAgentHybridResponsesAgent` supports three conversation scenarios:

1. **New Query**: Start a new conversation
2. **Clarification Response**: Answer agent's clarification question
3. **Follow-Up Query**: Continue conversation with context

All scenarios use the same `/invocations` endpoint but with different `custom_inputs` parameters.

---

## API Endpoint Structure

### Base URL

```
https://<databricks-workspace>/serving-endpoints/<endpoint-name>/invocations
```

### Authentication

```bash
Authorization: Bearer <databricks-token>
Content-Type: application/json
```

---

## Scenario 1: New Query

Start a new conversation or ask a standalone question.

### Request Format

```json
{
  "messages": [
    {
      "role": "user",
      "content": "How many active plan members do we have?"
    }
  ],
  "custom_inputs": {
    "thread_id": "user_123_session_20260117"
  }
}
```

### Key Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `messages` | Array | Yes | List of messages (user query is last) |
| `custom_inputs.thread_id` | String | Recommended | Unique identifier for conversation thread. Use descriptive IDs like `"user_{userId}_session_{timestamp}"` |

### Response (if No Clarification Needed)

```json
{
  "output": [
    {
      "type": "text",
      "text": "Based on the query...\n\n**Execution Summary:**\n- SQL Query: SELECT COUNT(*) FROM...\n- Results: 12,345 active members\n..."
    }
  ],
  "custom_outputs": {
    "thread_id": "user_123_session_20260117"
  }
}
```

### Response (if Clarification Needed)

```json
{
  "output": [
    {
      "type": "text",
      "text": "I need clarification: The query is ambiguous about which members.\n\nPlease choose one of the following options:\n1. All members regardless of status\n2. Only active members\n3. Members enrolled in specific plans\n"
    }
  ],
  "custom_outputs": {
    "thread_id": "user_123_session_20260117",
    "question_clear": false,
    "clarification_needed": "The query is ambiguous about which members",
    "clarification_options": ["All members...", "Only active...", "Members enrolled..."],
    "original_query": "How many members?",
    "clarification_message": "I need clarification...",
    "clarification_count": 1
  }
}
```

**Important**: When `question_clear: false`, you must proceed to **Scenario 2** with a clarification response.

---

## Scenario 2: Clarification Response

User answers the agent's clarification question from Scenario 1.

### Request Format

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Only active members with enrollment in the past 12 months"
    }
  ],
  "custom_inputs": {
    "thread_id": "user_123_session_20260117",
    "is_clarification_response": true,
    "original_query": "How many members?",
    "clarification_message": "I need clarification: The query is ambiguous...",
    "clarification_count": 1
  }
}
```

### Key Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `messages[0].content` | String | Yes | User's answer to clarification question |
| `custom_inputs.thread_id` | String | **Yes** | **Must match** thread_id from Scenario 1 |
| `custom_inputs.is_clarification_response` | Boolean | **Yes** | **Must be `true`** to trigger clarification handling |
| `custom_inputs.original_query` | String | **Yes** | Original query from previous response's `custom_outputs` |
| `custom_inputs.clarification_message` | String | **Yes** | Clarification message from previous response's `custom_outputs` |
| `custom_inputs.clarification_count` | Integer | **Yes** | Clarification count from previous response's `custom_outputs` |

### Response

```json
{
  "output": [
    {
      "type": "text",
      "text": "**Analysis Complete**\n\n**Combined Context Used:**\n- Original Query: How many members?\n- Clarification: Only active members with enrollment in past 12 months\n\n**Execution Summary:**\n- SQL Query: SELECT COUNT(*) FROM members WHERE status='ACTIVE' AND enrollment_date >= DATE_SUB(CURRENT_DATE, 365)\n- Results: 8,234 active members\n..."
    }
  ],
  "custom_outputs": {
    "thread_id": "user_123_session_20260117",
    "question_clear": true,
    "original_query": "How many members?",
    "combined_query_context": "**Original Query**: How many members?\n**Clarification Question**: ...\n**User's Answer**: Only active members..."
  }
}
```

**What Happens:**
1. Agent receives user's clarification
2. Preserves `original_query` unchanged
3. Combines all three: original query + clarification question + user answer
4. Passes combined context to planning agent
5. Continues workflow with full context

---

## Scenario 3: Follow-Up Query

Ask a related question using context from previous conversation.

### Request Format

```json
{
  "messages": [
    {
      "role": "user",
      "content": "What's the breakdown by age group?"
    }
  ],
  "custom_inputs": {
    "thread_id": "user_123_session_20260117"
  }
}
```

### Key Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `messages[0].content` | String | Yes | New query that may reference previous context |
| `custom_inputs.thread_id` | String | **Yes** | **Must match** thread_id from previous calls to access conversation context |

**Note:** This looks identical to Scenario 1, but the **thread_id** determines the difference:
- **New Query**: Fresh/unique `thread_id` (no previous context)
- **Follow-Up Query**: Same `thread_id` as previous calls (accesses previous context)

### Response

```json
{
  "output": [
    {
      "type": "text",
      "text": "**Analysis Complete**\n\n**Context from Previous Query:**\n- Previous query asked about active members\n- Applying same filters to age group breakdown\n\n**Results:**\n- 18-25: 1,234 members\n- 26-35: 2,456 members\n- 36-50: 3,123 members\n- 50+: 1,421 members\n..."
    }
  ],
  "custom_outputs": {
    "thread_id": "user_123_session_20260117"
  }
}
```

**What Happens:**
1. Thread memory restores previous conversation state
2. Agent sees previous queries, clarifications, and results
3. Agent understands "breakdown" refers to the active members from previous query
4. No need to repeat context - agent has it

---

## Complete Workflow Examples

### Example 1: Simple Query (No Clarification)

```python
import requests

ENDPOINT = "https://your-workspace.databricks.com/serving-endpoints/super-agent-hybrid/invocations"
TOKEN = "your-databricks-token"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# Step 1: New query
response1 = requests.post(ENDPOINT, headers=HEADERS, json={
    "messages": [{"role": "user", "content": "How many patients over 50?"}],
    "custom_inputs": {"thread_id": "user_alice_001"}
})

result1 = response1.json()
print(result1["output"][0]["text"])

# If question_clear is True, we're done!
if result1["custom_outputs"].get("question_clear", True):
    print("Query completed successfully!")
```

### Example 2: Query with Clarification

```python
# Step 1: Vague query triggers clarification
response1 = requests.post(ENDPOINT, headers=HEADERS, json={
    "messages": [{"role": "user", "content": "Show me patient data"}],
    "custom_inputs": {"thread_id": "user_alice_002"}
})

result1 = response1.json()

# Check if clarification needed
if not result1["custom_outputs"].get("question_clear", True):
    print("Clarification needed:")
    print(result1["output"][0]["text"])
    
    # Step 2: Provide clarification
    response2 = requests.post(ENDPOINT, headers=HEADERS, json={
        "messages": [{"role": "user", "content": "Show patient count by age group"}],
        "custom_inputs": {
            "thread_id": "user_alice_002",  # Same thread_id
            "is_clarification_response": True,  # Critical flag
            "original_query": result1["custom_outputs"]["original_query"],
            "clarification_message": result1["custom_outputs"]["clarification_message"],
            "clarification_count": result1["custom_outputs"]["clarification_count"]
        }
    })
    
    result2 = response2.json()
    print("\nFinal result:")
    print(result2["output"][0]["text"])
```

### Example 3: Multi-Turn Conversation

```python
thread = "user_alice_003"

# Turn 1: Initial query
response1 = requests.post(ENDPOINT, headers=HEADERS, json={
    "messages": [{"role": "user", "content": "How many active members?"}],
    "custom_inputs": {"thread_id": thread}
})
print("Turn 1:", response1.json()["output"][0]["text"])

# Turn 2: Follow-up (references "active members" from Turn 1)
response2 = requests.post(ENDPOINT, headers=HEADERS, json={
    "messages": [{"role": "user", "content": "What's the breakdown by age?"}],
    "custom_inputs": {"thread_id": thread}  # Same thread
})
print("Turn 2:", response2.json()["output"][0]["text"])

# Turn 3: Further refinement
response3 = requests.post(ENDPOINT, headers=HEADERS, json={
    "messages": [{"role": "user", "content": "Show only 50+ age group"}],
    "custom_inputs": {"thread_id": thread}  # Same thread
})
print("Turn 3:", response3.json()["output"][0]["text"])
```

### Example 4: Clarification + Follow-Up

```python
thread = "user_alice_004"

# Turn 1: Vague query
response1 = requests.post(ENDPOINT, headers=HEADERS, json={
    "messages": [{"role": "user", "content": "Show costs"}],
    "custom_inputs": {"thread_id": thread}
})

# Turn 2: Clarification response
if not response1.json()["custom_outputs"].get("question_clear", True):
    response2 = requests.post(ENDPOINT, headers=HEADERS, json={
        "messages": [{"role": "user", "content": "Average claim costs by payer type"}],
        "custom_inputs": {
            "thread_id": thread,
            "is_clarification_response": True,
            **{k: v for k, v in response1.json()["custom_outputs"].items() 
               if k in ["original_query", "clarification_message", "clarification_count"]}
        }
    })
    print("Turn 2:", response2.json()["output"][0]["text"])

# Turn 3: Follow-up
response3 = requests.post(ENDPOINT, headers=HEADERS, json={
    "messages": [{"role": "user", "content": "Medicare patients only"}],
    "custom_inputs": {"thread_id": thread}
})
print("Turn 3:", response3.json()["output"][0]["text"])
```

---

## Client Implementation Checklist

When building a client to interact with the deployed agent:

### ✅ For New Queries
- [ ] Generate unique `thread_id` for new conversations
- [ ] Send user message in `messages` array
- [ ] Include `thread_id` in `custom_inputs`
- [ ] Check response `custom_outputs.question_clear`
- [ ] If `false`, save clarification state for next call

### ✅ For Clarification Responses
- [ ] Use **same `thread_id`** from previous call
- [ ] Set `is_clarification_response: true` in `custom_inputs`
- [ ] Pass `original_query` from previous response
- [ ] Pass `clarification_message` from previous response
- [ ] Pass `clarification_count` from previous response
- [ ] Send user's clarification in `messages` array

### ✅ For Follow-Up Queries
- [ ] Use **same `thread_id`** from previous call(s)
- [ ] Send new query in `messages` array
- [ ] Include only `thread_id` in `custom_inputs` (no other fields needed)
- [ ] Thread memory automatically restores context

---

## Error Handling

### Common Issues

#### Issue 1: "Clarification response not processed correctly"

**Cause:** Missing required fields in `custom_inputs`

**Solution:**
```json
{
  "custom_inputs": {
    "thread_id": "...",               // ✅ Required
    "is_clarification_response": true, // ✅ Required
    "original_query": "...",           // ✅ Required
    "clarification_message": "...",    // ✅ Required
    "clarification_count": 1           // ✅ Required
  }
}
```

#### Issue 2: "Follow-up query doesn't have previous context"

**Cause:** Different `thread_id` used

**Solution:** Ensure all follow-up queries use the **exact same `thread_id`**

```python
# ❌ Wrong - different thread_id
requests.post(ENDPOINT, json={"custom_inputs": {"thread_id": "session_1"}})
requests.post(ENDPOINT, json={"custom_inputs": {"thread_id": "session_2"}})  # Lost context!

# ✅ Correct - same thread_id
THREAD = "session_1"
requests.post(ENDPOINT, json={"custom_inputs": {"thread_id": THREAD}})
requests.post(ENDPOINT, json={"custom_inputs": {"thread_id": THREAD}})  # Has context!
```

#### Issue 3: "Agent always starts fresh, no memory"

**Cause:** Not including `thread_id` or using default thread

**Solution:** Always include explicit `thread_id` in `custom_inputs`

```json
{
  "custom_inputs": {
    "thread_id": "unique_identifier_here"  // ✅ Explicit thread ID
  }
}
```

---

## Thread Management Best Practices

### Thread ID Naming Convention

Use descriptive, unique thread IDs:

```python
# ✅ Good - includes user ID and timestamp
thread_id = f"user_{user_id}_session_{int(time.time())}"

# ✅ Good - includes user ID and session UUID
thread_id = f"user_{user_id}_session_{uuid.uuid4()}"

# ❌ Bad - too generic
thread_id = "default"

# ❌ Bad - not unique per conversation
thread_id = f"user_{user_id}"  # Same ID across all sessions!
```

### Thread Lifecycle

```python
class ConversationManager:
    def __init__(self, user_id):
        self.user_id = user_id
        self.thread_id = None
        self.pending_clarification = None
    
    def start_conversation(self):
        """Start a new conversation with fresh thread ID"""
        self.thread_id = f"user_{self.user_id}_session_{int(time.time())}"
        self.pending_clarification = None
        return self.thread_id
    
    def send_query(self, query):
        """Send a new query or follow-up"""
        payload = {
            "messages": [{"role": "user", "content": query}],
            "custom_inputs": {"thread_id": self.thread_id}
        }
        
        response = requests.post(ENDPOINT, headers=HEADERS, json=payload)
        result = response.json()
        
        # Check if clarification needed
        if not result["custom_outputs"].get("question_clear", True):
            # Save clarification state
            self.pending_clarification = {
                "original_query": result["custom_outputs"]["original_query"],
                "clarification_message": result["custom_outputs"]["clarification_message"],
                "clarification_count": result["custom_outputs"]["clarification_count"]
            }
        else:
            self.pending_clarification = None
        
        return result
    
    def send_clarification(self, clarification):
        """Respond to pending clarification"""
        if not self.pending_clarification:
            raise ValueError("No pending clarification")
        
        payload = {
            "messages": [{"role": "user", "content": clarification}],
            "custom_inputs": {
                "thread_id": self.thread_id,
                "is_clarification_response": True,
                **self.pending_clarification
            }
        }
        
        response = requests.post(ENDPOINT, headers=HEADERS, json=payload)
        result = response.json()
        
        # Clear pending clarification
        self.pending_clarification = None
        
        return result

# Usage
manager = ConversationManager(user_id="alice")
manager.start_conversation()

# Query 1
result1 = manager.send_query("Show patient data")

# If clarification needed
if manager.pending_clarification:
    result2 = manager.send_clarification("Patient count by age")

# Query 2 (follow-up)
result3 = manager.send_query("Show only 50+ age group")
```

---

## State Preservation Details

### What Gets Preserved in Thread Memory

The MemorySaver checkpoint preserves:

- ✅ `original_query` - Never overwritten
- ✅ `clarification_message` - Agent's clarification question
- ✅ `user_clarification_response` - User's answer
- ✅ `combined_query_context` - Structured combination for planning
- ✅ `messages` - Full conversation history
- ✅ All execution results (SQL, plans, errors)
- ✅ Agent state at each workflow step

### What Gets Reset for Follow-Up Queries

When starting a new query in the same thread:

- ❌ `original_query` - Set to new query
- ❌ `question_clear` - Reset to False
- ❌ `clarification_needed` - Cleared
- ❌ `sql_query` - Cleared (new query needs new SQL)
- ❌ `execution_result` - Cleared

But previous conversation history remains accessible!

---

## Testing Your Integration

### Test Suite

```python
def test_new_query():
    """Test: New query without clarification"""
    response = call_agent("How many patients over 50?", thread_id="test_1")
    assert response["custom_outputs"].get("question_clear", True)
    print("✅ Test 1 passed")

def test_clarification_flow():
    """Test: Query triggers clarification, then user clarifies"""
    thread = "test_2"
    
    # Vague query
    response1 = call_agent("Show data", thread_id=thread)
    assert not response1["custom_outputs"].get("question_clear", True)
    
    # Clarification response
    response2 = call_agent_clarification(
        "Patient count",
        thread_id=thread,
        clarification_state=response1["custom_outputs"]
    )
    assert response2["custom_outputs"].get("question_clear", True)
    print("✅ Test 2 passed")

def test_follow_up():
    """Test: Multi-turn conversation"""
    thread = "test_3"
    
    response1 = call_agent("How many patients?", thread_id=thread)
    response2 = call_agent("By age group", thread_id=thread)
    response3 = call_agent("50+ only", thread_id=thread)
    
    # All should succeed
    assert all(r["output"] for r in [response1, response2, response3])
    print("✅ Test 3 passed")

def test_thread_isolation():
    """Test: Different threads don't share context"""
    response_a = call_agent("Query A", thread_id="thread_a")
    response_b = call_agent("Query B", thread_id="thread_b")
    
    # Threads should be independent
    print("✅ Test 4 passed")
```

---

## Summary

### Key Takeaways

1. **Thread ID is Critical**: Same `thread_id` = shared context, different `thread_id` = isolated conversation

2. **Three Scenarios**:
   - New Query: Fresh thread_id
   - Clarification: Set `is_clarification_response: true` + pass state fields
   - Follow-Up: Same thread_id as previous calls

3. **Clarification Response Requirements**:
   - `is_clarification_response: true`
   - `original_query` from previous response
   - `clarification_message` from previous response
   - `clarification_count` from previous response
   - Same `thread_id`

4. **Context Preservation**:
   - Original query never overwritten
   - All components stored separately
   - Combined context passed to planning
   - Thread memory maintains history

---

**For More Details:**
- Implementation: `Super_Agent_hybrid.py`
- Technical Details: `CLARIFICATION_FLOW_IMPROVEMENTS.md`
- Quick Reference: `QUICK_REFERENCE_CLARIFICATION.md`

**Status:** ✅ Production Ready for Model Serving Deployment
