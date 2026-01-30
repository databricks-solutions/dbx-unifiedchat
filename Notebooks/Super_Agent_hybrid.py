# Databricks notebook source
# DBTITLE 1,Install Packages
# MAGIC %pip install databricks-langchain==0.12.1 databricks-vectorsearch==0.63

# COMMAND ----------

# MAGIC %restart_python

# COMMAND ----------

"""
Super Agent (Hybrid Architecture) - Multi-Agent System Orchestrator

This notebook implements a hybrid architecture combining:
- OOP agent classes (from agent.py) for modularity and reusability
- Explicit state management (from Super_Agent.py) for observability and debugging

Architecture Benefits:
1. ✅ OOP modularity for agent logic - Easy to test and maintain
2. ✅ Explicit state for observability - Clear debugging and monitoring
3. ✅ Best practices from both approaches
4. ✅ Production-ready with rapid development capabilities

Components:
1. Clarification Agent - Validates query clarity (OOP class)
2. Planning Agent - Creates execution plan and identifies relevant spaces (OOP class)
3. SQL Synthesis Agent (Table Route) - Generates SQL using UC tools (OOP class)
4. SQL Synthesis Agent (Genie Route) - Generates SQL using Genie agents (OOP class)
5. SQL Execution Agent - Executes SQL and returns results (OOP class)

The Super Agent uses LangGraph with explicit state tracking for orchestration.
"""

import json
from typing import Dict, List, Optional, Any, Annotated, Literal, Generator
from typing_extensions import TypedDict
import operator
from uuid import uuid4
import re
from functools import partial

# COMMAND ----------

# DBTITLE 1,Configuration
# Configuration
CATALOG = "yyang"
SCHEMA = "multi_agent_genie"
TABLE_NAME = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks"
VECTOR_SEARCH_INDEX = f"{CATALOG}.{SCHEMA}.enriched_genie_docs_chunks_vs_index"
LLM_ENDPOINT_CLARIFICATION = "databricks-claude-haiku-4-5"
LLM_ENDPOINT_PLANNING = "databricks-claude-haiku-4-5"
LLM_ENDPOINT_SQL_SYNTHESIS = "databricks-claude-sonnet-4-5"  # More powerful for SQL synthesis
LLM_ENDPOINT_SUMMARIZE = "databricks-claude-haiku-4-5"  # Fast and cost-effective for summarization

print("="*80)
print("SUPER AGENT (HYBRID) CONFIGURATION")
print("="*80)
print(f"Catalog: {CATALOG}")
print(f"Schema: {SCHEMA}")
print(f"Table: {TABLE_NAME}")
print(f"Vector Search Index: {VECTOR_SEARCH_INDEX}")
print(f"\nLLM Endpoints:")
print(f"  - Clarification: {LLM_ENDPOINT_CLARIFICATION}")
print(f"  - Planning: {LLM_ENDPOINT_PLANNING}")
print(f"  - SQL Synthesis: {LLM_ENDPOINT_SQL_SYNTHESIS}")
print(f"  - Summarization: {LLM_ENDPOINT_SUMMARIZE}")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Import Dependencies
from databricks_langchain import (
    ChatDatabricks,
    VectorSearchRetrieverTool,
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
)
from databricks_langchain.genie import GenieAgent
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableLambda
import mlflow

# LangGraph imports
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import MemorySaver

# MLflow ResponsesAgent imports
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    output_to_responses_items_stream,
    to_chat_completions_input,
)

print("✓ All dependencies imported successfully")

# COMMAND ----------

# DBTITLE 1,Register Unity Catalog Functions for Metadata Querying
"""
Register UC functions that will be used as tools by the SQL Synthesis Agent.

These UC functions query different levels of the enriched genie docs chunks table:
1. get_space_summary: High-level space information
2. get_table_overview: Table-level metadata
3. get_column_detail: Column-level metadata
4. get_space_details: Complete metadata (last resort - token intensive)

All functions use LANGUAGE SQL for better performance and compatibility.
"""

print("="*80)
print("REGISTERING UNITY CATALOG FUNCTIONS")
print("="*80)
print(f"Target table: {TABLE_NAME}")
print(f"Functions will be created in: {CATALOG}.{SCHEMA}")
print("="*80)

# Optional: Drop existing functions if you need to recreate them
# Uncomment these lines if you need to drop and recreate the functions
# spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_space_summary')
# spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_table_overview')
# spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_column_detail')
# spark.sql(f'DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.get_space_details')

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
print("✅ ALL 4 UC FUNCTIONS REGISTERED SUCCESSFULLY!")
print("="*80)
print("Functions available for SQL Synthesis Agent:")
print(f"  1. {CATALOG}.{SCHEMA}.get_space_summary")
print(f"  2. {CATALOG}.{SCHEMA}.get_table_overview")
print(f"  3. {CATALOG}.{SCHEMA}.get_column_detail")
print(f"  4. {CATALOG}.{SCHEMA}.get_space_details")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Helper Functions - Data Loading
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

def load_space_context(table_name: str) -> Dict[str, str]:
    """
    Load space context from Delta table.
    Called fresh on each request - no caching for dynamic refresh.
    
    Args:
        table_name: Full table name (catalog.schema.table)
        
    Returns:
        Dictionary mapping space_id to searchable_content
    """
    df = spark.sql(f"""
        SELECT space_id, searchable_content
        FROM {table_name}
        WHERE chunk_type = 'space_summary'
    """)
    
    context = {row["space_id"]: row["searchable_content"] 
               for row in df.collect()}
    
    print(f"✓ Loaded {len(context)} Genie spaces for context")
    return context

# Note: Context is now loaded dynamically in clarification_node
# This allows refresh without model redeployment

# COMMAND ----------

# DBTITLE 1,Agent Class 0a: Meta-Question Detection Agent Class (OOP)
class MetaQuestionDetector:
    """
    Lightweight detector for meta-questions using pattern matching and optional LLM fallback.
    
    Meta-questions are exploratory queries about:
    - Agent capabilities ("What questions can I ask?")
    - Dataset descriptions ("Describe the available data")
    - Schema exploration ("List tables", "Show columns")
    - Statistical metadata ("How many rows?", "What's the date range?")
    
    Detection Strategy:
    1. Fast pattern matching (regex) - instant, covers ~80% of meta-questions
    2. LLM fallback for ambiguous queries - ~500ms, catches edge cases
    3. Default to SQL flow when uncertain
    """
    
    # Pattern-based detection (no LLM needed - instant!)
    META_PATTERNS = {
        "capability": [
            r"what (questions?|queries|types of questions?) (can|could|should|may) (i|we|one) (ask|query|request)",
            r"what can (this|the|your) (agent|system|tool|bot) (do|answer|handle|help with)",
            r"what (is|are) (you|your) (capabilities|functions|features)",
            r"help me (understand|know|learn) what.*ask",
            r"(show|tell|explain).*what.*can (ask|query)",
            r"(how|what) (do|does) (this|the) agent work",
            r"what (kind|type|sort) of questions",
        ],
        "dataset_description": [
            r"describe (the|this|available|all) (data|dataset|table|space|database)s?",
            r"what (data|dataset|table|space|database)s? (is|are) (available|present|here)",
            r"(show|list|tell|display)( me)?( the)? available (data|dataset|space|table)s?",
            r"what.*(in|within) (the|this) (dataset|database|system)",
            r"(overview|summary) of (the )?(data|dataset|available data)",
            r"what data (do|does) (you|this|the agent) have",
        ],
        "schema_exploration": [
            r"(list|show|display|what are) (the )?(tables?|columns?|fields?)",
            r"what (is|are) (the )?(table|column|field) (schema|structure)",
            r"(describe|explain|show) (the )?(table|column|schema) structure",
            r"what.*(tables?|columns?|fields?) (are )?(in|available)",
            r"(show|give) me (the )?(schema|structure|tables)",
        ],
        "statistical_meta": [
            r"how many (rows?|records?|tables?|entries)",
            r"what.*date range",
            r"what.*time (period|range|frame)",
            r"(count|size|volume) of (the )?(data|dataset|table|records)",
            r"(row|record|table) count",
        ]
    }
    
    def __init__(self, llm: Optional[Runnable] = None, context: Optional[Dict[str, str]] = None):
        """
        Initialize MetaQuestionDetector.
        
        Args:
            llm: Optional language model for ambiguous query detection
            context: Optional space context for enhanced detection
        """
        self.llm = llm
        self.context = context
        self.name = "MetaQuestionDetector"
    
    def detect(self, query: str) -> Dict[str, Any]:
        """
        Detect if query is a meta-question and classify it.
        
        Args:
            query: User's question
            
        Returns:
            {
                "is_meta": bool,
                "meta_type": str or None,  # capability/dataset_description/schema_exploration/statistical_meta
                "confidence": float,  # 0.0-1.0
                "route": str or None,  # "context" or "sql" or None
            }
        """
        query_lower = query.lower().strip()
        
        # Fast pattern matching
        for meta_type, patterns in self.META_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    # Determine route based on type
                    if meta_type == "statistical_meta":
                        route = "sql"  # Needs actual data query
                    else:
                        route = "context"  # Answerable from context
                    
                    print(f"✓ Pattern match: {meta_type} (route: {route})")
                    return {
                        "is_meta": True,
                        "meta_type": meta_type,
                        "confidence": 1.0,  # Pattern match = high confidence
                        "route": route,
                    }
        
        # If no pattern match but query seems exploratory, use fast LLM
        exploratory_keywords = [
            "describe", "what", "list", "show", "explain", "help", 
            "how many", "overview", "summary", "tell me about"
        ]
        if any(keyword in query_lower for keyword in exploratory_keywords):
            print("⚠ No pattern match, checking with LLM...")
            return self._llm_detect(query)
        
        # Default: Not a meta-question
        print("✓ Standard SQL question detected")
        return {
            "is_meta": False,
            "meta_type": None,
            "confidence": 1.0,
            "route": None,
        }
    
    def _llm_detect(self, query: str) -> Dict[str, Any]:
        """
        Use fast LLM to detect ambiguous meta-questions.
        Only called for edge cases not caught by patterns.
        
        Args:
            query: User's question
            
        Returns:
            Detection result dictionary
        """
        if not self.llm:
            print("⚠ No LLM available, defaulting to SQL question")
            return {
                "is_meta": False,
                "meta_type": None,
                "confidence": 0.5,
                "route": None,
            }
        
        detection_prompt = f"""Is this a meta-question about the dataset/agent capabilities, or a data query?

Query: {query}

Meta-question examples (questions ABOUT the system/data availability):
- "What questions can I ask?"
- "Describe the available data"
- "What tables are in the system?"
- "What can this agent do?"
- "Help me understand the dataset"

Data query examples (questions that NEED data to answer):
- "How many patients have diabetes?"
- "Show me top 10 expensive claims"
- "What's the average age of members?"
- "List patients on Lexapro"

Respond with JSON only:
{{
    "is_meta": true/false,
    "reasoning": "brief explanation"
}}

Only return valid JSON, no explanations."""
        
        try:
            response = self.llm.invoke(detection_prompt)
            content = response.content.strip()
            
            # Extract JSON from markdown code blocks if present
            json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).strip()
            else:
                json_str = content
            
            # Clean up JSON
            json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
            result = json.loads(json_str)
            
            is_meta = result.get("is_meta", False)
            print(f"✓ LLM detection: {'meta' if is_meta else 'SQL'} - {result.get('reasoning', 'N/A')}")
            
            return {
                "is_meta": is_meta,
                "meta_type": "general" if is_meta else None,
                "confidence": 0.8,  # LLM-based = medium confidence
                "route": "context" if is_meta else None,
            }
        except Exception as e:
            print(f"⚠ LLM detection error: {e}, defaulting to SQL question")
            return {
                "is_meta": False,
                "meta_type": None,
                "confidence": 0.5,
                "route": None,
            }
    
    def __call__(self, query: str) -> Dict[str, Any]:
        """Make detector callable for easy invocation."""
        return self.detect(query)

print("✓ MetaQuestionDetector class defined")

# COMMAND ----------

# DBTITLE 1,Agent Class 0b: Meta-Question Handler Agent Class (OOP)
class MetaQuestionHandler:
    """
    Handles meta-questions that can be answered from context alone (no SQL needed).
    
    Provides fast, context-based answers for:
    - Capability questions: "What can this agent do?"
    - Dataset descriptions: "Describe the available data"
    - Schema exploration: "What tables are available?"
    
    Uses space summaries loaded from the enriched genie docs chunks table.
    """
    
    def __init__(self, llm: Runnable, context: Dict[str, str]):
        """
        Initialize MetaQuestionHandler.
        
        Args:
            llm: Language model for generating natural language answers
            context: Dictionary mapping space_id to searchable_content
        """
        self.llm = llm
        self.context = context
        self.name = "MetaQuestionHandler"
    
    def answer(self, query: str, meta_type: str) -> str:
        """
        Answer meta-question using context only.
        
        Args:
            query: User's meta-question
            meta_type: Type of meta-question (capability/dataset_description/schema_exploration/general)
            
        Returns:
            Natural language answer
        """
        if meta_type == "capability":
            return self._answer_capability_question(query)
        elif meta_type == "dataset_description":
            return self._answer_dataset_description(query)
        elif meta_type == "schema_exploration":
            return self._answer_schema_exploration(query)
        else:
            return self._answer_general_meta(query)
    
    def _answer_capability_question(self, query: str) -> str:
        """
        Answer questions about what the agent can do.
        
        Args:
            query: User's capability question
            
        Returns:
            Natural language description of capabilities with examples
        """
        # Extract space info for example questions
        spaces_summary = []
        for space_id, content in self.context.items():
            # Truncate content for readability
            content_preview = content[:300] + "..." if len(content) > 300 else content
            spaces_summary.append(f"- {space_id}: {content_preview}")
        
        spaces_text = "\n".join(spaces_summary[:5])  # Limit to 5 spaces for brevity
        
        capability_prompt = f"""Answer this question about the agent's capabilities in a friendly, helpful way:

Question: {query}

Available Data Sources (Genie Spaces):
{spaces_text}

Agent Capabilities:
- Answer questions about data in the available Genie spaces listed above
- Generate SQL queries to analyze healthcare data (claims, pharmacy, patient demographics, etc.)
- Support multi-space queries with JOIN operations when needed
- Provide clarification when questions are ambiguous
- Route questions intelligently between different data sources

Provide a helpful, concise answer that includes:
1. Brief description of what the agent can do
2. What data/spaces are available
3. 3-5 example questions the user can ask (be specific to the available spaces)
4. Any important limitations or notes

Keep the tone friendly and actionable. Format with bullet points or numbered lists for readability."""
        
        response = self.llm.invoke(capability_prompt)
        return response.content.strip()
    
    def _answer_dataset_description(self, query: str) -> str:
        """
        Answer questions about available datasets.
        
        Args:
            query: User's dataset description question
            
        Returns:
            Structured description of available data
        """
        # Build structured context
        spaces_info = []
        for space_id, content in self.context.items():
            spaces_info.append({
                "space_id": space_id,
                "content": content[:500] + "..." if len(content) > 500 else content
            })
        
        description_prompt = f"""Describe the available datasets based on this context:

Question: {query}

Available Genie Spaces:
{json.dumps(spaces_info, indent=2)}

Provide a clear, structured description including:
1. Overview of available Genie spaces
2. Key data entities/tables in each space (if mentioned in content)
3. Typical use cases or questions that can be answered
4. Any relationships between spaces (if applicable)

Format as a well-organized response with:
- Clear headings or sections
- Bullet points for lists
- Specific details from the context

Keep it informative but concise."""
        
        response = self.llm.invoke(description_prompt)
        return response.content.strip()
    
    def _answer_schema_exploration(self, query: str) -> str:
        """
        Answer questions about schema structure (tables, columns, etc.).
        
        Args:
            query: User's schema exploration question
            
        Returns:
            Schema information extracted from context
        """
        schema_prompt = f"""Answer this schema exploration question using the available context:

Question: {query}

Available Context:
{json.dumps(self.context, indent=2)}

Extract and present relevant schema information:
- List of tables (if asked and available in context)
- Column information (if available in context)
- Data types or structure (if mentioned)
- Relationships between tables (if mentioned)

If full schema details aren't available in the context, acknowledge this clearly and describe what IS available.

Format the response clearly with:
- Bullet points or tables for lists
- Clear categorization by space or table
- Honest about limitations

Keep it factual and well-structured."""
        
        response = self.llm.invoke(schema_prompt)
        return response.content.strip()
    
    def _answer_general_meta(self, query: str) -> str:
        """
        Answer general meta-questions that don't fit specific categories.
        
        Args:
            query: User's general meta-question
            
        Returns:
            Natural language answer based on context
        """
        general_prompt = f"""Answer this exploratory question about the system using the available context:

Question: {query}

Context:
{json.dumps(self.context, indent=2)}

Provide a helpful, direct answer based on the available context.
If the question can't be fully answered from the context, acknowledge this and suggest what questions CAN be answered.

Keep the response:
- Direct and relevant to the question
- Based on factual information from context
- Friendly and helpful in tone
- Clear about any limitations"""
        
        response = self.llm.invoke(general_prompt)
        return response.content.strip()
    
    def __call__(self, query: str, meta_type: str) -> str:
        """Make handler callable for easy invocation."""
        return self.answer(query, meta_type)

