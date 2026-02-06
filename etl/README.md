# ETL Pipeline

⚠️ **IMPORTANT**: Run ETL pipeline BEFORE developing/deploying agents. The agent system depends on the data prepared by ETL.

## What ETL Does

The ETL pipeline prepares enriched metadata and vector search index that agents use for semantic query routing:

1. **Export Genie Spaces** (`01_export_genie_spaces.py`)
   - Exports Genie space metadata to Unity Catalog volume
   - **Prerequisite**: Genie spaces must exist in your workspace

2. **Enrich Table Metadata** (`02_enrich_table_metadata.py`)
   - Enriches table metadata with samples, statistics, column details
   - Creates `enriched_genie_docs` table with comprehensive metadata

3. **Build Vector Search Index** (`03_build_vector_search_index.py`)
   - Creates vector search index from enriched metadata
   - Enables semantic search for agent planning

## Three ETL Workflows

### Workflow 1: Local ETL Testing 🧪

Test ETL transformations locally with sample data before running on full dataset.

```bash
# Test individual steps
python local_dev_etl.py --step export --sample-size 10
python local_dev_etl.py --step enrich --sample-size 10
python local_dev_etl.py --step vectorize --sample-size 10

# Test complete pipeline
python local_dev_etl.py --all --sample-size 10
```

**When to use**: 
- Developing new ETL logic
- Debugging transformations
- Validating changes before Databricks

**Time**: ~5 minutes

---

### Workflow 2: Databricks Testing (Small Sample) 🔬

Test on real Databricks services with small dataset to validate before production run.

```python
# In Databricks, run notebooks with test parameters:

# 01_export_genie_spaces.py
dbutils.widgets.text("sample_size", "10")
dbutils.widgets.text("test_mode", "True")

# 02_enrich_table_metadata.py
dbutils.widgets.text("sample_size", "10")
dbutils.widgets.text("test_mode", "True")

# 03_build_vector_search_index.py
dbutils.widgets.text("sample_size", "10")
dbutils.widgets.text("test_mode", "True")
```

**When to use**:
- Validating ETL works on Databricks before full run
- Testing with real Genie spaces, Unity Catalog, Vector Search
- Verifying permissions and access

**Time**: ~10-15 minutes

---

### Workflow 3: Production ETL 🚀

Run full ETL pipeline on complete dataset for production use.

```python
# In Databricks, run notebooks with production parameters:

# 01_export_genie_spaces.py
# (No sample_size - processes all Genie spaces)

# 02_enrich_table_metadata.py
# (No sample_size - enriches all tables)

# 03_build_vector_search_index.py
# (Builds full vector search index)
```

**When to use**:
- Production data preparation
- Scheduled jobs
- After testing completes successfully

**Time**: Varies by data size (typically 30-60 minutes)

## Execution Order

ETL scripts must be run **in order**:

```
1. export_genie_spaces
   ↓
2. enrich_table_metadata (depends on step 1)
   ↓
3. build_vector_search_index (depends on step 2)
   ↓
4. Agent development can begin ✅
```

## Prerequisites

Before running ETL:

- ✅ Databricks workspace with Genie spaces created
- ✅ Unity Catalog with catalog and schema created
- ✅ SQL Warehouse for querying metadata
- ✅ Vector Search endpoint created
- ✅ Appropriate permissions for Unity Catalog and Vector Search

## Expected Outputs

After successful ETL run, you should have:

- ✅ Genie spaces exported to UC volume: `{catalog}.{schema}.volume`
- ✅ Enriched metadata table: `{catalog}.{schema}.enriched_genie_docs`
- ✅ Enriched chunks table: `{catalog}.{schema}.enriched_genie_docs_chunks`
- ✅ Vector search index: `{catalog}.{schema}.enriched_genie_docs_chunks_vs_index`
- ✅ Agent system can now query semantically

## Files in This Directory

| File | Purpose | When to Run |
|------|---------|-------------|
| `local_dev_etl.py` | Local ETL testing | During development |
| `01_export_genie_spaces.py` | Export Genie metadata | Step 1 (Databricks) |
| `02_enrich_table_metadata.py` | Enrich table metadata | Step 2 (Databricks) |
| `03_build_vector_search_index.py` | Build vector index | Step 3 (Databricks) |
| `test_etl.py` | ETL integration tests | Validation |

## Configuration

ETL uses the same configuration as the agent system:
- **Databricks**: Uses `dev_config.yaml` or `prod_config.yaml`
- **Local**: Uses `config.py` + `.env`

Key configuration values:
- `CATALOG_NAME`: Unity Catalog catalog name
- `SCHEMA_NAME`: Schema name for tables
- `GENIE_SPACE_IDS`: List of Genie space IDs to process
- `SQL_WAREHOUSE_ID`: SQL Warehouse for queries
- `VS_ENDPOINT_NAME`: Vector Search endpoint name

## Troubleshooting

### Common Issues

**Issue**: `ModuleNotFoundError: No module named 'config'`
- **Solution**: Make sure you're running from the repository root, or add parent directory to path

**Issue**: `PermissionError: Access denied to Unity Catalog`
- **Solution**: Verify you have appropriate UC permissions

**Issue**: `Vector Search index not syncing`
- **Solution**: Check Vector Search endpoint is running and accessible

**Issue**: `Genie space not found`
- **Solution**: Verify Genie space IDs in configuration are correct

### Getting Help

For detailed troubleshooting:
- See [../docs/ETL_GUIDE.md](../docs/ETL_GUIDE.md) for comprehensive guide
- Check Databricks workspace logs
- Review Unity Catalog permissions

## Next Steps

After ETL completes successfully:

1. ✅ Verify outputs exist (tables and vector search index)
2. ✅ Test vector search index with sample query
3. ✅ Proceed to **Agent Development**:
   - [Local Development](../docs/LOCAL_DEVELOPMENT.md)
   - [Databricks Testing](../notebooks/README.md)
   - [Deployment](../docs/DEPLOYMENT.md)

---

**Remember**: ETL is a prerequisite. Agents cannot function without enriched metadata and vector search index! 🎯
