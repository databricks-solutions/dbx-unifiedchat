# Databricks Notebooks Guide

This directory contains notebooks for testing and deploying the agent in Databricks.

## 📁 Files in This Directory

| File | Purpose | When to Use |
|------|---------|-------------|
| `test_agent_databricks.py` | Test modular code in Databricks | Before deploying - test with real services |
| `deploy_agent.py` | Deploy to Model Serving | After testing - final deployment |
| `agent.py` | MLflow wrapper | Referenced by deploy_agent.py |
| `examples/` | Demo notebooks | Learning and examples |
| `archive/` | Historical versions | Reference only |

## 🧪 Testing in Databricks (Workflow 2)

### Why Test in Databricks?

Before deploying to Model Serving, test your agent code in a Databricks notebook environment to:

- ✅ Test with real Databricks services (Genie, Vector Search, Lakebase)
- ✅ Catch environment-specific issues before deploying
- ✅ Faster iteration than deploying to Model Serving
- ✅ Validate the same code that will be deployed

### Quick Start

**1. Sync your code to Databricks**

Option 1: Databricks CLI
```bash
databricks workspace import-dir ../src/multi_agent /Workspace/src/multi_agent --overwrite
```

Option 2: Databricks Repos (Recommended)
- Fork this repo
- Link to Databricks Repos
- Code auto-syncs when you push

**2. Open `test_agent_databricks.py` in Databricks**

**3. Run the cells to test your code**
- Imports from `../src/multi_agent/`
- Uses `dev_config.yaml` for configuration
- Tests with real Databricks services

**4. Iterate**
- Make changes locally in `src/multi_agent/`
- Sync to Databricks
- Test again in `test_agent_databricks.py`

### What Gets Tested?

When you run `test_agent_databricks.py`, you're testing:

- ✅ All agents (Supervisor, Planning, Genie, SQL Synthesis, etc.)
- ✅ Real Genie space queries
- ✅ Real Vector Search retrieval
- ✅ Real Lakebase state management
- ✅ Configuration loading from YAML
- ✅ End-to-end multi-agent workflows

### Common Issues

**Import Error**: `ModuleNotFoundError: No module named 'multi_agent'`
- **Solution**: Make sure you synced `src/multi_agent/` to `/Workspace/src/multi_agent`
- Check the path in your import statement

**Config Error**: `FileNotFoundError: dev_config.yaml`
- **Solution**: Make sure `dev_config.yaml` is at repo root
- Verify the path in your config loading code

**Genie Error**: `GenieSpace not found`
- **Solution**: Check Genie space IDs in `dev_config.yaml` are correct
- Verify you have access to those Genie spaces

## 🚢 Deploying to Production (Workflow 3)

After testing successfully in `test_agent_databricks.py`, deploy with `deploy_agent.py`.

### Deployment Steps

**1. Update production configuration**
- Edit `../prod_config.yaml` if needed
- Verify all Genie space IDs, endpoints, and resources

**2. Open `deploy_agent.py` in Databricks**

**3. Run deployment cells**
The notebook will:
- Package modular code from `../src/multi_agent/` using `code_paths`
- Load configuration from `../prod_config.yaml`
- Log model to MLflow with all required resources
- Register model to Unity Catalog
- Deploy to Model Serving

**4. Monitor deployment**
- Check Model Serving UI for endpoint status
- Verify endpoint is ready and serving
- Test with sample queries

### What Gets Deployed?

When you run `deploy_agent.py`, MLflow packages:
- ✅ Agent wrapper (`agent.py`)
- ✅ All modular code (`../src/multi_agent/` via `code_paths`)
- ✅ Production configuration (`../prod_config.yaml`)
- ✅ All required resources (Genie spaces, Vector Search, Lakebase, etc.)

### Deployment Configuration

Key line in `deploy_agent.py` (around line 5627):
```python
logged_agent_info = mlflow.pyfunc.log_model(
    name="super_agent_hybrid_with_memory",
    python_model="./agent.py",
    code_paths=["../src/multi_agent"],  # 🎯 Packages modular code
    input_example=input_example,
    resources=resources,
    model_config="../prod_config.yaml",  # Runtime config
    pip_requirements=[...]
)
```

## 🔄 Typical Workflow

```
1. Develop locally (src/multi_agent/)
   ↓
2. Sync to Databricks
   databricks workspace import-dir src/multi_agent /Workspace/src/multi_agent
   ↓
3. Test with test_agent_databricks.py ← You are here
   - Open notebook in Databricks
   - Run cells to test
   - Verify all agents work with real services
   ↓
4. If tests pass → Deploy with deploy_agent.py
   - Update prod_config.yaml
   - Run deployment cells
   - Monitor Model Serving UI
   ↓
5. If tests fail → Fix locally → Repeat from step 2
```

## 📖 Related Guides

- [Local Development](../docs/LOCAL_DEVELOPMENT.md) - Develop agent code locally
- [Configuration](../docs/CONFIGURATION.md) - Understanding config systems
- [Deployment](../docs/DEPLOYMENT.md) - Complete deployment guide
- [Architecture](../docs/ARCHITECTURE.md) - System design

## 🎯 Best Practices

1. **Always test before deploying**
   - Use `test_agent_databricks.py` to catch issues early
   - Test with real data and services

2. **Use test mode for iteration**
   - Small sample sizes for quick validation
   - Full dataset only after validation

3. **Monitor your deployments**
   - Check Model Serving logs
   - Verify endpoint performance
   - Test with sample queries after deployment

4. **Keep configs updated**
   - `dev_config.yaml` for testing
   - `prod_config.yaml` for production
   - Document any changes

---

**Ready to test?** Start with [test_agent_databricks.py](test_agent_databricks.py) and validate your changes! 🧪