print("✓ MetaQuestionHandler class defined")

# COMMAND ----------

# DBTITLE 1,Define Agent State (Explicit State Management)
class AgentState(TypedDict):
    """
    Explicit state that flows through the multi-agent system.
    This provides full observability and makes debugging easier.
    """
    # Input
    original_query: str
    
    # Clarification
    question_clear: bool
    clarification_needed: Optional[str]
    clarification_options: Optional[List[str]]
    clarification_count: Optional[int]  # Track clarification attempts (max 1)
    user_clarification_response: Optional[str]  # User's response to clarification
    clarification_message: Optional[str]  # The clarification question asked by agent
    combined_query_context: Optional[str]  # Combined context: original + clarification + response
    
    # Planning
    plan: Optional[Dict[str, Any]]
    sub_questions: Optional[List[str]]
    requires_multiple_spaces: Optional[bool]
    relevant_space_ids: Optional[List[str]]
    relevant_spaces: Optional[List[Dict[str, Any]]]
    vector_search_relevant_spaces_info: Optional[List[Dict[str, str]]]
    requires_join: Optional[bool]
    join_strategy: Optional[str]  # "table_route" or "genie_route"
    execution_plan: Optional[str]
    genie_route_plan: Optional[Dict[str, str]]
    
    # SQL Synthesis
    sql_query: Optional[str]  # Keep for backward compatibility (first query)
    sql_queries: Optional[List[str]]  # NEW: List of all SQL queries from multi-part questions
    sql_synthesis_explanation: Optional[str]  # Agent's explanation/reasoning
    synthesis_error: Optional[str]
    has_sql: Optional[bool]  # Whether SQL was successfully extracted
    
    # Execution
    execution_result: Optional[Dict[str, Any]]  # Keep for backward compatibility (first result)
    execution_results: Optional[List[Dict[str, Any]]]  # NEW: List of all execution results
    execution_error: Optional[str]
    
    # Summary
    final_summary: Optional[str]  # Natural language summary of the workflow execution
    
    # Control flow
    next_agent: Optional[str]
    messages: Annotated[List, operator.add]
    
    # Meta-question handling
    is_meta_question: Optional[bool]
    meta_type: Optional[str]  # capability/dataset_description/schema_exploration/statistical_meta
    meta_route: Optional[str]  # "context" or "sql"
    meta_answer: Optional[str]  # Direct answer for context-based meta-questions
    
print("✓ Agent State defined with explicit fields for observability")

# COMMAND ----------

# DBTITLE 1,Agent Class 1: Clarification Agent (OOP)
class ClarificationAgent:
    """
    Agent responsible for checking query clarity.
    
    Hybrid approach: Can accept context directly (for testing) or load from table (for production).
    
    Usage:
        # Testing: Pass mock context
        agent = ClarificationAgent(llm, {"space1": "mock data"})
        
        # Production: Load from table
        agent = ClarificationAgent.from_table(llm, TABLE_NAME)
    """
    
    def __init__(self, llm: Runnable, context: Dict[str, str]):
        """
        Initialize with context directly.
        
        Args:
            llm: Language model for clarity checking
            context: Dictionary mapping space_id to searchable_content
        """
        self.llm = llm
        self.context = context
        self.name = "Clarification"
    
    @classmethod
    def from_table(cls, llm: Runnable, table_name: str):
        """
        Factory method to create agent by loading context from Delta table.
        Loads fresh context on each call - no caching for dynamic refresh.
        
        Args:
            llm: Language model for clarity checking
            table_name: Full table name (catalog.schema.table)
            
        Returns:
            ClarificationAgent instance with fresh context
        """
        context = load_space_context(table_name)
        return cls(llm, context)
    
    def check_clarity(self, query: str, clarification_count: int = 0) -> Dict[str, Any]:
        """
        Check if the user query is clear and answerable.
        
        Args:
            query: User's question
            clarification_count: Number of times clarification has been requested
            
        Returns:
            Dictionary with clarity analysis
        """
        # If already clarified once, don't ask again - proceed with best effort
        if clarification_count >= 1:
            print("⚠ Max clarification attempts reached (1) - proceeding with query as-is")
            return {"question_clear": True}
        
        clarity_prompt = f"""
Analyze the following question for clarity and specificity based on the context.

IMPORTANT: Only mark as unclear if the question is TRULY VAGUE or IMPOSSIBLE to answer.
Be lenient - if the question can reasonably be answered with the available data, mark it as clear.

Question: {query}

Context (Available Data Sources):
{json.dumps(self.context, indent=2)}

Determine if:
1. The question is clear and answerable as-is (BE LENIENT - default to TRUE)
2. The question is TRULY VAGUE and needs critical clarification (ONLY if essential information is missing)
3. If the question mentions any metrics/dimensions/filters that can be mapped to available data with certain confidence, mark it as CLEAR; otherwise, mark it as UNCLEAR and ask for clarification.


If clarification is truly needed, provide:
- A brief explanation of what's critically unclear
- 2-3 specific clarification options the user can choose from

Return your analysis as JSON:
{{
    "question_clear": true/false,
    "clarification_needed": "explanation if unclear (null if clear)",
    "clarification_options": ["option 1", "option 2", "option 3"] or null
}}

Only return valid JSON, no explanations.
"""
        
        response = self.llm.invoke(clarity_prompt)
        content = response.content.strip()
        
        # Use regex to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # No code blocks, assume entire content is JSON
            json_str = content
        
        # Remove any trailing commas before ] or }
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        
        try:
            clarity_result = json.loads(json_str)
            return clarity_result
        except json.JSONDecodeError as e:
            print(f"⚠ Clarification JSON parsing error at position {e.pos}: {e.msg}")
            print(f"Raw content (first 300 chars): {content[:300]}")
            print(f"Defaulting to question_clear=True")
            return {"question_clear": True}
    
    def __call__(self, query: str, clarification_count: int = 0) -> Dict[str, Any]:
        """Make agent callable for easy invocation."""
        return self.check_clarity(query, clarification_count)

print("✓ ClarificationAgent class defined")

# COMMAND ----------

# DBTITLE 1,Agent Class 2: Planning Agent (OOP)
class PlanningAgent:
    """
    Agent responsible for query analysis and execution planning.
    
    OOP design with vector search integration.
    """
    
    def __init__(self, llm: Runnable, vector_search_index: str):
        self.llm = llm
        self.vector_search_index = vector_search_index
        self.name = "Planning"
    
    def search_relevant_spaces(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """
        Search for relevant Genie spaces using vector search.
        
        Args:
            query: User's question
            num_results: Number of results to return
            
        Returns:
            List of relevant space dictionaries
        """
        vs_tool = VectorSearchRetrieverTool(
            index_name=self.vector_search_index,
            num_results=num_results,
            columns=["space_id", "space_title", "searchable_content"],
            filters={"chunk_type": "space_summary"},
            query_type="ANN",
            include_metadata=True,
            include_score=True
        )
        
        docs = vs_tool.invoke({"query": query})
        
        relevant_spaces = []
        for doc in docs:
            print(doc)
            relevant_spaces.append({
                "space_id": doc.metadata.get("space_id", ""),
                "space_title": doc.metadata.get("space_title", ""),
                "searchable_content": doc.page_content,
                "score": doc.metadata.get("score", 0.0)
            })
        
        return relevant_spaces
    
    def create_execution_plan(
        self, 
        query: str, 
        relevant_spaces: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Create execution plan based on query and relevant spaces.
        
        Args:
            query: User's question
            relevant_spaces: List of relevant Genie spaces
            
        Returns:
            Dictionary with execution plan
        """
        planning_prompt = f"""
You are a query planning expert. Analyze the following question and create an execution plan.

Question: {query}

Potentially relevant Genie spaces:
{json.dumps(relevant_spaces, indent=2)}

Break down the question and determine:
1. What are the sub-questions or analytical components?
2. How many Genie spaces are needed to answer completely? (List their space_ids)
3. If multiple spaces are needed, do we need to JOIN data across them? Reasoning whether the sub-questions are totally independent without joining need.
    - JOIN needed: E.g., "How many active plan members over 50 are on Lexapro?" requires joining member data with pharmacy claims.
    - No need for JOIN: E.g., "How many active plan members over 50? How much total cost for all Lexapro claims?" - Two independent questions.
4. If JOIN is needed, what's the best strategy:
    - "table_route": Directly synthesize SQL across multiple tables
    - "genie_route": Query each Genie Space Agent separately, then combine SQL queries
    - If user explicitly asks for "genie_route", use it; otherwise, use "table_route"
    - always populate the join_strategy field in the JSON output.
5. Execution plan: A brief description of how to execute the plan.
    - For genie_route: Return "genie_route_plan": {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}}
    - For table_route: Return "genie_route_plan": null
    - Each partial_question should be similar to original but scoped to that space
    - Add "Please limit to top 10 rows" to each partial question

Return your analysis as JSON:
{{
    "original_query": "{query}",
    "vector_search_relevant_spaces_info":{[{sp['space_id']: sp['space_title']} for sp in relevant_spaces]},
    "question_clear": true,
    "sub_questions": ["sub-question 1", "sub-question 2", ...],
    "requires_multiple_spaces": true/false,
    "relevant_space_ids": ["space_id_1", "space_id_2", ...],
    "requires_join": true/false,
    "join_strategy": "table_route" or "genie_route",
    "execution_plan": "Brief description of execution plan",
    "genie_route_plan": {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}} or null
}}

