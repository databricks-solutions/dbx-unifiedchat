# Databricks notebook source
# MAGIC %md
# MAGIC # Table Metadata Update and Enrichment Pipeline
# MAGIC 
# MAGIC This notebook enriches Genie space metadata with detailed table information:
# MAGIC 1. Samples column values from delta tables
# MAGIC 2. Builds value dictionaries for columns
# MAGIC 3. Enriches parsed docs from Genie space.json exports
# MAGIC 4. Saves enriched docs to Unity Catalog delta table
# MAGIC 5. TODO: remove substring contraints from space/table/column content to enable richer information there.

# COMMAND ----------

# MAGIC %pip install -U databricks-sdk
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import os
import json
import pandas as pd
from datetime import datetime
from pyspark.sql import functions as F
from pyspark.sql.functions import col, collect_set, count, lit
from typing import Dict, List, Any

# COMMAND ----------

# DBTITLE 1,Setup Parameters
dbutils.widgets.removeAll()

dbutils.widgets.text("catalog_name", os.getenv("CATALOG_NAME", "yyang"))
dbutils.widgets.text("schema_name", os.getenv("SCHEMA_NAME", "multi_agent_genie"))
dbutils.widgets.text("genie_exports_volume", os.getenv("GENIE_EXPORTS_VOLUME", "yyang.multi_agent_genie.volume"))
dbutils.widgets.text("enriched_docs_table", os.getenv("ENRICHED_DOCS_TABLE", "yyang.multi_agent_genie.enriched_genie_docs"))
dbutils.widgets.text("llm_endpoint", os.getenv("LLM_ENDPOINT", "databricks-claude-sonnet-4-5"))
dbutils.widgets.text("sample_size", os.getenv("SAMPLE_SIZE", "20"))
dbutils.widgets.text("max_unique_values", os.getenv("MAX_UNIQUE_VALUES", "20"))

catalog_name = dbutils.widgets.get("catalog_name")
schema_name = dbutils.widgets.get("schema_name")
genie_exports_volume = dbutils.widgets.get("genie_exports_volume")
enriched_docs_table = dbutils.widgets.get("enriched_docs_table")
llm_endpoint = dbutils.widgets.get("llm_endpoint")
sample_size = int(dbutils.widgets.get("sample_size"))
max_unique_values = int(dbutils.widgets.get("max_unique_values"))

print(f"Catalog: {catalog_name}")
print(f"Schema: {schema_name}")
print(f"Genie Exports Volume: {genie_exports_volume}")
print(f"Enriched Docs Table: {enriched_docs_table}")
print(f"LLM Endpoint: {llm_endpoint}")

# COMMAND ----------

# DBTITLE 1,Helper Functions

def get_table_metadata(table_identifier: str) -> pd.DataFrame:
    """
    Get table metadata including columns, data types, and comments.
    
    Args:
        table_identifier: Fully qualified table name (catalog.schema.table)
    
    Returns:
        Pandas DataFrame with column metadata
    """
    try:
        df_description = spark.sql(f"DESCRIBE EXTENDED {table_identifier}")
        
        # Filter out metadata rows, keep only actual columns
        # Look for the separator line that marks the end of column definitions
        df_clean = df_description.filter(
            (col('data_type').isNotNull()) & 
            (~col('col_name').startswith('#')) & 
            (col('col_name') != '') & 
            (col('data_type') != '') &
            (~col('col_name').isin(
                '# Delta Statistics Columns', 'Column Names', 'Column Selection Method', 
                'Created Time', 'Last Access', 'Created By', 'Statistics', 'Type', 
                'Location', 'Provider', 'Owner', 'Is_managed_location', 
                'Predictive Optimization', 'Table Properties', 'Catalog', 'Database', 
                'Table', '# Detailed Table Information', 'Name', 'Comment', 
                '# Partitioning', 'Part 0', 'Partition Provider'
            ))
        )
        
        return df_clean.toPandas()
    except Exception as e:
        print(f"Error getting metadata for {table_identifier}: {str(e)}")
        return pd.DataFrame(columns=['col_name', 'data_type', 'comment'])


