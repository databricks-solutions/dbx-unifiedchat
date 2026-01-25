# Databricks notebook source
# MAGIC %md
# MAGIC # Author and deploy an agent with short-term memory using Databricks Lakebase
# MAGIC
# MAGIC
# MAGIC This notebook demonstrates how to build an agent with short-term memory using the Agent Framework and Lakebase as the agent’s durable memory and checkpoint store. 
# MAGIC
# MAGIC Threads allow you to store conversational state in Lakebase so you can pass in thread IDs into your agent instead of needing to send the full conversation history.
# MAGIC
# MAGIC In this notebook, you will:
# MAGIC 1. Author an agent graph with Lakebase to manage state using thread ids in a Databricks Agent 
# MAGIC 2. Wrap the LangGraph agent with `ResponsesAgent` interface to ensure compatibility with Databricks features
# MAGIC 3. Test the agent's behavior locally
# MAGIC 4. Register model to Unity Catalog, log and deploy the agent for use in Review App, Playground, etc
# MAGIC
# MAGIC ## Prerequisites
# MAGIC - A Lakebase instance ready and running, see documentation ([AWS](https://docs.databricks.com/aws/en/oltp/create/) | [Azure](https://learn.microsoft.com/en-us/azure/databricks/oltp/create/)). 
# MAGIC   - You can create a Lakebase instance by going to SQL Warehouses -> Lakebase Postgres -> Create database instance. You will need to retrieve values from the "Connection details" section of your Lakebase to fill out this notebook.
# MAGIC - Complete all the "TODO"s throughout this notebook

# COMMAND ----------

# MAGIC %md
# MAGIC ### Install dependencies

# COMMAND ----------

# DBTITLE 1,or
# MAGIC %pip install -U -qqqq databricks-langchain[memory] uv databricks-agents mlflow-skinny[databricks]
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## First time setup only: Set up checkpoint tables with your Lakebase instance

# COMMAND ----------

# First-time checkpoint table setup
from databricks.sdk import WorkspaceClient
from databricks_langchain import CheckpointSaver

# --- TODO: Fill in Lakebase instance name ---
INSTANCE_NAME = "lakebase-name"

# Create tables if missing
with CheckpointSaver(instance_name=INSTANCE_NAME) as saver:
    saver.setup()           # sets up checkpoint tables
    print("✅ Checkpoint tables are ready.")

# COMMAND ----------

# MAGIC %md
# MAGIC # Define the agent in code
# MAGIC
# MAGIC ## Write agent code to file agent.py
# MAGIC Define the agent code in a single cell below. This lets you write the agent code to a local Python file, using the `%%writefile` magic command, for subsequent logging and deployment.
# MAGIC
# MAGIC ## Wrap the LangGraph agent using the ResponsesAgent interface
# MAGIC For compatibility with Databricks AI features, the `LangGraphResponsesAgent` class implements the `ResponsesAgent` interface to wrap the LangGraph agent.
# MAGIC
# MAGIC Databricks recommends using `ResponsesAgent` as it simplifies authoring multi-turn conversational agents using an open source standard. See MLflow's [ResponsesAgent documentation](https://www.mlflow.org/docs/latest/llms/responses-agent-intro/).

# COMMAND ----------

