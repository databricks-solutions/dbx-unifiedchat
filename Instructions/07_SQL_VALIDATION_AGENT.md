# SQL Validation Agent Design and Integration Guide

## Overview

The SQL Validation Agent is a specialized component in the multi-agent system that validates SQL queries before execution. It ensures that all referenced tables and columns exist in the metadata, preventing runtime errors and providing actionable feedback for corrections.

## Position in Multi-Agent Workflow

```
┌─────────────────────┐
│    User Query       │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ Clarification Agent │ ← Validates query clarity
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Planning Agent     │ ← Creates execution plan
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ SQL Synthesis Agent │ ← Generates SQL query
└──────────┬──────────┘
           ↓
┌─────────────────────────────────┐
│ **SQL Validation Agent** ✨     │ ← YOU ARE HERE
│  - Parse SQL                    │
│  - Check tables exist           │
│  - Check columns exist          │
│  - Report errors/suggestions    │
└──────────┬──────────────────────┘
           ↓
    ┌──────┴──────┐
    │             │
  Valid?       Invalid?
    │             │
    ↓             ↓
┌─────────┐  ┌──────────────┐
│ Execute │  │ Return to    │
│   SQL   │  │ Synthesis    │
└─────────┘  └──────────────┘
```

## Architecture Components

### 1. SQL Parsing Utilities

Three core parsing functions extract SQL components:

#### `extract_tables_from_sql(sql_query: str) -> List[str]`
- Extracts table names from FROM and JOIN clauses
- Handles 3-level names: `catalog.schema.table`
- Handles 2-level names: `schema.table`
- Handles 1-level names: `table`
- Removes aliases automatically

**Example:**
```python
sql = "SELECT * FROM yyang.multi_agent_genie.medical_claim mc JOIN diagnosis d ON mc.claim_id = d.claim_id"
tables = extract_tables_from_sql(sql)
# Returns: ['yyang.multi_agent_genie.medical_claim', 'diagnosis']
```

#### `extract_columns_from_sql(sql_query: str) -> Dict[str, List[str]]`
- Extracts column references with table qualifiers
- Format: `table_alias.column_name` or `table_name.column_name`
- Groups columns by table/alias
- Filters out SQL keywords

**Example:**
```python
sql = "SELECT mc.allowed_amount, d.icd10_code FROM medical_claim mc"
columns = extract_columns_from_sql(sql)
# Returns: {'mc': ['allowed_amount'], 'd': ['icd10_code']}
```

#### `extract_table_aliases(sql_query: str) -> Dict[str, str]`
- Extracts alias-to-table mappings
- Handles both explicit (AS) and implicit aliases

**Example:**
```python
sql = "FROM medical_claim AS mc JOIN diagnosis d"
aliases = extract_table_aliases(sql)
# Returns: {'mc': 'medical_claim', 'd': 'diagnosis'}
```

### 2. Validation Logic

#### `validate_sql_query(sql_query: str, uc_functions_available: bool) -> Dict[str, Any]`

Main validation orchestrator that:
1. Extracts tables, columns, and aliases
2. Resolves aliases to actual table names
3. Performs basic syntax checks
4. Structures validation results

**Return Structure:**
```json
{
    "is_valid": true/false,
    "sql_query": "SELECT ...",
    "validation_details": {
        "tables": {
            "extracted": ["table1", "table2"],
            "missing": [],
            "found": ["table1", "table2"]
        },
        "columns": {
            "extracted": {"table1": ["col1", "col2"]},
            "missing": {},
            "found": {"table1": ["col1", "col2"]}
        },
        "aliases": {"t1": "table1"}
    },
    "errors": [],
    "warnings": [],
    "suggestions": []
}
```

### 3. SQL Validation Agent (LangGraph Tool-Calling Agent)

**Configuration:**
- **LLM**: `databricks-claude-sonnet-4-5` (more powerful for complex validation)
- **Temperature**: `0.0` (deterministic validation results)
- **Tools**: Same 4 UC functions as SQL Synthesis Agent:
  - `get_space_summary`
  - `get_table_overview`
  - `get_column_detail`
  - `get_space_details`

**Agent Workflow:**
1. Parse SQL to extract tables and columns
2. For each table:
   - Call `get_table_overview` to verify existence
   - Record as error if not found
3. For each column:
   - Call `get_column_detail` for specific table/column
   - Record as error if not found
4. Compile validation results with specific errors and suggestions

**System Prompt Key Instructions:**
- Check all referenced tables exist
- Check all referenced columns exist in their tables
- Verify table joins use valid column names
- Basic SQL syntax validation
- Return structured JSON with errors and suggestions

### 4. Multi-Agent Wrapper Function

#### `validate_sql_with_agent(sql_query: str, context: Optional[Dict]) -> Dict[str, Any]`

**Purpose**: Interface for Super Agent to call validation

