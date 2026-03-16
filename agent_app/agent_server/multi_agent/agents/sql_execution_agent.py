"""
SQL Execution Agent

This module provides the SQLExecutionAgent class for executing SQL queries
using Databricks SQL Warehouse.

PRODUCTION-READY DESIGN:
- Uses databricks-sql-connector with unified authentication (Config + credentials_provider)
- Automatically handles OAuth credentials when deployed with registered resources
- Supports both development (notebook) and production (Model Serving) environments

AUTHENTICATION WITH AUTOMATIC PASSTHROUGH:
When you register resources during agent deployment:

    resources = [
        DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
        # ... other resources
    ]
    mlflow.langchain.log_model(..., resources=resources)

Databricks automatically:
1. Creates a service principal for your agent
2. Manages OAuth token generation and rotation
3. Injects credentials into the Model Serving environment

The Config() class automatically reads workspace host and injected OAuth credentials,
eliminating the need for manual DATABRICKS_HOST/DATABRICKS_TOKEN configuration.

Reference: https://docs.databricks.com/generative-ai/agent-framework/agent-authentication

LEGACY MANUAL AUTHENTICATION (if not using automatic passthrough):
If you're not using resource registration, you can still manually configure:
- DATABRICKS_HOST and DATABRICKS_TOKEN via Model Serving environment variables
- Config() will still read them from the environment
"""

import re
import json
from typing import Dict, Any, List


