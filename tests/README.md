# Test Suite

Comprehensive test suite for the multi-agent system.

## Test Structure

```
tests/
├── unit/            # Fast, isolated tests (no external dependencies)
├── integration/     # Tests with Databricks services
├── e2e/            # End-to-end system tests
├── conftest.py     # Shared fixtures
└── README.md       # This file
```

## Running Tests

### Quick Start

```bash
# Run all tests
pytest

# Run unit tests only (fast, ~1 minute)
pytest tests/unit/

# Run integration tests (requires Databricks, ~5-10 minutes)
pytest tests/integration/

# Run end-to-end tests (full system, ~10-15 minutes)
pytest tests/e2e/

# Run with coverage
pytest --cov=src.multi_agent tests/

# Run specific test file
pytest tests/unit/test_config.py -v

# Run specific test function
pytest tests/unit/test_agents.py::test_supervisor_agent -v
```

### Test Markers

Tests are automatically marked based on location:

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run only e2e tests
pytest -m e2e

# Exclude slow tests
pytest -m "not slow"
```

## Test Categories

### Unit Tests (`tests/unit/`)

**Purpose**: Test individual components in isolation

**Characteristics**:
- ✅ Fast (< 1 second each)
- ✅ No external dependencies
- ✅ Mocked Databricks services
- ✅ Can run offline

**Examples**:
- `test_config.py`: Configuration loading
- `test_agents.py`: Individual agent logic
- `test_tools.py`: Tool functions
- `test_utils.py`: Utility functions

**Run frequently**: After every code change

### Integration Tests (`tests/integration/`)

**Purpose**: Test integration with Databricks services

**Characteristics**:
- ⚠️ Requires Databricks connection
- ⚠️ Slower (5-30 seconds each)
- ✅ Tests real services (Genie, Vector Search, etc.)
- ✅ Validates end-to-end workflows

**Examples**:
- `test_vector_search_detailed.py`: Vector search integration
- `test_sql_validation_agent.py`: SQL validation with real warehouse
- `test_genie_routing_and_SQL_synthesis_agent.py`: Genie integration
- `test_notebooks_runner.py`: Notebook execution

**Run before**: Committing significant changes

### End-to-End Tests (`tests/e2e/`)

**Purpose**: Test complete multi-agent system

**Characteristics**:
- ⚠️ Requires complete setup (ETL completed)
- ⚠️ Slowest (10-60 seconds each)
- ✅ Tests full user workflows
- ✅ Validates production scenarios

**Examples**:
- `test_multi_agent_system.py`: Complete agent workflows

**Run before**: Deploying to production

## Configuration for Tests

### Local Testing

Uses `.env` file with test values:

```bash
# .env
TEST_MODE=1
CATALOG_NAME=test_catalog
SCHEMA_NAME=test_schema
# ... other test values
```

### Skip Integration Tests

If you don't have Databricks access:

```bash
# Skip integration tests
SKIP_INTEGRATION=1 pytest

# Or skip via marker
pytest -m "not integration"
```

## Writing Tests

### Unit Test Example

```python
# tests/unit/test_config.py
import pytest
from config import get_config

def test_config_loads():
    """Test configuration loads successfully."""
    config = get_config()
    assert config.unity_catalog.catalog_name is not None
    assert config.llm.endpoint_name is not None

def test_config_validation():
    """Test configuration validation."""
    config = get_config()
    config.validate()  # Should not raise
```

### Integration Test Example

```python
# tests/integration/test_vector_search.py
import pytest

@pytest.mark.integration
def test_vector_search(test_config, skip_integration):
    """Test vector search with real endpoint."""
    if skip_integration:
        pytest.skip("Integration tests disabled")
    
    from multi_agent.tools.vector_search import search_genie_spaces
    
    results = search_genie_spaces("patient demographics")
    assert len(results) > 0
    assert "space_id" in results[0]
```

### E2E Test Example

```python
# tests/e2e/test_multi_agent_system.py
import pytest

@pytest.mark.e2e
@pytest.mark.slow
def test_complete_query_workflow(test_config, sample_query):
    """Test complete agent workflow end-to-end."""
    from multi_agent.core.graph import create_agent_graph
    
    agent = create_agent_graph(test_config)
    response = agent.invoke({"input": [{"role": "user", "content": sample_query}]})
    
    assert response is not None
    assert "final_response" in response
    assert len(response["final_response"]) > 0
```

## Fixtures

Shared fixtures in `conftest.py`:

- `test_config`: Test configuration from .env
- `sample_query`: Sample query string
- `sample_conversation`: Sample conversation dict
- `sample_state`: Sample agent state
- `mock_genie_response`: Mocked Genie response
- `mock_vector_search_results`: Mocked vector search results

## Continuous Integration

Tests run automatically on:
- Pull requests
- Merges to main
- Scheduled nightly builds

See `.github/workflows/ci.yml` for CI configuration.

## Coverage

Maintain test coverage above 80%:

```bash
# Generate coverage report
pytest --cov=src.multi_agent --cov-report=html tests/

# View report
open htmlcov/index.html
```

## Troubleshooting

### Tests Failing: Module Not Found

**Problem**: `ModuleNotFoundError: No module named 'multi_agent'`

**Solution**:
```bash
# Make sure you're in repo root
cd /path/to/KUMC_POC_hlsfieldtemp

# Install in development mode
pip install -e .
```

### Integration Tests Failing

**Problem**: Databricks connection errors

**Solution**:
- Check `.env` file has correct credentials
- Verify Databricks workspace is accessible
- Run with `SKIP_INTEGRATION=1` to skip

### Tests Slow

**Problem**: Tests taking too long

**Solution**:
```bash
# Run only fast unit tests
pytest tests/unit/

# Skip slow tests
pytest -m "not slow"

# Run in parallel
pip install pytest-xdist
pytest -n auto
```

## Best Practices

1. **Write tests for new code**
   - Add unit tests for new functions
   - Add integration tests for Databricks interactions
   - Add e2e tests for new workflows

2. **Keep tests fast**
   - Mock external dependencies in unit tests
   - Use small datasets in integration tests
   - Mark slow tests with `@pytest.mark.slow`

3. **Test isolation**
   - Each test should be independent
   - Clean up resources after tests
   - Use fixtures for setup/teardown

4. **Descriptive names**
   - Test name should describe what it tests
   - Use `test_<feature>_<scenario>` pattern

## See Also

- [Local Development Guide](../docs/LOCAL_DEVELOPMENT.md)
- [Contributing Guide](../CONTRIBUTING.md)
- [Source Code](../src/multi_agent/)

---

**Run tests often!** They're your safety net. 🛡️