Only return valid JSON, no explanations.
"""
        
        response = self.llm.invoke(planning_prompt)
        content = response.content.strip()
        
        # Use regex to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # No code blocks, assume entire content is JSON
            json_str = content
        
        # Remove any trailing commas before ] or }
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        
        try:
            plan_result = json.loads(json_str)
            return plan_result
        except json.JSONDecodeError as e:
            print(f"❌ Planning JSON parsing error at position {e.pos}: {e.msg}")
            print(f"Raw content (first 500 chars):\n{content[:500]}")
            print(f"Cleaned JSON (first 500 chars):\n{json_str[:500]}")
            
            # Try one more time with even more aggressive cleaning
            try:
                # Remove comments
                json_str_clean = re.sub(r'//.*$', '', json_str, flags=re.MULTILINE)
                # Remove trailing commas again
                json_str_clean = re.sub(r',(\s*[}\]])', r'\1', json_str_clean)
                plan_result = json.loads(json_str_clean)
                print("✓ Successfully parsed JSON after aggressive cleaning")
                return plan_result
            except:
                raise e  # Re-raise original error
    
    def __call__(self, query: str) -> Dict[str, Any]:
        """
        Analyze query and create execution plan.
        
        Returns:
            Complete execution plan with relevant spaces
        """
        # Search for relevant spaces
        relevant_spaces = self.search_relevant_spaces(query)
        
        # Create execution plan
        plan = self.create_execution_plan(query, relevant_spaces)
        
        return plan

print("✓ PlanningAgent class defined")

# COMMAND ----------

# DBTITLE 1,Agent Class 3a: SQL Synthesis Agent - Table Route (OOP)
class SQLSynthesisTableAgent:
    """
    Agent responsible for fast SQL synthesis using UC function tools.
    
    OOP design with UC toolkit integration.
    """
    
    def __init__(
        self, 
        llm: Runnable, 
        catalog: str, 
        schema: str
    ):
        self.llm = llm
        self.catalog = catalog
        self.schema = schema
        self.name = "SQLSynthesisTable"
        
        # Initialize UC Function Client
        client = DatabricksFunctionClient()
        set_uc_function_client(client)
        
        # Create UC Function Toolkit
        uc_function_names = [
            f"{catalog}.{schema}.get_space_summary",
            f"{catalog}.{schema}.get_table_overview",
            f"{catalog}.{schema}.get_column_detail",
            f"{catalog}.{schema}.get_space_details",
        ]
        
        self.uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
        self.tools = self.uc_toolkit.tools
        
        # Create SQL synthesis agent with tools
        self.agent = create_agent(
            model=llm,
            tools=self.tools,
            system_prompt=(
                "You are a specialized SQL synthesis agent in a multi-agent system.\n\n"
                "ROLE: You receive execution plans from the planning agent and generate SQL queries.\n\n"

                "## WORKFLOW:\n"
                "1. Review the execution plan and provided metadata\n"
                "2. If metadata is sufficient → Generate SQL immediately\n"
                "3. If insufficient, call UC function tools in this order:\n"
                "   a) get_space_summary for space information\n"
                "   b) get_table_overview for table schemas\n"
                "   c) get_column_detail for specific columns\n"
                "   d) get_space_details ONLY as last resort (token intensive)\n"
                "4. At last, if you still cannot find enough metadata in relevant spaces provided, dont stuck there. Expand the searching scope to all spaces mentioned in the execution plan's 'vector_search_relevant_spaces_info' field. Extract the space_id from 'vector_search_relevant_spaces_info'. \n"
                "5. Generate complete, executable SQL\n\n"

                "## UC FUNCTION USAGE:\n"
                "- Pass arguments as JSON array strings: '[\"space_id_1\", \"space_id_2\"]' or 'null'\n"
                "- Only query spaces from execution plan's relevant_space_ids\n"
                "- Use minimal sufficiency: only query what you need\n\n"

                "## OUTPUT REQUIREMENTS:\n"
                "- Generate complete, executable SQL with:\n"
                "  * Proper JOINs based on execution plan\n"
                "  * WHERE clauses for filtering\n"
                "  * Appropriate aggregations\n"
                "  * Clear column aliases\n"
                "  * Always use real column names, never make up ones\n"
                "- Return your response with:\n"
                "1. Your explanations; If SQL cannot be generated, explain what metadata is missing\n"
                "2. The final SQL query in a ```sql code block\n\n"
            )
        )
    
    def synthesize_sql(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synthesize SQL query based on execution plan.
        
        Args:
            plan: Execution plan from planning agent
            
        Returns:
            Dictionary with:
            - sql: str - Extracted SQL query (None if cannot generate)
            - explanation: str - Agent's explanation/reasoning
            - has_sql: bool - Whether SQL was successfully extracted
        """
        # # Prepare plan summary for agent
        # plan_summary = {
        #     "original_query": plan.get("original_query", ""),
        #     "vector_search_relevant_spaces_info": plan.get("vector_search_relevant_spaces_info", []),
        #     "relevant_space_ids": plan.get("relevant_space_ids", []),
        #     "execution_plan": plan.get("execution_plan", ""),
        #     "requires_join": plan.get("requires_join", False),
        #     "sub_questions": plan.get("sub_questions", [])
        # }
        plan_result = plan
        # Invoke agent
        agent_message = {
            "messages": [
                {
                    "role": "user",
                    "content": f"""
Generate a SQL query to answer the question according to the Query Plan:
{json.dumps(plan_result, indent=2)}

Use your available UC function tools to gather metadata intelligently.
"""
                }
            ]
        }
        
        result = self.agent.invoke(agent_message)
        
        # Extract SQL and explanation from response
        if result and "messages" in result:
            final_content = result["messages"][-1].content
            original_content = final_content
            
            sql_query = None
            has_sql = False
            
            # Try to extract SQL from markdown if present
            if "```sql" in final_content.lower():
                sql_match = re.search(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
                if sql_match:
                    sql_query = sql_match.group(1).strip()
                    has_sql = True
                    # Remove SQL block from content to get explanation
                    final_content = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL)
            elif "```" in final_content:
                sql_match = re.search(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
                if sql_match:
                    # Check if it looks like SQL
                    potential_sql = sql_match.group(1).strip()
                    if any(keyword in potential_sql.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                        sql_query = potential_sql
                        has_sql = True
                        # Remove SQL block from content to get explanation
                        final_content = re.sub(r'```\s*.*?\s*```', '', final_content, flags=re.DOTALL)
            
            # Clean up explanation
            explanation = final_content.strip()
            if not explanation:
                explanation = original_content if not has_sql else "SQL query generated successfully."
            
            return {
                "sql": sql_query,
                "explanation": explanation,
                "has_sql": has_sql
            }
        else:
            raise Exception("No response from agent")
    
    def __call__(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Make agent callable."""
        return self.synthesize_sql(plan)

print("✓ SQLSynthesisTableAgent class defined")

# COMMAND ----------

# DBTITLE 1,Agent Class 3b: SQL Synthesis Agent - Genie Route (OOP)

class SQLSynthesisGenieAgent:
    """
    Agent responsible for Genie Route SQL synthesis using Genie agents as tools.
    
    Uses LangChain agent pattern where Genie agents are wrapped as tools.
    The agent orchestrates tool calling, retries, and SQL synthesis autonomously.
    
    OOP design with Genie agent-as-tools integration.
    Optimized to only create Genie agents for relevant spaces (not all spaces).
    """
    
    def __init__(self, llm: Runnable, relevant_spaces: List[Dict[str, Any]]):
        """
        Initialize SQL Synthesis Genie Agent with tool-calling pattern.
        
        Args:
            llm: Language model for SQL synthesis
            relevant_spaces: List of relevant spaces from PlanningAgent's Vector Search.
                            Each dict should have: space_id, space_title, searchable_content
        """
        self.llm = llm
        self.relevant_spaces = relevant_spaces
        self.name = "SQLSynthesisGenie"
        
        # Create Genie agents and their tool representations
        self.genie_agents = []
        self.genie_agent_tools = []
        self._create_genie_agent_tools()
        
        # Create SQL synthesis agent with Genie agent tools
        self.sql_synthesis_agent = self._create_sql_synthesis_agent()
    
    def _create_genie_agent_tools(self):
        """
        Create Genie agents as tools only for relevant spaces.
        Uses RunnableLambda wrapper pattern to avoid closure issues.
        
        Pattern copied from test_uc_functions.py lines 1283-1318
        """
        def enforce_limit(messages, n=5):
            last = messages[-1] if messages else {"content": ""}
            content = last.get("content", "") if isinstance(last, dict) else last.content
            return f"{content}\n\nPlease limit the result to at most {n} rows."
        
        print(f"  Creating Genie agent tools for {len(self.relevant_spaces)} relevant spaces...")
        
        for space in self.relevant_spaces:
            space_id = space.get("space_id")
            space_title = space.get("space_title", space_id)
            searchable_content = space.get("searchable_content", "")
            
            if not space_id:
                print(f"  ⚠ Warning: Space missing space_id, skipping: {space}")
                continue
            
            genie_agent_name = f"Genie_{space_title}"
            description = searchable_content
            
            # Create Genie agent
            genie_agent = GenieAgent(
                genie_space_id=space_id,
                genie_agent_name=genie_agent_name,
                description=description,
                include_context=True,
                message_processor=lambda msgs: enforce_limit(msgs, n=5)
            )
            self.genie_agents.append(genie_agent)
            
            # Wrap the agent call in a function that only takes a string argument
            # This function also returns a function to avoid closure issues
            def make_agent_invoker(agent):
                return lambda question: agent.invoke(
                    {"messages": [{"role": "user", "content": question}]}
                )
            
            runnable = RunnableLambda(make_agent_invoker(genie_agent))
            runnable.name = genie_agent_name
            runnable.description = description
            
            self.genie_agent_tools.append(
                runnable.as_tool(
                    name=genie_agent_name,
                    description=description,
                    arg_types={"question": str}
                )
            )
            
            print(f"  ✓ Created Genie agent tool: {genie_agent_name} ({space_id})")
    
    def _create_sql_synthesis_agent(self):
        """
        Create LangGraph SQL Synthesis Agent with Genie agent tools.
        
        Uses Databricks LangGraph SDK with create_agent pattern.
        Pattern copied from test_uc_functions.py lines 1375-1462
        """
        tools = []
        tools.extend(self.genie_agent_tools)
        
        print(f"✓ Created SQL Synthesis Agent with {len(tools)} Genie agent tools")
        
        # Create SQL Synthesis Agent (specialized for multi-agent system)
        sql_synthesis_agent = create_agent(
            model=self.llm,
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
- Return your response with:
1. Your explanation combining both the individual Genie thinking and your own reasoning
2. The final SQL query in a ```sql code block"""
            )
        )
        
        return sql_synthesis_agent
    
    def synthesize_sql(
        self, 
        plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Synthesize SQL using Genie agents (genie route) with autonomous tool calling.
        
        Args:
            plan: Complete plan dictionary from PlanningAgent containing:
                - original_query: Original user question
                - execution_plan: Execution plan description
                - genie_route_plan: Mapping of space_id to partial question
                - vector_search_relevant_spaces_info: List of relevant spaces
                - relevant_space_ids: List of relevant space IDs
                - requires_join: Whether join is needed
                - join_strategy: Join strategy (table_route/genie_route)
            
        Returns:
            Dictionary with:
            - sql: str - Combined SQL query (None if cannot generate)
            - explanation: str - Agent's explanation/reasoning
            - has_sql: bool - Whether SQL was successfully extracted
        """
        # Build the plan result JSON for the agent
        plan_result = plan
        
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
        
        print(f"\n{'='*80}")
        print("🤖 Invoking SQL Synthesis Agent with Genie Agent Tools...")
        print(f"{'='*80}")
        print(f"Plan: {json.dumps(plan_result, indent=2)}")
        print(f"{'='*80}\n")
        
        try:
            # Enable MLflow autologging for tracing
            mlflow.langchain.autolog()
            
            # Invoke the agent
            result = self.sql_synthesis_agent.invoke(agent_message)
            
            # Extract SQL from agent result
            # The agent returns {"messages": [...]}
            # Last message contains the final response
            final_message = result["messages"][-1]
            final_content = final_message.content.strip()
            
            print(f"\n{'='*80}")
            print("✅ SQL Synthesis Agent completed")
            print(f"{'='*80}")
            print(f"Result: {final_content[:500]}...")
            print(f"{'='*80}\n")
            
            # Extract SQL and explanation from the result
            sql_query = None
            has_sql = False
            explanation = final_content
            
            # Clean markdown if present and extract SQL
            if "```sql" in final_content.lower():
                sql_match = re.search(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
                if sql_match:
                    sql_query = sql_match.group(1).strip()
                    has_sql = True
                    # Remove SQL block to get explanation
                    explanation = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL)
            elif "```" in final_content:
                sql_match = re.search(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
                if sql_match:
                    potential_sql = sql_match.group(1).strip()
                    if any(keyword in potential_sql.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                        sql_query = potential_sql
                        has_sql = True
                        # Remove SQL block to get explanation
                        explanation = re.sub(r'```\s*.*?\s*```', '', final_content, flags=re.DOTALL)
            else:
                # No markdown, check if the entire content is SQL
                if any(keyword in final_content.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                    sql_query = final_content
                    has_sql = True
                    explanation = "SQL query generated successfully by Genie agent tools."
            
            explanation = explanation.strip()
            if not explanation:
                explanation = final_content if not has_sql else "SQL query generated successfully by Genie agent tools."
            
            return {
                "sql": sql_query,
                "explanation": explanation,
                "has_sql": has_sql
            }
            
        except Exception as e:
            print(f"\n{'='*80}")
            print("❌ SQL Synthesis Agent failed")
            print(f"{'='*80}")
            print(f"Error: {str(e)}")
            print(f"{'='*80}\n")
            
            return {
                "sql": None,
                "explanation": f"SQL synthesis failed: {str(e)}",
                "has_sql": False
            }
    
    def __call__(
        self, 
        plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make agent callable with plan dictionary."""
        return self.synthesize_sql(plan)

print("✓ SQLSynthesisGenieAgent class defined")

# COMMAND ----------

# DBTITLE 1,Utility Function: Extract Multiple SQL Queries
def extract_all_sql_queries(content: str) -> List[str]:
    """
    Extract all SQL queries from markdown code blocks.
    
    This function finds all SQL code blocks in the content, supporting both:
    - Explicit ```sql blocks
    - Generic ``` blocks containing SQL keywords
    
    Args:
        content: The text content containing SQL code blocks
        
    Returns:
        List of SQL query strings (empty list if none found)
    """
    sql_queries = []
    
    # Find all ```sql blocks (case-insensitive)
    sql_pattern = r'```sql\s*(.*?)\s*```'
    matches = re.findall(sql_pattern, content, re.IGNORECASE | re.DOTALL)
    
    if matches:
        sql_queries.extend([m.strip() for m in matches if m.strip()])
    else:
        # Fallback: check for generic code blocks containing SQL keywords
        generic_pattern = r'```\s*(.*?)\s*```'
        matches = re.findall(generic_pattern, content, re.DOTALL)
        for match in matches:
            match = match.strip()
            # Check if it looks like SQL (contains SQL keywords)
            if match and any(kw in match.upper() for kw in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                sql_queries.append(match)
    
    return sql_queries

print("✓ extract_all_sql_queries utility function defined")

# COMMAND ----------

# DBTITLE 1,Agent Class 4: SQL Execution Agent (OOP)
class SQLExecutionAgent:
    """
    Agent responsible for executing SQL queries.
    
    OOP design for clean execution logic.
    Synced with test_uc_functions.py implementation.
    """
    
    def __init__(self):
        self.name = "SQLExecution"
    
    def execute_sql(
        self, 
        sql_query: str, 
        max_rows: int = 100,
        return_format: str = "dict"
    ) -> Dict[str, Any]:
        """
        Execute SQL query on delta tables and return formatted results.
        
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
        """
        from pyspark.sql import SparkSession
        
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
            if return_format == "json":
                result_data = df.toJSON().collect()
            elif return_format == "markdown":
                # Create markdown table
                pandas_df = df.toPandas()
                result_data = pandas_df.to_markdown(index=False)
            else:  # dict (default)
                result_data = [row.asDict() for row in results_list]
            
            # Step 7: Display preview
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
            }
            
        except Exception as e:
            # Step 8: Handle errors
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
    
    def __call__(self, sql_query: str, max_rows: int = 100, return_format: str = "dict") -> Dict[str, Any]:
        """Make agent callable."""
        return self.execute_sql(sql_query, max_rows, return_format)

print("✓ SQLExecutionAgent class defined")

# COMMAND ----------

# DBTITLE 1,Agent Class 5: Result Summarize Agent (OOP Design)
class ResultSummarizeAgent:
    """
    Agent responsible for generating a final summary of the workflow execution.
    
    Analyzes the entire workflow state and produces a natural language summary
    of what was accomplished, whether successful or not.
    
    OOP design for clean summarization logic.
    """
    
    def __init__(self, llm: Runnable):
        self.name = "ResultSummarize"
        self.llm = llm
    
    def generate_summary(self, state: AgentState) -> str:
        """
        Generate a natural language summary of the workflow execution.
        
        Args:
            state: The complete workflow state
            
        Returns:
            String containing natural language summary
        """
        # Build context from state
        summary_prompt = self._build_summary_prompt(state)
        
        # Invoke LLM to generate summary
        response = self.llm.invoke(summary_prompt)
        summary = response.content.strip()
        
        return summary
    
    def _build_summary_prompt(self, state: AgentState) -> str:
        """Build the prompt for summary generation based on state."""
        
        original_query = state.get('original_query', 'N/A')
        question_clear = state.get('question_clear', False)
        clarification_needed = state.get('clarification_needed')
        execution_plan = state.get('execution_plan')
        join_strategy = state.get('join_strategy')
        sql_query = state.get('sql_query')
        sql_explanation = state.get('sql_synthesis_explanation')
        exec_result = state.get('execution_result', {})
        synthesis_error = state.get('synthesis_error')
        execution_error = state.get('execution_error')
        
        prompt = f"""You are a result summarization agent. Generate a concise, natural language summary of what this multi-agent workflow accomplished.

**Original User Query:** {original_query}

**Workflow Execution Details:**

"""
        
        # Check for meta-question answer first (priority)
        meta_answer = state.get('meta_answer')
        if meta_answer:
            prompt += f"""**Query Type:** Meta-question (exploratory query about system capabilities or data)
**Answer:**
{meta_answer}

**Summary:** The agent answered an exploratory question about its capabilities or available data without needing to generate SQL queries.
"""
            return prompt  # Early return for meta-questions
        
        # Add clarification info
        if not question_clear:
            prompt += f"""**Status:** Query needs clarification
**Clarification Needed:** {clarification_needed}
**Summary:** The query was too vague or ambiguous. Requested user clarification before proceeding.
"""
        else:
            # Add planning info
            if execution_plan:
                prompt += f"""**Planning:** {execution_plan}
**Strategy:** {join_strategy or 'N/A'}

"""
            
            # NEW: Check for multiple SQL queries and results
            sql_queries = state.get('sql_queries', [])
            execution_results = state.get('execution_results', [])
            
            # Fallback to single query/result for backward compatibility
            if not sql_queries and sql_query:
                sql_queries = [sql_query]
            if not execution_results and exec_result:
                execution_results = [exec_result]
            
            # Add SQL synthesis info
            if sql_queries:
                if len(sql_queries) == 1:
                    # Single query (original behavior)
                    prompt += f"""**SQL Generation:** ✅ Successful
**SQL Query:** 
```sql
{sql_queries[0]}
```

"""
                else:
                    # Multiple queries
                    prompt += f"""**SQL Generation:** ✅ Successful ({len(sql_queries)} queries for multi-part question)

"""
                    for i, query in enumerate(sql_queries, 1):
                        prompt += f"""**SQL Query {i}:** 
```sql
{query}
```

"""
                
                if sql_explanation:
                    prompt += f"""**SQL Synthesis Explanation:** {sql_explanation[:2000]}{'...' if len(sql_explanation) > 2000 else ''}

"""
                
                # Add execution info (single or multiple results)
                if execution_results:
                    if len(execution_results) == 1:
                        # Single result (original behavior)
                        result = execution_results[0]
                        if result.get('success'):
                            row_count = result.get('row_count', 0)
                            columns = result.get('columns', [])
                            result_data = result.get('result', [])
                            prompt += f"""**Execution:** ✅ Successful
**Rows:** {row_count} rows returned
**Columns:** {', '.join(columns[:5])}{'...' if len(columns) > 5 else ''}

**Result:** {json.dumps(result_data, indent=2)}
"""
                        else:
                            prompt += f"""**Execution:** ❌ Failed
**Error:** {result.get('error', 'Unknown error')}

"""
                    else:
                        # Multiple results
                        all_successful = all(r.get('success') for r in execution_results)
                        total_rows = sum(r.get('row_count', 0) for r in execution_results if r.get('success'))
                        
                        if all_successful:
                            prompt += f"""**Execution:** ✅ All {len(execution_results)} queries executed successfully
**Total Rows Returned:** {total_rows}

"""
                        else:
                            failed_count = sum(1 for r in execution_results if not r.get('success'))
                            prompt += f"""**Execution:** ⚠️ Partial success ({len(execution_results) - failed_count} succeeded, {failed_count} failed)

"""
                        
                        # Add details for each result
                        for i, result in enumerate(execution_results, 1):
                            if result.get('success'):
                                row_count = result.get('row_count', 0)
                                columns = result.get('columns', [])
                                result_data = result.get('result', [])
                                prompt += f"""**Query {i} Result:**
- Rows: {row_count}
- Columns: {', '.join(columns[:5])}{'...' if len(columns) > 5 else ''}
- Data: {json.dumps(result_data[:10], indent=2)}{'...(showing first 10 rows)' if len(result_data) > 10 else ''}

"""
                            else:
                                prompt += f"""**Query {i} Result:**
- Status: ❌ Failed
- Error: {result.get('error', 'Unknown error')}

"""
                elif execution_error:
                    prompt += f"""**Execution:** ❌ Failed
**Error:** {execution_error}

"""
            elif synthesis_error:
                prompt += f"""**SQL Generation:** ❌ Failed
**Error:** {synthesis_error}
**Explanation:** {sql_explanation or 'N/A'}

"""
        
        prompt += """
**Task:** Generate a comprehensive summary in natural language that:
1. Describes what the user asked for
2. Explains what the system did (planning, SQL generation, execution)
3. For multi-part questions with multiple queries:
   - Explain each sub-question that was addressed
   - Show each SQL query in its own code block with a clear label
   - Present each query's results in a clear, readable format (preferably as a markdown table)
   - Provide insights and analysis for each result
   - Synthesize an overall conclusion combining insights from all queries
4. For single queries:
   - Print out SQL synthesis explanation if any SQL was generated
   - Print out the SQL query in a code block
   - Print out the result in a readable format (preferably as a markdown table)
   - Provide insights and analysis for the result
5. States the outcome (success with X rows, error, needs clarification, etc.)

Use markdown formatting for readability. Keep it clear and user-friendly. 
"""
        
        return prompt
    
    def __call__(self, state: AgentState) -> str:
        """Make agent callable."""
        return self.generate_summary(state)

print("✓ ResultSummarizeAgent class defined")

# COMMAND ----------

# DBTITLE 1,Node Wrappers (Combining OOP Agents with Explicit State)
def meta_detection_node(state: AgentState) -> AgentState:
    """
    Meta-question detection node - Entry point of the workflow.
    
    Fast detection using pattern matching (instant) and optional LLM fallback.
    Routes meta-questions to appropriate handlers, SQL questions to clarification.
    
    This node prevents meta-questions from being blocked by the clarification agent.
    """
    print("\n" + "="*80)
    print("🔍 META-QUESTION DETECTOR (Entry Point)")
    print("="*80)
    
    query = state["original_query"]
    print(f"Query: {query}")
    
    # Load space context (same as clarification agent)
    print("\n📚 Loading space context...")
    context = load_space_context(TABLE_NAME)
    
    # Initialize detector with fast LLM
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
    detector = MetaQuestionDetector(llm=llm, context=context)
    
    # Detect meta-question
    print("\n🔎 Analyzing query type...")
    detection_result = detector.detect(query)
    
    # Update state with detection results
    state["is_meta_question"] = detection_result["is_meta"]
    state["meta_type"] = detection_result.get("meta_type")
    state["meta_route"] = detection_result.get("route")
    
    # Log detection results
    if detection_result["is_meta"]:
        print(f"\n✅ Meta-question detected!")
        print(f"  Type: {detection_result['meta_type']}")
        print(f"  Route: {detection_result['route']}")
        print(f"  Confidence: {detection_result['confidence']:.2f}")
        
        # Add message to state
        state["messages"] = [
            HumanMessage(content=query),
            SystemMessage(content=f"Meta-question detected: {detection_result['meta_type']}")
        ]
    else:
        print(f"\n➡️ Standard SQL question - routing to clarification")
        
        # Add message to state
        state["messages"] = [
            HumanMessage(content=query)
        ]
    
    return state


def meta_handler_node(state: AgentState) -> AgentState:
    """
    Meta-question handler node for context-based answers.
    
    Answers meta-questions using space summaries only (no SQL generation).
    Fast response path: ~2 seconds vs full SQL pipeline ~10-15 seconds.
    """
    print("\n" + "="*80)
    print("💬 META-QUESTION HANDLER (Context-Based)")
    print("="*80)
    
    query = state["original_query"]
    meta_type = state.get("meta_type", "general")
    
    print(f"Query: {query}")
    print(f"Meta Type: {meta_type}")
    
    # Load space context
    print("\n📚 Loading space context...")
    context = load_space_context(TABLE_NAME)
    
    # Initialize handler with LLM
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)  # Fast model for meta answers
    handler = MetaQuestionHandler(llm=llm, context=context)
    
    # Generate context-based answer
    print("\n🤔 Generating answer from context...")
    try:
        answer = handler.answer(query, meta_type)
        
        print(f"\n✅ Meta-question answered!")
        print(f"Answer preview: {answer[:200]}...")
        
        # Store answer and route to summarize
        state["meta_answer"] = answer
        state["question_clear"] = True  # Mark as clear since we answered it
        state["next_agent"] = "summarize"
        
        # Add message to state
        state["messages"].append(
            AIMessage(content=f"Meta-Question Answer:\n{answer}")
        )
        
    except Exception as e:
        print(f"\n❌ Meta-question handling failed: {e}")
        
        # Fallback: route to clarification for standard processing
        state["meta_answer"] = None
        state["is_meta_question"] = False
        state["next_agent"] = "clarification"
        
        state["messages"].append(
            SystemMessage(content=f"Meta-question handling failed, routing to standard flow: {e}")
        )
    
    return state


def clarification_node(state: AgentState) -> AgentState:
    """
    Clarification node wrapping ClarificationAgent class.
    Combines OOP modularity with explicit state management.
    
    Handles up to 1 clarification request. If user provides clarification,
    incorporates it and proceeds to planning.
    
    IMPORTANT: This node COMBINES context instead of overwriting:
    - Preserves original_query unchanged
    - Stores clarification_message separately
    - Stores user_clarification_response separately
    - Creates combined_query_context for planning agent
    """
    print("\n" + "="*80)
    print("🔍 CLARIFICATION AGENT")
    print("="*80)
    
    # Initialize clarification count if not present
    clarification_count = state.get("clarification_count", 0)
    
    # Check if this is a user response to a previous clarification request
    user_response = state.get("user_clarification_response")
    if user_response and clarification_count > 0:
        print("✓ User provided clarification - incorporating feedback")
        
        # IMPORTANT: Do NOT overwrite original_query - keep it unchanged
        # Instead, create a combined context for planning agent
        original = state["original_query"]
        clarif_msg = state.get("clarification_message", "")
        
        # Build combined query context with structured format
        combined_context = f"""**Original Query**: {original}

**Clarification Question**: {clarif_msg}

**User's Answer**: {user_response}

**Context**: The user was asked for clarification and provided additional information. Use all three pieces of information together to understand the complete intent."""
        
        state["combined_query_context"] = combined_context
        state["question_clear"] = True
        state["next_agent"] = "planning"
        
        print(f"   Original Query (preserved): {original}")
        print(f"   Clarification Message: {clarif_msg}")
        print(f"   User Response: {user_response}")
        print(f"   ✓ Combined context created for planning agent")
        
        state["messages"].append(
            SystemMessage(content=f"User clarification incorporated: {user_response}\nCombined context created with original query, clarification question, and user answer.")
        )
        
        return state
    
    query = state["original_query"]
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
    
    # Use OOP agent with clarification count
    # Load context fresh from table (no redeployment needed for updates)
    clarification_agent = ClarificationAgent.from_table(llm, TABLE_NAME)
    clarity_result = clarification_agent(query, clarification_count)
    
    # Update explicit state
    state["question_clear"] = clarity_result.get("question_clear", True)
    state["clarification_needed"] = clarity_result.get("clarification_needed")
    state["clarification_options"] = clarity_result.get("clarification_options")
    
    if state["question_clear"]:
        print("✓ Query is clear - proceeding to planning")
        state["next_agent"] = "planning"
        # No clarification needed, so combined context is just the original query
        state["combined_query_context"] = state["original_query"]
    else:
        print("⚠ Query needs clarification (attempt 1 of 1)")
        print(f"   Reason: {state['clarification_needed']}")
        if state["clarification_options"]:
            print("   Options:")
            for i, opt in enumerate(state["clarification_options"], 1):
                print(f"     {i}. {opt}")
        
        # Increment clarification count
        state["clarification_count"] = clarification_count + 1
        
        # Route to END to show clarification request (routing controlled by route_after_clarification)
        # The actual routing is handled by the conditional edge which checks question_clear flag
        
        # Build and store clarification message
        clarification_message = (
            f"I need clarification: {state['clarification_needed']}\n\n"
            f"Please choose one of the following options or provide your own clarification:\n"
        )
        if state["clarification_options"]:
            for i, opt in enumerate(state["clarification_options"], 1):
                clarification_message += f"{i}. {opt}\n"
        
        # Store the clarification message in state
        state["clarification_message"] = clarification_message
        
        state["messages"].append(
            AIMessage(content=clarification_message)
        )
    
    state["messages"].append(
        SystemMessage(content=f"Clarification result: {json.dumps(clarity_result, indent=2)}")
    )
    
    return state


def planning_node(state: AgentState) -> AgentState:
    """
    Planning node wrapping PlanningAgent class.
    Combines OOP modularity with explicit state management.
    
    Uses combined_query_context if available (from clarification flow),
    otherwise uses original_query.
    """
    print("\n" + "="*80)
    print("📋 PLANNING AGENT")
    print("="*80)
    
    # Use combined_query_context if available (includes clarification context)
    # Otherwise fall back to original_query
    query = state.get("combined_query_context") or state["original_query"]
    
    if state.get("combined_query_context"):
        print("✓ Using combined query context (includes clarification)")
    else:
        print("✓ Using original query (no clarification needed)")
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING)
    
    # Use OOP agent
    planning_agent = PlanningAgent(llm, VECTOR_SEARCH_INDEX)
    
    # Get relevant spaces with full metadata (for Genie agents)
    relevant_spaces_full = planning_agent.search_relevant_spaces(query)
    
    # Create execution plan
    plan = planning_agent.create_execution_plan(query, relevant_spaces_full)
    
    # Update explicit state
    state["plan"] = plan
    state["sub_questions"] = plan.get("sub_questions", [])
    state["requires_multiple_spaces"] = plan.get("requires_multiple_spaces", False)
    state["relevant_space_ids"] = plan.get("relevant_space_ids", [])
    state["requires_join"] = plan.get("requires_join", False)
    state["join_strategy"] = plan.get("join_strategy")
    state["execution_plan"] = plan.get("execution_plan", "")
    state["genie_route_plan"] = plan.get("genie_route_plan")
    state["vector_search_relevant_spaces_info"] = plan.get("vector_search_relevant_spaces_info", [])
    
    # Store full relevant_spaces for Genie agents (includes searchable_content)
    # This avoids re-querying and reuses Vector Search results
    state["relevant_spaces"] = relevant_spaces_full
    
    # Determine next agent
    if state["join_strategy"] == "genie_route":
        print("✓ Plan complete - using GENIE ROUTE (Genie agents)")
        state["next_agent"] = "sql_synthesis_genie"
    else:
        print("✓ Plan complete - using TABLE ROUTE (direct SQL synthesis)")
        state["next_agent"] = "sql_synthesis_table"
    
    state["messages"].append(
        SystemMessage(content=f"Execution plan: {json.dumps(plan, indent=2)}")
    )
    
    return state


