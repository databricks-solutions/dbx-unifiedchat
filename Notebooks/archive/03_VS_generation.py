# Databricks notebook source
# DBTITLE 1,RUNONCE
# %sh
# cp -pr /Workspace/Users/samuel.santos@databricks.com/hackathon ./hackathon

# COMMAND ----------

# MAGIC %pip install databricks-vectorsearch
# MAGIC %restart_python

# COMMAND ----------

import os
import time
from databricks.vector_search.client import VectorSearchClient

#: Databricks notebook widgets for parameterization
# catalog/schema/table refers to source table.

dbutils.widgets.removeAll()

dbutils.widgets.text("catalog_name", os.getenv("ETL_CATALOG_NAME", "yyang"))
dbutils.widgets.text("schema_name", os.getenv("ETL_SCHEMA_NAME", "csv_health"))
dbutils.widgets.text("table_name", os.getenv("ETL_QUESTIONS_TABLE_NAME", "questions_table"))
dbutils.widgets.text("vs_endpoint_name", os.getenv("ETL_VS_ENDPOINT_NAME", "cvs_health_poc"))

catalog_name = dbutils.widgets.get("catalog_name")
schema_name = dbutils.widgets.get("schema_name")
table_name = dbutils.widgets.get("table_name")
vs_endpoint_name = dbutils.widgets.get("vs_endpoint_name")

# Get optional parameters from environment variables
embedding_model = os.getenv("ETL_EMBEDDING_MODEL", "databricks-gte-large-en")
pipeline_type = os.getenv("ETL_PIPELINE_TYPE", "Continuous")

# Construct fully qualified table name
source_table_name = f"{catalog_name}.{schema_name}.{table_name}"

# Retrieve detailed table description (includes columns, data types, comments, etc.)
df_description = spark.sql(
    f"DESCRIBE EXTENDED {source_table_name}"
)
# Display the metadata
display(df_description)

# COMMAND ----------

# DBTITLE 1,create vs endpoint if not existed
client = VectorSearchClient()
index_name = source_table_name.replace("questions_table", "questions_table_vs")

vs_endpoint_name = f"vs_endpoint_{vs_endpoint_name}".lower()[:49]

# Check if endpoint exists
endpoints = client.list_endpoints()['endpoints']
endpoint_names = [ep['name'] for ep in endpoints]

if vs_endpoint_name in endpoint_names:
    print(f"VS endpoint '{vs_endpoint_name}' already exists.")
else:
    client.create_endpoint(name=vs_endpoint_name, endpoint_type="STANDARD")
    print(f"Created VS endpoint '{vs_endpoint_name}'.")

# COMMAND ----------

spark.sql(f"ALTER TABLE {source_table_name} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

# COMMAND ----------

# DBTITLE 1,use above created vs endpoint
client.wait_for_endpoint(vs_endpoint_name, "READY")

index = client.create_delta_sync_index(
    endpoint_name=vs_endpoint_name,
    source_table_name=source_table_name,
    index_name=index_name,
    pipeline_type=pipeline_type,
    primary_key="id",
    embedding_source_column="query",
    embedding_model_endpoint_name=embedding_model
)

# COMMAND ----------

# DBTITLE 1,(optional) Alternatively, use a default vs endpoint existed in your wksp
# default_vs_endpoint_name = "dbdemos_vs_endpoint"
# client.wait_for_endpoint(default_vs_endpoint_name, "READY")

# index = client.create_delta_sync_index(
#     endpoint_name=default_vs_endpoint_name,
#     source_table_name=source_table_name,
#     index_name=index_name,
#     pipeline_type=pipeline_type,
#     primary_key="id",
#     embedding_source_column="query",
#     embedding_model_endpoint_name=embedding_model
# )

# COMMAND ----------

# Wait for index to come online. Expect this command to take several minutes.
while not index.describe().get('status').get('detailed_state').startswith('ONLINE'):
  print("Waiting for index to be ONLINE...")
  time.sleep(5)
print("Index is ONLINE")
index.describe()

# COMMAND ----------

spark.sql(f"""select * from vector_search(
    index => '{index_name}',
    query => 'top 10 drugs with rebate, caculate average',
    num_results => 10)"""
    ).display()

# COMMAND ----------

# MAGIC %md
# MAGIC TODO: 
# MAGIC 1. diversify the similarity search returns, reduce those with minor variations, e.g., only variating in year. Pop up those in the bottom but more distinct.
# MAGIC 2. rerank according to user profile and chatting history

# COMMAND ----------

# MAGIC %md
# MAGIC # (optional) Query an existing index

# COMMAND ----------

vsc = VectorSearchClient()

# COMMAND ----------

index_name = source_table_name.replace("questions_table", "questions_table_vs")

# COMMAND ----------

index = vsc.get_index(index_name=index_name)

index.describe()

# COMMAND ----------

# Wait for index to come online. Expect this command to take several minutes.
while not index.describe().get('status').get('detailed_state').startswith('ONLINE'):
  print("Waiting for index to be ONLINE...")
  time.sleep(5)
print("Index is ONLINE")
index.describe()

# COMMAND ----------

results = index.similarity_search(
  query_text="What is top 10 customers with highest rebate",
  columns="query",
  num_results=5)

results