**Features:**
- Accepts raw SQL or agent response objects
- Extracts SQL from markdown code blocks
- Includes context from previous agents
- Returns structured validation results
- MLflow logging for tracing

**Usage Example:**
```python
# From SQL Synthesis Agent output
sql_result = sql_synthesis_agent.invoke(agent_message)

# Validate before execution
validation_result = validate_sql_with_agent(
    sql_query=sql_result,
    context={
        "relevant_space_ids": ["space_1", "space_2"],
        "execution_plan": "fast_route"
    }
)

if validation_result["is_valid"]:
    # Proceed to execution
    execute_sql_on_delta_tables(sql_query=sql_result)
else:
    # Return errors to user or re-synthesize
    print("Validation Errors:", validation_result["errors"])
    print("Suggestions:", validation_result["suggestions"])
```

## Validation Rules

### 1. Table Validation
✅ **Pass**: Table exists in metadata (any space)  
❌ **Fail**: Table not found in any space metadata

**Error Message Format:**
```
"Table 'catalog.schema.table_name' does not exist in metadata"
```

**Suggestion Format:**
```
"Did you mean 'catalog.schema.similar_table'? Use get_space_summary to find available tables."
```

### 2. Column Validation
✅ **Pass**: Column exists in the specified table  
❌ **Fail**: Column not found in table metadata

**Error Message Format:**
```
"Column 'column_name' does not exist in table 'table_name'"
```

**Suggestion Format:**
```
"Available columns in 'table_name': ['col1', 'col2', 'col3']. Did you mean 'col2'?"
```

### 3. Syntax Validation
✅ **Pass**: Query starts with SELECT, has FROM clause  
❌ **Fail**: Missing SELECT, FROM, or basic SQL structure

### 4. Alias Validation
⚠️ **Warning**: Ambiguous aliases that might cause confusion  
✅ **Pass**: All aliases properly resolved

## Integration with Super Agent

### Step 1: Add as InCodeSubAgent

```python
from databricks_langchain import (
    ChatDatabricks,
    create_langgraph_supervisor
)
from Notebooks.sql_validation_agent import validate_sql_with_agent

# Create validation sub-agent
sql_validation_subagent = InCodeSubAgent(
    func=validate_sql_with_agent,
    name="sql_validation_agent",
    description=(
        "Validates SQL queries before execution by checking if all referenced "
        "tables and columns exist in metadata. Returns validation results with "
        "specific errors and suggestions."
    )
)
```

### Step 2: Add to Supervisor Workflow

```python
supervisor = create_langgraph_supervisor(
    llm=llm,
    in_code_agents=[
        clarification_agent,
        planning_agent,
        sql_synthesis_agent,
        sql_validation_subagent,  # ← Add here
        sql_execution_agent
    ]
)
```

### Step 3: Add Conditional Routing Logic

```python
# In your supervisor routing logic
def should_execute_sql(state):
    """Determine if SQL should be executed based on validation"""
    last_agent = state.get("last_agent")
    
    if last_agent == "sql_validation_agent":
        validation_result = state.get("validation_result")
        
        if validation_result and validation_result.get("is_valid"):
            return "sql_execution_agent"  # Proceed to execution
        else:
            return "sql_synthesis_agent"  # Go back to synthesis with errors
    
    return None

# Add to supervisor
supervisor.add_conditional_edges(
    "sql_validation_agent",
    should_execute_sql,
    {
        "sql_execution_agent": "execute",
        "sql_synthesis_agent": "re_synthesize"
    }
)
```

## Error Handling and Recovery

### Scenario 1: Missing Table

**Validation Output:**
```json
{
    "is_valid": false,
    "errors": ["Table 'yyang.multi_agent_genie.patient_data' does not exist"],
    "suggestions": [
        "Available tables in space 'HealthVerityClaims': ['medical_claim', 'pharmacy_claim']",
        "Did you mean 'yyang.multi_agent_genie.enrollment'?"
    ]
}
```

**Recovery Action:**
- Return to SQL Synthesis Agent
- Include validation errors in context
- Re-synthesize SQL with correct table names

### Scenario 2: Missing Column

**Validation Output:**
```json
{
    "is_valid": false,
    "errors": ["Column 'cost_amount' does not exist in table 'medical_claim'"],
    "suggestions": [
        "Available columns: ['allowed_amount', 'paid_amount', 'charge_amount']",
        "Replace 'cost_amount' with 'allowed_amount'"
    ]
}
```

**Recovery Action:**
- Return to SQL Synthesis Agent with specific column error
- Agent adjusts SQL to use correct column name

### Scenario 3: Ambiguous Alias

**Validation Output:**
```json
{
    "is_valid": true,
    "warnings": ["Alias 'c' is ambiguous - could refer to 'claim' or 'coverage'"],
    "suggestions": ["Use more descriptive aliases like 'mc' for medical_claim"]
}
```

