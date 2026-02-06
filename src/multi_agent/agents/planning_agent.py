"""
Planning Agent for Multi-Agent System

This module contains the PlanningAgent class responsible for:
- Query analysis and execution planning
- Vector search for relevant Genie spaces
- Creating execution plans with join strategies
- Determining execution routes (table_route vs genie_route)

The PlanningAgent uses vector search to identify relevant Genie spaces and
creates detailed execution plans that guide subsequent SQL synthesis agents.

Example usage:
    from langchain_core.runnables import Runnable
    from databricks_langchain import ChatDatabricks
    
    llm = ChatDatabricks(endpoint="databricks-claude-sonnet-4-5")
    agent = PlanningAgent(
        llm=llm,
        vector_search_index="catalog.schema.index_name"
    )
    
    plan = agent("How many active plan members over 50 are on Lexapro?")
"""

import json
import re
from typing import Dict, List, Any, Optional

from langchain_core.runnables import Runnable
from databricks_langchain import VectorSearchRetrieverTool


class PlanningAgent:
    """
    Agent responsible for query analysis and execution planning.
    
    OOP design with vector search integration.
    """
    
    def __init__(self, llm: Runnable, vector_search_index: str):
        """
        Initialize PlanningAgent.
        
        Args:
            llm: Language model for planning (must support streaming)
            vector_search_index: Name of the vector search index to use
        """
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
            List of relevant space dictionaries with keys:
            - space_id: Genie space identifier
            - space_title: Human-readable space title
            - searchable_content: Space description/content
            - score: Relevance score from vector search
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
        relevant_spaces: List[Dict[str, Any]],
        original_query: str = None
    ) -> Dict[str, Any]:
        """
        Create execution plan based on query and relevant spaces.
        
        Args:
            query: User's question (may be context_summary if available)
            relevant_spaces: List of relevant Genie spaces from vector search
            original_query: Original user query from this turn (before context enrichment)
            
        Returns:
            Dictionary with execution plan containing:
            - original_query: Original user query
            - vector_search_relevant_spaces_info: Mapping of space_id to space_title
            - question_clear: Boolean indicating if question is clear
            - sub_questions: List of sub-questions identified
            - requires_multiple_spaces: Boolean indicating if multiple spaces needed
            - relevant_space_ids: List of space IDs needed for execution
            - requires_join: Boolean indicating if data join is needed
            - join_strategy: "table_route" or "genie_route"
            - execution_plan: Brief description of execution plan
            - genie_route_plan: Dictionary mapping space_id to partial question (if genie_route)
        """
        # Use original_query if provided, otherwise use query as original
        original_query_display = original_query if original_query is not None else query
        
        planning_prompt = f"""
You are a query planning expert. Analyze the following question and create an execution plan.

User original query this turn: {original_query_display}

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
        
        # Stream LLM response for immediate first token emission
        print("🤖 Streaming planning LLM call...")
        content = ""
        for chunk in self.llm.stream(planning_prompt):
            if chunk.content:
                content += chunk.content
        
        content = content.strip()
        print(f"✓ Planning stream complete ({len(content)} chars)")
        
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
        
        This method combines vector search and execution plan creation.
        
        Args:
            query: User's question
            
        Returns:
            Complete execution plan with relevant spaces
        """
        # Search for relevant spaces
        relevant_spaces = self.search_relevant_spaces(query)
        
        # Create execution plan
        plan = self.create_execution_plan(query, relevant_spaces)
        
        return plan
