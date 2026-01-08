# Databricks notebook source
# DBTITLE 1,SQL Query Validation Agent
"""
SQL Query Validation Agent

This agent validates SQL queries before execution by:
1. Parsing SQL to extract tables and columns
2. Checking if tables exist in the metadata
3. Verifying all columns exist in their respective tables
4. Reporting specific validation errors with actionable suggestions

Part of the multi-agent system workflow:
Planning Agent → SQL Synthesis Agent → **SQL Validation Agent** → SQL Execution Agent
"""

import json
import re
from typing import Dict, List, Optional, Any, Tuple
from databricks_langchain import (
    ChatDatabricks,
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
)
from langchain.agents import create_agent
import mlflow

# COMMAND ----------

# DBTITLE 1,Configuration
# Configuration - match the same catalog/schema as other agents
CATALOG = "yyang"
SCHEMA = "multi_agent_genie"
TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"

print(f"SQL Validation Agent Configuration:")
print(f"  - Catalog: {CATALOG}")
print(f"  - Schema: {SCHEMA}")
print(f"  - Metadata Table: {TABLE_NAME}")
print("="*80)

# COMMAND ----------

# DBTITLE 1,SQL Parsing Utility Functions
def extract_tables_from_sql(sql_query: str) -> List[str]:
    """
    Extract table names from SQL query.
    
    Args:
        sql_query: SQL query string
        
    Returns:
        List of unique table names (catalog.schema.table format)
    """
    # Pattern to match table names in FROM and JOIN clauses
    # Handles: FROM table, JOIN table, FROM catalog.schema.table AS alias
    patterns = [
        r'(?:FROM|JOIN)\s+([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)',  # 3-level names
        r'(?:FROM|JOIN)\s+([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)',  # 2-level names
        r'(?:FROM|JOIN)\s+([a-zA-Z0-9_]+)',  # 1-level names
    ]
    
    tables = set()
    sql_upper = sql_query.upper()
    
    for pattern in patterns:
        matches = re.finditer(pattern, sql_query, re.IGNORECASE)
        for match in matches:
            table_name = match.group(1)
            # Remove alias if present
            table_name = re.sub(r'\s+AS\s+\w+', '', table_name, flags=re.IGNORECASE)
            table_name = table_name.strip()
            tables.add(table_name)
    
    return list(tables)


def extract_columns_from_sql(sql_query: str) -> Dict[str, List[str]]:
    """
    Extract column references from SQL query.
    
    Args:
        sql_query: SQL query string
        
    Returns:
        Dict mapping table_alias/table_name to list of column names
        Format: {
            "medical_claim": ["allowed_amount", "patient_id"],
            "diagnosis": ["icd10_code", "claim_id"]
        }
    """
    columns_by_table = {}
    
    # Pattern to match table_alias.column_name or table_name.column_name
    # Handles: mc.allowed_amount, medical_claim.patient_id
    pattern = r'([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)'
    
    matches = re.finditer(pattern, sql_query, re.IGNORECASE)
    for match in matches:
        table_ref = match.group(1)
        column_name = match.group(2)
        
        # Skip SQL keywords
        keywords = {'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 
                   'OUTER', 'GROUP', 'ORDER', 'BY', 'HAVING', 'AS', 'ON', 'AND', 'OR'}
        if table_ref.upper() in keywords or column_name.upper() in keywords:
            continue
            
        if table_ref not in columns_by_table:
            columns_by_table[table_ref] = []
        columns_by_table[table_ref].append(column_name)
    
    # Deduplicate columns
    for table_ref in columns_by_table:
        columns_by_table[table_ref] = list(set(columns_by_table[table_ref]))
    
    return columns_by_table


