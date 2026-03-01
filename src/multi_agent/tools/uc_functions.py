"""
Register Unity Catalog Functions for SQL Synthesis Agent.

These UC functions query different levels of the enriched genie docs chunks table:
1. get_space_summary: High-level space information
2. get_table_overview: Table-level metadata
3. get_column_detail: Column-level metadata
4. get_space_instructions: Extract raw Genie space instructions JSON (any structure within instructions field) from space metadata (REQUIRED FINAL STEP)
5. get_space_details: Complete metadata (last resort - token intensive)

All functions use LANGUAGE SQL for better performance and compatibility.
"""

try:
    from databricks.sdk.runtime import spark
except ImportError:
    try:
        from databricks.connect import DatabricksSession
        spark = DatabricksSession.builder.getOrCreate()
    except ImportError:
        try:
            from pyspark.sql import SparkSession
            spark = SparkSession.builder.getOrCreate()
        except ImportError:
            spark = None


def register_uc_functions(catalog: str, schema: str, table_name: str):
    """
    Register Unity Catalog functions that will be used as tools by the SQL Synthesis Agent.
    
    Args:
        catalog: Unity Catalog catalog name
        schema: Schema name where functions will be created
        table_name: Fully qualified name of enriched_genie_docs_chunks table
    """
    print("=" * 80)
    print("REGISTERING UNITY CATALOG FUNCTIONS")
    print("=" * 80)
    print(f"Target table: {table_name}")
    print(f"Functions will be created in: {catalog}.{schema}")
    print("=" * 80)
    
    # UC Function 1: get_space_summary (SQL scalar function)
    spark.sql(f"""
    CREATE OR REPLACE FUNCTION {catalog}.{schema}.get_space_summary(
        space_ids_json STRING DEFAULT NULL COMMENT 'JSON array of space IDs to query, or NULL to retrieve all spaces. Example: ["space_1", "space_2"] or NULL'
    )
    RETURNS STRING
    LANGUAGE SQL
    COMMENT 'Get high-level summary of Genie spaces. Returns JSON with space summaries including chunk_id, chunk_type, space_title, and content.'
    RETURN
        SELECT COALESCE(
            to_json(
                map_from_entries(
                    collect_list(
                        struct(
                            space_id,
                            named_struct(
                                'chunk_id', chunk_id,
                                'chunk_type', chunk_type,
                                'space_title', space_title,
                                'content', searchable_content
                            )
                        )
                    )
                )
            ),
            '{{}}'
        ) as result
        FROM {table_name}
        WHERE chunk_type = 'space_summary'
        AND (
            space_ids_json IS NULL 
            OR TRIM(LOWER(space_ids_json)) IN ('null', 'none', '')
            OR array_contains(from_json(space_ids_json, 'array<string>'), space_id)
        )
    """)
    print("✓ Registered: get_space_summary")
    
    # UC Function 2: get_table_overview (SQL scalar function with grouping)
    spark.sql(f"""
    CREATE OR REPLACE FUNCTION {catalog}.{schema}.get_table_overview(
        space_ids_json STRING DEFAULT NULL COMMENT 'JSON array of space IDs to query (required, prefer single space). Example: ["space_1"]',
        table_names_json STRING DEFAULT NULL COMMENT 'JSON array of table names to filter, or NULL for all tables in the specified spaces. Example: ["table1", "table2"] or NULL'
    )
    RETURNS STRING
    LANGUAGE SQL
    COMMENT 'Get table-level metadata for specific Genie spaces. Returns JSON with table metadata including chunk_id, chunk_type, table_name, and content grouped by space.'
    RETURN
        SELECT COALESCE(
            to_json(
                map_from_entries(
                    collect_list(
                        struct(
                            space_id,
                            named_struct(
                                'space_title', space_title,
                                'tables', tables
                            )
                        )
                    )
                )
            ),
            '{{}}'
        ) as result
        FROM (
            SELECT 
                space_id,
                first(space_title) as space_title,
                collect_list(
                    named_struct(
                        'chunk_id', chunk_id,
                        'chunk_type', chunk_type,
                        'table_name', table_name,
                        'content', searchable_content
                    )
                ) as tables
            FROM {table_name}
            WHERE chunk_type = 'table_overview'
            AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
            AND (
                table_names_json IS NULL 
                OR TRIM(LOWER(table_names_json)) IN ('null', 'none', '')
                OR array_contains(from_json(table_names_json, 'array<string>'), table_name)
            )
            GROUP BY space_id
        )
    """)
    print("✓ Registered: get_table_overview")
    
    # UC Function 3: get_column_detail (SQL scalar function with grouping)
    spark.sql(f"""
    CREATE OR REPLACE FUNCTION {catalog}.{schema}.get_column_detail(
        space_ids_json STRING DEFAULT NULL COMMENT 'JSON array of space IDs to query (required, prefer single space). Example: ["space_1"]',
        table_names_json STRING DEFAULT NULL COMMENT 'JSON array of table names to filter (required, prefer single table). Example: ["table1"]',
        column_names_json STRING DEFAULT NULL COMMENT 'JSON array of column names to filter, or NULL for all columns in the specified tables. Example: ["col1", "col2"] or NULL'
    )
    RETURNS STRING
    LANGUAGE SQL
    COMMENT 'Get column-level metadata for specific Genie spaces. Returns JSON with column metadata including chunk_id, chunk_type, table_name, column_name, and content grouped by space.'
    RETURN
        SELECT COALESCE(
            to_json(
                map_from_entries(
                    collect_list(
                        struct(
                            space_id,
                            named_struct(
                                'space_title', space_title,
                                'columns', columns
                            )
                        )
                    )
                )
            ),
            '{{}}'
        ) as result
        FROM (
            SELECT 
                space_id,
                first(space_title) as space_title,
                collect_list(
                    named_struct(
                        'chunk_id', chunk_id,
                        'chunk_type', chunk_type,
                        'table_name', table_name,
                        'column_name', column_name,
                        'content', searchable_content
                    )
                ) as columns
            FROM {table_name}
            WHERE chunk_type = 'column_detail'
            AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
            AND array_contains(from_json(table_names_json, 'array<string>'), table_name)
            AND (
                column_names_json IS NULL 
                OR TRIM(LOWER(column_names_json)) IN ('null', 'none', '')
                OR array_contains(from_json(column_names_json, 'array<string>'), column_name)
            )
            GROUP BY space_id
        )
    """)
    print("✓ Registered: get_column_detail")
    
    # UC Function 4: get_space_instructions (REQUIRED FINAL STEP)
    spark.sql(f"""
    CREATE OR REPLACE FUNCTION {catalog}.{schema}.get_space_instructions(
        space_ids_json STRING DEFAULT NULL COMMENT 'JSON array of space IDs to query (required). Example: ["space_1", "space_2"]'
    )
    RETURNS STRING
    LANGUAGE SQL
    COMMENT 'Extract SQL instructions from Genie space metadata. Returns JSON with space-specific SQL guidance. The instructions field contains the raw JSON content from serialized_space.instructions, which may include example queries, filters, measures, and other space-specific guidance.'
    RETURN
        SELECT COALESCE(
            to_json(
                map_from_entries(
                    collect_list(
                        struct(
                            space_id,
                            named_struct(
                                'chunk_id', chunk_id,
                                'chunk_type', chunk_type,
                                'space_title', space_title,
                                'instructions', get_json_object(metadata_json, '$.serialized_space.instructions')
                            )
                        )
                    )
                )
            ),
            '{{}}'
        ) as result
        FROM {table_name}
        WHERE chunk_type = 'space_details'
        AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
    """)
    print("✓ Registered: get_space_instructions")
    
    # UC Function 5: get_space_details (SQL scalar function - last resort)
    spark.sql(f"""
    CREATE OR REPLACE FUNCTION {catalog}.{schema}.get_space_details(
        space_ids_json STRING DEFAULT NULL COMMENT 'JSON array of space IDs to query (required). Example: ["space_1", "space_2"]. WARNING: Returns large metadata - use as LAST RESORT.'
    )
    RETURNS STRING
    LANGUAGE SQL
    COMMENT 'Get complete metadata for specific Genie spaces - use as LAST RESORT (token intensive). Returns JSON with complete space metadata including chunk_id, chunk_type, space_title, and all available metadata content.'
    RETURN
        SELECT COALESCE(
            to_json(
                map_from_entries(
                    collect_list(
                        struct(
                            space_id,
                            named_struct(
                                'chunk_id', chunk_id,
                                'chunk_type', chunk_type,
                                'space_title', space_title,
                                'complete_metadata', searchable_content
                            )
                        )
                    )
                )
            ),
            '{{}}'
        ) as result
        FROM {table_name}
        WHERE chunk_type = 'space_details'
        AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
    """)
    print("✓ Registered: get_space_details")
    
    print("\n" + "=" * 80)
    print("✅ ALL 5 UC FUNCTIONS REGISTERED SUCCESSFULLY!")
    print("=" * 80)
    print("Functions available for SQL Synthesis Agent:")
    print(f"  1. {catalog}.{schema}.get_space_summary")
    print(f"  2. {catalog}.{schema}.get_table_overview")
    print(f"  3. {catalog}.{schema}.get_column_detail")
    print(f"  4. {catalog}.{schema}.get_space_instructions")
    print(f"  5. {catalog}.{schema}.get_space_details")
    print("=" * 80)


def check_uc_functions_exist(catalog: str, schema: str, verbose: bool = True):
    """Check if UC functions exist."""
    pass

