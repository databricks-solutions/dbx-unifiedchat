# Databricks notebook source
# MAGIC %md
# MAGIC # Mosaic AI Agent Framework: Author and deploy a multi-agent system with Genie and Serving Endpoints
# MAGIC
# MAGIC This notebook demonstrates how to build a multi-agent system using Mosaic AI Agent Framework and [LangGraph](https://blog.langchain.dev/langgraph-multi-agent-workflows/), where [Genie](https://www.databricks.com/product/ai-bi/genie) is one of the agents.
# MAGIC In this notebook, you:
# MAGIC 1. Author a multi-agent system using LangGraph.
# MAGIC 1. Wrap the LangGraph agent with MLflow `ResponsesAgent` to ensure compatibility with Databricks features.
# MAGIC 1. Manually test the multi-agent system's output.
# MAGIC 1. Log and deploy the multi-agent system.
# MAGIC
# MAGIC This example is based on [LangGraph documentation - Multi-agent supervisor example](https://github.com/langchain-ai/langgraph/blob/main/docs/docs/tutorials/multi_agent/agent_supervisor.md)
# MAGIC
# MAGIC ## Why use a Genie agent?
# MAGIC
# MAGIC Multi-agent systems consist of multiple AI agents working together, each with specialized capabilities. As one of those agents, Genie allows users to interact with their structured data using natural language. Unlike SQL functions which can only run pre-defined queries, Genie has the flexibility to create novel queries to answer user questions.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC
# MAGIC - Address all `TODO`s in this notebook.
# MAGIC - Create a Genie Space, see Databricks documentation ([AWS](https://docs.databricks.com/aws/genie/set-up) | [Azure](https://learn.microsoft.com/azure/databricks/genie/set-up)).

# COMMAND ----------

# MAGIC %pip install -U -qqq langgraph-supervisor==0.0.30 mlflow[databricks] databricks-langchain databricks-agents uv 
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC ## Define the multi-agent system
# MAGIC
# MAGIC Create a multi-agent system in LangGraph using a supervisor agent node with one or more of the following subagents:
# MAGIC - **GenieAgent**: A LangChain runnable that allows you to easily interact with your Genie Space to query structured data.
# MAGIC - **Custom serving agent**: An agent that is already hosted as an existing endpoint on Databricks.
# MAGIC - **In-code tool-calling agent**: An agent that calls Unity Catalog function tools, defined within this notebook. This example uses `system.ai.python_exec`, but for examples of other tools you can add to your agents, see Databricks documentation ([AWS](https://docs.databricks.com/aws/generative-ai/agent-framework/agent-tool) | [Azure](https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/agent-tool)).
# MAGIC
# MAGIC The supervisor agent is responsible for creating and routing tool calls to each of your subagents, passing only the context necessary. You can modify this behavior and pass along the entire message history if desired. See the [LangGraph docs](https://langchain-ai.github.io/langgraph/reference/supervisor/) for more information.
# MAGIC
# MAGIC ### Write agent code to file
# MAGIC
# MAGIC Define the agent code in a single cell below. This lets you write the agent code to a local Python file, using the `%%writefile` magic command, for subsequent logging and deployment.

# COMMAND ----------

