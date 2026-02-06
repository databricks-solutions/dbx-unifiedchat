# Databricks notebook source
# MAGIC %md
# MAGIC # Multi-Agent System for Cross-Domain Genie Queries
# MAGIC 
# MAGIC This notebook implements and tests a sophisticated multi-agent system that can:
# MAGIC - Answer questions across multiple Genie spaces
# MAGIC - Intelligently plan query execution strategies
# MAGIC - Handle both single-space and multi-space queries
# MAGIC - Perform fast-route (direct SQL synthesis) and slow-route (combine Genie results) execution
# MAGIC - Ask for clarification when questions are unclear
# MAGIC 
# MAGIC **Architecture:**
# MAGIC - **SupervisorAgent**: Routes to appropriate sub-agents
# MAGIC - **ThinkingPlanningAgent**: Analyzes queries and plans execution
# MAGIC - **GenieAgents**: Query individual Genie spaces
# MAGIC - **SQLSynthesisAgent**: Combines SQL across spaces
# MAGIC - **SQLExecutionAgent**: Executes synthesized queries

# COMMAND ----------

# MAGIC %md
# MAGIC ## Setup and Installation

# COMMAND ----------

# MAGIC %pip install -U -qqq langgraph langgraph-checkpoint mlflow[databricks]==3.7.0 databricks-langchain databricks-agents>=0.13.0 databricks-vectorsearch
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write Agent Code to File

# COMMAND ----------

# DBTITLE 1,Check if agent.py exists
import os

agent_file = "agent.py"
if os.path.exists(agent_file):
    print(f"✓ {agent_file} already exists")
    with open(agent_file, 'r') as f:
        print(f"  File size: {len(f.read())} bytes")
else:
    print(f"✗ {agent_file} not found - please ensure agent.py is in the same directory")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Agent - Basic Functionality

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

from agent import AGENT

# Test 1: Check available agents
input_example = {
    "input": [
        {"role": "user", "content": "What tools and agents do you have access to?"}
    ]
}

print("="*80)
print("TEST 1: Available Agents and Tools")
print("="*80)

for event in AGENT.predict_stream(input_example):
    print(event.model_dump(exclude_none=True))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Agent - Single Space Query

# COMMAND ----------

# Test 2: Simple single-space question
input_example = {
    "input": [
        {"role": "user", "content": "How many patients are older than 65 years?"}
    ]
}

print("\n" + "="*80)
print("TEST 2: Single Space Query - Patient Demographics")
print("="*80)

for event in AGENT.predict_stream(input_example):
    result = event.model_dump(exclude_none=True)
    print(result)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Agent - Cross-Domain Query (Multiple Spaces with Join)

# COMMAND ----------

# Test 3: Cross-domain question requiring join
input_example = {
    "input": [
        {"role": "user", "content": "How many patients older than 50 years are on Voltaren?"}
    ]
}

print("\n" + "="*80)
print("TEST 3: Cross-Domain Query - Patients + Medications")
print("="*80)

for event in AGENT.predict_stream(input_example):
    result = event.model_dump(exclude_none=True)
    print(result)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Agent - Multiple Spaces Without Join

# COMMAND ----------

# Test 4: Question requiring multiple spaces but no join (verbal merge)
input_example = {
    "input": [
        {"role": "user", "content": "What are the most common diagnoses and what are the most prescribed medications?"}
    ]
}

print("\n" + "="*80)
print("TEST 4: Multiple Spaces - Verbal Merge")
print("="*80)

for event in AGENT.predict_stream(input_example):
    result = event.model_dump(exclude_none=True)
    print(result)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Agent - Unclear Question (Clarification Flow)

# COMMAND ----------

# Test 5: Unclear question requiring clarification
input_example = {
    "input": [
        {"role": "user", "content": "Tell me about patients with cancer"}
    ]
}

print("\n" + "="*80)
print("TEST 5: Unclear Question - Should Request Clarification")
print("="*80)

for event in AGENT.predict_stream(input_example):
    result = event.model_dump(exclude_none=True)
    print(result)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Agent - Complex Multi-Domain Query

# COMMAND ----------

# Test 6: Complex question across multiple domains
input_example = {
    "input": [
        {
            "role": "user", 
            "content": "For patients diagnosed with lung cancer in 2023, what percentage are currently on chemotherapy medications?"
        }
    ]
}

