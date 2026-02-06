# Databricks notebook source
# DBTITLE 1,Install Packages
# MAGIC %pip install databricks-langchain==0.12.1 databricks-vectorsearch==0.63

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

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
# from databricks.sdk.runtime import spark

# COMMAND ----------

# DBTITLE 1,help func dynamic query delta table
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
    select_fields=["space_id", "space_title", "searchable_content"]
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
    context[space_id] = row["searchable_content"]
    

# Display the context JSON
print("\nContext JSON (per space):")
print("="*80)
print(json.dumps(context, indent=2))
print("="*80)
print()

# COMMAND ----------

context

# COMMAND ----------

from databricks_langchain import ChatDatabricks

# Initialize LLM for analysis
llm = ChatDatabricks(endpoint="databricks-claude-haiku-4-5")# "databricks-claude-sonnet-4-5"

# COMMAND ----------

# MAGIC %md
# MAGIC upon restart run until here

# COMMAND ----------

# DBTITLE 1,Simulate Mock Testing Questions

query = "Provide me three questions each need to be answered by joining two or more Genie spaces."

# Step 1: Check query clarity
json_template = {
    "questions": [
        {
            "question": "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?",
            "spaces_required": [
                "HealthVerityClaims",
                "HealthVerityProcedureDiagnosis",
                "HealthVerityProviderEnrollment"
            ],
            "reasoning": "Requires joining medical_claim (HealthVerityClaims) for cost data and payer type, diagnosis (HealthVerityProcedureDiagnosis) for diabetes ICD-10 codes, and enrollment (HealthVerityProviderEnrollment) for patient birth year to calculate age groups"
        },
        {
            "question": "Which medical procedures have the highest pharmacy claim costs within 30 days post-procedure, and what are the most commonly prescribed medications?",
            "spaces_required": [
                "HealthVerityClaims",
                "HealthVerityProcedureDiagnosis"
            ],
            "reasoning": "Requires joining procedure (HealthVerityProcedureDiagnosis) for CPT/HCPCS procedure codes and service dates with pharmacy_claim (HealthVerityClaims) for medication NDC codes and costs, matching by patient_id and date ranges"
        }
    ]
}

clarity_prompt = f"""
You are a helpful assistant that can analyze a user query and provide a JSON response with the following structure:
{json.dumps(json_template, indent=2)}

Question: {query}

Context: {json.dumps(context, indent=2)}

Provide your answer in JSON format with the same structure as above.

Only return valid JSON, no explanations.
"""

clarity_response = llm.invoke(clarity_prompt)

json_str = clarity_response.content.strip('```json')
# Parse JSON
try:
    clarity_result = json.loads(json_str)
    print("Parsed clarity result:")
    print(json.dumps(clarity_result, indent=2))
except json.JSONDecodeError as e:
    print(f"JSON parsing error: {e}")
    print(f"Attempted to parse: {json_str[:200]}...")
    raise

# COMMAND ----------

# DBTITLE 1,Clarification Agent

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
    - join_strategy: str ("table_route" or "genie_route")
    - execution_plan: str
"""
from databricks_langchain import ChatDatabricks

# Initialize LLM for analysis
llm = ChatDatabricks(endpoint="databricks-claude-haiku-4-5")

query = "How many patients are ?"
query = "How many patients are in the dataset (total unique patients)?"
query = 'What is the average cost of medical claims in 2024?' # reminds not clear and provide hints to clarify, see below next query.
query = "What is the average cost per medical claim? For cost, use the average allowed amount per claim as a whole."
query = "Calculate the average allowed amount using the procedure table (procedure-level allowed amounts), then average across all unique claims"
query = "Calculate the average allowed amount per procedure line, then for each claim calculate the sum of all procedure allowed amounts, then average those claim totals across all unique claims"
query = 'What is the average cost of medical claims in 2024?' # reminds not clear and provide hints to clarify, see below next query.


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

# COMMAND ----------

print(clarity_prompt)

# COMMAND ----------

clarity_response

# COMMAND ----------

# Extract JSON from the response (handle markdown code blocks)
response_text = clarity_response.content

# Debug: print raw response
print("Raw LLM Response:")
print(response_text)
print("\n" + "="*80 + "\n")

# COMMAND ----------


# # Try to extract JSON from markdown code blocks
# import re
# json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response_text, re.DOTALL)
# if json_match:
#     json_str = json_match.group(1).strip()
# else:
#     # If no code blocks, try to use the whole response
#     json_str = response_text.strip()

json_str = response_text.strip('```json')
# Parse JSON
try:
    clarity_result = json.loads(json_str)
    print("Parsed clarity result:")
    print(json.dumps(clarity_result, indent=2))
except json.JSONDecodeError as e:
    print(f"JSON parsing error: {e}")
    print(f"Attempted to parse: {json_str[:200]}...")
    raise

# COMMAND ----------

# DBTITLE 1,VS Retriver Tool
vector_search_index: str = "yyang.multi_agent_genie.enriched_genie_docs_chunks_vs_index"
num_results: int = 5
query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?"
query = "How many patients are 65 years old and above?"
query = "How many medical claims are above $500? How many Rx claims are above $1000?"
query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?"
query = 'What is the average cost of medical claims in 2024?' # reminds not clear and provide hints to clarify, see below next query.
query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?"

# Step 2: Search for relevant Genie spaces using AI Bridge VectorSearchRetrieverTool
from databricks_langchain import VectorSearchRetrieverTool

# Create VectorSearchRetrieverTool with filter for space_summary chunks
vs_tool = VectorSearchRetrieverTool(
    index_name=vector_search_index,
    num_results=num_results,
    columns=["space_id", "space_title", "searchable_content"],
    filters={"chunk_type": "space_summary"},
    query_type="ANN",
    include_metadata=True,
    include_score=True
)

# Invoke the tool to get results
docs = vs_tool.invoke({"query": query})

# Extract space information from document metadata
relevant_spaces = []
for doc in docs:
    relevant_spaces.append({
        "space_id": doc.metadata.get("space_id", ""),
        "space_title": doc.metadata.get("space_title", ""),
        "searchable_content": doc.page_content,
        "score": doc.metadata.get("score", 0.0)
    })

if not relevant_spaces:
    relevant_spaces = []

# COMMAND ----------

docs

# COMMAND ----------

relevant_spaces

# COMMAND ----------

context

# COMMAND ----------

doc

# COMMAND ----------

# MAGIC %md
# MAGIC ## planning agent

# COMMAND ----------

query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? Please use genie route"

# COMMAND ----------

query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?"

# COMMAND ----------

query = "What is the average cost of medical claims in 2024?"

# COMMAND ----------

query = "What is the average cost of medical claims in 2024? Use genie route"

# COMMAND ----------

query = "What is the average cost of medical claims in 2024? How many patients are 65 years old and above in 2024?"

# COMMAND ----------

query = "What is the average cost of medical claims in 2024? How many patients are 65 years old and above in 2024? Use genie route"

# COMMAND ----------

query = "What is the average cost of medical claims in 2024? For these claims, how many patients are 65 years old and above in 2024?"

# COMMAND ----------

query = "How many patients are 65 years old and above in 2024? What is the average cost of all medical claims in 2024?"

# COMMAND ----------

query = "How many patients are 65 years old and above in 2024? Among these patients, What is the average cost of all medical claims in 2024?"

# COMMAND ----------

query = "How many patients are 65 years old and above in 2024? Among these patients, What is the average cost of all medical claims in 2024? Use genie route"

# COMMAND ----------

query = 'What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? Use genie route.'

# COMMAND ----------

# DBTITLE 1,Create Planning Agent
# Step 3: Create execution plan with relevant spaces
# at least, relevant spaces have sorted by score in descending order, this will be additional context for the LLM.
import json
import mlflow
from databricks_langchain import ChatDatabricks

# Initialize LLM for analysis
llm = ChatDatabricks(endpoint="databricks-claude-haiku-4-5")# "databricks-claude-sonnet-4-5"

planning_prompt = f"""
You are a query planning expert. Analyze the following question and create an execution plan.

Question: {query}

Potentially relevant Genie spaces:
{json.dumps(relevant_spaces, indent=2)}