def extract_table_aliases(sql_query: str) -> Dict[str, str]:
    """
    Extract table alias mappings from SQL query.
    
    Args:
        sql_query: SQL query string
        
    Returns:
        Dict mapping alias to full table name
        Format: {"mc": "medical_claim", "d": "diagnosis"}
    """
    alias_map = {}
    
    # Pattern to match: FROM/JOIN table_name [AS] alias
    patterns = [
        r'(?:FROM|JOIN)\s+([a-zA-Z0-9_.]+)\s+AS\s+([a-zA-Z0-9_]+)',
        r'(?:FROM|JOIN)\s+([a-zA-Z0-9_.]+)\s+([a-zA-Z0-9_]+)(?:\s|,|$)',
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, sql_query, re.IGNORECASE)
        for match in matches:
            table_name = match.group(1)
            alias = match.group(2)
            
            # Skip if alias is a SQL keyword
            keywords = {'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'WHERE', 'GROUP', 'ORDER'}
            if alias.upper() not in keywords:
                alias_map[alias] = table_name
    
    return alias_map

# COMMAND ----------

# DBTITLE 1,Validation Logic Functions
def validate_sql_query(
    sql_query: str,
    uc_functions_available: bool = True
) -> Dict[str, Any]:
    """
    Validate SQL query by checking tables and columns against metadata.
    
    This is the main validation function that orchestrates the validation process.
    
    Args:
        sql_query: The SQL query to validate
        uc_functions_available: Whether UC function tools are available for metadata lookup
        
    Returns:
        Validation result dictionary with structure:
        {
            "is_valid": bool,
            "sql_query": str,
            "validation_details": {
                "tables": {
                    "extracted": List[str],
                    "missing": List[str],
                    "found": List[str]
                },
                "columns": {
                    "extracted": Dict[str, List[str]],
                    "missing": Dict[str, List[str]],
                    "found": Dict[str, List[str]]
                }
            },
            "errors": List[str],
            "warnings": List[str],
            "suggestions": List[str]
        }
    """
    # Extract SQL components
    extracted_tables = extract_tables_from_sql(sql_query)
    extracted_columns = extract_columns_from_sql(sql_query)
    table_aliases = extract_table_aliases(sql_query)
    
    # Resolve aliases to table names
    resolved_columns = {}
    for alias_or_table, cols in extracted_columns.items():
        # Check if it's an alias
        if alias_or_table in table_aliases:
            actual_table = table_aliases[alias_or_table]
        else:
            actual_table = alias_or_table
        resolved_columns[actual_table] = cols
    
    # Initialize result structure
    result = {
        "is_valid": True,
        "sql_query": sql_query,
        "validation_details": {
            "tables": {
                "extracted": extracted_tables,
                "missing": [],
                "found": []
            },
            "columns": {
                "extracted": resolved_columns,
                "missing": {},
                "found": {}
            },
            "aliases": table_aliases
        },
        "errors": [],
        "warnings": [],
        "suggestions": []
    }
    
    # Basic syntax check
    if not sql_query.strip().upper().startswith('SELECT'):
        result["is_valid"] = False
        result["errors"].append("SQL query must start with SELECT")
        return result
    
    if not extracted_tables:
        result["warnings"].append("No tables could be extracted from SQL query. Manual verification recommended.")
        return result
    
    # If UC functions are available, validation will be done by the agent
    # This function provides the structure and basic validation
    # The agent will use UC function tools to check table/column existence
    
    if not uc_functions_available:
        result["warnings"].append(
            "UC function tools not available. Cannot validate table and column existence. "
            "SQL validation will be partial."
        )
    
    return result

# COMMAND ----------

# DBTITLE 1,Create SQL Validation Agent with UC Tools
"""
Create SQL Validation Agent using the same UC functions as SQL Synthesis Agent

The agent will:
1. Parse the SQL query to extract tables and columns
2. Use UC function tools to check metadata
3. Report validation results with specific errors and suggestions
"""

# Initialize Databricks Function Client
client = DatabricksFunctionClient()
set_uc_function_client(client)

# Initialize LLM
LLM_ENDPOINT_NAME = "databricks-claude-sonnet-4-5"  # Use more powerful model for validation
llm = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME, temperature=0.0)  # Temperature 0 for deterministic validation

# Create UC Function Toolkit with the same functions as SQL Synthesis Agent
uc_function_names = [
    f"{CATALOG}.{SCHEMA}.get_space_summary",
    f"{CATALOG}.{SCHEMA}.get_table_overview",
    f"{CATALOG}.{SCHEMA}.get_column_detail",
    f"{CATALOG}.{SCHEMA}.get_space_details",
]

uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
tools = uc_toolkit.tools

print(f"✓ Created UCFunctionToolkit with {len(tools)} tools:")
for tool in tools:
    print(f"  - {tool.name}")

