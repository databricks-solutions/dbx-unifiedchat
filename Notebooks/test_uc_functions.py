"""
Unity Catalog Functions for Multi-Agent System

This module contains UC function implementations for the custom agents:
- Query Planning and Analysis
- SQL Synthesis
- SQL Execution

These functions can be registered in Unity Catalog and called by the LangGraph supervisor.
"""

import json
from typing import Dict, List, Optional, Any
from databricks.sdk.runtime import spark


"""
Analyze a user query and create an execution plan.

This function:
1. Checks if the query is clear or needs clarification
2. Searches for relevant Genie spaces using vector search
3. Determines execution strategy (single/multi-space, join requirements)

Args:
    query: The user's question
    vector_search_index: Full name of the vector search index
    num_results: Number of relevant spaces to retrieve
    
Returns:
    JSON string with QueryPlan structure containing:
    - question_clear: bool
    - clarification_needed: str (if applicable)
    - clarification_options: list[str] (if applicable)
    - sub_questions: list[str]
    - requires_multiple_spaces: bool
    - relevant_space_ids: list[str]
    - requires_join: bool
    - join_strategy: str ("fast_route" or "slow_route")
    - execution_plan: str
"""
from databricks_langchain import ChatDatabricks

# Initialize LLM for analysis
llm = ChatDatabricks(endpoint="databricks-claude-sonnet-4-5")
query = "How many patients are in the database?"

# 1. Function to query delta table by field
def query_delta_table(table_name: str, filter_field: str, filter_value: str, select_fields: List[str] = None) -> Any:
    """
    Query a delta table with a filter condition.
    
    Args:
        table_name: Full table name (catalog.schema.table)
        filter_field: Field name to filter on
        filter_value: Value to filter by
        select_fields: List of fields to select (None = all fields)
    
    Returns:
        Spark DataFrame with query results
    """
    if select_fields:
        fields_str = ", ".join(select_fields)
    else:
        fields_str = "*"
    
    df = spark.sql(f"""
        SELECT {fields_str}
        FROM {table_name}
        WHERE {filter_field} = '{filter_value}'
    """)
    
    return df

# 2. Call the function to get space_summary chunks
table_name = "yyang.multi_agent_genie.enriched_genie_docs_chunks"
space_summary_df = query_delta_table(
    table_name=table_name,
    filter_field="chunk_type",
    filter_value="space_summary",
    select_fields=["space_id", "space_name", "searchable_content"]
)

# Display the table
print("Space Summary Data from Delta Table:")
print("="*80)
space_summary_df.show(truncate=False)
print("="*80)

# 3. Convert table to JSON with one entry per space
space_summary_list = space_summary_df.collect()
context = {}

for row in space_summary_list:
    space_id = row["space_id"]
    context[space_id] = {
        "space_id": space_id,
        "space_name": row["space_name"],
        "searchable_content": row["searchable_content"]
    }

# Display the context JSON
print("\nContext JSON (per space):")
print("="*80)
print(json.dumps(context, indent=2))
print("="*80)
print()

# Step 1: Check query clarity
# TODO: include {context} in the prompt, context could be some part of the VS results that is relevant to the question
clarity_prompt = f"""
Analyze the following question for clarity and specificity based on the context.

Question: {query}

Context: {json.dumps(context, indent=2)}

Determine if:
1. The question is clear and answerable as-is
2. The question needs clarification

If clarification is needed, provide:
- A brief explanation of what's unclear
- 2-3 specific clarification options the user can choose from

Return your analysis as JSON:
{{
    "question_clear": true/false,
    "clarification_needed": "explanation if unclear",
    "clarification_options": ["option 1", "option 2", "option 3"]
}}

Only return valid JSON, no explanations.
"""

clarity_response = llm.invoke(clarity_prompt)

# Extract JSON from the response (handle markdown code blocks)
response_text = clarity_response.content

# Debug: print raw response
print("Raw LLM Response:")
print(response_text)
print("\n" + "="*80 + "\n")

# Try to extract JSON from markdown code blocks
import re
json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response_text, re.DOTALL)
if json_match:
    json_str = json_match.group(1).strip()
else:
    # If no code blocks, try to use the whole response
    json_str = response_text.strip()

# Parse JSON
try:
    clarity_result = json.loads(json_str)
    print("Parsed clarity result:")
    print(json.dumps(clarity_result, indent=2))
except json.JSONDecodeError as e:
    print(f"JSON parsing error: {e}")
    print(f"Attempted to parse: {json_str[:200]}...")
    raise