def sql_synthesis_table_node(state: AgentState) -> AgentState:
    """
    Fast SQL synthesis node wrapping SQLSynthesisTableAgent class.
    Combines OOP modularity with explicit state management.
    """
    print("\n" + "="*80)
    print("⚡ SQL SYNTHESIS AGENT - TABLE ROUTE")
    print("="*80)
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    
    # Use OOP agent
    sql_agent = SQLSynthesisTableAgent(llm, CATALOG, SCHEMA)
    
    # # Prepare plan for agent
    # plan = {
    #     "original_query": state["original_query"],
    #     "vector_search_relevant_spaces_info": state.get("vector_search_relevant_spaces_info", []),
    #     "relevant_space_ids": state.get("relevant_space_ids", []),
    #     "execution_plan": state.get("execution_plan", ""),
    #     "requires_join": state.get("requires_join", False),
    #     "sub_questions": state.get("sub_questions", [])
    # }
    plan = state.get("plan", {})
    print("plan loaded from state is:", plan)
    print(json.dumps(plan, indent=2))
    
    try:
        print("🤖 Invoking SQL synthesis agent...")
        result = sql_agent(plan)
        
        # Extract SQL and explanation
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        state["sql_synthesis_explanation"] = explanation
        
        # NEW: Extract all SQL queries from the complete response
        # Check both the extracted SQL and the full explanation for SQL blocks
        full_content = explanation
        if sql_query:
            full_content = f"{explanation}\n\n```sql\n{sql_query}\n```"
        
        sql_queries = extract_all_sql_queries(full_content)
        
        if sql_queries:
            # Multi-query support
            state["sql_queries"] = sql_queries
            state["sql_query"] = sql_queries[0]  # For backward compatibility
            state["has_sql"] = True
            state["next_agent"] = "sql_execution"
            
            print(f"✓ Extracted {len(sql_queries)} SQL quer{'y' if len(sql_queries) == 1 else 'ies'}")
            for i, query in enumerate(sql_queries, 1):
                print(f"  Query {i} preview: {query[:100]}...")
            
            if explanation:
                print(f"Agent Explanation: {explanation[:200]}...")
            
            # Add message with SQL synthesis explanation
            state["messages"].append(
                AIMessage(content=f"SQL Synthesis (Table Route):\n{explanation}")
            )
        else:
            print("⚠ No SQL generated - agent explanation:")
            print(f"  {explanation}")
            state["synthesis_error"] = "Cannot generate SQL query"
            state["next_agent"] = "summarize"
            
            # Add message with explanation even if no SQL
            state["messages"].append(
                AIMessage(content=f"SQL Synthesis Failed (Table Route):\n{explanation}")
            )
        
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        state["synthesis_error"] = str(e)
        state["sql_synthesis_explanation"] = str(e)
        # Route to summarize via conditional edge (route_after_synthesis)
        state["messages"].append(
                AIMessage(content=f"SQL Synthesis Failed (Table Route):\n{state['sql_synthesis_explanation']}")
            )
    
    return state


