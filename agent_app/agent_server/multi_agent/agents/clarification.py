"""
Unified Intent, Context, and Clarification Agent

This module provides the unified clarification agent that combines:
- Intent detection (new_question, refinement, continuation, clarification_response)
- Context summary generation
- Clarity assessment with rate limiting
- Meta-question detection and direct answering

The agent uses streaming LLM calls with hybrid output format for immediate user feedback.
TODO: 1. separate agent class and agent node like all other agents; now it is crammed here.
      2. many tasks, consider skill this.
"""

import json
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from databricks_langchain import ChatDatabricks

from ..core.state import (
    AgentState,
    ConversationTurn,
    IntentMetadata,
    create_conversation_turn,
    create_clarification_request,
)


# ==============================================================================
# Configuration and Caching
# ==============================================================================

# Space context cache (TTL-based)
_space_context_cache = {"data": None, "timestamp": None, "table_name": None}
_SPACE_CONTEXT_CACHE_TTL = timedelta(minutes=30)

# LLM connection pool (reuses connections across requests)
_llm_connection_pool: Dict[str, ChatDatabricks] = {}

# Performance metrics storage
_performance_metrics = {
    "node_timings": {},
    "cache_stats": {
        "space_context_hits": 0,
        "space_context_misses": 0,
        "llm_pool_hits": 0,
        "llm_pool_misses": 0
    },
    "agent_model_usage": {}
}


# ==============================================================================
# Helper Functions
# ==============================================================================

def _load_space_context_uncached(table_name: str) -> Dict[str, str]:
    """
    Internal function: Load space context from Delta table without caching.
    
    Args:
        table_name: Full table name (catalog.schema.table)
        
    Returns:
        Dictionary mapping space_id to searchable_content
    """
    try:
        from databricks.connect import DatabricksSession
        # You must add .serverless() or .clusterId("your-cluster-id") here
        spark = DatabricksSession.builder.serverless().getOrCreate()
    except ImportError:
        # This only runs inside Databricks where databricks-connect isn't installed
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
    
    df = spark.sql(f"""
        SELECT space_id, searchable_content
        FROM {table_name}
        WHERE chunk_type = 'space_summary'
    """)
    
    context = {row["space_id"]: row["searchable_content"] 
               for row in df.collect()}
    
    return context


def load_space_context(table_name: str) -> Dict[str, str]:
    """
    Load space context from Delta table with TTL-based caching.
    
    OPTIMIZATION: Caches results for 30 minutes to avoid repeated Spark queries.
    Expected gain: -1 to -2s per request (when cache is hot)
    
    Args:
        table_name: Full table name (catalog.schema.table)
        
    Returns:
        Dictionary mapping space_id to searchable_content
    """
    global _space_context_cache
    
    now = datetime.now()
    
    # Check if cache is valid
    cache_valid = (
        _space_context_cache["data"] is not None and
        _space_context_cache["timestamp"] is not None and
        _space_context_cache["table_name"] == table_name and
        now - _space_context_cache["timestamp"] < _SPACE_CONTEXT_CACHE_TTL
    )
    
    if cache_valid:
        record_cache_hit("space_context")
        cache_age_seconds = (now - _space_context_cache["timestamp"]).total_seconds()
        print(f"✓ Using cached space context ({len(_space_context_cache['data'])} spaces, age: {cache_age_seconds:.1f}s)")
        return _space_context_cache["data"]
    else:
        # Cache miss - load from database
        record_cache_miss("space_context")
        print(f"⚡ Loading space context from database (cache {'expired' if _space_context_cache['data'] else 'empty'})...")
        context = _load_space_context_uncached(table_name)
        
        # Update cache
        _space_context_cache["data"] = context
        _space_context_cache["timestamp"] = now
        _space_context_cache["table_name"] = table_name
        
        print(f"✓ Loaded {len(context)} Genie spaces and cached for {_SPACE_CONTEXT_CACHE_TTL.total_seconds()/60:.0f} minutes")
        return context


