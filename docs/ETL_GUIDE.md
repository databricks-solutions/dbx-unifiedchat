# ETL Pipeline - Detailed Guide

Comprehensive guide for the ETL (Extract, Transform, Load) pipeline that prepares data for the multi-agent system.

## Overview

The ETL pipeline enriches Genie space metadata with table schemas, column details, and sample data, then builds a vector search index for semantic retrieval. This is a **prerequisite** for the agent system to function.

## Pipeline Stages

### Stage 1: Export Genie Spaces

**Script**: `etl/01_export_genie_spaces.py`

**What it does**:
- Connects to Databricks Genie API
- Exports metadata for all configured Genie spaces
- Saves to Unity Catalog volume

**Outputs**:
- JSON files in `{catalog}.{schema}.volume/genie_exports/`
- One file per Genie space

**Configuration**:
- `GENIE_SPACE_IDS`: List of space IDs to export
- `GENIE_EXPORTS_VOLUME`: UC volume path

**Runtime**: ~5-10 minutes (depends on number of spaces)

### Stage 2: Enrich Table Metadata

**Script**: `etl/02_enrich_table_metadata.py`

**What it does**:
- Reads Genie space exports
- Queries Unity Catalog for table schemas
- Extracts sample data and statistics
- Calculates column distributions
- Creates enriched documentation chunks

**Outputs**:
- `{catalog}.{schema}.enriched_genie_docs`: Enriched metadata table
- `{catalog}.{schema}.enriched_genie_docs_chunks`: Chunked documents for vector search

**Configuration**:
- `SAMPLE_SIZE`: Number of sample rows per table (default: 100)
- `MAX_UNIQUE_VALUES`: Max unique values to capture per column (default: 50)
- `SQL_WAREHOUSE_ID`: SQL Warehouse for querying

**Runtime**: ~15-30 minutes (depends on table sizes)

### Stage 3: Build Vector Search Index

**Script**: `etl/03_build_vector_search_index.py`

**What it does**:
- Creates vector search index from enriched chunks
- Configures embedding endpoint
- Syncs index for querying

**Outputs**:
- `{catalog}.{schema}.enriched_genie_docs_chunks_vs_index`: Vector search index

**Configuration**:
- `VS_ENDPOINT_NAME`: Vector search endpoint
- `EMBEDDING_MODEL`: Embedding model (e.g., databricks-gte-large-en)
- `PIPELINE_TYPE`: TRIGGERED or CONTINUOUS

**Runtime**: ~10-20 minutes (includes index sync)

## Three ETL Workflows

### 1. Local ETL Testing

**Purpose**: Test ETL logic locally before running on Databricks

**Setup**:
```bash
# Install ETL dependencies
pip install -r requirements.txt

# Run local ETL tester
python etl/local_dev_etl.py --step export --sample-size 10
python etl/local_dev_etl.py --step enrich --sample-size 10
python etl/local_dev_etl.py --step vectorize --sample-size 10

# Or run all steps
python etl/local_dev_etl.py --all --sample-size 10
```

**What gets tested**:
- Data transformation logic
- Schema validation
- Sample data processing
- Error handling

**What doesn't work locally**:
- Actual Genie API calls (mocked)
- Vector search index creation (mocked)
- Unity Catalog writes (uses local files)

**Use cases**:
- Developing new ETL logic
- Testing transformations
- Validating changes before Databricks

### 2. Databricks ETL Testing

**Purpose**: Test ETL on real Databricks services with small dataset

**Setup**:
```python
# In each ETL notebook, set test parameters:
dbutils.widgets.text("test_mode", "True")
dbutils.widgets.text("sample_size", "10")
dbutils.widgets.text("max_spaces", "3")

# Run notebooks in order
```

**What gets tested**:
- Real Genie API calls
- Real Unity Catalog access
- Real Vector Search
- Small dataset (fast validation)

**Use cases**:
- Validating ETL on Databricks
- Testing permissions and access
- Quick iteration before production run

### 3. Production ETL

**Purpose**: Run complete ETL pipeline on full dataset

**Setup**:
```python
# In each ETL notebook, set production parameters:
# (Remove or set to False)
dbutils.widgets.text("test_mode", "False")
# (Remove sample_size to process all data)

# Run notebooks in order
```

**What runs**:
- All Genie spaces
- All tables
- Full enrichment
- Complete vector index

**Use cases**:
- Production data preparation
- Scheduled jobs
- After testing validates successfully

## Scheduling ETL

### Option 1: Databricks Jobs

