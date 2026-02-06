# Local Development Guide

Complete guide for developing the multi-agent system locally.

## Prerequisites

- Python 3.10 or above
- Git
- Databricks workspace access (for testing against real services)
- Virtual environment tool (`venv` or similar)

## Quick Setup (5-10 minutes)

```bash
# 1. Clone the repository
git clone <repo-url>
cd KUMC_POC_hlsfieldtemp  # Or multi-agent-genie after rename

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # For testing

# 4. Configure environment
cp .env.example .env
# Edit .env with your Databricks credentials and configuration
# See "Configuration" section below

# 5. Verify installation
python -c "from src.multi_agent import *; print('✓ Installation successful')"

# 6. Run tests to verify setup
pytest tests/unit/          # Fast unit tests

# 7. Run the agent locally
python -m src.multi_agent.main --query "Show me patient data"
```

## Configuration Setup

Edit `.env` file with your credentials:

```bash
# Databricks Connection
DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
DATABRICKS_TOKEN=your-personal-access-token

# Unity Catalog
CATALOG_NAME=your_catalog
SCHEMA_NAME=your_schema

# LLM Endpoints (use your workspace endpoints)
LLM_ENDPOINT=databricks-claude-sonnet-4-5
LLM_ENDPOINT_CLARIFICATION=databricks-claude-haiku-4-5
LLM_ENDPOINT_PLANNING=databricks-claude-sonnet-4-5
# ... (see .env.example for all endpoints)

# Genie Configuration
GENIE_SPACE_IDS=space_id_1,space_id_2,space_id_3
SQL_WAREHOUSE_ID=your_warehouse_id

# Vector Search
VS_ENDPOINT_NAME=genie_multi_agent_vs
EMBEDDING_MODEL=databricks-gte-large-en

# Lakebase (state management)
LAKEBASE_INSTANCE_NAME=your-lakebase-instance
```

**Get these values**:
- `DATABRICKS_TOKEN`: Settings → User Settings → Access Tokens
- `SQL_WAREHOUSE_ID`: SQL Warehouses → Your warehouse → Copy ID from URL
- `GENIE_SPACE_IDS`: Genie UI → Space settings → Copy space ID

## Development Workflow

### Standard Development Cycle

```bash
# 1. Create a feature branch
git checkout -b feature/my-new-feature

# 2. Edit code in src/multi_agent/
# Example: Modify an agent
vim src/multi_agent/agents/supervisor.py

# 3. Test your changes
pytest tests/unit/test_supervisor.py -v

# 4. Run agent locally to verify
python -m src.multi_agent.main --query "test query"

# 5. Run integration tests (requires Databricks)
pytest tests/integration/ -v

# 6. Commit changes
git add .
git commit -m "feat: Add new feature"

# 7. Push and create PR
git push origin feature/my-new-feature
```

## Key Files for Local Development

| File/Directory | Purpose | Edit Frequency |
|---------------|---------|----------------|
| `src/multi_agent/` | **All agent code** - main development area | ⭐ High |
| `.env` | Local configuration & secrets | Once (setup) |
| `config.py` | Config loader (rarely needs changes) | Low |
| `tests/` | Unit & integration tests | High |
| `src/multi_agent/main.py` | CLI entry point | Medium |

## Common Development Tasks

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/unit/test_agents.py -v

# Run specific test function
pytest tests/unit/test_agents.py::test_supervisor_agent -v

# Run with coverage
pytest --cov=src.multi_agent tests/

# Run only unit tests (fast)
pytest tests/unit/

# Run only integration tests (requires Databricks)
pytest tests/integration/
```

### Code Formatting

```bash
# Format code with Black
black src/ tests/

# Sort imports
isort src/ tests/

# Lint code
flake8 src/ tests/

# Type checking
mypy src/
```

### Running the Agent

```bash
# Basic query
python -m src.multi_agent.main --query "Show me patient demographics"

# With debug logging
DEBUG=1 python -m src.multi_agent.main --query "test"

# Interactive mode
python -m src.multi_agent.main --interactive

# With conversation ID (multi-turn)
python -m src.multi_agent.main --query "Follow up question" --thread-id conv-123
```

### Debugging

```bash
# Run with verbose logging
python -m src.multi_agent.main --query "test" --verbose

# Run with Python debugger
python -m pdb -m src.multi_agent.main --query "test"