# MAGIC %%writefile agent.py
# MAGIC import logging
# MAGIC import os
# MAGIC import uuid
# MAGIC from typing import Annotated, Any, Generator, Optional, Sequence, TypedDict
# MAGIC
# MAGIC import mlflow
# MAGIC from databricks_langchain import (
# MAGIC     ChatDatabricks,
# MAGIC     UCFunctionToolkit,
# MAGIC     CheckpointSaver,
# MAGIC )
# MAGIC from databricks.sdk import WorkspaceClient
# MAGIC from langchain_core.messages import AIMessage, AIMessageChunk, AnyMessage
# MAGIC from langchain_core.runnables import RunnableConfig, RunnableLambda
# MAGIC from langgraph.graph import END, StateGraph
# MAGIC from langgraph.graph.message import add_messages
# MAGIC from langgraph.prebuilt.tool_node import ToolNode
# MAGIC from mlflow.pyfunc import ResponsesAgent
# MAGIC from mlflow.types.responses import (
# MAGIC     ResponsesAgentRequest,
# MAGIC     ResponsesAgentResponse,
# MAGIC     ResponsesAgentStreamEvent,
# MAGIC     output_to_responses_items_stream,
# MAGIC )
# MAGIC
# MAGIC logger = logging.getLogger(__name__)
# MAGIC logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
# MAGIC
# MAGIC ############################################
# MAGIC # Define your LLM endpoint and system prompt
# MAGIC ############################################
# MAGIC # TODO: Replace with your model serving endpoint
# MAGIC LLM_ENDPOINT_NAME = "databricks-claude-3-7-sonnet"
# MAGIC
# MAGIC # TODO: Update with your system prompt
# MAGIC SYSTEM_PROMPT = "You are a helpful assistant. Use the available tools to answer questions."
# MAGIC
# MAGIC ############################################
# MAGIC # Lakebase configuration
# MAGIC ############################################
# MAGIC # TODO: Fill in Lakebase instance name
# MAGIC LAKEBASE_INSTANCE_NAME = "lakebase-name"
# MAGIC
# MAGIC ###############################################################################
# MAGIC ## Define tools for your agent,enabling it to retrieve data or take actions
# MAGIC ## beyond text generation
# MAGIC ## To create and see usage examples of more tools, see
# MAGIC ## https://docs.databricks.com/en/generative-ai/agent-framework/agent-tool.html
# MAGIC ###############################################################################
# MAGIC tools = []
# MAGIC
# MAGIC # Example UC tools; add your own as needed
# MAGIC UC_TOOL_NAMES: list[str] = []
# MAGIC if UC_TOOL_NAMES:
# MAGIC     uc_toolkit = UCFunctionToolkit(function_names=UC_TOOL_NAMES)
# MAGIC     tools.extend(uc_toolkit.tools)
# MAGIC
# MAGIC # Use Databricks vector search indexes as tools
# MAGIC # See https://docs.databricks.com/en/generative-ai/agent-framework/unstructured-retrieval-tools.html#locally-develop-vector-search-retriever-tools-with-ai-bridge
# MAGIC # List to store vector search tool instances for unstructured retrieval.
# MAGIC VECTOR_SEARCH_TOOLS = []
# MAGIC
# MAGIC # To add vector search retriever tools,
# MAGIC # use VectorSearchRetrieverTool and create_tool_info,
# MAGIC # then append the result to TOOL_INFOS.
# MAGIC # Example:
# MAGIC # VECTOR_SEARCH_TOOLS.append(
# MAGIC #     VectorSearchRetrieverTool(
# MAGIC #         index_name="",
# MAGIC #         # filters="..."
# MAGIC #     )
# MAGIC # )
# MAGIC
# MAGIC tools.extend(VECTOR_SEARCH_TOOLS)
# MAGIC
# MAGIC #####################
# MAGIC ## Define agent logic
# MAGIC #####################
# MAGIC
# MAGIC class AgentState(TypedDict):
# MAGIC     messages: Annotated[Sequence[AnyMessage], add_messages]
# MAGIC     custom_inputs: Optional[dict[str, Any]]
# MAGIC     custom_outputs: Optional[dict[str, Any]]
# MAGIC
# MAGIC class LangGraphResponsesAgent(ResponsesAgent):
# MAGIC     """Stateful agent using ResponsesAgent with pooled Lakebase checkpointing."""
# MAGIC
# MAGIC     def __init__(self, lakebase_config: dict[str, Any]):
# MAGIC         self.workspace_client = WorkspaceClient()
# MAGIC
# MAGIC         self.model = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME)
# MAGIC         self.system_prompt = SYSTEM_PROMPT
# MAGIC         self.model_with_tools = self.model.bind_tools(tools) if tools else self.model
# MAGIC
# MAGIC     def _create_graph(self, checkpointer: Any):
# MAGIC         def should_continue(state: AgentState):
# MAGIC             messages = state["messages"]
# MAGIC             last_message = messages[-1]
# MAGIC             if isinstance(last_message, AIMessage) and last_message.tool_calls:
# MAGIC                 return "continue"
# MAGIC             return "end"
# MAGIC
# MAGIC         preprocessor = (
# MAGIC             RunnableLambda(lambda state: [{"role": "system", "content": self.system_prompt}] + state["messages"])
# MAGIC             if self.system_prompt
# MAGIC             else RunnableLambda(lambda state: state["messages"])
# MAGIC         )
# MAGIC         model_runnable = preprocessor | self.model_with_tools
# MAGIC
# MAGIC         def call_model(state: AgentState, config: RunnableConfig):
# MAGIC             response = model_runnable.invoke(state, config)
# MAGIC             return {"messages": [response]}
# MAGIC
# MAGIC         workflow = StateGraph(AgentState)
# MAGIC         workflow.add_node("agent", RunnableLambda(call_model))
# MAGIC
# MAGIC         if tools:
# MAGIC             workflow.add_node("tools", ToolNode(tools))
# MAGIC             workflow.add_conditional_edges("agent", should_continue, {"continue": "tools", "end": END})
# MAGIC             workflow.add_edge("tools", "agent")
# MAGIC         else:
# MAGIC             workflow.add_edge("agent", END)
# MAGIC
# MAGIC         workflow.set_entry_point("agent")
# MAGIC         return workflow.compile(checkpointer=checkpointer)
# MAGIC
# MAGIC     def _get_or_create_thread_id(self, request: ResponsesAgentRequest) -> str:
# MAGIC         """Get thread_id from request or create a new one.
# MAGIC
# MAGIC         Priority:
# MAGIC         1. Use thread_id from custom_inputs if present
# MAGIC         2. Use conversation_id from chat context if available
# MAGIC         3. Generate a new UUID
# MAGIC
# MAGIC         Returns:
# MAGIC             thread_id: The thread identifier to use for this conversation
# MAGIC         """
# MAGIC         ci = dict(request.custom_inputs or {})
# MAGIC
# MAGIC         if "thread_id" in ci:
# MAGIC             return ci["thread_id"]
# MAGIC
# MAGIC         # using conversation id from chat context as thread id
# MAGIC         # https://mlflow.org/docs/latest/api_reference/python_api/mlflow.types.html#mlflow.types.agent.ChatContext
# MAGIC         if request.context and getattr(request.context, "conversation_id", None):
# MAGIC             return request.context.conversation_id
# MAGIC
# MAGIC         # Generate new thread_id
# MAGIC         return str(uuid.uuid4())
# MAGIC
# MAGIC     def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
# MAGIC         outputs = [
# MAGIC             event.item
# MAGIC             for event in self.predict_stream(request)
# MAGIC             if event.type == "response.output_item.done"
# MAGIC         ]
# MAGIC         return ResponsesAgentResponse(output=outputs, custom_outputs=request.custom_inputs)
# MAGIC
# MAGIC     def predict_stream(
# MAGIC         self, request: ResponsesAgentRequest
# MAGIC     ) -> Generator[ResponsesAgentStreamEvent, None, None]:
# MAGIC         thread_id = self._get_or_create_thread_id(request)
# MAGIC         ci = dict(request.custom_inputs or {})
# MAGIC         ci["thread_id"] = thread_id
# MAGIC         request.custom_inputs = ci
# MAGIC
# MAGIC         # Convert incoming Responses messages to ChatCompletions format
# MAGIC         # LangChain will automatically convert from ChatCompletions to LangChain format
# MAGIC         cc_msgs = self.prep_msgs_for_cc_llm([i.model_dump() for i in request.input])
# MAGIC         langchain_msgs = cc_msgs
# MAGIC         checkpoint_config = {"configurable": {"thread_id": thread_id}}
# MAGIC
# MAGIC         with CheckpointSaver(instance_name=LAKEBASE_INSTANCE_NAME) as checkpointer:
# MAGIC             graph = self._create_graph(checkpointer)
# MAGIC
# MAGIC             for event in graph.stream(
# MAGIC                 {"messages": langchain_msgs},
# MAGIC                 checkpoint_config,
# MAGIC                 stream_mode=["updates", "messages"],
# MAGIC             ):
# MAGIC                 if event[0] == "updates":
# MAGIC                     for node_data in event[1].values():
# MAGIC                         if len(node_data.get("messages", [])) > 0:
# MAGIC                             yield from output_to_responses_items_stream(node_data["messages"])
# MAGIC                 elif event[0] == "messages":
# MAGIC                     try:
# MAGIC                         chunk = event[1][0]
# MAGIC                         if isinstance(chunk, AIMessageChunk) and chunk.content:
# MAGIC                             yield ResponsesAgentStreamEvent(
# MAGIC                                 **self.create_text_delta(delta=chunk.content, item_id=chunk.id),
# MAGIC                             )
# MAGIC                     except Exception as exc:
# MAGIC                         logger.error("Error streaming chunk: %s", exc)
# MAGIC
# MAGIC
# MAGIC # ----- Export model -----
# MAGIC mlflow.langchain.autolog()
# MAGIC AGENT = LangGraphResponsesAgent(LAKEBASE_INSTANCE_NAME)
# MAGIC mlflow.models.set_model(AGENT)