Break down the question and determine:
1. What are the sub-questions or analytical components?
2. How many Genie spaces are needed to answer completely? (List their space_ids)
3. If multiple spaces are needed, do we need to JOIN data across them? Reasoning whether the sub-questions are totally independent without joining need, e.g., one sub-question is asking about claims and the other sub-question is asking about patient enrollment, two sub-questions are independent asked in the original question without relying on each other. 
    - JOIN needed: E.g., "How many active plan members over 50 are on Lexapro?" can be split into "How many active plan member over 50?" and "How many members are taking medicine Lexapro?". THey are related and need JOIN later.
    - No need for JOIN: E.g., "How many active plan members over 50? How much total out of pocket cost for all Lexapro Rx claims submitted in Q4 2024?" could be split into "How many active plan member over 50?" and "How much total out of pocket cost for all Lexapro Rx claims submitted in Q4 2024?". Two sub-questions are totally independent without relying on each other to answer.  
4. If JOIN is needed, what's the best strategy:
    - "table_route": Directly synthesize SQL across multiple tables
    - "genie_route": Query each Genie Space Agent separately using reframed partial question which fit into a single space to answer, then combine SQL queries (not quantitative result) returned from Genie Agents to synthesize the final SQL query
    - If user asks explicitly for "genie_route", use the specified strategy in the user query; otherwise, use "table_route" 
5. Execution plan: A brief description of how to execute the plan based on previous steps.
    - Multiple spaces are needed with JOIN   
        - "table_route": how to synthesize SQL across multiple tables  
        - "genie_route": Return a json dictionary of the corresponding Genie space id and the reframed partial question, i.e., {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', 'space_id_3':'partial_question_3'}}. The reframed partial question should be 
            1) to most extent similar to the original question in format and asking style, only removing the extra components like metrics, where filters, group-by dimensions from the original question if they dont exist for that Genie space. For example, if original question is "How many active members older than 50 are on statin and have annual wellness check finished by Dec 2025? Among them, how many we have unclosed HEDIS gaps for them this year?". You should return {{'MemberEnrollment_space':'How many active members are older than 50?', 'MedicalRxClaim_space':'How many members have Rx claims of statin?', 'CareManagement_Space':'How many members have annual wellness check finished by Dec 2025?', 'StarRating_space':'How many members have unclosed HEDIS gaps for them this year?'}}.
            2) to most extent retain the analytical components existed in this Genie space, remove components in the query that do not belong to this Genie space.  
            3) if original question involves SQL aggregation and group by, you should try best in each reframed partial question, also involve SQL aggregation and group by. E.g., "How many active members are older than 50?" is favoured, while "What are the members older than 50?" is not favoured. 
            4) for each partial_question generated, e.g., "How many active members are older than 50?" , add "Please limit to top 10 rows", so the question becomes ""How many active members are older than 50? Please limit to top 10 rows" 
    - Multiple spaces are needed without JOIN
        - ignore "genie_route" specification in user query since for this scenario we only have one route.  
        - query each Genie Space Agent separately using sub-questions which fit into a single space to answer. Keep all Genie Agent results returned; Verbally summarize into a merged conclusion if you think necessary.  
        - also return {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', 'space_id_3':'partial_question_3'}} in the {{"genie_route_plan"}} field of the output JSON
    - Single space is needed
        - ignore "genie_route" specification in user query since for this scenario we only have one route.  
        - route the question to the specific Genie Space Agent to answer the question
        - also return {{'space_id_1':'Original Question'}} in the {{"genie_route_plan"}} field of the output JSON


Return your analysis as JSON:
{{
    "original_query":{query},
    "vector_search_relevant_spaces_info":{[{sp['space_id']: sp['space_title']} for sp in relevant_spaces]},
    "question_clear": true,
    "sub_questions": ["sub-question 1", "sub-question 2", ...],
    "requires_multiple_spaces": true/false,
    "relevant_space_ids": ["space_id_1", "space_id_2", ...],
    "requires_join": true/false,
    "join_strategy": "table_route" or "genie_route" or null,
    "execution_plan": "Brief description of execution plan",
    "genie_route_plan": {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', 'space_id_3':'partial_question_3', ...}} or null,
}}

Only return valid JSON, no explanations.
"""

print(planning_prompt)
#: backup:
# genie route plan: how the origial question can be splited into variant questions with each variant question trying to ask part of the original question? For each space, list the new variant question. The purpose is each of the variant questions could fit into a Genie Space to get answered completely without triggering Genie failure to answer due to missing information. Lastly combine SQL queries (not quantitative result) returned from Genie Agents as context to help synthesize the final SQL query
#---#
# - "genie_route": give a json dictionary of the corresponding Genie space id and the reframed question (contain partial sub-questions/analytical components within the original question) i.e., {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', 'space_id_3':'partial_question_3'}}

# Enable MLflow autologging for tracing
mlflow.langchain.autolog()

planning_response = llm.invoke(planning_prompt)
plan_result = json.loads(planning_response.content.strip('```json').strip('\n'))


# COMMAND ----------

plan_result

# COMMAND ----------

import ast
ast.literal_eval(planning_response.content.strip('```json').strip('\n'))

# COMMAND ----------

f"{plan_result}"

# COMMAND ----------

print(json.dumps(plan_result, indent=2))

# COMMAND ----------

planning_response.content.strip('```json')


# COMMAND ----------

print(planning_response.content.strip('```json').strip('\n'))

# COMMAND ----------

(planning_response.content.strip('```json').strip('\n'))

# COMMAND ----------

json.loads(planning_response.content.strip('```json').strip('\n'))

# COMMAND ----------

import time

# Freeze execution for 3 hours (3 * 60 * 60 seconds)
time.sleep(3 * 60 * 60)

# COMMAND ----------

plan_result

# COMMAND ----------

# DBTITLE 1,no-tool agent failure proof
# this is no-tool agent version
def synthesize_sql_table_route(
    query: str,
) -> str:
    """
    Synthesize SQL query directly across multiple tables (table route).
    
    Args:
        query: The user's question
        table_metadata_json: JSON string with table metadata including:
            - table_name
            - columns
            - relationships
            - sample_data
            
    Returns:
        SQL query string
    """

    prompt = f"""
    You are an expert SQL developer. Generate a SQL query to answer the following question
    using the available tables.
    
    Question: {query}

    Execution Plan:
    {json.dumps(plan_result, indent=2)}

    Available Tables and Columns Metadata from the Genie spaces summary level: 
    {json.dumps(relevant_spaces, indent=2)}

    Try if you can synthesize the SQL query directly from the available metadata already provided.

    If you are not sure, feel free to call tools you have access to get more drill-down information about the relevant tables and columns you can use in forming the SQL query.
    1. call sql UC function to query relevant table level metadata by filtering chunk_type == "table_overview"
    2. call sql UC function to query relevant column level metadata by filtering chunk_type == "column_detail"
    3. last resort, if still not enough, you can query all Metadata information lumped together from calling sql UC function to query chunk_type == "space_details". Be careful not to use it too often cause it will include a lot of tokens.
    
    Generate a complete, executable SQL query. Include:
    - Proper JOINs where needed
    - WHERE clauses for filtering
    - Appropriate aggregations
    - Column aliases for clarity
    - Always use real column name existed in the data, never make up one
    Return ONLY the SQL query, no explanations or markdown formatting. If SQL cannot be generated, explain what metadata is missing
    """
    
    response = llm.invoke(prompt)
    print(prompt)
    sql = response.content.strip()
    
    # Remove markdown code blocks if present
    if sql.startswith("```"):
        lines = sql.split("\n")
        sql = "\n".join(lines[1:-1]) if len(lines) > 2 else sql
    
    return sql

# COMMAND ----------

sql_result = synthesize_sql_table_route(query)

# COMMAND ----------

print(sql_result)

# COMMAND ----------

# MAGIC %md
# MAGIC __Conclusion of no-tool agent version__
# MAGIC 1. on Dec 2025 test, procedure.charge_amount in the generated SQL query doesn't exist at all.
# MAGIC 2. UPDATE: on Jan 7th 2026, due to we totally migrate the pipeline to new workspace (old wksp got deleted), the metainfo might have some change (e.g., space summary) after re-run the pipeline. 
# MAGIC     - 1st test with Claude Sonnet 4.5: The SQL now makes more sense as it returns results sounds right (I have tested it in SQL editor).
# MAGIC     - 2nd test with Claude Haiku 4.5: `AVG(mc.paid_gross_due) AS average_claim_cost`, as you can see we dont have such field in medical claim table; we do have this field in pharmacy claim table, however, our question is not calculating pharmacy claim cost.
# MAGIC
# MAGIC __Final Conclusion:__
# MAGIC Without UC function tools, e.g., SQL tools querying the underlying metadata table, agent with LLM alone are not guranteed to work correctly with enough context/meta info. **Dont use `synthesize_sql_table_route` without any tools being registered.**

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create SQL Synthesis Agent (table route) With UC Tools

# COMMAND ----------

# # -- Use spark.sql to drop a specific function by 3-level name
# spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_space_summary');
# spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_table_overview');
# spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_column_detail');
# spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_space_details');

# COMMAND ----------

"""
Step 1: Register Unity Catalog Functions for Metadata Querying

These UC functions will be used as tools by the LangGraph agent.
Each function queries different levels of the enriched genie docs chunks table.
"""

# Configuration
CATALOG = "yyang"
SCHEMA = "multi_agent_genie"
TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"

print(f"Registering UC functions in: {CATALOG}.{SCHEMA}")
print(f"Target table: {TABLE_NAME}")
print("="*80)


# COMMAND ----------

# DBTITLE 1,Create UC Function Tools for SQL query
"""
Step 2: Create UC Functions using SQL

Register SQL UC functions that query metadata at different levels
All functions use LANGUAGE SQL for better performance and compatibility
"""

# UC Function 1: get_space_summary (SQL scalar function)
spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_space_summary(
    space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs to query, or "null" to retrieve all spaces. Example: ["space_1", "space_2"] or "null"'
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
    FROM {TABLE_NAME}
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
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_table_overview(
    space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs to query (required, prefer single space). Example: ["space_1"]',
    table_names_json STRING DEFAULT 'null' COMMENT 'JSON array of table names to filter, or "null" for all tables in the specified spaces. Example: ["table1", "table2"] or "null"'
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
        FROM {TABLE_NAME}
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
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_column_detail(
    space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs to query (required, prefer single space). Example: ["space_1"]',
    table_names_json STRING DEFAULT 'null' COMMENT 'JSON array of table names to filter (required, prefer single table). Example: ["table1"]',
    column_names_json STRING DEFAULT 'null' COMMENT 'JSON array of column names to filter, or "null" for all columns in the specified tables. Example: ["col1", "col2"] or "null"'
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
        FROM {TABLE_NAME}
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

# UC Function 4: get_space_details (SQL scalar function - last resort)
spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.get_space_details(
    space_ids_json STRING DEFAULT 'null' COMMENT 'JSON array of space IDs to query (required). Example: ["space_1", "space_2"]. WARNING: Returns large metadata - use as LAST RESORT.'
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
    FROM {TABLE_NAME}
    WHERE chunk_type = 'space_details'
    AND array_contains(from_json(space_ids_json, 'array<string>'), space_id)
""")
print("✓ Registered: get_space_details")

print("\n" + "="*80)
print("✅ All 4 UC SQL functions registered successfully!")
print("="*80)


# COMMAND ----------

TABLE_NAME

# COMMAND ----------

# DBTITLE 1,Create table route agent with UC tools
"""
Step 3: Create LangGraph SQL Synthesis Agent with UC Function Toolkit

Uses Databricks LangGraph SDK with create_react_agent pattern
"""

from databricks_langchain import (
    ChatDatabricks,
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
)
from langchain.agents import create_agent
import mlflow

# Initialize Databricks Function Client
client = DatabricksFunctionClient()
set_uc_function_client(client)

# Initialize LLM
# experience:
# 1. prefer to have a more powerful LLM here cause the task for this agent is complex
# 2. I have experience that haiku couldn't work out an edge case but sonnet could.
LLM_ENDPOINT_NAME = "databricks-claude-sonnet-4-5" # options: haiku, sonnet
llm = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME, temperature=0.1)

