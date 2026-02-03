# Testing and Benchmarking Guide for LLM Diversification

This guide provides instructions for testing the diversified LLM configuration and benchmarking performance improvements.

## Prerequisites

- Databricks workspace with access to all configured foundation models
- `.env` file configured with diversified LLM endpoints
- Access to Databricks SQL Warehouse for testing
- Test Genie spaces configured

## Phase 1: Configuration Verification

### Step 1: Verify Configuration Loading

Run the configuration cell in `Notebooks/Super_Agent_hybrid.py` and verify the output:

```python
# Run cell: DBTITLE 1,Configuration
# Expected output should show:
# LLM Endpoints (Diversified by Agent):
#   Default/Fallback: databricks-claude-sonnet-4-5
#   Clarification Agent: databricks-gpt-5-mini
#   Planning Agent: databricks-claude-sonnet-4-5
#   SQL Synthesis (Table) Agent: databricks-gpt-5-1-codex-mini
#   SQL Synthesis (Genie) Agent: databricks-gpt-5
#   SQL Execution Agent: databricks-gpt-5-nano
#   Summarize Agent: databricks-gemini-2-5-flash
```

### Step 2: Verify Model Access

Test that all configured models are accessible:

```python
from databricks_langchain import ChatDatabricks

# Test each endpoint
endpoints_to_test = [
    ("Clarification", LLM_ENDPOINT_CLARIFICATION),
    ("Planning", LLM_ENDPOINT_PLANNING),
    ("SQL Synthesis (Table)", LLM_ENDPOINT_SQL_SYNTHESIS_TABLE),
    ("SQL Synthesis (Genie)", LLM_ENDPOINT_SQL_SYNTHESIS_GENIE),
    ("Execution", LLM_ENDPOINT_EXECUTION),
    ("Summarize", LLM_ENDPOINT_SUMMARIZE),
]

for name, endpoint in endpoints_to_test:
    try:
        llm = ChatDatabricks(endpoint=endpoint, max_tokens=10)
        response = llm.invoke("Hello")
        print(f"✓ {name} ({endpoint}): ACCESSIBLE")
    except Exception as e:
        print(f"✗ {name} ({endpoint}): FAILED - {str(e)}")
```

## Phase 2: Individual Agent Testing

### Test 1: Clarification Agent

Test the unified intent/context/clarification agent:

```python
# Simple query - should use fast-path
test_query_1 = "Show me patient demographics"
result_1 = AGENT.predict(messages=[{"role": "user", "content": test_query_1}])

# Complex query - should trigger full LLM analysis
test_query_2 = "What are the total claims by diagnosis code for patients who had encounters in cardiology departments during Q4 2023, broken down by month and filtered for only high-cost procedures?"
result_2 = AGENT.predict(messages=[{"role": "user", "content": test_query_2}])

# Check tracking
print_agent_model_usage()
# Should show clarification agent using databricks-gpt-5-mini
```

### Test 2: Planning Agent

Test query planning and routing:

```python
# Test table route
result_table = AGENT.predict(messages=[{
    "role": "user", 
    "content": "Show me the count of patients by state"
}])

# Test genie route
result_genie = AGENT.predict(messages=[{
    "role": "user", 
    "content": "What are the trends in emergency department visits over the last year?"
}])

# Check metrics
summary = get_performance_summary()
print(json.dumps(summary["agent_model_usage"], indent=2))
```

### Test 3: SQL Synthesis Agents

Test both table and genie route synthesis:

```python
# Test Table Route (UC Functions)
result_uc = AGENT.predict(messages=[{
    "role": "user",
    "content": "List all tables in the healthcare schema with their row counts"
}])

# Test Genie Route (Multi-agent orchestration)
result_genie = AGENT.predict(messages=[{
    "role": "user",
    "content": "Compare medication costs across different pharmacy networks and identify cost-saving opportunities"
}])

# Verify correct models used
print_agent_model_usage()
# Table route should use: databricks-gpt-5-1-codex-mini
# Genie route should use: databricks-gpt-5
```

### Test 4: Summarize Agent

Test result summarization:

```python
# Run any query that generates results
result = AGENT.predict(messages=[{
    "role": "user",
    "content": "Show me top 5 patients by total claim amount"
}])

# Check summary quality and model usage
print_agent_model_usage()
# Should show: databricks-gemini-2-5-flash
```

## Phase 3: Performance Benchmarking

### Benchmark 1: Latency Comparison

Create a baseline benchmark with the old single-model configuration, then compare with diversified configuration:

```python
import time
import statistics

def benchmark_queries(queries, num_runs=3):
    """Run queries multiple times and collect metrics."""
    results = []
    
    for query in queries:
        query_times = []
        for _ in range(num_runs):
            start = time.time()
            response = AGENT.predict(messages=[{"role": "user", "content": query}])
            elapsed = time.time() - start
            query_times.append(elapsed)
        
        results.append({
            "query": query[:50] + "...",
            "avg_time": statistics.mean(query_times),
            "min_time": min(query_times),
            "max_time": max(query_times),
            "std_dev": statistics.stdev(query_times) if len(query_times) > 1 else 0
        })
    
    return results

# Test queries covering different agents
test_queries = [
    "Show me patient count by age group",  # Simple - tests clarification + planning + table synthesis
    "What are the top diagnoses for patients with diabetes?",  # Medium complexity
    "Analyze medication adherence patterns across different patient populations and identify factors affecting compliance",  # Complex - tests genie route
    "Compare emergency room wait times between different hospitals",  # Genie route
]

# Run benchmark
print("Running benchmark (this may take 5-10 minutes)...")
benchmark_results = benchmark_queries(test_queries, num_runs=3)

# Display results
print("\n" + "="*80)
print("BENCHMARK RESULTS")
print("="*80)
for result in benchmark_results:
    print(f"\nQuery: {result['query']}")
    print(f"  Average: {result['avg_time']:.2f}s")
    print(f"  Min: {result['min_time']:.2f}s")
    print(f"  Max: {result['max_time']:.2f}s")
    print(f"  Std Dev: {result['std_dev']:.2f}s")

# Show agent model usage
print_agent_model_usage()
```

### Benchmark 2: Token Usage and Cost Analysis

Track token consumption per agent (requires MLflow tracking):

```python
import mlflow

# Start tracking run
with mlflow.start_run():
    # Log configuration
    mlflow.log_param("config", "diversified")
    
    # Run test queries
    for i, query in enumerate(test_queries):
        result = AGENT.predict(messages=[{"role": "user", "content": query}])
        
        # Log per-query metrics (if available from response)
        mlflow.log_metric(f"query_{i}_duration", result.get("duration", 0))
    
    # Log aggregate metrics
    summary = get_performance_summary()
    if "agent_model_usage" in summary:
        for agent_name, usage in summary["agent_model_usage"].items():
            mlflow.log_metric(f"{agent_name}_invocations", usage["invocations"])
            mlflow.set_tag(f"{agent_name}_model", usage["model"])
    
    # Log performance summary
    mlflow.log_dict(summary, "performance_summary.json")

print("✓ Metrics logged to MLflow")
```

### Benchmark 3: Quality Assessment

Evaluate response quality across different models:

```python
# Test queries with expected outputs
quality_tests = [
    {
        "query": "How many patients do we have in total?",
        "expected_sql_pattern": "SELECT COUNT",
        "expected_result_type": "numeric"
    },
    {
        "query": "Show me the top 5 most expensive medications",
        "expected_sql_pattern": "ORDER BY.*DESC.*LIMIT 5",
        "expected_result_type": "table"
    },
    {
        "query": "What percentage of patients have diabetes?",
        "expected_sql_pattern": "SUM.*CASE",
        "expected_result_type": "percentage"
    }
]

# Run quality tests
quality_results = []
for test in quality_tests:
    result = AGENT.predict(messages=[{"role": "user", "content": test["query"]}])
    
    # Extract SQL and check against pattern
    sql = result.get("sql_query", "")
    matches_pattern = bool(re.search(test["expected_sql_pattern"], sql, re.IGNORECASE))
    
    quality_results.append({
        "query": test["query"],
        "matches_expected": matches_pattern,
        "has_results": bool(result.get("results")),
        "has_error": bool(result.get("error"))
    })

# Display quality results
print("\n" + "="*80)
print("QUALITY ASSESSMENT")
print("="*80)
for result in quality_results:
    status = "✓" if result["matches_expected"] and result["has_results"] and not result["has_error"] else "✗"
    print(f"{status} {result['query']}")
    if not result["matches_expected"]:
        print(f"   Warning: SQL pattern doesn't match expected")
    if result["has_error"]:
        print(f"   Error: {result['error']}")
```

## Phase 4: Cost Analysis

### Calculate Model Costs

Estimate costs based on token usage (requires actual token counts from model responses):