class SQLExecutionAgent:
    """
    Agent responsible for executing SQL queries using Databricks SQL Warehouse.
    
    PRODUCTION-READY DESIGN:
    - Uses databricks-sql-connector with unified authentication (Config + credentials_provider)
    - Automatically handles OAuth credentials when deployed with registered resources
    - Supports both development (notebook) and production (Model Serving) environments
    
    AUTHENTICATION WITH AUTOMATIC PASSTHROUGH:
    When you register resources during agent deployment:
    
        resources = [
            DatabricksSQLWarehouse(warehouse_id=SQL_WAREHOUSE_ID),
            # ... other resources
        ]
        mlflow.langchain.log_model(..., resources=resources)
    
    Databricks automatically:
    1. Creates a service principal for your agent
    2. Manages OAuth token generation and rotation
    3. Injects credentials into the Model Serving environment
    
    The Config() class automatically reads workspace host and injected OAuth credentials,
    eliminating the need for manual DATABRICKS_HOST/DATABRICKS_TOKEN configuration.
    
    Reference: https://docs.databricks.com/generative-ai/agent-framework/agent-authentication
    
    LEGACY MANUAL AUTHENTICATION (if not using automatic passthrough):
    If you're not using resource registration, you can still manually configure:
    - DATABRICKS_HOST and DATABRICKS_TOKEN via Model Serving environment variables
    - Config() will still read them from the environment
    """
    
    def __init__(self, warehouse_id: str):
        """
        Initialize SQL Execution Agent.
        
        Args:
            warehouse_id: Databricks SQL Warehouse ID for query execution
        """
        self.name = "SQLExecution"
        self.warehouse_id = warehouse_id
    
    def execute_sql(
        self, 
        sql_query: str, 
        max_rows: int = 100,
        return_format: str = "dict"
    ) -> Dict[str, Any]:
        """
        Execute SQL query using Databricks SQL Warehouse and return formatted results.
        
        PRODUCTION BEST PRACTICES IMPLEMENTED:
        1. Context Managers: Uses 'with' statements for automatic resource cleanup
        2. Connection Resilience: Configures timeouts and retry logic for transient failures
        3. Proper Error Handling: Categorizes errors for better production debugging
        4. ANSI SQL Mode: Ensures consistent SQL behavior across environments
        5. Model Serving Compatible: Works without Spark session via REST API
        
        Connection Configuration:
        - Socket timeout: 900s (balances Model Serving 297s limit with warehouse query time)
        - HTTP retries: 30 attempts with exponential backoff (1-60s)
        - Session config: ANSI mode enabled for SQL compliance
        
        Args:
            sql_query: Support two types: 
                1) The result from invoke the SQL synthesis agent (dict with messages)
                2) The SQL query string (can be raw SQL or contain markdown code blocks)
            max_rows: Maximum number of rows to return (default: 100)
            return_format: Format of the result - "dict", "json", or "markdown"
            
        Returns:
            Dictionary containing:
            - success: bool - Whether execution was successful
            - sql: str - The executed SQL query
            - result: Any - Query results in requested format
            - row_count: int - Number of rows returned
            - columns: List[str] - Column names
            - error: str - Error message if failed (optional)
            - error_type: str - Exception type for debugging (only on failure)
            - error_hint: str - Suggested resolution (only on failure)
        """
        from databricks import sql
        from databricks.sdk.core import Config
        
        # Step 1: Extract SQL from agent result or markdown code blocks if present
        if sql_query and isinstance(sql_query, dict) and "messages" in sql_query:
            sql_query = sql_query["messages"][-1].content
        
        extracted_sql = sql_query.strip()
        
        if "```sql" in extracted_sql.lower():
            # Extract content between ```sql and ```
            sql_match = re.search(r'```sql\s*(.*?)\s*```', extracted_sql, re.IGNORECASE | re.DOTALL)
            if sql_match:
                extracted_sql = sql_match.group(1).strip()
        elif "```" in extracted_sql:
            # Extract any code block
            sql_match = re.search(r'```\s*(.*?)\s*```', extracted_sql, re.DOTALL)
            if sql_match:
                extracted_sql = sql_match.group(1).strip()
        
        # Step 2: Enforce LIMIT clause (for safety and token management)
        # Always enforce max_rows limit, even if query already has LIMIT
        limit_pattern = re.search(r'\bLIMIT\s+(\d+)\b', extracted_sql, re.IGNORECASE)
        if limit_pattern:
            existing_limit = int(limit_pattern.group(1))
            if existing_limit > max_rows:
                # Replace existing LIMIT with max_rows if it exceeds the limit
                extracted_sql = re.sub(
                    r'\bLIMIT\s+\d+\b', 
                    f'LIMIT {max_rows}', 
                    extracted_sql, 
                    flags=re.IGNORECASE
                )
                print(f"⚠️  Reduced LIMIT from {existing_limit} to {max_rows} (max_rows enforcement)")
        else:
            # Add LIMIT if not present
            extracted_sql = f"{extracted_sql.rstrip(';')} LIMIT {max_rows}"
        
        try:
            # Step 3: Initialize Databricks Config for unified authentication
            # BEST PRACTICE: Config() automatically reads workspace host and OAuth credentials
            # - In Model Serving with automatic passthrough: reads injected service principal credentials
            # - In notebooks: reads from notebook context or environment variables
            # - With manual config: reads DATABRICKS_HOST and DATABRICKS_TOKEN from environment
            cfg = Config()
            
            # Step 4: Execute the SQL query using SQL Warehouse
            print(f"\n{'='*80}")
            print("🔍 EXECUTING SQL QUERY (via SQL Warehouse)")
            print(f"{'='*80}")
            print(f"Warehouse ID: {self.warehouse_id}")
            print(f"SQL:\n{extracted_sql}")
            print(f"{'='*80}\n")
            
            # Connect to SQL Warehouse using context manager (production best practice)
            # Context managers ensure proper cleanup even if exceptions occur
            # credentials_provider=cfg lets the connector fetch OAuth tokens transparently
            with sql.connect(
                server_hostname=cfg.host,
                http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
                credentials_provider=lambda: cfg.authenticate,  # Unified authentication - handles OAuth automatically
                # Production settings for resilience
                session_configuration={
                    "ansi_mode": "true"  # Enable ANSI SQL compliance for consistent behavior
                },
                socket_timeout=900,  # 15 minutes - Model Serving has 297s limit, warehouse queries can be longer
                http_retry_delay_min=1,  # Minimum retry delay in seconds
                http_retry_delay_max=60,  # Maximum retry delay in seconds
                http_retry_max_redirects=5,  # Max HTTP redirects
                http_retry_stop_after_attempts=30,  # Max retry attempts for transient failures
            ) as connection:
                
                # Use nested context manager for cursor (ensures cursor cleanup)
                with connection.cursor() as cursor:
                    
                    # Execute query
                    cursor.execute(extracted_sql)
                    
                    # PHASE 2 OPTIMIZATION: Get row count efficiently
                    # Try to use cursor.rowcount if available (more efficient than len(fetchall()))
                    columns = [desc[0] for desc in cursor.description]
                    
                    # Fetch results (limited by LIMIT clause already enforced)
                    results = cursor.fetchall()
                    
                    # Use actual result count (fetchall is safe because of LIMIT enforcement)
                    row_count = len(results)
                    
                    print(f"✅ Query executed successfully!")
                    print(f"📊 Rows returned: {row_count} (LIMIT enforced at {max_rows})")
                    print(f"📋 Columns: {', '.join(columns)}\n")
                    print(f"⚡ Optimization: Query has LIMIT {max_rows} - safe to fetch all rows")
                    
                    # Step 5: Convert results to list of dicts for compatibility
                    result_data = [dict(zip(columns, row)) for row in results]
                    
                # Cursor automatically closed here by context manager
            
            # Connection automatically closed here by context manager
            
            # Step 6: Format results based on return_format
            if return_format == "json":
                # Convert to JSON strings (matching old spark behavior)
                result_data = [json.dumps(row) for row in result_data]
            elif return_format == "markdown":
                # Create markdown table
                import pandas as pd
                pandas_df = pd.DataFrame(result_data)
                result_data = pandas_df.to_markdown(index=False)
            # else: dict format (default) - already in correct format
            
            # Step 7: Display preview
            print(f"{'='*80}")
            print("📄 RESULTS PREVIEW (first 10 rows)")
            print(f"{'='*80}")
            # Preview first 10 rows
            for i, row in enumerate(result_data[:10]):
                if return_format == "markdown":
                    break  # Don't print individual rows for markdown
                print(f"Row {i+1}: {row}")
            print(f"{'='*80}\n")
            
            return {
                "success": True,
                "sql": extracted_sql,
                "result": result_data,
                "row_count": row_count,
                "columns": columns,
            }
            
        except Exception as e:
            # Step 8: Handle errors with specific exception types for better diagnostics
            error_type = type(e).__name__
            error_msg = str(e)
            
            # Provide production-grade error categorization
            if "DatabaseError" in error_type or "OperationalError" in error_type:
                error_category = "SQL Execution Error"
                error_hint = "Check SQL syntax and table/column permissions"
            elif "ConnectionError" in error_type or "timeout" in error_msg.lower():
                error_category = "Connection Error"
                error_hint = "Verify SQL Warehouse is running and network connectivity"
            elif "Authentication" in error_msg or "Unauthorized" in error_msg:
                error_category = "Authentication Error"
                error_hint = "Verify access token and warehouse permissions"
            else:
                error_category = "General Error"
                error_hint = "Review full error details below"
            
            print(f"\n{'='*80}")
            print(f"❌ SQL EXECUTION FAILED - {error_category}")
            print(f"{'='*80}")
            print(f"Error Type: {error_type}")
            print(f"Error Message: {error_msg}")
            print(f"Hint: {error_hint}")
            print(f"Warehouse ID: {self.warehouse_id}")
            print(f"{'='*80}\n")
            
            return {
                "success": False,
                "sql": extracted_sql,
                "result": None,
                "row_count": 0,
                "columns": [],
                "error": f"{error_category}: {error_msg}",
                "error_type": error_type,
                "error_hint": error_hint
            }
    
    def execute_sql_parallel(
        self,
        sql_queries: List[str],
        max_rows: int = 100,
        return_format: str = "dict",
        max_workers: int = 4
    ) -> List[Dict[str, Any]]:
        """
        Execute multiple SQL queries in parallel using ThreadPoolExecutor.
        
        Each query runs in its own thread with an independent sql.connect() connection,
        so there is no shared state between threads. This is safe because execute_sql()
        creates and closes its own connection/cursor via context managers per call.
        
        ThreadPoolExecutor is used instead of asyncio because databricks-sql-connector
        is synchronous (no native async API), and the work is I/O-bound (waiting on
        SQL Warehouse HTTP responses), so the GIL is not a bottleneck.
        
        Args:
            sql_queries: List of SQL query strings to execute
            max_rows: Maximum rows per query (default: 100)
            return_format: Result format - "dict", "json", or "markdown"
            max_workers: Maximum concurrent threads (default: 4, tune to warehouse concurrency)
        
        Returns:
            List of result dicts (same format as execute_sql), ordered to match input queries.
            Each result includes a "query_number" field (1-indexed).
        """
        import concurrent.futures
        
        # Fast path: skip threading overhead for single query
        if len(sql_queries) <= 1:
            if sql_queries:
                result = self.execute_sql(sql_queries[0], max_rows, return_format)
                result["query_number"] = 1
                return [result]
            return []
        
        print(f"⚡ Executing {len(sql_queries)} queries in parallel (max_workers={min(len(sql_queries), max_workers)})")
        
        results = [None] * len(sql_queries)  # Pre-allocate to preserve ordering
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(sql_queries), max_workers)) as executor:
            future_to_idx = {
                executor.submit(self.execute_sql, query, max_rows, return_format): idx
                for idx, query in enumerate(sql_queries)
            }
            
            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                except Exception as e:
                    # Catch unexpected errors not handled inside execute_sql
                    result = {
                        "success": False,
                        "sql": sql_queries[idx],
                        "result": None,
                        "row_count": 0,
                        "columns": [],
                        "error": f"Parallel execution error: {type(e).__name__}: {str(e)}"
                    }
                result["query_number"] = idx + 1
                results[idx] = result
        
        succeeded = sum(1 for r in results if r["success"])
        print(f"⚡ Parallel execution complete: {succeeded}/{len(sql_queries)} succeeded")
        
        return results
    
    def __call__(self, sql_query: str, max_rows: int = 100, return_format: str = "dict") -> Dict[str, Any]:
        """Make agent callable."""
        return self.execute_sql(sql_query, max_rows, return_format)