print("\n" + "="*80)
print("TEST 6: Complex Multi-Domain Query")
print("="*80)

for event in AGENT.predict_stream(input_example):
    result = event.model_dump(exclude_none=True)
    print(result)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Agent - Laboratory and Treatment Integration

# COMMAND ----------

# Test 7: Lab results and treatment
input_example = {
    "input": [
        {
            "role": "user",
            "content": "Show me patients with abnormal biomarker results who underwent surgical treatment"
        }
    ]
}

print("\n" + "="*80)
print("TEST 7: Laboratory + Treatment Integration")
print("="*80)

for event in AGENT.predict_stream(input_example):
    result = event.model_dump(exclude_none=True)
    print(result)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Agent Deployment Preparation

# COMMAND ----------

# MAGIC %md
# MAGIC ### Deploy Agent Using MLflow 3.7.0 ResponsesAgent

# COMMAND ----------

import mlflow
from mlflow.models import infer_signature
from databricks import agents

# Get current user for model naming
current_user = spark.sql("SELECT current_user()").collect()[0][0]

# Set MLflow experiment
mlflow.set_experiment(f"/Users/{current_user}/multi_agent_genie")

print("Deploying agent with MLflow 3.7.0 ResponsesAgent...")

# Import ResponsesAgent wrapper
from agent import MLFLOW_AGENT, get_agent_graph

# Create sample input following ResponsesAgentRequest format
sample_input = {
    "input": [
        {"role": "user", "content": "How many patients are older than 50?"}
    ],
    "context": {}
}

# Test the ResponsesAgent to get output signature
from mlflow.types.responses import ResponsesAgentRequest
test_request = ResponsesAgentRequest(**sample_input)
sample_output = MLFLOW_AGENT.predict(test_request)

# Infer signature for MLflow 3.7.0 ResponsesAgent
signature = infer_signature(sample_input, sample_output)

# Log ResponsesAgent with MLflow 3.7.0
with mlflow.start_run(run_name="multi_agent_genie_responses_v1") as run:
    mlflow.pyfunc.log_model(
        artifact_path="agent",
        python_model=MLFLOW_AGENT,  # ResponsesAgent instance
        signature=signature,
        input_example=sample_input,
        pip_requirements=[
            "langgraph",
            "langgraph-checkpoint",
            "mlflow[databricks]==3.7.0",
            "databricks-langchain",
            "databricks-agents>=0.13.0",
            "databricks-vectorsearch",
        ],
        code_paths=["agent.py"],
        registered_model_name="multi_agent_genie_system",
    )
    
    run_id = run.info.run_id
    model_uri = f"runs:/{run_id}/agent"
    
    print(f"✓ ResponsesAgent logged successfully with MLflow 3.7.0!")
    print(f"  Run ID: {run_id}")
    print(f"  Model URI: {model_uri}")

# Deploy to serving endpoint using agents.deploy()
print("\nDeploying ResponsesAgent to Model Serving endpoint...")

deployment = agents.deploy(
    model_uri=model_uri,
    endpoint_name="multi-agent-genie-endpoint",
    
    # Deployment settings
    scale_to_zero_enabled=True,
    workload_size="Small",
)

print(f"✓ ResponsesAgent deployed successfully!")
print(f"  Model name: multi_agent_genie_system")
print(f"  Endpoint: multi-agent-genie-endpoint")
print(f"  Status: {deployment}")


# COMMAND ----------

# MAGIC %md
# MAGIC ### Test Deployed Endpoint

# COMMAND ----------

# Test the deployed endpoint using MLflow 3.7.0 ResponsesAgent format
print("Testing deployed ResponsesAgent endpoint...")

test_query = "How many patients are over 60 years old?"

# Query the endpoint with ResponsesAgentRequest format
response = deployment.predict(
    inputs={
        "input": [
            {"role": "user", "content": test_query}
        ],
        "context": {}
    }
)

print(f"\nTest Query: {test_query}")
print("="*80)
print("\nEndpoint Response:")
print(response)

# Alternative: Test locally before deploying
print("\n" + "="*80)
print("Testing locally with ResponsesAgent...")
print("="*80)

from agent import MLFLOW_AGENT
from mlflow.types.responses import ResponsesAgentRequest

