# Deployment Guide - Super Agent with Memory to Databricks Model Serving

## Overview

This guide walks you through deploying your Super Agent with both short-term and long-term memory to Databricks Model Serving.

**Prerequisites:**
- ✅ Lakebase instance created and configured in `.env`
- ✅ One-time setup completed (checkpoint and store tables created)
- ✅ Agent tested locally

---

## Prerequisites: Find Required Resource IDs

Before deploying, you need to identify two critical resources:

### 1. SQL Warehouse ID

Your Genie spaces use a SQL Warehouse to execute queries. Find its ID:

**Method 1: From Databricks UI**
1. Go to: **SQL Warehouses** in the sidebar
2. Click on the warehouse your Genie spaces use
3. Copy the **Warehouse ID** from:
   - The URL: `...sql/warehouses/abc123...` → `abc123`
   - Or the **Details** tab

**Method 2: From Genie Space**
1. Go to: **Genie** → Select one of your Genie spaces
2. Click **Settings** → **SQL Warehouse**
3. Note the warehouse name, then get its ID using Method 1

### 2. Underlying Tables Used by Genie Spaces

Query your metadata table to find all tables referenced by your Genie spaces:

```sql
-- Run in Databricks SQL Editor
SELECT DISTINCT table_name 
FROM yyang.multi_agent_genie.enriched_genie_docs_chunks
WHERE table_name IS NOT NULL
ORDER BY table_name;
```

**Expected Output:**
```
yyang.multi_agent_genie.patient_demographics
yyang.multi_agent_genie.clinical_trials
yyang.multi_agent_genie.medication_orders
...
```