**Recovery Action:**
- Proceed with execution (warning doesn't block)
- Log warning for future improvement

## Testing the Validation Agent

### Test Case 1: Valid SQL
```python
test_sql = """
SELECT 
    mc.patient_id,
    mc.allowed_amount,
    d.icd10_code
FROM yyang.multi_agent_genie.medical_claim mc
JOIN yyang.multi_agent_genie.diagnosis d 
    ON mc.claim_id = d.claim_id
WHERE d.icd10_code LIKE 'E11%'
"""

result = validate_sql_with_agent(test_sql)
assert result["is_valid"] == True
assert len(result["errors"]) == 0
```

### Test Case 2: Invalid Table Name
```python
test_sql = """
SELECT patient_id, cost
FROM yyang.multi_agent_genie.patients_table
"""

result = validate_sql_with_agent(test_sql)
assert result["is_valid"] == False
assert "patients_table" in result["errors"][0]
assert len(result["suggestions"]) > 0
```

### Test Case 3: Invalid Column Name
```python
test_sql = """
SELECT patient_id, total_cost
FROM yyang.multi_agent_genie.medical_claim
"""

result = validate_sql_with_agent(test_sql)
assert result["is_valid"] == False
assert "total_cost" in result["errors"][0]
```

## Performance Considerations

### Token Efficiency
- **Parsing first**: Extract tables/columns before calling UC functions
- **Minimal queries**: Only query metadata for extracted entities
- **Hierarchical checking**: Check tables before columns
- **Caching**: Same as SQL Synthesis Agent (UC function results cached)

### Validation Speed
- **Parallel checks**: Agent can call multiple UC functions simultaneously
- **Early exit**: Stop on first critical error if configured
- **Smart ordering**: Check most likely issues first

### Error Recovery Time
- **Specific errors**: Detailed messages reduce re-synthesis iterations
- **Actionable suggestions**: Direct fixes minimize back-and-forth

## Benefits

### 1. Prevents Runtime Errors
❌ **Before**: SQL execution fails with cryptic database errors  
✅ **After**: Validation catches issues before execution

### 2. Actionable Feedback
❌ **Before**: "Column not found"  
✅ **After**: "Column 'cost_amount' not found. Available: ['allowed_amount', 'paid_amount']. Use 'allowed_amount'."

### 3. Faster Iteration
- Validation is much faster than execution
- Specific suggestions reduce re-synthesis cycles
- Users get immediate feedback

### 4. Better User Experience
- Clear error messages user can understand
- Suggestions help users refine their questions
- Transparent validation process

### 5. System Reliability
- Catches LLM hallucinations (made-up column names)
- Ensures metadata consistency
- Reduces failed queries in production

## Comparison with Other Approaches

### Approach 1: No Validation (Direct Execution)
❌ Runtime failures  
❌ Cryptic error messages  
❌ Wasted execution time  
❌ Poor user experience  

### Approach 2: Simple Regex Validation
⚠️ Basic syntax checking only  
⚠️ Cannot verify metadata existence  
⚠️ No actionable suggestions  
✅ Fast  

### Approach 3: SQL Validation Agent (This Implementation)
✅ Full metadata verification  
✅ Actionable error messages  
✅ Intelligent suggestions  
✅ Multi-agent compatible  
✅ MLflow tracked  
⚠️ Additional latency (minimal)  

## Future Enhancements

### 1. Semantic Validation
- Check if JOINs make logical sense
- Validate aggregation functions
- Verify GROUP BY clauses match SELECT

### 2. Performance Optimization
- Batch multiple table/column checks
- Cache validation results
- Pre-compute common validation patterns

### 3. Auto-Correction
- Automatically fix simple errors
- Suggest and apply column name corrections
- Handle common typos

### 4. Query Optimization Hints
- Suggest adding indexes
- Recommend query rewrites for performance
- Warn about expensive operations

### 5. Security Validation
- Check user permissions
- Validate data access policies
- Ensure row-level security compliance

## Conclusion

The SQL Validation Agent is a critical component in the multi-agent system that:

1. **Prevents errors** before they happen
2. **Provides actionable feedback** for quick fixes
3. **Integrates seamlessly** with the multi-agent workflow
4. **Improves user experience** with clear error messages
5. **Increases system reliability** by catching LLM mistakes

By validating SQL queries before execution, we ensure a more robust, user-friendly, and efficient multi-agent system.

## Quick Start

### 1. Install Dependencies
```bash
%pip install databricks-langchain databricks-vectorsearch
```

### 2. Run the Agent Notebook
```python
# In Databricks
%run /Notebooks/sql_validation_agent
```

### 3. Test Validation
```python
# Test with a sample SQL query
test_sql = "SELECT patient_id FROM medical_claim"
result = validate_sql_with_agent(test_sql)
print(json.dumps(result, indent=2))
```

### 4. Integrate with Super Agent
See "Integration with Super Agent" section above for detailed steps.

---

**Created**: January 2026  
**Author**: Multi-Agent System Development Team  
**Version**: 1.0  
**Status**: Ready for Integration
