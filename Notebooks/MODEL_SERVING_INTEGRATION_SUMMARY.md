# Model Serving Integration Summary

**Date:** January 17, 2026  
**Status:** ✅ Complete and Production Ready

---

## Overview

This document explains how the local helper functions (`respond_to_clarification()` and `ask_follow_up_query()`) relate to the deployed `SuperAgentHybridResponsesAgent` on Databricks Model Serving.

---

## Architecture: Local vs. Deployed

### Local Testing Functions (Python Helpers)

**Location:** `Super_Agent_hybrid.py` (lines 1785-2060)

These functions provide a convenient Python interface for local testing:

```python
# Helper Function 1: invoke_super_agent_hybrid()
def invoke_super_agent_hybrid(query: str, thread_id: str = "default"):
    """Start new conversation - local testing"""
    # Directly invokes the LangGraph workflow
    # Uses MemorySaver with thread_id
    return super_agent_hybrid.invoke(initial_state, config)

# Helper Function 2: respond_to_clarification()
def respond_to_clarification(
    clarification_response: str,
    previous_state: Dict[str, Any],
    thread_id: str = "default"
):
    """Answer clarification - local testing"""
    # Preserves state from previous_state
    # Re-invokes workflow with clarification response
    return super_agent_hybrid.invoke(new_state, config)

# Helper Function 3: ask_follow_up_query()
def ask_follow_up_query(new_query: str, thread_id: str = "default"):
    """Follow-up query - local testing"""
    # Thread memory restores context
    # Invokes workflow with new query
    return super_agent_hybrid.invoke(new_state, config)
```

**Purpose:** Simplify local testing and development

---

### Deployed Agent (Model Serving)

**Location:** `Super_Agent_hybrid.py` (lines 1708-1833)

**Class:** `SuperAgentHybridResponsesAgent(ResponsesAgent)`

This class wraps the LangGraph workflow for MLflow Model Serving deployment:

```python
class SuperAgentHybridResponsesAgent(ResponsesAgent):
    def __init__(self, agent: CompiledStateGraph):
        self.agent = agent
    
    def predict(self, request: ResponsesAgentRequest):
        """Non-streaming predictions"""
        # Collects all stream events and returns final output
        ...
    
    def predict_stream(self, request: ResponsesAgentRequest):
        """Streaming predictions - MAIN ENTRY POINT for Model Serving"""
        # Handles three scenarios:
        # 1. New Query
        # 2. Clarification Response  
        # 3. Follow-Up Query
        ...
```

**Purpose:** Production deployment via Databricks Model Serving

---

## How They Work Together

### Mapping: Local Functions → Model Serving API

| Local Function | Model Serving Equivalent | Request Structure |
|----------------|-------------------------|-------------------|
| `invoke_super_agent_hybrid(query, thread_id)` | POST `/invocations` with new query | `{"messages": [...], "custom_inputs": {"thread_id": "..."}}` |
| `respond_to_clarification(response, state, thread_id)` | POST `/invocations` with clarification | `{"messages": [...], "custom_inputs": {"thread_id": "...", "is_clarification_response": true, ...}}` |
| `ask_follow_up_query(query, thread_id)` | POST `/invocations` with follow-up | `{"messages": [...], "custom_inputs": {"thread_id": "..."}}` (same thread_id) |

---

## Implementation Details

### Local Helper Functions

#### Purpose
- **Development & Testing**: Easy to use Python functions for notebook testing
- **State Management**: Automatically handle state preservation between calls
- **Type Safety**: Python types and IDE support
- **Debugging**: Direct access to full state dict

#### How They Work

```python
# Example: Local testing
thread = "test_session_001"

# Call 1: New query
state1 = invoke_super_agent_hybrid("Show data", thread_id=thread)

# Call 2: Clarification (if needed)
if not state1['question_clear']:
    state2 = respond_to_clarification(
        "Patient count",
        previous_state=state1,  # Helper preserves state automatically
        thread_id=thread
    )

# Call 3: Follow-up
state3 = ask_follow_up_query("By age group", thread_id=thread)
```