# Create UC Function Toolkit with the registered functions
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

# Create SQL Synthesis Agent (specialized for multi-agent system)
sql_synthesis_agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=(
        "You are a specialized SQL synthesis agent in a multi-agent system.\n\n"
        "ROLE: You receive execution plans from the planning agent and generate SQL queries.\n\n"

        "## ASSUMPTIONS:\n"
        "- The query has been clarified and validated by upstream agents\n"
        "- The planning agent has identified relevant spaces and execution strategy\n"
        "- Your ONLY job is to synthesize the SQL query based on the plan\n\n"

        "## WORKFLOW:\n"
        "1. Review the execution plan and the provided metadata from planning agent\n"
        "2. If metadata is sufficient → Generate SQL immediately\n"
        "3. If insufficient, tell users you need more metadata, and reveal the thinking process including analyzing which additional part of metadata might be useful\n"
        "4. Call minimal sufficient number of UC function tools with minimal sufficient argument values to help you synthesize the SQL query; call tools in this order, you dont have to finish the order, stop when you have enough metadata:\n"
        "   a) call get_space_summary for related spaces' information including its purpose, its processing logic and how its compotent tables are related and used. Find minimal sufficent needed tables or columns, then call next tool\n"
        "   b) call get_table_overview for specific tables' schemas and relationships, if still not clear about specific columns, call next tool\n"
        "   c) call get_column_detail for wanted column details and sample values\n"
        "5. call get_space_details ONLY as last resort (token intensive!)\n"
        "6. At last, if you still cannot find enough metadata in relevant spaces provided, dont stuck there. Expand the searching scope to all spaces mentioned in the execution plan's 'vector_search_relevant_spaces_info' field. Extract the space_id from 'vector_search_relevant_spaces_info'. \n\n"

        "## UC FUNCTION USAGE:\n"
        "- Pass the argument values, e.g., space_ids_json STRING, table_names_json STRING, column_names_json STRING, each as a JSON array string: '[\"space_id_1\", \"space_id_2\"]' if you have specific space_ids or table_names or column_names to filter on; or pass 'null' if you need all entities under the parent level, e.g., query all tables under a space, then table_names_json should be 'null'; if you need all columns under a table, then column_names_json should be 'null'.\n"
        "- Only limit the query to spaces listed under the execution plan's relevant_space_ids. Same applied to tables and columns since they are under the space. In rare cases, you can search other spaces as WORKFLOW Item 6 described.\n"
        "- Adopt the rule of get_table_overview miminal sufficiency, i.e., if you need only two tables info in a space, call the get_table_overview with space_ids_json vale as '[\"space_id_1\"]', and table_names_json value as '[\"table_name_1\", \"table_name_2\"]', instead of passing table_names_json value as 'null' for getting all tables under a space.\n"
        "- Adopt the rule of get_column_detail miminal sufficiency, i.e., only query minimal sufficient columns details if you really need\n\n"

        "## OUTPUT REQUIREMENTS:\n"
        "- Generate complete, executable SQL with:\n"
        "  * Proper JOINs based on execution plan strategy\n"
        "  * WHERE clauses for filtering\n"
        "  * Appropriate aggregations\n"
        "  * Clear column aliases\n"
        "  * Always use real column name existed in the data, never make up one\n"
        "- Return ONLY the SQL query without explanations or markdown formatting\n"
        "- If SQL cannot be generated, explain what metadata is missing"
    ),
)

print("\n" + "="*80)
print("✅ SQL Synthesis Agent created successfully!")
print("="*80)
print("\nAgent Configuration:")
print(f"  - LLM: {LLM_ENDPOINT_NAME}")
print(f"  - Tools: {len(tools)} UC functions")
print(f"  - Agent Type: LangChain Tool-Calling Agent")


# COMMAND ----------

# Verify the agent is properly created
print("Verification:")
print(f"  - Agent type: {type(sql_synthesis_agent)}")
print(f"  - Agent has invoke method: {hasattr(sql_synthesis_agent, 'invoke')}")
print("\n✅ SQL Synthesis Agent is ready!")
print("✅ Configured for multi-agent system (expects Planning Agent input)")