Create a job with three tasks:

```yaml
name: "Multi-Agent ETL Pipeline"
schedule:
  quartz_cron_expression: "0 0 2 * * ?"  # 2 AM daily
  timezone_id: "UTC"
tasks:
  - task_key: "export"
    notebook_task:
      notebook_path: "/Workspace/etl/01_export_genie_spaces"
  - task_key: "enrich"
    depends_on: ["export"]
    notebook_task:
      notebook_path: "/Workspace/etl/02_enrich_table_metadata"
  - task_key: "vectorize"
    depends_on: ["enrich"]
    notebook_task:
      notebook_path: "/Workspace/etl/03_build_vector_search_index"
```

### Option 2: Databricks Workflows

Use Databricks Workflows UI:
1. Create new workflow
2. Add three tasks (export, enrich, vectorize)
3. Set dependencies
4. Configure schedule
5. Enable alerts

## ETL Configuration

ETL uses the same configuration as the agent system:

### For Databricks ETL

Uses `dev_config.yaml` or `prod_config.yaml`:
```yaml
# Genie Configuration
genie_space_ids:
  - space_id_1
  - space_id_2

# Table Metadata
sample_size: 100
max_unique_values: 50
sql_warehouse_id: warehouse_id

# Vector Search
vs_endpoint_name: endpoint_name
embedding_model: databricks-gte-large-en
```

### For Local ETL

Uses `config.py` + `.env`:
```bash
GENIE_SPACE_IDS=space_id_1,space_id_2
SAMPLE_SIZE=100
MAX_UNIQUE_VALUES=50
SQL_WAREHOUSE_ID=warehouse_id
```

## Monitoring ETL

### Check Progress

```sql
-- Monitor enrichment progress
SELECT 
  chunk_type,
  COUNT(*) as count,
  MAX(timestamp) as last_update
FROM {catalog}.{schema}.enriched_genie_docs_chunks
GROUP BY chunk_type;

-- Check specific Genie space
SELECT * 
FROM {catalog}.{schema}.enriched_genie_docs_chunks
WHERE space_id = 'your_space_id'
LIMIT 10;
```

### Vector Search Index Status

```python
from databricks.vector_search.client import VectorSearchClient

client = VectorSearchClient()
index = client.get_index(
    endpoint_name="your_endpoint",
    index_name=f"{catalog}.{schema}.enriched_genie_docs_chunks_vs_index"
)

print(f"Index status: {index.describe()}")
```

## Troubleshooting

### Common Issues

**Issue**: Genie space not accessible
- **Solution**: Verify space ID is correct and you have access

**Issue**: SQL Warehouse not found
- **Solution**: Check `SQL_WAREHOUSE_ID` in configuration

**Issue**: Vector Search endpoint not found
- **Solution**: Create Vector Search endpoint in Databricks UI

**Issue**: Unity Catalog permission denied
- **Solution**: Verify you have CREATE TABLE permission on schema

**Issue**: Enrichment taking too long
- **Solution**: Reduce `SAMPLE_SIZE` for testing, or use smaller table subset

### Performance Optimization

1. **Parallel processing**: Process multiple tables in parallel
2. **Sample size**: Start small (10-20) for testing, increase for production
3. **Incremental updates**: Update only changed tables
4. **Index sync**: Use TRIGGERED pipeline for controlled syncing

## Data Quality

### Validation Checks

After ETL, validate:

```sql
-- Check all Genie spaces are represented
SELECT DISTINCT space_id, space_name 
FROM {catalog}.{schema}.enriched_genie_docs;

-- Check chunk types distribution
SELECT chunk_type, COUNT(*) 
FROM {catalog}.{schema}.enriched_genie_docs_chunks 
GROUP BY chunk_type;

-- Verify sample data exists
SELECT COUNT(*) 
FROM {catalog}.{schema}.enriched_genie_docs_chunks 
WHERE chunk_type = 'sample_data';
```

### Quality Metrics

Monitor these metrics:
- Number of Genie spaces processed
- Number of tables enriched
- Number of chunks created
- Vector search index size
- Sample data coverage

## Next Steps

After ETL completes successfully:

1. ✅ Verify all outputs exist (tables and vector index)
2. ✅ Test vector search: Query index with sample query
3. ✅ Proceed to agent development:
   - [Local Development](LOCAL_DEVELOPMENT.md)
   - [Databricks Testing](../notebooks/README.md)
   - [Deployment](DEPLOYMENT.md)

---

**ETL is critical!** Make sure it completes successfully before developing agents. 🎯
