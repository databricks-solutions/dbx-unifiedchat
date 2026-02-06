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


def extract_execution_context(state: AgentState) -> dict:
    """Extract minimal context for SQL execution."""
    return {
        "sql_query": state.get("sql_query")
    }


def sql_execution_node(state: AgentState) -> dict:
    """
    SQL execution node wrapping SQLExecutionAgent class.
    Combines OOP modularity with explicit state management.
    
    OPTIMIZED: Uses minimal state extraction to reduce token usage
    
    Returns: Dictionary with only the state updates (for clean MLflow traces)
    """
    writer = get_stream_writer()
    
    print("\n" + "="*80)
    print("🚀 SQL EXECUTION AGENT (Token Optimized)")
    print("="*80)
    
    # OPTIMIZATION: Extract only minimal context needed for execution
    context = extract_execution_context(state)
    print(f"📊 State optimization: Using {len(context)} fields (vs {len([k for k in state.keys() if state.get(k) is not None])} in full state)")
    
    sql_query = context.get("sql_query")
    
    if not sql_query:
        print("❌ No SQL query to execute")
        # Return error update
        return {
            "execution_error": "No SQL query provided"
        }
    
    # Emit validation start event
    writer({"type": "sql_validation_start", "query": sql_query[:200]})
    
    # Emit execution start event
    writer({"type": "sql_execution_start", "estimated_complexity": "standard"})
    
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
    # Note: SQLExecutionAgent should be imported from appropriate module
    # For now, we'll use a placeholder that needs to be implemented
    try:
        from ..agents.sql_execution_agent import SQLExecutionAgent
        execution_agent = SQLExecutionAgent(warehouse_id=sql_warehouse_id)
        result = execution_agent(sql_query)
    except ImportError:
        # Fallback: Execute SQL directly if SQLExecutionAgent not available
        # This is a simplified version - full implementation should use SQLExecutionAgent
        result = _execute_sql_fallback(sql_query, sql_warehouse_id)
    
    # Prepare updates based on result
    updates = {
        "execution_result": result,
        "next_agent": "summarize",
        "messages": []
    }
    
    if result["success"]:
        print(f"✓ Query executed successfully!")
        print(f"📊 Rows returned: {result['row_count']}")
        print(f"📋 Columns: {', '.join(result['columns'])}")
        
        # Emit execution complete event
        writer({"type": "sql_execution_complete", "rows": result['row_count'], "columns": result['columns']})
        
        updates["messages"].append(
            SystemMessage(content=f"Execution successful: {result['row_count']} rows returned")
        )
    else:
        print(f"❌ SQL execution failed: {result.get('error', 'Unknown error')}")
        updates["execution_error"] = result.get("error")
        
        updates["messages"].append(
            SystemMessage(content=f"Execution failed: {result.get('error')}")
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