# COMMAND ----------

# DBTITLE 1,(after restart) create a plan_result example
# plan_result = """{
#     "question_clear": true,
#     "sub_questions": [
#         "Identify patients diagnosed with diabetes from diagnosis codes",
#         "Get medical claim costs for diabetes patients",
#         "Determine insurance payer type for each claim",
#         "Calculate patient age from birth year and service date",
#         "Group patients into age groups",
#         "Calculate average claim cost by payer type and age group"
#     ],
#     "requires_multiple_spaces": true,
#     "relevant_space_ids": [
#         "01f0956a387714969edde65458dcc22a",
#         "01f0956a54af123e9cd23907e8167df9",
#         "01f0956a4b0512e2a8aa325ffbac821b"
#     ],
#     "requires_join": true,
#     "join_strategy": "table_route",
#     "execution_plan": "Use table_route to JOIN across three spaces: (1) HealthVerityProcedureDiagnosis to filter patients with diabetes diagnosis codes (ICD-10), (2) HealthVerityClaims to get medical claim costs and payer types, and (3) HealthVerityProviderEnrollment to get patient birth year for age calculation. JOIN on patient_id and claim_id, calculate age groups from birth year and service date, then aggregate average costs by payer type and age group."
# }"""
# plan_result = json.loads(plan_result.strip('```json'))

# COMMAND ----------

plan_result

# COMMAND ----------

"""
Step 4: Test the SQL Synthesis Agent

Demonstrates the agent using UC functions to intelligently query metadata
"""

# Example query
example_query = plan_result['original_query']

# Extract space_ids from earlier plan_result or relevant_spaces
# For this example, we'll use the identified relevant spaces
if 'plan_result' in globals() and 'relevant_space_ids' in plan_result:
    space_ids_for_query = plan_result['relevant_space_ids']
else:
    # Fallback to all spaces from context
    space_ids_for_query = list(context.keys()) if 'context' in globals() else []

print("=" * 80)
print("TESTING: SQL Synthesis Agent with UC Functions")
print("=" * 80)
print(f"\nQuery: {example_query}")
print(f"\nRelevant Space IDs: {space_ids_for_query}")
print("\n" + "-" * 80)
print("Agent will:")
print("  1. Call UC functions to query metadata at appropriate levels")
print("  2. Start with space summaries, drill down as needed")
print("  3. Synthesize SQL query based on gathered metadata")
print("-" * 80 + "\n")

# Create the message for the agent
agent_message = {
    "messages": [
        {
            "role": "user",
            "content": f"""
Generate a SQL query to answer the question according to the following Query Plan:
{json.dumps(plan_result, indent=2)}

Use your available UC function tools to gather metadata intelligently.
"""
        }
    ]
}
# Generate a SQL query to answer this question: {example_query}


# Enable MLflow autologging for tracing
mlflow.langchain.autolog()

# Invoke the agent
print("🤖 Invoking SQL Synthesis Agent...")
print("="*80 + "\n")

result = sql_synthesis_agent.invoke(agent_message)

print("\n" + "="*80)
print("✅ AGENT EXECUTION COMPLETE")
print("="*80)
print("\nFinal Response:")
print("-"*80)
# Extract the final message content
if result and "messages" in result:
    final_message = result["messages"][-1]
    print(final_message.content)
print("-"*80)


# COMMAND ----------

