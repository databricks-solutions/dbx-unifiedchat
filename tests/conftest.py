"""
Shared test fixtures and configuration for the test suite.
"""

import pytest
import os
from typing import Generator
from src.multi_agent.core.config import get_config, AgentConfig


@pytest.fixture(scope="session")
def test_config() -> AgentConfig:
    """
    Load configuration for testing.
    
    Uses .env file for local testing.
    Set TEST_MODE=1 in .env to use test resources.
    """
    # Force reload to get latest .env values
    config = get_config(reload=True)
    return config


@pytest.fixture(scope="session")
def databricks_config(test_config: AgentConfig):
    """Databricks connection configuration."""
    return test_config.databricks


@pytest.fixture(scope="session")
def unity_catalog_config(test_config: AgentConfig):
    """Unity Catalog configuration."""
    return test_config.unity_catalog


@pytest.fixture(scope="session")
def llm_config(test_config: AgentConfig):
    """LLM endpoints configuration."""
    return test_config.llm


@pytest.fixture
def sample_query() -> str:
    """Sample query for testing."""
    return "Show me patient demographics"


@pytest.fixture
def sample_conversation():
    """Sample conversation for testing."""
    return {
        "input": [{"role": "user", "content": "Show me patient data"}],
        "custom_inputs": {"thread_id": "test-123"},
        "context": {"conversation_id": "conv-test", "user_id": "test-user"}
    }


@pytest.fixture
def sample_state():
    """Sample agent state for testing."""
    return {
        "messages": [],
        "relevant_spaces": [],
        "sql_query": None,
        "sql_results": None,
        "final_response": None,
        "conversation_id": "test-conv",
        "user_id": "test-user",
        "thread_id": "test-thread"
    }


@pytest.fixture(scope="session")
def skip_integration() -> bool:
    """
    Check if integration tests should be skipped.
    
    Integration tests require Databricks connection.
    Set SKIP_INTEGRATION=1 to skip these tests.
    """
    return os.getenv("SKIP_INTEGRATION", "0") == "1"


@pytest.fixture
def mock_genie_response():
    """Mock Genie space response for testing."""
    return {
        "result": {
            "rows": [
                {"patient_id": "1", "name": "John Doe", "age": 45},
                {"patient_id": "2", "name": "Jane Smith", "age": 52}
            ],
            "columns": ["patient_id", "name", "age"]
        }
    }


@pytest.fixture
def mock_vector_search_results():
    """Mock vector search results for testing."""
    return [
        {
            "space_id": "space1",
            "space_name": "patient_demographics",
            "score": 0.95
        },
        {
            "space_id": "space2",
            "space_name": "medications",
            "score": 0.82
        }
    ]


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "unit: Unit tests (fast, no external dependencies)"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests (require Databricks)"
    )
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests (full system)"
    )
    config.addinivalue_line(
        "markers", "slow: Slow tests (may take >10 seconds)"
    )


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests based on their location."""
    for item in items:
        # Add markers based on test location
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
