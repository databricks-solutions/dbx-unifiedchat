"""
Multi-Agent System for Cross-Domain Queries.

This package provides a modular multi-agent system built with LangGraph
for intelligent cross-domain querying using Databricks Genie.
"""

from .core.state import AgentState, ConversationTurn, ClarificationRequest, IntentMetadata
from .core.graph import create_super_agent_hybrid, create_agent_graph
from .core.config import get_config, AgentConfig

__version__ = "1.0.0"

__all__ = [
    # State types
    "AgentState",
    "ConversationTurn",
    "ClarificationRequest",
    "IntentMetadata",
    
    # Graph functions
    "create_super_agent_hybrid",
    "create_agent_graph",
    
    # Configuration
    "get_config",
    "AgentConfig",
]