# MAGIC %%writefile agent.py
# MAGIC import json
# MAGIC from typing import Generator, Literal
# MAGIC from uuid import uuid4
# MAGIC
# MAGIC import mlflow
# MAGIC from databricks_langchain import (
# MAGIC     ChatDatabricks,
# MAGIC     DatabricksFunctionClient,
# MAGIC     UCFunctionToolkit,
# MAGIC     set_uc_function_client,
# MAGIC )
# MAGIC from databricks_langchain.genie import GenieAgent
# MAGIC from langchain_core.runnables import Runnable
# MAGIC from langchain.agents import create_agent
# MAGIC from langgraph.graph.state import CompiledStateGraph
# MAGIC from langgraph_supervisor import create_supervisor
# MAGIC from mlflow.pyfunc import ResponsesAgent
# MAGIC from mlflow.types.responses import (
# MAGIC     ResponsesAgentRequest,
# MAGIC     ResponsesAgentResponse,
# MAGIC     ResponsesAgentStreamEvent,
# MAGIC     output_to_responses_items_stream,
# MAGIC     to_chat_completions_input,
# MAGIC )
# MAGIC from pydantic import BaseModel
# MAGIC
# MAGIC client = DatabricksFunctionClient()
# MAGIC set_uc_function_client(client)
# MAGIC
# MAGIC ########################################
# MAGIC # Create your LangGraph Supervisor Agent
# MAGIC ########################################
# MAGIC
# MAGIC GENIE = "genie"
# MAGIC
# MAGIC
# MAGIC class ServedSubAgent(BaseModel):
# MAGIC     endpoint_name: str
# MAGIC     name: str
# MAGIC     task: Literal["agent/v1/responses", "agent/v1/chat", "agent/v2/chat"]
# MAGIC     description: str
# MAGIC
# MAGIC
# MAGIC class Genie(BaseModel):
# MAGIC     space_id: str
# MAGIC     name: str
# MAGIC     task: str = GENIE
# MAGIC     description: str
# MAGIC
# MAGIC
# MAGIC class InCodeSubAgent(BaseModel):
# MAGIC     tools: list[str]
# MAGIC     name: str
# MAGIC     description: str
# MAGIC
# MAGIC
# MAGIC TOOLS = []
# MAGIC
# MAGIC
# MAGIC def stringify_content(state):
# MAGIC     msgs = state["messages"]
# MAGIC     if isinstance(msgs[-1].content, list):
# MAGIC         msgs[-1].content = json.dumps(msgs[-1].content, indent=4)
# MAGIC     return {"messages": msgs}
# MAGIC
# MAGIC
# MAGIC def create_langgraph_supervisor(
# MAGIC     llm: Runnable,
# MAGIC     externally_served_agents: list[ServedSubAgent] = [],
# MAGIC     in_code_agents: list[InCodeSubAgent] = [],
# MAGIC ):
# MAGIC     agents = []
# MAGIC     agent_descriptions = ""
# MAGIC
# MAGIC     # Process inline code agents
# MAGIC     for agent in in_code_agents:
# MAGIC         agent_descriptions += f"- {agent.name}: {agent.description}\n"
# MAGIC         uc_toolkit = UCFunctionToolkit(function_names=agent.tools)
# MAGIC         TOOLS.extend(uc_toolkit.tools)
# MAGIC         agents.append(create_agent(llm, tools=uc_toolkit.tools, name=agent.name))
# MAGIC
# MAGIC     # Process served endpoints and Genie Spaces
# MAGIC     for agent in externally_served_agents:
# MAGIC         agent_descriptions += f"- {agent.name}: {agent.description}\n"
# MAGIC         if isinstance(agent, Genie):
# MAGIC             # to better control the messages sent to the genie agent, you can use the `message_processor` param: https://api-docs.databricks.com/python/databricks-ai-bridge/latest/databricks_langchain.html#databricks_langchain.GenieAgent
# MAGIC             genie_agent = GenieAgent(
# MAGIC                 genie_space_id=agent.space_id,
# MAGIC                 genie_agent_name=agent.name,
# MAGIC                 description=agent.description,
# MAGIC             )
# MAGIC             genie_agent.name = agent.name
# MAGIC             agents.append(genie_agent)
# MAGIC         else:
# MAGIC             model = ChatDatabricks(
# MAGIC                 endpoint=agent.endpoint_name, use_responses_api="responses" in agent.task
# MAGIC             )
# MAGIC             # Disable streaming for subagents for ease of parsing
# MAGIC             model._stream = lambda x: model._stream(**x, stream=False)
# MAGIC             agents.append(
# MAGIC                 create_agent(
# MAGIC                     model,
# MAGIC                     tools=[],
# MAGIC                     name=agent.name,
# MAGIC                     post_model_hook=stringify_content,
# MAGIC                 )
# MAGIC             )
# MAGIC
# MAGIC     # TODO: The supervisor prompt includes agent names/descriptions as well as general
# MAGIC     # instructions. You can modify this to improve quality or provide custom instructions.
# MAGIC     prompt = f"""
# MAGIC     You are a supervisor in a multi-agent system.
# MAGIC
# MAGIC     1. Understand the user's last request
# MAGIC     2. Read through the entire chat history.
# MAGIC     3. If the answer to the user's last request is present in chat history, answer using information in the history.
# MAGIC     4. If the answer is not in the history, from the below list of agents, determine which agent is best suited to answer the question.
# MAGIC     5. Provide a summarized response to the user's last query, even if it's been answered before.
# MAGIC
# MAGIC     {agent_descriptions}"""
# MAGIC
# MAGIC     return create_supervisor(
# MAGIC         agents=agents,
# MAGIC         model=llm,
# MAGIC         prompt=prompt,
# MAGIC         add_handoff_messages=False,
# MAGIC         output_mode="full_history",
# MAGIC     ).compile()
# MAGIC
# MAGIC
# MAGIC ##########################################
# MAGIC # Wrap LangGraph Supervisor as a ResponsesAgent
# MAGIC ##########################################
# MAGIC
# MAGIC
# MAGIC class LangGraphResponsesAgent(ResponsesAgent):
# MAGIC     def __init__(self, agent: CompiledStateGraph):
# MAGIC         self.agent = agent
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
# MAGIC         self,
# MAGIC         request: ResponsesAgentRequest,
# MAGIC     ) -> Generator[ResponsesAgentStreamEvent, None, None]:
# MAGIC         cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
# MAGIC         first_message = True
# MAGIC         seen_ids = set()
# MAGIC
# MAGIC         # can adjust `recursion_limit` to limit looping: https://docs.langchain.com/oss/python/langgraph/GRAPH_RECURSION_LIMIT#troubleshooting
# MAGIC         for _, events in self.agent.stream({"messages": cc_msgs}, stream_mode=["updates"]):
# MAGIC             new_msgs = [
# MAGIC                 msg
# MAGIC                 for v in events.values()
# MAGIC                 for msg in v.get("messages", [])
# MAGIC                 if msg.id not in seen_ids
# MAGIC             ]
# MAGIC             if first_message:
# MAGIC                 seen_ids.update(msg.id for msg in new_msgs[: len(cc_msgs)])
# MAGIC                 new_msgs = new_msgs[len(cc_msgs) :]
# MAGIC                 first_message = False
# MAGIC             else:
# MAGIC                 seen_ids.update(msg.id for msg in new_msgs)
# MAGIC                 node_name = tuple(events.keys())[0]  # assumes one name per node
# MAGIC                 yield ResponsesAgentStreamEvent(
# MAGIC                     type="response.output_item.done",
# MAGIC                     item=self.create_text_output_item(
# MAGIC                         text=f"<name>{node_name}</name>", id=str(uuid4())
# MAGIC                     ),
# MAGIC                 )
# MAGIC             if len(new_msgs) > 0:
# MAGIC                 yield from output_to_responses_items_stream(new_msgs)
# MAGIC
# MAGIC
# MAGIC #######################################################
# MAGIC # Configure the Foundation Model and Serving Sub-Agents
# MAGIC #######################################################
# MAGIC
# MAGIC # TODO: Replace with your model serving endpoint
# MAGIC LLM_ENDPOINT_NAME = "databricks-claude-sonnet-4-5"
# MAGIC llm = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME)
# MAGIC
# MAGIC # TODO: Add the necessary information about each of your subagents. Subagents could be agents deployed to Model Serving endpoints or Genie Space subagents.
# MAGIC # Your agent descriptions are crucial for improving quality. Include as much detail as possible.
# MAGIC EXTERNALLY_SERVED_AGENTS = [
# MAGIC     Genie(
# MAGIC         space_id="<your_genie_space_id>",
# MAGIC         name="<your-genie-name>",
# MAGIC         description="This agent can answer questions...",
# MAGIC     ),
# MAGIC     # ServedSubAgent(
# MAGIC     #     endpoint_name="cities-agent",
# MAGIC     #     name="city-agent", # choose a semantically relevant name for your agent
# MAGIC     #     task="agent/v1/responses",
# MAGIC     #     description="This agent can answer questions about the best cities to visit in the world.",
# MAGIC     # ),
# MAGIC ]
# MAGIC
# MAGIC ############################################################
# MAGIC # Create additional agents in code
# MAGIC ############################################################
# MAGIC
# MAGIC # TODO: Fill the following with UC function-calling agents. The tools parameter is a list of UC function names that you want your agent to call.
# MAGIC IN_CODE_AGENTS = [
# MAGIC     InCodeSubAgent(
# MAGIC         tools=["system.ai.*"],
# MAGIC         name="code execution agent",
# MAGIC         description="The code execution agent specializes in solving programming challenges, generating code snippets, debugging issues, and explaining complex coding concepts.",
# MAGIC     )
# MAGIC ]
# MAGIC
# MAGIC #################################################
# MAGIC # Create supervisor and set up MLflow for tracing
# MAGIC #################################################
# MAGIC
# MAGIC supervisor = create_langgraph_supervisor(llm, EXTERNALLY_SERVED_AGENTS, IN_CODE_AGENTS)
# MAGIC
# MAGIC mlflow.langchain.autolog()
# MAGIC AGENT = LangGraphResponsesAgent(supervisor)
# MAGIC mlflow.models.set_model(AGENT)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test the agent
# MAGIC
# MAGIC Interact with the agent to test its output. Since this notebook called `mlflow.langchain.autolog()` you can view the trace for each step the agent takes.
# MAGIC
# MAGIC Even if you didn't add any subagents in the agent definition above, the supervisor agent can still answer questions. It just won't have any subagents to switch to.
# MAGIC
# MAGIC **Important:** LangGraph internally uses exceptions (something like `Command` or `ParentCommand`) to switch between nodes. These particular exceptions may appear in your MLflow traces as Events, but this behavior is expected and should not be a cause for concern.

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

