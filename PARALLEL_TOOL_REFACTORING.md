# Parallel Execution Tool Refactoring

## Summary
Refactored `_create_parallel_execution_tool()` method to follow LangChain best practices using `RunnableParallel` + `StructuredTool` pattern.

## Changes Made

### 1. Input Schema with Pydantic
**Before:** String-based JSON input
```python
def invoke_parallel_genie_agents(genie_route_plan: str) -> str:
    route_plan = json.loads(genie_route_plan)
```

**After:** Type-safe Pydantic schema
```python
class ParallelGenieInput(BaseModel):
    genie_route_plan: Dict[str, str] = Field(
        ..., 
        description="Dictionary mapping space_id to question"
    )

def invoke_parallel_genie_agents(args: ParallelGenieInput) -> Dict[str, Any]:
    route_plan = args.genie_route_plan
```

### 2. RunnableParallel Pattern
**Before:** Manual parallel execution with try/catch
```python
parallel_tasks = {}
for space_id in route_plan.keys():
    parallel_tasks[space_id] = RunnableLambda(...)
parallel_runner = RunnableParallel(**parallel_tasks)
results = parallel_runner.invoke(route_plan)
```

**After:** Composed chain with merge function
```python
# Build parallel tasks
parallel_tasks = {
    space_id: RunnableLambda(
        lambda inp, sid=space_id: self.parallel_executors[sid].invoke(
            GenieToolInput(question=inp[sid], conversation_id=None)
        )
    )
    for space_id, question in route_plan.items()
}

# Compose with merge function
parallel = RunnableParallel(**parallel_tasks)
composed = parallel | RunnableLambda(merge_genie_outputs)
results = composed.invoke(route_plan)
```

### 3. Merge Function
**Before:** Inline extraction logic
```python
extracted_results = {}
for space_id, result in results.items():
    extracted = {...}
    # ... extraction logic ...
    extracted_results[space_id] = extracted
```

**After:** Dedicated merge function
```python
def merge_genie_outputs(outputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge outputs from multiple Genie agents into a unified result.
    """
    merged_results = {}
    for space_id, result in outputs.items():
        extracted = {...}
        # Handle direct dict output from StructuredTool
        if isinstance(result, dict):
            extracted["answer"] = result.get("answer", "")
            extracted["sql"] = result.get("sql", "")
            # ...
        # Handle message-based output (fallback)
        elif isinstance(result, dict) and "messages" in result:
            # ... extract from messages ...
        merged_results[space_id] = extracted
    return merged_results
```

### 4. StructuredTool Instead of @tool Decorator
**Before:** Simple function decorator
```python
from langchain_core.tools import tool as langchain_tool

parallel_tool = langchain_tool(invoke_parallel_genie_agents)
parallel_tool.name = "invoke_parallel_genie_agents"
parallel_tool.description = """..."""
```

**After:** Explicit StructuredTool with schema
```python
from langchain.tools import StructuredTool

parallel_tool = StructuredTool(
    name="invoke_parallel_genie_agents",
    description="...",
    args_schema=ParallelGenieInput,
    func=invoke_parallel_genie_agents,
)
```

### 5. Return Type
**Before:** JSON string
```python
return json.dumps(extracted_results, indent=2)
```

**After:** Dictionary
```python
return results  # Dict[str, Any]
```

## Benefits

1. **Type Safety**: Pydantic schema provides compile-time and runtime type checking
2. **Better Tool Documentation**: LLM can understand the exact structure of inputs/outputs
3. **Cleaner Composition**: `parallel | RunnableLambda(merge)` pattern is more idiomatic
4. **Easier Testing**: Dict return values are easier to test than JSON strings
5. **Better Error Messages**: Pydantic validation provides clear error messages
6. **Follows LangChain Best Practices**: Aligns with official LangChain patterns

## Key Pattern Elements

### 1. RunnableParallel
```python
parallel = RunnableParallel(
    task1=lambda x: agent1.invoke(x["input1"]),
    task2=lambda x: agent2.invoke(x["input2"]),
)
```

### 2. Merge Function
```python
def merge_outputs(outputs: Dict[str, Any]) -> Dict[str, Any]:
    # Combine outputs from parallel tasks
    return combined_result
```

### 3. Composition
```python
composed = parallel | RunnableLambda(merge_outputs)
```

### 4. StructuredTool
```python
class InputSchema(BaseModel):
    field: str = Field(..., description="...")

tool = StructuredTool(
    name="tool_name",
    description="...",
    args_schema=InputSchema,
    func=lambda args: composed.invoke(args.field),
)
```

## Testing Recommendations

Test the refactored tool with:
1. Single space query
2. Multiple space queries (2-3 spaces)
3. Invalid space_id (error handling)
4. Empty route_plan (error handling)
5. Mixed success/failure results

Example test:
```python
tool_input = ParallelGenieInput(
    genie_route_plan={
        "space_id_1": "Get member demographics",
        "space_id_2": "Get benefit costs"
    }
)
result = parallel_tool.invoke(tool_input)
assert "space_id_1" in result
assert result["space_id_1"]["sql"]
```
