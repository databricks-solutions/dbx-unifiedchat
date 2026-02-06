# Architecture

Multi-Agent System for Cross-Domain Queries with Databricks Genie.

## System Overview

This system enables intelligent querying across multiple data domains (patients, medications, diagnoses, treatments, etc.) using a multi-agent architecture built with LangGraph.

## High-Level Architecture

```
User Query
    ↓
SupervisorAgent (orchestrates all agents)
    ↓
ThinkingPlanningAgent (analyzes & plans)
    ├── Vector Search (semantic retrieval)
    └── Genie Space Metadata
    ↓
    ├─ Single Space → GenieAgent
    ├─ Multiple Spaces (No Join) → Multiple GenieAgents → Verbal Merge
    └─ Multiple Spaces (Join) → Fast/Genie Route → SQLSynthesis → SQLExecution
    ↓
Response with reasoning
```

## Architecture Diagrams

Visual representations of the system architecture:

- [**Simple Architecture**](architecture/architecture_diagram_simple.svg) - High-level overview
- [**Detailed Architecture**](architecture/architecture_diagram.svg) - Complete system
- [**Clarification Flow**](architecture/clarification_flow_diagram.mmd) - Clarification handling

All diagrams are in the [`architecture/`](architecture/) directory in multiple formats (SVG, PNG, PDF, Mermaid).

## Core Components

### 1. SupervisorAgent

**Role**: Central orchestrator
**Responsibilities**:
- Routes requests to appropriate sub-agents
- Manages conversation state
- Handles final response formatting

### 2. ThinkingPlanningAgent

**Role**: Query analysis and planning
**Responsibilities**:
- Uses vector search to find relevant Genie spaces
- Analyzes query complexity
- Plans execution strategy (single space, multiple spaces, SQL synthesis)
- Handles clarification requests when query is ambiguous

**Tools**:
- Vector search over enriched Genie space metadata
- Unity Catalog functions for metadata retrieval

### 3. GenieAgent(s)

**Role**: Query individual Genie spaces
**Responsibilities**:
- Execute queries against specific Genie spaces
- Return structured results
- Handle Genie-specific errors

**Multiple instances**: One per Genie space being queried

### 4. SQLSynthesisAgent

**Role**: Combines SQL queries across tables/spaces
**Responsibilities**:
- Synthesizes JOIN queries across multiple tables
- Uses table metadata and samples for accurate SQL generation
- Validates SQL syntax

**Variants**:
- `SQLSynthesisTableAgent`: Direct table queries
- `SQLSynthesisGenieAgent`: Leverages Genie for SQL generation

### 5. SQLExecutionAgent

**Role**: Executes synthesized SQL
**Responsibilities**:
- Connects to SQL Warehouse
- Executes SQL queries
- Returns formatted results
- Handles execution errors

### 6. ClarificationAgent

**Role**: Handles ambiguous queries
**Responsibilities**:
- Detects when query needs clarification
- Asks clarifying questions
- Guides user to more specific query

### 7. SummarizeAgent

**Role**: Final response formatting
**Responsibilities**:
- Synthesizes results from multiple agents
- Provides clear, comprehensive answers
- Includes reasoning and sources

## Data Flow

### Scenario 1: Single Genie Space Query

```
User: "Show me patient demographics"
   ↓
ThinkingPlanningAgent
   ├─ Vector Search: Finds "patient_demographics" space
   └─ Decision: Single space query
   ↓
GenieAgent (patient_demographics space)
   └─ Query Genie space directly
   ↓
SummarizeAgent
   └─ Format response
   ↓
User: Response with patient demographics
```

### Scenario 2: Multi-Space Query (No Join)

```
User: "Show me patients and their medications"
   ↓
ThinkingPlanningAgent
   ├─ Vector Search: Finds "patients" AND "medications" spaces
   └─ Decision: Multiple spaces, no join needed
   ↓
GenieAgent (patients) + GenieAgent (medications)
   └─ Query both spaces in parallel
   ↓
SummarizeAgent
   └─ Verbal merge: "Here are patients... and their medications..."
   ↓
User: Combined response
```

### Scenario 3: Multi-Space Query (With Join)

```
User: "Show me patients with high blood pressure AND their medications"
   ↓
ThinkingPlanningAgent
   ├─ Vector Search: Finds "patients" AND "medications" spaces
   └─ Decision: Multiple spaces, JOIN required
   ↓
SQLSynthesisAgent
   ├─ Get table schemas
   ├─ Get sample data
   └─ Generate JOIN query
   ↓
SQLExecutionAgent
   └─ Execute SQL on SQL Warehouse
   ↓
SummarizeAgent
   └─ Format results with reasoning
   ↓
User: Synthesized response
```

### Scenario 4: Ambiguous Query

```
User: "Show me data"
   ↓
ThinkingPlanningAgent
   └─ Decision: Query too ambiguous
   ↓
ClarificationAgent
   └─ "What type of data are you interested in?"
   ↓
User: "Patient data"
   ↓
ThinkingPlanningAgent
   └─ Continue with Scenario 1
```

## State Management

### Short-Term Memory (CheckpointSaver)

Uses Lakebase PostgreSQL for conversation state:
- **Purpose**: Multi-turn conversations
- **Storage**: Conversation checkpoints
- **Lifetime**: Session-based
- **Implementation**: LangGraph CheckpointSaver with Lakebase backend

