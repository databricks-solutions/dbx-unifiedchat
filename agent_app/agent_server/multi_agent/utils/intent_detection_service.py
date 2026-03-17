"""
Intent Detection Service for Multi-Turn Conversation Management

This module provides a first-class service for detecting user intent in
conversational contexts. Intent detection informs clarification logic,
planning strategies, and business logic (billing, analytics, routing).

Design Principles:
- Intent detection as a dedicated service (not a helper function)
- Structured output with confidence scores and metadata
- Extensible for business logic integration
- Clear separation from clarification logic
"""

import json
from typing import List, Dict, Any, Optional, Literal
from datetime import datetime
from langchain_core.runnables import Runnable
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from .conversation_models import (
    ConversationTurn,
    IntentMetadata,
    create_conversation_turn,
    get_recent_turn_summary
)


# ==============================================================================
# Intent Detection Prompts
# ==============================================================================

INTENT_DETECTION_PROMPT = """You are an expert at analyzing user queries in conversational context.

Your task is to classify the user's current query based on the conversation history.

Current Query: {current_query}

{conversation_context}

## Intent Types

Classify the query into ONE of these categories:

1. **new_question**: A completely different topic, question, or data domain from previous queries
   - Example: "Show patient data" → "Show medication costs" (different topics)
   - Example: "Claims by provider" → "Member demographics" (different domain)

2. **refinement**: Narrowing, filtering, or modifying the previous query on the same topic
   - Example: "Show patient data" → "Only patients age 50+" (filtering same query)
   - Example: "Show active members" → "Break down by age group" (refining same data)

3. **clarification_response**: User is answering the agent's clarification request
   - Example: Agent asks "Which metric?" → User says "Patient count" (answering question)
   - Pattern: Previous message from AI contains "clarification" or asks a question

4. **continuation**: Follow-up question exploring the same topic from a different angle
   - Example: "Show active members" → "What about inactive ones?" (related but different)
   - Example: "Patient count by state" → "Now show by gender" (same domain, new dimension)

## Additional Analysis

Also determine:
- **Topic Change Score**: How much the topic changed (0.0 = same, 1.0 = completely different)
- **Domain**: Data domain (patients, claims, providers, medications, etc.)
- **Operation**: Type of operation (aggregate, filter, compare, lookup, analyze, etc.)
- **Complexity**: Query complexity (simple, moderate, complex)
- **Parent Turn ID**: If this is a refinement/continuation, which turn is it related to?

## Output Format

Return ONLY valid JSON with this exact structure:

{{
    "intent_type": "new_question | refinement | clarification_response | continuation",
    "confidence": 0.95,
    "reasoning": "Brief 1-2 sentence explanation of classification",
    "topic_change_score": 0.8,
    "context_summary": "2-3 sentence summary of relevant history for this query that will help the planning agent understand the full context",
    "metadata": {{
        "domain": "patients | claims | providers | medications | ...",
        "operation": "aggregate | filter | compare | lookup | analyze | ...",
        "complexity": "simple | moderate | complex"
    }},
    "parent_turn_id": "uuid-here-if-refinement-or-continuation-else-null"
}}

Important:
- Be precise in classification - don't confuse refinements with new questions
- If unsure between two types, use confidence < 0.7 to indicate uncertainty
- Context summary should be specific and actionable for planning
- Parent turn ID should reference the most relevant previous turn (use turn_id from history)
"""


CLARIFICATION_DETECTION_PROMPT = """Analyze the conversation to determine if the user is responding to a clarification request.

Recent Conversation:
{conversation_messages}

Current User Query: {current_query}

Pattern to detect:
1. Look for the most recent AI message that requested clarification
2. Check if it contains phrases like "I need clarification", "Please clarify", "Which", "What", etc.
3. Determine if the current query is answering that clarification request

Return ONLY valid JSON:

{{
    "is_clarification_response": true or false,
    "clarification_question": "The question agent asked" or null,
    "original_query": "The user's original query that triggered clarification" or null,
    "confidence": 0.95
}}
"""


# ==============================================================================
# Intent Detection Agent
# ==============================================================================

