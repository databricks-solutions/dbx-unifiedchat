# Multi-Agent Source Code

This directory contains all agent logic in a modular structure.

## 🎯 Purpose

This is the **single source of truth** for agent code, used by:
- ✅ Local development (`config.py` + `.env`)
- ✅ Databricks testing (`dev_config.yaml`)
- ✅ Production deployment (`prod_config.yaml`)

**All three workflows use the same code from this directory!**

## 📂 Directory Structure

```
src/multi_agent/
├── __init__.py          # Package exports
├── main.py              # CLI entry point (local dev)
├── agents/              # Agent implementations
│   ├── __init__.py
│   ├── clarification.py              # Clarification node + helpers
│   ├── planning.py                   # Planning node + helpers
│   ├── planning_agent.py             # PlanningAgent class
│   ├── sql_synthesis.py              # SQL synthesis nodes
│   ├── sql_synthesis_agents.py       # SQL synthesis agent classes
│   ├── sql_execution.py              # SQL execution node
│   ├── sql_execution_agent.py        # SQLExecutionAgent class
│   ├── summarize.py                  # Summarization node
│   └── summarize_agent.py            # ResultSummarizeAgent class
├── core/                # Core infrastructure
│   ├── __init__.py
│   ├── config.py                     # Configuration management
│   ├── state.py                      # State TypedDicts and helpers
│   └── graph.py                      # LangGraph workflow
├── tools/               # Agent tools
│   ├── __init__.py
│   └── uc_functions.py               # Unity Catalog functions
└── utils/               # Utilities
    ├── __init__.py
    ├── conversation.py               # Conversation management
    └── intent_detection_service.py   # Intent detection
```

## 🛠️ For Developers

### Adding a New Agent

1. **Create agent file** in `agents/`: `agents/my_new_agent.py`

```python
"""My new agent implementation."""
from typing import Dict, Any
from ..core.state import AgentState

def my_new_agent_node(state: AgentState) -> Dict[str, Any]:
    """
    My new agent node function.
    
    Args:
        state: Current agent state
        
    Returns:
        State updates
    """
    # Your agent logic here
    return {
        "messages": [{"role": "assistant", "content": "response"}],
        "next_agent": "summarize"
    }
```

2. **Register in graph** (`core/graph.py`):

```python
from ..agents.my_new_agent import my_new_agent_node

# In create_super_agent_hybrid():
workflow.add_node("my_new_agent", my_new_agent_node)
workflow.add_edge("some_node", "my_new_agent")
```

3. **Add tests** (`tests/unit/test_my_new_agent.py`):

```python
import pytest
from src.multi_agent.agents.my_new_agent import my_new_agent_node

def test_my_new_agent():
    state = {"messages": []}
    result = my_new_agent_node(state)
    assert "messages" in result
```

4. **Test locally**:
```bash
pytest tests/unit/test_my_new_agent.py -v
python -m src.multi_agent.main --query "test new agent"
```

### Modifying Existing Agent

1. **Edit the agent file** (e.g., `agents/supervisor.py`)
2. **Run agent-specific tests**: `pytest tests/unit/test_supervisor.py -v`
3. **Run full test suite**: `pytest tests/`
4. **Test locally**: `python -m src.multi_agent.main --query "test"`
5. **Test in Databricks**: `notebooks/test_agent_databricks.py`
6. **Deploy**: `notebooks/deploy_agent.py`

### Working with Agent Classes vs Node Functions

The codebase has two patterns:

**Agent Classes** (e.g., `PlanningAgent`):
- Encapsulate agent logic and state
- Can be instantiated and reused
- Good for complex agents with multiple methods

**Agent Node Functions** (e.g., `planning_node`):
- Wrapper functions that create/call agent classes
- Used by LangGraph for node registration
- Handle state extraction and updates

**Pattern**:
```python
# Agent class
class PlanningAgent:
    def __init__(self, llm, vector_search_index):
        self.llm = llm
        self.vector_search_index = vector_search_index
    
    def plan(self, query):
        # Complex planning logic
        pass

# Node function (used by graph)
def planning_node(state: AgentState) -> dict:
    # Extract context
    query = state["current_turn"]["query"]
    
    # Get or create agent instance
    agent = get_cached_planning_agent()
    
    # Call agent
    result = agent.plan(query)
    
    # Return state updates
    return {"plan": result}
```

### Code Style

Follow these guidelines:

```bash
# Format code
black src/multi_agent/
isort src/multi_agent/

# Lint
flake8 src/multi_agent/

# Type check
mypy src/multi_agent/
```

**Standards**:
- PEP 8 compliance
- Type hints on all functions
- Docstrings (Google style)
- Keep files <500 lines
- Meaningful variable names
- Clear comments for complex logic

## 🔄 Three Workflows Using This Code

### Workflow 1: Local Development

```bash
# Uses config.py + .env
python -m src.multi_agent.main --query "test"
```

**Configuration**: `.env` file
**Purpose**: Fast iteration, unit testing

### Workflow 2: Databricks Testing

```python
# Uses dev_config.yaml
# In notebooks/test_agent_databricks.py
import sys
sys.path.insert(0, "../src")
from multi_agent.core.graph import create_agent_graph
```

**Configuration**: `dev_config.yaml`
**Purpose**: Test with real Databricks services

### Workflow 3: Production Deployment

```python
# Uses prod_config.yaml
# In notebooks/deploy_agent.py
mlflow.pyfunc.log_model(
    python_model="./agent.py",
    code_paths=["../src/multi_agent"],  # Packages this code!
    model_config="../prod_config.yaml"
)
```

**Configuration**: `prod_config.yaml`
**Purpose**: Production deployment to Model Serving

## 📖 Key Files

| File | Purpose | When to Edit |
|------|---------|--------------|
| `agents/*.py` | Agent implementations | Adding/modifying agents |
| `core/state.py` | State definitions | Changing state schema |
| `core/graph.py` | Workflow graph | Changing agent flow |
| `core/config.py` | Configuration | Adding config values |
| `tools/*.py` | Agent tools | Adding new tools |
| `utils/*.py` | Utilities | Adding helpers |
| `main.py` | CLI entry point | Changing CLI behavior |

## 🧪 Testing

```bash
# Test imports work
python -c "from src.multi_agent import *; print('✓ Imports OK')"

# Run unit tests
pytest tests/unit/ -v

# Test specific agent
pytest tests/unit/test_planning.py -v

# Test locally
python -m src.multi_agent.main --query "test" --verbose
```

## 📚 Documentation

- [Local Development Guide](../../docs/LOCAL_DEVELOPMENT.md) - Complete local dev workflow
- [Architecture](../../docs/ARCHITECTURE.md) - System design
- [API Reference](../../docs/API.md) - API documentation
- [Configuration](../../docs/CONFIGURATION.md) - Config systems

## 🤝 Contributing

When contributing:

1. **Follow the structure**: Keep code organized by type (agents, core, tools, utils)
2. **Keep files small**: Target <500 lines per file
3. **Add tests**: Every new feature needs tests
4. **Update docs**: Update relevant README files
5. **Test all three workflows**: Ensure code works locally, in Databricks, and deploys correctly

See [../../CONTRIBUTING.md](../../CONTRIBUTING.md) for complete guidelines.

---

**This is the heart of the system!** All agent logic lives here. 💡