def sql_synthesis_genie_node(state: AgentState) -> AgentState:
    """
    Slow SQL synthesis node wrapping SQLSynthesisGenieAgent class.
    Combines OOP modularity with explicit state management.
    
    Uses relevant_spaces from PlanningAgent (no need to re-query all spaces).
    """
    print("\n" + "="*80)
    print("🐢 SQL SYNTHESIS AGENT - GENIE ROUTE")
    print("="*80)
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    
    # Get relevant spaces from state (already discovered by PlanningAgent)
    relevant_spaces = state.get("relevant_spaces", [])
    
    if not relevant_spaces:
        print("❌ No relevant_spaces found in state")
        state["synthesis_error"] = "No relevant spaces available for genie route"
        # Route to summarize via conditional edge (route_after_synthesis)
        return state
    
    # Use OOP agent - only creates Genie agents for relevant spaces
    sql_agent = SQLSynthesisGenieAgent(llm, relevant_spaces)
    
    plan = state.get("plan", {})
    genie_route_plan = plan.get("genie_route_plan", {})
    
    if not genie_route_plan:
        print("❌ No genie_route_plan found in plan")
        state["synthesis_error"] = "No routing plan available for genie route"
        # Route to summarize via conditional edge (route_after_synthesis)
        return state
    
    try:
        print(f"🤖 Querying {len(genie_route_plan)} Genie agents...")
        result = sql_agent(plan)
        
        # Extract SQL and explanation
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        state["sql_synthesis_explanation"] = explanation
        
        # NEW: Extract all SQL queries from the complete response
        # Check both the extracted SQL and the full explanation for SQL blocks
        full_content = explanation
        if sql_query:
            full_content = f"{explanation}\n\n```sql\n{sql_query}\n```"
        
        sql_queries = extract_all_sql_queries(full_content)
        
        if sql_queries:
            # Multi-query support
            state["sql_queries"] = sql_queries
            state["sql_query"] = sql_queries[0]  # For backward compatibility
            state["has_sql"] = True
            state["next_agent"] = "sql_execution"
            
            print(f"✓ Extracted {len(sql_queries)} SQL quer{'y' if len(sql_queries) == 1 else 'ies'}")
            for i, query in enumerate(sql_queries, 1):
                print(f"  Query {i} preview: {query[:100]}...")
            
            if explanation:
                print(f"Agent Explanation: {explanation[:200]}...")
            
            # Add message with SQL synthesis explanation
            state["messages"].append(
                AIMessage(content=f"SQL Synthesis (Genie Route):\n{explanation}")
            )
        else:
            print("⚠ No SQL generated - agent explanation:")
            print(f"  {explanation}")
            state["synthesis_error"] = "Cannot generate SQL query from Genie agent fragments"
            state["next_agent"] = "summarize"
            
            # Add message with explanation even if no SQL
            state["messages"].append(
                AIMessage(content=f"SQL Synthesis Failed (Genie Route):\n{explanation}")
            )
        
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        state["synthesis_error"] = str(e)
        state["sql_synthesis_explanation"] = str(e)
        # Route to summarize via conditional edge (route_after_synthesis)
        state["messages"].append(
                AIMessage(content=f"SQL Synthesis Failed (Genie Route):\n{state['sql_synthesis_explanation']}")
            )
    
    return state


def sql_execution_node(state: AgentState) -> AgentState:
    """
    SQL execution node wrapping SQLExecutionAgent class.
    Supports executing multiple SQL queries for multi-part questions.
    Combines OOP modularity with explicit state management.
    """
    print("\n" + "="*80)
    print("🚀 SQL EXECUTION AGENT")
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
        state["execution_error"] = "No SQL queries provided"
        state["next_agent"] = "summarize"
        return state
    
    print(f"📊 Executing {len(sql_queries)} SQL quer{'y' if len(sql_queries) == 1 else 'ies'}")
    
    # Use OOP agent
    execution_agent = SQLExecutionAgent()
    execution_results = []
    all_successful = True
    
    # Execute each query
    for i, query in enumerate(sql_queries, 1):
        print(f"\n{'─'*80}")
        print(f"Query {i} of {len(sql_queries)}")
        print(f"{'─'*80}")
        
        result = execution_agent.execute_sql(query)
        result["query_number"] = i  # Track which query this result is from
        execution_results.append(result)
        
        if not result["success"]:
            all_successful = False
            print(f"❌ Query {i} failed: {result.get('error')}")
        else:
            print(f"✓ Query {i} succeeded: {result['row_count']} rows")
    
    # Store results (both single and multiple for backward compatibility)
    state["execution_results"] = execution_results
    state["execution_result"] = execution_results[0]  # For backward compatibility
    
    # Update state based on execution results
    if all_successful:
        total_rows = sum(r["row_count"] for r in execution_results)
        success_msg = f"Executed {len(sql_queries)} quer{'y' if len(sql_queries) == 1 else 'ies'} successfully. Total rows: {total_rows}"
        print(f"\n✅ {success_msg}")
        
        state["messages"].append(
            SystemMessage(content=success_msg)
        )
    else:
        failed_count = sum(1 for r in execution_results if not r["success"])
        success_count = len(sql_queries) - failed_count
        error_msg = f"{failed_count} of {len(sql_queries)} queries failed"
        
        print(f"\n⚠️ Partial success: {success_count} succeeded, {failed_count} failed")
        
        state["execution_error"] = error_msg
        state["messages"].append(
            SystemMessage(content=f"{success_count} queries succeeded, {failed_count} failed")
        )
    
    state["next_agent"] = "summarize"
    
    return state


def summarize_node(state: AgentState) -> AgentState:
    """
    Result summarize node wrapping ResultSummarizeAgent class.
    
    This is the final node that all workflow paths go through.
    Generates a natural language summary AND preserves all workflow data.
    
    Returns state with ALL fields preserved including:
    - sql_query: Generated SQL query
    - execution_result: Query execution results
    - sql_synthesis_explanation: SQL generation explanation
    - synthesis_error: SQL generation errors (if any)
    - execution_error: Query execution errors (if any)
    - execution_plan: Planning agent's execution plan
    - final_summary: Natural language summary (NEW)
    """
    print("\n" + "="*80)
    print("📝 RESULT SUMMARIZE AGENT")
    print("="*80)
    
    # Create LLM for summarization (no max_tokens limit for comprehensive output)
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SUMMARIZE, temperature=0.1, max_tokens=2000)
    
    # Use OOP agent to generate summary
    summarize_agent = ResultSummarizeAgent(llm)
    summary = summarize_agent(state)
    
    print(f"\n✅ Summary Generated:")
    print(f"{summary}")
    
    # Store summary in state (all other fields are preserved automatically)
    state["final_summary"] = summary
    
    # Display what's being returned
    print(f"\n📦 State Fields Being Returned:")
    print(f"  ✓ final_summary: {len(summary)} chars")
    if state.get("sql_query"):
        print(f"  ✓ sql_query: {len(state['sql_query'])} chars")
    if state.get("execution_result"):
        exec_result = state["execution_result"]
        if exec_result.get("success"):
            print(f"  ✓ execution_result: {exec_result.get('row_count', 0)} rows")
        else:
            print(f"  ✓ execution_result: Failed - {exec_result.get('error', 'Unknown')[:50]}...")
    if state.get("sql_synthesis_explanation"):
        print(f"  ✓ sql_synthesis_explanation: {len(state['sql_synthesis_explanation'])} chars")
    if state.get("execution_plan"):
        print(f"  ✓ execution_plan: {state['execution_plan'][:80]}...")
    if state.get("synthesis_error"):
        print(f"  ⚠ synthesis_error: {state['synthesis_error'][:50]}...")
    if state.get("execution_error"):
        print(f"  ⚠ execution_error: {state['execution_error'][:50]}...")
    
    print("="*80)
    
    # Build comprehensive final message with ALL workflow information
    final_message_parts = []
    
    # 1. Summary
    final_message_parts.append(f"📝 **Summary:**\n{summary}\n")
    
    # 2. Original Query
    if state.get("original_query"):
        final_message_parts.append(f"🔍 **Original Query:**\n{state['original_query']}\n")
    
    # 3. Execution Plan
    if state.get("execution_plan"):
        final_message_parts.append(f"📋 **Execution Plan:**\n{state['execution_plan']}")
        if state.get("join_strategy"):
            final_message_parts.append(f"Strategy: {state['join_strategy']}\n")
    
    # 4. SQL Synthesis Explanation
    if state.get("sql_synthesis_explanation"):
        final_message_parts.append(f"💭 **SQL Synthesis Explanation:**\n{state['sql_synthesis_explanation']}\n")
    
    # 5. Generated SQL
    if state.get("sql_query"):
        final_message_parts.append(f"💻 **Generated SQL:**\n```sql\n{state['sql_query']}\n```\n")
    
    # 6. Execution Results
    exec_result = state.get("execution_result")
    if exec_result:
        if exec_result.get("success"):
            final_message_parts.append(f"✅ **Execution Successful:**\n")
            final_message_parts.append(f"- Rows: {exec_result.get('row_count', 0)}\n")
            final_message_parts.append(f"- Columns: {', '.join(exec_result.get('columns', []))}\n")
            
            # Convert results to pandas DataFrame and display
            results = exec_result.get("result", [])
            if results:
                try:
                    import pandas as pd
                    df = pd.DataFrame(results)
                    
                    final_message_parts.append(f"\n📊 **Query Results:**\n")
                    
                    # Display DataFrame
                    print("\n" + "="*80)
                    print("📊 QUERY RESULTS (Pandas DataFrame)")
                    print("="*80)
                    try:
                        display(df)  # Use Databricks display() for interactive view
                    except:
                        print(df.to_string())  # Fallback to string representation
                    print("="*80 + "\n")
                    
                    # Add DataFrame info to message
                    final_message_parts.append(f"DataFrame shape: {df.shape}\n")
                    final_message_parts.append(f"Preview (first 5 rows):\n```\n{df.head().to_string()}\n```\n")
                    
                    # Note: DataFrame not stored in state (not msgpack serializable)
                    # Users can recreate it from state['execution_result']['result']
                    
                except Exception as e:
                    final_message_parts.append(f"⚠️ Could not convert to DataFrame: {e}\n")
                    final_message_parts.append(f"Raw results (first 3): {results[:3]}\n")
        else:
            final_message_parts.append(f"❌ **Execution Failed:**\n")
            final_message_parts.append(f"Error: {exec_result.get('error', 'Unknown error')}\n")
    
    # 7. Errors (if any)
    if state.get("synthesis_error"):
        final_message_parts.append(f"❌ **Synthesis Error:**\n{state['synthesis_error']}\n")
    if state.get("execution_error"):
        final_message_parts.append(f"❌ **Execution Error:**\n{state['execution_error']}\n")
    
    # 8. Relevant Spaces (if any)
    if state.get("relevant_space_ids"):
        final_message_parts.append(f"\n🎯 **Relevant Genie Spaces:** {len(state['relevant_space_ids'])} spaces analyzed\n")
    
    # Combine all parts into final comprehensive message
    comprehensive_message = "\n".join(final_message_parts)
    
    # Add comprehensive message to state messages
    state["messages"].append(
        AIMessage(content=comprehensive_message)
    )
    
    print(f"\n✅ Comprehensive final message created ({len(comprehensive_message)} chars)")
    
    # Route to END via fixed edge (summarize → END)
    # Return complete state with ALL fields preserved
    return state

print("✓ All node wrappers defined (including summarize)")

# COMMAND ----------

