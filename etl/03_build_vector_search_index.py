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

# Ensure catalog/schema context is set (each serverless task is a separate session)
try:
    spark.sql(f"CREATE CATALOG IF NOT EXISTS `{catalog_name}`")
except Exception:
    pass
spark.sql(f"USE CATALOG `{catalog_name}`")
try:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{schema_name}`")
except Exception:
    pass
spark.sql(f"USE SCHEMA `{schema_name}`")
print(f"✓ Using {catalog_name}.{schema_name}")

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
vs_endpoint_name = f"{vs_endpoint_name}".lower()[:49]

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
        
        if detailed_state.startswith('ONLINE_NO_PENDING_UPDATE'):
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

# DBTITLE 1,(optional) after restart, reload index
client = VectorSearchClient()
# index_name = "yyang.multi_agent_genie.enriched_genie_docs_chunks_vs_index"
vs_index = client.get_index(index_name=index_name)

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

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC ### For now, we dont have to use VS Tool as UCFunction, because later we will use the VS retriever tool `VectorSearchRetrieverTool` as
# MAGIC ```
# MAGIC vs_tool = VectorSearchRetrieverTool(
# MAGIC   index_name="catalog.schema.my_databricks_docs_index",
# MAGIC   tool_name="databricks_docs_retriever",
# MAGIC   tool_description="Retrieves information about Databricks products from official Databricks documentation."
# MAGIC )
# MAGIC ```
# MAGIC
# MAGIC ref: [https://docs.databricks.com/aws/en/generative-ai/agent-framework/unstructured-retrieval-tools](https://docs.databricks.com/aws/en/generative-ai/agent-framework/unstructured-retrieval-tools)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Register Vector Search Tool as UC Function (Optional)
# MAGIC
# MAGIC This creates a Unity Catalog function that can be called by agents.
# MAGIC
# MAGIC 1. we cannot use pure SQL function in UC function cause it doesnt support in-function filter in `vector_search()`
# MAGIC 2. we thus have to wrap python function in UC function to support in-function filter.
# MAGIC

# COMMAND ----------

# DBTITLE 1,untested template
# import pandas as pd
# from databricks.vector_search.client import VectorSearchClient
# from pyspark.sql.functions import pandas_udf
# from pyspark.sql.types import ArrayType, StructType, StructField, StringType, MapType

# INDEX_NAME = index_name  # Use your UC index name

# schema = ArrayType(
#     StructType([
#         StructField("page_content", StringType()),
#         StructField("metadata", MapType(StringType(), StringType()))
#     ])
# )

# @pandas_udf(schema)
# def vs_search_with_filters_udf(
#     query_text: pd.Series,
#     filter_str: pd.Series,
#     num_results: pd.Series
# ) -> pd.Series:
#     vsc = VectorSearchClient()
#     idx = vsc.get_index(index_name=INDEX_NAME)
#     results = []
#     for q, f, n in zip(query_text, filter_str, num_results):
#         kwargs = {
#             "query_text": q,
#             "num_results": int(n) if pd.notnull(n) else 10,
#             "columns": ["id", "text", "url", "chunk_id"]
#         }
#         if pd.notnull(f) and str(f).strip():
#             kwargs["filters"] = f
#         res = idx.similarity_search(query_type="hybrid", **kwargs)
#         out = []
#         for item in res.get("data", []):
#             text = item.get("text", "")
#             url = item.get("url", "")
#             chunk_id = str(item.get("chunk_id", ""))
#             score = str(item.get("score", ""))
#             meta = {
#                 "doc_uri": url,
#                 "chunk_id": chunk_id,
#                 "similarity_score": score
#             }
#             out.append((text, meta))
#         results.append(out)
#     return pd.Series(results)


##:----testing-------------------
# # Example input DataFrame
# input_data = [
#     ("patient age and demographics information", '{\\"chunk_type\\": \\"column_detail\\"}', 5),
#     ("medication prescriptions", '{\\"chunk_type\\": \\"table_overview\\"}', 3)
# ]
# input_df = spark.createDataFrame(
#     input_data,
#     ["query_text", "filter_str", "num_results"]
# )

# # Apply the pandas UDF
# result_df = input_df.withColumn(
#     "search_results",
#     vs_search_with_filters_udf(
#         input_df["query_text"],
#         input_df["filter_str"],
#         input_df["num_results"]
#     )
# )

# display(result_df)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC
# MAGIC This notebook has:
# MAGIC 1. ✓ Created a vector search endpoint
# MAGIC 2. ✓ Built a managed Delta Sync vector search index on multi-level chunks
# MAGIC 3. ✓ Tested semantic search with metadata filtering across all chunk types:
# MAGIC    - **Space Summary**: Overview of available spaces and tables
# MAGIC    - **Table Overview**: Column lists and table structure
# MAGIC    - **Column Detail**: Full descriptions, sample values, value dictionaries
# MAGIC
# MAGIC **Key Outputs:**
# MAGIC - Vector Search Index: `{index_name}`
# MAGIC
# MAGIC **Metadata Filters Available:**
# MAGIC - `chunk_type`: Filter by granularity (space_summary, table_overview, column_detail)
# MAGIC - `table_name`, `column_name`: Filter to specific schema objects
# MAGIC - `is_categorical`, `is_temporal`, `is_identifier`: Filter by column characteristics
# MAGIC - `has_value_dictionary`: Find columns with enumerated value sets
# MAGIC
# MAGIC **Next Steps:**
# MAGIC - Use the `VectorSearchRetrieverTool` for retrieval tasks (no need to register as a Unity Catalog function)
# MAGIC - Build a multi-agent system that leverages this index (`05_Multi_Agent_System.py`)