**Why are these needed?**
Per [Databricks documentation](https://docs.databricks.com/aws/en/generative-ai/agent-framework/agent-authentication?language=Genie+Spaces+%28LangChain%29#automatic-authentication-passthrough):

> "Remember to log all downstream dependent resources, too. For example, if you log a Genie Space, you must also log its tables, SQL Warehouses, and Unity Catalog functions."

This enables automatic authentication passthrough for distributed serving.

---

## Deployment Steps

### Step 1: Prepare for Deployment

**Location:** `Notebooks/Super_Agent_hybrid.py` - Line ~2577

Uncomment the "Deploy Agent to Model Serving" cell. The code is already there!

**Before running:** Update these variables in the deployment cell:
```python
# Update with your actual SQL Warehouse ID (from above)
SQL_WAREHOUSE_ID = "your_warehouse_id"  # e.g., "abc123def456"

# Update with your underlying tables (from query above)
UNDERLYING_TABLES = [
    f"{CATALOG}.{SCHEMA}.patient_demographics",
    f"{CATALOG}.{SCHEMA}.clinical_trials",
    # ... add all tables from your query
]
```

### Step 2: Log Model with Resources (CRITICAL!)

```python
# DBTITLE 1,Deploy Agent to Model Serving with Memory Support
from mlflow.models.resources import (
    DatabricksServingEndpoint,
    DatabricksLakebase,
    DatabricksFunction,
    DatabricksVectorSearchIndex,
    DatabricksGenieSpace,
    DatabricksSQLWarehouse,
    DatabricksTable,
)
from pkg_resources import get_distribution

# Get Genie space IDs from config
GENIE_SPACE_IDS = config.table_metadata.genie_space_ids

# ⚠️ TODO: Get your SQL Warehouse ID
# Go to: SQL Warehouses → Click your warehouse → Copy the ID from URL or Details
SQL_WAREHOUSE_ID = "your_warehouse_id"  

# ⚠️ TODO: Query underlying tables used by Genie spaces
# Run: SELECT DISTINCT table_name FROM yyang.multi_agent_genie.enriched_genie_docs_chunks
# Add each table as DatabricksTable(table_name="catalog.schema.table")
UNDERLYING_TABLES = [
    # Example: f"{CATALOG}.{SCHEMA}.patient_demographics",
    # Example: f"{CATALOG}.{SCHEMA}.clinical_trials",
]

# Declare all resources the agent needs
# This enables automatic authentication passthrough
resources = [
    # LLM endpoints
    DatabricksServingEndpoint(LLM_ENDPOINT_CLARIFICATION),
    DatabricksServingEndpoint(LLM_ENDPOINT_PLANNING),
    DatabricksServingEndpoint(LLM_ENDPOINT_SQL_SYNTHESIS),
    DatabricksServingEndpoint(LLM_ENDPOINT_SUMMARIZE),
    DatabricksServingEndpoint(EMBEDDING_ENDPOINT),
    
    # ⚠️ CRITICAL: Lakebase for state management
    # Without this, distributed serving will break!
    DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME),
    
    # Vector Search Index
    DatabricksVectorSearchIndex(index_name=VECTOR_SEARCH_INDEX),
    
    # SQL Warehouse (required for Genie spaces and UC functions)
    DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
    
    # ⚠️ IMPORTANT: Genie Spaces (Must declare ALL Genie spaces!)
    # Per Databricks docs: "if you log a Genie Space, you must also log its 
    # tables, SQL Warehouses, and Unity Catalog functions"
    *[DatabricksGenieSpace(genie_space_id=space_id) for space_id in GENIE_SPACE_IDS],
    
    # Tables (metadata enrichment + underlying Genie tables)
    DatabricksTable(table_name=TABLE_NAME),  # Metadata enrichment table
    *[DatabricksTable(table_name=table) for table in UNDERLYING_TABLES],
    
    # UC Functions (metadata querying tools)
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_summary"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_table_overview"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_column_detail"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_details"),
]

# Input example for MLflow
input_example = {
    "input": [{"role": "user", "content": "Show me patient data"}],
    "custom_inputs": {"thread_id": "example-123"},
    "context": {"conversation_id": "sess-001", "user_id": "user@example.com"}
}

# Log model to MLflow
with mlflow.start_run():
    logged_agent_info = mlflow.pyfunc.log_model(
        name="super_agent_hybrid_with_memory",
        python_model="Super_Agent_hybrid.py",  # This file
        input_example=input_example,
        resources=resources,  # ⚠️ CRITICAL for distributed serving!
        pip_requirements=[
            f"databricks-langchain[memory]=={get_distribution('databricks-langchain').version}",
            f"databricks-agents=={get_distribution('databricks-agents').version}",
            f"databricks-vectorsearch=={get_distribution('databricks-vectorsearch').version}",
            f"mlflow[databricks]=={mlflow.__version__}",
        ]
    )
    print(f"✓ Model logged: {logged_agent_info.model_uri}")
```

**Expected Output:**
```
✓ Model logged: runs:/abc123def456/super_agent_hybrid_with_memory
```

---

### Step 3: Register to Unity Catalog

```python
# Set Unity Catalog as the registry
mlflow.set_registry_uri("databricks-uc")

# Define model name in Unity Catalog
UC_MODEL_NAME = f"{CATALOG}.{SCHEMA}.super_agent_hybrid"
# Example: yyang.multi_agent_genie.super_agent_hybrid

# Register the model
uc_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri,
    name=UC_MODEL_NAME
)

print(f"✓ Model registered: {UC_MODEL_NAME} version {uc_model_info.version}")
```

**Expected Output:**
```
✓ Model registered: yyang.multi_agent_genie.super_agent_hybrid version 1
```

---

### Step 4: Deploy to Model Serving

```python
from databricks import agents

# Deploy the model
deployment_info = agents.deploy(
    UC_MODEL_NAME,
    uc_model_info.version,
    scale_to_zero=True,      # Cost optimization
    workload_size="Small"    # Start small, can scale up later
)

print(f"✓ Deployed to Model Serving: {deployment_info.endpoint_name}")
print("\n" + "="*80)
print("DEPLOYMENT COMPLETE")
print("="*80)
print(f"Model: {UC_MODEL_NAME} v{uc_model_info.version}")
print(f"Endpoint: {deployment_info.endpoint_name}")
print("\nMemory Features Enabled:")
print("  ✓ Short-term: Multi-turn conversations via CheckpointSaver")
print("  ✓ Long-term: User preferences via DatabricksStore")
print("  ✓ Distributed serving: State shared across all instances")
print("="*80)
```

**Expected Output:**
```
✓ Deployed to Model Serving: super_agent_hybrid
================================================================================
DEPLOYMENT COMPLETE
================================================================================
Model: yyang.multi_agent_genie.super_agent_hybrid v1
Endpoint: super_agent_hybrid
Memory Features Enabled:
  ✓ Short-term: Multi-turn conversations via CheckpointSaver
  ✓ Long-term: User preferences via DatabricksStore
  ✓ Distributed serving: State shared across all instances
================================================================================
```

---

## Complete Deployment Code (Copy-Paste Ready)

Here's the complete code block ready to run:

```python
# COMMAND ----------

# DBTITLE 1,Deploy Agent to Model Serving with Memory Support
from mlflow.models.resources import (
    DatabricksServingEndpoint,
    DatabricksLakebase,
    DatabricksFunction,
    DatabricksVectorSearchIndex
)
from pkg_resources import get_distribution
from databricks import agents

print("="*80)
print("DEPLOYING SUPER AGENT WITH MEMORY TO MODEL SERVING")
print("="*80)

# Step 1: Declare resources
print("\n[1/4] Declaring resources...")
resources = [
    # LLM endpoints
    DatabricksServingEndpoint(LLM_ENDPOINT_CLARIFICATION),
    DatabricksServingEndpoint(LLM_ENDPOINT_PLANNING),
    DatabricksServingEndpoint(LLM_ENDPOINT_SQL_SYNTHESIS),
    DatabricksServingEndpoint(LLM_ENDPOINT_SUMMARIZE),
    DatabricksServingEndpoint(EMBEDDING_ENDPOINT),
    
    # Lakebase for state management (CRITICAL!)
    DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME),
    
    # Vector Search Index
    DatabricksVectorSearchIndex(index_name=VECTOR_SEARCH_INDEX),
    
    # UC Functions
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_summary"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_table_overview"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_column_detail"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_details"),
]
print(f"✓ Declared {len(resources)} resources")

# Step 2: Log model
print("\n[2/4] Logging model to MLflow...")
input_example = {
    "input": [{"role": "user", "content": "Show me patient data"}],
    "custom_inputs": {"thread_id": "example-123"},
    "context": {"conversation_id": "sess-001", "user_id": "user@example.com"}
}

with mlflow.start_run():
    logged_agent_info = mlflow.pyfunc.log_model(
        name="super_agent_hybrid_with_memory",
        python_model="Super_Agent_hybrid.py",
        input_example=input_example,
        resources=resources,
        pip_requirements=[
            f"databricks-langchain[memory]=={get_distribution('databricks-langchain').version}",
            f"databricks-agents=={get_distribution('databricks-agents').version}",
            f"databricks-vectorsearch=={get_distribution('databricks-vectorsearch').version}",
            f"mlflow[databricks]=={mlflow.__version__}",
        ]
    )
print(f"✓ Model logged: {logged_agent_info.model_uri}")

# Step 3: Register to Unity Catalog
print("\n[3/4] Registering to Unity Catalog...")
mlflow.set_registry_uri("databricks-uc")
UC_MODEL_NAME = f"{CATALOG}.{SCHEMA}.super_agent_hybrid"

uc_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri,
    name=UC_MODEL_NAME
)
print(f"✓ Model registered: {UC_MODEL_NAME} version {uc_model_info.version}")

# Step 4: Deploy to Model Serving
print("\n[4/4] Deploying to Model Serving...")
deployment_info = agents.deploy(
    UC_MODEL_NAME,
    uc_model_info.version,
    scale_to_zero=True,
    workload_size="Small"
)

print("\n" + "="*80)
print("✅ DEPLOYMENT COMPLETE")
print("="*80)
print(f"Model: {UC_MODEL_NAME}")
print(f"Version: {uc_model_info.version}")
print(f"Endpoint: {deployment_info.endpoint_name}")
print(f"\nEndpoint URL:")
print(f"  https://{dbutils.notebook.entry_point.getDbutils().notebook().getContext().browserHostName().get()}/ml/endpoints/{deployment_info.endpoint_name}")
print("\nMemory Features:")
print("  ✓ Short-term: Multi-turn conversations (CheckpointSaver + Lakebase)")
print("  ✓ Long-term: User preferences (DatabricksStore + Lakebase)")
print("  ✓ Distributed: State shared across all serving instances")
print("\nNext Steps:")
print("  1. Go to Machine Learning → Serving → Find your endpoint")
print("  2. Test with sample queries")
print("  3. Verify multi-turn conversations work")
print("="*80)
```

---

## Verification After Deployment

### Check Deployment Status

```python
# Get endpoint info
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
endpoint = w.serving_endpoints.get(name="super_agent_hybrid")

print(f"Endpoint: {endpoint.name}")
print(f"State: {endpoint.state.config_update}")
print(f"Ready: {endpoint.state.ready}")
```

### Test Multi-turn Conversation

```python
import requests
import json

# Get workspace URL and token
workspace_url = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiUrl().get()
token = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()

endpoint_url = f"{workspace_url}/serving-endpoints/super_agent_hybrid/invocations"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# Request 1
print("Request 1: Initial query")
response1 = requests.post(
    endpoint_url,
    headers=headers,
    json={
        "messages": [{"role": "user", "content": "Show me patient demographics"}],
        "custom_inputs": {"thread_id": "test_session_001"}
    }
)
print(f"Response 1: {response1.status_code}")
print(response1.json())

# Wait a moment
import time
time.sleep(2)

# Request 2 (should remember context)
print("\nRequest 2: Follow-up query (should remember context)")
response2 = requests.post(
    endpoint_url,
    headers=headers,
    json={
        "messages": [{"role": "user", "content": "Filter by age > 50"}],
        "custom_inputs": {"thread_id": "test_session_001"}  # SAME thread_id
    }
)
print(f"Response 2: {response2.status_code}")
print(response2.json())

# Verify it remembered context
if "patient" in str(response2.json()).lower():
    print("\n✅ SUCCESS: Agent remembered context from previous request!")
else:
    print("\n⚠️ WARNING: Context may not have been preserved")
```

---

## Important Notes

### 1. Resource Declaration is CRITICAL

```python
# ⚠️ MUST include DatabricksLakebase!
resources = [
    # ... other resources ...
    DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME),  # CRITICAL!
]
```

**Why?**
- Enables automatic authentication passthrough
- Without it, Model Serving cannot access Lakebase
- Without Lakebase access, multi-turn conversations break in distributed serving

### 2. Configuration Loaded from .env

All configuration (Lakebase instance, LLM endpoints, catalog/schema) is loaded from `.env`:
```bash
# .env file
LAKEBASE_INSTANCE_NAME=your-actual-instance-name
CATALOG_NAME=yyang
SCHEMA_NAME=multi_agent_genie
```

### 3. Deployment Time

- Initial deployment: ~15-20 minutes
- Model Serving endpoint startup: ~5-10 minutes
- Subsequent updates: ~10-15 minutes

### 4. Scaling Configuration

```python
agents.deploy(
    UC_MODEL_NAME,
    uc_model_info.version,
    scale_to_zero=True,        # Recommended: saves costs when idle
    workload_size="Small"      # Options: Small, Medium, Large
)
```

**Recommendations:**
- Start with `Small` + `scale_to_zero=True`
- Monitor performance and scale up if needed
- For production with high traffic: `Medium` or `Large`

---

## Troubleshooting

### Issue: "Resource not found: Lakebase instance"
**Solution:** Verify Lakebase instance name in `.env`:
```bash
LAKEBASE_INSTANCE_NAME=your-actual-instance-name
```

### Issue: "Permission denied accessing Lakebase"
**Solution:** Ensure `DatabricksLakebase` is in resources list:
```python
resources = [
    # ... other resources ...
    DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME),
]
```

### Issue: "Resource not found: SQL Warehouse"
**Solution:** Verify the SQL Warehouse ID is correct:
```python
SQL_WAREHOUSE_ID = "abc123def456"  # Must match actual warehouse ID
```
Check: Go to SQL Warehouses → Click your warehouse → Verify ID from URL or Details tab

### Issue: "Permission denied accessing Genie Space"
**Solution:** Ensure all Genie spaces are declared in resources:
```python
resources = [
    # ... other resources ...
    *[DatabricksGenieSpace(genie_space_id=space_id) for space_id in GENIE_SPACE_IDS],
]
```
Verify space IDs match your `.env` file: `GENIE_SPACE_IDS=space1,space2,space3`

### Issue: "Permission denied accessing table"
**Solution:** Ensure all underlying tables are declared:
```python
# Query to find missing tables:
# SELECT DISTINCT table_name FROM enriched_genie_docs_chunks

resources = [
    # ... other resources ...
    DatabricksTable(table_name=TABLE_NAME),  # Metadata table
    DatabricksTable(table_name=f"{CATALOG}.{SCHEMA}.patient_demographics"),
    # Add all tables from your query
]
```

### Issue: Multi-turn conversations not working
**Solutions:**
1. Verify Lakebase resource was included in deployment
2. Check endpoint has multiple instances running
3. Test with explicit thread_id in requests
4. Query Lakebase checkpoints table to verify state is being stored

### Issue: Deployment fails with "Invalid model signature"
**Solution:** Check input_example format:
```python
input_example = {
    "input": [{"role": "user", "content": "Show me data"}],
    "custom_inputs": {"thread_id": "example"},
    "context": {"conversation_id": "sess", "user_id": "user@example.com"}
}
```

### Issue: "Missing downstream resources for Genie Space"
**Solution:** Per Databricks documentation, when you declare a `DatabricksGenieSpace`, you **MUST** also declare:
1. `DatabricksSQLWarehouse` - The warehouse the Genie space uses
2. `DatabricksTable` - All tables the Genie space accesses
3. `DatabricksFunction` - All UC functions the Genie space calls

If missing any of these, add them to the `resources` list.

---

## Monitoring

### Query Lakebase State

```python
# Connect to Lakebase (if you have SQL Warehouses access)
from databricks import sql

# View recent checkpoints
query = """
SELECT 
    thread_id,
    checkpoint_id,
    (checkpoint::json->>'ts')::timestamptz AS timestamp
FROM checkpoints
ORDER BY timestamp DESC
LIMIT 20;
"""

# View user memories
query = """
SELECT 
    namespace,
    key,
    value,
    updated_at
FROM public.store
WHERE namespace LIKE '%user_memories%'
ORDER BY updated_at DESC
LIMIT 50;
```

### Check Endpoint Metrics

Go to: **Machine Learning → Serving → Your Endpoint → Metrics**

Monitor:
- Request rate
- Latency (p50, p95, p99)
- Error rate
- Instance count (should scale with load)

---

## Summary

### Deployment Checklist

- [ ] ✅ Lakebase instance created and configured in `.env`
- [ ] ✅ One-time setup completed (checkpoint/store tables)
- [ ] ✅ Agent tested locally
- [ ] ✅ Uncomment deployment cell in notebook
- [ ] ✅ Run Step 1: Log model with resources (include DatabricksLakebase!)
- [ ] ✅ Run Step 2: Register to Unity Catalog
- [ ] ✅ Run Step 3: Deploy to Model Serving
- [ ] ✅ Wait ~15-20 minutes for deployment
- [ ] ✅ Test multi-turn conversations
- [ ] ✅ Verify state is shared across instances
- [ ] ✅ Monitor endpoint performance

### Key Success Factors

1. **DatabricksLakebase in resources** - Enables distributed state sharing
2. **Correct pip requirements** - Must include `[memory]` extra
3. **Valid input example** - Proper format for signature
4. **Lakebase instance running** - Verify before deployment
5. **Multiple instances** - Model Serving automatically scales

---

**Ready to Deploy?** 

Run the complete deployment code block in your notebook! 🚀