# Extract SQL from final message
if result and "messages" in result:
    final_content = result["messages"][-1].content
    
    # Try to extract SQL from markdown code blocks if present
    if "```sql" in final_content.lower():
        # Extract content between ```sql and ```
        import re
        sql_match = re.search(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
        
    elif "```" in final_content:
        # Extract any code block
        import re
        sql_match = re.search(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
    else:
        sql_match = None
    
    if sql_match:
            print(sql_match.group(1).strip())

# COMMAND ----------

example_query

# COMMAND ----------

import time

# Freeze execution for 4 hours (4 * 60 * 60 seconds)
time.sleep(6 * 60 * 60)

# COMMAND ----------

# MAGIC %md
# MAGIC ## SQL Execuation Tool (dont register as UC function cause it contains spark operation)
# MAGIC
# MAGIC This is a tool and suppose Super Agent should call it to execute the SQL queries generated.

# COMMAND ----------

########################################
# UC Function: Execute SQL - robust and complex version
########################################

import re
import json
from typing import Dict, Any, Optional, List
import pandas as pd


def execute_sql_on_delta_tables(
    sql_query: str,
    max_rows: int = 100,
    return_format: str = "dict"  # Options: "dict", "dataframe", "json", "markdown"
) -> Dict[str, Any]:
    """
    Execute SQL query on delta tables and return formatted results.
    
    Args:
        sql_query: support two types: 1), The result from invoke the SQL systhesis agent; 2), The SQL query to execute (can be raw SQL or contain markdown code blocks)
        max_rows: Maximum number of rows to return (default: 100)
        return_format: Format of the result - "dict", "dataframe", "json", or "markdown"
    
    Returns:
        Dictionary containing:
        - success: bool - Whether execution was successful
        - sql: str - The executed SQL query
        - result: Any - Query results in requested format
        - row_count: int - Number of rows returned
        - columns: List[str] - Column names
        - error: str - Error message if failed (optional)
    """
    
    # Step 1: Extract SQL from markdown code blocks if present
    if sql_query and "messages" in sql_query:
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
    
    # Step 2: Add LIMIT clause if not present (for safety)
    if "limit" not in extracted_sql.lower():
        extracted_sql = f"{extracted_sql.rstrip(';')} LIMIT {max_rows}"
    
    try:
        # Step 3: Execute the SQL query
        print(f"\n{'='*80}")
        print("🔍 EXECUTING SQL QUERY")
        print(f"{'='*80}")
        print(f"SQL:\n{extracted_sql}")
        print(f"{'='*80}\n")
        
        df = spark.sql(extracted_sql)
        
        # Step 4: Collect results
        results_list = df.collect()
        row_count = len(results_list)
        columns = df.columns
        
        print(f"✅ Query executed successfully!")
        print(f"📊 Rows returned: {row_count}")
        print(f"📋 Columns: {', '.join(columns)}\n")
        
        # Step 5: Format results based on return_format
        if return_format == "dataframe":
            result_data = df.toPandas()
        elif return_format == "json":
            result_data = df.toJSON().collect()
        elif return_format == "markdown":
            # Create markdown table
            pandas_df = df.toPandas()
            result_data = pandas_df.to_markdown(index=False)
        else:  # dict (default)
            result_data = [row.asDict() for row in results_list]
        
        # Step 6: Display preview
        print(f"{'='*80}")
        print("📄 RESULTS PREVIEW (first 10 rows)")
        print(f"{'='*80}")
        df.show(n=min(10, row_count), truncate=False)
        print(f"{'='*80}\n")
        
        return {
            "success": True,
            "sql": extracted_sql,
            "result": result_data,
            "row_count": row_count,
            "columns": columns,
            "dataframe": df  # Keep original Spark DataFrame for further processing
        }
        
    except Exception as e:
        # Step 7: Handle errors
        error_msg = str(e)
        print(f"\n{'='*80}")
        print("❌ SQL EXECUTION FAILED")
        print(f"{'='*80}")
        print(f"Error: {error_msg}")
        print(f"{'='*80}\n")
        
        return {
            "success": False,
            "sql": extracted_sql,
            "result": None,
            "row_count": 0,
            "columns": [],
            "error": error_msg
        }


# Display agent info
print("=" * 80)
print("✅ SQL EXECUTION AGENT CREATED")
print("=" * 80)
print("\nFunction: execute_sql_on_delta_tables()")
print("\nFeatures:")
print("  - Extracts SQL from markdown code blocks")
print("  - Executes queries on delta tables via spark.sql()")
print("  - Adds LIMIT clause for safety (if not present)")
print("  - Returns formatted results (dict, dataframe, json, or markdown)")
print("  - Provides detailed execution logs and preview")
print("  - Handles errors gracefully")
print("\nParameters:")
print("  - sql_query: SQL to execute (str)")
print("  - max_rows: Maximum rows to return (default: 100)")
print("  - return_format: 'dict', 'dataframe', 'json', or 'markdown' (default: 'dict')")
print("\nReturns:")
print("  - success: bool")
print("  - sql: str (executed query)")
print("  - result: formatted results")
print("  - row_count: int")
print("  - columns: List[str]")
print("  - dataframe: Spark DataFrame")
print("  - error: str (if failed)")
print("=" * 80)

# COMMAND ----------

result

# COMMAND ----------

# test
execute_sql_on_delta_tables(sql_query=result)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create SQL Synthesis Agent (genie route)

# COMMAND ----------

# DBTITLE 1,Create Genie Agent list
from databricks_langchain.genie import GenieAgent
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from functools import partial

# 2. Call the function to get space_summary chunks for the Genie Agents
table_name = "yyang.multi_agent_genie.enriched_genie_docs_chunks"
space_summary_df = query_delta_table(
    table_name=table_name,
    filter_field="chunk_type",
    filter_value="space_summary",
    select_fields=["space_id", "space_title", "searchable_content"],
)


# 3. Define the function to get space info by space_id
def get_space_info_by_id(space_summary_df, space_id):
    """
    Query space_summary_df by space_id and return space_title and searchable_content.

    Args:
        space_summary_df: Spark DataFrame with columns including 'space_id', 'space_title', 'searchable_content'
        space_id: The space_id to filter on

    Returns:
        Spark DataFrame with columns 'space_title' and 'searchable_content' for the given space_id
    """
    result_df = space_summary_df.filter(space_summary_df.space_id == space_id).select(
        "space_title", "searchable_content"
    )
    return result_df

# 4. enforce row limit helper
def enforce_limit(messages, n=50):
    """
    Appends an instruction to the last user message to limit the result size.

    Args:
        messages: List of message dicts or LangChain Message objects.
        n: Maximum number of rows to return (default: 50).

    Returns:
        Modified user message string with appended instruction to limit result size.
    """
    # syntax ref: https://api-docs.databricks.com/python/databricks-ai-bridge/latest/databricks_langchain.html#databricks_langchain.GenieAgent
    last = messages[-1] if messages else {"content": ""}
    content = last.get("content", "") if isinstance(last, dict) else last.content
    # Lightweight instruction to constrain result size
    return f"{content}\n\nPlease limit the result to at most {n} rows."

# 5. partial function for invoke_genie_agent
def invoke_genie_agent(genie_agent: RunnableLambda, question: str) -> dict:
    """
    Invoke the GenieAgent for this space with a user question.

    Args:
        genie_agent: a RunnableLambda GenieAgent instance
        question: The user question as a string.

    Returns:
        The agent's response.
    """
    return genie_agent.invoke({"messages": [{"role": "user", "content": question}]})


# genie_agents = {}
genie_agents = []
genie_agent_tools = []
# 4. Create the Genie Agents
for row in space_summary_df.collect():
    space_id = row["space_id"]
    space_title = row["space_title"]
    searchable_content = row["searchable_content"]
    genie_agent_name = f"Genie_{space_title}"
    description = searchable_content
    genie_agent = GenieAgent(
        genie_space_id=space_id,
        genie_agent_name=genie_agent_name,
        description=description,
        include_context=True,
        message_processor=lambda msgs: enforce_limit(msgs, n=5)
    )
    genie_agents.append(genie_agent)
    # genie_agents[space_id] = genie_agent.as_tool() # dict

    # Wrap the agent call in a function that only takes a string argument. this func also return a func.
    def make_agent_invoker(agent):
        return lambda question: agent.invoke(
            {"messages": [{"role": "user", "content": question}]}
        )

    runnable = RunnableLambda(make_agent_invoker(genie_agent))
    runnable.name = genie_agent_name
    runnable.description = description

    genie_agent_tools.append(
        runnable.as_tool(
            name=genie_agent_name,
            description=description,
            arg_types={"question": str}
        )
    )

    ##: -----(error) will throw arrow in runnable.as_tool() step
    # partial_invoke_genie_agent = partial(invoke_genie_agent,
    #                                      genie_agent=genie_agent) # now assign the value to realize genie_agent at function creation time.
    # runnable = RunnableLambda(partial_invoke_genie_agent)
    # runnable.name = genie_agent_name
    # runnable.description = description
    # genie_agent_tools.append(
    #     runnable.as_tool(
    #         name=genie_agent_name,
    #         description=description,
    #         arg_types={"question": str}
    #     )
    # )


    ##: ------(bugs) cannot feed the right format for the question to be passed by agent to the tool)
    # #: genie or as_tool must receive input in this format: {"messages": [{"role": "user", "content": "What are the claims for patients diagnosed with diabetes?"}]}
    # genie_agent_tools.append(
    #     genie_agent.as_tool(
    #         name=genie_agent_name, description=description
    #     )
    # )


    # #: ------(bugs) below register agent using RunnableLambda will incur bus of Super Agent assign first to the right tool (according to tool name appeared in the mlflow trace), but then the tool will call the RunnableLambda wrapper of the wrong agent. E.g., Genie_HealthVerityProcedureDiagnosis was correctly called ty super agent, but then, it calls the running lambda of Genie_HealthVerityProviderEnrollment, which is wrong.------
    # print(genie_agent)
    # one_turn_genie_agent = RunnableLambda(
    #     lambda question: genie_agent.invoke(
    #         {"messages": [{"role": "user", "content": f"{question}"}]}
    #     )
    # )
    # genie_agent_tools.append(
    #     one_turn_genie_agent.as_tool(
    #         name=genie_agent_name, description=description, arg_types={"question": str}
    #     )
    # )


# ##: ---1. bugs analysis from DA explanation (which I personally think is wrong):
# The genie_agent used in your lambda inside RunnableLambda is captured from the local environment at the time the lambda function is created. This is standard Python closure behavior: the lambda "remembers" the value of genie_agent as it was when the lambda was defined, not as it might be changed later in the surrounding scope.

# If you reassign genie_agent after creating the RunnableLambda, the lambda will still use the original genie_agent object it closed over at creation time.

# ##: ---2. bugs analysis from copilot smart mode explanation:
# Key points
# • In Python, lambdas (and functions in general) capture variables by reference, not by value.
# • That means the genie_agent inside your lambda is not frozen at function creation time. Instead, the lambda holds a reference to the name genie_agent in its enclosing lexical scope.
# • When you later call one_turn_genie_agent(question), Python will resolve genie_agent at that moment by looking it up in the environment where the lambda was defined.
# Implications
# • If genie_agent was already defined when you created the lambda, the lambda will use that object.
# • If you reassign genie_agent later (e.g., genie_agent = SomeOtherAgent()), the lambda will now use the new object, because it always looks up the variable at runtime.
# • If genie_agent is undefined at call time, you’ll get a NameError.

# COMMAND ----------

# DBTITLE 1,Create Genie Routing and SQL Synthesis Agent
"""
Create LangGraph SQL Synthesis Agent with UC Function Toolkit

Uses Databricks LangGraph SDK with create_react_agent pattern
"""

from databricks_langchain import (
    ChatDatabricks,
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
)
from langchain.agents import create_agent
import mlflow

# Initialize Databricks Function Client
client = DatabricksFunctionClient()
set_uc_function_client(client)

# Initialize LLM
LLM_ENDPOINT_NAME = "databricks-claude-haiku-4-5"
llm = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME, temperature=0.1)

# # Create UC Function Toolkit with the registered functions
# uc_function_names = [
# ]

# uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
# tools = uc_toolkit.tools
tools = []
tools.extend(genie_agent_tools)
# tools.append(extract_genie_sql_tool)

print(f"✓ Created UCFunctionToolkit with {len(tools)} tools:")
# for tool, value in tools.items():
#     print(f"  - {tool}:{value.name}")

# Create SQL Synthesis Agent (specialized for multi-agent system)
# Create SQL Synthesis Agent (specialized for multi-agent system)
sql_synthesis_agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=(
"""You are a SQL synthesis agent, which can take analysis plan, and route queries to the corresponding Genie Agent.
The Plan given to you is a JSON:
{
'original_query': 'The User's Question',
'vector_search_relevant_spaces_info': [{'space_id': 'space_id_1',
   'space_title': 'space_title_1'},
  {'space_id': 'space_id_2',
   'space_title': 'space_title_2'},
  {'space_id': 'space_id_3',
   'space_title': 'space_title_3'}],
"question_clear": true,
"sub_questions": ["sub-question 1", "sub-question 2", ...],
"requires_multiple_spaces": true/false,
"relevant_space_ids": ["space_id_1", "space_id_2", ...],
"requires_join": true/false,
"join_strategy": "table_route" or "genie_route" or null,
"execution_plan": "Brief description of execution plan",
"genie_route_plan": {'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', 'space_id_3':'partial_question_3', ...} or null,}

## Tool Calling Plan:
1. Under the key of 'genie_route_plan' in the JSON, extracting 'partial_question_1' and feed to the right Genie Agent tool of 'space_id_1' with the input as a string. 
2. Asynchronously send all other partial_questions to the corresponding Genie Agent tools accordingly.
3. You have access to all Genie Agents as tools given to you; locate the proper Genie Agent Tool by searching the 'space_id_1' in the tool's description. After each Genie agent returns result, only extract the SQL string from the Genie tool output JSON {"thinking": thinking, "sql": sql, "answer": answer}.
4. If you find you are still missing necessary analytical components (metrics, filters, dimensions, etc.) to assemble the final SQL, which might be due to some genie agent tool may not have the necessary information being assigned, try to leverage other most likely Genie agents to find the missing pieces.

## Disaster Recovery (DR) Plan:
1. If one Genie agent tool fail to generate a SQL query, allow retry AS IS only one time; 
2. If fail again, try to reframe the partial question 'partial_question_1' according to the error msg returned by the genie tool, e.g., genie tool may say "I dont have information for cost related information", you can remove those components in the 'partial_question_1' which doesn't exist in the genie tool. For example, if the genie tool "Genie_MemberBenefits" doesn't contain benefit cost related information, you can reframe the question by removing the cost-related components in the 'partial_question_1', generate 'partial_question_1_v2' and try again. Only try once;
3. If fail again, return response as is. 


## Overall SQL Synthesis Plan:
Then, you can combine all the SQL pieces into a single SQL query, and return the final SQL query.
OUTPUT REQUIREMENTS:
- Generate complete, executable SQL with:
  * Proper JOINs based on execution plan strategy
  * WHERE clauses for filtering
  * Appropriate aggregations
  * Clear column aliases
  * Always use real column name existed in the data, never make up one
- Return ONLY the SQL query without explanations or markdown formatting
- If SQL cannot be generated, explain what metadata is missing"""
    )
)

