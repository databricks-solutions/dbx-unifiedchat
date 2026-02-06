# Deployment Guide

Complete guide for deploying the multi-agent system to Databricks Model Serving.

## Overview

This guide covers deploying both the ETL pipeline and the agent system to Databricks.

## Two-Phase Deployment

### Phase 1: Deploy ETL Pipeline

ETL must be deployed and run **before** deploying the agent system.

**Options**:
1. **Databricks Jobs** (Recommended for scheduled ETL)
2. **Manual execution** (Run notebooks directly)
3. **Databricks Workflows** (For complex orchestration)

See [ETL Deployment](#etl-deployment) section below.

### Phase 2: Deploy Agent System

After ETL completes, deploy the agent to Model Serving.

See [Agent Deployment](#agent-deployment) section below.

---

## ETL Deployment

### Option 1: Databricks Jobs (Recommended)

**Setup**:
```bash
# 1. Upload ETL notebooks to Databricks workspace
databricks workspace import-dir etl /Workspace/etl --overwrite

# 2. Create job configuration
# Use config/jobs/job_config_etl.json as template

# 3. Create job via CLI or UI
databricks jobs create --json-file config/jobs/job_config_etl.json

# 4. Run job
databricks jobs run-now --job-id <job-id>
```

**Job Configuration** (`config/jobs/job_config_etl.json`):
```json
{
  "name": "Multi-Agent ETL Pipeline",
  "tasks": [
    {
      "task_key": "export_genie_spaces",
      "notebook_task": {
        "notebook_path": "/Workspace/etl/01_export_genie_spaces"
      }
    },
    {
      "task_key": "enrich_metadata",
      "depends_on": [{"task_key": "export_genie_spaces"}],
      "notebook_task": {
        "notebook_path": "/Workspace/etl/02_enrich_table_metadata"
      }
    },
    {
      "task_key": "build_vector_index",
      "depends_on": [{"task_key": "enrich_metadata"}],
      "notebook_task": {
        "notebook_path": "/Workspace/etl/03_build_vector_search_index"
      }
    }
  ]
}
```

### Option 2: Manual Execution

For one-time setup or testing:

1. Upload notebooks to Databricks workspace
2. Open each notebook in Databricks
3. Run notebooks **in order**:
   - `01_export_genie_spaces.py`
   - `02_enrich_table_metadata.py`
   - `03_build_vector_search_index.py`

### ETL Verification

After ETL completes, verify:

```sql
-- Check enriched metadata table
SELECT COUNT(*) FROM {catalog}.{schema}.enriched_genie_docs;

-- Check chunks table
SELECT COUNT(*) FROM {catalog}.{schema}.enriched_genie_docs_chunks;

-- Check vector search index
-- Go to UI: Compute → Vector Search → Check index status
```

---

## Agent Deployment

### Prerequisites

Before deploying the agent:

1. ✅ ETL pipeline has run successfully
2. ✅ Enriched tables exist
3. ✅ Vector search index is synced
4. ✅ Agent code tested locally (see [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md))
5. ✅ Agent code tested in Databricks (see [../notebooks/README.md](../notebooks/README.md))

### Deployment Steps

#### Step 1: Prepare Code and Configuration

```bash
# 1. Sync agent code to Databricks
databricks workspace import-dir src/multi_agent /Workspace/src/multi_agent --overwrite

# 2. Upload notebooks
databricks workspace import notebooks/deploy_agent.py /Workspace/notebooks/deploy_agent
databricks workspace import notebooks/agent.py /Workspace/notebooks/agent

# 3. Update prod_config.yaml
# Edit prod_config.yaml with production values:
# - Production Genie space IDs
# - Production SQL Warehouse ID
# - Production Lakebase instance
# - Production LLM endpoints

# 4. Upload configuration
databricks workspace upload prod_config.yaml /Workspace/prod_config.yaml
```

#### Step 2: Deploy via Databricks Notebook

1. **Open `notebooks/deploy_agent.py` in Databricks**

2. **Verify configuration** in the first few cells:
   - Check `prod_config.yaml` path
   - Verify all resources are accessible

3. **Run deployment cells**:

The deployment notebook does:
```python
# Key deployment code (around line 5627)
logged_agent_info = mlflow.pyfunc.log_model(
    name="super_agent_hybrid_with_memory",
    python_model="./agent.py",              # MLflow wrapper
    code_paths=["../src/multi_agent"],      # 🎯 Packages modular code
    input_example=input_example,
    resources=resources,                     # All Databricks resources
    model_config="../prod_config.yaml",      # Production config
    pip_requirements=[...]
)

# Register to Unity Catalog
uc_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri,
    name=f"{catalog}.{schema}.super_agent_hybrid"
)

# Deploy to Model Serving
deployment_info = agents.deploy(
    f"{catalog}.{schema}.super_agent_hybrid",
    uc_model_info.version,
    scale_to_zero=True,
    workload_size="Small"
)
```

4. **Monitor deployment**:
   - Check MLflow UI for model registration
   - Check Model Serving UI for endpoint status
   - Wait for endpoint to be ready (typically 5-10 minutes)

#### Step 3: Verify Deployment

```bash
# Test endpoint via CLI
databricks serving-endpoints query \
  --endpoint-name multi-agent-genie-endpoint \
  --data '{
    "input": [{"role": "user", "content": "Show me patient data"}],
    "custom_inputs": {"thread_id": "test-123"}
  }'
```

Or test in AI Playground:
- Go to Model Serving UI
- Open your endpoint
- Click "Query Endpoint" or "AI Playground"
- Test with sample queries

### Deployment Resources

The agent requires these Databricks resources (automatically logged):

- **LLM Endpoints**: Various Claude models for different agents
- **Lakebase**: State management (short-term + long-term memory)
- **Vector Search Index**: Semantic search for planning
- **SQL Warehouse**: Query execution
- **Genie Spaces**: Data source querying
- **Unity Catalog Tables**: Enriched metadata
- **UC Functions**: Metadata tools

All resources are declared in `deploy_agent.py` and logged with MLflow.

## Configuration: Three Systems

### Development Testing (`dev_config.yaml`)
- Development Genie spaces
- Test data
- Smaller/cheaper resources
- Used by `notebooks/test_agent_databricks.py`

### Production (`prod_config.yaml`)
- Production Genie spaces
- Full data
- Production-grade resources
- Used by `notebooks/deploy_agent.py`

### Local (`config.py` + `.env`)
- Local development
- Mock or real services
- Not used for deployment

See [CONFIGURATION.md](CONFIGURATION.md) for complete guide.

## Updating Deployed Agent

### Option 1: New Version (Recommended)

```python
# In deploy_agent.py:
# 1. Make code changes in src/multi_agent/
# 2. Sync to Databricks
# 3. Run deployment cells again
# This creates a new model version
```

### Option 2: Update Configuration Only

If only configuration changed:
```yaml
# 1. Edit prod_config.yaml
# 2. Redeploy (creates new version with new config)
```

## Monitoring

### Check Endpoint Status

```bash
# Get endpoint details
databricks serving-endpoints get --name multi-agent-genie-endpoint

# View logs
databricks serving-endpoints logs --name multi-agent-genie-endpoint
```

### MLflow Tracking

View in MLflow UI:
- Model versions
- Logged parameters (LLM endpoints, config version)
- Metrics and traces
- Deployment history

### Inference Tables

Check AI Gateway inference tables:
```sql
SELECT * FROM system.ai_gateway.inference_logs
WHERE endpoint_name = 'multi-agent-genie-endpoint'
ORDER BY timestamp DESC
LIMIT 100;
```

## Rollback

### Rollback to Previous Version

```python
from databricks import agents

# List versions
# Find previous working version

# Deploy previous version
agents.deploy(
    "catalog.schema.super_agent_hybrid",
    previous_version_number,
    scale_to_zero=True,
    workload_size="Small"
)
```

## Troubleshooting

### Deployment Fails: Resource Not Found

**Problem**: `ResourceNotFound: Genie space not found`

**Solution**:
- Verify all Genie space IDs in `prod_config.yaml` exist
- Check you have access to all resources
- Review resource list in `deploy_agent.py`

### Deployment Fails: Import Error

**Problem**: `ModuleNotFoundError: No module named 'multi_agent'`

**Solution**:
- Verify `code_paths=["../src/multi_agent"]` in `deploy_agent.py`
- Check `src/multi_agent/` was synced to Databricks
- Verify path is correct relative to notebooks/

### Endpoint Not Responding

**Problem**: Endpoint returns errors or timeouts

**Solution**:
- Check endpoint logs in Model Serving UI
- Verify all resources are accessible
- Check Lakebase connection
- Increase workload size if needed

### Configuration Not Loading

**Problem**: Agent using wrong configuration

**Solution**:
- Verify `model_config="../prod_config.yaml"` path
- Check YAML file exists at repo root
- Review MLflow logs for loaded config

## Scaling and Performance

### Workload Sizing

Start with `Small` and scale up based on usage:
- `Small`: Development/testing, low traffic
- `Medium`: Moderate traffic
- `Large`: High traffic, complex queries

### Auto-scaling

Enable with `scale_to_zero=True`:
- Saves costs when not in use
- Automatically scales based on traffic
- Cold start: ~1-2 minutes

### Performance Optimization

- Use appropriate LLM models per agent (balance speed/accuracy)
- Monitor inference latency
- Optimize vector search queries
- Cache frequently used data

## Cost Management

### Development
- Use `dev_config.yaml` with smaller models
- Enable scale-to-zero
- Use Small workload size
- Limit testing Genie spaces

### Production
- Use `prod_config.yaml` with optimal models
- Monitor costs in Databricks UI
- Set up alerts for unexpected spikes
- Consider reserved capacity for predictable workloads

## See Also

- [Local Development Guide](LOCAL_DEVELOPMENT.md)
- [Configuration Guide](CONFIGURATION.md)
- [Architecture](ARCHITECTURE.md)
- [Databricks Testing Guide](../notebooks/README.md)

---

**Ready to deploy?** Follow the steps above and monitor your deployment! 🚀