# COMMAND ----------

# MAGIC %md
# MAGIC # Test the Agent locally

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

from agent import AGENT
# Message 1, don't include thread_id (creates new thread)
result = AGENT.predict({
    "input": [{"role": "user", "content": "I am working on stateful agents"}]
})
print(result.model_dump(exclude_none=True))
thread_id = result.custom_outputs["thread_id"]

# COMMAND ----------

# Message 2, include thread ID and notice how agent remembers context from previous predict message
response2 = AGENT.predict({
    "input": [{"role": "user", "content": "What am I working on?"}],
    "custom_inputs": {"thread_id": thread_id}
})
print("Response 2:", response2.model_dump(exclude_none=True))

# COMMAND ----------

# Example calling agent without passing in thread id - notice it does not retain the memory
response3 = AGENT.predict({
    "input": [{"role": "user", "content": "What am I working on?"}],
})
print("Response 3 No thread id passed:", response3.model_dump(exclude_none=True))

# COMMAND ----------

# predict stream example
for chunk in AGENT.predict_stream({
    "input": [{"role": "user", "content": "What am I working on?"}],
    "custom_inputs": {"thread_id": thread_id}
}):
    print("Chunk:", chunk.model_dump(exclude_none=True))

# COMMAND ----------

# example using conversation_id from ChatContext as thread_id
# https://mlflow.org/docs/latest/api_reference/python_api/mlflow.types.html#mlflow.types.agent.ChatContext
from agent import AGENT
import mlflow
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ChatContext
)