# backup:
# """{'space_id_1': RunnableLambda(...),
#     'space_id_2': RunnableLambda(...),
#     'space_id_3': RunnableLambda(...)}\n
# """
# Use the mapping table given to you to assign the question to the correct tool by searching tool id by tool name.
# mapping table format like this:
# - space_id_1:Genie_tool_name_1
# - space_id_1:Genie_tool_name_2
# - space_id_1:Genie_tool_name_3
# After each Genie agent returns result, only extract the SQL string by using the 'extract_genie_sql_tool' tool.

print("\n" + "="*80)
print("✅ SQL Synthesis Agent created successfully!")
print("="*80)
print("\nAgent Configuration:")
print(f"  - LLM: {LLM_ENDPOINT_NAME}")
print(f"  - Tools: {len(tools)} UC functions")
print(f"  - Agent Type: LangChain Tool-Calling Agent")


# COMMAND ----------

# DBTITLE 1,Test the Agent
# Create the message for the agent
agent_message = {
    "messages": [
        {
            "role": "user",
            "content": f"""
Generate a SQL query to answer the question according to the Query Plan:
{json.dumps(plan_result, indent=2)}
"""
        }
    ]
}

# backup:
# Use your available Genie Agent tools to generate SQL and finally assemble them into an overall SQL to answer the original question.

print(agent_message)

# Enable MLflow autologging for tracing
mlflow.langchain.autolog()

# Invoke the agent
print("🤖 Invoking SQL Synthesis Agent...")
print("="*80 + "\n")

result = sql_synthesis_agent.invoke(agent_message)

# COMMAND ----------

# test
execute_sql_on_delta_tables(sql_query=result)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Dev/Test Codes Below

# COMMAND ----------

# DBTITLE 1,(not working under agent call this tool) extract_genie_sql_tool
from langchain_core.tools import tool

def extract_genie_response(resp: dict) -> dict:
    """
    Extracts 'thinking' (reasoning), 'sql', and 'answer' from Genie agent response.

    Args:
        resp: The response object from Genie agent, containing a 'messages' list.

    Returns:
        Tuple (thinking, sql, answer)
    """
    thinking = None
    sql = None
    answer = None

    for msg in resp["messages"]:
        if isinstance(msg, AIMessage):
            if msg.name == "query_reasoning":
                thinking = msg.content
            elif msg.name == "query_sql":
                sql = msg.content
            elif msg.name == "query_result":
                answer = msg.content
    return thinking, sql, answer

@tool("extract_genie_sql_tool", description="Extracts 'thinking' and 'sql' from a Genie agent response dict object as a dict {'thinking': str, 'sql': str}.")
def extract_genie_sql_tool(resp: dict) -> dict:
    """
    LangChain tool wrapper for extract_genie_response.
    Args:
        resp: The response object from Genie agent, containing a 'messages' list.
    Returns:
        Dict with 'thinking', 'sql', and 'answer'
    """
    thinking, sql, _ = extract_genie_response(resp)
    return {"thinking": thinking, "sql": sql}

# COMMAND ----------

extract_genie_sql_tool

# COMMAND ----------

# 2. Call the function to get space_summary chunks
table_name = "yyang.multi_agent_genie.enriched_genie_docs_chunks"
space_summary_df = query_delta_table(
    table_name=table_name,
    filter_field="chunk_type",
    filter_value="space_summary",
    select_fields=["space_id", "space_title", "searchable_content"]
)


# COMMAND ----------

space_summary_df.display()

# COMMAND ----------

def get_space_info_by_id(space_summary_df, space_id):
    """
    Query space_summary_df by space_id and return space_title and searchable_content.

    Args:
        space_summary_df: Spark DataFrame with columns including 'space_id', 'space_title', 'searchable_content'
        space_id: The space_id to filter on

    Returns:
        Spark DataFrame with columns 'space_title' and 'searchable_content' for the given space_id
    """
    result_df = space_summary_df.filter(space_summary_df.space_id == space_id).select("space_title", "searchable_content")
    return result_df

# COMMAND ----------


get_space_info_by_id(space_summary_df, "01f0956a387714969edde65458dcc22a").collect()

# COMMAND ----------

plan_result

# COMMAND ----------

plan_result = {'question_clear': True,
 'sub_questions': ['What are the medical claims for patients diagnosed with diabetes?',
  'What is the patient age group for each patient?',
  'What is the insurance payer type for each claim?',
  'What is the average cost of these claims broken down by payer type and age group?'],
 'requires_multiple_spaces': True,
 'relevant_space_ids': ['01f0956a4b0512e2a8aa325ffbac821b',
  '01f0956a387714969edde65458dcc22a',
  '01f0956a54af123e9cd23907e8167df9'],
 'requires_join': True,
 'join_strategy': 'genie_route',
 'execution_plan': 'Using genie_route as requested: Query HealthVerityProcedureDiagnosis space to identify claims with diabetes diagnosis codes. Query HealthVerityClaims space to get medical claim costs and payer types. Query HealthVerityProviderEnrollment space to get patient demographics including birth year for age calculation. Combine the SQL queries from each Genie Agent to synthesize final SQL that joins diagnosis, medical_claim, and enrollment tables on claim_id and patient_id, calculates age groups from birth year, filters for diabetes diagnoses, and computes average costs grouped by payer type and age group.',
 'genie_route_plan': {'01f0956a4b0512e2a8aa325ffbac821b': 'What are the claim IDs and patient IDs for patients diagnosed with diabetes based on ICD-10 diagnosis codes?',
  '01f0956a387714969edde65458dcc22a': 'What is the cost information and payer type for medical claims?',
  '01f0956a54af123e9cd23907e8167df9': 'What is the birth year and patient demographics from the enrollment table for calculating patient age groups?'}}

