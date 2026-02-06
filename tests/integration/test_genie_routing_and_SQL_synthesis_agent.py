plan_result = """
{'question_clear': True,
 'sub_questions': ['What are the medical claims for patients diagnosed with diabetes?',
  'What is the average cost of these claims?',
  'How to break down by insurance payer type?',
  'How to break down by patient age group?'],
 'requires_multiple_spaces': True,
 'relevant_space_ids': ['01f0956a4b0512e2a8aa325ffbac821b',
  '01f0956a387714969edde65458dcc22a',
  '01f0956a54af123e9cd23907e8167df9'],
 'requires_join': True,
 'join_strategy': 'genie_route',
 'execution_plan': 'Query each Genie Space separately: (1) HealthVerityProcedureDiagnosis for diabetes diagnosis claims with costs, (2) HealthVerityClaims for payer type information, (3) HealthVerityProviderEnrollment for patient age groups. Then combine the SQL queries to synthesize a final query that calculates average medical claim costs grouped by payer type and age group for diabetes patients.',
 'genie_route_plan': {'01f0956a4b0512e2a8aa325ffbac821b': 'What is the average cost of medical claims for patients diagnosed with diabetes?',
  '01f0956a387714969edde65458dcc22a': 'What is the average cost of medical claims broken down by insurance payer type?',
  '01f0956a54af123e9cd23907e8167df9': 'What is the patient age group breakdown for enrolled patients?'}}
  """

  # 2. Call the function to get space_summary chunks for the Genie Agents
table_name = "yyang.multi_agent_genie.enriched_genie_docs_chunks"
space_summary_df = query_delta_table(
    table_name=table_name,
    filter_field="chunk_type",
    filter_value="space_summary",
    select_fields=["space_id", "space_title", "searchable_content"]
)




from databricks_langchain.genie import GenieAgent
from langchain_core.messages import AIMessage
genie_agents = []
genie_agent_tools = []
# 4. Create the Genie Agents and get their tools
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
    genie_agents.append(genie_agent)
    # genie_agents[space_id] = genie_agent.as_tool() # dict
    genie_agent_tools.append(genie_agent.as_tool(name = genie_agent_name,
                                            description = description,
                                            arg_types = {"question": str}
                                            ))
print(f"✓ Created Genie Tools with {len(genie_agent_tools)} tools:")
for tool in genie_agent_tools:
    print(f"  - {tool.name}")

# 5. Define the tool to extract 'thinking' and 'sql' from Genie agent response
def extract_genie_response(resp):
    """
    Extracts 'thinking' (reasoning), 'sql', and 'answer' from Genie agent response.

    Args:
        resp: The response object from Genie agent, containing a 'messages' list.

    Returns:
        Tuple (thinking, sql, answer)
    """
    thinking = None
    sql = None
    answer = None

    for msg in resp["messages"]:
        if isinstance(msg, AIMessage):
            if msg.name == "query_reasoning":
                thinking = msg.content
            elif msg.name == "query_sql":
                sql = msg.content
            elif msg.name == "query_result":
                answer = msg.content
    return thinking, sql, answer
from langchain_core.tools import tool

@tool("extract_genie_sql_tool", description="Extracts 'thinking' and 'sql' from a Genie agent response dict object as a dict {'thinking': str, 'sql': str}.")
def extract_genie_sql_tool(resp: dict) -> dict:
    """
    LangChain tool wrapper for extract_genie_response.
    Args:
        resp: The response object from Genie agent, containing a 'messages' list.
    Returns:
        Dict with 'thinking', 'sql', and 'answer'
    """
    thinking, sql, _ = extract_genie_response(resp)
    return {"thinking": thinking, "sql": sql}


# 6. Create Genie Routing and SQL Synthesis Agent
from databricks_langchain import (
    ChatDatabricks,
    DatabricksFunctionClient,
    UCFunctionToolkit,
    set_uc_function_client,
)
from langchain.agents import create_agent
import mlflow