class IntentDetectionAgent:
    """
    Agent responsible for detecting user intent in conversational context.
    
    This is a first-class service that runs BEFORE clarification to inform
    all downstream logic including clarification, planning, and business logic.
    
    Usage:
        agent = IntentDetectionAgent(llm)
        result = agent.detect_intent(
            current_query="Show me patient count",
            turn_history=[...],
            messages=[...]
        )
    """
    
    def __init__(self, llm: Runnable):
        """
        Initialize the Intent Detection Agent.
        
        Args:
            llm: LLM instance for intent classification
        """
        self.llm = llm
        self.name = "IntentDetection"
    
    def _format_conversation_context(
        self,
        turn_history: List[ConversationTurn],
        messages: List,
        max_turns: int = 5
    ) -> str:
        """
        Format conversation context for the intent detection prompt.
        
        Uses topic-scoped context to ensure strict isolation between different
        topics in the same thread (e.g., Question 1 and Question 2 don't mix).
        
        Args:
            turn_history: List of previous conversation turns
            messages: Raw message history
            max_turns: Maximum number of recent turns from current topic to include
        
        Returns:
            Formatted context string with topic-scoped turns only
        """
        if not turn_history:
            return "No previous conversation history (this is the first query)."
        
        # Get topic-scoped turns (strict isolation)
        # Import here to avoid circular dependency
        from .conversation_models import get_current_topic_turns
        
        # For the PREVIOUS turn (we're analyzing the current query, not in turn_history yet)
        last_turn = turn_history[-1]
        topic_turns = get_current_topic_turns(turn_history, last_turn, max_recent=max_turns)
        
        # Format only topic-scoped turns
        context = "Current Topic Context (Topic-Isolated):\n\n"
        
        for i, turn in enumerate(topic_turns, 1):
            intent_label = turn['intent_type'].replace('_', ' ').title()
            context += f"Turn {i} [{intent_label}]:\n"
            context += f"  Query: {turn['query']}\n"
            context += f"  Turn ID: {turn['turn_id']}\n"
            
            if turn.get('context_summary'):
                context += f"  Context: {turn['context_summary']}\n"
            
            if turn.get('triggered_clarification'):
                context += f"  Note: This turn triggered a clarification request\n"
            
            context += "\n"
        
        # Add recent AI messages (especially clarification requests)
        # Still use recent messages for clarification detection
        ai_messages = [msg for msg in messages[-5:] if isinstance(msg, AIMessage)]
        if ai_messages:
            context += "Recent Agent Responses:\n"
            for msg in ai_messages[-2:]:  # Last 2 AI messages
                snippet = msg.content[:200] + "..." if len(msg.content) > 200 else msg.content
                context += f"  - {snippet}\n"
        
        return context
    
    def _check_for_clarification_response(
        self,
        current_query: str,
        messages: List
    ) -> Dict[str, Any]:
        """
        Two-phase check if current query is a clarification response.
        
        Phase 1 (Pattern Matching): Fast detection of unanswered clarification requests
        Phase 2 (LLM Validation): Verify the user's message actually answers the clarification
        
        This prevents false positives when users ignore clarification and ask something else.
        
        IMPORTANT: Only detects unanswered clarification requests.
        If a clarification has already been answered by a HumanMessage,
        subsequent queries are NOT clarification responses.
        
        Args:
            current_query: Current user query
            messages: Message history
        
        Returns:
            Dict with is_clarification_response flag and context
        """
        if len(messages) < 2:
            return {"is_clarification_response": False}
        
        # PHASE 1: Pattern Matching (Fast-path)
        # Look for recent AI clarification message (tightened to last 3 messages for recency)
        # Reduced from 5 to 3 to avoid stale clarification requests
        search_window = min(3, len(messages) - 1)
        
        for i in range(len(messages) - 1, max(0, len(messages) - search_window - 1), -1):
            msg = messages[i]
            
            # ONLY check AIMessages (not SystemMessages which may contain "clarification" in best-effort traces)
            if isinstance(msg, AIMessage):
                content_lower = msg.content.lower()
                clarification_keywords = [
                    "clarification",
                    "please clarify",
                    "which",
                    "what do you mean",
                    "can you specify",
                    "choose one",
                    "options:"
                ]
                
                if any(keyword in content_lower for keyword in clarification_keywords):
                    # Found potential clarification message
                    # CHECK: Has this clarification already been answered?
                    # Look for HumanMessage AFTER this AIMessage
                    has_human_response_after = False
                    for k in range(i + 1, len(messages)):
                        if isinstance(messages[k], HumanMessage):
                            has_human_response_after = True
                            break
                    
                    if has_human_response_after:
                        # Clarification was already answered - keep searching for more recent clarification
                        print(f"  ⚠ Found clarification at index {i} but it was already answered - skipping")
                        continue
                    
                    # This is an UNANSWERED clarification request
                    print(f"  ✓ Found unanswered clarification at index {i}")
                    
                    # PHASE 2: LLM Validation (Smart check)
                    # Verify the user's current query actually answers the clarification
                    # This prevents false positives like:
                    #   AI: "Which age group? 1) 0-18, 2) 19-65, 3) 65+"
                    #   User: "Actually, show me medications instead" ← Not a clarification response!
                    
                    clarification_question = msg.content
                    
                    # Get original query that triggered the clarification
                    original_query = None
                    for j in range(i - 1, -1, -1):
                        if isinstance(messages[j], HumanMessage):
                            original_query = messages[j].content
                            break
                    
                    # Use LLM to validate if current_query answers the clarification
                    validation_result = self._validate_clarification_response(
                        current_query=current_query,
                        clarification_question=clarification_question,
                        original_query=original_query
                    )
                    
                    if validation_result["is_answer"]:
                        print(f"  ✓ LLM confirmed: User is answering the clarification (confidence: {validation_result['confidence']:.2f})")
                        return {
                            "is_clarification_response": True,
                            "clarification_question": clarification_question,
                            "original_query": original_query,
                            "confidence": validation_result["confidence"]
                        }
                    else:
                        print(f"  ✗ LLM determined: User is NOT answering the clarification")
                        print(f"    Reason: {validation_result['reasoning']}")
                        # User ignored the clarification - continue searching or return False
                        continue
        
        return {"is_clarification_response": False}
    
    def _validate_clarification_response(
        self,
        current_query: str,
        clarification_question: str,
        original_query: Optional[str]
    ) -> Dict[str, Any]:
        """
        Use LLM to validate if the current query is actually answering the clarification.
        
        This prevents false positives when users change topics after a clarification request.
        
        Args:
            current_query: User's current message
            clarification_question: The AI's clarification question
            original_query: The user's original query that triggered clarification
        
        Returns:
            Dict with is_answer (bool), confidence (float), reasoning (str)
        """
        validation_prompt = f"""You are analyzing if a user's message is answering a clarification request.

Original User Query: {original_query or "N/A"}

Agent's Clarification Question:
{clarification_question}

User's Current Message:
{current_query}

Determine if the user's current message is:
A) Answering/responding to the clarification question (e.g., choosing an option, providing the requested detail)
B) Ignoring the clarification and asking something completely different

Return ONLY valid JSON:
{{
    "is_answer": true or false,
    "confidence": 0.95,
    "reasoning": "Brief 1-sentence explanation"
}}

Examples:
- Clarification: "Which age group? 1) 0-18, 2) 19-65" → User: "Option 2" → is_answer: true
- Clarification: "Which age group? 1) 0-18, 2) 19-65" → User: "Show medications" → is_answer: false
- Clarification: "Do you mean active or inactive?" → User: "Active ones" → is_answer: true
- Clarification: "Do you mean active or inactive?" → User: "What about claims?" → is_answer: false
"""
        
        try:
            response = self.llm.invoke(validation_prompt)
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Parse JSON response
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            
            result = json.loads(content)
            
            return {
                "is_answer": result.get("is_answer", False),
                "confidence": result.get("confidence", 0.5),
                "reasoning": result.get("reasoning", "No reasoning provided")
            }
            
        except Exception as e:
            print(f"  ⚠ Clarification validation failed: {e}")
            # Conservative fallback: assume it IS an answer (to avoid breaking existing behavior)
            # But with low confidence so downstream logic can handle it carefully
            return {
                "is_answer": True,
                "confidence": 0.6,
                "reasoning": f"Validation failed, assuming it's an answer: {str(e)}"
            }
    
    def detect_intent(
        self,
        current_query: str,
        turn_history: List[ConversationTurn],
        messages: List,
        previous_intent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Detect the intent of the current query in conversational context.
        
        Args:
            current_query: The current user query
            turn_history: List of previous conversation turns
            messages: Raw message history
            previous_intent: Intent from previous turn (for context)
        
        Returns:
            Dict with intent_type, confidence, reasoning, context_summary, and metadata
        """
        print(f"\n{'='*80}")
        print(f"🎯 INTENT DETECTION")
        print(f"{'='*80}")
        print(f"Query: {current_query}")
        
        # Smart two-phase check: Pattern matching + LLM validation
        clarification_check = self._check_for_clarification_response(current_query, messages)
        if clarification_check["is_clarification_response"]:
            print("✓ Detected clarification response (validated by LLM)")
            
            # Generate intelligent context summary using LLM
            original_query = clarification_check.get("original_query", "")
            clarification_question = clarification_check.get("clarification_question", "")
            
            # Get previous context from turn_history (crucial for refinement chains)
            # The original_query might only be a refinement, missing the full conversation context
            previous_context = ""
            if turn_history:
                last_turn = turn_history[-1]
                previous_context_summary = last_turn.get("context_summary", "")
                
                if previous_context_summary:
                    print(f"  ✓ Found previous context_summary from turn {last_turn['turn_id'][:8]}...")
                    previous_context = f"\nPrevious Conversation Context:\n{previous_context_summary}\n"
                else:
                    # Fallback: Build context from recent turns
                    recent_turns = turn_history[-3:] if len(turn_history) >= 3 else turn_history
                    if recent_turns:
                        print(f"  ✓ Building context from {len(recent_turns)} recent turns...")
                        previous_context = "\nPrevious Conversation History:\n"
                        for turn in recent_turns:
                            intent_label = turn['intent_type'].replace('_', ' ').title()
                            previous_context += f"- [{intent_label}] {turn['query']}\n"
                        previous_context += "\n"
            
            # Use LLM to generate context summary (instead of manual template)
            context_generation_prompt = f"""You are helping a planning agent understand the complete context of a clarification flow.

The user was asked for clarification and has now responded. Generate a concise, actionable context summary that combines all pieces of information for the planning agent.
{previous_context}
Original Query: {original_query}
Clarification Question Asked: {clarification_question}
User's Clarification Response: {current_query}

Generate a 2-3 sentence context summary that:
1. Synthesizes the FULL conversation context (including previous context if provided)
2. States clearly what the user wants
3. Is actionable for SQL query generation

Return ONLY the context summary text (no JSON, no formatting)."""

            try:
                print("🤖 Generating LLM-based context summary for clarification response...")
                summary_response = self.llm.invoke(context_generation_prompt)
                context_summary = summary_response.content if hasattr(summary_response, 'content') else str(summary_response)
                context_summary = context_summary.strip()
                print(f"✓ Context summary generated: {context_summary[:150]}...")
            except Exception as e:
                print(f"⚠ Failed to generate LLM context summary: {e}")
                # Fallback to structured template (better than crashing)
                context_summary = f"""User is responding to a clarification request.
            
Original Query: {original_query}
Clarification Asked: {clarification_question}
User's Answer: {current_query}

The planning agent should use all three pieces to understand the complete intent."""
            
            return {
                "intent_type": "clarification_response",
                "confidence": clarification_check["confidence"],
                "reasoning": "User is answering agent's clarification request",
                "topic_change_score": 0.0,
                "context_summary": context_summary,
                "metadata": {
                    "domain": "unknown",
                    "operation": "clarification",
                    "complexity": "simple"
                },
                "parent_turn_id": turn_history[-1]["turn_id"] if turn_history else None
            }
        
        # If NOT a clarification response (either no clarification found, or user ignored it),
        # fall through to full LLM-based intent detection
        print("  → Not a clarification response, proceeding to full intent classification")
        
        # Full LLM-based intent detection
        conversation_context = self._format_conversation_context(
            turn_history, messages, max_turns=5
        )
        
        prompt = INTENT_DETECTION_PROMPT.format(
            current_query=current_query,
            conversation_context=conversation_context
        )
        
        try:
            print("🤖 Invoking LLM for intent classification...")
            response = self.llm.invoke(prompt)
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Parse JSON response
            # Handle markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            
            result = json.loads(content)
            
            # Normalize intent_type to lowercase for consistency
            # LLM might return uppercase (CLARIFICATION_RESPONSE) or lowercase (clarification_response)
            if "intent_type" in result and result["intent_type"]:
                result["intent_type"] = result["intent_type"].lower()
            
            print(f"✓ Intent: {result['intent_type']} (confidence: {result['confidence']:.2f})")
            print(f"  Reasoning: {result['reasoning']}")
            print(f"  Topic Change: {result['topic_change_score']:.2f}")
            print(f"  Domain: {result['metadata'].get('domain', 'unknown')}")
            print(f"  Complexity: {result['metadata'].get('complexity', 'unknown')}")
            
            # Final safety check: ensure intent_type is lowercase before returning
            assert result["intent_type"] == result["intent_type"].lower(), \
                f"Bug: intent_type should be lowercase but got {result['intent_type']}"
            
            return result
            
        except json.JSONDecodeError as e:
            print(f"⚠ JSON parsing error: {e}")
            print(f"  Response: {content[:500]}...")
            
            # Fallback: default to NEW_QUESTION
            return {
                "intent_type": "new_question",
                "confidence": 0.5,
                "reasoning": "Failed to parse LLM response, defaulting to new question",
                "topic_change_score": 1.0,
                "context_summary": f"Query: {current_query}",
                "metadata": {
                    "domain": "unknown",
                    "operation": "unknown",
                    "complexity": "moderate"
                },
                "parent_turn_id": None
            }
        
        except Exception as e:
            print(f"⚠ Intent detection failed: {e}")
            
            # Fallback: default to NEW_QUESTION
            return {
                "intent_type": "new_question",
                "confidence": 0.5,
                "reasoning": f"Intent detection error: {str(e)}",
                "topic_change_score": 1.0,
                "context_summary": f"Query: {current_query}",
                "metadata": {
                    "domain": "unknown",
                    "operation": "unknown",
                    "complexity": "moderate"
                },
                "parent_turn_id": None
            }
    
    def __call__(
        self,
        current_query: str,
        turn_history: List[ConversationTurn],
        messages: List
    ) -> Dict[str, Any]:
        """
        Callable interface for the agent.
        
        Args:
            current_query: The current user query
            turn_history: List of previous conversation turns
            messages: Raw message history
        
        Returns:
            Intent detection result
        """
        return self.detect_intent(current_query, turn_history, messages)


# ==============================================================================
# Helper Functions
# ==============================================================================

def create_intent_metadata_from_result(result: Dict[str, Any]) -> IntentMetadata:
    """
    Convert intent detection result to IntentMetadata TypedDict.
    
    Args:
        result: Result from IntentDetectionAgent.detect_intent()
    
    Returns:
        Properly typed IntentMetadata object
    """
    metadata = result.get("metadata", {})
    
    return IntentMetadata(
        intent_type=result["intent_type"],
        confidence=result["confidence"],
        reasoning=result["reasoning"],
        topic_change_score=result["topic_change_score"],
        domain=metadata.get("domain"),
        operation=metadata.get("operation"),
        complexity=metadata.get("complexity", "moderate"),
        parent_turn_id=result.get("parent_turn_id")
    )


def should_skip_clarification_for_intent(intent_type: str) -> bool:
    """
    Determine if clarification should be skipped based on intent type.
    
    Some intents (like clarification_response) should skip clarification
    to avoid asking for clarification on a clarification.
    
    Args:
        intent_type: Intent type from intent detection (case-insensitive)
    
    Returns:
        True if clarification should be skipped, False otherwise
    """
    skip_intents = {
        "clarification_response",  # Already answering a clarification
    }
    
    # Case-insensitive comparison to handle both uppercase and lowercase
    return intent_type.lower() in skip_intents