def get_pooled_llm(endpoint_name: str, temperature: float = 0.1, max_tokens: Optional[int] = None) -> ChatDatabricks:
    """
    Get or create a pooled LLM connection.
    Reuses connections across requests to avoid connection overhead.
    Expected gain: -500ms cumulative across multiple LLM calls.
    
    Args:
        endpoint_name: Name of the LLM endpoint
        temperature: Temperature for generation (default 0.1)
        max_tokens: Maximum tokens to generate (default None)
    
    Returns:
        ChatDatabricks instance from pool
    """
    
    # Create a cache key that includes temperature and max_tokens
    cache_key = f"{endpoint_name}_{temperature}_{max_tokens}"
    
    if cache_key not in _llm_connection_pool:
        record_cache_miss("llm_pool")
        print(f"⚡ Creating pooled LLM connection: {endpoint_name} (temperature={temperature})")
        kwargs = {"endpoint": endpoint_name, "temperature": temperature}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        _llm_connection_pool[cache_key] = ChatDatabricks(**kwargs)
        print(f"✓ LLM connection pooled: {cache_key}")
    else:
        record_cache_hit("llm_pool")
        print(f"♻️ Reusing pooled LLM connection: {cache_key} (-50ms to -200ms)")
    
    return _llm_connection_pool[cache_key]


def track_agent_model_usage(agent_name: str, model_endpoint: str):
    """
    Track which LLM model is used by each agent for monitoring and cost analysis.
    
    Args:
        agent_name: Name of the agent (e.g., "clarification", "planning")
        model_endpoint: LLM endpoint being used (e.g., "databricks-claude-haiku-4-5")
    """
    if "agent_model_usage" not in _performance_metrics:
        _performance_metrics["agent_model_usage"] = {}
    
    if agent_name not in _performance_metrics["agent_model_usage"]:
        _performance_metrics["agent_model_usage"][agent_name] = {
            "model": model_endpoint,
            "invocations": 0
        }
    
    _performance_metrics["agent_model_usage"][agent_name]["invocations"] += 1
    print(f"📊 Agent '{agent_name}' using model: {model_endpoint}")


def record_cache_hit(cache_type: str):
    """Record a cache hit for monitoring."""
    key = f"{cache_type}_hits"
    if key in _performance_metrics["cache_stats"]:
        _performance_metrics["cache_stats"][key] += 1


def record_cache_miss(cache_type: str):
    """Record a cache miss for monitoring."""
    key = f"{cache_type}_misses"
    if key in _performance_metrics["cache_stats"]:
        _performance_metrics["cache_stats"][key] += 1


def check_clarification_rate_limit(turn_history: List[ConversationTurn], window_size: int = 5) -> bool:
    """
    Check if clarification was triggered in the last N turns (sliding window).
    OPTIMIZED: Fast-path checks for common cases.
    
    Args:
        turn_history: List of conversation turns
        window_size: Number of recent turns to check (default: 5)
    
    Returns:
        True if rate limited (skip clarification), False if ok to clarify
    """
    # PHASE 3 OPTIMIZATION: Fast-path for empty history
    if not turn_history:
        return False  # No history = no rate limit
    
    # PHASE 3 OPTIMIZATION: Fast-path check most recent turn first (most likely)
    if turn_history[-1].get("triggered_clarification", False):
        return True  # Rate limited (most recent turn had clarification)
    
    # PHASE 3 OPTIMIZATION: Fast-path for short history
    if len(turn_history) < 2:
        return False  # Only 1 turn and it doesn't have clarification
    
    # Look at remaining recent turns (skip last one, already checked)
    recent_turns = turn_history[max(0, len(turn_history) - window_size):-1]
    
    # Check remaining turns
    for turn in recent_turns:
        if turn.get("triggered_clarification", False):
            return True  # Rate limited
    
    return False  # OK to clarify


# ==============================================================================
# Main Agent Function
# ==============================================================================