from agent import AGENT

# TODO: Replace this placeholder `input_example` with a domain-specific prompt for your agent.
input_example = {
    "input": [
        {"role": "user", "content": "what tools do you have access to"}
    ]
}


AGENT.predict(input_example)

# COMMAND ----------

for event in AGENT.predict_stream(input_example):
  print(event.model_dump(exclude_none=True))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Log the agent as an MLflow model
# MAGIC
# MAGIC Log the agent as code from the `agent.py` file. See [MLflow - Models from Code](https://mlflow.org/docs/latest/models.html#models-from-code).
# MAGIC
# MAGIC ### Enable automatic authentication for Databricks resources
# MAGIC For the most common Databricks resource types, Databricks supports and recommends declaring resource dependencies for the agent upfront during logging. This enables automatic authentication passthrough when you deploy the agent. With automatic authentication passthrough, Databricks automatically provisions, rotates, and manages short-lived credentials to securely access these resource dependencies from within the agent endpoint.
# MAGIC
# MAGIC To enable automatic authentication, specify the dependent Databricks resources when calling `mlflow.pyfunc.log_model().`
# MAGIC   - **TODO**: If your Unity Catalog tool queries a [vector search index](docs link) or leverages [external functions](docs link), you need to include the dependent vector search index and UC connection objects, respectively, as resources. See docs ([AWS](https://docs.databricks.com/aws/generative-ai/agent-framework/agent-authentication#supported-resources-for-automatic-authentication-passthrough) | [Azure](https://docs.databricks.com/aws/generative-ai/agent-framework/agent-authentication#supported-resources-for-automatic-authentication-passthrough)).
# MAGIC
# MAGIC   - **TODO**: Add the SQL Warehouse or tables powering your Genie space to enable passthrough authentication. ([AWS](https://docs.databricks.com/aws/generative-ai/agent-framework/agent-authentication#supported-resources-for-automatic-authentication-passthrough) | [Azure](https://docs.databricks.com/aws/generative-ai/agent-framework/agent-authentication#supported-resources-for-automatic-authentication-passthrough)). If your genie space uses "embedded credentials" then you do not have to add this.