```python
# Estimated pricing (tokens per million) - UPDATE WITH ACTUAL DATABRICKS PRICING
MODEL_PRICING = {
    "databricks-gpt-5-nano": {"input": 0.15, "output": 0.60},
    "databricks-gpt-5-mini": {"input": 0.50, "output": 1.50},
    "databricks-claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "databricks-claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "databricks-gpt-5": {"input": 2.50, "output": 10.00},
    "databricks-gpt-5-1-codex-mini": {"input": 1.00, "output": 3.00},
    "databricks-gemini-2-5-flash": {"input": 0.075, "output": 0.30},
}

def estimate_cost(model, input_tokens, output_tokens):
    """Estimate cost for a model invocation."""
    if model not in MODEL_PRICING:
        return 0.0
    
    pricing = MODEL_PRICING[model]
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost

# Calculate total cost for benchmark run
# Note: This requires token counts from actual model responses
print("\n" + "="*80)
print("COST ESTIMATION")
print("="*80)
print("Note: Requires actual token counts from model responses")
print("Update this with your actual usage data from MLflow tracking")
```

## Phase 5: Comparison Report

### Generate Comparison Report

```python
def generate_comparison_report(baseline_metrics, diversified_metrics):
    """Generate a comparison report between baseline and diversified configs."""
    
    print("\n" + "="*80)
    print("CONFIGURATION COMPARISON REPORT")
    print("="*80)
    
    # Latency comparison
    baseline_avg = baseline_metrics.get("avg_latency", 0)
    diversified_avg = diversified_metrics.get("avg_latency", 0)
    improvement = ((baseline_avg - diversified_avg) / baseline_avg) * 100 if baseline_avg else 0
    
    print(f"\n📊 LATENCY METRICS:")
    print(f"  Baseline (single model):    {baseline_avg:.2f}s")
    print(f"  Diversified (multi-model):  {diversified_avg:.2f}s")
    print(f"  Improvement:                {improvement:+.1f}%")
    
    # Cost comparison (if available)
    if "total_cost" in baseline_metrics and "total_cost" in diversified_metrics:
        baseline_cost = baseline_metrics["total_cost"]
        diversified_cost = diversified_metrics["total_cost"]
        cost_change = ((diversified_cost - baseline_cost) / baseline_cost) * 100
        
        print(f"\n💰 COST METRICS:")
        print(f"  Baseline:      ${baseline_cost:.4f}")
        print(f"  Diversified:   ${diversified_cost:.4f}")
        print(f"  Change:        {cost_change:+.1f}%")
    
    # Agent-specific breakdown
    if "agent_model_usage" in diversified_metrics:
        print(f"\n🤖 AGENT MODEL CONFIGURATION:")
        for agent, info in diversified_metrics["agent_model_usage"].items():
            print(f"  {agent}: {info['model']} ({info['invocations']} invocations)")
    
    print("\n" + "="*80)

# Usage:
# 1. Run baseline benchmark with single model config
# 2. Run diversified benchmark with new config
# 3. Generate comparison report
# generate_comparison_report(baseline_results, diversified_results)
```

## Expected Results

Based on the plan recommendations, you should expect:

| Metric | Expected Change | Notes |
|--------|----------------|-------|
| Overall Latency | **-15% to -25%** | Faster models on non-critical paths |
| Clarification Agent | **-30% to -50%** | GPT-5 mini vs Claude Sonnet 4.5 |
| SQL Synthesis (Table) | **-20% to -40%** | Codex Mini optimized for SQL |
| SQL Synthesis (Genie) | **+10% to +20%** | More powerful model for complex orchestration |
| Summarize Agent | **-40% to -60%** | Gemini Flash optimized for speed |
| Overall Cost | **-10% to -20%** | Depends on query distribution |
| Accuracy | **Maintained or Improved** | Code-specialized models for SQL |

## Troubleshooting

### Common Issues

1. **Model Access Errors**
   - Verify model availability in your Databricks region
   - Check Acceptable Use Policy compliance
   - Enable cross-geo routing if needed (Gemini models)

2. **Performance Not Improved**
   - Verify correct endpoints are being used (check `print_agent_model_usage()`)
   - Check for network/region latency issues
   - Verify LLM connection pooling is working

3. **Quality Degradation**
   - Try Quality-First configuration for critical agents
   - Adjust temperature/max_tokens for specific agents
   - Review error logs for specific failure patterns

## Next Steps

After completing testing and benchmarking:

1. **Document Results**: Save benchmark results to version control
2. **Fine-tune Configuration**: Adjust model choices based on results
3. **Deploy to Production**: Use best-performing configuration
4. **Set up Monitoring**: Configure alerts for latency/cost/quality metrics
5. **A/B Testing**: Consider gradual rollout with traffic splitting

## Monitoring in Production

After deployment, monitor these metrics:

- Per-agent latency (p50, p95, p99)
- Per-agent token usage and cost
- Error rates by agent
- Overall user satisfaction metrics
- Model availability/reliability

Use the MLflow tracking added to the notebook to capture these metrics automatically.
