Please resort to context7 MCP server for update-to-date syntax and documentation; please use the .env file for accessing Databricks workspace for resource access.


# 1. Requirements for the multi-agent system:
According to @Super_Agent.ipynb as reference code example, please expand and build a multi-agent system using LangGraph and Databricks ResponseAgent integration. Please stick to `create_langgraph_supervisor` style supervisor agent for now. Please stick to the `LangGraphResponsesAgent` class, adopting the same writing style as the reference code example, writing code into agent.py file, and mlflow logging, deployment practice in the reference code example.

1. please build a system that can answer user's questions about cross-domain questions from different genie spaces, e.g., patients, medications, diagnoses, treatments, laboratory, etc.
2. super agent is the main agent that can call other agents and tools
3. thinking and planning agent is used to plan the best approach to answer the question, 
    - it first breaks down the question into analytical sub-tasks, for example, "How many patients older than 50 years are on Voltaren?" can be broken down into:
        - I need to get the count of the patients who are older than 50 years and are taking medicine Voltaren.
        - I need to get the count of the patients who are older than 50 years (sub-question part 1).
        - I need to get the count of the patients who are taking medicine Voltaren (sub-question part 2).
        - I need to find out patient related information about age (sub-question part 1), and patient prescription medication related information about drug name (sub-question part 2). 
        - The two parts of information should be connected by some common column, e.g., patient_id, in order to answer the question about count of patients satisfying both conditions. 
        - While not in this case, but in some other cases, the two parts of information can be verbally merged in a way to answer the question without the need of a common column, e.g., a qualitative question about the relationship between age and drug name.
    - it then calls the vector search index tool to find out what Genie Agents relevant to the last step breakdowns. The vector search index is built on the parsed docs from genie spaces space.json exports.
        - (single-genie-agent case) if one of the Genie Agents can answer the question completely, it will call the Genie Agent to answer the question, return thinking_result, sql_result, answer_result to the super agent.
        - (multiple-genie-agents case) if multiple Genie Agents are needed to completely answer the question, i.e., the query relates columns in different Genie Agents to answer the question, for example, Patients Genie Agent has the age information while Medications Genie Agent has the medication information, it will then further split into two scenarios: 
            - a), row-wise join based on common column is needed to answer the question from the last step breakdowns; 
                - (fast route) call the SQL synthesis agent to assembly the overall SQL queries based on related Genie Agents' metadata, and then call the SQL execution agent to execute the SQL query on these delta tables behind the Genie Agents and return the result as the same format as in the single-genie-agent case.
                - (slow route) on parallel, asynchronously call each Genie Agent to answer the broken-down sub-question parts which each of them can answer completely, fetch the sql_result from the Genie Agents, call the SQL synthesis agent to assembly the overall SQL queries based on multiple sql_results, and then call the SQL execution agent to execute the SQL query on these delta tables behind the Genie Agents and return the result as the same format as in the single-genie-agent case.
                - allow fast route answer to be returned first to the user, notifying the user that the slow route answer is in progress, and slow route answer to be returned later.
            - b), no row-wise join is needed, just verbally merge the two parts of information if no common column is needed to answer the question from the last step breakdowns.
               - call each Genie Agent to answer the broken-down sub-question parts, and then verbally merge the answers from the Genie Agents to an overall polished integrative answer. Return the result as the same format as in the single-genie-agent case, just for sql_result part, make sure to include two separate sql_results from the two sub-question parts.
4. if the original question is vague or unclear deemed by thinking and planning agent, it will return feedback to the super agent, super agent will ask the users to clarify the question, providing a few choices to the user on how to clarify the question, e.g.,
    - "please make a few choices: \n1. Do you mean the number of patients who are older than 50 years and are taking medicine Voltaren?\n2. Do you mean the number of patients who are older than 50 years and are diagnosed with Voltaren related diseases? \n3. Provide your own refined question." After the user chooses one of the choices and hit enter, the Super Agent will trigger the whole process again based on refined question.

# 2. Requirements for the vector search index pipeline:
Please refer to @03_VS_generation.ipynb as reference code example, and build the vector search index pipeline.

1. use the enriched parsed docs from genie spaces space.json exports to build vector search index for the spaces.
2. each genie space should have a baseline parsed docs based on space.json export, which contains metadata of the space itself. For how to obtain the baseline parsed docs, please refer to code snippet in@Super_Agent.ipynb as reference code example.
3. for each genie space, please enrich the baseline parsed docs with the table metadata from the table metadata update pipeline.
4. for vector search index pipeline, please build a separate notebook.
5. adopt databricks managed vector search index.

# 3. Requirements for the table metadata update pipeline:
Please refer to @01_Table_MetaInfo_Update.ipynb as reference code example, and build the table metadata update pipeline.
1. update the table metadata for the delta tables behind all Genie Agents.
2. reference this website https://docs.databricks.com/aws/en/genie/knowledge-store for definition and implementation details, 
    a. please sample the column values for the columns in the delta tables behind all Genie Agents, and enrich the metadata with the sampled column values.
    b. please also build the value dictionary for the columns in the delta tables behind all Genie Agents, and enrich the metadata with the value dictionary.
3. Enrich the the parsed docs from genie spaces space.json exports with the table metadata.
4. save the enriched parsed docs to a delta table in the unity catalog.


Please build item 3 first, then 2, then 1. Please test each pipeline separately before integrating them into the multi-agent system. Make sure each pipeline is working as expected. Make sure the multi-agent system is working as expected.

# 4. TODO: caching system for the multi-agent system.
1. full-text cache
2. parameterized SQL cache
3. semantic cache
