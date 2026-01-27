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

1. **NEW_QUESTION**: A completely different topic, question, or data domain from previous queries
   - Example: "Show patient data" → "Show medication costs" (different topics)
   - Example: "Claims by provider" → "Member demographics" (different domain)

2. **REFINEMENT**: Narrowing, filtering, or modifying the previous query on the same topic
   - Example: "Show patient data" → "Only patients age 50+" (filtering same query)
   - Example: "Show active members" → "Break down by age group" (refining same data)

3. **CLARIFICATION_RESPONSE**: User is answering the agent's clarification request
   - Example: Agent asks "Which metric?" → User says "Patient count" (answering question)
   - Pattern: Previous message from AI contains "clarification" or asks a question

4. **CONTINUATION**: Follow-up question exploring the same topic from a different angle
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
    "intent_type": "NEW_QUESTION | REFINEMENT | CLARIFICATION_RESPONSE | CONTINUATION",
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
        
        Args:
            turn_history: List of previous conversation turns
            messages: Raw message history
            max_turns: Maximum number of turns to include
        
        Returns:
            Formatted context string
        """
        if not turn_history:
            return "No previous conversation history (this is the first query)."
        
        # Get recent turns
        recent_turns = turn_history[-max_turns:]
        
        context = "Recent Conversation History:\n\n"
        
        for i, turn in enumerate(recent_turns, 1):
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
        Quick check if current query is a clarification response.
        
        This is a fast-path detection using pattern matching before
        invoking the full LLM classification.
        
        Args:
            current_query: Current user query
            messages: Message history
        
        Returns:
            Dict with is_clarification_response flag and context
        """
        if len(messages) < 2:
            return {"is_clarification_response": False}
        
        # Look for recent AI clarification message
        for i in range(len(messages) - 1, max(0, len(messages) - 5), -1):
            msg = messages[i]
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
                    # Found clarification message, get original query
                    original_query = None
                    for j in range(i - 1, -1, -1):
                        if isinstance(messages[j], HumanMessage):
                            original_query = messages[j].content
                            break
                    
                    return {
                        "is_clarification_response": True,
                        "clarification_question": msg.content,
                        "original_query": original_query,
                        "confidence": 0.9
                    }
        
        return {"is_clarification_response": False}
    
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
        
        # Fast-path: Check for clarification response
        clarification_check = self._check_for_clarification_response(current_query, messages)
        if clarification_check["is_clarification_response"]:
            print("✓ Detected clarification response (fast-path)")
            
            # Build context summary for clarification response
            original_query = clarification_check.get("original_query", "")
            clarification_question = clarification_check.get("clarification_question", "")
            
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
            
            print(f"✓ Intent: {result['intent_type']} (confidence: {result['confidence']:.2f})")
            print(f"  Reasoning: {result['reasoning']}")
            print(f"  Topic Change: {result['topic_change_score']:.2f}")
            print(f"  Domain: {result['metadata'].get('domain', 'unknown')}")
            print(f"  Complexity: {result['metadata'].get('complexity', 'unknown')}")
            
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
        intent_type: Intent type from intent detection
    
    Returns:
        True if clarification should be skipped, False otherwise
    """
    skip_intents = {
        "clarification_response",  # Already answering a clarification
    }
    
    return intent_type in skip_intents
