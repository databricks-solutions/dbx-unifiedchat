"""
SQL Execution Agent Node

This module provides the SQL execution node for the multi-agent system.
It wraps the SQLExecutionAgent class and executes SQL queries using Databricks SQL Warehouse.

The node is optimized to use minimal state extraction to reduce token usage.
"""

from typing import Dict, Any
from langgraph.config import get_stream_writer
from langchain_core.messages import SystemMessage

from ..core.state import AgentState
from ..core.config import get_config
from .sql_execution_agent import SQLExecutionAgent

def extract_execution_context(state: AgentState) -> dict:
    """Extract minimal context for SQL execution."""
    return {
        "sql_query": state.get("sql_query"),
        "sql_queries": state.get("sql_queries", []),
        "sql_query_labels": state.get("sql_query_labels", [])
    }


def sql_execution_node(state: AgentState) -> dict:
    """
    SQL execution node wrapping SQLExecutionAgent class.
    Supports executing multiple SQL queries for multi-part questions.
    Combines OOP modularity with explicit state management.
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("🚀 SQL EXECUTION AGENT (Token Optimized)")
    print("="*80)
    
    # NEW: Support multiple queries
    sql_queries = state.get("sql_queries", [])
    
    # Fallback to single query for backward compatibility
    if not sql_queries:
        single_query = state.get("sql_query")
        if single_query:
            sql_queries = [single_query]
    
    if not sql_queries:
        print("❌ No SQL queries to execute")
        # Return error update
        return {
            "execution_error": "No SQL queries provided",
            "next_agent": "summarize"
        }
    
    print(f"📊 Executing {len(sql_queries)} SQL quer{'y' if len(sql_queries) == 1 else 'ies'}")
    
    # Get SQL Warehouse ID from config
    config = get_config()
    sql_warehouse_id = config.table_metadata.sql_warehouse_id
    
    if not sql_warehouse_id:
        error_msg = "SQL_WAREHOUSE_ID is not configured"
        print(f"❌ {error_msg}")
        return {
            "execution_error": error_msg
        }
    
    # Use OOP agent with SQL Warehouse
    try:
        execution_agent = SQLExecutionAgent(warehouse_id=sql_warehouse_id)
    except Exception as e:
        # Fallback for single query if SQLExecutionAgent not available
        print(f"Failed to load SQLExecutionAgent: {e}")
        result = _execute_sql_fallback(sql_queries[0], sql_warehouse_id)
        return {
            "execution_result": result,
            "execution_results": [result],
            "next_agent": "summarize",
            "messages": [
                SystemMessage(content=f"Execution {'successful' if result['success'] else 'failed'}: {result.get('row_count', 0)} rows")
            ]
        }
    
    # Emit start events before parallel execution
    for i, query in enumerate(sql_queries, 1):
        writer({"type": "sql_validation_start", "query": query[:200], "query_number": i})
        writer({"type": "sql_execution_start", "estimated_complexity": "standard", "query_number": i})
    
    # Execute queries in parallel (ThreadPoolExecutor inside the class)
    execution_results = execution_agent.execute_sql_parallel(sql_queries)
    all_successful = all(r["success"] for r in execution_results)
    
    # Emit completion events after parallel execution (writer is not thread-safe)
    for result in execution_results:
        i = result["query_number"]
        if result["success"]:
            print(f"✓ Query {i} succeeded: {result['row_count']} rows")
            writer({"type": "sql_execution_complete", "rows": result['row_count'], "columns": result['columns'], "query_number": i})
        else:
            print(f"❌ Query {i} failed: {result.get('error')}")
    
    # Prepare updates (both single and multiple for backward compatibility)
    updates = {
        "execution_results": execution_results,
        "execution_result": execution_results[0],  # For backward compatibility
        "next_agent": "summarize",
        "messages": []
    }
    
    if all_successful:
        total_rows = sum(r["row_count"] for r in execution_results)
        success_msg = f"Executed {len(sql_queries)} quer{'y' if len(sql_queries) == 1 else 'ies'} successfully. Total rows: {total_rows}"
        print(f"\n✅ {success_msg}")
        
        updates["messages"].append(
            SystemMessage(content=success_msg)
        )
    else:
        failed_count = sum(1 for r in execution_results if not r["success"])
        success_count = len(sql_queries) - failed_count
        error_msg = f"{failed_count} of {len(sql_queries)} queries failed"
        
        print(f"\n⚠️ Partial success: {success_count} succeeded, {failed_count} failed")
        
        updates["execution_error"] = error_msg
        updates["messages"].append(
            SystemMessage(content=f"{success_count} queries succeeded, {failed_count} failed")
        )
    
    return updates


def _execute_sql_fallback(sql_query: str, warehouse_id: str) -> Dict[str, Any]:
    """
    Fallback SQL execution function.
    
    This is a simplified implementation. In production, use SQLExecutionAgent class.
    """
    try:
        from databricks import sql
        from databricks.sdk.core import Config
        import re
        
        # Extract SQL from markdown code blocks if present
        extracted_sql = sql_query.strip()
        if "```sql" in extracted_sql.lower():
            sql_match = re.search(r'```sql\s*(.*?)\s*```', extracted_sql, re.IGNORECASE | re.DOTALL)
            if sql_match:
                extracted_sql = sql_match.group(1).strip()
        elif "```" in extracted_sql:
            sql_match = re.search(r'```\s*(.*?)\s*```', extracted_sql, re.DOTALL)
            if sql_match:
                extracted_sql = sql_match.group(1).strip()
        
        # Enforce LIMIT clause
        max_rows = 100
        limit_pattern = re.search(r'\bLIMIT\s+(\d+)\b', extracted_sql, re.IGNORECASE)
        if limit_pattern:
            existing_limit = int(limit_pattern.group(1))
            if existing_limit > max_rows:
                extracted_sql = re.sub(
                    r'\bLIMIT\s+\d+\b',
                    f'LIMIT {max_rows}',
                    extracted_sql,
                    flags=re.IGNORECASE
                )
        else:
            extracted_sql = f"{extracted_sql.rstrip(';')} LIMIT {max_rows}"
        
        # Initialize Databricks Config
        cfg = Config()
        
        # Execute SQL query
        print(f"\n{'='*80}")
        print("🔍 EXECUTING SQL QUERY (via SQL Warehouse)")
        print(f"{'='*80}")
        print(f"Warehouse ID: {warehouse_id}")
        print(f"SQL:\n{extracted_sql}")
        print(f"{'='*80}\n")
        
        with sql.connect(
            server_hostname=cfg.host,
            http_path=f"/sql/1.0/warehouses/{warehouse_id}",
            credentials_provider=lambda: cfg.authenticate,
            session_configuration={"ansi_mode": "true"},
            socket_timeout=900,
            http_retry_delay_min=1,
            http_retry_delay_max=60,
            http_retry_max_redirects=5,
            http_retry_stop_after_attempts=30,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(extracted_sql)
                columns = [desc[0] for desc in cursor.description]
                results = cursor.fetchall()
                row_count = len(results)
                
                print(f"✅ Query executed successfully!")
                print(f"📊 Rows returned: {row_count} (LIMIT enforced at {max_rows})")
                print(f"📋 Columns: {', '.join(columns)}\n")
                
                # Convert results to list of dicts
                result_data = [dict(zip(columns, row)) for row in results]
                
                return {
                    "success": True,
                    "sql": extracted_sql,
                    "result": result_data,
                    "row_count": row_count,
                    "columns": columns,
                }
                
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        
        print(f"\n{'='*80}")
        print(f"❌ SQL EXECUTION FAILED - {error_type}")
        print(f"{'='*80}")
        print(f"Error Message: {error_msg}")
        print(f"Warehouse ID: {warehouse_id}")
        print(f"{'='*80}\n")
        
        return {
            "success": False,
            "sql": extracted_sql if 'extracted_sql' in locals() else sql_query,
            "result": None,
            "row_count": 0,
            "columns": [],
            "error": f"{error_type}: {error_msg}",
            "error_type": error_type,
        }
