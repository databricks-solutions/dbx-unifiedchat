import json
from typing import Generator, Literal
from uuid import uuid4

import mlflow
from databricks_langchain import (
    ChatDatabricks,
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
)
from databricks_langchain.genie import GenieAgent
from langchain_core.runnables import Runnable
from langchain.agents import create_agent
from langgraph.graph.state import CompiledStateGraph
from langgraph_supervisor import create_supervisor
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    output_to_responses_items_stream,
    to_chat_completions_input,
)
from pydantic import BaseModel

client = DatabricksFunctionClient()
set_uc_function_client(client)

########################################
# Create your LangGraph Supervisor Agent
########################################

GENIE = "genie"


class ServedSubAgent(BaseModel):
    endpoint_name: str
    name: str
    task: Literal["agent/v1/responses", "agent/v1/chat", "agent/v2/chat"]
    description: str


class Genie(BaseModel):
    space_id: str
    name: str
    task: str = GENIE
    description: str


class InCodeSubAgent(BaseModel):
    tools: list[str]
    name: str
    description: str


TOOLS = []


def stringify_content(state):
    msgs = state["messages"]
    if isinstance(msgs[-1].content, list):
        msgs[-1].content = json.dumps(msgs[-1].content, indent=4)
    return {"messages": msgs}


def create_langgraph_supervisor(
    llm: Runnable,
    externally_served_agents: list[ServedSubAgent] = [],
    in_code_agents: list[InCodeSubAgent] = [],
):
    agents = []
    agent_descriptions = ""

    # Process inline code agents
    for agent in in_code_agents:
        agent_descriptions += f"- {agent.name}: {agent.description}\n"
        uc_toolkit = UCFunctionToolkit(function_names=agent.tools)
        TOOLS.extend(uc_toolkit.tools)
        agents.append(create_agent(llm, tools=uc_toolkit.tools, name=agent.name))

    # Process served endpoints and Genie Spaces
    for agent in externally_served_agents:
        agent_descriptions += f"- {agent.name}: {agent.description}\n"
        if isinstance(agent, Genie):
            # to better control the messages sent to the genie agent, you can use the `message_processor` param: https://api-docs.databricks.com/python/databricks-ai-bridge/latest/databricks_langchain.html#databricks_langchain.GenieAgent
            genie_agent = GenieAgent(
                genie_space_id=agent.space_id,
                genie_agent_name=agent.name,
                description=agent.description,
            )
            genie_agent.name = agent.name
            agents.append(genie_agent)
        else:
            model = ChatDatabricks(
                endpoint=agent.endpoint_name, use_responses_api="responses" in agent.task
            )
            # Disable streaming for subagents for ease of parsing
            model._stream = lambda x: model._stream(**x, stream=False)
            agents.append(
                create_agent(
                    model,
                    tools=[],
                    name=agent.name,
                    post_model_hook=stringify_content,
                )
            )

    # TODO: The supervisor prompt includes agent names/descriptions as well as general
    # instructions. You can modify this to improve quality or provide custom instructions.
    prompt = f"""
    You are a supervisor in a multi-agent system.

    1. Understand the user's last request
    2. Read through the entire chat history.
    3. If the answer to the user's last request is present in chat history, answer using information in the history.
    4. If the answer is not in the history, from the below list of agents, determine which agents are best suited to answer the question.
    5, Follow the agent route of “plan → choose tools → execute → reflect → respond.” 
    6, Show the thinking and planning process.
    5. Provide a summarized response to the user's last query, even if it's been answered before.

    {agent_descriptions}"""

    return create_supervisor(
        agents=agents,
        model=llm,
        prompt=prompt,
        add_handoff_messages=False,
        output_mode="full_history",
    ).compile()


##########################################
# Wrap LangGraph Supervisor as a ResponsesAgent
##########################################


class LangGraphResponsesAgent(ResponsesAgent):
    def __init__(self, agent: CompiledStateGraph):
        self.agent = agent

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        outputs = [
            event.item
            for event in self.predict_stream(request)
            if event.type == "response.output_item.done"
        ]
        return ResponsesAgentResponse(output=outputs, custom_outputs=request.custom_inputs)

    def predict_stream(
        self,
        request: ResponsesAgentRequest,
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
        first_message = True
        seen_ids = set()

        # can adjust `recursion_limit` to limit looping: https://docs.langchain.com/oss/python/langgraph/GRAPH_RECURSION_LIMIT#troubleshooting
        for _, events in self.agent.stream({"messages": cc_msgs}, stream_mode=["updates"]):
            new_msgs = [
                msg
                for v in events.values()
                for msg in v.get("messages", [])
                if msg.id not in seen_ids
            ]
            if first_message:
                seen_ids.update(msg.id for msg in new_msgs[: len(cc_msgs)])
                new_msgs = new_msgs[len(cc_msgs) :]
                first_message = False
            else:
                seen_ids.update(msg.id for msg in new_msgs)
                node_name = tuple(events.keys())[0]  # assumes one name per node
                yield ResponsesAgentStreamEvent(
                    type="response.output_item.done",
                    item=self.create_text_output_item(
                        text=f"<name>{node_name}</name>", id=str(uuid4())
                    ),
                )
            if len(new_msgs) > 0:
                yield from output_to_responses_items_stream(new_msgs)


#######################################################
# Configure the Foundation Model and Serving Sub-Agents
#######################################################

# TODO: Replace with your model serving endpoint

# 1. Super Agent, which will finally generate the ensemble code
LLM_ENDPOINT_NAME = "databricks-claude-sonnet-4-5"
llm = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME)