# Use ipdb for better debugging
pip install ipdb
# Add breakpoint in code: import ipdb; ipdb.set_trace()
python -m src.multi_agent.main --query "test"
```

## What You DON'T Need for Local Dev

- ❌ `notebooks/deploy_agent.py` - Only for deployment
- ❌ `dev_config.yaml` / `prod_config.yaml` - Only for Databricks
- ❌ Databricks notebook environment
- ❌ Model Serving endpoint

## What You DO Need

- ✅ `src/multi_agent/` - All agent code
- ✅ `.env` - Your local configuration
- ✅ `config.py` - Config loader
- ✅ Python 3.10+ virtual environment
- ✅ Test data (in `tests/fixtures/`)

## Working with Agent Code

### Adding a New Agent

1. Create new file: `src/multi_agent/agents/my_new_agent.py`

```python
from langchain_core.messages import HumanMessage
from typing import TypedDict

def my_new_agent(state: dict) -> dict:
    """New agent implementation."""
    # Your agent logic here
    return {"messages": [HumanMessage(content="response")]}
```

2. Register in `src/multi_agent/core/graph.py`

```python
from multi_agent.agents.my_new_agent import my_new_agent

# Add to graph
graph.add_node("my_new_agent", my_new_agent)
```

3. Add tests: `tests/unit/test_my_new_agent.py`

4. Test locally:
```bash
pytest tests/unit/test_my_new_agent.py -v
python -m src.multi_agent.main --query "test new agent"
```

### Modifying Existing Agent

1. Edit the agent file (e.g., `src/multi_agent/agents/supervisor.py`)
2. Run agent-specific tests: `pytest tests/unit/test_supervisor.py -v`
3. Run full test suite: `pytest tests/`
4. Test locally: `python -m src.multi_agent.main --query "test"`

### Working with Configuration

The `config.py` file provides type-safe configuration classes:

```python
from config import get_config

# Get configuration
config = get_config()

# Access values
catalog = config.unity_catalog.catalog_name
llm_endpoint = config.llm.endpoint_name
warehouse_id = config.table_metadata.sql_warehouse_id

# Reload configuration (if .env changed)
config = get_config(reload=True)
```

## Testing in Databricks (Optional but Recommended)

After local development, test in Databricks before deploying:

```bash
# 1. Sync code to Databricks
databricks workspace import-dir src/multi_agent /Workspace/src/multi_agent --overwrite

# 2. Open notebooks/test_agent_databricks.py in Databricks
# 3. Run notebook to test with real services
```

See [../notebooks/README.md](../notebooks/README.md) for complete Databricks testing guide.

## Troubleshooting

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'multi_agent'`

**Solution**:
```bash
# Make sure you're in repo root
cd /path/to/KUMC_POC_hlsfieldtemp

# Run with proper module path
python -m src.multi_agent.main
```

### Configuration Errors

**Problem**: `ValueError: DATABRICKS_HOST must be set`

**Solution**:
- Check `.env` file exists
- Verify all required values are set
- Try reloading: `config = get_config(reload=True)`

### Databricks Connection Errors

**Problem**: `DatabricksError: Authentication failed`

**Solution**:
- Verify `DATABRICKS_TOKEN` is valid (not expired)
- Check `DATABRICKS_HOST` includes `https://`
- Test connection: `databricks workspace ls /`

### Test Failures

**Problem**: Integration tests failing

**Solution**:
- Make sure ETL pipeline has run successfully
- Verify all resources exist (tables, vector search index)
- Check Databricks services are accessible
- Try running only unit tests: `pytest tests/unit/`

## Best Practices

1. **Always use virtual environment**
   - Isolates dependencies
   - Prevents version conflicts

2. **Keep .env up to date**
   - Document any new variables in `.env.example`
   - Never commit `.env` to git

3. **Run tests before committing**
   - At minimum: `pytest tests/unit/`
   - Ideally: `pytest tests/`

4. **Format code before committing**
   ```bash
   black src/ tests/
   isort src/ tests/
   ```

5. **Test in Databricks before deploying**
   - Catches environment-specific issues
   - Validates with real services

## Next Steps

After setting up local development:

1. ✅ Run ETL pipeline if not already done ([etl/README.md](../etl/README.md))
2. ✅ Familiarize yourself with agent code ([src/multi_agent/README.md](../src/multi_agent/README.md))
3. ✅ Make your code changes
4. ✅ Test locally
5. ✅ Test in Databricks ([notebooks/README.md](../notebooks/README.md))
6. ✅ Deploy to production ([DEPLOYMENT.md](DEPLOYMENT.md))

---

**Happy coding!** 🚀 For questions, see [CONTRIBUTING.md](../CONTRIBUTING.md) or open a discussion.
