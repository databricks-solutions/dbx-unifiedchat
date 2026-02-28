import json
import time
import logging
from uuid import uuid4
from typing import Dict, List, Optional, Any, Generator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import StateGraph

from databricks_langchain import CheckpointSaver, DatabricksStore

from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    output_to_responses_items_stream,
    to_chat_completions_input,
)

from .state import RESET_STATE_TEMPLATE
from .config import get_config

logger = logging.getLogger(__name__)

# Performance metrics storage
_performance_metrics = {
    "node_timings": {},
    "cache_stats": {
        "space_context_hits": 0,
        "space_context_misses": 0,
        "vector_search_hits": 0,
        "vector_search_misses": 0,
        "agent_cache_hits": 0,
        "agent_cache_misses": 0,
        "llm_pool_hits": 0,
        "llm_pool_misses": 0
    },
    "workflow_metrics": {
        "ttft_seconds": [],
        "ttcl_seconds": [],
        "total_requests": 0
    }
}

class SuperAgentHybridResponsesAgent(ResponsesAgent):
    """
    Enhanced ResponsesAgent with both short-term and long-term memory for distributed Model Serving.
    
    Features:
    - Short-term memory (CheckpointSaver): Multi-turn conversations within a session
    - Long-term memory (DatabricksStore): User preferences across sessions with semantic search
    - Connection pooling and automatic credential rotation
    - Works seamlessly in distributed Model Serving (multiple instances)
    """
    
    def __init__(self, workflow: StateGraph):
        self.workflow = workflow
        config = get_config()
        self.lakebase_instance_name = config.lakebase.instance_name
        self.embedding_endpoint = config.lakebase.embedding_endpoint
        self.embedding_dims = config.lakebase.embedding_dims
        self._store = None
        self._memory_tools = None
        logger.info("✓ SuperAgentHybridResponsesAgent initialized with memory support")
    
    @property
    def store(self):
        if self._store is None:
            logger.info(f"Initializing DatabricksStore with instance: {self.lakebase_instance_name}")
            self._store = DatabricksStore(
                instance_name=self.lakebase_instance_name,
                embedding_endpoint=self.embedding_endpoint,
                embedding_dims=self.embedding_dims,
            )
            self._store.setup()
            logger.info("✓ DatabricksStore initialized")
        return self._store
    
    @property
    def memory_tools(self):
        if self._memory_tools is None:
            logger.info("Creating memory tools for long-term memory")
            
            @tool
            def get_user_memory(query: str, config: RunnableConfig) -> str:
                """Search for relevant user information using semantic search."""
                user_id = config.get("configurable", {}).get("user_id")
                if not user_id:
                    return "Memory not available - no user_id provided."
                
                namespace = ("user_memories", user_id.replace(".", "-"))
                results = self.store.search(namespace, query=query, limit=5)
                
                if not results:
                    return "No memories found for this user."
                
                memory_items = [f"- [{item.key}]: {json.dumps(item.value)}" for item in results]
                return f"Found {len(results)} relevant memories (ranked by similarity):\n" + "\n".join(memory_items)
            
            @tool
            def save_user_memory(memory_key: str, memory_data_json: str, config: RunnableConfig) -> str:
                """Save information about the user to long-term memory."""
                user_id = config.get("configurable", {}).get("user_id")
                if not user_id:
                    return "Cannot save memory - no user_id provided."
                
                namespace = ("user_memories", user_id.replace(".", "-"))
                try:
                    memory_data = json.loads(memory_data_json)
                    if not isinstance(memory_data, dict):
                        return f"Failed: memory_data must be a JSON object, not {type(memory_data).__name__}"
                    self.store.put(namespace, memory_key, memory_data)
                    return f"Successfully saved memory with key '{memory_key}' for user"
                except json.JSONDecodeError as e:
                    return f"Failed to save memory: Invalid JSON format - {str(e)}"
            
            @tool
            def delete_user_memory(memory_key: str, config: RunnableConfig) -> str:
                """Delete a specific memory from the user's long-term memory."""
                user_id = config.get("configurable", {}).get("user_id")
                if not user_id:
                    return "Cannot delete memory - no user_id provided."
                
                namespace = ("user_memories", user_id.replace(".", "-"))
                self.store.delete(namespace, memory_key)
                return f"Successfully deleted memory with key '{memory_key}' for user"
            
            self._memory_tools = [get_user_memory, save_user_memory, delete_user_memory]
            logger.info(f"✓ Created {len(self._memory_tools)} memory tools")
        
        return self._memory_tools
    
    def _get_or_create_thread_id(self, request: ResponsesAgentRequest) -> str:
        ci = dict(request.custom_inputs or {})
        if "thread_id" in ci:
            return ci["thread_id"]
        if request.context and getattr(request.context, "conversation_id", None):
            return request.context.conversation_id
        return str(uuid4())
    
    def _get_user_id(self, request: ResponsesAgentRequest) -> Optional[str]:
        ci = dict(request.custom_inputs or {})
        if "user_id" in ci:
            return ci["user_id"]
        if request.context and getattr(request.context, "user_id", None):
            return request.context.user_id
        return None

    def create_text_output_item(self, text: str, id: str) -> Dict[str, Any]:
        return {
            "id": id,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        }

    def format_custom_event(self, event_data: dict) -> str:
        event_type = event_data.get("type", "unknown")
        if event_type == "agent_start":
            return f"🚀 Starting {event_data.get('agent', 'agent')}"
        elif event_type == "agent_thinking":
            return f"💭 {event_data.get('content', 'Thinking...')}"
        elif event_type == "intent_detected":
            return f"🎯 Intent detected: {event_data.get('intent_type', 'unknown')} (confidence: {event_data.get('confidence', 0):.2f})"
        elif event_type == "clarification_requested":
            return f"❓ Clarification needed"
        elif event_type == "planning_complete":
            return f"📋 Plan created: routing to {event_data.get('next_agent', 'unknown')}"
        elif event_type == "sql_generated":
            return f"📝 SQL generated successfully"
        elif event_type == "sql_executed":
            return f"✅ SQL executed: {event_data.get('rows', 0)} rows returned"
        else:
            return f"ℹ️ Event: {event_type}"

    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[ResponsesAgentStreamEvent, None, None]:
        thread_id = self._get_or_create_thread_id(request)
        user_id = self._get_user_id(request)
        
        ci = dict(request.custom_inputs or {})
        ci["thread_id"] = thread_id
        if user_id:
            ci["user_id"] = user_id
        request.custom_inputs = ci
        
        logger.info(f"Processing request - thread_id: {thread_id}, user_id: {user_id}")
        
        workflow_start_time = time.time()
        first_token_time = None
        _performance_metrics["workflow_metrics"]["total_requests"] += 1
        
        try:
            import mlflow.tracing
            if not hasattr(mlflow.tracing, '_is_enabled') or not mlflow.tracing._is_enabled():
                logger.debug("MLflow tracing not enabled, continuing without tracing")
        except Exception as e:
            logger.debug(f"MLflow tracing check skipped: {e}")
        
        cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
        latest_query = cc_msgs[-1]["content"] if cc_msgs else ""
        
        run_config = {"configurable": {"thread_id": thread_id}}
        if user_id:
            run_config["configurable"]["user_id"] = user_id
        
        initial_state = {
            **RESET_STATE_TEMPLATE,
            "original_query": latest_query,
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
            ]
        }
        
        if user_id:
            initial_state["user_id"] = user_id
            initial_state["thread_id"] = thread_id
        
        first_message = True
        seen_ids = set()
        
        with CheckpointSaver(instance_name=self.lakebase_instance_name) as checkpointer:
            app = self.workflow.compile(checkpointer=checkpointer)
            logger.info(f"Executing workflow with checkpointer (thread: {thread_id})")
            
            for event in app.stream(initial_state, run_config, stream_mode=["updates", "messages", "custom", "tasks"]):
                event_type = event[0]
                event_data = event[1]
                
                if event_type == "messages":
                    try:
                        chunk = event_data[0] if isinstance(event_data, (list, tuple)) else event_data
                        if isinstance(chunk, AIMessageChunk) and (content := chunk.content):
                            if first_token_time is None:
                                first_token_time = time.time()
                                ttft = first_token_time - workflow_start_time
                                _performance_metrics["workflow_metrics"]["ttft_seconds"].append(ttft)
                            
                            yield ResponsesAgentStreamEvent(
                                type="response.output_item.delta",
                                item=self.create_text_output_item(text=content, id=chunk.id),
                            )
                    except Exception as e:
                        logger.warning(f"Error streaming message chunk: {e}")
                
                elif event_type == "updates":
                    for node_name, node_state in event_data.items():
                        if "messages" not in node_state:
                            continue
                        
                        messages = node_state["messages"]
                        if not isinstance(messages, list):
                            messages = [messages]
                        
                        for msg in messages:
                            if hasattr(msg, 'id') and msg.id in seen_ids:
                                continue
                            if hasattr(msg, 'id'):
                                seen_ids.add(msg.id)
                            
                            if hasattr(msg, '__class__') and msg.__class__.__name__ == 'AIMessage':
                                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                    for tool_call in msg.tool_calls:
                                        try:
                                            tool_name = tool_call.get('name', 'unknown')
                                            tool_args = json.dumps(tool_call.get('args', {}))
                                            yield ResponsesAgentStreamEvent(
                                                type="response.output_item.done",
                                                item=self.create_text_output_item(
                                                    text=f"🛠️ Calling tool: {tool_name}({tool_args})",
                                                    id=str(uuid4())
                                                ),
                                            )
                                        except Exception as e:
                                            logger.warning(f"Error emitting tool call: {e}")
                            elif hasattr(msg, '__class__') and msg.__class__.__name__ == 'ToolMessage':
                                try:
                                    tool_name = getattr(msg, 'name', 'unknown')
                                    tool_content = str(msg.content)[:200] if msg.content else "No content"
                                    yield ResponsesAgentStreamEvent(
                                        type="response.output_item.done",
                                        item=self.create_text_output_item(
                                            text=f"🔨 Tool result ({tool_name}): {tool_content}...",
                                            id=str(uuid4())
                                        ),
                                    )
                                except Exception as e:
                                    logger.warning(f"Error emitting tool result: {e}")
                            else:
                                yield from output_to_responses_items_stream([msg])
                
                elif event_type == "custom":
                    try:
                        formatted_text = self.format_custom_event(event_data)
                        yield ResponsesAgentStreamEvent(
                            type="response.output_item.done",
                            item=self.create_text_output_item(
                                text=formatted_text,
                                id=str(uuid4())
                            ),
                        )
                    except Exception as e:
                        logger.warning(f"Error processing custom event: {e}")
                
                elif event_type == "tasks":
                    try:
                        task_event = event_data
                        event_name = task_event.get("event", "unknown")
                        node_name = task_event.get("name", "unknown")
                        
                        if event_name == "start":
                            logger.debug(f"⏳ Task started: {node_name}")
                        elif event_name == "end":
                            duration = task_event.get("duration")
                            if duration:
                                logger.info(f"✅ Task completed: {node_name} ({duration:.3f}s)")
                                if "node_timings" not in _performance_metrics["workflow_metrics"]:
                                    _performance_metrics["workflow_metrics"]["node_timings"] = {}
                                _performance_metrics["workflow_metrics"]["node_timings"][node_name] = duration
                            else:
                                logger.info(f"✅ Task completed: {node_name}")
                        elif event_name == "error":
                            error = task_event.get("error", "Unknown error")
                            logger.error(f"❌ Task failed: {node_name} - {error}")
                            yield ResponsesAgentStreamEvent(
                                type="response.output_item.done",
                                item=self.create_text_output_item(
                                    text=f"❌ Error in {node_name}: {error}",
                                    id=str(uuid4())
                                ),
                            )
                    except Exception as e:
                        logger.warning(f"Error processing task event: {e}")
        
        workflow_end_time = time.time()
        ttcl = workflow_end_time - workflow_start_time
        _performance_metrics["workflow_metrics"]["ttcl_seconds"].append(ttcl)
        
        logger.info(f"Workflow execution completed (thread: {thread_id})")
        logger.info(f"⏱️  Performance: TTFT={first_token_time - workflow_start_time if first_token_time else 'N/A'}s, TTCL={ttcl:.3f}s")