**Key Point**: Helper functions handle state preservation internally, making local testing simple.

---

### Model Serving Agent

#### Purpose
- **Production Deployment**: REST API endpoint for client applications
- **Scalability**: Auto-scaling, load balancing, monitoring
- **Standard Interface**: ResponsesAgent compatible with OpenAI API format
- **Multi-User**: Handles concurrent requests from multiple users

#### How It Works

```python
def predict_stream(self, request: ResponsesAgentRequest):
    """
    Main entry point when endpoint receives HTTP POST /invocations
    """
    # Extract parameters from request
    messages = request.input  # User messages
    thread_id = request.custom_inputs.get("thread_id", "default")
    is_clarification = request.custom_inputs.get("is_clarification_response", False)
    
    # Build initial state based on scenario
    if is_clarification:
        # Scenario 2: Clarification Response
        # Caller must pass original_query, clarification_message, clarification_count
        initial_state = {
            "original_query": request.custom_inputs.get("original_query"),
            "clarification_message": request.custom_inputs.get("clarification_message"),
            "clarification_count": request.custom_inputs.get("clarification_count"),
            "user_clarification_response": latest_query,
            ...
        }
    else:
        # Scenario 1 & 3: New Query or Follow-Up
        initial_state = {
            "original_query": latest_query,
            ...
        }
    
    # Invoke with thread memory
    config = {"configurable": {"thread_id": thread_id}}
    for _, events in self.agent.stream(initial_state, config, stream_mode=["updates"]):
        # Stream results back to client
        ...
```

**Key Point**: Caller must explicitly pass state fields for clarification responses (no automatic preservation like local helpers).

---

## Critical Difference: State Passing

### Local Helpers (Automatic State Preservation)

```python
# ✅ Easy: Helper automatically extracts and passes state
state1 = invoke_super_agent_hybrid("Show data", thread_id="session")

if not state1['question_clear']:
    state2 = respond_to_clarification(
        "Patient count",
        previous_state=state1,  # ← Function extracts state internally
        thread_id="session"
    )
```

The `respond_to_clarification()` function **automatically** extracts:
- `original_query` from `previous_state['original_query']`
- `clarification_message` from `previous_state['clarification_message']`
- `clarification_count` from `previous_state['clarification_count']`

---

### Model Serving (Explicit State Passing Required)

```python
# ❌ Client MUST manually pass state fields
response1 = requests.post(endpoint, json={
    "messages": [{"role": "user", "content": "Show data"}],
    "custom_inputs": {"thread_id": "session"}
})

if not response1.json()["custom_outputs"].get("question_clear", True):
    # Client must extract from custom_outputs and pass back
    custom_outputs = response1.json()["custom_outputs"]
    
    response2 = requests.post(endpoint, json={
        "messages": [{"role": "user", "content": "Patient count"}],
        "custom_inputs": {
            "thread_id": "session",
            "is_clarification_response": True,  # ← Required
            "original_query": custom_outputs["original_query"],  # ← Required
            "clarification_message": custom_outputs["clarification_message"],  # ← Required
            "clarification_count": custom_outputs["clarification_count"]  # ← Required
        }
    })
```

**Why This Difference?**

1. **Stateless HTTP**: Model Serving is stateless - each request is independent
2. **No Previous State Access**: The endpoint cannot access `previous_state` from Python
3. **Custom Outputs**: Agent returns state in `custom_outputs` for client to preserve
4. **Client Responsibility**: Client must track and pass back required state fields

---

## Client Implementation Responsibilities

When building a client to call the Model Serving endpoint:

### ✅ Client Must Implement

1. **State Tracking**: Store `custom_outputs` from each response
2. **State Passing**: Pass required fields back for clarification responses
3. **Thread Management**: Generate and track thread_ids per conversation
4. **Scenario Detection**: Detect when clarification is needed from `custom_outputs.question_clear`

