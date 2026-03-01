# Development Guide

This guide covers the three supported development workflows for the Multi-Agent Genie System. Choose the workflow that best fits your current task.

## Workflow 1: Local Development (Fastest Iteration)

Best for: Writing unit tests, modifying single agent logic, fast iteration without deploying.

### Setup

```bash
# Setup virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Setup pre-commit hooks for code quality
pip install pre-commit
pre-commit install
```

### Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
# Edit .env with your credentials and resource IDs
```

### Testing & Running

```bash
# Test individual modules
pytest tests/unit/test_planning_agent.py -v

# Test full system locally
python src/multi_agent/main.py
```

## Workflow 2: Databricks Notebook Dev (Real Services)

Best for: Integration testing, debugging with real Databricks services (Genie, Vector Search, Lakebase) before deployment.

### 1. Sync code to Databricks
We recommend using Databricks Repos to keep your Databricks workspace synced with your git repository:
```bash
databricks repos update <repo-id>
```

### 2. Configuration
The notebook tests use `dev_config.yaml`. Make sure your variables in this file point to your development/sandbox resources.

### 3. Run Notebooks
Open the Databricks UI, navigate to the synced repo, and run:
1. `Notebooks/test_agent_databricks.py`
2. Iterate on `src/multi_agent/` code (changes will auto-reload if using `%autoreload 2`)
3. Debug directly against real services without packaging the model.

## Workflow 3: Production Deployment (CI/CD)

Best for: Final deployment to Model Serving endpoints.

### Method A: Via GitHub Actions (Recommended)

Our CI/CD pipeline automatically runs tests and deploys based on branch pushes.

1. **Deploy to Dev**:
   ```bash
   git checkout develop
   git commit -m "feat: add new agent"
   git push  # Auto-runs tests, validates bundle, and deploys to dev workspace
   ```

2. **Deploy to Prod**:
   ```bash
   # Create a Pull Request to main branch
   # Once merged:
   # Auto-runs tests, validates bundle, and deploys to prod workspace
   ```

### Method B: Via CLI (Manual)

If you need to deploy manually using the Databricks Asset Bundle (DAB):

```bash
# Validate the bundle
databricks bundle validate

# Test agent in Databricks (Dev)
databricks bundle run agent_integration_test -t dev

# Deploy to Dev
databricks bundle run agent_deploy -t dev

# Deploy to Prod
databricks bundle run agent_deploy -t prod
```