def unified_intent_context_clarification_node(
    state: AgentState,
    llm_endpoint: Optional[str] = None,
    table_name: Optional[str] = None
) -> dict:
    """
    Unified node that combines intent detection, context generation, and clarity check.
    
    Uses STREAMING LLM call with HYBRID OUTPUT FORMAT for immediate user feedback:
    - For meta-questions: Markdown answer streamed FIRST, then JSON metadata parsed
    - For clarifications: Markdown request streamed FIRST, then JSON metadata parsed
    - For regular queries: JSON ONLY (no markdown streaming needed)
    
    Single LLM call for:
    1. Intent classification (new_question, refinement, continuation, clarification_response)
    2. Context summary generation
    3. Clarity assessment with rate limiting (max 1 per 5 turns)
    4. Meta-question detection and direct answering
    
    Streaming behavior:
    - Markdown content is streamed to UI as LLM generates it (better TTFT)
    - JSON metadata is parsed after streaming completes for routing decisions
    
    Args:
        state: Current agent state
        llm_endpoint: LLM endpoint name for clarification agent (optional, uses config if not provided)
        table_name: Full table name for space context loading (optional, uses config if not provided)
    
    Returns:
        Dictionary with state updates
    """
    from langgraph.config import get_stream_writer
    
    # Get configuration if not provided
    if llm_endpoint is None or table_name is None:
        try:
            from ..core.config import get_config
            config = get_config()
            if llm_endpoint is None:
                llm_endpoint = config.llm.clarification_endpoint
            if table_name is None:
                table_name = config.source_table_fq
        except ImportError:
            raise ValueError(
                "llm_endpoint and table_name must be provided if config module is not available"
            )
    
    writer = get_stream_writer()
    
    def stream_markdown_response(content: str, label: str = "Response"):
        """
        DEPRECATED: For local/notebook testing only.
        In production, use writer() events instead for UI display.
        This function only prints to console/logs, not to model serving UI.
        """
        print(f"\n✨ {label}:")
        print("-" * 80)
        
        # Print content immediately without character-by-character delay
        print(content)
        
        print("-" * 80)
    
    def format_clarification_markdown(reason: str, options: list = None) -> str:
        """
        Format clarification reason and options as professional markdown.
        
        Args:
            reason: The clarification reason text
            options: List of clarification options
            
        Returns:
            Formatted markdown string
        """
        # Start with heading and reason
        markdown = f"### Clarification Needed\n\n{reason}\n\n"
        
        # Add options if provided
        if options and len(options) > 0:
            markdown += "**Please choose from the following options:**\n\n"
            for i, option in enumerate(options, 1):
                markdown += f"{i}. {option}\n\n"
        
        return markdown.strip()
    
    def format_meta_answer_markdown(answer: str) -> str:
        """
        Format meta-answer as professional markdown if not already formatted.
        
        Args:
            answer: The meta answer text
            
        Returns:
            Formatted markdown string
        """
        # Check if already formatted (has markdown headings)
        if answer.startswith("#") or "**" in answer:
            return answer  # Already formatted
        
        # Add basic formatting
        markdown = f"## Available Capabilities\n\n{answer}"
        return markdown
    
    print("\n" + "="*80)
    print("🎯 UNIFIED INTENT, CONTEXT & CLARIFICATION AGENT")
    print("="*80)
    
    # Get current query from messages
    messages = state.get("messages", [])
    turn_history = state.get("turn_history", [])
    
    human_messages = [m for m in messages if isinstance(m, HumanMessage)]
    current_query = human_messages[-1].content if human_messages else ""
    
    writer({"type": "agent_start", "agent": "unified_intent_context_clarification", "query": current_query})
    
    print(f"Query: {current_query}")
    print(f"Turn history: {len(turn_history)} turns")
    
    # Analyze query with full LLM (intent + context + clarity + meta-question detection)
    print("🔄 Analyzing query with LLM (intent + context + clarity + meta-question detection)")
    
    # Format conversation context
    conversation_context = ""
    if turn_history:
        conversation_context = "Previous conversation:\n"
        for i, turn in enumerate(turn_history[-5:], 1):  # Last 5 turns
            intent_label = turn['intent_type'].replace('_', ' ').title()
            conversation_context += f"{i}. [{intent_label}] {turn['query']}\n"
            if turn.get('context_summary'):
                conversation_context += f"   Context: {turn['context_summary']}...\n"
    else:
        conversation_context = "No previous conversation (first query)."
    
    # Load space context for clarity check
    space_context = load_space_context(table_name)
    
    # Single unified prompt for intent + context + clarity + meta-question detection
    unified_prompt = f"""Analyze the user's query in the context of the conversation history.

Current Query: {current_query}

Conversation History:
{conversation_context}

Available Data Sources:
{json.dumps(space_context, indent=2)}

## Task 0: Detect Irrelevant Questions (NEW)
FIRST, determine if this is an IRRELEVANT question completely unrelated to data analytics:
- Greetings, small talk, casual conversation (e.g., "Hello", "How are you?", "What's up?")
- Questions about weather, sports, politics, entertainment, current events
- Personal questions about the AI/system itself (e.g., "Who created you?", "Are you sentient?")
- Jokes, riddles, creative writing requests, role-playing
- Questions about topics outside of data analysis and business intelligence
- Programming help, homework, recipes, travel advice, etc.

Examples of irrelevant questions (all should set is_irrelevant=true):
- "What's the weather like today?"
- "Tell me a joke"
- "Who won the Super Bowl?"
- "How do I make pasta?"
- "What are your thoughts on politics?"
- "I want to buy some milk."
- "Trim me a haircut."

If it's irrelevant, you MUST:
1. Set "is_irrelevant": true
2. Provide a polite refusal explaining you're a data analytics assistant
3. Redirect the user to ask questions about the available data sources

NOTE: If a question mentions data but in an irrelevant context (e.g., "What's the weather like in my data?"), treat it as irrelevant.

## Task 1: Detect Meta-Questions
Next, determine if this is a META-QUESTION about the system itself:
- Questions about available tables, data sources, spaces, schemas
- Questions about system capabilities, what data is available
- Questions about the structure or organization of data
- Questions asking for EXAMPLE QUERIES or SAMPLE QUESTIONS they can ask (e.g., "give me 10 example questions I can ask", "what kinds of questions can I ask?", "show me sample questions", "what can I query?")

Examples of meta-questions (all should set is_meta_question=true):
- "What tables are available?"
- "Give me 10 example questions I can ask"
- "What kinds of questions can I ask this system?"
- "Show me sample queries I can run"
- "What data do you have access to?"
- "What can I query here?"

If it's a meta-question, you MUST:
1. Set "is_meta_question": true
2. Generate a direct answer using the Available Data Sources above
3. Provide a clear, informative response about what's available

## Task 2: Classify Intent
Classify the query into ONE of these categories:
1. **new_question**: A completely different topic/domain from previous queries
2. **refinement**: Narrowing/filtering/modifying the previous query on same topic
3. **continuation**: Follow-up exploring same topic from different angle
4. **clarification_response**: User is providing the clarification response to the clarification request

## Task 3: Generate Context Summary
Create a 2-3 sentence summary that:
- Synthesizes the conversation history
- States clearly what the user wants
- Is actionable for SQL query planning

## Task 4: Check Clarity
Determine if the query is clear enough to generate SQL:
- Is the question clear and answerable as-is? (BE LENIENT - default to TRUE)
- ONLY mark as unclear if CRITICAL information is missing
- If unclear, provide 2-3 specific clarification options
- Never mark as unclear if the question is a clarification response to a previous clarification request
- Meta-questions should always be marked as clear

## OUTPUT FORMAT (HYBRID - IMPORTANT!)

Your response format depends on the situation:

**CASE 0: Irrelevant Question** (is_irrelevant=true)
Output polite refusal markdown FIRST, then JSON metadata:

I'm a data analytics assistant focused on helping you analyze and query the available data sources.

I can help you with questions about the data domains available in the system. To see what data is available, you can ask:
- "What data sources are available?"
- "What tables can I query?"
- "Show me example questions I can ask"

Could you rephrase your question to focus on analyzing the available data?

```json
{{{{
  "is_irrelevant": true,
  "is_meta_question": false,
  "meta_answer": null,
  "intent_type": "new_question",
  "confidence": 0.95,
  "context_summary": "User asked an irrelevant question unrelated to data analytics",
  "question_clear": true,
  "clarification_reason": null,
  "clarification_options": null,
  "metadata": {{{{"domain": "irrelevant", "complexity": "simple", "topic_change_score": 1.0}}}}
}}}}
```

**CASE 1: Meta-Question** (is_meta_question=true)
Output markdown answer FIRST, then JSON metadata:

## Available Data Sources

[Your detailed markdown answer here with headings, bullets, bold keywords]

```json
{{
  "is_irrelevant": false,
  "is_meta_question": true,
  "meta_answer": null,
  "intent_type": "new_question",
  "confidence": 0.95,
  "context_summary": "User asking about available data sources",
  "question_clear": true,
  "clarification_reason": null,
  "clarification_options": null,
  "metadata": {{"domain": "system", "complexity": "simple", "topic_change_score": 0.5}}
}}
```

**CASE 2: Unclear Query** (question_clear=false)
Output clarification markdown FIRST, then JSON metadata:

### Clarification Needed

[Explain what's unclear with headings, bullets, and numbered options]

```json
{{
  "is_irrelevant": false,
  "is_meta_question": false,
  "meta_answer": null,
  "intent_type": "new_question",
  "confidence": 0.85,
  "context_summary": "2-3 sentence summary",
  "question_clear": false,
  "clarification_reason": null,
  "clarification_options": ["Option 1", "Option 2", "Option 3"],
  "metadata": {{"domain": "...", "complexity": "...", "topic_change_score": 0.8}}
}}
```

**CASE 3: Clear Regular Query** (question_clear=true, is_meta_question=false)
Output ONLY JSON (no markdown prefix):

```json
{{
  "is_irrelevant": false,
  "is_meta_question": false,
  "meta_answer": null,
  "intent_type": "new_question" | "refinement" | "continuation" | "clarification_response",
  "confidence": 0.95,
  "context_summary": "2-3 sentence summary for planning agent",
  "question_clear": true,
  "clarification_reason": null,
  "clarification_options": null,
  "metadata": {{
    "domain": "patients | claims | providers | medications | ...",
    "complexity": "simple | moderate | complex",
    "topic_change_score": 0.8
  }}
}}
```

CRITICAL: 
- For irrelevant questions, meta-questions, and clarifications: markdown FIRST (will be streamed to user), then JSON
- For regular clear queries: JSON ONLY (no markdown needed)
- Always use proper markdown formatting with ##/### headings, **bold**, bullet lists
- Use professional but friendly tone for data analytics
"""
    
    # Call LLM with stream for immediate markdown output (using pooled connection)
    llm = get_pooled_llm(llm_endpoint)
    track_agent_model_usage("clarification", llm_endpoint)
    
    # Emit minimal logging message
    writer({"type": "agent_thinking", "agent": "unified", "content": "Analyzing query context..."})
    
    try:
        print("🤖 Streaming unified LLM response for immediate markdown display...")
        
        # Use stream for immediate user feedback on markdown content
        # Hybrid format: markdown FIRST (streamed), then JSON (parsed)
        accumulated_content = ""
        markdown_section = ""
        in_json_block = False
        streamed_markdown = False
        
        for chunk in llm.stream(unified_prompt):
            if chunk.content:
                accumulated_content += chunk.content
                
                # Detect if we've hit the JSON block
                if "```json" in accumulated_content and not in_json_block:
                    in_json_block = True
                    # Extract and stream any remaining markdown before JSON block
                    if not streamed_markdown:
                        parts = accumulated_content.split("```json")
                        markdown_section = parts[0].strip()
                        if markdown_section:
                            # Stream the markdown we've accumulated
                            # Note: This will be picked up by ResponseAgent's "messages" stream mode
                            print(f"  📄 Streaming markdown section ({len(markdown_section)} chars)...")
                            streamed_markdown = True
                
                # Stream markdown chunks if we haven't hit JSON yet
                if not in_json_block and chunk.content.strip():
                    markdown_section += chunk.content
                    # Emit as AIMessageChunk for ResponseAgent to stream
                    # The ResponseAgent's predict_stream already handles AIMessageChunk via "messages" mode
        
        content = accumulated_content  # Full content for JSON parsing
        
        print(f"✓ Stream complete ({len(content)} chars total)")
        if streamed_markdown:
            print(f"  ✓ Streamed {len(markdown_section)} chars of markdown to UI")
        
        # Parse JSON response from hybrid format
        # Extract JSON from code block after markdown (if present)
        if "```json" in content:
            # Split markdown and JSON sections
            parts = content.split("```json")
            markdown_section = parts[0].strip()  # Markdown prefix (if any)
            json_section = parts[1].split("```")[0].strip()  # JSON content
        elif "```" in content:
            # Fallback for generic code block
            json_section = content.split("```")[1].split("```")[0].strip()
        else:
            # Pure JSON (regular clear queries with no markdown)
            json_section = content.strip()
        
        result = json.loads(json_section)
        
        # Extract results
        is_irrelevant = result.get("is_irrelevant", False)
        is_meta_question = result.get("is_meta_question", False)
        meta_answer = result.get("meta_answer")
        intent_type = result["intent_type"].lower()
        confidence = result["confidence"]
        context_summary = result["context_summary"]
        question_clear = result["question_clear"]
        clarification_reason = result.get("clarification_reason")
        clarification_options = result.get("clarification_options", [])
        metadata = result.get("metadata", {})
        
        print(f"✓ Intent: {intent_type} (confidence: {confidence:.2f})")
        print(f"  Context: {context_summary[:100]}...")
        print(f"  Question clear: {question_clear}")
        print(f"  Irrelevant: {is_irrelevant}")
        print(f"  Meta-question: {is_meta_question}")
        
        # Create conversation turn
        turn = create_conversation_turn(
            query=current_query,
            intent_type=intent_type,
            parent_turn_id=None,  # Could extract from history if needed
            context_summary=context_summary,
            triggered_clarification=False,  # Will be updated if clarification triggered
            metadata=metadata
        )
        
        # Create intent metadata
        intent_metadata = IntentMetadata(
            intent_type=intent_type,
            confidence=confidence,
            reasoning=f"Unified analysis: {intent_type}",
            topic_change_score=metadata.get("topic_change_score", 0.5),
            domain=metadata.get("domain"),
            operation=None,
            complexity=metadata.get("complexity", "moderate"),
            parent_turn_id=None
        )
        
        # Emit events
        writer({
            "type": "intent_detected",
            "intent_type": intent_type,
            "confidence": confidence,
            "complexity": metadata.get("complexity", "moderate")
        })
        
        # NEW: Check if this is an irrelevant question - handle immediately
        if is_irrelevant:
            print("🚫 Irrelevant question detected - providing polite refusal")
            
            # Create turn for irrelevant question
            turn["metadata"]["is_irrelevant"] = True
            
            # Emit metadata event (markdown was already streamed during LLM call)
            writer({
                "type": "irrelevant_question_detected",
                "note": "Irrelevant refusal markdown already streamed to UI"
            })
            
            # Use the markdown section that was streamed (from hybrid output)
            # If no markdown section (edge case), format a simple response
            if markdown_section and markdown_section.strip():
                irrelevant_display = markdown_section
            else:
                irrelevant_display = """I'm a data analytics assistant focused on helping you analyze and query the available data sources.

I can help you with questions about the data domains available in the system. To see what data is available, you can ask:
- "What data sources are available?"
- "What tables can I query?"
- "Show me example questions I can ask"

Could you rephrase your question to focus on analyzing the available data?"""
            
            # Return with irrelevant flag to skip SQL generation
            return {
                "current_turn": turn,
                "turn_history": [turn],
                "intent_metadata": IntentMetadata(
                    intent_type=intent_type,
                    confidence=confidence,
                    reasoning=f"Irrelevant question: {intent_type}",
                    topic_change_score=1.0,
                    domain="irrelevant",
                    operation=None,
                    complexity=metadata.get("complexity", "simple"),
                    parent_turn_id=None
                ),
                "question_clear": True,  # Set to True so it doesn't trigger clarification
                "is_irrelevant": True,  # Flag for routing
                "is_meta_question": False,
                "pending_clarification": None,
                "messages": [
                    AIMessage(content=irrelevant_display),
                    SystemMessage(content="Irrelevant question detected, skipping SQL generation")
                ]
            }
        
        # NEW: Check if this is a meta-question - handle immediately
        if is_meta_question:
            print("🔍 Meta-question detected - answering directly without SQL")
            
            # Create turn for meta-question
            turn["metadata"]["is_meta_question"] = True
            
            # Emit metadata event (markdown was already streamed during LLM call)
            writer({
                "type": "meta_question_detected",
                "note": "Meta-answer markdown already streamed to UI"
            })
            
            # Use the markdown section that was streamed (from hybrid output)
            # If no markdown section (edge case), format a simple response
            if markdown_section and markdown_section.strip():
                meta_answer_display = markdown_section
            else:
                meta_answer_display = format_meta_answer_markdown(
                    "Meta-question detected. The answer was provided above."
                )
            
            # Return with meta-answer and flag to skip SQL generation
            return {
                "current_turn": turn,
                "turn_history": [turn],
                "intent_metadata": IntentMetadata(
                    intent_type=intent_type,
                    confidence=confidence,
                    reasoning=f"Meta-question: {intent_type}",
                    topic_change_score=metadata.get("topic_change_score", 0.5),
                    domain=metadata.get("domain"),
                    operation=None,
                    complexity=metadata.get("complexity", "simple"),
                    parent_turn_id=None
                ),
                "question_clear": True,
                "is_meta_question": True,  # Flag for routing
                "meta_answer": markdown_section,  # The streamed markdown
                "pending_clarification": None,
                "messages": [
                    AIMessage(content=meta_answer_display),
                    SystemMessage(content="Meta-question answered directly, skipping SQL generation")
                ]
            }
        
        # Check if clarification needed
        if not question_clear:
            print(f"⚠ Query unclear: {clarification_reason}")
            
            # Check rate limit
            is_rate_limited = check_clarification_rate_limit(turn_history, window_size=5)
            
            if is_rate_limited:
                print("⚠ Clarification rate limit reached (1 per 5 turns)")
                print("  Proceeding with best-effort interpretation")
                
                writer({"type": "clarification_skipped", "reason": "Rate limited (1 per 5 turns)"})
                
                # Force proceed to planning
                return {
                    "current_turn": turn,
                    "turn_history": [turn],
                    "intent_metadata": intent_metadata,
                    "question_clear": True,  # Force clear
                    "pending_clarification": None,
                    "messages": [
                        SystemMessage(content=f"Clarification rate limited. Proceeding with: {context_summary}")
                    ]
                }
            else:
                # OK to clarify
                print("✓ Requesting clarification from user")
                
                # Create clarification request
                clarification_request = create_clarification_request(
                    reason=clarification_reason or "Query needs more specificity",
                    options=clarification_options,
                    turn_id=turn["turn_id"],
                    best_guess=context_summary,
                    best_guess_confidence=confidence
                )
                
                # Mark turn as triggering clarification
                turn["triggered_clarification"] = True
                
                # Emit metadata event (markdown was already streamed during LLM call)
                writer({
                    "type": "clarification_requested", 
                    "note": "Clarification markdown already streamed to UI"
                })
                
                # Use the markdown section that was streamed (from hybrid output)
                # If no markdown section (edge case), format a simple response
                if markdown_section and markdown_section.strip():
                    clarification_display = markdown_section
                else:
                    # Fallback: format clarification with options as markdown
                    clarification_display = format_clarification_markdown(
                        reason=clarification_reason or "Query needs more specificity",
                        options=clarification_options
                    )
                
                return {
                    "current_turn": turn,
                    "turn_history": [turn],
                    "intent_metadata": intent_metadata,
                    "question_clear": False,
                    "pending_clarification": clarification_request,
                    "messages": [
                        AIMessage(content=clarification_display),
                        SystemMessage(content=f"Clarification requested for turn {turn['turn_id']}")
                    ]
                }
        else:
            # Question is clear, proceed to planning
            print("✓ Query is clear - proceeding to planning")
            
            writer({"type": "clarity_analysis", "clear": True, "reasoning": "Query is clear and answerable"})
            
            return {
                "current_turn": turn,
                "turn_history": [turn],
                "intent_metadata": intent_metadata,
                "question_clear": True,
                "pending_clarification": None,
                "messages": [
                    SystemMessage(content=f"Intent: {intent_type}, proceeding to planning")
                ]
            }
        
    except Exception as e:
        print(f"❌ Unified agent error: {e}")
        traceback.print_exc()
        
        # Fallback: create minimal turn and proceed
        turn = create_conversation_turn(
            query=current_query,
            intent_type="new_question",
            context_summary=f"Query: {current_query}",
            triggered_clarification=False,
            metadata={}
        )
        
        intent_metadata = IntentMetadata(
            intent_type="new_question",
            confidence=0.5,
            reasoning=f"Error fallback: {str(e)}",
            topic_change_score=1.0,
            domain=None,
            operation=None,
            complexity="moderate",
            parent_turn_id=None
        )
        
        return {
            "current_turn": turn,
            "turn_history": [turn],
            "intent_metadata": intent_metadata,
            "question_clear": True,  # Proceed despite error
            "pending_clarification": None,
            "messages": [
                SystemMessage(content=f"Unified agent error (proceeding anyway): {str(e)}")
            ]
        }