### Example Client Implementation

```python
class AgentClient:
    def __init__(self, endpoint, token):
        self.endpoint = endpoint
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def send_query(self, query, thread_id):
        """Send new query or follow-up"""
        response = requests.post(self.endpoint, headers=self.headers, json={
            "messages": [{"role": "user", "content": query}],
            "custom_inputs": {"thread_id": thread_id}
        })
        return response.json()
    
    def send_clarification(self, clarification, thread_id, previous_response):
        """Send clarification response"""
        custom_outputs = previous_response["custom_outputs"]
        
        response = requests.post(self.endpoint, headers=self.headers, json={
            "messages": [{"role": "user", "content": clarification}],
            "custom_inputs": {
                "thread_id": thread_id,
                "is_clarification_response": True,
                "original_query": custom_outputs["original_query"],
                "clarification_message": custom_outputs["clarification_message"],
                "clarification_count": custom_outputs["clarification_count"]
            }
        })
        return response.json()

# Usage
client = AgentClient(endpoint="...", token="...")
thread = "user_alice_001"

# Query 1
response1 = client.send_query("Show data", thread)

# Clarification (if needed)
if not response1["custom_outputs"].get("question_clear", True):
    response2 = client.send_clarification("Patient count", thread, response1)

# Follow-up
response3 = client.send_query("By age group", thread)
```

---

## What Gets Deployed to Model Serving

When you log and deploy the agent to Model Serving:

### ✅ Deployed Components

```python
# The deployable agent instance
AGENT = SuperAgentHybridResponsesAgent(super_agent_hybrid)

# MLflow logging
mlflow.pyfunc.log_model(
    artifact_path="agent",
    python_model=AGENT,
    ...
)
```

**What's Included:**
- `SuperAgentHybridResponsesAgent` class with `predict()` and `predict_stream()` methods
- Compiled LangGraph workflow (`super_agent_hybrid`)
- MemorySaver checkpoint for thread-based memory
- All agent classes (ClarificationAgent, PlanningAgent, etc.)
- All dependencies (databricks-langchain, langgraph, etc.)

### ❌ NOT Deployed (Local Testing Only)

- `invoke_super_agent_hybrid()` helper function
- `respond_to_clarification()` helper function
- `ask_follow_up_query()` helper function
- Test cases and examples at end of notebook

**Why?** These are Python convenience functions for local testing. Model Serving only exposes the `ResponsesAgent` interface (`predict` and `predict_stream` methods).

---

## Testing Strategy

### Phase 1: Local Testing (Use Helper Functions)

```python
# Use convenient Python helpers for development
thread = "dev_test_001"

# Test new query
state1 = invoke_super_agent_hybrid("How many patients?", thread)
display_results(state1)

# Test clarification
if not state1['question_clear']:
    state2 = respond_to_clarification("Over 50", state1, thread)
    display_results(state2)

# Test follow-up
state3 = ask_follow_up_query("By gender", thread)
display_results(state3)
```

### Phase 2: Pre-Deployment Testing (Simulate HTTP)

```python
# Test ResponsesAgent directly to simulate Model Serving
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentInput

request = ResponsesAgentRequest(
    input=[ResponsesAgentInput(role="user", content="Show patient data")],
    custom_inputs={"thread_id": "test_002"}
)

# This is what Model Serving will call
response = AGENT.predict(request)
print(response.output[0].text)
```

### Phase 3: Post-Deployment Testing (HTTP Calls)

```python
# Test deployed endpoint with HTTP requests
import requests

endpoint = "https://your-workspace.databricks.com/serving-endpoints/..."
response = requests.post(endpoint, headers=headers, json={
    "messages": [{"role": "user", "content": "Show patient data"}],
    "custom_inputs": {"thread_id": "prod_test_001"}
})
```

---

## Deployment Checklist

### Before Deployment

