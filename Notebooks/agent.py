"""
MLflow agent wrapper for Model Serving deployment.

This file is referenced by deploy_agent.py in mlflow.pyfunc.log_model().
It imports the modular agent code from src/multi_agent/ which gets packaged
via the code_paths parameter.

The agent is deployed as a ResponsesAgent following Databricks best practices.
"""

import sys
import os
from typing import Dict, Any, Optional

# Add src to path to import modular code
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

# Import modular agent code
from multi_agent.core.graph import create_agent_graph
from multi_agent.core.state import get_initial_state, AgentState

# Import MLflow and LangGraph
import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse, ResponsesAgentStreamEvent
from mlflow.models import ModelConfig
from langgraph.graph import StateGraph
from databricks_langchain.checkpoint import DatabricksCheckpointSaver
from databricks.sdk import WorkspaceClient
from typing import Generator
import yaml


class SuperAgentHybridResponsesAgent(ResponsesAgent):
    """
    Multi-Agent ResponsesAgent wrapper for distributed Model Serving.
    
    Features:
    - Short-term memory (CheckpointSaver): Multi-turn conversations
    - Long-term memory (DatabricksStore): User preferences with semantic search
    - Modular code from src/multi_agent/
    - Configuration from YAML (prod_config.yaml)
    
    The modular code is packaged via MLflow's code_paths parameter.
    """
    
    def __init__(self):
        """Initialize the agent wrapper."""
        # Load configuration from YAML (passed via model_config parameter)
        try:
            model_config = ModelConfig.get()
            config_dict = model_config.to_dict()
        except:
            # Fallback to dev config for local testing
            with open("../dev_config.yaml", 'r') as f:
                config_dict = yaml.safe_load(f)
        
        # Extract key configuration
        self.catalog = config_dict['catalog_name']
        self.schema = config_dict['schema_name']
        self.lakebase_instance = config_dict['lakebase_instance_name']
        self.genie_space_ids = config_dict['genie_space_ids']
        
        # Create workflow (without checkpointer - added per request)
        self.workflow = self._create_workflow()
        
        print("✓ SuperAgentHybridResponsesAgent initialized")
        print(f"  Catalog: {self.catalog}")
        print(f"  Schema: {self.schema}")
        print(f"  Lakebase: {self.lakebase_instance}")
        print(f"  Genie Spaces: {len(self.genie_space_ids)}")
    
    def _create_workflow(self) -> StateGraph:
        """
        Create the agent workflow from modular code.
        
        Returns:
            StateGraph: Uncompiled workflow
        """
        # Import from modular code
        from multi_agent.core.graph import create_super_agent_hybrid
        
        # Create workflow
        workflow = create_super_agent_hybrid()
        return workflow
    
    def _get_checkpointer(self):
        """Get or create checkpointer for conversation state."""
        w = WorkspaceClient()
        return DatabricksCheckpointSaver(
            w.lakebase,
            database_instance_name=self.lakebase_instance
        )
    
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        """
        Handle non-streaming prediction request.
        
        Args:
            request: ResponsesAgentRequest with input messages
            
        Returns:
            ResponsesAgentResponse with agent output
        """
        # Extract thread_id from custom_inputs
        thread_id = request.custom_inputs.get("thread_id") if request.custom_inputs else None
        
        # Create agent with checkpointer
        checkpointer = self._get_checkpointer()
        agent = self.workflow.compile(checkpointer=checkpointer)
        
        # Prepare initial state
        initial_state = get_initial_state(thread_id=thread_id)
        initial_state["messages"] = [msg.model_dump() for msg in request.input]
        
        # Invoke agent
        config = {"configurable": {"thread_id": thread_id}} if thread_id else {}
        response = agent.invoke(initial_state, config=config)
        
        # Extract response
        final_response = response.get("final_response") or response.get("meta_answer")
        if not final_response and response.get("pending_clarification"):
            clarification = response["pending_clarification"]
            final_response = f"{clarification['reason']}\n\nOptions:\n"
            for i, option in enumerate(clarification['options'], 1):
                final_response += f"{i}. {option}\n"
        
        # Create ResponsesAgentResponse
        import uuid
        output_item = self.create_text_output_item(
            text=final_response or "No response generated",
            id=str(uuid.uuid4())
        )
        
        return ResponsesAgentResponse(output=[output_item])
    
    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[ResponsesAgentStreamEvent, None, None]:
        """
        Handle streaming prediction request.
        
        Args:
            request: ResponsesAgentRequest with input messages
            
        Yields:
            ResponsesAgentStreamEvent for streaming response
        """
        # Extract thread_id
        thread_id = request.custom_inputs.get("thread_id") if request.custom_inputs else None
        
        # Create agent with checkpointer
        checkpointer = self._get_checkpointer()
        agent = self.workflow.compile(checkpointer=checkpointer)
        
        # Prepare initial state
        initial_state = get_initial_state(thread_id=thread_id)
        initial_state["messages"] = [msg.model_dump() for msg in request.input]
        
        # Stream response
        import uuid
        item_id = str(uuid.uuid4())
        full_text = ""
        
        config = {"configurable": {"thread_id": thread_id}} if thread_id else {}
        
        for chunk in agent.stream(initial_state, config=config):
            # Extract text from chunk
            text = None
            if isinstance(chunk, dict):
                for value in chunk.values():
                    if isinstance(value, dict):
                        if "final_summary" in value:
                            text = value["final_summary"]
                        elif "meta_answer" in value:
                            text = value["meta_answer"]
                        break
            
            if text:
                full_text = text
                # Yield delta event
                yield self.create_text_delta(delta=text, item_id=item_id)
        
        # Yield final done event
        yield ResponsesAgentStreamEvent(
            type="response.output_item.done",
            item=self.create_text_output_item(text=full_text or "No response", id=item_id)
        )


# Register agent with MLflow
agent = SuperAgentHybridResponsesAgent()
mlflow.models.set_model(agent)

print("✓ Agent registered with MLflow")
print("✓ Ready for deployment via deploy_agent.py")