# Create SQL Validation Agent
sql_validation_agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=(
        "You are a specialized SQL validation agent in a multi-agent system.\n\n"
        
        "## ROLE:\n"
        "You receive a SQL query from the SQL Synthesis Agent and validate it before execution.\n"
        "Your ONLY job is to ensure the SQL is executable by checking:\n"
        "1. All referenced tables exist in the metadata\n"
        "2. All referenced columns exist in their respective tables\n"
        "3. Table joins are using valid column names\n"
        "4. Basic SQL syntax is correct\n\n"
        
        "## ASSUMPTIONS:\n"
        "- The SQL query has been generated by the SQL Synthesis Agent\n"
        "- The query structure is generally sound\n"
        "- You need to verify the existence of tables and columns against actual metadata\n\n"
        
        "## WORKFLOW:\n"
        "1. Parse the SQL query to extract:\n"
        "   - Table names (including catalog.schema.table)\n"
        "   - Column names and their table references\n"
        "   - Table aliases and their mappings\n"
        "2. For each table referenced:\n"
        "   a) Call get_table_overview to verify table exists\n"
        "   b) If table not found, record as error\n"
        "3. For each column referenced:\n"
        "   a) Call get_column_detail for the specific table and column\n"
        "   b) If column not found, record as error\n"
        "4. Compile validation results:\n"
        "   - List all missing tables\n"
        "   - List all missing columns (grouped by table)\n"
        "   - Provide specific suggestions for corrections\n\n"
        
        "## UC FUNCTION USAGE STRATEGY:\n"
        "- Start with get_table_overview to check if tables exist\n"
        "- Use get_column_detail only for columns that need verification\n"
        "- Pass space_ids_json, table_names_json, column_names_json as JSON array strings\n"
        "  Example: '[\"table_name_1\", \"table_name_2\"]'\n"
        "- If you don't know which space a table belongs to, call get_space_summary first\n"
        "- Use minimal sufficient queries to reduce token usage\n\n"
        
        "## OUTPUT REQUIREMENTS:\n"
        "Return a JSON object with this exact structure:\n"
        "{\n"
        "    \"is_valid\": true/false,\n"
        "    \"sql_query\": \"<original SQL query>\",\n"
        "    \"validation_details\": {\n"
        "        \"tables\": {\n"
        "            \"extracted\": [\"table1\", \"table2\"],\n"
        "            \"missing\": [\"table_x\"],\n"
        "            \"found\": [\"table1\", \"table2\"]\n"
        "        },\n"
        "        \"columns\": {\n"
        "            \"extracted\": {\"table1\": [\"col1\", \"col2\"]},\n"
        "            \"missing\": {\"table1\": [\"col_x\"]},\n"
        "            \"found\": {\"table1\": [\"col1\", \"col2\"]}\n"
        "        }\n"
        "    },\n"
        "    \"errors\": [\n"
        "        \"Table 'table_x' does not exist in metadata\",\n"
        "        \"Column 'col_x' does not exist in table 'table1'\"\n"
        "    ],\n"
        "    \"warnings\": [\n"
        "        \"Table alias 'mc' might be ambiguous\"\n"
        "    ],\n"
        "    \"suggestions\": [\n"
        "        \"Replace 'table_x' with 'table_y'\",\n"
        "        \"Replace 'col_x' with 'col_z' in table 'table1'\"\n"
        "    ]\n"
        "}\n\n"
        
        "## VALIDATION RULES:\n"
        "- is_valid = true ONLY if no errors found\n"
        "- is_valid = false if any table or column is missing\n"
        "- Warnings don't affect is_valid status\n"
        "- Provide actionable suggestions for each error\n"
        "- If you cannot find metadata for a table/column, mark it as missing\n\n"
        
        "Return ONLY the JSON object, no explanations or markdown formatting."
    ),
)

print("\n" + "="*80)
print("✅ SQL Validation Agent created successfully!")
print("="*80)
print("\nAgent Configuration:")
print(f"  - LLM: {LLM_ENDPOINT_NAME}")
print(f"  - Temperature: 0.0 (deterministic)")
print(f"  - Tools: {len(tools)} UC functions")
print(f"  - Agent Type: LangChain Tool-Calling Agent")
print("\n✅ Ready to validate SQL queries!")

# COMMAND ----------