local_request = ResponsesAgentRequest(
    input=[{"role": "user", "content": test_query}],
    context={}
)

local_response = MLFLOW_AGENT.predict(local_request)

print("\nLocal ResponsesAgent Response:")
print(local_response)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Performance Metrics and Monitoring

# COMMAND ----------

# DBTITLE 1,Agent Performance Metrics
import pandas as pd
from datetime import datetime

# Define test queries with expected characteristics
test_suite = [
    {
        "query": "How many patients are older than 50?",
        "expected_spaces": 1,
        "expected_route": "single",
        "category": "simple"
    },
    {
        "query": "How many patients older than 50 are on Voltaren?",
        "expected_spaces": 2,
        "expected_route": "fast_join",
        "category": "complex"
    },
    {
        "query": "What are common diagnoses and common medications?",
        "expected_spaces": 2,
        "expected_route": "verbal_merge",
        "category": "medium"
    },
]

results = []

for test_case in test_suite:
    print(f"\nTesting: {test_case['query']}")
    print("-" * 80)
    
    start_time = datetime.now()
    
    try:
        # Test using deployed ResponsesAgent endpoint if available
        if 'deployment' in globals():
            response = deployment.predict(
                inputs={
                    "input": [{"role": "user", "content": test_case["query"]}],
                    "context": {}
                }
            )
        # Otherwise test with ResponsesAgent locally
        elif 'MLFLOW_AGENT' in dir():
            from agent import MLFLOW_AGENT
            from mlflow.types.responses import ResponsesAgentRequest
            
            request = ResponsesAgentRequest(
                input=[{"role": "user", "content": test_case["query"]}],
                context={}
            )
            response = MLFLOW_AGENT.predict(request)
        # Fallback to direct agent testing
        else:
            input_example = {
                "input": [{"role": "user", "content": test_case["query"]}]
            }
            response = AGENT.predict(input_example)
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        results.append({
            "query": test_case["query"],
            "category": test_case["category"],
            "expected_route": test_case["expected_route"],
            "duration_seconds": duration,
            "success": True,
            "response_length": len(str(response)),
        })
        
        print(f"✓ Success ({duration:.2f}s)")
        
    except Exception as e:
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        results.append({
            "query": test_case["query"],
            "category": test_case["category"],
            "expected_route": test_case["expected_route"],
            "duration_seconds": duration,
            "success": False,
            "error": str(e),
        })
        
        print(f"✗ Failed: {str(e)}")

# Display results
df_results = pd.DataFrame(results)
display(df_results)

# Summary statistics
print("\n" + "="*80)
print("PERFORMANCE SUMMARY")
print("="*80)
print(f"Total tests: {len(results)}")
print(f"Success rate: {df_results['success'].mean() * 100:.1f}%")
print(f"Average duration: {df_results['duration_seconds'].mean():.2f}s")
print(f"Min duration: {df_results['duration_seconds'].min():.2f}s")
print(f"Max duration: {df_results['duration_seconds'].max():.2f}s")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Agent Trace Visualization

# COMMAND ----------

# View traces in MLflow
current_user = spark.sql("SELECT current_user()").collect()[0][0]
experiment_name = f"/Users/{current_user}/multi_agent_genie"

# Get experiment
experiment = mlflow.get_experiment_by_name(experiment_name)

if experiment:
    print(f"View detailed traces at:")
    print(f"  MLflow UI: /ml/experiments/{experiment.experiment_id}")
    print(f"\nYou can also use MLflow Tracing to view agent execution traces:")
    print(f"  - Agent decisions and routing")
    print(f"  - Individual agent executions")
    print(f"  - SQL queries and results")
