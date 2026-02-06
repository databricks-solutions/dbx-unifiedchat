# Contributing to Multi-Agent Genie

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Code of Conduct

By participating in this project, you agree to maintain a respectful and collaborative environment.

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Git
- Databricks workspace access (for integration testing)

### Setup for Local Development

Follow our [Local Development Guide](docs/LOCAL_DEVELOPMENT.md) for complete setup instructions.

Quick setup:
```bash
git clone <repo-url>
cd KUMC_POC_hlsfieldtemp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env  # Edit with your credentials
pytest tests/unit/
```

## How to Contribute

### 1. Find or Create an Issue

- Check existing issues for tasks you can help with
- Create a new issue to discuss significant changes before starting work
- Get feedback on your proposed approach

### 2. Fork and Branch

```bash
# Fork the repository on GitHub
# Clone your fork
git clone https://github.com/YOUR_USERNAME/multi-agent-genie.git
cd multi-agent-genie

# Create a feature branch
git checkout -b feature/my-feature-name
```

### 3. Make Your Changes

Follow our development workflow:

```bash
# Edit code in src/multi_agent/
vim src/multi_agent/agents/my_agent.py

# Run tests
pytest tests/unit/test_my_agent.py -v

# Format code
black src/ tests/
isort src/ tests/

# Lint
flake8 src/ tests/

# Test locally
python -m src.multi_agent.main --query "test"
```

### 4. Write Tests

- Add unit tests for new functions/classes
- Add integration tests for Databricks interactions
- Maintain >80% code coverage
- All tests must pass before submitting PR

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src.multi_agent tests/
```

### 5. Document Your Changes

- Update relevant README files
- Add docstrings to new functions/classes (Google style)
- Update API documentation if adding public APIs
- Add entry to CHANGELOG.md

### 6. Commit and Push

```bash
# Commit with descriptive message
git add .
git commit -m "feat: Add new planning strategy"

# Push to your fork
git push origin feature/my-feature-name
```

### 7. Create Pull Request

- Go to GitHub and create a PR from your fork
- Fill out the PR template with:
  - Description of changes
  - Related issue numbers
  - Testing performed
  - Screenshots (if UI changes)
- Request review from maintainers

## Development Guidelines

### Code Style

- Follow PEP 8
- Use type hints
- Maximum line length: 100 characters
- Use meaningful variable names
- Add docstrings to all public functions

**Format code before committing**:
```bash
black src/ tests/
isort src/ tests/
```

### File Organization

Keep code organized by purpose:
- `src/multi_agent/agents/`: Agent implementations
- `src/multi_agent/core/`: Core infrastructure
- `src/multi_agent/tools/`: Agent tools
- `src/multi_agent/utils/`: Utility functions

**Keep files small**: Target <500 lines per file

### Testing Requirements

1. **Unit Tests**: Fast, isolated tests with mocked dependencies
2. **Integration Tests**: Tests with real Databricks services
3. **E2E Tests**: Complete system tests

Mark tests appropriately:
```python
@pytest.mark.unit
def test_fast_function():
    pass

@pytest.mark.integration
def test_databricks_connection():
    pass
```

### Documentation

- Update README files when adding features
- Document configuration changes in CONFIGURATION.md
- Add architecture notes for significant changes
- Keep documentation up-to-date with code

## What to Contribute

### Good First Issues

Look for issues tagged with `good-first-issue`:
- Documentation improvements
- Test coverage improvements
- Bug fixes in isolated components
- Code refactoring for clarity

### High-Value Contributions

- New agent implementations
- Performance optimizations
- Better error handling
- Integration with new Databricks services
- Documentation and examples

### Areas We Need Help

- More comprehensive test coverage
- Performance benchmarking
- Example notebooks
- Documentation improvements
- Bug fixes

## Development Workflows

This project supports three workflows. Ensure your changes work in all three:

### Workflow 1: Local Development

```bash
# Test locally
python -m src.multi_agent.main --query "test"
pytest tests/unit/
```

### Workflow 2: Databricks Testing

```bash
# Sync code to Databricks
databricks workspace import-dir src/multi_agent /Workspace/src/multi_agent

# Test in notebooks/test_agent_databricks.py
```

### Workflow 3: Deployment

```bash
# Deploy via notebooks/deploy_agent.py
# Verify deployment works with your changes
```

## Reporting Bugs

When reporting bugs, include:

1. **Description**: Clear description of the issue
2. **Steps to Reproduce**: Minimal example to reproduce
3. **Expected Behavior**: What should happen
4. **Actual Behavior**: What actually happens
5. **Environment**:
   - Python version
   - Package versions (`pip list`)
   - Databricks Runtime version (if applicable)
6. **Logs/Screenshots**: Any relevant error messages

## Suggesting Enhancements

For feature requests:

1. **Use Case**: Describe the problem you're trying to solve
2. **Proposed Solution**: How you envision solving it
3. **Alternatives**: Other approaches you've considered
4. **Impact**: How this helps users

## Review Process

All contributions go through review:

1. **Automated Checks**: CI runs tests, linting, type checking
2. **Code Review**: Maintainers review code quality and design
3. **Testing**: Verify tests pass and changes work as intended
4. **Documentation**: Check documentation is updated
5. **Approval**: At least one maintainer approval required

## Style Guide

### Python Code

```python
def my_function(param1: str, param2: int) -> dict:
    """
    Brief description of what the function does.
    
    Args:
        param1: Description of param1
        param2: Description of param2
        
    Returns:
        Description of return value
        
    Raises:
        ValueError: When validation fails
    """
    if not param1:
        raise ValueError("param1 cannot be empty")
    
    result = {"key": f"{param1}_{param2}"}
    return result
```

### Commit Messages

Follow conventional commits:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `refactor:` Code refactoring
- `test:` Adding or updating tests
- `chore:` Maintenance tasks

Examples:
```
feat: Add new SQL synthesis strategy
fix: Correct vector search caching issue
docs: Update deployment guide with code_paths example
refactor: Extract state management to separate module
test: Add integration tests for Genie agent
chore: Update dependencies to latest versions
```

## Community

- **Questions?** Open a discussion on GitHub
- **Found a bug?** Open an issue with reproduction steps
- **Want to chat?** Join our discussions

## Recognition

Contributors are recognized in:
- GitHub contributors list
- CHANGELOG.md for significant contributions
- Special thanks in release notes

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.

---

**Thank you for contributing!** Your work helps make this project better for everyone. 🙏
