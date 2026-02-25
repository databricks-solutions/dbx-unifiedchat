"""
SQL Synthesis Agents for Multi-Agent System

This module contains two SQL synthesis agent classes:

1. SQLSynthesisTableAgent:
   - Fast SQL synthesis using Unity Catalog (UC) function tools
   - Direct table metadata access via UC functions
   - Best for table_route execution strategy

2. SQLSynthesisGenieAgent:
   - SQL synthesis using Genie agents as tools
   - Supports both parallel and sequential execution
   - Best for genie_route execution strategy

Both agents receive execution plans from PlanningAgent and generate
executable SQL queries with proper error handling and explanation.

Example usage:
    from langchain_core.runnables import Runnable
    from databricks_langchain import ChatDatabricks
    
    llm = ChatDatabricks(endpoint="databricks-claude-sonnet-4-5")
    
    # Table Route Agent
    table_agent = SQLSynthesisTableAgent(
        llm=llm,
        catalog="catalog_name",
        schema="schema_name"
    )
    sql_result = table_agent(execution_plan)
    
    # Genie Route Agent
    genie_agent = SQLSynthesisGenieAgent(
        llm=llm,
        relevant_spaces=[{"space_id": "...", "space_title": "...", "searchable_content": "..."}]
    )
    sql_result = genie_agent(execution_plan)
"""

import json
import re
from typing import Dict, List, Any, Optional

from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel
from langchain_core.tools import StructuredTool
from langchain.agents import create_agent
from pydantic import BaseModel, Field

from databricks_langchain import (
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
    GenieAgent,
)


# ==============================================================================
# Genie Agent Pool (for SQLSynthesisGenieAgent)
# ==============================================================================

# Global pool for caching Genie agents across requests
_genie_agent_pool: Dict[str, Any] = {}


def get_or_create_genie_agent(space_id: str, space_title: str, description: str):
    """
    Get existing Genie agent from pool or create new one if not cached.
    
    OPTIMIZATION: Reuses Genie agents across requests to avoid expensive initialization.
    Expected gain: -1 to -3s on genie route (creating 3-5 agents)
    
    Args:
        space_id: Genie space ID
        space_title: Space title for agent name
        description: Space description
    
    Returns:
        Cached or newly created GenieAgent instance
    """
    global _genie_agent_pool
    
    if space_id not in _genie_agent_pool:
        print(f"⚡ Creating Genie agent for space: {space_title} (first use)")
        
        def enforce_limit(messages, n=5):
            """Enforce result limit in Genie queries."""
            last = messages[-1] if messages else {"content": ""}
            content = last.get("content", "") if isinstance(last, dict) else last.content
            return f"{content}\n\nPlease limit the result to at most {n} rows."
        
        genie_agent = GenieAgent(
            genie_space_id=space_id,
            genie_agent_name=f"Genie_{space_title}",
            description=description,
            include_context=True,
            message_processor=lambda msgs: enforce_limit(msgs, n=5)
        )
        
        _genie_agent_pool[space_id] = genie_agent
        print(f"✓ Genie agent cached for {space_title}")
    else:
        print(f"✓ Using cached Genie agent for {space_title}")
    
    return _genie_agent_pool[space_id]