else:
    print(f"Experiment '{experiment_name}' not found yet. Deploy the agent first.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Working with Deployed Agent

# COMMAND ----------

# MAGIC %md
# MAGIC ### Loading an Existing Deployment
# MAGIC 
# MAGIC If you've already deployed the agent, you can load it using the deployment name:

# COMMAND ----------

from databricks import agents

# Load existing deployment
try:
    existing_deployment = agents.get_deployment("multi-agent-genie-endpoint")
    print(f"✓ Loaded existing deployment: {existing_deployment}")
    
    # Test a query
    test_response = existing_deployment.predict(
        messages=[
            {"role": "user", "content": "How many patients do we have?"}
        ]
    )
    print("\nTest Response:")
    print(test_response)
    
except Exception as e:
    print(f"Note: Deployment not found or not ready yet: {e}")
    print("Deploy the agent first using the cells above.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Using Deployment in Production

# COMMAND ----------

# Example: Production usage pattern with MLflow 3.7.0 ResponsesAgent
def query_multi_agent_system(question: str, endpoint_name: str = "multi-agent-genie-endpoint"):
    """
    Query the multi-agent system with a question using MLflow 3.7.0 ResponsesAgent format.
    
    Args:
        question: Natural language question
        endpoint_name: Name of the deployed endpoint
        
    Returns:
        ResponsesAgentResponse object
    """
    from databricks import agents
    
    # Get deployment
    deployment = agents.get_deployment(endpoint_name)
    
    # Query the agent with ResponsesAgentRequest format
    response = deployment.predict(
        inputs={
            "input": [
                {"role": "user", "content": question}
            ],
            "context": {}
        }
    )
    
    return response

# Example usage
try:
    result = query_multi_agent_system("Show me patients over 65 years old")
    print("ResponsesAgent Response:")
    print(result)
    
    # Extract output items
    if hasattr(result, 'output') and len(result.output) > 0:
        print("\nOutput Items:")
        for item in result.output:
            print(item)
        
except Exception as e:
    print(f"Error: {e}")

# COMMAND ----------

# Example: Using the ResponsesAgent locally for testing
from agent import MLFLOW_AGENT
from mlflow.types.responses import ResponsesAgentRequest

def test_agent_locally(question: str):
    """Test the ResponsesAgent locally before deploying."""
    request = ResponsesAgentRequest(
        input=[{"role": "user", "content": question}],
        context={}
    )
    response = MLFLOW_AGENT.predict(request)
    return response

# Test locally
try:
    local_result = test_agent_locally("How many patients are in the database?")
    print("\nLocal ResponsesAgent Test:")
    print(local_result)
except Exception as e:
    print(f"Error testing locally: {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Documentation and Usage Guide

# COMMAND ----------

# MAGIC %md
# MAGIC ### Agent Usage Guide
# MAGIC 
# MAGIC #### Modern Deployment Pattern (MLflow 3.7.0 ResponsesAgent + databricks-agents SDK 0.13.0+)
# MAGIC 
# MAGIC This notebook uses **MLflow 3.7.0 ResponsesAgent** with the modern `agents.deploy()` pattern:
# MAGIC 
# MAGIC **Key Features:**
# MAGIC - **MLflow 3.7.0 ResponsesAgent**: Official pattern for serving LangGraph agents
# MAGIC - **ResponsesAgentRequest format**: Standard request/response format with input and context
# MAGIC - **Streaming support**: Built-in streaming with `predict_stream()` method
# MAGIC - **Simplified deployment**: Combined MLflow logging + agents.deploy()
# MAGIC - **Better monitoring**: Full MLflow 3.7.0 tracing with multi-turn conversation support
# MAGIC - **Version management**: Automatic model versioning and endpoint updates
# MAGIC 
# MAGIC ```python
# MAGIC # Define ResponsesAgent wrapper
# MAGIC class LangGraphResponsesAgent(ResponsesAgent):
# MAGIC     def __init__(self, agent: StateGraph):
# MAGIC         self.agent = agent
# MAGIC     
# MAGIC     def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
# MAGIC         outputs = [event.item for event in self.predict_stream(request)
# MAGIC                   if event.type == "response.output_item.done"]
# MAGIC         return ResponsesAgentResponse(output=outputs)
# MAGIC     
# MAGIC     def predict_stream(self, request):
# MAGIC         cc_msgs = to_chat_completions_input([i.model_dump() for i in request.input])
# MAGIC         for _, events in self.agent.stream({"messages": cc_msgs}):
# MAGIC             yield from output_to_responses_items_stream(...)
# MAGIC 
# MAGIC # Log with MLflow 3.7.0
# MAGIC with mlflow.start_run() as run:
# MAGIC     mlflow.pyfunc.log_model(
# MAGIC         artifact_path="agent",
# MAGIC         python_model=MLFLOW_AGENT,  # ResponsesAgent instance
# MAGIC         signature=signature,
# MAGIC         pip_requirements=["mlflow[databricks]==3.7.0", ...],
# MAGIC         registered_model_name="multi_agent_genie_system",
# MAGIC     )
# MAGIC 
# MAGIC # Deploy to endpoint
# MAGIC deployment = agents.deploy(
# MAGIC     model_uri=model_uri,
# MAGIC     endpoint_name="multi-agent-genie-endpoint",
# MAGIC )
# MAGIC 
# MAGIC # Query with ResponsesAgentRequest format
# MAGIC response = deployment.predict(
# MAGIC     inputs={
# MAGIC         "input": [{"role": "user", "content": "Your question"}],
# MAGIC         "context": {}
# MAGIC     }
# MAGIC )
# MAGIC ```
# MAGIC 
# MAGIC #### Architecture Overview
# MAGIC 
# MAGIC The multi-agent system follows a hierarchical architecture using modern LangGraph:
# MAGIC 
# MAGIC ```
# MAGIC User Query
# MAGIC     ↓
# MAGIC StateGraph (Supervisor)
# MAGIC     ↓
# MAGIC ThinkingPlanningAgent → Analyzes query, searches vector index
# MAGIC     ↓
# MAGIC Decision Point:
# MAGIC     ├─ Single Space → GenieAgent → Response
# MAGIC     ├─ Multiple Spaces (No Join) → GenieAgents (parallel) → Verbal Merge → Response
# MAGIC     └─ Multiple Spaces (Join Required)
# MAGIC         ├─ Table Route → SQLSynthesisAgent → SQLExecutionAgent → Response
# MAGIC         └─ Genie Route → GenieAgents (parallel) → SQLSynthesisAgent → SQLExecutionAgent → Response
# MAGIC ```
# MAGIC 
# MAGIC #### Query Types Supported
# MAGIC 
# MAGIC 1. **Single-Space Queries**
# MAGIC    - Example: "How many patients are over 65?"
# MAGIC    - Handled by: Single Genie Agent
# MAGIC    
# MAGIC 2. **Multi-Space Queries with Join**
# MAGIC    - Example: "How many patients over 50 are on Voltaren?"
# MAGIC    - Handled by: SQLSynthesisAgent + SQLExecutionAgent
# MAGIC    
# MAGIC 3. **Multi-Space Queries without Join**
# MAGIC    - Example: "What are common diagnoses and common medications?"
# MAGIC    - Handled by: Multiple Genie Agents + Verbal Merge
# MAGIC    
# MAGIC 4. **Unclear Queries**
# MAGIC    - Example: "Tell me about cancer patients"
# MAGIC    - Handled by: ThinkingPlanningAgent → Clarification Request
# MAGIC 
# MAGIC #### Best Practices
# MAGIC 
# MAGIC - **Be specific**: More specific questions get better results
# MAGIC - **Use proper terminology**: Medical terms work well with the system
# MAGIC - **Iterate**: If first answer isn't perfect, refine your question
# MAGIC - **Check traces**: Use MLflow traces to understand agent decisions
# MAGIC - **Monitor performance**: Use built-in monitoring from agents.deploy()
# MAGIC 
# MAGIC #### Limitations
# MAGIC 
# MAGIC - Patient counts < 10 are returned as "Count is less than 10"
# MAGIC - Individual patient IDs are never shown, only aggregates
# MAGIC - Complex multi-way joins may take longer (genie route)
# MAGIC - Vector search finds relevant spaces, but planning agent makes final decision
# MAGIC 
# MAGIC #### Modernization Updates
# MAGIC 
# MAGIC **Version Updates:**
# MAGIC - ✓ **MLflow 3.7.0**: Latest MLflow with ResponsesAgent support
# MAGIC - ✓ **databricks-agents>=0.13.0**: Modern deployment APIs
# MAGIC - ✓ **LangGraph**: Modern `langgraph` + `langgraph-checkpoint` (replacing deprecated langgraph-supervisor)
# MAGIC 
# MAGIC **Architecture Updates:**
# MAGIC - ✓ **LangGraphResponsesAgent**: Implements MLflow 3.7.0 ResponsesAgent interface
# MAGIC - ✓ **StateGraph**: Modern LangGraph supervisor using StateGraph pattern
# MAGIC - ✓ **ResponsesAgentRequest/Response**: Official MLflow request/response objects
# MAGIC - ✓ **Streaming support**: Built-in `predict_stream()` method for real-time responses
# MAGIC - ✓ **Dual wrappers**: AGENT for testing, MLFLOW_AGENT for deployment
# MAGIC 
# MAGIC **Deployment Pattern:**
# MAGIC - ✓ **Step 1**: Log with `mlflow.pyfunc.log_model()` using ResponsesAgent
# MAGIC - ✓ **Step 2**: Deploy with `agents.deploy()` for endpoint creation
# MAGIC - ✓ **Query**: Use `deployment.predict(inputs={"input": [...], "context": {}})` format
# MAGIC - ✓ **Streaming**: Use `predict_stream()` for real-time event streaming
# MAGIC - ✓ **Monitoring**: Full MLflow 3.7.0 tracing with multi-turn conversation support
# MAGIC 
# MAGIC **Official MLflow Pattern:**
# MAGIC - Based on: https://mlflow.org/docs/latest/genai/flavors/responses-agent-intro.html
# MAGIC - Uses Context7 MCP documented syntax for ResponsesAgent
# MAGIC - Follows MLflow best practices for serving LangGraph agents

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC 
# MAGIC ✅ **Completed Tasks:**
# MAGIC 
# MAGIC 1. ✓ Built table metadata update pipeline (02_Table_MetaInfo_Enrichment.py)
# MAGIC    - Samples column values from delta tables
# MAGIC    - Builds value dictionaries
# MAGIC    - Enriches metadata with LLM-generated descriptions
# MAGIC    - Saves enriched docs to Unity Catalog
# MAGIC 
# MAGIC 2. ✓ Built vector search index pipeline (04_VS_Enriched_Genie_Spaces.py)
# MAGIC    - Creates vector search endpoint
# MAGIC    - Builds delta sync index on enriched docs
# MAGIC    - Registers UC function for agent access
# MAGIC 
# MAGIC 3. ✓ Built modern multi-agent system (agent.py + this notebook)
# MAGIC    - ThinkingPlanningAgent for query analysis
# MAGIC    - Multiple Genie Agents for data access
# MAGIC    - SQLSynthesisAgent for query combination
# MAGIC    - SQLExecutionAgent for execution
# MAGIC    - Modern LangGraph StateGraph supervisor
# MAGIC    - Question clarification flow
# MAGIC 
# MAGIC 4. ✓ Modernized deployment with MLflow 3.7.0 ResponsesAgent
# MAGIC    - **MLflow 3.7.0 ResponsesAgent**: Official MLflow pattern for agent serving
# MAGIC    - **LangGraphResponsesAgent**: Wraps LangGraph StateGraph as ResponsesAgent
# MAGIC    - **ResponsesAgentRequest/Response**: Standard request/response format
# MAGIC    - **Streaming support**: Built-in `predict_stream()` for real-time responses
# MAGIC    - **agents.deploy()**: Modern deployment from databricks-agents SDK 0.13.0+
# MAGIC    - **Modern LangGraph**: Using StateGraph instead of deprecated langgraph-supervisor
# MAGIC    - Comprehensive test suite with multiple testing modes
# MAGIC    - Integrated MLflow 3.7.0 tracing with conversation support
# MAGIC    - Automated model versioning and endpoint management
# MAGIC 
# MAGIC **MLflow 3.7.0 ResponsesAgent Benefits:**
# MAGIC - ✅ **Official pattern**: Following MLflow documentation for LangGraph agents
# MAGIC - ✅ **Streaming native**: Built-in streaming with `predict_stream()` method
# MAGIC - ✅ **Standard format**: ResponsesAgentRequest with input and context
# MAGIC - ✅ **Multi-turn support**: Full conversation tracking in MLflow 3.7.0
# MAGIC - ✅ **Better tracing**: Enhanced trace comparison and full-text search
# MAGIC - ✅ **Event streaming**: Real-time events with ResponsesAgentStreamEvent
# MAGIC - ✅ **Future-proof**: Using actively maintained MLflow patterns
# MAGIC 
# MAGIC **Next Steps:**
# MAGIC - Monitor agent performance using built-in Databricks monitoring
# MAGIC - Refine prompts based on user feedback and trace analysis
# MAGIC - Add more sophisticated routing logic in StateGraph
# MAGIC - Implement caching for common queries
# MAGIC - Add support for follow-up questions with conversation memory
# MAGIC - Consider adding streaming responses for better UX