# COMMAND ----------

plan_result

# COMMAND ----------

context

# COMMAND ----------

context['01f0956a387714969edde65458dcc22a']

# COMMAND ----------

row[0]

# COMMAND ----------

# DBTITLE 1,test single Genie Agent
from databricks_langchain.genie import GenieAgent
from langchain_core.messages import AIMessage
# syntax ref: https://api-docs.databricks.com/python/databricks-ai-bridge/latest/databricks_langchain.html#databricks_langchain.GenieAgent
"""
Create a genie agent that can be used to query the API. If a description is not provided, the description of the genie space will be used.

Parameters
:
genie_space_id – The ID of the genie space to use

genie_agent_name – Name for the agent (default: “Genie”)

description – Custom description for the agent

include_context – Whether to include query reasoning and SQL in the response

message_processor – Optional function to process messages before querying. It should accept a list of either dict or LangChain Message objects and return a query string. If not provided, the agent will use the chat history to form the query.

client – Optional WorkspaceClient instance
"""


# 1), routing A

genie_space_id = "01f0956a4b0512e2a8aa325ffbac821b"
genie_space_id = "01f0956a387714969edde65458dcc22a"
genie_space_id = "01f0956a54af123e9cd23907e8167df9"

row = get_space_info_by_id(space_summary_df, genie_space_id).collect()[0]
genie_agent_name = f"Genie_{row['space_title']}"
description = row['searchable_content']



def enforce_limit(messages, n=50):
    """
    Appends an instruction to the last user message to limit the result size.

    Args:
        messages: List of message dicts or LangChain Message objects.
        n: Maximum number of rows to return (default: 50).

    Returns:
        Modified user message string with appended instruction to limit result size.
    """
    # syntax ref: https://api-docs.databricks.com/python/databricks-ai-bridge/latest/databricks_langchain.html#databricks_langchain.GenieAgent
    last = messages[-1] if messages else {"content": ""}
    content = last.get("content", "") if isinstance(last, dict) else last.content
    # Lightweight instruction to constrain result size
    return f"{content}\n\nPlease limit the result to at most {n} rows."

# Create the Genie agent and include reasoning + SQL in the response
agent = GenieAgent(
    genie_space_id=genie_space_id,
    genie_agent_name=genie_agent_name,
    description=description,
    include_context=True,
    message_processor=lambda msgs: enforce_limit(msgs, n=5),

)

# COMMAND ----------

# DBTITLE 1,test single
query = plan_result['genie_route_plan'][genie_space_id]
# query = "What are the claim IDs and patient IDs for patients diagnosed with diabetes based on ICD-10 diagnosis codes? Please also list column of ICD-10 diagnosis codes" # use this one to check ICD-10
# query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?" # use this full complete query to test the agent if can still return meaningful result. however, even that, hard to control what is expected to return.
print(query)

# Invoke the agent with a single user message (you can pass a full chat history too)
resp = agent.invoke({
    "messages": [
        {"role": "user", "content": f"{query}"}
    ]
})

# Extract "thinking" (reasoning), SQL, and final answer/result
thinking = None
sql = None
answer = None

for msg in resp["messages"]:
    # Each item is an AIMessage with a 'name' and 'content'
    if isinstance(msg, AIMessage):
        if msg.name == "query_reasoning":
            thinking = msg.content
        elif msg.name == "query_sql":
            sql = msg.content
        elif msg.name == "query_result":
            answer = msg.content



# COMMAND ----------

resp.__class__

# COMMAND ----------

print("THINKING:\n", thinking)
print("\nSQL:\n", sql)
print("\nANSWER:\n", answer[:500])

# COMMAND ----------

space_summary_df.display()

# COMMAND ----------

# Wrap the agent call in a function that only takes a string argument
def make_agent_invoker(agent):
    return lambda question: agent.invoke(
        {"messages": [{"role": "user", "content": question}]}
    )

runnable = RunnableLambda(make_agent_invoker(genie_agent))
runnable.name = genie_agent_name
runnable.description = description

genie_agent_tools.append(
    runnable.as_tool(
        name=genie_agent_name,
        description=description,
        arg_types={"question": str}
    )
)

# COMMAND ----------

genie_agent.invoke({"messages": [{"role": "user", "content": "What are the claims for patients diagnosed with diabetes?"}]})

# COMMAND ----------

genie_agents[1].invoke({"messages": [{"role": "user", "content": "What are the claims for patients diagnosed with diabetes?"}]})

# COMMAND ----------

genie_agents[1].description

# COMMAND ----------

# DBTITLE 1,test single agent
genie_agents[1].invoke(input={"messages": [{"role": "user", "content": "What are the claims for patients diagnosed with diabetes?"}]})

# COMMAND ----------

# DBTITLE 1,test single tool
genie_agent.func

# COMMAND ----------

import inspect
print(inspect.getsource(genie_agent.func.func))

# COMMAND ----------

genie_agent.description

# COMMAND ----------

genie_agent.as_tool().get_input_schema()

# COMMAND ----------

# uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
# tools = uc_toolkit.tools
tools = genie_agent_tools

print(f"✓ Created Genie Tools with {len(tools)} tools:")
for tool in tools:
    print(f"  - {tool.name}")

# COMMAND ----------

# # uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
# # tools = uc_toolkit.tools
# tools = genie_agent_tools

# print(f"✓ Created UCFunctionToolkit with {len(tools)} tools:")
# for tool, value in tools.items():
#     print(f"  - {tool}:{value.name}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## (Optional) SQL Query Validation Agent
# MAGIC
# MAGIC ### Validation Plan:
# MAGIC Validate the SQL is executable by checking the fields are all there in the tables before calling SQL Execuation Tool.
# MAGIC
# MAGIC ### Skip Plan:
# MAGIC 1. However, the validation agent will again use the UC functions of SQL tools to verify the table/column names and joins in the synthesized SQL are legit, which will be time consuming.
# MAGIC
# MAGIC 2. **An efficient alternative** is to directly call the execution tool return msg to super agent and super agent decide next step.
# MAGIC

# COMMAND ----------



# COMMAND ----------