conversation_id = "e396d36f-b237-484f-ad6e-f000551703f5"

req = ResponsesAgentRequest(
    input=[{"role": "user", "content": "I am working on stateful agents"}],
    context=ChatContext(
        conversation_id=conversation_id,
        user_id="email@databricks.com"
    )
)
result = AGENT.predict(req)

print(result.model_dump(exclude_none=True))
thread_id = result.custom_outputs["thread_id"]
print(f"Resolved thread_id from agent: {thread_id}")

# COMMAND ----------

# MAGIC %md
# MAGIC # Log the agent as an MLflow model
# MAGIC Log the agent as code from the agent.py file. See [MLflow - Models from Code](https://mlflow.org/docs/latest/models.html#models-from-code).
# MAGIC
# MAGIC ## Enable automatic authentication for Databricks resources
# MAGIC For the most common Databricks resource types, Databricks supports and recommends declaring resource dependencies for the agent upfront during logging. This enables automatic authentication passthrough when you deploy the agent. With automatic authentication passthrough, Databricks automatically provisions, rotates, and manages short-lived credentials to securely access these resource dependencies from within the agent endpoint.
# MAGIC
# MAGIC To enable automatic authentication, specify the dependent Databricks resources when calling `mlflow.pyfunc.log_model()`.
# MAGIC
# MAGIC **TODO:** 
# MAGIC - Add lakebase as a resource type
# MAGIC - If your Unity Catalog tool queries a [vector search index](https://docs.databricks.com/docs%20link) or leverages [external functions](https://docs.databricks.com/docs%20link), you need to include the dependent vector search index and UC connection objects, respectively, as resources. See docs ([AWS](https://docs.databricks.com/generative-ai/agent-framework/log-agent.html#specify-resources-for-automatic-authentication-passthrough) | [Azure](https://learn.microsoft.com/azure/databricks/generative-ai/agent-framework/log-agent#resources)).

# COMMAND ----------

# Determine Databricks resources to specify for automatic auth passthrough at deployment time
import mlflow
from agent import tools, LLM_ENDPOINT_NAME, LAKEBASE_INSTANCE_NAME
from databricks_langchain import VectorSearchRetrieverTool
from mlflow.models.resources import DatabricksFunction, DatabricksServingEndpoint, DatabricksLakebase
from unitycatalog.ai.langchain.toolkit import UnityCatalogTool
from pkg_resources import get_distribution

resources = [DatabricksServingEndpoint(LLM_ENDPOINT_NAME), DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE_NAME)]