# TODO: Add the necessary information about each of your subagents. Subagents could be agents deployed to Model Serving endpoints or Genie Space subagents.
# Your agent descriptions are crucial for improving quality. Include as much detail as possible.
EXTERNALLY_SERVED_AGENTS = [
    Genie(
        space_id="01f0956a54af123e9cd23907e8167df9",
        name="Provider Enrollment",
        description="This agent can answer questions about provider and patient enrollment. \
            This dataset contains two tables: provider and enrollment. The provider table includes \
            information about healthcare claims, such as claim ID, patient ID, provider NPI, provider role, \
            and taxonomy code. \
            The enrollment table contains patient demographic and enrollment details, including gender, year of birth, ZIP code, state, enrollment dates, benefit type, and pay type.",
    ),
    Genie(
        space_id="01f0956a387714969edde65458dcc22a",
        name="Claims",
        description=(
            "This agent can answer questions about Medical and pharmacy claims. There are two "
            "tables: medical_claim and pharmacy_claim, both in the hv_claims_sample schema. Each "
            "table contains claims data with columns for claim_id, patient_id, date_service, and "
            "pay_type, among others. They can be connected by the patient_id column, which "
            "identifies the patient associated with each claim."
        ),
    ), 
    Genie(
        space_id="01f0956a4b0512e2a8aa325ffbac821b",
        name="Diagnosiss and Procedures",
        description=(
            "This agent can answer questions about diagnosiss and procedures. There are two tables: procedure and diagnosis, \
            both in the hv_claims_sample schema. They are connected by the columns claim_id and patient_id, which appear in \
            both tables and can be used to join procedure and diagnosis information for the same claim and patient."
        ),
    ),

    # ServedSubAgent(
    #     endpoint_name="cities-agent",
    #     name="city-agent", # choose a semantically relevant name for your agent
    #     task="agent/v1/responses",
    #     description="This agent can answer questions about the best cities to visit in the world.",
    # ),
]

############################################################
# Create additional agents in code
############################################################

# TODO: Fill the following with UC function-calling agents. The tools parameter is a list of UC function names that you want your agent to call.
IN_CODE_AGENTS = [
    InCodeSubAgent(
        tools=["system.ai.*"],
        name="code execution agent",
        description="The code execution agent specializes in solving programming challenges, generating code snippets, debugging issues, and explaining complex coding concepts.",
    )
]

#################################################
# Create supervisor and set up MLflow for tracing
#################################################

supervisor = create_langgraph_supervisor(llm, EXTERNALLY_SERVED_AGENTS, IN_CODE_AGENTS)

mlflow.langchain.autolog()
AGENT = LangGraphResponsesAgent(supervisor)
mlflow.models.set_model(AGENT)



#_-----------------------------------------------------------
# 2. Call the function to get space_summary chunks for the Genie Agents
table_name = "yyang.multi_agent_genie.enriched_genie_docs_chunks"
space_summary_df = query_delta_table(
    table_name=table_name,
    filter_field="chunk_type",
    filter_value="space_summary",
    select_fields=["space_id", "space_title", "searchable_content"]
)

# 3. Define the function to get space info by space_id
def get_space_info_by_id(space_summary_df, space_id):
    """
    Query space_summary_df by space_id and return space_title and searchable_content.

    Args:
        space_summary_df: Spark DataFrame with columns including 'space_id', 'space_title', 'searchable_content'
        space_id: The space_id to filter on

    Returns:
        Spark DataFrame with columns 'space_title' and 'searchable_content' for the given space_id
    """
    result_df = space_summary_df.filter(space_summary_df.space_id == space_id).select("space_title", "searchable_content")
    return result_df

from databricks_langchain.genie import GenieAgent
from langchain_core.messages import AIMessage
# syntax ref: https://api-docs.databricks.com/python/databricks-ai-bridge/latest/databricks_langchain.html#databricks_langchain.GenieAgent
"""
Create a genie agent that can be used to query the API. If a description is not provided, the description of the genie space will be used.

Parameters
:
genie_space_id – The ID of the genie space to use

genie_agent_name – Name for the agent (default: “Genie”)

description – Custom description for the agent

include_context – Whether to include query reasoning and SQL in the response

message_processor – Optional function to process messages before querying. It should accept a list of either dict or LangChain Message objects and return a query string. If not provided, the agent will use the chat history to form the query.

client – Optional WorkspaceClient instance
"""


# 1), routing A

genie_space_id = "01f0956a4b0512e2a8aa325ffbac821b"

row = get_space_info_by_id(space_summary_df, genie_space_id).collect()[0]
genie_agent_name = f"Genie_{row['space_title']}"
description = row['searchable_content']
# Create the Genie agent and include reasoning + SQL in the response
agent = GenieAgent(
    genie_space_id=genie_space_id,
    genie_agent_name=genie_agent_name,
    description=description,
    include_context=True,
)

agents = []
# 4. Create the Genie Agents
for row in space_summary_df.collect():
    space_id = row["space_id"]
    space_title = row["space_title"]
    searchable_content = row["searchable_content"]
    genie_agent_name = f"Genie_{space_title}"
    description = searchable_content
    genie_agent = GenieAgent(
        genie_space_id=space_id,
        genie_agent_name=genie_agent_name,
        description=description,
        include_context=True,
    )
    agents.append(genie_agent)