### Long-Term Memory (DatabricksStore)

Uses Lakebase with semantic search:
- **Purpose**: User preferences and past interactions
- **Storage**: Vector-indexed memories
- **Lifetime**: Persistent across sessions
- **Implementation**: DatabricksStore with embedding endpoint

### State Schema

```python
class AgentState(TypedDict):
    messages: list  # Conversation history
    relevant_spaces: list  # Genie spaces for current query
    sql_query: Optional[str]  # Generated SQL
    sql_results: Optional[dict]  # Execution results
    final_response: Optional[str]  # Final answer
    # ... more fields
```

## Technology Stack

### Core Framework
- **LangGraph**: Agent orchestration and workflow
- **LangChain**: Agent tools and integrations
- **Databricks**: Platform and services

### Databricks Services
- **Genie**: Natural language to SQL
- **Vector Search**: Semantic metadata retrieval
- **Unity Catalog**: Data governance
- **Model Serving**: Agent deployment
- **Lakebase**: State management (PostgreSQL)
- **MLflow**: Model packaging and tracking

### Models
- **Claude Sonnet 4.5**: Planning, SQL synthesis (high accuracy)
- **Claude Haiku 4.5**: Clarification, execution, summarization (speed)
- **GTE-Large-EN**: Text embeddings for vector search

## Deployment Architecture

```
┌─────────────────────────────────────────────────────┐
│              Databricks Model Serving                │
│  ┌───────────────────────────────────────────────┐  │
│  │   Agent Container (Auto-scaled)              │  │
│  │   ├─ agent.py (MLflow wrapper)               │  │
│  │   ├─ src/multi_agent/ (packaged code)        │  │
│  │   └─ prod_config.yaml (runtime config)       │  │
│  └───────────────────────────────────────────────┘  │
│                     ↓                                │
│  ┌───────────────────────────────────────────────┐  │
│  │   Databricks Services                         │  │
│  │   ├─ Genie Spaces (data querying)            │  │
│  │   ├─ Vector Search (semantic retrieval)      │  │
│  │   ├─ SQL Warehouse (query execution)         │  │
│  │   ├─ Lakebase (state management)             │  │
│  │   ├─ Unity Catalog (metadata)                │  │
│  │   └─ LLM Endpoints (Claude models)           │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

## Security

### Authentication
- **Unified Auth**: Uses `databricks.sdk.Config()` for automatic authentication
- **Resource Logging**: All resources logged with MLflow for automatic access

### Authorization
- **Unity Catalog**: Table-level and row-level security
- **Genie Spaces**: Space-level access control
- **Model Serving**: Endpoint permissions

### Data Privacy
- **No data storage**: Agent doesn't store query results
- **Conversation state**: Stored in secure Lakebase
- **Audit logs**: All queries logged in inference tables

## Performance

### Latency
- **Simple queries**: 2-5 seconds
- **Complex queries**: 5-15 seconds (with SQL synthesis)
- **Cold start**: 1-2 minutes (with scale-to-zero enabled)

### Optimization
- **Parallel execution**: Multiple agents run in parallel when possible
- **Model diversification**: Fast models for simple tasks, smart models for complex
- **Vector search**: Quick metadata retrieval (<1 second)
- **Caching**: Configuration cached per request

## Design Decisions

### Why Multi-Agent?

- **Separation of concerns**: Each agent specializes in one task
- **Easier testing**: Test agents independently
- **Better observability**: Track each agent's performance
- **Flexible orchestration**: Easy to add/remove agents

### Why LangGraph?

- **State management**: Built-in state passing between agents
- **Conditional routing**: Dynamic agent selection based on query
- **Streaming support**: Real-time response streaming
- **Checkpointing**: Conversation state persistence

### Why Modular Code?

- **Single source of truth**: Same code for local dev and deployment
- **Easier maintenance**: Small, focused modules (<500 lines)
- **Better testing**: Test individual components
- **MLflow native**: `code_paths` parameter packages modules

## Scalability

The system scales in multiple dimensions:

1. **Genie Spaces**: Add more spaces by updating config
2. **Agents**: Add new agents by extending graph
3. **LLM Models**: Swap models per agent for cost/performance balance
4. **Workload**: Model Serving auto-scales based on traffic

## Extensibility

### Adding New Agent

1. Create agent in `src/multi_agent/agents/new_agent.py`
2. Register in `src/multi_agent/core/graph.py`
3. Add routing logic if needed
4. Test locally and in Databricks
5. Redeploy

### Adding New Tool

1. Create tool in `src/multi_agent/tools/new_tool.py`
2. Register with appropriate agent
3. Test tool independently
4. Integrate with agent workflow

### Supporting New Data Source

1. Add Genie space ID to config
2. Run ETL to enrich metadata
3. Rebuild vector search index
4. Agents automatically discover via vector search

## See Also

- [Deployment Guide](DEPLOYMENT.md)
- [Configuration Guide](CONFIGURATION.md)
- [API Reference](API.md)
- [Architecture Diagrams](architecture/)

---

**Want to understand the code?** See [../src/multi_agent/README.md](../src/multi_agent/README.md) for code structure guide! 💡