for tool in tools:
    if isinstance(tool, VectorSearchRetrieverTool):
        resources.extend(tool.resources)
    elif isinstance(tool, UnityCatalogTool):
        resources.append(DatabricksFunction(function_name=tool.uc_function_name))

input_example = {
    "input": [
        {
            "role": "user",
            "content": "What is an LLM agent?"
        }
    ],
    "custom_inputs": {"thread_id": "example-thread-123"},
}

with mlflow.start_run():
    logged_agent_info = mlflow.pyfunc.log_model(
        name="agent",
        python_model="agent.py",
        input_example=input_example,
        resources=resources,
        pip_requirements=[
            f"databricks-langchain[memory]=={get_distribution('databricks-langchain[memory]').version}",
        ]
    )

# COMMAND ----------

# MAGIC %md
# MAGIC # Evaluate the agent with Agent Evaluation
# MAGIC Use Mosaic AI Agent Evaluation to evalaute the agent's responses based on expected responses and other evaluation criteria. Use the evaluation criteria you specify to guide iterations, using MLflow to track the computed quality metrics. See Databricks documentation ([AWS](https://docs.databricks.com/(https://docs.databricks.com/aws/generative-ai/agent-evaluation) | [Azure](https://learn.microsoft.com/azure/databricks/generative-ai/agent-evaluation/)).
# MAGIC
# MAGIC To evaluate your tool calls, add custom metrics. See Databricks documentation ([AWS](https://docs.databricks.com/en/generative-ai/agent-evaluation/custom-metrics.html#evaluating-tool-calls) | [Azure](https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-evaluation/custom-metrics#evaluating-tool-calls)).

# COMMAND ----------

import mlflow
from mlflow.genai.scorers import RelevanceToQuery, RetrievalGroundedness, RetrievalRelevance, Safety

eval_dataset = [
    {
        "inputs": {"input": [{"role": "user", "content": "Calculate the 15th Fibonacci number"}]},
        "expected_response": "The 15th Fibonacci number is 610.",
    }
]

eval_results = mlflow.genai.evaluate(
    data=eval_dataset,
    predict_fn=lambda input: AGENT.predict({"input": input}),
    scorers=[RelevanceToQuery(), Safety()],  # add more scorers here if they're applicable
)

# Review the evaluation results in the MLfLow UI (see console output)

# COMMAND ----------

# MAGIC %md
# MAGIC # Pre-deployment agent validation
# MAGIC Before registering and deploying the agent, perform pre-deployment checks using the mlflow.models.predict() API.

# COMMAND ----------

mlflow.models.predict(
    model_uri=f"runs:/{logged_agent_info.run_id}/agent",
    input_data={"input": [{"role": "user", "content": "I am working on stateful agents"}]},
    env_manager="uv",
)

# COMMAND ----------

# MAGIC %md
# MAGIC # Register the model to Unity Catalog
# MAGIC Update the `catalog`, `schema`, and `model_name` below to register the MLflow model to Unity Catalog.

# COMMAND ----------

mlflow.set_registry_uri("databricks-uc")

# TODO: define the catalog, schema, and model name for your UC model
catalog = "catalog"
schema = "schema"
model_name = "short-term-memory-agent"

UC_MODEL_NAME = f"{catalog}.{schema}.{model_name}"

# register the model to UC
uc_registered_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri, name=UC_MODEL_NAME
)

# COMMAND ----------

# MAGIC %md
# MAGIC Deploy the agent

# COMMAND ----------

from databricks import agents
agents.deploy(UC_MODEL_NAME, uc_registered_model_info.version, tags = {"endpointSource": "docs"}, deploy_feedback_model=False)

# COMMAND ----------

# MAGIC %md
# MAGIC # Next steps
# MAGIC It will take around 15 minutes for you to finish deploying your agent. After your agent is deployed, you can chat with it in Review App/playground to perform additional checks, share it with SMEs in your organization for feedback, or embed it in a production application. 
# MAGIC
# MAGIC Query your Lakebase instance to see a record of your conversation at various threads/checkpoints. Here is a basic query to see the 10 most recent checkpoints:
# MAGIC
# MAGIC ```
# MAGIC SELECT
# MAGIC     c.*,
# MAGIC     (c.checkpoint::json->>'ts')::timestamptz AS ts
# MAGIC FROM checkpoints c
# MAGIC ORDER BY ts DESC
# MAGIC LIMIT 10;
# MAGIC ```