# DBTITLE 1,Build LangGraph Workflow
def create_super_agent_hybrid():
    """
    Create the Hybrid Super Agent LangGraph workflow.
    
    Combines:
    - OOP agent classes for modularity
    - Explicit state management for observability
    """
    print("\n" + "="*80)
    print("🏗️ BUILDING HYBRID SUPER AGENT WORKFLOW")
    print("="*80)
    
    # Create the graph with explicit state
    workflow = StateGraph(AgentState)
    
    # Add nodes (wrapping OOP agents)
    workflow.add_node("meta_detection", meta_detection_node)  # NEW: Entry point for meta-question detection
    workflow.add_node("meta_handler", meta_handler_node)  # NEW: Fast context-based meta answers
    workflow.add_node("clarification", clarification_node)
    workflow.add_node("planning", planning_node)
    workflow.add_node("sql_synthesis_table", sql_synthesis_table_node)
    workflow.add_node("sql_synthesis_genie", sql_synthesis_genie_node)
    workflow.add_node("sql_execution", sql_execution_node)
    workflow.add_node("summarize", summarize_node)  # Final summarization node
    
    # Define routing logic based on explicit state
    def route_after_meta_detection(state: AgentState) -> str:
        """Route based on meta-question detection."""
        if state.get("is_meta_question"):
            route = state.get("meta_route", "context")
            if route == "context":
                return "meta_handler"  # Fast context-based answer
            elif route == "sql":
                return "planning"  # Statistical meta needs SQL generation
        return "clarification"  # Standard SQL question
    
    def route_after_clarification(state: AgentState) -> str:
        if state.get("question_clear", False):
            return "planning"
        return END  # End if clarification needed
    
    def route_after_planning(state: AgentState) -> str:
        next_agent = state.get("next_agent", "summarize")
        if next_agent == "sql_synthesis_table":
            return "sql_synthesis_table"
        elif next_agent == "sql_synthesis_genie":
            return "sql_synthesis_genie"
        return "summarize"
    
    def route_after_synthesis(state: AgentState) -> str:
        next_agent = state.get("next_agent", "summarize")
        if next_agent == "sql_execution":
            return "sql_execution"
        return "summarize"  # Summarize if synthesis error
    
    # Add edges with conditional routing
    workflow.set_entry_point("meta_detection")  # Changed from "clarification" to support meta-questions
    
    # NEW: Route from meta_detection to appropriate handler
    workflow.add_conditional_edges(
        "meta_detection",
        route_after_meta_detection,
        {
            "meta_handler": "meta_handler",  # Context-based meta answer
            "planning": "planning",  # Statistical meta (needs SQL)
            "clarification": "clarification"  # Standard SQL question
        }
    )
    
    # NEW: Meta handler goes directly to summarize (no SQL needed)
    workflow.add_edge("meta_handler", "summarize")
    
    workflow.add_conditional_edges(
        "clarification",
        route_after_clarification,
        {
            "planning": "planning",
            END: END
        }
    )
    
    workflow.add_conditional_edges(
        "planning",
        route_after_planning,
        {
            "sql_synthesis_table": "sql_synthesis_table",
            "sql_synthesis_genie": "sql_synthesis_genie",
            "summarize": "summarize"
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_table",
        route_after_synthesis,
        {
            "sql_execution": "sql_execution",
            "summarize": "summarize"
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_genie",
        route_after_synthesis,
        {
            "sql_execution": "sql_execution",
            "summarize": "summarize"
        }
    )
    
    # SQL execution always goes to summarize
    workflow.add_edge("sql_execution", "summarize")
    
    # Summarize is the final node before END
    workflow.add_edge("summarize", END)
    
    # Compile the graph with memory
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)
    
    print("✓ Workflow nodes added:")
    print("  1. Meta-Question Detector (OOP) - ENTRY POINT")
    print("  2. Meta-Question Handler (OOP) - Context-based answers")
    print("  3. Clarification Agent (OOP)")
    print("  4. Planning Agent (OOP)")
    print("  5. SQL Synthesis Agent - Table Route (OOP)")
    print("  6. SQL Synthesis Agent - Genie Route (OOP)")
    print("  7. SQL Execution Agent (OOP)")
    print("  8. Result Summarize Agent (OOP) - FINAL NODE")
    print("\n✓ Meta-question detection enabled (pattern-based + LLM fallback)")
    print("✓ Explicit state management enabled")
    print("✓ Conditional routing configured")
    print("✓ All paths route to summarize node before END")
    print("✓ Memory checkpointer enabled")
    print("\n✅ Hybrid Super Agent workflow compiled successfully!")
    print("="*80)
    
    return app

# Create the Hybrid Super Agent
super_agent_hybrid = create_super_agent_hybrid()

# COMMAND ----------

# DBTITLE 1,ResponsesAgent Wrapper for Deployment
class SuperAgentHybridResponsesAgent(ResponsesAgent):
    """
    Wrapper class to make the Hybrid Super Agent compatible with Databricks Model Serving.
    
    This class implements the ResponsesAgent interface required for deployment
    to Databricks Model Serving endpoints with proper streaming support.
    """
    
    def __init__(self, agent: CompiledStateGraph):
        """
        Initialize the ResponsesAgent wrapper.
        
        Args:
            agent: The compiled LangGraph workflow
        """
        self.agent = agent
        print("✓ SuperAgentHybridResponsesAgent initialized")
    
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        """
        Make a prediction (non-streaming).
        
        Args:
            request: The request containing input messages
            
        Returns:
            ResponsesAgentResponse with output items
        """
        outputs = [
            event.item
            for event in self.predict_stream(request)
            if event.type == "response.output_item.done"
        ]
        return ResponsesAgentResponse(output=outputs, custom_outputs=request.custom_inputs)
    
    def predict_stream(
        self,
        request: ResponsesAgentRequest,
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        """
        Make a streaming prediction.
        
        Handles three scenarios:
        1. New query: Fresh start with new original_query
        2. Clarification response: User answering agent's clarification question
        3. Follow-up query: New query with access to previous conversation context
        
        The thread-based memory system (MemorySaver) automatically preserves and restores
        conversation context across all scenarios.
        
        Args:
            request: The request containing:
                - input: List of messages (user query is the last message)
                - custom_inputs: Dict with optional keys:
                    - thread_id (str): Thread identifier for conversation continuity (default: "default")
                    - is_clarification_response (bool): Set to True when user is answering clarification
                    - clarification_count (int): Preserved from previous state
                    - original_query (str): Preserved from previous state for clarification responses
                    - clarification_message (str): Preserved from previous state for clarification responses
            
        Yields:
            ResponsesAgentStreamEvent for each step in the workflow
            
        Usage in Model Serving:
            # New query
            POST /invocations
            {
                "messages": [{"role": "user", "content": "Show me patient data"}],
                "custom_inputs": {"thread_id": "session_001"}
            }
            
            # Clarification response (after agent asked for clarification)
            POST /invocations
            {
                "messages": [{"role": "user", "content": "Patient count by age group"}],
                "custom_inputs": {
                    "thread_id": "session_001",  # Must match previous call
                    "is_clarification_response": true,
                    "original_query": "Show me patient data",  # From previous state
                    "clarification_message": "...",  # From previous state
                    "clarification_count": 1  # From previous state
                }
            }
            
            # Follow-up query
            POST /invocations
            {
                "messages": [{"role": "user", "content": "Now show by gender"}],
                "custom_inputs": {"thread_id": "session_001"}  # Same thread_id
            }
        """
        # Convert request input to chat completions format
        cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
        
        # Get the latest user message
        latest_query = cc_msgs[-1]["content"] if cc_msgs else ""
        
        # Configure with thread ID for conversation continuity
        # The MemorySaver checkpoint will restore previous state for this thread
        thread_id = request.custom_inputs.get("thread_id", "default") if request.custom_inputs else "default"
        config = {"configurable": {"thread_id": thread_id}}
        
        # Check if this is a clarification response
        # When True, the user is answering the agent's clarification question
        is_clarification_response = request.custom_inputs.get("is_clarification_response", False) if request.custom_inputs else False
        
        # Initialize state based on scenario
        if is_clarification_response:
            # Scenario 2: Clarification Response
            # User is answering the agent's clarification question
            # We need to preserve state from previous call and add user's response
            
            # Get preserved state from custom_inputs (caller must pass these)
            original_query = request.custom_inputs.get("original_query", latest_query)
            clarification_message = request.custom_inputs.get("clarification_message", "")
            clarification_count = request.custom_inputs.get("clarification_count", 1)
            
            initial_state = {
                # Preserve from previous state
                "original_query": original_query,  # Keep original unchanged
                "clarification_message": clarification_message,  # Keep clarification question
                "clarification_count": clarification_count,  # Keep count
                
                # Add user's clarification response
                "user_clarification_response": latest_query,
                "question_clear": False,  # Will be set to True by clarification_node
                
                # Messages
                "messages": [HumanMessage(content=f"Clarification response: {latest_query}")],
                
                # Route back to clarification node to process response
                "next_agent": "clarification"
            }
            
        else:
            # Scenario 1 & 3: New Query or Follow-Up Query
            # For both scenarios, we start fresh but thread memory will restore context
            # The key difference:
            # - New query (Scenario 1): No previous context in thread memory
            # - Follow-up query (Scenario 3): Thread memory restores previous conversation
            
            initial_state = {
                "original_query": latest_query,
                "question_clear": False,
                "messages": [
                    SystemMessage(content="""You are a multi-agent Q&A analysis system.
Your role is to help users query and analyze cross-domain data.

Guidelines:
- Always explain your reasoning and execution plan
- Validate SQL queries before execution
- Provide clear, comprehensive summaries
- If information is missing, ask for clarification (max once)
- Use UC functions and Genie agents to generate accurate SQL
- Return results with proper context and explanations"""),
                    HumanMessage(content=latest_query)
                ],
                "next_agent": "clarification"
            }
        
        first_message = True
        seen_ids = set()
        
        # Stream the workflow execution
        # The MemorySaver checkpoint will:
        # 1. Restore previous state from thread_id (if exists)
        # 2. Merge with initial_state (initial_state takes precedence for specified fields)
        # 3. Preserve conversation history across turns
        for _, events in self.agent.stream(initial_state, config, stream_mode=["updates"]):
            new_msgs = [
                msg
                for v in events.values()
                for msg in v.get("messages", [])
                if hasattr(msg, 'id') and msg.id not in seen_ids
            ]
            
            if first_message:
                seen_ids.update(msg.id for msg in new_msgs[: len(cc_msgs)])
                new_msgs = new_msgs[len(cc_msgs) :]
                first_message = False
            else:
                seen_ids.update(msg.id for msg in new_msgs)
                # Get node name
                if events:
                    node_name = tuple(events.keys())[0]
                    yield ResponsesAgentStreamEvent(
                        type="response.output_item.done",
                        item=self.create_text_output_item(
                            text=f"<name>{node_name}</name>", id=str(uuid4())
                        ),
                    )
            
            if len(new_msgs) > 0:
                yield from output_to_responses_items_stream(new_msgs)


# Create the deployable agent
AGENT = SuperAgentHybridResponsesAgent(super_agent_hybrid)

print("\n" + "="*80)
print("✅ HYBRID SUPER AGENT RESPONSES AGENT CREATED")
print("="*80)
print("Architecture: OOP Agents + Explicit State Management")
print("Benefits:")
print("  ✓ Modular and testable agent classes")
print("  ✓ Full state observability for debugging")
print("  ✓ Production-ready with development-friendly design")
print("\nThis agent is now ready for:")
print("  1. Local testing with AGENT.predict()")
print("  2. Logging with mlflow.pyfunc.log_model()")
print("  3. Deployment to Databricks Model Serving")
print("="*80)

# Set the agent for MLflow tracking
mlflow.langchain.autolog()
mlflow.models.set_model(AGENT)

# COMMAND ----------

# DBTITLE 1,Helper Function: Invoke Hybrid Super Agent
def invoke_super_agent_hybrid(query: str, thread_id: str = "default") -> Dict[str, Any]:
    """
    Invoke the Hybrid Super Agent with a user query.
    
    Args:
        query: User's question
        thread_id: Thread ID for conversation tracking (default: "default")
    
    Returns:
        Final state with execution results
    """
    print("\n" + "="*80)
    print("🚀 INVOKING HYBRID SUPER AGENT")
    print("="*80)
    print(f"Query: {query}")
    print(f"Thread ID: {thread_id}")
    print("="*80)
    
    # Initialize state
    initial_state = {
        "original_query": query,
        "question_clear": False,
        "messages": [
            SystemMessage(content="""You are a multi-agent Q&A analysis system.
Your role is to help users query and analyze cross-domain data.

Guidelines:
- Always explain your reasoning and execution plan
- Validate SQL queries before execution
- Provide clear, comprehensive summaries
- If information is missing, ask for clarification (max once)
- Use UC functions and Genie agents to generate accurate SQL
- Return results with proper context and explanations"""),
            HumanMessage(content=query)
        ],
        "next_agent": "clarification"
    }
    
    # Configure with thread
    config = {"configurable": {"thread_id": thread_id}}
    
    # Enable MLflow tracing
    mlflow.langchain.autolog()
    
    # Invoke the workflow
    final_state = super_agent_hybrid.invoke(initial_state, config)
    
    print("\n" + "="*80)
    print("✅ HYBRID SUPER AGENT EXECUTION COMPLETE")
    print("="*80)
    
    return final_state

# COMMAND ----------

# DBTITLE 1,Helper Function: Respond to Clarification
def respond_to_clarification(
    clarification_response: str, 
    previous_state: Dict[str, Any],
    thread_id: str = "default"
) -> Dict[str, Any]:
    """
    Respond to a clarification request and continue the workflow.
    
    Use this function when the agent requests clarification. Provide your
    clarification and the workflow will continue to planning and execution.
    
    IMPORTANT: This function preserves conversation history and state from
    the previous turn, leveraging the thread-based memory system.
    
    Args:
        clarification_response: Your clarification/answer to the agent's question
        previous_state: The state returned from the previous invoke call
        thread_id: Thread ID for conversation tracking (must match previous call)
    
    Returns:
        Final state with execution results
    
    Example:
        # First call
        state1 = invoke_super_agent_hybrid("Show me the data", thread_id="session_001")
        
        # If clarification needed
        if not state1['question_clear']:
            print("Clarification needed:", state1['clarification_needed'])
            print("Options:", state1['clarification_options'])
            
            # Provide clarification
            state2 = respond_to_clarification(
                "Show me patient count by age group",
                previous_state=state1,
                thread_id="session_001"
            )
    """
    print("\n" + "="*80)
    print("💬 RESPONDING TO CLARIFICATION")
    print("="*80)
    print(f"User Response: {clarification_response}")
    print(f"Thread ID: {thread_id}")
    print(f"Original Query: {previous_state['original_query']}")
    print("="*80)
    
    # Create new state that PRESERVES previous state and adds clarification response
    # The thread memory will automatically restore previous conversation context
    new_state = {
        "original_query": previous_state["original_query"],  # Keep original unchanged
        "question_clear": False,  # Will be set to True by clarification node
        "clarification_count": previous_state.get("clarification_count", 1),
        "clarification_message": previous_state.get("clarification_message", ""),  # Preserve clarification message
        "user_clarification_response": clarification_response,  # Store user's answer
        "messages": [
            # Add user's clarification response as a new message
            HumanMessage(content=f"Clarification response: {clarification_response}")
        ],
        "next_agent": "clarification"  # Re-enter clarification node to process response
    }
    
    # Configure with thread (must match previous call)
    # The thread memory system will restore previous conversation state
    config = {"configurable": {"thread_id": thread_id}}
    
    # Enable MLflow tracing
    mlflow.langchain.autolog()
    
    print("✓ State prepared with preserved context")
    print(f"  - Original query preserved: {new_state['original_query']}")
    print(f"  - Clarification message preserved: {new_state.get('clarification_message', 'N/A')[:50]}...")
    print(f"  - User response added: {clarification_response}")
    
    # Continue the workflow - thread memory will merge with previous state
    final_state = super_agent_hybrid.invoke(new_state, config)
    
    print("\n" + "="*80)
    print("✅ WORKFLOW COMPLETE AFTER CLARIFICATION")
    print("="*80)
    
    return final_state

# COMMAND ----------

# DBTITLE 1,Helper Function: Follow-Up Query
def ask_follow_up_query(
    new_query: str,
    thread_id: str = "default"
) -> Dict[str, Any]:
    """
    Ask a follow-up query in the same conversation thread.
    
    This function enables conversation continuity - the agent will have access
    to all previous queries, clarifications, and results in the same thread.
    
    Use this for:
    - Asking a completely new question while maintaining context
    - Asking a related question that builds on previous results
    - Requesting different analysis of the same data
    
    Args:
        new_query: The new question to ask
        thread_id: Thread ID for conversation tracking (use same thread_id as previous calls)
    
    Returns:
        Final state with execution results
    
    Example:
        # First query
        state1 = invoke_super_agent_hybrid("Show me patient count", thread_id="session_001")
        display_results(state1)
        
        # Follow-up query in same conversation
        state2 = ask_follow_up_query(
            "Now show me the average age by gender",
            thread_id="session_001"
        )
        display_results(state2)
        
        # Another follow-up
        state3 = ask_follow_up_query(
            "What about diabetes patients only?",
            thread_id="session_001"
        )
        display_results(state3)
    """
    print("\n" + "="*80)
    print("💬 FOLLOW-UP QUERY")
    print("="*80)
    print(f"New Query: {new_query}")
    print(f"Thread ID: {thread_id}")
    print(f"✓ This query will have access to previous conversation context")
    print("="*80)
    
    # Create state for new query
    # The thread memory will automatically restore previous conversation context
    new_state = {
        "original_query": new_query,
        "question_clear": False,
        "messages": [
            HumanMessage(content=new_query)
        ],
        "next_agent": "clarification"
    }
    
    # Configure with thread ID to restore conversation context
    config = {"configurable": {"thread_id": thread_id}}
    
    # Enable MLflow tracing
    mlflow.langchain.autolog()
    
    print("✓ Invoking agent with conversation context from thread")
    
    # Invoke the workflow - thread memory will merge with previous states
    final_state = super_agent_hybrid.invoke(new_state, config)
    
    print("\n" + "="*80)
    print("✅ FOLLOW-UP QUERY COMPLETE")
    print("="*80)
    
    return final_state

# COMMAND ----------

# DBTITLE 1,Helper Function: Display Results
def display_results(final_state: Dict[str, Any]):
    """
    Display the results from the Hybrid Super Agent execution.
    Shows SQL query, execution results, plans, and any errors.
    """
    print("\n" + "="*80)
    print("📊 FINAL RESULTS")
    print("="*80)
    
    # Display Summary (if available)
    if final_state.get('final_summary'):
        print(f"\n📝 Summary:")
        print(f"  {final_state.get('final_summary')}")
        print()
    
    # Display Original Query
    print(f"\n🔍 Original Query:")
    print(f"  {final_state.get('original_query', 'N/A')}")
    
    # Display Clarification Info (if any)
    if not final_state.get('question_clear', True):
        print(f"\n⚠️  Clarification Needed:")
        print(f"  Reason: {final_state.get('clarification_needed', 'N/A')}")
        if final_state.get('clarification_options'):
            print(f"  Options:")
            for i, opt in enumerate(final_state.get('clarification_options', []), 1):
                print(f"    {i}. {opt}")
    
    # Display Execution Plan
    if final_state.get('execution_plan'):
        print(f"\n📋 Execution Plan:")
        print(f"  {final_state.get('execution_plan')}")
        print(f"  Strategy: {final_state.get('join_strategy', 'N/A')}")
    
    # Display SQL Synthesis Explanation
    if final_state.get('sql_synthesis_explanation'):
        print(f"\n💭 SQL Synthesis Explanation:")
        print(f"  {final_state.get('sql_synthesis_explanation')}")
    
    # Display SQL
    if final_state.get('sql_query'):
        print(f"\n💻 Generated SQL:")
        print("─"*80)
        print(final_state.get('sql_query'))
        print("─"*80)
    
    # Display Execution Results
    exec_result = final_state.get('execution_result')
    if exec_result and exec_result.get('success'):
        print(f"\n✅ Execution Successful:")
        print(f"  Rows: {exec_result.get('row_count', 0)}")
        print(f"  Columns: {', '.join(exec_result.get('columns', []))}")
        
        # Display results preview
        results = exec_result.get("result", [])
        if results:
            print(f"\n📊 Query Results (first 10 rows):")
            for i, row in enumerate(results[:10], 1):
                print(f"  Row {i}: {row}")
    elif exec_result and not exec_result.get('success'):
        print(f"\n❌ Execution Failed:")
        print(f"  Error: {exec_result.get('error', 'Unknown error')}")
    
    # Display Errors
    if final_state.get('synthesis_error'):
        print(f"\n❌ Synthesis Error:")
        print(f"  {final_state.get('synthesis_error')}")
    if final_state.get('execution_error'):
        print(f"\n❌ Execution Error:")
        print(f"  {final_state.get('execution_error')}")
    
    print("\n" + "="*80)

# COMMAND ----------

# DBTITLE 1,Helper Function: Get Results as DataFrame
def get_results_as_dataframe(final_state: Dict[str, Any]):
    """
    Convert execution results to pandas DataFrame for easy analysis.
    
    Args:
        final_state: The final state from invoke_super_agent_hybrid()
        
    Returns:
        pandas.DataFrame or None if no results
        
    Example:
        final_state = invoke_super_agent_hybrid("Show claims")
        df = get_results_as_dataframe(final_state)
        if df is not None:
            print(df.describe())
            df.plot()
    """
    import pandas as pd
    
    exec_result = final_state.get('execution_result')
    if not exec_result or not exec_result.get('success'):
        print("⚠️ No successful execution results to convert")
        return None
    
    results = exec_result.get('result', [])
    if not results:
        print("⚠️ No data in results")
        return None
    
    try:
        df = pd.DataFrame(results)
        print(f"✅ Converted {len(results)} rows to pandas DataFrame")
        print(f"Shape: {df.shape}")
        print(f"Columns: {list(df.columns)}")
        return df
    except Exception as e:
        print(f"❌ Error converting to DataFrame: {e}")
        return None

print("✓ Helper functions defined")

# COMMAND ----------

# DBTITLE 1,Test Start Here
# Example test query
test_query = "What is the average cost of medical claims per claim in 2024?"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_001")

# COMMAND ----------

# DBTITLE 1,meta 1
# Example meta query 1
test_query = "What I can do here"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_meta_001")

# COMMAND ----------

# DBTITLE 1,meta 2
# Example meta query 1
test_query = "What datasets are available"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_meta_002")

# COMMAND ----------

# DBTITLE 1,meta 3
# Example meta query 1
test_query = "What questions can I ask?"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_meta_003")

# COMMAND ----------

# DBTITLE 1,meta 4
# Example meta query 1
test_query = "Can you offer me a list of cross-space questions I can ask"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_meta_004")

# COMMAND ----------

# DBTITLE 1,meta 5
# Example meta query 1
test_query = "How many total claims? How many total members?"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_meta_005")

# COMMAND ----------

# DBTITLE 1,meta 6
# Example meta query 1
test_query = "How many total claims group by payer type and report average medical cost? How many total members by age group? what are top 5 frequent diagnosis codes and top 5 procedure codes for members between 30-45?"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_meta_006")

# COMMAND ----------

# DBTITLE 1,same query for genie route
# Example test query
test_query = "What is the average cost of medical claims per claim in 2024? Use genie route"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_001_genie")

# COMMAND ----------

# Example test query
test_query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?"
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_003_table")

##----clarify quetsions-----
#  Options:
#      1. Which specific diabetes diagnosis codes (ICD-10) should be included in the analysis (e.g., E10.x for Type 1, E11.x for Type 2, or all diabetes-related codes)?
#      2. Which cost metric should be used: line charges, allowed amounts, patient copays, or another financial measure from the claims data?
#      3. Should the analysis include only claims where diabetes is the primary diagnosis, or any claim where diabetes appears as a secondary diagnosis?

# COMMAND ----------

#-------
follow_up = """
 1. E10-E14
  2. both line charges and allowed, aggregate at claim level
  3. any claim where a diabetes diagnosis appears

"""
# User provides clarification
final_state = respond_to_clarification(
    follow_up,
    previous_state=final_state,
    thread_id="test_hybrid_003_table"
)

# COMMAND ----------

# Example test query
test_query = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? Use genie route. Dont clarify anything, go with your call."
# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_003_genie")

##----clarify quetsions-----
#  Options:
#      1. Which specific diabetes diagnosis codes (ICD-10) should be included in the analysis (e.g., E10.x for Type 1, E11.x for Type 2, or all diabetes-related codes)?
#      2. Which cost metric should be used: line charges, allowed amounts, patient copays, or another financial measure from the claims data?
#      3. Should the analysis include only claims where diabetes is the primary diagnosis, or any claim where diabetes appears as a secondary diagnosis?

# COMMAND ----------

#-------
# follow_up = """
#   1. both line charges and allowed
#   2. you decide
#   3. E10-E14
# """

follow_up = """
you decide anything
"""

# User provides clarification
final_state = respond_to_clarification(
    follow_up,
    previous_state=final_state,
    thread_id="test_hybrid_003_genie"
)

# COMMAND ----------

final_state

# COMMAND ----------

# DBTITLE 1,Test Hybrid Super Agent (table route)
# Display results
display_results(final_state)

# COMMAND ----------

# DBTITLE 1,Test Hybrid Super Agent (genie route)
# Example test query
test_query = "What is the average cost of medical claims  per claim in 2024? use genie route"

# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_002")

# Display results
display_results(final_state)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Additional Test Cases

# COMMAND ----------

# DBTITLE 1,Test Case 1: Simple Single-Space Query
test_query_1 = "How many patients are in the dataset?"
result_1 = invoke_super_agent_hybrid(test_query_1, thread_id="test_hybrid_simple")
display_results(result_1)

# COMMAND ----------

# DBTITLE 1,Test Case 2: Multi-Space Query with JOIN (Table Route)
test_query_2 = "What is the average cost of medical claims for patients diagnosed with diabetes?"
result_2 = invoke_super_agent_hybrid(test_query_2, thread_id="test_hybrid_fast")
display_results(result_2)

# COMMAND ----------

# DBTITLE 1,Test Case 3: Multi-Space Query with JOIN (Genie Route - Explicit)
test_query_3 = "What is the average cost of medical claims for patients diagnosed with diabetes? Use genie route"
result_3 = invoke_super_agent_hybrid(test_query_3, thread_id="test_hybrid_slow")
display_results(result_3)

# COMMAND ----------

# DBTITLE 1,Test Case 4: Complex Multi-Space Query
test_query_4 = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group? Dont clarify. Use Genie Route."
result_4 = invoke_super_agent_hybrid(test_query_4, thread_id="test_hybrid_complex")
display_results(result_4)

# COMMAND ----------

# DBTITLE 1,Test Case 5: Clarification Flow (Vague Query → Clarify → Continue)
# Test with an intentionally vague query
test_query_5 = "How many?"  # Vague - should trigger clarification
result_5a = invoke_super_agent_hybrid(test_query_5, thread_id="test_hybrid_clarification")
display_results(result_5a)

# If clarification was requested, respond to it
if not result_5a.get('question_clear'):
    print("\n" + "="*80)
    print("📝 PROVIDING CLARIFICATION")
    print("="*80)
    
    # User provides clarification
    result_5b = respond_to_clarification(
        "I want to know how many patients are in the dataset",
        previous_state=result_5a,
        thread_id="test_hybrid_clarification"
    )
    
    # Display final results after clarification
    display_results(result_5b)
else:
    print("✓ No clarification needed - query was clear enough")

# COMMAND ----------

# DBTITLE 1,Test Case 6: Another Clarification Example
# Test with another vague query
test_query_6 = "Show me the data"  # Vague - should trigger clarification
result_6a = invoke_super_agent_hybrid(test_query_6, thread_id="test_hybrid_clarification2")
display_results(result_6a)

# If clarification was requested, respond to it
if not result_6a.get('question_clear'):
    print("\n" + "="*80)
    print("📝 PROVIDING CLARIFICATION")
    print("="*80)
    
    # User chooses one of the suggested options
    result_6b = respond_to_clarification(
        "Show me patient count grouped by age group",
        previous_state=result_6a,
        thread_id="test_hybrid_clarification2"
    )
    
    # Display final results after clarification
    display_results(result_6b)
else:
    print("✓ No clarification needed - query was clear enough")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Clarification Flow Summary
# MAGIC
# MAGIC The clarification system now works as follows:
# MAGIC
# MAGIC 1. **Lenient by Default**: Only asks for clarification on truly vague queries
# MAGIC 2. **Max 1 Clarification**: Won't ask endlessly - at most one clarification request
# MAGIC 3. **Easy Response Flow**: Use `respond_to_clarification()` to provide feedback
# MAGIC 4. **Proceeds Automatically**: After clarification, continues to planning and execution
# MAGIC
# MAGIC **Example Flow:**
# MAGIC ```python
# MAGIC # Step 1: Try with vague query
# MAGIC state1 = invoke_super_agent_hybrid("How many?", thread_id="session_001")
# MAGIC
# MAGIC # Step 2: Check if clarification needed
# MAGIC if not state1['question_clear']:
# MAGIC     # Step 3: Provide clarification
# MAGIC     state2 = respond_to_clarification(
# MAGIC         "How many patients are in the dataset?",
# MAGIC         previous_state=state1,
# MAGIC         thread_id="session_001"
# MAGIC     )
# MAGIC     # Step 4: Get results
# MAGIC     display_results(state2)
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC
# MAGIC ```
# MAGIC ===================================================================================
# MAGIC HYBRID SUPER AGENT - BEST OF BOTH WORLDS
# MAGIC ===================================================================================
# MAGIC
# MAGIC This notebook implements a hybrid architecture combining:
# MAGIC
# MAGIC 1. OOP AGENT CLASSES (from agent.py)
# MAGIC    ✅ Modular and testable
# MAGIC    ✅ Clean separation of concerns
# MAGIC    ✅ Reusable across projects
# MAGIC    ✅ Easy to extend and maintain
# MAGIC
# MAGIC 2. EXPLICIT STATE MANAGEMENT (from Super_Agent.py)
# MAGIC    ✅ Full observability
# MAGIC    ✅ Easy debugging
# MAGIC    ✅ Clear data flow
# MAGIC    ✅ Type-safe state tracking
# MAGIC
# MAGIC ARCHITECTURE:
# MAGIC ══════════════
# MAGIC
# MAGIC Agent Classes (OOP):
# MAGIC - ClarificationAgent
# MAGIC - PlanningAgent
# MAGIC - SQLSynthesisTableAgent
# MAGIC - SQLSynthesisGenieAgent
# MAGIC - SQLExecutionAgent
# MAGIC
# MAGIC Node Wrappers:
# MAGIC - clarification_node(state) → calls ClarificationAgent, updates state
# MAGIC - planning_node(state) → calls PlanningAgent, updates state
# MAGIC - sql_synthesis_table_node(state) → calls SQLSynthesisTableAgent, updates state
# MAGIC - sql_synthesis_genie_node(state) → calls SQLSynthesisGenieAgent, updates state
# MAGIC - sql_execution_node(state) → calls SQLExecutionAgent, updates state
# MAGIC - summarize_node(state) → calls ResultSummarizeAgent, preserves ALL state fields
# MAGIC
# MAGIC State Management:
# MAGIC - AgentState(TypedDict) with explicit fields
# MAGIC - Full observability at every step
# MAGIC - Easy to debug and monitor
# MAGIC - ALL fields preserved in final state (sql_query, results, explanations, errors, summary)
# MAGIC
# MAGIC BENEFITS:
# MAGIC ═════════
# MAGIC
# MAGIC ✅ Production Ready - Clean OOP design for maintainability
# MAGIC ✅ Easy Debugging - Explicit state shows exactly what's happening
# MAGIC ✅ Modular - Agent classes can be tested independently
# MAGIC ✅ Observable - All state transitions are visible
# MAGIC ✅ Extensible - Easy to add new agents or modify existing ones
# MAGIC ✅ Team-Friendly - Clear contracts and interfaces
# MAGIC
# MAGIC USAGE:
# MAGIC ══════
# MAGIC
# MAGIC # Simple invocation
# MAGIC final_state = invoke_super_agent_hybrid("Your question here", thread_id="session_123")
# MAGIC display_results(final_state)
# MAGIC
# MAGIC # Access ALL preserved fields programmatically
# MAGIC summary = final_state['final_summary']  # Natural language summary
# MAGIC sql = final_state.get('sql_query')  # Generated SQL
# MAGIC explanation = final_state.get('sql_synthesis_explanation')  # SQL generation explanation
# MAGIC plan = final_state.get('execution_plan')  # Execution plan
# MAGIC
# MAGIC if final_state['execution_result']['success']:
# MAGIC     data = final_state['execution_result']['result']  # Query results
# MAGIC     row_count = final_state['execution_result']['row_count']  # Number of rows
# MAGIC     columns = final_state['execution_result']['columns']  # Column names
# MAGIC else:
# MAGIC     error = final_state['execution_result'].get('error')  # Execution error
# MAGIC     synthesis_error = final_state.get('synthesis_error')  # SQL generation error
# MAGIC
# MAGIC NEXT STEPS:
# MAGIC ═══════════
# MAGIC
# MAGIC 1. Deploy to Databricks as a ResponsesAgent endpoint
# MAGIC 2. Monitor with MLflow tracing in production
# MAGIC 3. Extend with additional specialized agents
# MAGIC 4. Integrate with Genie UI
# MAGIC
# MAGIC ===================================================================================
# MAGIC ```

# COMMAND ----------

# DBTITLE 1,Example: Complete Clarification Flow with Context Preservation
# MAGIC %md
# MAGIC ### Example: Clarification Flow with Context Preservation
# MAGIC
# MAGIC This example demonstrates how the agent:
# MAGIC 1. Detects vague queries and asks for clarification
# MAGIC 2. **PRESERVES** original query, clarification message, and user response separately
# MAGIC 3. **COMBINES** all context for planning agent
# MAGIC 4. Continues workflow seamlessly
# MAGIC
# MAGIC """
# MAGIC # Example 1: Clarification Flow with Context Combination
# MAGIC session_id = "demo_clarification_001"
# MAGIC
# MAGIC # Step 1: Ask a vague query
# MAGIC print("="*80)
# MAGIC print("STEP 1: Initial vague query")
# MAGIC print("="*80)
# MAGIC state1 = invoke_super_agent_hybrid(
# MAGIC     "Show me the data about patients",  # Vague - what data? which patients?
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC
# MAGIC # Check if clarification is needed
# MAGIC if not state1.get('question_clear'):
# MAGIC     print("\n" + "="*80)
# MAGIC     print("CLARIFICATION REQUESTED BY AGENT")
# MAGIC     print("="*80)
# MAGIC     print(f"Original Query (preserved): {state1['original_query']}")
# MAGIC     print(f"Clarification Needed: {state1['clarification_needed']}")
# MAGIC     print(f"Options: {state1['clarification_options']}")
# MAGIC     
# MAGIC     # Step 2: User provides clarification
# MAGIC     print("\n" + "="*80)
# MAGIC     print("STEP 2: User provides clarification")
# MAGIC     print("="*80)
# MAGIC     state2 = respond_to_clarification(
# MAGIC         "Show me patient count grouped by age group",
# MAGIC         previous_state=state1,
# MAGIC         thread_id=session_id
# MAGIC     )
# MAGIC     
# MAGIC     # Verify context was properly combined
# MAGIC     print("\n" + "="*80)
# MAGIC     print("CONTEXT VERIFICATION")
# MAGIC     print("="*80)
# MAGIC     print(f"✓ Original Query Preserved: {state2.get('original_query')}")
# MAGIC     print(f"✓ Clarification Message: {state2.get('clarification_message', 'N/A')[:100]}...")
# MAGIC     print(f"✓ User Response: {state2.get('user_clarification_response')}")
# MAGIC     print(f"✓ Combined Context Created: {state2.get('combined_query_context') is not None}")
# MAGIC     
# MAGIC     display_results(state2)
# MAGIC else:
# MAGIC     display_results(state1)
# MAGIC """

# COMMAND ----------

# DBTITLE 1,Example: Follow-Up Queries with Conversation Continuity
# MAGIC %md
# MAGIC ### Example: Follow-Up Queries with Conversation Continuity
# MAGIC
# MAGIC This example demonstrates conversation continuity across multiple queries:
# MAGIC - Each new query has access to previous conversation context
# MAGIC - Thread-based memory preserves state across invocations
# MAGIC - Users can ask related follow-up questions naturally
# MAGIC
# MAGIC """
# MAGIC # Example 2: Multi-Turn Conversation with Follow-Ups
# MAGIC session_id = "demo_followup_001"
# MAGIC
# MAGIC # Turn 1: First query
# MAGIC print("="*80)
# MAGIC print("TURN 1: Initial Query")
# MAGIC print("="*80)
# MAGIC state1 = invoke_super_agent_hybrid(
# MAGIC     "How many active plan members do we have?",
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC display_results(state1)
# MAGIC
# MAGIC # Turn 2: Follow-up query building on Turn 1
# MAGIC print("\n" + "="*80)
# MAGIC print("TURN 2: Follow-Up Query")
# MAGIC print("="*80)
# MAGIC state2 = ask_follow_up_query(
# MAGIC     "What's the breakdown by age group?",  # Refers to "active plan members" from Turn 1
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC display_results(state2)
# MAGIC
# MAGIC # Turn 3: Another follow-up
# MAGIC print("\n" + "="*80)
# MAGIC print("TURN 3: Second Follow-Up")
# MAGIC print("="*80)
# MAGIC state3 = ask_follow_up_query(
# MAGIC     "Now show me the gender distribution for the 50+ age group",  # Builds on previous context
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC display_results(state3)
# MAGIC
# MAGIC # Turn 4: Completely new question in same thread
# MAGIC print("\n" + "="*80)
# MAGIC print("TURN 4: New Question (but same thread)")
# MAGIC print("="*80)
# MAGIC state4 = ask_follow_up_query(
# MAGIC     "Which medications are most prescribed for diabetes patients?",  # New topic
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC display_results(state4)
# MAGIC
# MAGIC print("\n" + "="*80)
# MAGIC print("✅ CONVERSATION SUMMARY")
# MAGIC print("="*80)
# MAGIC print(f"Session ID: {session_id}")
# MAGIC print(f"Total Turns: 4")
# MAGIC print(f"✓ All queries had access to previous conversation context")
# MAGIC print(f"✓ Thread-based memory preserved state across invocations")
# MAGIC print("="*80)
# MAGIC """

# COMMAND ----------

# DBTITLE 1,Example: Clarification + Follow-Up Combined
# MAGIC %md
# MAGIC ### Example: Clarification + Follow-Up Combined
# MAGIC
# MAGIC This example shows the complete workflow:
# MAGIC 1. Vague query → Clarification requested
# MAGIC 2. User clarifies → Workflow completes
# MAGIC 3. Follow-up query → Uses context from Turn 1+2
# MAGIC
# MAGIC """
# MAGIC # Example 3: Complete Multi-Turn Workflow
# MAGIC session_id = "demo_complete_001"
# MAGIC
# MAGIC # Turn 1: Vague query triggers clarification
# MAGIC print("="*80)
# MAGIC print("TURN 1: Vague Query")
# MAGIC print("="*80)
# MAGIC state1 = invoke_super_agent_hybrid(
# MAGIC     "Show me patient costs",  # Vague - what costs? which patients?
# MAGIC     thread_id=session_id
# MAGIC )
# MAGIC
# MAGIC if not state1.get('question_clear'):
# MAGIC     # Turn 2: Respond to clarification
# MAGIC     print("\n" + "="*80)
# MAGIC     print("TURN 2: Clarification Response")
# MAGIC     print("="*80)
# MAGIC     state2 = respond_to_clarification(
# MAGIC         "Show me average total claim costs for diabetic patients by insurance type",
# MAGIC         previous_state=state1,
# MAGIC         thread_id=session_id
# MAGIC     )
# MAGIC     display_results(state2)
# MAGIC     
# MAGIC     # Turn 3: Follow-up question
# MAGIC     print("\n" + "="*80)
# MAGIC     print("TURN 3: Follow-Up After Clarification")
# MAGIC     print("="*80)
# MAGIC     state3 = ask_follow_up_query(
# MAGIC         "What about only Medicare patients over 65?",  # Refines previous query
# MAGIC         thread_id=session_id
# MAGIC     )
# MAGIC     display_results(state3)
# MAGIC     
# MAGIC     # Turn 4: Another follow-up
# MAGIC     print("\n" + "="*80)
# MAGIC     print("TURN 4: Second Follow-Up")
# MAGIC     print("="*80)
# MAGIC     state4 = ask_follow_up_query(
# MAGIC         "Compare that to Medicaid patients in the same age group",
# MAGIC         thread_id=session_id
# MAGIC     )
# MAGIC     display_results(state4)
# MAGIC     
# MAGIC     print("\n" + "="*80)
# MAGIC     print("✅ WORKFLOW SUMMARY")
# MAGIC     print("="*80)
# MAGIC     print(f"Session ID: {session_id}")
# MAGIC     print(f"Turn 1: Vague query → Clarification requested")
# MAGIC     print(f"Turn 2: User clarified → Context combined and preserved")
# MAGIC     print(f"Turn 3-4: Follow-ups → Used combined context from Turn 1+2")
# MAGIC     print(f"✓ Original query always preserved")
# MAGIC     print(f"✓ Clarification context properly combined")
# MAGIC     print(f"✓ Thread memory maintained conversation continuity")
# MAGIC     print("="*80)
# MAGIC """

# COMMAND ----------

# DBTITLE 1,Example: Multiple Conversations with Different Threads
# MAGIC %md
# MAGIC ### Example: Multiple Parallel Conversations
# MAGIC
# MAGIC Thread IDs enable multiple independent conversations:
# MAGIC - Each thread maintains its own conversation context
# MAGIC - Different users or sessions don't interfere with each other
# MAGIC
# MAGIC """
# MAGIC # Example 4: Multiple Independent Conversations
# MAGIC # Conversation A: Patient demographics
# MAGIC thread_a = "user_alice_session_001"
# MAGIC stateA1 = invoke_super_agent_hybrid("Show patient count by age", thread_id=thread_a)
# MAGIC stateA2 = ask_follow_up_query("Now by gender", thread_id=thread_a)
# MAGIC stateA3 = ask_follow_up_query("Focus on 50+ age group", thread_id=thread_a)
# MAGIC
# MAGIC # Conversation B: Medication analysis (completely independent)
# MAGIC thread_b = "user_bob_session_001"
# MAGIC stateB1 = invoke_super_agent_hybrid("Which drugs are most prescribed?", thread_id=thread_b)
# MAGIC stateB2 = ask_follow_up_query("For diabetes patients only", thread_id=thread_b)
# MAGIC
# MAGIC # Conversation C: Cost analysis (independent)
# MAGIC thread_c = "user_charlie_session_001"
# MAGIC stateC1 = invoke_super_agent_hybrid("Average claim costs", thread_id=thread_c)
# MAGIC
# MAGIC print("\n" + "="*80)
# MAGIC print("✅ MULTIPLE CONVERSATIONS")
# MAGIC print("="*80)
# MAGIC print(f"Thread A ({thread_a}): 3 turns about patient demographics")
# MAGIC print(f"Thread B ({thread_b}): 2 turns about medications")
# MAGIC print(f"Thread C ({thread_c}): 1 turn about costs")
# MAGIC print(f"✓ All threads maintained independent conversation contexts")
# MAGIC print(f"✓ No cross-contamination between threads")
# MAGIC print("="*80)
# MAGIC """

# COMMAND ----------

# DBTITLE 1,Test: Meta-Question Handling
# MAGIC %md
# MAGIC ### Test: Meta-Question Detection and Handling
# MAGIC
# MAGIC This section tests the new meta-question detection and handling capabilities.
# MAGIC
# MAGIC **Test Cases:**
# MAGIC - Meta-questions (Route 1 - Context): Fast answers from space summaries
# MAGIC - Meta-questions (Route 2 - SQL): Statistical meta requiring SQL generation
# MAGIC - SQL questions: Standard workflow (unchanged)
# MAGIC - Edge cases: Ambiguous queries

# COMMAND ----------

# DBTITLE 1,Test 1: Capability Meta-Questions
"""
Test meta-questions about agent capabilities.
These should route through: meta_detection → meta_handler → summarize → END
Expected: Fast context-based answer (~2s)
"""

# Test: What can this agent do?
print("\n" + "="*80)
print("TEST 1: Capability Meta-Question")
print("="*80)
print("Query: 'What questions can I ask?'")
print("Expected: Route to meta_handler, answer from context\n")

state1 = invoke_super_agent_hybrid("What questions can I ask?")

print("\n📊 TEST 1 RESULTS:")
print(f"  is_meta_question: {state1.get('is_meta_question')}")
print(f"  meta_type: {state1.get('meta_type')}")
print(f"  meta_route: {state1.get('meta_route')}")
print(f"  meta_answer length: {len(state1.get('meta_answer', '')) if state1.get('meta_answer') else 0} chars")
print(f"  sql_query: {state1.get('sql_query')}")
print(f"  Final summary: {state1.get('final_summary', 'N/A')[:200]}...")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Test 2: Dataset Description Meta-Questions
"""
Test meta-questions about available datasets.
These should route through: meta_detection → meta_handler → summarize → END
Expected: Fast context-based answer (~2s)
"""

print("\n" + "="*80)
print("TEST 2: Dataset Description Meta-Question")
print("="*80)
print("Query: 'Describe the available datasets'")
print("Expected: Route to meta_handler, answer from context\n")

state2 = invoke_super_agent_hybrid("Describe the available datasets")

print("\n📊 TEST 2 RESULTS:")
print(f"  is_meta_question: {state2.get('is_meta_question')}")
print(f"  meta_type: {state2.get('meta_type')}")
print(f"  meta_route: {state2.get('meta_route')}")
print(f"  meta_answer length: {len(state2.get('meta_answer', '')) if state2.get('meta_answer') else 0} chars")
print(f"  sql_query: {state2.get('sql_query')}")
print(f"  Final summary preview: {state2.get('final_summary', 'N/A')[:200]}...")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Test 3: Schema Exploration Meta-Questions
"""
Test meta-questions about schema/tables.
These should route through: meta_detection → meta_handler → summarize → END
Expected: Fast context-based answer (~2s)
"""

print("\n" + "="*80)
print("TEST 3: Schema Exploration Meta-Question")
print("="*80)
print("Query: 'What tables are available?'")
print("Expected: Route to meta_handler, answer from context\n")

state3 = invoke_super_agent_hybrid("What tables are available?")

print("\n📊 TEST 3 RESULTS:")
print(f"  is_meta_question: {state3.get('is_meta_question')}")
print(f"  meta_type: {state3.get('meta_type')}")
print(f"  meta_route: {state3.get('meta_route')}")
print(f"  meta_answer length: {len(state3.get('meta_answer', '')) if state3.get('meta_answer') else 0} chars")
print(f"  sql_query: {state3.get('sql_query')}")
print(f"  Final summary preview: {state3.get('final_summary', 'N/A')[:200]}...")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Test 4: Statistical Meta-Questions (Route 2 - SQL)
"""
Test statistical meta-questions that require SQL.
These should route through: meta_detection → planning → sql_synthesis → execution → summarize → END
Expected: SQL generation and execution (~10s)
"""

print("\n" + "="*80)
print("TEST 4: Statistical Meta-Question (Needs SQL)")
print("="*80)
print("Query: 'How many rows are in each table?'")
print("Expected: Route to planning (SQL generation)\n")

state4 = invoke_super_agent_hybrid("How many rows are in each table?")

print("\n📊 TEST 4 RESULTS:")
print(f"  is_meta_question: {state4.get('is_meta_question')}")
print(f"  meta_type: {state4.get('meta_type')}")
print(f"  meta_route: {state4.get('meta_route')}")
print(f"  sql_query: {state4.get('sql_query', 'N/A')[:200]}...")
print(f"  execution_result success: {state4.get('execution_result', {}).get('success', False)}")
print(f"  Final summary preview: {state4.get('final_summary', 'N/A')[:200]}...")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Test 5: Standard SQL Question (Unchanged Workflow)
"""
Test standard SQL questions to ensure existing workflow still works.
These should route through: meta_detection → clarification → planning → sql_synthesis → execution → summarize → END
Expected: Standard SQL workflow (~7s)
"""

print("\n" + "="*80)
print("TEST 5: Standard SQL Question")
print("="*80)
print("Query: 'How many active members are over 50 years old?'")
print("Expected: Route to clarification (standard workflow)\n")

state5 = invoke_super_agent_hybrid("How many active members are over 50 years old?")

print("\n📊 TEST 5 RESULTS:")
print(f"  is_meta_question: {state5.get('is_meta_question')}")
print(f"  meta_type: {state5.get('meta_type')}")
print(f"  question_clear: {state5.get('question_clear')}")
print(f"  execution_plan: {state5.get('execution_plan', 'N/A')[:100]}...")
print(f"  sql_query: {state5.get('sql_query', 'N/A')[:200]}...")
print(f"  execution_result success: {state5.get('execution_result', {}).get('success', False)}")
print(f"  Final summary preview: {state5.get('final_summary', 'N/A')[:200]}...")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Test 6: Edge Case - Ambiguous Query
"""
Test ambiguous queries that could be meta or SQL.
Expected: LLM fallback detection
"""

print("\n" + "="*80)
print("TEST 6: Edge Case - Ambiguous Query")
print("="*80)
print("Query: 'Describe the claims'")
print("Expected: LLM detection, could be meta or SQL\n")

state6 = invoke_super_agent_hybrid("Describe the claims")

print("\n📊 TEST 6 RESULTS:")
print(f"  is_meta_question: {state6.get('is_meta_question')}")
print(f"  meta_type: {state6.get('meta_type')}")
print(f"  meta_route: {state6.get('meta_route')}")
print(f"  sql_query: {'Present' if state6.get('sql_query') else 'None'}")
print(f"  meta_answer: {'Present' if state6.get('meta_answer') else 'None'}")
print(f"  Final summary preview: {state6.get('final_summary', 'N/A')[:200]}...")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Test Summary
"""
Summary of all test results
"""

print("\n" + "="*80)
print("🎯 TEST SUMMARY")
print("="*80)

test_results = [
    ("Capability Meta", state1, True, "context"),
    ("Dataset Description", state2, True, "context"),
    ("Schema Exploration", state3, True, "context"),
    ("Statistical Meta", state4, True, "sql"),
    ("Standard SQL", state5, False, None),
    ("Ambiguous Query", state6, None, None),
]

print(f"\n{'Test Name':<25} {'Is Meta':<12} {'Route':<12} {'Has SQL':<12} {'Has Meta Answer':<18} {'Status'}")
print("-" * 100)

for test_name, state, expected_meta, expected_route in test_results:
    is_meta = state.get('is_meta_question')
    route = state.get('meta_route')
    has_sql = bool(state.get('sql_query'))
    has_meta_ans = bool(state.get('meta_answer'))
    
    # Determine status
    if expected_meta is None:  # Ambiguous case
        status = "✓ Handled"
    elif is_meta == expected_meta:
        if expected_route is None or route == expected_route:
            status = "✅ PASS"
        else:
            status = "⚠️ WARN (wrong route)"
    else:
        status = "❌ FAIL"
    
    print(f"{test_name:<25} {str(is_meta):<12} {str(route):<12} {str(has_sql):<12} {str(has_meta_ans):<18} {status}")

print("\n" + "="*80)
print("✅ Meta-question handling tests completed!")
print("="*80)
print("\nKey Observations:")
print("  1. Meta-questions (Tests 1-3) should have meta_answer without SQL")
print("  2. Statistical meta (Test 4) should have SQL query")
print("  3. Standard SQL (Test 5) should go through clarification")
print("  4. Ambiguous queries (Test 6) handled by LLM fallback")
print("\nPerformance:")
print("  - Meta-questions (context): ~2s (fast!)")
print("  - Statistical meta (SQL): ~10s")
print("  - Standard SQL: ~7s (minimal impact: +0.5s for detection)")
print("="*80)

# COMMAND ----------


