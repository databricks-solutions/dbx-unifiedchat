# Databricks notebook source
# MAGIC %md
# MAGIC # Vector Search Index for Enriched Genie Spaces
# MAGIC 
# MAGIC This notebook creates a vector search index on enriched Genie space metadata.
# MAGIC The index enables semantic search to find relevant Genie spaces for user questions.

# COMMAND ----------

# MAGIC %pip install -U databricks-vectorsearch
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import os
import time
from databricks.vector_search.client import VectorSearchClient

# COMMAND ----------

# DBTITLE 1,Setup Parameters

dbutils.widgets.removeAll()

dbutils.widgets.text("catalog_name", os.getenv("CATALOG_NAME", "yyang"))
dbutils.widgets.text("schema_name", os.getenv("SCHEMA_NAME", "multi_agent_genie"))
dbutils.widgets.text("source_table", os.getenv("SOURCE_TABLE", "enriched_genie_docs_chunks"))
dbutils.widgets.text("vs_endpoint_name", os.getenv("VS_ENDPOINT_NAME", "genie_multi_agent_vs"))
dbutils.widgets.text("embedding_model", os.getenv("EMBEDDING_MODEL", "databricks-gte-large-en"))
dbutils.widgets.text("pipeline_type", os.getenv("PIPELINE_TYPE", "TRIGGERED"))

catalog_name = dbutils.widgets.get("catalog_name")
schema_name = dbutils.widgets.get("schema_name")
source_table = dbutils.widgets.get("source_table")
vs_endpoint_name = dbutils.widgets.get("vs_endpoint_name")
embedding_model = dbutils.widgets.get("embedding_model")
pipeline_type = dbutils.widgets.get("pipeline_type")

# Construct fully qualified table names
source_table_name = f"{catalog_name}.{schema_name}.{source_table}"
index_name = f"{catalog_name}.{schema_name}.{source_table}_vs_index"

print(f"Source Table: {source_table_name}")
print(f"VS Endpoint: {vs_endpoint_name}")
print(f"Index Name: {index_name}")
print(f"Embedding Model: {embedding_model}")
print(f"Pipeline Type: {pipeline_type}")
print("\nNote: Using multi-level chunks table with space_summary, table_overview, and column_detail chunks")

# COMMAND ----------

# DBTITLE 1,Verify Source Table

# Check if source table exists
try:
    df_source = spark.table(source_table_name)
    print(f"✓ Source table exists with {df_source.count()} records")
    display(df_source.limit(5))
except Exception as e:
    raise Exception(f"Source table {source_table_name} not found. Please run 02_Table_MetaInfo_Enrichment.py first.") from e

# COMMAND ----------

# DBTITLE 1,Enable Change Data Feed

