# Deployment Resources Update

## What Was Missing

You correctly identified that the deployment code was missing critical resources required by Databricks for automatic authentication passthrough. Per the [Databricks documentation](https://docs.databricks.com/aws/en/generative-ai/agent-framework/agent-authentication?language=Genie+Spaces+%28LangChain%29#automatic-authentication-passthrough):

> "Remember to log all downstream dependent resources, too. For example, if you log a Genie Space, you must also log its tables, SQL Warehouses, and Unity Catalog functions."

## What Was Added

### 1. New Resource Types

Added the following `mlflow.models.resources` imports:

```python
from mlflow.models.resources import (
    # ... existing imports ...
    DatabricksGenieSpace,      # ✅ NEW: For Genie agents
    DatabricksSQLWarehouse,    # ✅ NEW: For SQL execution
    DatabricksTable,           # ✅ NEW: For table access
)
```

### 2. Genie Space Resources

```python
# Get Genie space IDs from config
GENIE_SPACE_IDS = config.table_metadata.genie_space_ids

# Declare all Genie spaces
resources = [
    # ... other resources ...
    *[DatabricksGenieSpace(genie_space_id=space_id) for space_id in GENIE_SPACE_IDS],
]
```

**Why?** Your agent uses `GenieAgent` to query multiple Genie spaces dynamically. Each space must be declared for authentication.

### 3. SQL Warehouse Resource

```python
# ⚠️ TODO: Get your SQL Warehouse ID
SQL_WAREHOUSE_ID = "your_warehouse_id"  

resources = [
    # ... other resources ...
    DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
]
```

**Why?** Genie spaces and UC functions require a SQL Warehouse to execute queries.

### 4. Table Resources

```python
# ⚠️ TODO: Query underlying tables
UNDERLYING_TABLES = [
    # Query: SELECT DISTINCT table_name FROM enriched_genie_docs_chunks
]

resources = [
    # ... other resources ...
    # Metadata enrichment table
    DatabricksTable(table_name=TABLE_NAME),
    # Underlying Genie tables
    *[DatabricksTable(table_name=table) for table in UNDERLYING_TABLES],
]
```

**Why?** The agent accesses:
- **Metadata table** (`enriched_genie_docs_chunks`) for metadata querying via UC functions
- **Underlying tables** that Genie spaces query (e.g., patient_demographics, clinical_trials)

## What You Need to Do Now

### Action 1: Find Your SQL Warehouse ID

**Method 1: From UI**
1. Go to: **SQL Warehouses** in Databricks
2. Click on the warehouse your Genie spaces use
3. Copy the **Warehouse ID** from:
   - URL: `...sql/warehouses/abc123...` → `abc123`
   - Or **Details** tab

**Method 2: From Genie Space Settings**
1. Go to **Genie** → Select a Genie space
2. **Settings** → **SQL Warehouse** → Note the name
3. Use Method 1 to get its ID

**Update in code:**
```python
# In deployment cell (Line ~2620)
SQL_WAREHOUSE_ID = "abc123def456"  # Your actual warehouse ID
```

### Action 2: Query Underlying Tables

Run this SQL query in Databricks SQL Editor:

```sql
SELECT DISTINCT table_name 
FROM yyang.multi_agent_genie.enriched_genie_docs_chunks
WHERE table_name IS NOT NULL
ORDER BY table_name;
```

**Example output:**
```
yyang.multi_agent_genie.patient_demographics
yyang.multi_agent_genie.clinical_trials
yyang.multi_agent_genie.medication_orders
```

**Update in code:**
```python
# In deployment cell (Line ~2625)
UNDERLYING_TABLES = [
    f"{CATALOG}.{SCHEMA}.patient_demographics",
    f"{CATALOG}.{SCHEMA}.clinical_trials",
    f"{CATALOG}.{SCHEMA}.medication_orders",
    # ... add all tables from your query
]
```

### Action 3: Use the Helper Cell (Optional)

I added a helper cell in `Super_Agent_hybrid.py` (Line ~2577) to help discover resources:

```python
# DBTITLE 1,Helper: Find All Required Resources for Deployment

# Uncomment and run to see:
# 1. All Genie Space IDs (from config)
# 2. SQL query to find underlying tables
# 3. Generated resources code snippet
```

This will generate the complete resources list for you!

### Action 4: Deploy

Once you've updated `SQL_WAREHOUSE_ID` and `UNDERLYING_TABLES`:

1. Scroll to deployment cell (Line ~2660)
2. Uncomment the deployment code
3. Run the cell
4. Wait ~15-20 minutes for deployment

## Updated Files

### 1. `Notebooks/Super_Agent_hybrid.py`

**Line ~2577:** Added helper cell to discover resources

**Line ~2583-2630:** Updated deployment resources to include:
- `DatabricksGenieSpace` for all Genie spaces
- `DatabricksSQLWarehouse` for SQL execution
- `DatabricksTable` for metadata and underlying tables
- TODOs for SQL_WAREHOUSE_ID and UNDERLYING_TABLES

### 2. `DEPLOYMENT_GUIDE.md`

Added new sections:

**Prerequisites: Find Required Resource IDs**
- How to find SQL Warehouse ID
- How to query underlying tables
- Why these are needed (Databricks documentation reference)

**Step 1: Prepare for Deployment**
- Updated to include resource discovery steps
- Added variables to update before deployment

**Troubleshooting**
- New troubleshooting entries for:
  - "Resource not found: SQL Warehouse"
  - "Permission denied accessing Genie Space"
  - "Permission denied accessing table"
  - "Missing downstream resources for Genie Space"

## Complete Resource List

Here's what your final `resources` list should include:

```python
resources = [
    # ✅ LLM Endpoints (5)
    DatabricksServingEndpoint(LLM_ENDPOINT_CLARIFICATION),
    DatabricksServingEndpoint(LLM_ENDPOINT_PLANNING),
    DatabricksServingEndpoint(LLM_ENDPOINT_SQL_SYNTHESIS),
    DatabricksServingEndpoint(LLM_ENDPOINT_SUMMARIZE),
    DatabricksServingEndpoint(EMBEDDING_ENDPOINT),
    
    # ✅ Lakebase (for state management) - CRITICAL!
    DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME),
    
    # ✅ Vector Search (1)
    DatabricksVectorSearchIndex(index_name=VECTOR_SEARCH_INDEX),
    
    # ✅ SQL Warehouse (1) - NEW!
    DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
    
    # ✅ Genie Spaces (3 from your .env) - NEW!
    *[DatabricksGenieSpace(genie_space_id=space_id) for space_id in GENIE_SPACE_IDS],
    
    # ✅ Tables (1 metadata + N underlying) - NEW!
    DatabricksTable(table_name=TABLE_NAME),  # Metadata
    *[DatabricksTable(table_name=table) for table in UNDERLYING_TABLES],
    
    # ✅ UC Functions (4)
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_summary"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_table_overview"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_column_detail"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.get_space_details"),
]
```

**Total Resources:** 
- 5 LLM endpoints
- 1 Lakebase instance
- 1 Vector Search index
- 1 SQL Warehouse
- 3 Genie Spaces (from your .env)
- 1+ Tables (metadata + underlying)
- 4 UC Functions

**Total: ~16+ resources** (depending on number of underlying tables)

## Why This Matters

### Without These Resources

❌ Deployment would fail or agent would encounter permission errors at runtime:
- "Permission denied accessing Genie Space"
- "Cannot access SQL Warehouse"
- "Table access denied"

### With These Resources

✅ Databricks automatically:
- Creates a service principal for your agent
- Grants read access to all declared resources
- Provisions and rotates short-lived credentials
- Enables seamless distributed serving

## Next Steps

1. ✅ **Find SQL Warehouse ID** (Action 1 above)
2. ✅ **Query underlying tables** (Action 2 above)
3. ✅ **Update deployment code** with both values
4. ✅ **Run helper cell** (optional, to verify)
5. ✅ **Deploy agent** (uncomment and run deployment cell)

## Reference

- **Databricks Documentation:** https://docs.databricks.com/aws/en/generative-ai/agent-framework/agent-authentication?language=Genie+Spaces+%28LangChain%29#automatic-authentication-passthrough
- **Deployment Guide:** `DEPLOYMENT_GUIDE.md`
- **Deployment Code:** `Notebooks/Super_Agent_hybrid.py` Line ~2577-2700

---

**Questions?** Check the updated `DEPLOYMENT_GUIDE.md` for detailed instructions and troubleshooting!
