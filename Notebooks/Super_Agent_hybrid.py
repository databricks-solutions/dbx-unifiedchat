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
3. SQL Synthesis Agent (Fast Route) - Generates SQL using UC tools (OOP class)
4. SQL Synthesis Agent (Slow Route) - Generates SQL using Genie agents (OOP class)
5. SQL Execution Agent - Executes SQL and returns results (OOP class)

The Super Agent uses LangGraph with explicit state tracking for orchestration.
"""

import json
from typing import Dict, List, Optional, Any, Annotated, Literal, Generator
from typing_extensions import TypedDict
import operator
from uuid import uuid4
import re

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

# DBTITLE 1,Helper Function - Query Delta Table
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

# Load space summaries for context
space_summary_df = query_delta_table(
    table_name=TABLE_NAME,
    filter_field="chunk_type",
    filter_value="space_summary",
    select_fields=["space_id", "space_title", "searchable_content"]
)

# Convert to context dictionary
space_summary_list = space_summary_df.collect()
context = {}
for row in space_summary_list:
    space_id = row["space_id"]
    context[space_id] = row["searchable_content"]

print(f"✓ Loaded {len(context)} Genie spaces for context")

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
    
    # Planning
    sub_questions: Optional[List[str]]
    requires_multiple_spaces: Optional[bool]
    relevant_space_ids: Optional[List[str]]
    relevant_spaces: Optional[List[Dict[str, Any]]]
    vector_search_relevant_spaces_info: Optional[List[Dict[str, str]]]
    requires_join: Optional[bool]
    join_strategy: Optional[str]  # "fast_route" or "slow_route"
    execution_plan: Optional[str]
    genie_route_plan: Optional[Dict[str, str]]
    
    # SQL Synthesis
    sql_query: Optional[str]
    sql_synthesis_explanation: Optional[str]  # Agent's explanation/reasoning
    synthesis_error: Optional[str]
    
    # Execution
    execution_result: Optional[Dict[str, Any]]
    execution_error: Optional[str]
    
    # Summary
    final_summary: Optional[str]  # Natural language summary of the workflow execution
    
    # Control flow
    next_agent: Optional[str]
    messages: Annotated[List, operator.add]
    
print("✓ Agent State defined with explicit fields for observability")

# COMMAND ----------

# DBTITLE 1,Agent Class 1: Clarification Agent (OOP)
class ClarificationAgent:
    """
    Agent responsible for checking query clarity.
    
    OOP design for modularity and reusability.
    """
    
    def __init__(self, llm: Runnable, context: Dict[str, str]):
        self.llm = llm
        self.context = context
        self.name = "Clarification"
    
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
    - "fast_route": Directly synthesize SQL across multiple tables
    - "slow_route": Query each Genie Space Agent separately, then combine SQL queries
    - If user explicitly asks for "slow_route", use it; otherwise, use "fast_route"
5. Execution plan: A brief description of how to execute the plan.
    - For slow_route: Return {{'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2'}}
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
    "join_strategy": "fast_route" or "slow_route" or null,
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

# DBTITLE 1,Agent Class 3a: SQL Synthesis Agent - Fast Route (OOP)
class SQLSynthesisFastAgent:
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
        self.name = "SQLSynthesisFast"
        
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
                "- Return ONLY the SQL query without explanations or markdown\n"
                "- If SQL cannot be generated, explain what metadata is missing"
            ),
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
        # Prepare plan summary for agent
        plan_summary = {
            "original_query": plan.get("original_query", ""),
            "vector_search_relevant_spaces_info": plan.get("vector_search_relevant_spaces_info", []),
            "relevant_space_ids": plan.get("relevant_space_ids", []),
            "execution_plan": plan.get("execution_plan", ""),
            "requires_join": plan.get("requires_join", False),
            "sub_questions": plan.get("sub_questions", [])
        }
        
        # Invoke agent
        agent_message = {
            "messages": [
                {
                    "role": "user",
                    "content": f"""
Generate a SQL query to answer the question according to the Query Plan:
{json.dumps(plan_summary, indent=2)}

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