# Enable CDC for delta sync
try:
    spark.sql(f"ALTER TABLE {source_table_name} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
    print(f"✓ Enabled Change Data Feed on {source_table_name}")
except Exception as e:
    print(f"Note: {str(e)}")

# COMMAND ----------

# DBTITLE 1,Create or Get Vector Search Endpoint

client = VectorSearchClient()

# Format endpoint name (lowercase, max 49 chars)
vs_endpoint_name = f"vs_endpoint_{vs_endpoint_name}".lower()[:49]

# Check if endpoint exists
try:
    endpoints = client.list_endpoints().get('endpoints', [])
    endpoint_names = [ep['name'] for ep in endpoints]
    
    if vs_endpoint_name in endpoint_names:
        print(f"✓ VS endpoint '{vs_endpoint_name}' already exists")
        endpoint = client.get_endpoint(vs_endpoint_name)
    else:
        print(f"Creating VS endpoint '{vs_endpoint_name}'...")
        endpoint = client.create_endpoint(
            name=vs_endpoint_name, 
            endpoint_type="STANDARD"
        )
        print(f"✓ Created VS endpoint '{vs_endpoint_name}'")
except Exception as e:
    print(f"Error with endpoint: {str(e)}")
    raise

# COMMAND ----------

# DBTITLE 1,Wait for Endpoint to be Ready

print(f"Waiting for endpoint '{vs_endpoint_name}' to be ready...")
client.wait_for_endpoint(vs_endpoint_name, "ONLINE")
print(f"✓ Endpoint '{vs_endpoint_name}' is online and ready")

# COMMAND ----------

# DBTITLE 1,Create Delta Sync Vector Search Index

print(f"Creating vector search index: {index_name}")
print(f"  Source: {source_table_name}")
print(f"  Embedding column: searchable_content")
print(f"  Primary key: chunk_id")
print(f"  Embedding model: {embedding_model}")

try:
    # Check if index already exists
    try:
        existing_index = client.get_index(index_name=index_name)
        print(f"Index '{index_name}' already exists. Deleting and recreating...")
        client.delete_index(index_name=index_name)
        time.sleep(5)  # Wait for deletion to complete
    except Exception:
        print(f"Index does not exist, creating new...")
    
    # Create new index with metadata filters
    index = client.create_delta_sync_index(
        endpoint_name=vs_endpoint_name,
        source_table_name=source_table_name,
        index_name=index_name,
        pipeline_type=pipeline_type,
        primary_key="chunk_id",
        embedding_source_column="searchable_content",
        embedding_model_endpoint_name=embedding_model
    )
    
    print(f"✓ Vector search index creation initiated: {index_name}")
    print(f"  Metadata fields available for filtering:")
    print(f"    - chunk_type (space_summary, table_overview, column_detail)")
    print(f"    - table_name, column_name")
    print(f"    - is_categorical, is_temporal, is_identifier, has_value_dictionary")
    
except Exception as e:
    print(f"Error creating index: {str(e)}")
    raise

# COMMAND ----------

# DBTITLE 1,Wait for Index to be Online

print("Waiting for index to be ONLINE...")
max_wait_time = 600  # 10 minutes
start_time = time.time()

while time.time() - start_time < max_wait_time:
    try:
        index_status = index.describe()
        detailed_state = index_status.get('status', {}).get('detailed_state', '')
        
        print(f"  Current state: {detailed_state}")
        
        if detailed_state.startswith('ONLINE'):
            print(f"✓ Index is ONLINE and ready to use!")
            break
        elif 'FAILED' in detailed_state:
            print(f"✗ Index creation failed: {detailed_state}")
            print(f"Full status: {index_status}")
            raise Exception(f"Index creation failed: {detailed_state}")
        
        time.sleep(10)
    except Exception as e:
        if time.time() - start_time >= max_wait_time:
            raise Exception(f"Timeout waiting for index to be online: {str(e)}")
        time.sleep(10)

print("\nIndex Details:")
display(index.describe())

# COMMAND ----------

# DBTITLE 1,Test Vector Search with Multi-Level Chunks

print("\n" + "="*80)
print("Testing Vector Search with Multi-Level Chunks")
print("="*80)

# Get the index for Python SDK queries
vs_index = client.get_index(index_name=index_name)

# Test 1: General queries across all chunk types
print("\n" + "="*80)
print("Test 1: General Semantic Search (All Chunk Types)")
print("="*80)

test_queries = [
    "patient age and demographics information",
    "medication prescriptions and drug information",
    "cancer diagnosis and staging"
]

for query in test_queries:
    print(f"\nQuery: {query}")
    print("-" * 80)
    
    try:
        # Use Python SDK similarity_search
        results = vs_index.similarity_search(
            query_text=query,
            columns=["chunk_id", "chunk_type", "space_title", "table_name", "column_name"],
            num_results=5
        )
        
        # Extract result data
        result_data = results.get('result', {})
        manifest = results.get('manifest', {})
        data_array = result_data.get('data_array', [])
        
        # Convert to DataFrame for display (score is included in manifest)
        if len(data_array) > 0:
            result_df = spark.createDataFrame(data_array, schema= [col.get('name') if isinstance(col, dict) else str(col) for col in  manifest.get('columns', [])] )
            display(result_df)
        else:
            print("No results found")
    except Exception as e:
        print(f"Error searching: {str(e)}")

# Test 2: Space-level queries (filtered to space_summary chunks)
print("\n" + "="*80)
print("Test 2: Space Discovery (Space Summary Chunks Only)")
print("="*80)

space_queries = [
    "What data is available for patient claims analysis?",
    "What tables contain medical claims information?"
]

for query in space_queries:
    print(f"\nQuery: {query}")
    print("-" * 80)
    
    try:
        # Use Python SDK with filters parameter (dict syntax for standard endpoints)
        results = vs_index.similarity_search(
            query_text=query,
            columns=["chunk_id", "chunk_type", "space_title"],
            filters={"chunk_type": "space_summary"},
            num_results=3
        )
        
        # Extract result data
        result_data = results.get('result', {})
        manifest = results.get('manifest', {})
        data_array = result_data.get('data_array', [])
        
        # Convert to DataFrame for display (score is included in manifest)
        if len(data_array) > 0:
            result_df = spark.createDataFrame(data_array, schema= [col.get('name') if isinstance(col, dict) else str(col) for col in  manifest.get('columns', [])] )
            display(result_df)
        else:
            print("No results found")
    except Exception as e:
        print(f"Error searching: {str(e)}")

# Test 3: Table-level queries (filtered to table_overview chunks)
print("\n" + "="*80)
print("Test 3: Table Selection (Table Overview Chunks Only)")
print("="*80)

table_queries = [
    "What tables have date fields for temporal analysis?",
    "Which tables contain patient demographics?"
]

for query in table_queries:
    print(f"\nQuery: {query}")
    print("-" * 80)
    
    try:
        # Use Python SDK with filters parameter (dict syntax for standard endpoints)
        results = vs_index.similarity_search(
            query_text=query,
            columns=["chunk_id", "chunk_type", "space_title", "table_name", "is_temporal"],
            filters={"chunk_type": "table_overview"},
            num_results=5
        )
        
        # Extract result data
        result_data = results.get('result', {})
        manifest = results.get('manifest', {})
        data_array = result_data.get('data_array', [])
        
        # Convert to DataFrame for display (score is included in manifest)
        if len(data_array) > 0:
            result_df = spark.createDataFrame(data_array, schema= [col.get('name') if isinstance(col, dict) else str(col) for col in  manifest.get('columns', [])] )
            display(result_df)
        else:
            print("No results found")
    except Exception as e:
        print(f"Error searching: {str(e)}")

# Test 4: Column-level queries with metadata filters
print("\n" + "="*80)
print("Test 4: Column Discovery with Metadata Filters")
print("="*80)

# Find categorical columns
print("\nFind categorical columns with valid value sets:")
try:
    # Use Python SDK with multiple filter conditions (dict syntax for standard endpoints)
    results = vs_index.similarity_search(
        query_text="location or place of service",
        columns=["chunk_id", "table_name", "column_name"],
        filters={"chunk_type": "column_detail", "has_value_dictionary": True},
        num_results=5
    )
    result_data = results.get('result', {})
    manifest = results.get('manifest', {})
    data_array = result_data.get('data_array', [])
    if len(data_array) > 0:
        result_df = spark.createDataFrame(data_array, schema= [col.get('name') if isinstance(col, dict) else str(col) for col in  manifest.get('columns', [])] )
        display(result_df)
    else:
        print("No results found")
except Exception as e:
    print(f"Error searching: {str(e)}")

# Find identifier columns
print("\nFind patient identifier columns:")
try:
    results = vs_index.similarity_search(
        query_text="patient identifier or patient id",
        columns=["chunk_id", "table_name", "column_name", "is_identifier"],
        filters={"chunk_type": "column_detail", "is_identifier": True},
        num_results=5
    )
    result_data = results.get('result', {})
    manifest = results.get('manifest', {})
    data_array = result_data.get('data_array', [])
    if len(data_array) > 0:
        result_df = spark.createDataFrame(data_array, schema= [col.get('name') if isinstance(col, dict) else str(col) for col in  manifest.get('columns', [])] )
        display(result_df)
    else:
        print("No results found")
except Exception as e:
    print(f"Error searching: {str(e)}")

# Find temporal columns
print("\nFind date/time columns:")
try:
    results = vs_index.similarity_search(
        query_text="service date or claim date",
        columns=["chunk_id", "table_name", "column_name", "is_temporal"],
        filters={"chunk_type": "column_detail", "is_temporal": True},
        num_results=5
    )
    result_data = results.get('result', {})
    manifest = results.get('manifest', {})
    data_array = result_data.get('data_array', [])
    if len(data_array) > 0:
        result_df = spark.createDataFrame(data_array, schema= [col.get('name') if isinstance(col, dict) else str(col) for col in  manifest.get('columns', [])] )
        display(result_df)
    else:
        print("No results found")
except Exception as e:
    print(f"Error searching: {str(e)}")



