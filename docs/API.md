# API Reference

API documentation for the multi-agent system.

## Agent APIs

### SupervisorAgent

**Purpose**: Central orchestrator for all agent operations

```python
def supervisor_agent(state: AgentState) -> AgentState:
    """
    Routes requests to appropriate sub-agents.
    
    Args:
        state: Current agent state with messages and context
        
    Returns:
        Updated state with routing decisions
    """
```

### ThinkingPlanningAgent

**Purpose**: Query analysis and execution planning

```python
def thinking_planning_agent(state: AgentState) -> AgentState:
    """
    Analyzes query and plans execution strategy.
    
    Uses vector search to find relevant Genie spaces.
    Decides whether to use single agent, multiple agents, or SQL synthesis.
    
    Args:
        state: Current agent state
        
    Returns:
        State with relevant_spaces and execution plan
    """
```

### GenieAgent

**Purpose**: Query individual Genie spaces

```python
def genie_agent(state: AgentState, space_id: str) -> AgentState:
    """
    Queries a specific Genie space.
    
    Args:
        state: Current agent state
        space_id: Genie space ID to query
        
    Returns:
        State with query results
    """
```

### SQLSynthesisAgent

**Purpose**: Generate SQL across multiple tables

```python
def sql_synthesis_agent(state: AgentState) -> AgentState:
    """
    Synthesizes SQL query across multiple tables.
    
    Uses table metadata and samples to generate accurate JOINs.
    
    Args:
        state: Current agent state
        
    Returns:
        State with synthesized SQL query
    """
```

### SQLExecutionAgent

**Purpose**: Execute SQL queries

```python
def sql_execution_agent(state: AgentState) -> AgentState:
    """
    Executes SQL query via SQL Warehouse.
    
    Args:
        state: Current agent state with sql_query
        
    Returns:
        State with execution results
    """
```

### ClarificationAgent

**Purpose**: Handle ambiguous queries

```python
def clarification_agent(state: AgentState) -> AgentState:
    """
    Asks clarifying questions for ambiguous queries.
    
    Args:
        state: Current agent state
        
    Returns:
        State with clarification question
    """
```

### SummarizeAgent

**Purpose**: Final response formatting

```python
def summarize_agent(state: AgentState) -> AgentState:
    """
    Formats final response with reasoning.
    
    Args:
        state: Current agent state with results
        
    Returns:
        State with final_response
    """
```

## Configuration API

### get_config()

```python
from config import get_config

# Get configuration instance
config = get_config()

# Access configuration
catalog = config.unity_catalog.catalog_name
llm_endpoint = config.llm.endpoint_name

# Reload configuration (if .env changed)
config = get_config(reload=True)
```

### Configuration Classes

See [`config.py`](../config.py) for complete dataclass definitions:
- `DatabricksConfig`: Workspace connection
- `UnityCatalogConfig`: Catalog and schema
- `LLMConfig`: LLM endpoints per agent
- `VectorSearchConfig`: Vector search settings
- `TableMetadataConfig`: ETL and metadata settings
- `ModelServingConfig`: Deployment settings
- `LakebaseConfig`: State management settings

## Tools API

### Vector Search Tool

```python
from multi_agent.tools.vector_search import search_genie_spaces

# Search for relevant Genie spaces
results = search_genie_spaces(
    query="patient demographics",
    num_results=5
)
```

### Unity Catalog Functions

```python
from multi_agent.tools.uc_functions import (
    get_space_summary,
    get_table_overview,
    get_column_detail,
    get_space_details
)

# Get Genie space summary
summary = get_space_summary(space_id="...")

# Get table metadata
overview = get_table_overview(table_name="catalog.schema.table")

# Get column details
details = get_column_detail(table_name="...", column_name="...")
```

## Graph API

### create_agent_graph()

```python
from multi_agent.core.graph import create_agent_graph
from multi_agent.core.config import load_config_from_yaml

# Load configuration
config = load_config_from_yaml("config.yaml")

# Create agent graph
agent = create_agent_graph(config)

# Invoke agent
response = agent.invoke({
    "input": [{"role": "user", "content": "Show me patient data"}],
    "custom_inputs": {"thread_id": "conv-123"}
})
```

## State API

### AgentState

Complete state schema:

```python
class AgentState(TypedDict):
    # Input/Output
    messages: list[dict]  # Conversation messages
    final_response: Optional[str]  # Final answer
    
    # Planning
    relevant_spaces: Optional[list]  # Relevant Genie spaces
    execution_plan: Optional[str]  # How to execute query
    
    # SQL Synthesis
    sql_query: Optional[str]  # Generated SQL
    sql_results: Optional[dict]  # Execution results
    
    # Clarification
    needs_clarification: bool  # Whether query is ambiguous
    clarification_question: Optional[str]  # Question for user
    
    # Metadata
    conversation_id: str  # Conversation identifier
    user_id: str  # User identifier
    thread_id: str  # Thread identifier
```

## MLflow Integration

### Logging

```python
import mlflow

# MLflow automatically logs agent traces
with mlflow.start_run():
    response = agent.invoke(request)
    # Traces automatically captured
```

### Deployment

```python
logged_agent_info = mlflow.pyfunc.log_model(
    name="agent_name",
    python_model="./agent.py",
    code_paths=["../src/multi_agent"],
    model_config="../prod_config.yaml",
    resources=[...],
    pip_requirements=[...]
)
```

## Usage Examples

### Basic Query

```python
from multi_agent.core.graph import create_agent_graph

agent = create_agent_graph(config)

response = agent.invoke({
    "input": [{"role": "user", "content": "Show me patient demographics"}]
})

print(response["final_response"])
```

### Multi-Turn Conversation

```python
# First turn
response1 = agent.invoke({
    "input": [{"role": "user", "content": "Show me patients"}],
    "custom_inputs": {"thread_id": "conv-123"}
})

# Follow-up turn (uses same thread_id)
response2 = agent.invoke({
    "input": [
        {"role": "user", "content": "Show me patients"},
        {"role": "assistant", "content": response1["final_response"]},
        {"role": "user", "content": "What about their medications?"}
    ],
    "custom_inputs": {"thread_id": "conv-123"}
})
```

### Streaming Response

```python
# Stream response chunks
for chunk in agent.stream({
    "input": [{"role": "user", "content": "Show me patient data"}]
}):
    print(chunk)
```

## See Also

- [Architecture Overview](ARCHITECTURE.md)
- [Configuration Guide](CONFIGURATION.md)
- [Local Development](LOCAL_DEVELOPMENT.md)
- [Source Code Structure](../src/multi_agent/README.md)

---

**Note**: This API reference will be expanded as the codebase evolves. For latest API details, see source code and inline documentation.