print("✓ SQLSynthesisFastAgent class defined")

# COMMAND ----------

# DBTITLE 1,Agent Class 3b: SQL Synthesis Agent - Slow Route (OOP)

class SQLSynthesisSlowAgent:
    """
    Agent responsible for slow SQL synthesis using Genie agents.
    
    OOP design with Genie agent integration.
    """
    
    def __init__(self, llm: Runnable, space_summary_df):
        self.llm = llm
        self.space_summary_df = space_summary_df
        self.name = "SQLSynthesisSlow"
        
        # Create Genie agents
        self.genie_agents_dict = self._create_genie_agents()
    
    def _create_genie_agents(self) -> Dict[str, GenieAgent]:
        """Create Genie agents for each space."""
        def enforce_limit(messages, n=10):
            last = messages[-1] if messages else {"content": ""}
            content = last.get("content", "") if isinstance(last, dict) else last.content
            return f"{content}\n\nPlease limit the result to at most {n} rows."
        
        genie_agents = {}
        for row in self.space_summary_df.collect():
            space_id = row["space_id"]
            space_title = row["space_title"]
            searchable_content = row["searchable_content"]
            
            genie_agent = GenieAgent(
                genie_space_id=space_id,
                genie_agent_name=f"Genie_{space_title}",
                description=searchable_content,
                include_context=True,
                message_processor=lambda msgs: enforce_limit(msgs, n=10)
            )
            genie_agents[space_id] = genie_agent
        
        return genie_agents
    
    def query_genie_agents(self, genie_route_plan: Dict[str, str]) -> Dict[str, Dict[str, str]]:
        """
        Query Genie agents with partial questions.
        
        Args:
            genie_route_plan: Mapping of space_id to partial question
            
        Returns:
            Dictionary of space_id to {question, thinking, sql}
        """
        sql_fragments = {}
        
        for space_id, partial_question in genie_route_plan.items():
            if space_id not in self.genie_agents_dict:
                print(f"⚠ Warning: Space {space_id} not found in Genie agents")
                continue
            
            try:
                genie_agent = self.genie_agents_dict[space_id]
                resp = genie_agent.invoke({
                    "messages": [{"role": "user", "content": partial_question}]
                })
                
                # Extract thinking (reasoning) and SQL from response
                thinking = None
                sql = None
                
                for msg in resp["messages"]:
                    if isinstance(msg, AIMessage):
                        if msg.name == "query_reasoning":
                            thinking = msg.content
                        elif msg.name == "query_sql":
                            sql = msg.content
                
                if sql:
                    sql_fragments[space_id] = {
                        "question": partial_question,
                        "thinking": thinking if thinking else "No reasoning provided",
                        "sql": sql
                    }
                    print(f"  ✓ Got SQL from space {space_id}")
                    if thinking:
                        print(f"    Reasoning: {thinking[:100]}...")
                else:
                    print(f"  ⚠ No SQL returned from space {space_id}")
                    
            except Exception as e:
                print(f"❌ Error querying space {space_id}: {e}")
        
        return sql_fragments
    
    def combine_sql_fragments(
        self, 
        original_query: str,
        execution_plan: str,
        sql_fragments: Dict[str, Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Combine SQL fragments into a single query.
        
        Args:
            original_query: Original user question
            execution_plan: Execution plan description
            sql_fragments: SQL fragments from Genie agents
            
        Returns:
            Dictionary with:
            - sql: str - Combined SQL query (None if cannot generate)
            - explanation: str - Agent's explanation/reasoning
            - has_sql: bool - Whether SQL was successfully extracted
        """
        combine_prompt = f"""
You are an expert SQL developer. Combine the following SQL fragments into a single executable SQL query.

Original Question: {original_query}

Execution Plan: {execution_plan}

SQL Fragments from Genie Agents:
{json.dumps(sql_fragments, indent=2)}

Generate a complete SQL query that:
1. Combines these fragments with proper JOINs
2. Answers the original question
3. Uses real table and column names from the fragments
4. Includes proper WHERE clauses and aggregations

Return ONLY the final SQL query, no explanations or markdown.
"""
        
        response = self.llm.invoke(combine_prompt)
        original_content = response.content.strip()
        combined_sql = original_content
        
        sql_query = None
        has_sql = False
        
        # Clean markdown if present and extract SQL
        if "```sql" in combined_sql.lower():
            sql_match = re.search(r'```sql\s*(.*?)\s*```', combined_sql, re.IGNORECASE | re.DOTALL)
            if sql_match:
                sql_query = sql_match.group(1).strip()
                has_sql = True
                # Remove SQL block to get explanation
                combined_sql = re.sub(r'```sql\s*.*?\s*```', '', combined_sql, flags=re.IGNORECASE | re.DOTALL)
        elif "```" in combined_sql:
            sql_match = re.search(r'```\s*(.*?)\s*```', combined_sql, re.DOTALL)
            if sql_match:
                potential_sql = sql_match.group(1).strip()
                if any(keyword in potential_sql.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                    sql_query = potential_sql
                    has_sql = True
                    # Remove SQL block to get explanation
                    combined_sql = re.sub(r'```\s*.*?\s*```', '', combined_sql, flags=re.DOTALL)
        else:
            # No markdown, check if the entire content is SQL
            if any(keyword in combined_sql.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN']):
                sql_query = combined_sql
                has_sql = True
                combined_sql = "SQL query combined from Genie agent fragments."
        
        explanation = combined_sql.strip()
        if not explanation:
            explanation = original_content if not has_sql else "SQL query combined successfully from Genie agent fragments."
        
        return {
            "sql": sql_query,
            "explanation": explanation,
            "has_sql": has_sql
        }
    
    def synthesize_sql(
        self, 
        original_query: str,
        execution_plan: str,
        genie_route_plan: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Synthesize SQL using Genie agents (slow route).
        
        Args:
            original_query: Original user question
            execution_plan: Execution plan description
            genie_route_plan: Mapping of space_id to partial question
            
        Returns:
            Dictionary with:
            - sql: str - Combined SQL query (None if cannot generate)
            - explanation: str - Agent's explanation/reasoning
            - has_sql: bool - Whether SQL was successfully extracted
        """
        # Query Genie agents
        sql_fragments = self.query_genie_agents(genie_route_plan)
        
        if not sql_fragments:
            return {
                "sql": None,
                "explanation": "No SQL fragments collected from Genie agents. Unable to generate query.",
                "has_sql": False
            }
        
        # Combine SQL fragments
        result = self.combine_sql_fragments(
            original_query,
            execution_plan,
            sql_fragments
        )
        
        return result
    
    def __call__(
        self, 
        original_query: str,
        execution_plan: str,
        genie_route_plan: Dict[str, str]
    ) -> Dict[str, Any]:
        """Make agent callable."""
        return self.synthesize_sql(original_query, execution_plan, genie_route_plan)

print("✓ SQLSynthesisSlowAgent class defined")

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

# DBTITLE 1,Result Summarize Agent (OOP Design)
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
            
            # Add SQL synthesis info
            if sql_query:
                prompt += f"""**SQL Generation:** ✅ Successful
**SQL Query:** 
```sql
{sql_query[:300]}{'...' if len(sql_query) > 300 else ''}
```

"""
                if sql_explanation:
                    prompt += f"""**SQL Synthesis Explanation:** {sql_explanation[:200]}{'...' if len(sql_explanation) > 200 else ''}

"""
                
                # Add execution info
                if exec_result.get('success'):
                    row_count = exec_result.get('row_count', 0)
                    columns = exec_result.get('columns', [])
                    prompt += f"""**Execution:** ✅ Successful
**Results:** {row_count} rows returned
**Columns:** {', '.join(columns[:5])}{'...' if len(columns) > 5 else ''}

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
**Task:** Generate a 2-3 sentence summary in natural language that:
1. Describes what the user asked for
2. Explains what the system did (planning, SQL generation, execution)
3. States the outcome (success with X rows, error, needs clarification, etc.)

Keep it concise and user-friendly. Do not include code or technical jargon unless necessary.
Return ONLY the summary text, no explanations or meta-commentary.
"""
        
        return prompt
    
    def __call__(self, state: AgentState) -> str:
        """Make agent callable."""
        return self.generate_summary(state)

print("✓ ResultSummarizeAgent class defined")

# COMMAND ----------

# DBTITLE 1,Node Wrappers (Combining OOP Agents with Explicit State)
def clarification_node(state: AgentState) -> AgentState:
    """
    Clarification node wrapping ClarificationAgent class.
    Combines OOP modularity with explicit state management.
    
    Handles up to 1 clarification request. If user provides clarification,
    incorporates it and proceeds to planning.
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
        # Append user's clarification to the original query
        original = state["original_query"]
        state["original_query"] = f"{original} [User Clarification: {user_response}]"
        state["question_clear"] = True
        state["next_agent"] = "planning"
        
        state["messages"].append(
            SystemMessage(content=f"User clarification incorporated: {user_response}")
        )
        
        return state
    
    query = state["original_query"]
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_CLARIFICATION)
    
    # Use OOP agent with clarification count
    clarification_agent = ClarificationAgent(llm, context)
    clarity_result = clarification_agent(query, clarification_count)
    
    # Update explicit state
    state["question_clear"] = clarity_result.get("question_clear", True)
    state["clarification_needed"] = clarity_result.get("clarification_needed")
    state["clarification_options"] = clarity_result.get("clarification_options")
    
    if state["question_clear"]:
        print("✓ Query is clear - proceeding to planning")
        state["next_agent"] = "planning"
    else:
        print("⚠ Query needs clarification (attempt 1 of 1)")
        print(f"   Reason: {state['clarification_needed']}")
        if state["clarification_options"]:
            print("   Options:")
            for i, opt in enumerate(state["clarification_options"], 1):
                print(f"     {i}. {opt}")
        
        # Increment clarification count
        state["clarification_count"] = clarification_count + 1
        
        # Route to summarize to show clarification request
        state["next_agent"] = "summarize"
        
        # Add message prompting user for clarification
        clarification_message = (
            f"I need clarification: {state['clarification_needed']}\n\n"
            f"Please choose one of the following options or provide your own clarification:\n"
        )
        if state["clarification_options"]:
            for i, opt in enumerate(state["clarification_options"], 1):
                clarification_message += f"{i}. {opt}\n"
        
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
    """
    print("\n" + "="*80)
    print("📋 PLANNING AGENT")
    print("="*80)
    
    query = state["original_query"]
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_PLANNING)
    
    # Use OOP agent
    planning_agent = PlanningAgent(llm, VECTOR_SEARCH_INDEX)
    plan = planning_agent(query)
    
    # Update explicit state
    state["sub_questions"] = plan.get("sub_questions", [])
    state["requires_multiple_spaces"] = plan.get("requires_multiple_spaces", False)
    state["relevant_space_ids"] = plan.get("relevant_space_ids", [])
    state["requires_join"] = plan.get("requires_join", False)
    state["join_strategy"] = plan.get("join_strategy")
    state["execution_plan"] = plan.get("execution_plan", "")
    state["genie_route_plan"] = plan.get("genie_route_plan")
    state["vector_search_relevant_spaces_info"] = plan.get("vector_search_relevant_spaces_info", [])
    # Note: relevant_spaces with searchable_content removed to save tokens
    # Only store space_id and space_title in vector_search_relevant_spaces_info
    # state["vector_search_relevant_spaces_info"] = [
    #     {"space_id": sp["space_id"], "space_title": sp["space_title"]}
    #     for sp in plan.get("relevant_spaces", [])
    # ]
    
    # Determine next agent
    if state["join_strategy"] == "slow_route":
        print("✓ Plan complete - using SLOW ROUTE (Genie agents)")
        state["next_agent"] = "sql_synthesis_slow"
    else:
        print("✓ Plan complete - using FAST ROUTE (direct SQL synthesis)")
        state["next_agent"] = "sql_synthesis_fast"
    
    state["messages"].append(
        SystemMessage(content=f"Execution plan: {json.dumps(plan, indent=2)}")
    )
    
    return state


def sql_synthesis_fast_node(state: AgentState) -> AgentState:
    """
    Fast SQL synthesis node wrapping SQLSynthesisFastAgent class.
    Combines OOP modularity with explicit state management.
    """
    print("\n" + "="*80)
    print("⚡ SQL SYNTHESIS AGENT - FAST ROUTE")
    print("="*80)
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    
    # Use OOP agent
    sql_agent = SQLSynthesisFastAgent(llm, CATALOG, SCHEMA)
    
    # Prepare plan for agent
    plan = {
        "original_query": state["original_query"],
        "vector_search_relevant_spaces_info": state.get("vector_search_relevant_spaces_info", []),
        "relevant_space_ids": state.get("relevant_space_ids", []),
        "execution_plan": state.get("execution_plan", ""),
        "requires_join": state.get("requires_join", False),
        "sub_questions": state.get("sub_questions", [])
    }
    
    try:
        print("🤖 Invoking SQL synthesis agent...")
        result = sql_agent(plan)
        
        # Extract SQL and explanation
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        state["sql_synthesis_explanation"] = explanation
        
        if has_sql and sql_query and explanation:
            state["sql_query"] = sql_query
            state["has_sql"] = has_sql
            state["next_agent"] = "sql_execution"
            print("✓ SQL query synthesized successfully")
            print(f"SQL Preview: {sql_query[:200]}...")
            if explanation:
                print(f"Agent Explanation: {explanation[:200]}...")
            
            # Add message with SQL synthesis explanation
            state["messages"].append(
                AIMessage(content=f"SQL Synthesis (Fast Route):\n{explanation}")
            )
        else:
            print("⚠ No SQL generated - agent explanation:")
            print(f"  {explanation}")
            state["synthesis_error"] = "Cannot generate SQL query"
            state["next_agent"] = "summarize"
            
            # Add message with explanation even if no SQL
            state["messages"].append(
                AIMessage(content=f"SQL Synthesis Failed (Fast Route):\n{explanation}")
            )
        
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        state["synthesis_error"] = str(e)
        state["sql_synthesis_explanation"] = str(e)
        state["next_agent"] = "end"
        state["messages"].append(
                AIMessage(content=f"SQL Synthesis Failed (Fast Route):\n{state["sql_synthesis_explanation"]}")
            )
    
    return state


def sql_synthesis_slow_node(state: AgentState) -> AgentState:
    """
    Slow SQL synthesis node wrapping SQLSynthesisSlowAgent class.
    Combines OOP modularity with explicit state management.
    """
    print("\n" + "="*80)
    print("🐢 SQL SYNTHESIS AGENT - SLOW ROUTE")
    print("="*80)
    
    llm = ChatDatabricks(endpoint=LLM_ENDPOINT_SQL_SYNTHESIS, temperature=0.1)
    
    # Use OOP agent
    sql_agent = SQLSynthesisSlowAgent(llm, space_summary_df)
    
    genie_route_plan = state.get("genie_route_plan", {})
    
    if not genie_route_plan:
        print("❌ No genie_route_plan found in state")
        state["synthesis_error"] = "No routing plan available for slow route"
        state["next_agent"] = "end"
        return state
    
    try:
        print(f"🤖 Querying {len(genie_route_plan)} Genie agents...")
        result = sql_agent(
            state["original_query"],
            state.get("execution_plan", ""),
            genie_route_plan
        )
        
        # Extract SQL and explanation
        sql_query = result.get("sql")
        explanation = result.get("explanation", "")
        has_sql = result.get("has_sql", False)
        
        state["sql_synthesis_explanation"] = explanation
        
        # Update explicit state
        if has_sql and sql_query and explanation:
            state["sql_query"] = sql_query
            state["next_agent"] = "sql_execution"
            state["has_sql"] = has_sql
            print("✓ SQL fragments combined successfully")
            print(f"SQL Preview: {sql_query[:200]}...")
            if explanation:
                print(f"Agent Explanation: {explanation[:200]}...")
            
            # Add message with SQL synthesis explanation
            state["messages"].append(
                AIMessage(content=f"SQL Synthesis (Slow Route):\n{explanation}")
            )
        else:
            print("⚠ No SQL generated - agent explanation:")
            print(f"  {explanation}")
            state["synthesis_error"] = "Cannot generate SQL query from Genie agent fragments"
            state["next_agent"] = "summarize"
            
            # Add message with explanation even if no SQL
            state["messages"].append(
                AIMessage(content=f"SQL Synthesis Failed (Slow Route):\n{explanation}")
            )
        
    except Exception as e:
        print(f"❌ SQL synthesis failed: {e}")
        state["synthesis_error"] = str(e)
        state["sql_synthesis_explanation"] = str(e)
        state["next_agent"] = "end"
        state["messages"].append(
                AIMessage(content=f"SQL Synthesis Failed (Slow Route):\n{state["sql_synthesis_explanation"]}")
            )
    
    return state


def sql_execution_node(state: AgentState) -> AgentState:
    """
    SQL execution node wrapping SQLExecutionAgent class.
    Combines OOP modularity with explicit state management.
    """
    print("\n" + "="*80)
    print("🚀 SQL EXECUTION AGENT")
    print("="*80)
    
    sql_query = state.get("sql_query")
    
    if not sql_query:
        print("❌ No SQL query to execute")
        state["execution_error"] = "No SQL query provided"
        state["next_agent"] = "end"
        return state
    
    # Use OOP agent
    execution_agent = SQLExecutionAgent()
    result = execution_agent(sql_query)
    
    if result["success"]:
        print(f"✓ Query executed successfully!")
        print(f"📊 Rows returned: {result['row_count']}")
        print(f"📋 Columns: {', '.join(result['columns'])}")
        
        state["messages"].append(
            SystemMessage(content=f"Execution successful: {result['row_count']} rows returned")
        )
    else:
        print(f"❌ SQL execution failed: {result.get('error', 'Unknown error')}")
        state["execution_error"] = result.get("error")
        
        state["messages"].append(
            SystemMessage(content=f"Execution failed: {result.get('error')}")
        )
    
    state["execution_result"] = result
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
    
    state["next_agent"] = "end"
    
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
    workflow.add_node("clarification", clarification_node)
    workflow.add_node("planning", planning_node)
    workflow.add_node("sql_synthesis_fast", sql_synthesis_fast_node)
    workflow.add_node("sql_synthesis_slow", sql_synthesis_slow_node)
    workflow.add_node("sql_execution", sql_execution_node)
    workflow.add_node("summarize", summarize_node)  # Final summarization node
    
    # Define routing logic based on explicit state
    def route_after_clarification(state: AgentState) -> str:
        if state.get("question_clear", False):
            return "planning"
        return "summarize"  # Summarize if clarification needed
    
    def route_after_planning(state: AgentState) -> str:
        next_agent = state.get("next_agent", "summarize")
        if next_agent == "sql_synthesis_fast":
            return "sql_synthesis_fast"
        elif next_agent == "sql_synthesis_slow":
            return "sql_synthesis_slow"
        return "summarize"
    
    def route_after_synthesis(state: AgentState) -> str:
        next_agent = state.get("next_agent", "summarize")
        if next_agent == "sql_execution":
            return "sql_execution"
        return "summarize"  # Summarize if synthesis error
    
    # Add edges with conditional routing
    workflow.set_entry_point("clarification")
    
    workflow.add_conditional_edges(
        "clarification",
        route_after_clarification,
        {
            "planning": "planning",
            "summarize": "summarize"
        }
    )
    
    workflow.add_conditional_edges(
        "planning",
        route_after_planning,
        {
            "sql_synthesis_fast": "sql_synthesis_fast",
            "sql_synthesis_slow": "sql_synthesis_slow",
            "summarize": "summarize"
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_fast",
        route_after_synthesis,
        {
            "sql_execution": "sql_execution",
            "summarize": "summarize"
        }
    )
    
    workflow.add_conditional_edges(
        "sql_synthesis_slow",
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
    print("  1. Clarification Agent (OOP)")
    print("  2. Planning Agent (OOP)")
    print("  3. SQL Synthesis Agent - Fast Route (OOP)")
    print("  4. SQL Synthesis Agent - Slow Route (OOP)")
    print("  5. SQL Execution Agent (OOP)")
    print("  6. Result Summarize Agent (OOP) - FINAL NODE")
    print("\n✓ Explicit state management enabled")
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
        
        Args:
            request: The request containing input messages
            
        Yields:
            ResponsesAgentStreamEvent for each step in the workflow
        """
        # Convert request input to chat completions format
        cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
        
        # Initialize state with messages
        initial_state = {
            "original_query": cc_msgs[-1]["content"] if cc_msgs else "",
            "question_clear": False,
            "messages": [HumanMessage(content=msg["content"]) for msg in cc_msgs if msg["role"] == "user"],
            "next_agent": "clarification"
        }
        
        # Configure with thread ID
        thread_id = request.custom_inputs.get("thread_id", "default") if request.custom_inputs else "default"
        config = {"configurable": {"thread_id": thread_id}}
        
        first_message = True
        seen_ids = set()
        
        # Stream the workflow execution
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
    print("="*80)
    
    # Create new state with user's clarification response
    new_state = {
        "original_query": previous_state["original_query"],
        "question_clear": False,
        "clarification_count": previous_state.get("clarification_count", 1),
        "user_clarification_response": clarification_response,
        "messages": [
            HumanMessage(content=previous_state["original_query"]),
            HumanMessage(content=f"Clarification: {clarification_response}")
        ],
        "next_agent": "clarification"  # Re-enter clarification node to process response
    }
    
    # Configure with thread (must match previous call)
    config = {"configurable": {"thread_id": thread_id}}
    
    # Enable MLflow tracing
    mlflow.langchain.autolog()
    
    # Continue the workflow
    final_state = super_agent_hybrid.invoke(new_state, config)
    
    print("\n" + "="*80)
    print("✅ WORKFLOW COMPLETE AFTER CLARIFICATION")
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

# Example test query
test_query = "What is the average cost of medical claims per claim in 2024?"

# Invoke Hybrid Super Agent
final_state = invoke_super_agent_hybrid(test_query, thread_id="test_hybrid_001")

# COMMAND ----------

final_state

# COMMAND ----------

# DBTITLE 1,Test Hybrid Super Agent (quick route)
# Display results
display_results(final_state)

# COMMAND ----------

# DBTITLE 1,Test Hybrid Super Agent (slow route)
# Example test query
test_query = "What is the average cost of medical claims  per claim in 2024? use slow route"

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

# DBTITLE 1,Test Case 2: Multi-Space Query with JOIN (Fast Route)
test_query_2 = "What is the average cost of medical claims for patients diagnosed with diabetes?"
result_2 = invoke_super_agent_hybrid(test_query_2, thread_id="test_hybrid_fast")
display_results(result_2)

# COMMAND ----------

# DBTITLE 1,Test Case 3: Multi-Space Query with JOIN (Slow Route - Explicit)
test_query_3 = "What is the average cost of medical claims for patients diagnosed with diabetes? Use slow route"
result_3 = invoke_super_agent_hybrid(test_query_3, thread_id="test_hybrid_slow")
display_results(result_3)

# COMMAND ----------

# DBTITLE 1,Test Case 4: Complex Multi-Space Query
test_query_4 = "What is the average cost of medical claims for patients diagnosed with diabetes, broken down by insurance payer type and patient age group?"
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
# MAGIC - SQLSynthesisFastAgent
# MAGIC - SQLSynthesisSlowAgent
# MAGIC - SQLExecutionAgent
# MAGIC
# MAGIC Node Wrappers:
# MAGIC - clarification_node(state) → calls ClarificationAgent, updates state
# MAGIC - planning_node(state) → calls PlanningAgent, updates state
# MAGIC - sql_synthesis_fast_node(state) → calls SQLSynthesisFastAgent, updates state
# MAGIC - sql_synthesis_slow_node(state) → calls SQLSynthesisSlowAgent, updates state
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


