# Configuration Guide

This repository uses three different configuration systems depending on your workflow.

## Three Configuration Systems

### 1. Local Development: `config.py` + `.env`

**Used by**: Local development and testing
**Purpose**: Quick iteration with local Python environment

**Setup**:
```bash
# 1. Copy template
cp .env.example .env

# 2. Edit .env with your credentials
# DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
# DATABRICKS_TOKEN=your-token
# CATALOG_NAME=your_catalog
# SCHEMA_NAME=your_schema
# ...

# 3. Run locally
python -m src.multi_agent.main --query "test"
```

**How it works**:
- `config.py` loads environment variables from `.env`
- Provides Python dataclasses for type-safe configuration
- Used by CLI and local testing

**Pros**:
- ✅ Fast iteration (no Databricks needed)
- ✅ Keep secrets in .env (gitignored)
- ✅ Type-safe with Python classes

**Cons**:
- ❌ Can't test Databricks-specific services locally

---

### 2. Databricks Testing: `dev_config.yaml`

**Used by**: Testing in Databricks notebooks
**Purpose**: Test with real Databricks services before deploying

**Setup**:
```yaml
# dev_config.yaml (at repo root)
catalog_name: your_catalog
schema_name: your_schema
llm_endpoint: databricks-claude-sonnet-4-5
genie_space_ids:
  - space_id_1
  - space_id_2
sql_warehouse_id: your_warehouse_id
# ...
```

**How it works**:
- Loaded by `notebooks/test_agent_databricks.py`
- Provides configuration for Databricks testing
- Same format as production config (easy to compare)

**Pros**:
- ✅ Test with real Genie spaces, Vector Search, Lakebase
- ✅ Catch environment-specific issues before deploying
- ✅ Faster than deploying to Model Serving

**Cons**:
- ⚠️ Requires syncing code to Databricks

---

### 3. Production Deployment: `prod_config.yaml`

**Used by**: Model Serving deployment
**Purpose**: Production configuration packaged with model

**Setup**:
```yaml
# prod_config.yaml (at repo root)
catalog_name: prod_catalog
schema_name: prod_schema
llm_endpoint: databricks-claude-sonnet-4-5
genie_space_ids:
  - prod_space_id_1
  - prod_space_id_2
sql_warehouse_id: prod_warehouse_id
# ...
```

**How it works**:
- Referenced by `notebooks/deploy_agent.py` (line ~5637)
- Packaged with model via `model_config` parameter
- Loaded at runtime by deployed model

**Pros**:
- ✅ Configuration versioned with model
- ✅ No environment variables needed in Model Serving
- ✅ Type-safe and structured
- ✅ Easy to test different configs

**Cons**:
- ⚠️ Must redeploy to change configuration

---

## Configuration Comparison

| Feature | Local (.env) | Databricks Test (YAML) | Production (YAML) |
|---------|--------------|------------------------|-------------------|
| **Code Location** | Local machine | Databricks workspace | Model Serving |
| **Config File** | `.env` | `dev_config.yaml` | `prod_config.yaml` |
| **Config Loader** | `config.py` | `dev_config.yaml` | `prod_config.yaml` |
| **Agent Code** | `src/multi_agent/` | `src/multi_agent/` | `src/multi_agent/` |
| **Real Services** | ❌ (can mock) | ✅ Yes | ✅ Yes |
| **Use Case** | Development | Testing | Production |

**Key Insight**: All three use the **same agent code** from `src/multi_agent/`!

## Configuration Values

### Required Values

All configuration systems need these values:

**Databricks Connection**:
- `DATABRICKS_HOST` / `databricks_host`: Your Databricks workspace URL
- `DATABRICKS_TOKEN` / `databricks_token`: Authentication token

**Unity Catalog**:
- `CATALOG_NAME` / `catalog_name`: Unity Catalog catalog name
- `SCHEMA_NAME` / `schema_name`: Schema name for tables

**LLM Endpoints** (agent-specific for optimal performance):
- `llm_endpoint_clarification`: Fast model for clarification
- `llm_endpoint_planning`: Smart model for planning
- `llm_endpoint_sql_synthesis_table`: Smart model for SQL synthesis
- `llm_endpoint_sql_synthesis_genie`: Smart model for Genie queries
- `llm_endpoint_execution`: Fast model for execution
- `llm_endpoint_summarize`: Fast model for summarization

**Genie & SQL**:
- `genie_space_ids`: List of Genie space IDs to query
- `sql_warehouse_id`: SQL Warehouse ID for queries

**Vector Search**:
- `vs_endpoint_name`: Vector Search endpoint name
- `embedding_model`: Embedding model for vector search

**Lakebase** (for state management):
- `lakebase_instance_name`: Lakebase instance name
- `lakebase_embedding_endpoint`: Embedding endpoint
- `lakebase_embedding_dims`: Embedding dimensions

### Optional Values

- `sample_size`: Number of samples for enrichment (default: 100)
- `max_unique_values`: Max unique values to capture (default: 50)

## Switching Between Configurations

### Scenario 1: Local Development → Databricks Testing

```bash
# 1. Develop locally with .env
python -m src.multi_agent.main --query "test"

# 2. Sync code to Databricks
databricks workspace import-dir src/multi_agent /Workspace/src/multi_agent

# 3. Open notebooks/test_agent_databricks.py in Databricks
# (Uses dev_config.yaml automatically)
```

### Scenario 2: Databricks Testing → Production

```bash
# 1. Test with dev_config.yaml
# Run notebooks/test_agent_databricks.py

# 2. Update prod_config.yaml if needed
# Compare with dev_config.yaml, adjust for production

# 3. Deploy with prod_config.yaml
# Run notebooks/deploy_agent.py
# (References prod_config.yaml at line ~5637)
```

## Environment-Specific Configuration

### Development Environment

Use smaller/cheaper resources:
- Smaller LLM models (e.g., Haiku for more agents)
- Small sample sizes for testing
- Test Genie spaces

### Production Environment

Use production resources:
- Optimal LLM models (e.g., Sonnet for critical agents)
- Full data processing
- Production Genie spaces
- Appropriate workload size and scaling

## Security Best Practices

1. **Never commit `.env` files**
   - Already in `.gitignore`
   - Contains sensitive credentials

2. **Use secrets for YAML configs**
   - For production: Use Databricks secrets
   - Reference in YAML: `{{secrets/scope/key}}`

3. **Rotate tokens regularly**
   - Update `.env` and YAML configs
   - Redeploy if production config changes

4. **Separate dev and prod**
   - Use different catalogs/schemas
   - Different Genie spaces
   - Prevents accidental production impact

## Troubleshooting

### Issue: Configuration not loading

**Symptom**: `FileNotFoundError` or `KeyError`

**Solutions**:
- **Local**: Check `.env` file exists and has all required values
- **Databricks**: Check YAML file is at repo root
- Verify file names match exactly

### Issue: Wrong configuration being used

**Symptom**: Using dev config in production

**Solutions**:
- Check `model_config` parameter in `deploy_agent.py`
- Verify it points to correct YAML file
- Review MLflow logs for loaded config

### Issue: Can't connect to Databricks

**Symptom**: Authentication errors

**Solutions**:
- Verify `DATABRICKS_HOST` includes `https://`
- Check token hasn't expired
- Verify workspace access

## See Also

- [Local Development Guide](LOCAL_DEVELOPMENT.md)
- [Deployment Guide](DEPLOYMENT.md)
- [ETL Configuration](../etl/README.md)

---

**Questions?** See full guide at [docs/](.) or ask in discussions!