def sample_column_values(table_identifier: str, column_name: str, sample_size: int = 100) -> List[Any]:
    """
    Sample distinct values from a column.
    
    Args:
        table_identifier: Fully qualified table name
        column_name: Column to sample from
        sample_size: Number of samples to retrieve
    
    Returns:
        List of sampled values (converted to JSON-serializable types)
    """
    try:
        query = f"""
        SELECT DISTINCT `{column_name}` 
        FROM {table_identifier} 
        WHERE `{column_name}` IS NOT NULL 
        LIMIT {sample_size}
        """
        result = spark.sql(query).collect()
        # Convert values to JSON-serializable types
        sampled_values = []
        for row in result:
            val = row[0]
            # Convert date/datetime objects to strings
            if hasattr(val, 'isoformat'):
                sampled_values.append(val.isoformat())
            else:
                sampled_values.append(val)
        return sampled_values
    except Exception as e:
        print(f"Error sampling {column_name} from {table_identifier}: {str(e)}")
        return []


def build_value_dictionary(table_identifier: str, column_name: str, max_values: int = 50) -> Dict[str, int]:
    """
    Build a value dictionary (value frequency) for a column.
    
    Args:
        table_identifier: Fully qualified table name
        column_name: Column to build dictionary for
        max_values: Maximum number of unique values to include
    
    Returns:
        Dictionary mapping values to their frequencies
    """
    try:
        query = f"""
        SELECT `{column_name}`, COUNT(*) as frequency 
        FROM {table_identifier} 
        WHERE `{column_name}` IS NOT NULL 
        GROUP BY `{column_name}` 
        ORDER BY frequency DESC 
        LIMIT {max_values}
        """
        result = spark.sql(query).collect()
        return {str(row[0]): int(row[1]) for row in result}
    except Exception as e:
        print(f"Error building value dictionary for {column_name} from {table_identifier}: {str(e)}")
        return {}


def enrich_column_metadata(columns_metadata: pd.DataFrame, table_identifier: str, 
                          sample_size: int, max_unique_values: int,
                          column_configs: List[Dict] = None) -> List[Dict]:
    """
    Enrich column metadata with sampled values and value dictionaries.
    
    Args:
        columns_metadata: DataFrame with column metadata
        table_identifier: Fully qualified table name
        sample_size: Number of samples per column
        max_unique_values: Maximum unique values for value dictionary
        column_configs: Original column configurations from space.json
    
    Returns:
        List of enriched column metadata dictionaries
    """
    enriched_columns = []
    
    # Create a lookup for column configs
    config_lookup = {}
    if column_configs:
        for config in column_configs:
            config_lookup[config.get('column_name')] = config
    
    for _, row in columns_metadata.iterrows():
        col_name = row['col_name']
        col_type = row['data_type']
        col_comment = row.get('comment', '')
        
        # Get original config if exists
        original_config = config_lookup.get(col_name, {})
        
        enriched_col = {
            'column_name': col_name,
            'data_type': col_type,
            'comment': col_comment,
            'original_config': original_config
        }
        
        # Only sample and build dictionaries if not excluded
        if not original_config.get('exclude', False):
            # Sample values if requested or by default
            if original_config.get('get_example_values', True):
                sampled_values = sample_column_values(table_identifier, col_name, sample_size)
                enriched_col['sample_values'] = sampled_values
            
            # Build value dictionary if requested
            if original_config.get('build_value_dictionary', False):
                value_dict = build_value_dictionary(table_identifier, col_name, max_unique_values)
                enriched_col['value_dictionary'] = value_dict
        
        enriched_columns.append(enriched_col)
    
    return enriched_columns