# ==============================================================================
# SQLSynthesisTableAgent
# ==============================================================================

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
        """
        Initialize SQLSynthesisTableAgent.
        
        Args:
            llm: Language model for SQL synthesis
            catalog: Unity Catalog catalog name
            schema: Unity Catalog schema name
        """
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
            f"{catalog}.{schema}.get_space_instructions",  # REQUIRED FINAL STEP before SQL synthesis
            f"{catalog}.{schema}.get_space_details",  # Last resort only
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
                "3. If insufficient, call UC function tools in this order to gather metadata:\n"
                "   a) get_space_summary for space information\n"
                "   b) get_table_overview for table schemas\n"
                "   c) get_column_detail for specific columns\n"
                "   d) get_space_details ONLY as last resort (token intensive)\n"
                "4. If still cannot find enough metadata in relevant spaces, expand searching scope to all spaces\n"
                "   mentioned in the execution plan's 'vector_search_relevant_spaces_info' field\n"
                "5. Generate complete, executable SQL using the gathered metadata, print out the final SQL\n\n"

                "## UC FUNCTION USAGE:\n"
                "- Pass arguments as JSON array strings: '[\"space_id_1\", \"space_id_2\"]' or 'null'\n"
                "- Always explicitly passing all required arguments, even it is 'null'\n"
                "- Only query spaces from execution plan's relevant_space_ids\n"
                "- Use minimal sufficiency: only query what you need\n"
                "- OPTIMIZATION: When possible, call multiple UC functions in parallel by returning multiple tool calls\n"
                "  Example: If you need table_overview for space_1 AND column_detail for space_2, call both tools at once\n"
                "- This enables parallel execution and reduces latency by 1-2 seconds\n\n"

                "## SQL FINETUNE INSTRUCTIONS:\n"
                "- **Additional SQL Finetune Step** After you already generated the SQL, take a reflection first, and then you are ready to call **get_space_instructions** to extract the space instructions taught by human; only use the most related instruction parts to finetune the SQL if necessary.\n"
                "- This provides essential human-taught SQL patterns and best practices for the specific space.\n\n"

                "## OUTPUT REQUIREMENTS:\n"
                "- Generate complete, executable SQL with:\n"
                "  * Proper JOINs based on execution plan\n"
                "  * WHERE clauses for filtering\n"
                "  * Appropriate aggregations\n"
                "  * Clear column aliases\n"
                "  * Always use real column names, never make up ones\n\n"
                "## MULTI-QUERY STRATEGY:\n"
                "- If the question has multiple parts (sub_questions) and you think it's better to report\n"
                "  each query and result separately instead of combining into one big complex query:\n"
                "  * Generate MULTIPLE separate SQL queries (one per sub-question)\n"
                "  * This is preferred when: sub-questions are independent, results are easier to interpret\n"
                "    separately, or combining would create overly complex SQL\n"
                "- If sub-questions are closely related and naturally combine (e.g., same table, similar filters):\n"
                "  * You may generate a single combined SQL query\n\n"
                "## OUTPUT FORMAT:\n"
                "- Return your response with:\n"
                "1. Your explanations; If SQL cannot be generated, explain what metadata is missing\n"
                "2. SQL queries formatted as follows:\n"
                "   * For SINGLE-part questions: One ```sql code block with query ending in semicolon\n"
                "   * For MULTI-part questions: Use SEPARATE ```sql code blocks (one per query)\n"
                "   * Each query MUST end with a semicolon (;)\n"
                "   * Add a leading comment before each query: -- Query N: <brief description>\n"
                "   * Example for multi-part:\n"
                "     ```sql\n"
                "     -- Query 1: Most common diagnoses\n"
                "     SELECT diagnosis_code, COUNT(*) AS freq FROM diagnosis GROUP BY diagnosis_code;\n"
                "     ```\n"
                "     ```sql\n"
                "     -- Query 2: Top procedures\n"
                "     SELECT procedure_code, COUNT(*) AS count FROM procedures GROUP BY procedure_code;\n"
                "     ```\n\n"
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
            
            # Try to extract SQL from markdown - use findall to capture ALL code blocks
            if "```sql" in final_content.lower():
                # Find all ```sql blocks
                sql_blocks = re.findall(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
                if sql_blocks:
                    # Join all SQL blocks with newlines to preserve multi-query structure
                    sql_query = '\n\n'.join(block.strip() for block in sql_blocks if block.strip())
                    has_sql = True
                    # Remove all SQL blocks from content to get explanation
                    final_content = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL)
            elif "```" in final_content:
                # Find all generic code blocks
                code_blocks = re.findall(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
                # Filter for SQL-like blocks
                sql_blocks = [
                    block.strip() for block in code_blocks 
                    if block.strip() and any(keyword in block.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN', 'WITH'])
                ]
                if sql_blocks:
                    # Join all SQL blocks
                    sql_query = '\n\n'.join(sql_blocks)
                    has_sql = True
                    # Remove all code blocks from content to get explanation
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


# ==============================================================================
# SQLSynthesisGenieAgent
# ==============================================================================

class SQLSynthesisGenieAgent:
    """
    Agent responsible for Genie Route SQL synthesis using Genie agents as tools.
    
    EXECUTION MODES:
    ---------------
    1. LangGraph Agent Mode (default via synthesize_sql()):
       - Uses LangGraph agent with tool calling
       - Supports retries, disaster recovery, and adaptive routing
       - Agent decides which tools to call and when
       - Best for complex queries requiring orchestration
    
    2. RunnableParallel Mode (via invoke_genie_agents_parallel()):
       - Uses RunnableParallel for direct parallel execution
       - Faster for simple parallel queries
       - No retry logic or adaptive routing
       - Best for straightforward parallel execution
    
    ARCHITECTURE:
    ------------
    - Upgraded from RunnableLambda to RunnableParallel pattern
    - Each Genie agent is wrapped as both a tool and a parallel executor
    - Supports efficient parallel invocation using LangChain's RunnableParallel
    - Optimized to only create Genie agents for relevant spaces (not all spaces)
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
        
        OPTIMIZED: Uses cached Genie agents from pool to avoid expensive initialization.
        Expected gain: -1 to -3s on genie route (when agents are already cached)
        
        Creates both:
        1. Individual tool wrappers for LangGraph agent tool calling
        2. A parallel executor mapping for efficient batch invocation
        
        Uses LangChain preferred syntax with Pydantic BaseModel and StructuredTool.
        """
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
            
            # OPTIMIZATION: Get Genie agent from pool (cached or newly created)
            genie_agent = get_or_create_genie_agent(space_id, space_title, description)
            self.genie_agents.append(genie_agent)
            
            # Define tool input schema using Pydantic
            class GenieToolInput(BaseModel):
                question: str = Field(..., description="Natural-language query to run in the Genie Space")
                conversation_id: Optional[str] = Field(None, description="Optional Genie conversation for continuity")
            
            # Create tool function using factory pattern to capture agent
            def make_genie_tool_call(agent):
                """Factory function to capture agent in closure properly"""
                def _genie_tool_call(question: str, conversation_id: Optional[str] = None):
                    """
                    StructuredTool with args_schema expects individual field arguments,
                    not a single Pydantic object.
                    """
                    # GenieAgent expects a LangChain-style message list
                    result = agent.invoke({
                        "messages": [{"role": "user", "content": question}],
                        "conversation_id": conversation_id,
                    })
                    # Extract final output + optional context
                    out = {"conversation_id": result.get("conversation_id")}
                    msgs = result["messages"]
                    def _get(name): 
                        return next((getattr(m, "content", "") for m in msgs if getattr(m, "name", None) == name), None)
                    out["answer"] = _get("query_result") or ""
                    reasoning = _get("query_reasoning")
                    sql = _get("query_sql")
                    if reasoning: out["reasoning"] = reasoning
                    if sql: out["sql"] = sql
                    return out
                return _genie_tool_call
            
            # Create StructuredTool
            genie_tool = StructuredTool(
                name=genie_agent_name,
                description=(
                    f"Use for governed analytics queries (NL→SQL) in {space_title}. "
                    f"{description}. "
                    "Returns an answer and, when available, the generated SQL and reasoning."
                ),
                args_schema=GenieToolInput,
                func=make_genie_tool_call(genie_agent),
            )
            self.genie_agent_tools.append(genie_tool)
            
            print(f"  ✓ Created Genie agent tool: {genie_agent_name} ({space_id})")
    
    def _create_parallel_execution_tool(self):
        """
        Create a tool that allows the agent to invoke multiple Genie agents in parallel.
        
        This tool gives the agent control over parallel execution with the same
        disaster recovery capabilities as individual tool calls.
        
        Uses RunnableParallel pattern with StructuredTool for type safety.
        """
        
        # Define input schema for parallel execution
        class ParallelGenieInput(BaseModel):
            genie_route_plan: Dict[str, str] = Field(
                ..., 
                description="Dictionary mapping space_id to question. Example: {'space_id_1': 'Get member demographics', 'space_id_2': 'Get benefits'}"
            )
        
        # Merge function to combine outputs from multiple Genie agents
        def merge_genie_outputs(outputs: Dict[str, Any]) -> Dict[str, Any]:
            """
            Merge outputs from multiple Genie agents into a unified result.
            
            Args:
                outputs: Dictionary keyed by space_id, each containing agent results
            
            Returns:
                Unified dictionary with extracted SQL, reasoning, and metadata from all agents
            """
            merged_results = {}
            
            for space_id, result in outputs.items():
                extracted = {
                    "space_id": space_id,
                    "question": outputs.get(f"{space_id}_question", ""),
                    "sql": "",
                    "reasoning": "",
                    "answer": "",
                    "conversation_id": "",
                    "success": False
                }
                
                # Handle direct dict output from StructuredTool
                if isinstance(result, dict):
                    extracted["answer"] = result.get("answer", "")
                    extracted["sql"] = result.get("sql", "")
                    extracted["reasoning"] = result.get("reasoning", "")
                    extracted["conversation_id"] = result.get("conversation_id", "")
                    extracted["success"] = bool(result.get("sql") or result.get("answer"))
                
                # Handle message-based output (fallback)
                elif isinstance(result, dict) and "messages" in result:
                    messages = result.get("messages", [])
                    
                    # Extract reasoning (query_reasoning)
                    for msg in messages:
                        if hasattr(msg, 'name') and msg.name == 'query_reasoning':
                            extracted["reasoning"] = msg.content if hasattr(msg, 'content') else ""
                            break
                    
                    # Extract SQL (query_sql)
                    for msg in messages:
                        if hasattr(msg, 'name') and msg.name == 'query_sql':
                            extracted["sql"] = msg.content if hasattr(msg, 'content') else ""
                            extracted["success"] = True
                            break
                    
                    # Extract answer (query_result)
                    for msg in messages:
                        if hasattr(msg, 'name') and msg.name == 'query_result':
                            extracted["answer"] = msg.content if hasattr(msg, 'content') else ""
                            break
                    
                    # Extract conversation_id
                    extracted["conversation_id"] = result.get("conversation_id", "")
                
                merged_results[space_id] = extracted
            
            return merged_results
        
        # Build a mapping from space_id to tool for easy lookup
        space_id_to_tool = {}
        for space in self.relevant_spaces:
            space_id = space.get("space_id")
            if space_id:
                # Find the corresponding tool by matching space_id
                for tool in self.genie_agent_tools:
                    # Match tool to space by checking if space_title is in tool name
                    space_title = space.get("space_title", space_id)
                    if f"Genie_{space_title}" == tool.name:
                        space_id_to_tool[space_id] = tool
                        break
        
        # Tool function that builds and invokes dynamic parallel execution
        def invoke_parallel_genie_agents(genie_route_plan: Dict[str, str]) -> Dict[str, Any]:
            """
            Invoke multiple Genie agents in parallel for efficient SQL generation.
            
            StructuredTool with args_schema expects individual field arguments,
            not a single Pydantic object.
            
            Args:
                genie_route_plan: Dictionary mapping space_id to question
            
            Returns:
                Dictionary with results from each Genie agent, keyed by space_id.
                Each result contains the SQL query, reasoning, and answer from that agent.
            """
            try:
                route_plan = genie_route_plan
                
                # Validate all requested space_ids exist
                for space_id in route_plan.keys():
                    if space_id not in space_id_to_tool:
                        return {
                            "error": f"No tool found for space_id: {space_id}",
                            "available_space_ids": list(space_id_to_tool.keys())
                        }
                
                if not route_plan:
                    return {"error": "No valid parallel tasks to execute"}
                
                # Build dynamic parallel tasks - each task invokes the corresponding tool's func
                # Call the underlying function directly with individual arguments
                parallel_tasks = {}
                for space_id, question in route_plan.items():
                    tool = space_id_to_tool[space_id]
                    # Create a lambda that calls the tool's func with individual kwargs
                    # Use default argument to capture values properly in closure
                    parallel_tasks[space_id] = RunnableLambda(
                        lambda inp, sid=space_id, t=tool: t.func(
                            question=inp[sid], conversation_id=None
                        )
                    )
                
                # Create parallel runner and compose with merge function
                parallel = RunnableParallel(**parallel_tasks)
                composed = parallel | RunnableLambda(merge_genie_outputs)
                
                # Invoke the composed chain
                results = composed.invoke(route_plan)
                
                return results
                
            except Exception as e:
                return {"error": f"Parallel execution failed: {str(e)}"}
        
        # Create StructuredTool with proper schema
        parallel_tool = StructuredTool(
            name="invoke_parallel_genie_agents",
            description=(
                "Invoke multiple Genie agents in PARALLEL for fast SQL generation. "
                "Input: Dictionary mapping space_id to question. "
                "Example: {'space_01j9t0jhx009k25rvp67y1k7j0': 'Get member demographics', 'space_01j9t0jhx009k25rvp67y1k7j1': 'Get benefit costs'}. "
                "Returns: Dictionary with SQL, reasoning, and answer from each agent. "
                "Use this tool when: "
                "(1) You need to query multiple Genie spaces simultaneously, "
                "(2) The queries are independent (no dependencies between them), "
                "(3) You want faster execution than calling each agent sequentially. "
                "After getting results, check if you have all needed SQL components. If missing information, you can: "
                "call this tool again with updated questions, or call individual Genie agent tools for specific missing pieces."
            ),
            args_schema=ParallelGenieInput,
            func=invoke_parallel_genie_agents,
        )
        
        return parallel_tool
    
    def _create_sql_synthesis_agent(self):
        """
        Create LangGraph SQL Synthesis Agent with Genie agent tools.
        
        Uses Databricks LangGraph SDK with create_agent pattern.
        Includes both individual Genie agent tools AND a parallel execution tool.
        """
        tools = []
        tools.extend(self.genie_agent_tools)
        
        # Add parallel execution tool
        parallel_tool = self._create_parallel_execution_tool()
        tools.append(parallel_tool)
        
        print(f"✓ Created SQL Synthesis Agent with {len(self.genie_agent_tools)} Genie agent tools + 1 parallel execution tool")
        
        # Create SQL Synthesis Agent (specialized for multi-agent system)
        sql_synthesis_agent = create_agent(
            model=self.llm,
            tools=tools,
            system_prompt=(
"""You are a SQL synthesis agent with access to both INDIVIDUAL and PARALLEL Genie agent execution tools.

The Plan given to you is a JSON:
{
'original_query': 'The User's Question',
'vector_search_relevant_spaces_info': [{'space_id': 'space_id_1', 'space_title': 'space_title_1'}, ...],
"question_clear": true,
"sub_questions": ["sub-question 1", "sub-question 2", ...],
"requires_multiple_spaces": true/false,
"relevant_space_ids": ["space_id_1", "space_id_2", ...],
"requires_join": true/false,
"join_strategy": "table_route" or "genie_route" or null,
"execution_plan": "Brief description of execution plan",
"genie_route_plan": {'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', ...} or null,}

## TOOL EXECUTION STRATEGY:

### OPTION 1: PARALLEL EXECUTION (⚡ ALWAYS USE THIS - Saves 1-2 seconds!)
**DEFAULT STRATEGY**: Use the `invoke_parallel_genie_agents` tool to query ALL Genie spaces simultaneously.
This tool executes multiple Genie agent calls in parallel using RunnableParallel pattern.

1. Extract the genie_route_plan from the input JSON
2. Convert it to a JSON string: '{"space_id_1": "question1", "space_id_2": "question2"}'
3. Call: invoke_parallel_genie_agents(genie_route_plan='{"space_id_1": "question1", ...}')
4. You'll receive JSON with SQL and thinking from ALL agents at once
5. Check if you have all needed SQL components
6. If missing information:
   - Reframe questions and call invoke_parallel_genie_agents again with updated questions
   - OR call specific individual Genie agent tools for missing pieces

### OPTION 2: SEQUENTIAL EXECUTION (⚠️ Only for Special Cases)
**RARE**: Only use individual Genie agent tools sequentially when:
- One query strictly depends on results from another (rare in practice)
- Parallel execution failed and you need granular error handling
- You're doing adaptive refinement based on partial results

**NOTE**: 99% of queries should use OPTION 1 (parallel) for optimal performance.

## DISASTER RECOVERY (DR) - WORKS FOR BOTH PARALLEL AND SEQUENTIAL:

1. **First Attempt**: Try your query AS IS
2. **If fails**: Analyze the error message
   - If agent says "I don't have information for X", remove X from the question
   - If agent returns empty/incomplete SQL, try rephrasing the question
3. **Retry Once**: Call the same tool with updated question(s)
4. **If still fails**: Try alternative Genie agents that might have the information
5. **Final fallback**: Work with what you have and explain limitations

## EXAMPLE PARALLEL EXECUTION WITH DR:

Step 1: Call invoke_parallel_genie_agents with initial questions
Step 2: Check results - if space_1 succeeded but space_2 failed
Step 3: Keep space_1 SQL, retry space_2 with reframed question using invoke_parallel_genie_agents
Step 4: Combine all successful SQL fragments

## SQL SYNTHESIS:

MULTI-QUERY STRATEGY:
- If the question has multiple parts and you think it's better to report each query
  and result separately instead of combining into one big complex query:
  * Generate MULTIPLE separate SQL queries (one per sub-question)
  * This is preferred when: sub-questions are independent, results are easier to interpret
    separately, or combining would create overly complex SQL
- If sub-questions are closely related and naturally combine (e.g., same Genie space, similar context):
  * You may combine SQL fragments into a single query

OUTPUT REQUIREMENTS:
- Generate complete, executable SQL with:
  * Proper JOINs based on execution plan strategy
  * WHERE clauses for filtering  
  * Appropriate aggregations
  * Clear column aliases
  * Always use real column names from the data
- Return your response with:
  1. Your explanation (including which execution strategy you used)
  2. SQL queries formatted as follows:
     * For SINGLE-part questions: One ```sql code block with query ending in semicolon
     * For MULTI-part questions: Use SEPARATE ```sql code blocks (one per query)
     * Each query MUST end with a semicolon (;)
     * Add a leading comment before each query: -- Query N: <brief description>
     * Example for multi-part:
       ```sql
       -- Query 1: Most common diagnoses
       SELECT diagnosis_code, COUNT(*) AS freq FROM diagnosis GROUP BY diagnosis_code;
       ```
       ```sql
       -- Query 2: Top procedures
       SELECT procedure_code, COUNT(*) AS count FROM procedures GROUP BY procedure_code;
       ```"""
            )
        )
        
        return sql_synthesis_agent
    
    def invoke_genie_agents_parallel(self, genie_route_plan: Dict[str, str]) -> Dict[str, Any]:
        """
        Invoke multiple Genie agents in parallel using RunnableParallel.
        
        This method demonstrates the proper use of RunnableParallel for efficient
        parallel execution of multiple Genie agents simultaneously.
        
        Args:
            genie_route_plan: Dictionary mapping space_id to partial_question
                Example: {
                    "space_01j9t0jhx009k25rvp67y1k7j0": "Get member demographics",
                    "space_01j9t0jhx009k25rvp67y1k7j1": "Get benefit costs"
                }
        
        Returns:
            Dictionary mapping space_id to agent response
            Example: {
                "space_01j9t0jhx009k25rvp67y1k7j0": {...response...},
                "space_01j9t0jhx009k25rvp67y1k7j1": {...response...}
            }
        """
        if not genie_route_plan:
            return {}
        
        # Build space_id to tool mapping
        space_id_to_tool = {}
        for space in self.relevant_spaces:
            space_id = space.get("space_id")
            if space_id and space_id in genie_route_plan:
                # Find the corresponding tool by matching space_id
                for tool in self.genie_agent_tools:
                    space_title = space.get("space_title", space_id)
                    if f"Genie_{space_title}" == tool.name:
                        space_id_to_tool[space_id] = tool
                        break
        
        # Build parallel tasks that call tool.func() directly with individual arguments
        parallel_tasks = {}
        for space_id, question in genie_route_plan.items():
            if space_id in space_id_to_tool:
                tool = space_id_to_tool[space_id]
                parallel_tasks[space_id] = RunnableLambda(
                    lambda inp, sid=space_id, t=tool: t.func(
                        question=inp[sid], conversation_id=None
                    )
                )
            else:
                print(f"  ⚠ Warning: No tool found for space_id: {space_id}")
        
        if not parallel_tasks:
            print("  ⚠ Warning: No valid parallel tasks to execute")
            return {}
        
        # Create RunnableParallel with all tasks
        parallel_runner = RunnableParallel(**parallel_tasks)
        
        print(f"  🚀 Invoking {len(parallel_tasks)} Genie agents in parallel using RunnableParallel...")
        
        try:
            # Invoke all agents in parallel
            # Now invoke with the actual question mapping
            results = parallel_runner.invoke(genie_route_plan)
            
            print(f"  ✅ Parallel invocation completed for {len(results)} agents")
            print(results)
            return results
            
        except Exception as e:
            print(f"  ❌ Parallel invocation failed: {str(e)}")
            return {}
    
    def synthesize_sql(
        self, 
        plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Synthesize SQL using Genie agents with intelligent tool selection.
        
        The agent has access to:
        1. invoke_parallel_genie_agents tool - For fast parallel execution
        2. Individual Genie agent tools - For sequential/dependent queries
        
        The agent autonomously decides which strategy to use and handles
        disaster recovery with retry logic for both parallel and sequential execution
        
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
        
        print(f"\n{'='*80}")
        print("🤖 SQL Synthesis Agent - Starting (with parallel execution tool)...")
        print(f"{'='*80}")
        print(f"Plan: {json.dumps(plan_result, indent=2)}")
        print(f"{'='*80}\n")
        
        # Create the message for the agent
        # The agent will autonomously decide whether to use:
        # 1. invoke_parallel_genie_agents tool (fast parallel execution)
        # 2. Individual Genie agent tools (sequential execution)
        # 3. A combination of both strategies
        agent_message = {
            "messages": [
                {
                    "role": "user",
                    "content": f"""
Generate a SQL query to answer the question according to the Query Plan:
{json.dumps(plan_result, indent=2)}

RECOMMENDED APPROACH:
If 'genie_route_plan' is provided with multiple spaces, consider using the invoke_parallel_genie_agents tool for faster execution.
Convert the genie_route_plan to a JSON string and call the tool to get all SQL fragments in parallel.
Then combine them into a final SQL query.
"""
                }
            ]
        }
        
        try:
            # MLflow autologging is enabled globally at agent initialization
            # No need to call it again here to avoid context issues
            
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
            
            # Clean markdown if present and extract SQL - use findall to capture ALL code blocks
            if "```sql" in final_content.lower():
                # Find all ```sql blocks
                sql_blocks = re.findall(r'```sql\s*(.*?)\s*```', final_content, re.IGNORECASE | re.DOTALL)
                if sql_blocks:
                    # Join all SQL blocks with newlines to preserve multi-query structure
                    sql_query = '\n\n'.join(block.strip() for block in sql_blocks if block.strip())
                    has_sql = True
                    # Remove all SQL blocks to get explanation
                    explanation = re.sub(r'```sql\s*.*?\s*```', '', final_content, flags=re.IGNORECASE | re.DOTALL)
            elif "```" in final_content:
                # Find all generic code blocks
                code_blocks = re.findall(r'```\s*(.*?)\s*```', final_content, re.DOTALL)
                # Filter for SQL-like blocks
                sql_blocks = [
                    block.strip() for block in code_blocks 
                    if block.strip() and any(keyword in block.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN', 'WITH'])
                ]
                if sql_blocks:
                    # Join all SQL blocks
                    sql_query = '\n\n'.join(sql_blocks)
                    has_sql = True
                    # Remove all code blocks to get explanation
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