- [x] `SuperAgentHybridResponsesAgent` class implemented
- [x] `predict()` method implemented
- [x] `predict_stream()` method implemented with all three scenarios
- [x] Thread memory (MemorySaver) configured
- [x] All state fields properly handled
- [x] Clarification response detection implemented
- [x] Follow-up query support via thread_id
- [x] Local testing with helper functions passed
- [x] Pre-deployment testing with ResponsesAgent.predict() passed

### Deployment Steps

```python
# 1. Log model to MLflow
import mlflow

with mlflow.start_run():
    mlflow.pyfunc.log_model(
        artifact_path="super_agent_hybrid",
        python_model=AGENT,
        registered_model_name=f"{CATALOG}.{SCHEMA}.super_agent_hybrid",
        input_example={
            "messages": [{"role": "user", "content": "How many patients?"}],
            "custom_inputs": {"thread_id": "example_thread"}
        }
    )

# 2. Register to Unity Catalog
model_uri = f"runs:/{run_id}/super_agent_hybrid"
mlflow.register_model(model_uri, f"{CATALOG}.{SCHEMA}.super_agent_hybrid")

# 3. Create Model Serving endpoint
from mlflow.deployments import get_deploy_client

client = get_deploy_client("databricks")
endpoint = client.create_endpoint(
    name="super-agent-hybrid",
    config={
        "served_entities": [{
            "name": "super-agent-hybrid-entity",
            "entity_name": f"{CATALOG}.{SCHEMA}.super_agent_hybrid",
            "entity_version": "1",
            "workload_size": "Small",
            "scale_to_zero_enabled": True,
        }]
    }
)
```

### After Deployment

- [ ] Test new query scenario
- [ ] Test clarification response scenario
- [ ] Test follow-up query scenario
- [ ] Test thread isolation (multiple concurrent threads)
- [ ] Monitor latency and success rates
- [ ] Document endpoint URL and authentication for clients

---

## Summary

### Key Points

1. **Local Helpers = Development Convenience**
   - Python functions for easy local testing
   - Automatic state preservation
   - Direct state dict access

2. **Model Serving = Production Deployment**
   - REST API endpoint for clients
   - Explicit state passing required (via `custom_inputs`)
   - Standard ResponsesAgent interface

3. **Both Use Same Core Logic**
   - Same LangGraph workflow
   - Same agent classes
   - Same clarification flow with context preservation
   - Same thread-based memory system

4. **Client Responsibility**
   - Track conversation state
   - Pass required fields for clarification responses
   - Manage thread_ids properly
   - Detect scenarios from `custom_outputs`

### Architecture Flow

```
Local Development:
User → invoke_super_agent_hybrid() → LangGraph Workflow → MemorySaver → Result
                ↓ (automatic state extraction)
User → respond_to_clarification() → LangGraph Workflow → MemorySaver → Result
                ↓ (automatic state extraction)
User → ask_follow_up_query() → LangGraph Workflow → MemorySaver → Result

Production (Model Serving):
Client → HTTP POST /invocations → SuperAgentHybridResponsesAgent.predict_stream()
            ↓ (custom_inputs)
        LangGraph Workflow → MemorySaver → HTTP Response (custom_outputs)
            ↓ (client extracts and passes back)
Client → HTTP POST /invocations (clarification) → predict_stream()
            ↓ (same thread_id)
        LangGraph Workflow → MemorySaver → HTTP Response
```

### Documentation Files

- **`Super_Agent_hybrid.py`**: Complete implementation (local helpers + deployed agent)
- **`CLARIFICATION_FLOW_IMPROVEMENTS.md`**: Technical details of clarification flow
- **`MODEL_SERVING_API_GUIDE.md`**: Complete API guide for calling deployed endpoint
- **`QUICK_REFERENCE_CLARIFICATION.md`**: Quick usage examples
- **`MODEL_SERVING_INTEGRATION_SUMMARY.md`** (this file): How local and deployed relate

---

**Status:** ✅ Production Ready

Both local testing (via Python helpers) and production deployment (via Model Serving) are fully implemented and documented.