# Initialize Databricks Function Client
client = DatabricksFunctionClient()
set_uc_function_client(client)

# Initialize LLM
LLM_ENDPOINT_NAME = "databricks-claude-sonnet-4-5"
llm = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME, temperature=0.1)

# # Create UC Function Toolkit with the registered functions
# uc_function_names = [
# ]

# uc_toolkit = UCFunctionToolkit(function_names=uc_function_names)
# tools = uc_toolkit.tools
tools = []
tools.extend(genie_agent_tools)
tools.append(extract_genie_sql_tool)

print(f"✓ Created UCFunctionToolkit with {len(tools)} tools:")
# for tool, value in tools.items():
#     print(f"  - {tool}:{value.name}")

# Create SQL Synthesis Agent (specialized for multi-agent system)
# Create SQL Synthesis Agent (specialized for multi-agent system)
sql_synthesis_agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=(
"""You are a SQL synthesis agent, which can take analysis plan, and route queries to the corresponding Genie Agent.
The Plan given to you is a JSON:
{"question_clear": true,
"sub_questions": ["sub-question 1", "sub-question 2", ...],
"requires_multiple_spaces": true/false,
"relevant_space_ids": ["space_id_1", "space_id_2", ...],
"requires_join": true/false,
"join_strategy": "table_route" or "genie_route" or null,
"execution_plan": "Brief description of execution plan",
"genie_route_plan": {'space_id_1':'partial_question_1', 'space_id_2':'partial_question_2', 'space_id_3':'partial_question_3', ...} or null,}

Under the key of 'genie_route_plan' in the JSON, extracting 'partial_question_1' and feed to the right Genie Agent tool with the input fromat {"question": 'partial_question_1'}; asynchronously send all other partial_questions to the corresponding Genie Agent tools accordingly.

You have access to all Genie Agents as tools given to you.
Use the mapping table given to you to assign the question to the correct tool by searching tool id by tool name.
mapping table format like this:
- space_id_1:Genie_tool_name_1
- space_id_1:Genie_tool_name_2
- space_id_1:Genie_tool_name_3

After each Genie agent returns result, only extract the SQL string by using the 'extract_genie_sql_tool' tool.
Then, you can combine all the SQL pieces into a single SQL query, and return the final SQL query.
OUTPUT REQUIREMENTS:
- Generate complete, executable SQL with:
  * Proper JOINs based on execution plan strategy
  * WHERE clauses for filtering
  * Appropriate aggregations
  * Clear column aliases
- Return ONLY the SQL query without explanations or markdown formatting
- If SQL cannot be generated, explain what metadata is missing"""
    )
)

# backup:
# """{'space_id_1': RunnableLambda(...),
#     'space_id_2': RunnableLambda(...),
#     'space_id_3': RunnableLambda(...)}\n
# """

print("\n" + "="*80)
print("✅ SQL Synthesis Agent created successfully!")
print("="*80)
print("\nAgent Configuration:")
print(f"  - LLM: {LLM_ENDPOINT_NAME}")
print(f"  - Tools: {len(tools)} UC functions")
print(f"  - Agent Type: LangChain Tool-Calling Agent")


# 7. Test the Genie Routing and SQL Synthesis Agent

# Create the message for the agent
agent_message = {
    "messages": [
        {
            "role": "user",
            "content": f"""
Generate a SQL query to answer this question: {query}

Query Plan:
{json.dumps(plan_result, indent=2)}

Use your available Genie Agent tools to generate SQL and finally assemble them into an overall SQL to answer the original question.
"""
        }
    ]
}

# Enable MLflow autologging for tracing
mlflow.langchain.autolog()

# Invoke the agent
print("🤖 Invoking SQL Synthesis Agent...")
print("="*80 + "\n")

result = sql_synthesis_agent.invoke(agent_message)