# MAGIC %md
# MAGIC """
# MAGIC ===================================================================================
# MAGIC MULTI-AGENT SYSTEM ARCHITECTURE SUMMARY
# MAGIC ===================================================================================
# MAGIC
# MAGIC This notebook implements the SQL Synthesis Agent as part of a larger multi-agent system.
# MAGIC
# MAGIC COMPLETE WORKFLOW:
# MAGIC ══════════════════
# MAGIC
# MAGIC ┌─────────────────────┐
# MAGIC │    User Query       │
# MAGIC └──────────┬──────────┘
# MAGIC            ↓
# MAGIC ┌─────────────────────────────────────────────────────────────────┐
# MAGIC │                      SUPER AGENT                                 │
# MAGIC │                   (LangGraph Supervisor)                         │
# MAGIC │  - Orchestrates all sub-agents                                  │
# MAGIC │  - Manages state and handoffs                                   │
# MAGIC │  - Returns final result to user                                 │
# MAGIC └──────────┬──────────────────────────────────────────────────────┘
# MAGIC            ↓
# MAGIC ┌─────────────────────┐
# MAGIC │ 1. CLARIFICATION    │ ← Validates query clarity
# MAGIC │    AGENT            │   Requests clarification if needed
# MAGIC └──────────┬──────────┘
# MAGIC            ↓
# MAGIC ┌─────────────────────┐
# MAGIC │ 2. PLANNING         │ ← Analyzes query
# MAGIC │    AGENT            │   Searches vector index
# MAGIC │                     │   Identifies relevant spaces
# MAGIC │                     │   Creates execution plan
# MAGIC └──────────┬──────────┘
# MAGIC            ↓
# MAGIC ┌─────────────────────┐
# MAGIC │ 3. SQL SYNTHESIS    │ ← THIS NOTEBOOK ✨
# MAGIC │    AGENT            │   Receives execution plan
# MAGIC │    (with UC Tools)  │   Calls UC functions as needed
# MAGIC │                     │   Generates SQL query
# MAGIC └──────────┬──────────┘
# MAGIC            ↓
# MAGIC ┌─────────────────────┐
# MAGIC │ 4. SQL EXECUTION    │ ← Executes SQL on delta tables
# MAGIC │    AGENT            │   Returns results
# MAGIC └──────────┬──────────┘
# MAGIC            ↓
# MAGIC ┌─────────────────────┐
# MAGIC │    Final Answer     │
# MAGIC └─────────────────────┘
# MAGIC
# MAGIC
# MAGIC KEY COMPONENTS IN THIS NOTEBOOK:
# MAGIC ═════════════════════════════════
# MAGIC
# MAGIC 1. ✅ 4 UC Functions (Registered in Unity Catalog)
# MAGIC    - get_space_summary
# MAGIC    - get_table_overview
# MAGIC    - get_column_detail
# MAGIC    - get_space_details
# MAGIC
# MAGIC 2. ✅ SQL Synthesis Agent (Specialized for Multi-Agent System)
# MAGIC    - Uses Databricks LangChain SDK (not native LangChain)
# MAGIC    - Created with create_agent() (updated API)
# MAGIC    - Has access to UC function tools
# MAGIC    - Focused on SQL generation only
# MAGIC
# MAGIC 3. ✅ Wrapper Function: synthesize_sql_table_route_with_langgraph()
# MAGIC    - Receives structured input from Planning Agent
# MAGIC    - Validates execution plan
# MAGIC    - Invokes agent with proper context
# MAGIC    - Extracts clean SQL query
# MAGIC
# MAGIC INTEGRATION WITH SUPER AGENT:
# MAGIC ══════════════════════════════
# MAGIC
# MAGIC In your agent.py file, you would integrate this as:
# MAGIC
# MAGIC ```python
# MAGIC from databricks_langchain import (
# MAGIC     ChatDatabricks,
# MAGIC     UCFunctionToolkit,
# MAGIC     DatabricksFunctionClient,
# MAGIC     set_uc_function_client
# MAGIC )
# MAGIC from langchain.agents import create_agent
# MAGIC
# MAGIC # Initialize
# MAGIC client = DatabricksFunctionClient()
# MAGIC set_uc_function_client(client)
# MAGIC llm = ChatDatabricks(endpoint="databricks-claude-sonnet-4-5")
# MAGIC
# MAGIC # Create SQL Synthesis Agent as InCodeSubAgent
# MAGIC sql_synthesis_subagent = InCodeSubAgent(
# MAGIC     tools=[
# MAGIC         "yyang.multi_agent_genie.get_space_summary",
# MAGIC         "yyang.multi_agent_genie.get_table_overview",
# MAGIC         "yyang.multi_agent_genie.get_column_detail",
# MAGIC         "yyang.multi_agent_genie.get_space_details",
# MAGIC     ],
# MAGIC     name="sql_synthesis_agent",
# MAGIC     description="Generates SQL queries based on execution plans using UC metadata tools"
# MAGIC )
# MAGIC
# MAGIC # Add to supervisor
# MAGIC supervisor = create_langgraph_supervisor(
# MAGIC     llm=llm,
# MAGIC     in_code_agents=[
# MAGIC         clarification_agent,
# MAGIC         planning_agent,
# MAGIC         sql_synthesis_subagent,  # ← This agent
# MAGIC         sql_execution_agent
# MAGIC     ]
# MAGIC )
# MAGIC ```
# MAGIC
# MAGIC BENEFITS OF THIS ARCHITECTURE:
# MAGIC ═══════════════════════════════
# MAGIC
# MAGIC ✅ Separation of Concerns - Each agent has ONE job
# MAGIC ✅ Reusability - SQL agent can be used in different workflows  
# MAGIC ✅ Testability - Can test SQL synthesis independently
# MAGIC ✅ Scalability - Easy to add more agents
# MAGIC ✅ Debugging - Clear boundaries make issues traceable
# MAGIC ✅ Dynamic Metadata - Tools query only what's needed
# MAGIC ✅ Governance - UC functions are versioned and governed
# MAGIC
# MAGIC NEXT STEPS:
# MAGIC ═══════════
# MAGIC
# MAGIC 1. Integrate into agent.py with create_langgraph_supervisor()
# MAGIC 2. Implement Clarification and Planning agents
# MAGIC 3. Connect to Super Agent orchestration
# MAGIC 4. Add SQL Execution agent
# MAGIC 5. Deploy entire system to Unity Catalog
# MAGIC 6. Set up Databricks ResponsesAgent endpoint
# MAGIC
# MAGIC See Instructions/01_overall.md for complete multi-agent requirements.
# MAGIC """
# MAGIC
# MAGIC print("=" * 80)
# MAGIC print("🎉 MULTI-AGENT SQL SYNTHESIS IMPLEMENTATION COMPLETE")
# MAGIC print("=" * 80)
# MAGIC print("\n📚 Components Created:")
# MAGIC print("  1. 4 UC Functions for metadata querying")
# MAGIC print("  2. SQL Synthesis Agent with tool-calling capability")
# MAGIC print("  3. Multi-agent wrapper function")
# MAGIC print("  4. Example test with simulated Planning Agent input")
# MAGIC print("\n🔗 Ready for Super Agent integration!")
# MAGIC print("=" * 80)
# MAGIC

# COMMAND ----------

# MAGIC %md
# MAGIC """
# MAGIC SUMMARY: LangGraph SQL Synthesis Agent with UC Functions
# MAGIC
# MAGIC This notebook demonstrates:
# MAGIC
# MAGIC 1. ✅ UC Function Registration
# MAGIC    - Registered 4 Python UC functions for metadata querying
# MAGIC    - Functions query different levels: space_summary, table_overview, column_detail, space_details
# MAGIC    - Registered in: {CATALOG}.{SCHEMA}
# MAGIC
# MAGIC 2. ✅ Databricks LangGraph SDK Integration
# MAGIC    - Used DatabricksFunctionClient for UC function access
# MAGIC    - Created UCFunctionToolkit to wrap UC functions as tools
# MAGIC    - Built ReAct agent with create_react_agent (NOT native LangChain)
# MAGIC
# MAGIC 3. ✅ Intelligent Metadata Querying
# MAGIC    - Agent calls UC functions dynamically based on needs
# MAGIC    - Starts with high-level summaries
# MAGIC    - Drills down to tables/columns only when needed
# MAGIC    - Minimizes token usage
# MAGIC
# MAGIC 4. ✅ MLflow Integration
# MAGIC    - MLflow autologging enabled for tracing
# MAGIC    - Agent can be logged and deployed to Unity Catalog
# MAGIC    - Compatible with Databricks ResponsesAgent pattern
# MAGIC
# MAGIC 5. ✅ Production-Ready Function
# MAGIC    - synthesize_sql_table_route_with_langgraph() wraps the agent
# MAGIC    - Compatible with existing multi-agent system workflow
# MAGIC    - Returns clean SQL queries
# MAGIC
# MAGIC Key Advantages over Native LangChain:
# MAGIC - Direct integration with Databricks infrastructure
# MAGIC - UC functions automatically versioned and governed
# MAGIC - Built-in MLflow tracking and deployment
# MAGIC - Compatible with Databricks ResponsesAgent and Genie integration
# MAGIC
# MAGIC Next Steps:
# MAGIC - Integrate into Super Agent workflow (Instructions/01_overall.md)
# MAGIC - Deploy as part of multi-agent system
# MAGIC - Add to LangGraph supervisor with other agents
# MAGIC """
# MAGIC
# MAGIC print("=" * 80)
# MAGIC print("🎉 NOTEBOOK COMPLETE: LangGraph SQL Synthesis Agent")
# MAGIC print("=" * 80)
# MAGIC print("\nKey Components Created:")
# MAGIC print(f"  1. 4 UC Functions in {CATALOG}.{SCHEMA}")
# MAGIC print("  2. LangGraph ReAct Agent with UC tools")
# MAGIC print("  3. Production-ready synthesize_sql_table_route_with_langgraph()")
# MAGIC print("\nReady for integration into the multi-agent system!")
# MAGIC print("=" * 80)
# MAGIC

# COMMAND ----------