def enhance_comments_with_llm(columns: List[Dict], llm_endpoint: str) -> List[Dict]:
    """
    Use LLM to enhance column comments with better descriptions.
    
    Args:
        columns: List of column dictionaries
        llm_endpoint: LLM endpoint name
    
    Returns:
        List of columns with enhanced comments
    """
    # Helper to serialize dates and other objects
    def safe_json_default(obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        return str(obj)
    
    # Prepare simplified version for LLM
    simplified_cols = []
    for col in columns:
        simplified_cols.append({
            'col_name': col['column_name'],
            'data_type': col['data_type'],
            'comment': col.get('comment', ''),
            'sample_values': col.get('sample_values', [])[:10] if 'sample_values' in col else []
        })
    
    prompt = (
        "You will receive a list of database columns in JSON format. "
        "Your task is to improve the 'comment' field for each column by:\n"
        "1. Making it more descriptive and informative\n"
        "2. Using the data_type and sample_values as context\n"
        "3. Explaining what the column represents in plain English\n\n"
        f"{json.dumps(simplified_cols, indent=2, default=safe_json_default)}\n\n"
        "Return a JSON array with the same structure, but add an 'enhanced_comment' field "
        "with your improved description. Only return valid JSON, no explanations."
    )
    
    try:
        llm_result = spark.sql(
            f"SELECT ai_query('{llm_endpoint}', ?) as result", 
            [prompt]
        ).collect()[0]['result']
        
        # Clean up response
        llm_result_str = llm_result.replace('```json', '').replace('```', '').strip()
        enhanced_cols = json.loads(llm_result_str)
        
        # Merge enhanced comments back
        for i, col in enumerate(columns):
            if i < len(enhanced_cols) and 'enhanced_comment' in enhanced_cols[i]:
                col['enhanced_comment'] = enhanced_cols[i]['enhanced_comment']
        
        return columns
    except Exception as e:
        print(f"Error enhancing comments with LLM: {str(e)}")
        return columns


def synthesize_table_description(table_identifier: str, enriched_columns: List[Dict], llm_endpoint: str) -> str:
    """
    Synthesize a comprehensive table description using column metadata.
    
    Args:
        table_identifier: Fully qualified table name
        enriched_columns: List of enriched column dictionaries
        llm_endpoint: LLM endpoint name
    
    Returns:
        Synthesized table description
    """
    # Helper to serialize dates and other objects
    def safe_json_default(obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        return str(obj)
    
    # Prepare column summary for LLM
    col_summaries = []
    for col in enriched_columns:
        col_summary = {
            'column_name': col['column_name'],
            'data_type': col['data_type'],
            'enhanced_comment': col.get('enhanced_comment', col.get('comment', '')),
            'has_value_dictionary': 'value_dictionary' in col,
            'sample_values': col.get('sample_values', [])[:3] if 'sample_values' in col else []
        }
        col_summaries.append(col_summary)
    
    prompt = (
        f"You are analyzing a database table: {table_identifier}\n\n"
        f"Here are the columns with their descriptions:\n"
        f"{json.dumps(col_summaries, indent=2, default=safe_json_default)}\n\n"
        "Based on this information, write a concise 2-3 sentence description of what this table contains "
        "and its purpose. Focus on the data domain, key entities, and typical use cases. "
        "Return only the description text, no JSON or formatting."
    )
    
    try:
        llm_result = spark.sql(
            f"SELECT ai_query('{llm_endpoint}', ?) as result", 
            [prompt]
        ).collect()[0]['result']
        
        # Clean up response
        table_description = llm_result.strip()
        return table_description
    except Exception as e:
        print(f"Error synthesizing table description: {str(e)}")
        # Fallback: create a simple description
        return f"Table {table_identifier} with {len(enriched_columns)} columns."


def synthesize_space_description(enriched_tables: List[Dict], llm_endpoint: str) -> str:
    """
    Synthesize a comprehensive space description using table metadata.
    
    Args:
        enriched_tables: List of enriched table dictionaries
        llm_endpoint: LLM endpoint name
    
    Returns:
        Synthesized space description
    """
    # Helper to serialize dates and other objects
    def safe_json_default(obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        return str(obj)
    
    # Prepare table summary for LLM
    table_summaries = []
    for table in enriched_tables:
        table_summary = {
            'table_identifier': table['table_identifier'],
            'table_description': table.get('table_description', ''),
            'column_count': table['total_columns']
        }
        table_summaries.append(table_summary)
    
    prompt = (
        f"You are analyzing a Genie space that contains {len(enriched_tables)} tables.\n\n"
        f"Here are the tables with their descriptions:\n"
        f"{json.dumps(table_summaries, indent=2, default=safe_json_default)}\n\n"
        "Based on this information, write a concise 2-3 sentence description of what this Genie space provides "
        "and its overall purpose. Focus on the data domain, key use cases, and the types of queries it can answer. "
        "Return only the description text, no JSON or formatting."
    )
    
    try:
        llm_result = spark.sql(
            f"SELECT ai_query('{llm_endpoint}', ?) as result", 
            [prompt]
        ).collect()[0]['result']
        
        # Clean up response
        space_description = llm_result.strip()
        return space_description
    except Exception as e:
        print(f"Error synthesizing space description: {str(e)}")
        # Fallback: create a simple description
        return f"Genie space with {len(enriched_tables)} tables for data analysis."


# COMMAND ----------

# DBTITLE 1,Load and Process Genie Space Exports

def process_genie_space(space_json_path: str, enriched_docs_table: str) -> Dict[str, Any]:
    """
    Process a single Genie space export and enrich it with table metadata.
    
    Args:
        space_json_path: Path to the space.json file
        enriched_docs_table: Target table for enriched docs
    
    Returns:
        Enriched space document as dictionary
    """
    print(f"\n{'='*80}")
    print(f"Processing: {space_json_path}")
    print(f"{'='*80}")
    
    # Load space.json
    with open(space_json_path, 'r', encoding='utf-8') as f:
        space_data = json.load(f)
    
    space_id = space_data.get('space_id')
    space_title = space_data.get('title')
    
    print(f"Space ID: {space_id}")
    print(f"Space Title: {space_title}")
    
    # Parse serialized_space if available
    serialized_space = None
    if 'serialized_space' in space_data:
        try:
            serialized_space = json.loads(space_data['serialized_space'])
        except json.JSONDecodeError:
            print("Warning: Could not parse serialized_space")
    
    # Enrich table metadata
    enriched_tables = []
    
    if serialized_space and 'data_sources' in serialized_space:
        tables = serialized_space.get('data_sources', {}).get('tables', [])
        
        for table_config in tables:
            table_identifier = table_config.get('identifier')
            column_configs = table_config.get('column_configs', [])
            
            print(f"\nEnriching table: {table_identifier}")
            
            # Get base table metadata
            table_metadata = get_table_metadata(table_identifier)
            
            if not table_metadata.empty:
                # Enrich with samples and value dictionaries
                enriched_columns = enrich_column_metadata(
                    table_metadata, 
                    table_identifier, 
                    sample_size, 
                    max_unique_values,
                    column_configs
                )
                
                # Enhance comments with LLM
                enriched_columns = enhance_comments_with_llm(enriched_columns, llm_endpoint)
                
                # Synthesize table description using enriched column metadata
                print(f"  → Synthesizing table description...")
                table_description = synthesize_table_description(table_identifier, enriched_columns, llm_endpoint)
                
                enriched_table = {
                    'table_identifier': table_identifier,
                    'original_config': table_config,
                    'enriched_columns': enriched_columns,
                    'total_columns': len(enriched_columns),
                    'table_description': table_description
                }
                
                enriched_tables.append(enriched_table)
                print(f"  ✓ Enriched {len(enriched_columns)} columns")
                print(f"  ✓ Table description: {table_description[:100]}...")
            else:
                print(f"  ✗ Could not get metadata for {table_identifier}")
    
    # Synthesize space description if empty
    space_description = space_data.get('description', '')
    if not space_description and enriched_tables:
        print(f"\n→ Space description is empty. Synthesizing from table metadata...")
        space_description = synthesize_space_description(enriched_tables, llm_endpoint)
        print(f"✓ Synthesized space description: {space_description[:100]}...")
    
    # Build final enriched document
    enriched_doc = {
        'space_id': space_id,
        'space_title': space_title,
        'space_description': space_description,
        'warehouse_id': space_data.get('warehouse_id', ''),
        'original_space_data': space_data,
        'serialized_space': serialized_space,
        'enriched_tables': enriched_tables,
        'enrichment_timestamp': datetime.now().isoformat(),
        'source_file': space_json_path
    }
    
    return enriched_doc


# COMMAND ----------

# DBTITLE 1,Process All Genie Spaces

# Get all space.json files from the volume
genie_exports_path = f"/Volumes/{genie_exports_volume.replace('.', '/')}/genie_exports"
print(f"Looking for Genie exports in: {genie_exports_path}")

import glob
space_files = glob.glob(f"{genie_exports_path}/*.space.json")
print(f"Found {len(space_files)} Genie space files")

# Process each space
all_enriched_docs = []

for space_file in space_files:
    try:
        enriched_doc = process_genie_space(space_file, enriched_docs_table)
        all_enriched_docs.append(enriched_doc)
    except Exception as e:
        print(f"Error processing {space_file}: {str(e)}")
        import traceback
        traceback.print_exc()

print(f"\n{'='*80}")
print(f"Successfully enriched {len(all_enriched_docs)} Genie spaces")
print(f"{'='*80}")

# COMMAND ----------

# DBTITLE 1,Save Enriched Docs to Unity Catalog

# Helper function to safely serialize objects
def json_serializer(obj):
    """Convert non-serializable objects to strings"""
    # Handle PySpark Column objects
    if hasattr(obj, '__class__') and 'pyspark' in str(type(obj)):
        return str(obj)
    # Handle datetime/date objects
    if hasattr(obj, 'isoformat') and callable(getattr(obj, 'isoformat', None)):
        return obj.isoformat()
    return str(obj)

# Convert to Spark DataFrame
enriched_docs_json = [json.dumps(doc, default=json_serializer) for doc in all_enriched_docs]

df_enriched = spark.createDataFrame(
    [(i, doc_json, doc['space_id'], doc['space_title']) 
     for i, (doc_json, doc) in enumerate(zip(enriched_docs_json, all_enriched_docs))],
    schema="id INT, enriched_doc STRING, space_id STRING, space_title STRING"
)

# Save to Delta table
df_enriched.write.mode("overwrite").saveAsTable(enriched_docs_table)

print(f"\n✓ Saved enriched docs to: {enriched_docs_table}")
print(f"  Total records: {df_enriched.count()}")

# Display sample
display(spark.table(enriched_docs_table))

# COMMAND ----------

# DBTITLE 1,Create Multi-Level Chunks for Vector Search

def create_multi_level_chunks(enriched_docs: List[Dict]) -> List[Dict]:
    """
    Create multi-level chunks following the Hybrid Multi-Level Chunking strategy.
    
    Returns:
        List of chunk dictionaries with metadata for vector search
    """
    all_chunks = []
    chunk_id = 0
    
    for doc in enriched_docs:
        space_id = doc.get('space_id')
        space_title = doc.get('space_title')
        space_description = doc.get('space_description', '')
        enriched_tables = doc.get('enriched_tables', [])
        
        # ===================================================================
        # Level 1: Space Summary Chunk
        # ===================================================================
        # Create overview of all tables in the space
        table_summaries = []
        for table in enriched_tables:
            table_id = table.get('table_identifier', '')
            table_desc = table.get('table_description', '')
            columns = table.get('enriched_columns', [])
            col_count = len(columns)
            
            # Get key column types
            categorical_cols = [c['column_name'] for c in columns if 'value_dictionary' in c]
            temporal_cols = [c['column_name'] for c in columns if any(t in c.get('data_type', '').lower() for t in ['date', 'time', 'timestamp'])]
            id_cols = [c['column_name'] for c in columns if any(k in c['column_name'].lower() for k in ['_id', 'id_'])] # TODO: this could be more flexible to allow for other identifier patterns.
            
            table_summary = f"• {table_id} ({col_count} columns)\n  Description: {table_desc}"
            if categorical_cols:
                table_summary += f"\n  - Categorical fields: {', '.join(categorical_cols)}"
            if temporal_cols:
                table_summary += f"\n  - Temporal fields: {', '.join(temporal_cols)}"
            if id_cols:
                table_summary += f"\n  - Identifier fields: {', '.join(id_cols)}"
            
            table_summaries.append(table_summary)
        
        space_summary_text = f"""Space: {space_title}
Space ID: {space_id}

Description: {space_description}

Available Tables ({len(enriched_tables)} total):
{chr(10).join(table_summaries)}

Purpose: This Genie space provides access to structured data across {len(enriched_tables)} tables for analytical queries and reporting."""
        
        all_chunks.append({
            'chunk_id': chunk_id,
            'chunk_type': 'space_summary',
            'space_id': space_id,
            'space_title': space_title,
            'table_name': None,
            'column_name': None,
            'searchable_content': space_summary_text,
            'is_categorical': False,
            'is_temporal': False,
            'is_identifier': False,
            'has_value_dictionary': False,
            'metadata_json': json.dumps({
                'total_tables': len(enriched_tables),
                'space_description': space_description
            }, default=json_serializer)
        })
        chunk_id += 1
        
        # ===================================================================
        # Level 1B: Space Details Chunk (Full enriched document)
        # ===================================================================
        # This chunk contains everything from enriched_doc for precision retrieval
        space_details_text = f"""Space: {space_title}
Space ID: {space_id}

Description: {space_description}

This is a comprehensive view of the entire Genie space including all tables, columns, and metadata.
Use this for detailed analysis when precision is more important than speed.

Total Tables: {len(enriched_tables)}

Metadata_json: {json.dumps(doc, default=json_serializer)}
"""
        
        all_chunks.append({
            'chunk_id': chunk_id,
            'chunk_type': 'space_details',
            'space_id': space_id,
            'space_title': space_title,
            'table_name': None,
            'column_name': None,
            'searchable_content': space_details_text,
            'is_categorical': False,
            'is_temporal': False,
            'is_identifier': False,
            'has_value_dictionary': False,
            'metadata_json': json.dumps(doc, default=json_serializer)
        })
        chunk_id += 1
        
        # ===================================================================
        # Level 2: Table Overview Chunks (one per table)
        # ===================================================================
        for table in enriched_tables:
            table_id = table.get('table_identifier', '')
            table_name = table_id.split('.')[-1] if '.' in table_id else table_id
            table_desc = table.get('table_description', '')
            columns = table.get('enriched_columns', [])
            
            # Build column list with brief descriptions
            column_lines = []
            categorical_fields = []
            
            for col in columns:
                col_name = col.get('column_name')
                col_type = col.get('data_type')
                enhanced_comment = col.get('enhanced_comment', col.get('comment', ''))
                
                # Truncate long descriptions for overview
                if len(enhanced_comment) > 300:
                    enhanced_comment = enhanced_comment[:300-3] + "..."
                
                column_lines.append(f"• {col_name} ({col_type}): {enhanced_comment}")
                
                # Track categorical fields with top values
                if 'value_dictionary' in col and col['value_dictionary']:
                    top_values = list(col['value_dictionary'].keys())[:5]
                    categorical_fields.append(f"• {col_name}: {', '.join(top_values)}")
            
            table_overview_text = f"""Table: {table_name}
Full Path: {table_id}
Space: {space_title}

Table Description: {table_desc}

Columns ({len(columns)} total):
{chr(10).join(column_lines)}
"""
            
            if categorical_fields:
                table_overview_text += f"""
Key Categorical Fields:
{chr(10).join(categorical_fields)}
"""
            
            all_chunks.append({
                'chunk_id': chunk_id,
                'chunk_type': 'table_overview',
                'space_id': space_id,
                'space_title': space_title,
                'table_name': table_name,
                'column_name': None,
                'searchable_content': table_overview_text,
                'is_categorical': any('value_dictionary' in c for c in columns),
                'is_temporal': any(any(t in c.get('data_type', '').lower() for t in ['date', 'time', 'timestamp']) for c in columns),
                'is_identifier': any(any(k in c['column_name'].lower() for k in ['_id', 'id_']) for c in columns),
                'has_value_dictionary': any('value_dictionary' in c for c in columns),
                'metadata_json': json.dumps({
                    'table_identifier': table_id,
                    'total_columns': len(columns)
                }, default=json_serializer)
            })
            chunk_id += 1
            
            # ===================================================================
            # Level 3: Column Detail Chunks (one per column)
            # ===================================================================
            for col in columns:
                col_name = col.get('column_name')
                col_type = col.get('data_type')
                enhanced_comment = col.get('enhanced_comment', col.get('comment', ''))
                sample_values = col.get('sample_values', [])
                value_dict = col.get('value_dictionary', {})
                
                # Determine column characteristics
                is_categorical = len(value_dict) > 0
                is_temporal = any(t in col_type.lower() for t in ['date', 'time', 'timestamp'])
                is_identifier = any(k in col_name.lower() for k in ['_id', 'id_'])
                has_value_dictionary = len(value_dict) > 0
                
                # Build column detail text
                column_detail_text = f"""Column: {col_name}
Table: {table_name}
Full Table Path: {table_id}
Space: {space_title}
Data Type: {col_type}

Description: {enhanced_comment}
"""
                
                # Add sample values (limited to 5 for readability)
                if sample_values:
                    limited_samples = sample_values[:5]
                    column_detail_text += f"""
Sample Values: {', '.join([str(v) for v in limited_samples])}
"""
                
                # Add top values from value dictionary
                if value_dict:
                    top_entries = sorted(value_dict.items(), key=lambda x: x[1], reverse=True)[:10]
                    value_lines = [f"  - {val}: {count:,} records" for val, count in top_entries]
                    column_detail_text += f"""
Top Values:
{chr(10).join(value_lines)}
"""
                
                # Add classification info
                characteristics = []
                if is_categorical:
                    characteristics.append("categorical")
                if is_temporal:
                    characteristics.append("temporal")
                if is_identifier:
                    characteristics.append("identifier")
                
                if characteristics:
                    column_detail_text += f"""
Classification: {', '.join(characteristics)}
"""
                
                all_chunks.append({
                    'chunk_id': chunk_id,
                    'chunk_type': 'column_detail',
                    'space_id': space_id,
                    'space_title': space_title,
                    'table_name': table_name,
                    'column_name': col_name,
                    'searchable_content': column_detail_text,
                    'is_categorical': is_categorical,
                    'is_temporal': is_temporal,
                    'is_identifier': is_identifier,
                    'has_value_dictionary': has_value_dictionary,
                    'metadata_json': json.dumps({
                        'data_type': col_type,
                        'sample_values': sample_values[:5],
                        'value_dictionary_size': len(value_dict)
                    }, default=json_serializer)
                })
                chunk_id += 1
    
    return all_chunks


# Create multi-level chunks
print(f"\n{'='*80}")
print("Creating Multi-Level Chunks for Vector Search")
print(f"{'='*80}")

all_chunks = create_multi_level_chunks(all_enriched_docs)

print(f"\n✓ Created {len(all_chunks)} total chunks:")
chunk_type_counts = {}
for chunk in all_chunks:
    chunk_type = chunk['chunk_type']
    chunk_type_counts[chunk_type] = chunk_type_counts.get(chunk_type, 0) + 1

for chunk_type, count in chunk_type_counts.items():
    print(f"  - {chunk_type}: {count} chunks")

# Convert to Spark DataFrame
df_chunks = spark.createDataFrame(all_chunks)

# Save to Delta table
chunks_table_name = f"{enriched_docs_table}_chunks"
df_chunks.write.mode("overwrite").saveAsTable(chunks_table_name)

print(f"\n✓ Saved chunks to: {chunks_table_name}")
print(f"  Total records: {df_chunks.count()}")

# Display samples of each chunk type
print("\n" + "="*80)
print("Sample Chunks by Type")
print("="*80)

for chunk_type in ['space_summary', 'space_details', 'table_overview', 'column_detail']:
    print(f"\n{chunk_type.upper()}:")
    sample = spark.table(chunks_table_name).filter(f"chunk_type = '{chunk_type}'").limit(1)
    display(sample.select('chunk_id', 'chunk_type', 'space_title', 'table_name', 'column_name', 'searchable_content'))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC 
# MAGIC This notebook has:
# MAGIC 1. ✓ Sampled column values from all Genie space tables
# MAGIC 2. ✓ Built value dictionaries for configured columns
# MAGIC 3. ✓ Enhanced column descriptions using LLM
# MAGIC 4. ✓ Synthesized table descriptions from enriched column metadata
# MAGIC 5. ✓ Synthesized space descriptions (if empty) from table metadata
# MAGIC 6. ✓ Enriched Genie space.json exports with table metadata
# MAGIC 7. ✓ Saved enriched docs to Unity Catalog delta table
# MAGIC 8. ✓ Created multi-level chunks using Hybrid Multi-Level Chunking Strategy:
# MAGIC    - **Level 1**: Space Summary Chunks (overview with space & table descriptions)
# MAGIC    - **Level 1B**: Space Details Chunks (full enriched document for precision)
# MAGIC    - **Level 2**: Table Overview Chunks (with table descriptions and column list)
# MAGIC    - **Level 3**: Column Detail Chunks (full descriptions, samples, value dictionaries)
# MAGIC 9. ✓ Added metadata fields for filtered retrieval (chunk_type, is_categorical, is_temporal, etc.)
# MAGIC 
# MAGIC **Key Outputs:**
# MAGIC - Enriched Docs Table: `{enriched_docs_table}`
# MAGIC - Multi-Level Chunks Table: `{enriched_docs_table}_chunks`
# MAGIC 
# MAGIC **New Features:**
# MAGIC - Table descriptions synthesized from column metadata
# MAGIC - Space descriptions synthesized from table metadata (when empty)
# MAGIC - Space Details chunks for precision retrieval vs. Space Summary for speed
# MAGIC 
# MAGIC Next: Use these chunks to build vector search index (04_VS_Enriched_Genie_Spaces.py)