# COMMAND ----------

# Determine Databricks resources to specify for automatic auth passthrough at deployment time
import mlflow
from agent import EXTERNALLY_SERVED_AGENTS, LLM_ENDPOINT_NAME, TOOLS, Genie
from databricks_langchain import UnityCatalogTool, VectorSearchRetrieverTool
from mlflow.models.resources import (
    DatabricksFunction,
    DatabricksGenieSpace,
    DatabricksServingEndpoint,
    DatabricksSQLWarehouse,
    DatabricksTable
)
from pkg_resources import get_distribution

# TODO: Manually include underlying resources if needed. See the TODO in the markdown above for more information.
resources = [DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT_NAME)]
# TODO: Add SQL Warehouses and delta tables powering the Genie Space
resources.append(DatabricksSQLWarehouse(warehouse_id="<your_warehouse_id>"))
resources.append(DatabricksTable(table_name="<your_catalog>.<schema>.<table_name>"))

# Add tools from Unity Catalog
for tool in TOOLS:
    if isinstance(tool, VectorSearchRetrieverTool):
        resources.extend(tool.resources)
    elif isinstance(tool, UnityCatalogTool):
        resources.append(DatabricksFunction(function_name=tool.uc_function_name))

# Add serving endpoints and Genie Spaces
for agent in EXTERNALLY_SERVED_AGENTS:
    if isinstance(agent, Genie):
        resources.append(DatabricksGenieSpace(genie_space_id=agent.space_id))
    else:
        resources.append(DatabricksServingEndpoint(endpoint_name=agent.endpoint_name))