# DBTITLE 1,Wrapper Function for Multi-Agent Integration
def validate_sql_with_agent(
    sql_query: str,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Wrapper function to validate SQL query using the SQL Validation Agent.
    
    This function is designed to be called by the Super Agent in the multi-agent workflow.
    
    Args:
        sql_query: The SQL query to validate (can be raw SQL or agent response object)
        context: Optional context from previous agents (planning agent output, etc.)
        
    Returns:
        Validation result dictionary
    """
    # Extract SQL from agent response if needed
    if isinstance(sql_query, dict) and "messages" in sql_query:
        # Agent response format
        final_message = sql_query["messages"][-1]
        sql_content = final_message.content
        
        # Extract SQL from markdown code blocks if present
        if "```sql" in sql_content.lower():
            match = re.search(r'```sql\s*(.*?)\s*```', sql_content, re.IGNORECASE | re.DOTALL)
            if match:
                sql_content = match.group(1).strip()
        elif "```" in sql_content:
            match = re.search(r'```\s*(.*?)\s*```', sql_content, re.DOTALL)
            if match:
                sql_content = match.group(1).strip()
        
        sql_query = sql_content
    
    # Prepare agent message
    agent_message = {
        "messages": [
            {
                "role": "user",
                "content": f"""
Validate the following SQL query:

```sql
{sql_query}
```

Context from previous agents:
{json.dumps(context, indent=2) if context else "No context provided"}

Check:
1. All tables exist in the metadata
2. All columns exist in their respective tables
3. Table aliases are correctly mapped
4. Provide specific error messages and suggestions

Return validation results as JSON.
"""
            }
        ]
    }
    
    # Enable MLflow autologging for tracing
    mlflow.langchain.autolog()
    
    # Invoke the validation agent
    print("\n" + "="*80)
    print("🔍 VALIDATING SQL QUERY")
    print("="*80)
    print(f"SQL Query:\n{sql_query[:200]}...")
    print("="*80 + "\n")
    
    result = sql_validation_agent.invoke(agent_message)
    
    # Extract validation result from agent response
    if result and "messages" in result:
        final_message = result["messages"][-1]
        response_text = final_message.content
        
        # Parse JSON response
        try:
            # Remove markdown code blocks if present
            json_str = response_text.strip()
            if "```json" in json_str.lower():
                match = re.search(r'```json\s*(.*?)\s*```', json_str, re.IGNORECASE | re.DOTALL)
                if match:
                    json_str = match.group(1).strip()
            elif "```" in json_str:
                match = re.search(r'```\s*(.*?)\s*```', json_str, re.DOTALL)
                if match:
                    json_str = match.group(1).strip()
            
            validation_result = json.loads(json_str)
            
            print("\n" + "="*80)
            print("✅ VALIDATION COMPLETE")
            print("="*80)
            print(f"Is Valid: {validation_result.get('is_valid', False)}")
            print(f"Errors: {len(validation_result.get('errors', []))}")
            print(f"Warnings: {len(validation_result.get('warnings', []))}")
            print("="*80 + "\n")
            
            return validation_result
            
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse validation result: {e}")
            return {
                "is_valid": False,
                "sql_query": sql_query,
                "errors": [f"Validation agent returned invalid JSON: {str(e)}"],
                "warnings": [],
                "suggestions": ["Re-run validation or manually check SQL query"]
            }
    
    # Fallback if agent didn't return expected format
    return {
        "is_valid": False,
        "sql_query": sql_query,
        "errors": ["Validation agent did not return expected format"],
        "warnings": [],
        "suggestions": ["Re-run validation with proper agent configuration"]
    }

print("\n" + "="*80)
print("✅ SQL VALIDATION AGENT READY FOR MULTI-AGENT INTEGRATION")
print("="*80)
print("\nWrapper Function: validate_sql_with_agent()")
print("\nUsage in Multi-Agent System:")
print("  1. SQL Synthesis Agent generates SQL")
print("  2. Super Agent calls validate_sql_with_agent(sql_query, context)")
print("  3. Validation Agent checks tables/columns using UC functions")
print("  4. Returns validation result (is_valid, errors, suggestions)")
print("  5. If valid → proceed to SQL Execution Agent")
print("  6. If invalid → return to SQL Synthesis Agent with errors")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Test the SQL Validation Agent
# Example test case - you can uncomment and run this after setting up the agent

"""
# Example SQL query to validate
test_sql = '''
SELECT 
    mc.patient_id,
    mc.allowed_amount,
    d.icd10_code,
    e.birth_year,
    CASE 
        WHEN YEAR(CURRENT_DATE()) - e.birth_year < 18 THEN '0-17'
        WHEN YEAR(CURRENT_DATE()) - e.birth_year BETWEEN 18 AND 34 THEN '18-34'
        WHEN YEAR(CURRENT_DATE()) - e.birth_year BETWEEN 35 AND 49 THEN '35-49'
        WHEN YEAR(CURRENT_DATE()) - e.birth_year BETWEEN 50 AND 64 THEN '50-64'
        ELSE '65+'
    END AS age_group,
    mc.payer_type
FROM yyang.multi_agent_genie.medical_claim mc
JOIN yyang.multi_agent_genie.diagnosis d ON mc.claim_id = d.claim_id
JOIN yyang.multi_agent_genie.enrollment e ON mc.patient_id = e.patient_id
WHERE d.icd10_code LIKE 'E11%'  -- Diabetes Type 2
GROUP BY mc.payer_type, age_group
'''

# Test with context from planning agent
test_context = {
    "relevant_space_ids": ["01f0956a387714969edde65458dcc22a", "01f0956a4b0512e2a8aa325ffbac821b"],
    "execution_plan": "fast_route"
}

# Validate the SQL
validation_result = validate_sql_with_agent(test_sql, context=test_context)

# Display results
print("\n" + "="*80)
print("VALIDATION RESULTS")
print("="*80)
print(json.dumps(validation_result, indent=2))
print("="*80)
"""

# COMMAND ----------

# MAGIC %md
# MAGIC ## Integration with Multi-Agent System
# MAGIC 
# MAGIC ### Workflow Position:
# MAGIC ```
# MAGIC User Query
# MAGIC     ↓
# MAGIC Clarification Agent
# MAGIC     ↓
# MAGIC Planning Agent
# MAGIC     ↓
# MAGIC SQL Synthesis Agent
# MAGIC     ↓
# MAGIC **SQL Validation Agent** ← YOU ARE HERE
# MAGIC     ↓ (if valid)
# MAGIC SQL Execution Agent
# MAGIC     ↓
# MAGIC Final Answer
# MAGIC ```
# MAGIC 
# MAGIC ### Key Features:
# MAGIC 
# MAGIC 1. **Parsing Logic**: Extracts tables, columns, and aliases from SQL
# MAGIC 2. **Metadata Verification**: Uses UC functions to check existence
# MAGIC 3. **Detailed Reporting**: Provides specific errors and suggestions
# MAGIC 4. **Multi-Agent Compatible**: Fits into LangGraph supervisor workflow
# MAGIC 5. **MLflow Tracked**: All validations are logged for debugging
# MAGIC 
# MAGIC ### Benefits:
# MAGIC 
# MAGIC ✅ **Prevents Execution Errors**: Catches invalid SQL before execution  
# MAGIC ✅ **Actionable Feedback**: Provides specific suggestions for fixes  
# MAGIC ✅ **Token Efficient**: Only queries needed metadata  
# MAGIC ✅ **Reusable**: Can validate SQL from any source  
# MAGIC ✅ **Traceable**: MLflow logging for all validations  
# MAGIC 
# MAGIC ### Next Steps:
# MAGIC 
# MAGIC 1. Integrate into Super Agent (agent.py)
# MAGIC 2. Add conditional routing: valid → execute, invalid → re-synthesize
# MAGIC 3. Test with edge cases and malformed SQL
# MAGIC 4. Deploy to Unity Catalog as part of multi-agent system

# COMMAND ----------

print("="*80)
print("🎉 SQL VALIDATION AGENT IMPLEMENTATION COMPLETE")
print("="*80)
print("\n📚 Components Created:")
print("  1. SQL parsing utilities (extract tables, columns, aliases)")
print("  2. Validation logic with UC function integration")
print("  3. SQL Validation Agent with tool-calling capability")
print("  4. Multi-agent wrapper function (validate_sql_with_agent)")
print("\n🔗 Ready for Super Agent integration!")
print("  - Add as InCodeSubAgent to LangGraph supervisor")
print("  - Insert between SQL Synthesis and SQL Execution agents")
print("  - Enable conditional routing based on validation results")
print("="*80)