with mlflow.start_run():
    logged_agent_info = mlflow.pyfunc.log_model(
        name="agent",
        python_model="agent.py",
        resources=resources,
        pip_requirements=[
            f"databricks-connect=={get_distribution('databricks-connect').version}",
            f"mlflow=={get_distribution('mlflow').version}",
            f"databricks-langchain=={get_distribution('databricks-langchain').version}",
            f"langgraph=={get_distribution('langgraph').version}",
            f"langgraph-supervisor=={get_distribution('langgraph-supervisor').version}",
        ],
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pre-deployment agent validation
# MAGIC Before registering and deploying the agent, perform pre-deployment checks using the [mlflow.models.predict()](https://mlflow.org/docs/latest/python_api/mlflow.models.html#mlflow.models.predict) API. See Databricks documentation ([AWS](https://docs.databricks.com/en/machine-learning/model-serving/model-serving-debug.html#validate-inputs) | [Azure](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/model-serving-debug#before-model-deployment-validation-checks)).

# COMMAND ----------

import mlflow
mlflow.models.predict(
    model_uri=f"runs:/{logged_agent_info.run_id}/agent",
    input_data=input_example,
    env_manager="uv",
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Register the model to Unity Catalog
# MAGIC
# MAGIC Update the `catalog`, `schema`, and `model_name` below to register the MLflow model to Unity Catalog.

# COMMAND ----------

mlflow.set_registry_uri("databricks-uc")

# TODO: define the catalog, schema, and model name for your UC model
catalog = ""
schema = ""
model_name = ""
UC_MODEL_NAME = f"{catalog}.{schema}.{model_name}"

# register the model to UC
uc_registered_model_info = mlflow.register_model(
    model_uri=logged_agent_info.model_uri, name=UC_MODEL_NAME
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Deploy the agent

# COMMAND ----------

from databricks import agents

agents.deploy(UC_MODEL_NAME, uc_registered_model_info.version, tags={"endpointSource": "docs"}, deploy_feedback_model=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next steps
# MAGIC
# MAGIC After your agent is deployed, you can chat with it in AI playground to perform additional checks, share it with SMEs in your organization for feedback, or embed it in a production application. See Databricks documentation ([AWS](https://docs.databricks.com/en/generative-ai/deploy-agent.html) | [Azure](https://learn.microsoft.com/en-us/azure/databricks/generative-ai/deploy-agent)).