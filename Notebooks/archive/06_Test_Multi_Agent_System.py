# Databricks notebook source
# MAGIC %md
# MAGIC # Multi-Agent System Testing
# MAGIC 
# MAGIC This notebook tests the multi-agent system with various query types:
# MAGIC - Simple single-space queries
# MAGIC - Cross-domain queries with joins
# MAGIC - Unclear queries (clarification flow)
# MAGIC - Performance metrics
# MAGIC 
# MAGIC **Prerequisites:**
# MAGIC - Vector search index is ONLINE
# MAGIC - agent.py file exists in the same directory
# MAGIC - All dependencies installed

# COMMAND ----------

# MAGIC %md
# MAGIC ## Setup and Dependencies

# COMMAND ----------

# Install required packages
%pip install -U -qqq langgraph-supervisor==0.0.30 mlflow[databricks] databricks-langchain databricks-agents databricks-vectorsearch
dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Import Agent Module

# COMMAND ----------

import os
import time
import json
from datetime import datetime

print("="*80)
print("Importing Agent Module")
print("="*80)

# Check if agent.py exists
agent_file = "agent.py"
if os.path.exists(agent_file):
    print(f"✅ {agent_file} found")
    with open(agent_file, 'r') as f:
        print(f"   File size: {len(f.read())} bytes")
else:
    print(f"❌ {agent_file} not found!")
    print("   Please ensure agent.py is in the same directory as this notebook")
    dbutils.notebook.exit("Agent file not found")

# Import agent
try:
    from agent import AGENT
    print(f"\n✅ Successfully imported AGENT")
    print(f"   Agent type: {type(AGENT)}")
    print(f"   Agent class: {AGENT.__class__.__name__}")
except Exception as e:
    print(f"\n❌ Failed to import agent: {str(e)}")
    import traceback
    traceback.print_exc()
    dbutils.notebook.exit("Failed to import agent")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 1: Simple Single-Space Query

# COMMAND ----------

print("\n" + "="*80)
print("TEST 1: Simple Single-Space Query - Patient Demographics")
print("="*80)
print("Query: How many patients are older than 65 years?")
print("-"*80)

start_time = time.time()

try:
    input_example = {
        "input": [
            {"role": "user", "content": "How many patients are older than 65 years?"}
        ]
    }
    
    # Collect responses
    responses = []
    agent_calls = []
    
    print("\nAgent Processing:")
    print("-"*80)
    
    for event in AGENT.predict_stream(input_example):
        event_dict = event.model_dump(exclude_none=True)
        responses.append(event_dict)
        
        # Extract agent names
        if 'item' in event_dict:
            item = event_dict['item']
            if isinstance(item, dict) and 'content' in item:
                content = item['content']
                if isinstance(content, str):
                    if '<name>' in content and '</name>' in content:
                        agent_name = content.split('<name>')[1].split('</name>')[0]
                        agent_calls.append(agent_name)
                        print(f"\n🤖 Agent: {agent_name}")
                    else:
                        print(content[:300] + "..." if len(content) > 300 else content)
    
    duration = time.time() - start_time
    
    print(f"\n" + "-"*80)
    print(f"✅ Test 1 completed in {duration:.2f}s")
    print(f"   Agents called: {', '.join(agent_calls) if agent_calls else 'None detected'}")
    
    test1_result = {
        "success": True,
        "duration": duration,
        "agents": agent_calls
    }
    
except Exception as e:
    duration = time.time() - start_time
    print(f"\n❌ Test 1 failed: {str(e)}")
    import traceback
    traceback.print_exc()
    
    test1_result = {
        "success": False,
        "duration": duration,
        "error": str(e)
    }

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 2: Medication Query

# COMMAND ----------

print("\n" + "="*80)
print("TEST 2: Medication Query")
print("="*80)
print("Query: What are the most common medications prescribed?")
print("-"*80)

start_time = time.time()

try:
    input_example = {
        "input": [
            {"role": "user", "content": "What are the most common medications prescribed?"}
        ]
    }
    
    responses = []
    agent_calls = []
    
    print("\nAgent Processing:")
    print("-"*80)
    
    for event in AGENT.predict_stream(input_example):
        event_dict = event.model_dump(exclude_none=True)
        responses.append(event_dict)
        
        if 'item' in event_dict:
            item = event_dict['item']
            if isinstance(item, dict) and 'content' in item:
                content = item['content']
                if isinstance(content, str):
                    if '<name>' in content and '</name>' in content:
                        agent_name = content.split('<name>')[1].split('</name>')[0]
                        agent_calls.append(agent_name)
                        print(f"\n🤖 Agent: {agent_name}")
                    else:
                        print(content[:300] + "..." if len(content) > 300 else content)
    
    duration = time.time() - start_time
    
    print(f"\n" + "-"*80)
    print(f"✅ Test 2 completed in {duration:.2f}s")
    print(f"   Agents called: {', '.join(agent_calls) if agent_calls else 'None detected'}")
    
    test2_result = {
        "success": True,
        "duration": duration,
        "agents": agent_calls
    }
    
except Exception as e:
    duration = time.time() - start_time
    print(f"\n❌ Test 2 failed: {str(e)}")
    import traceback
    traceback.print_exc()
    
    test2_result = {
        "success": False,
        "duration": duration,
        "error": str(e)
    }

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 3: Cross-Domain Query with Join

# COMMAND ----------

print("\n" + "="*80)
print("TEST 3: Cross-Domain Query - Patients + Medications")
print("="*80)
print("Query: How many patients older than 50 years are on Voltaren?")
print("-"*80)

start_time = time.time()

try:
    input_example = {
        "input": [
            {"role": "user", "content": "How many patients older than 50 years are on Voltaren?"}
        ]
    }
    
    responses = []
    agent_calls = []
    
    print("\nAgent Processing:")
    print("-"*80)
    
    for event in AGENT.predict_stream(input_example):
        event_dict = event.model_dump(exclude_none=True)
        responses.append(event_dict)
        
        if 'item' in event_dict:
            item = event_dict['item']
            if isinstance(item, dict) and 'content' in item:
                content = item['content']
                if isinstance(content, str):
                    if '<name>' in content and '</name>' in content:
                        agent_name = content.split('<name>')[1].split('</name>')[0]
                        agent_calls.append(agent_name)
                        print(f"\n🤖 Agent: {agent_name}")
                    else:
                        print(content[:300] + "..." if len(content) > 300 else content)
    
    duration = time.time() - start_time
    
    print(f"\n" + "-"*80)
    print(f"✅ Test 3 completed in {duration:.2f}s")
    print(f"   Agents called: {', '.join(agent_calls) if agent_calls else 'None detected'}")
    
    test3_result = {
        "success": True,
        "duration": duration,
        "agents": agent_calls
    }
    
except Exception as e:
    duration = time.time() - start_time
    print(f"\n❌ Test 3 failed: {str(e)}")
    import traceback
    traceback.print_exc()
    
    test3_result = {
        "success": False,
        "duration": duration,
        "error": str(e)
    }

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 4: Multiple Spaces - Verbal Merge

# COMMAND ----------

print("\n" + "="*80)
print("TEST 4: Multiple Spaces - Verbal Merge")
print("="*80)
print("Query: What are the most common diagnoses and what are the most prescribed medications?")
print("-"*80)

start_time = time.time()

try:
    input_example = {
        "input": [
            {"role": "user", "content": "What are the most common diagnoses and what are the most prescribed medications?"}
        ]
    }
    
    responses = []
    agent_calls = []
    
    print("\nAgent Processing:")
    print("-"*80)
    
    for event in AGENT.predict_stream(input_example):
        event_dict = event.model_dump(exclude_none=True)
        responses.append(event_dict)
        
        if 'item' in event_dict:
            item = event_dict['item']
            if isinstance(item, dict) and 'content' in item:
                content = item['content']
                if isinstance(content, str):
                    if '<name>' in content and '</name>' in content:
                        agent_name = content.split('<name>')[1].split('</name>')[0]
                        agent_calls.append(agent_name)
                        print(f"\n🤖 Agent: {agent_name}")
                    else:
                        print(content[:300] + "..." if len(content) > 300 else content)
    
    duration = time.time() - start_time
    
    print(f"\n" + "-"*80)
    print(f"✅ Test 4 completed in {duration:.2f}s")
    print(f"   Agents called: {', '.join(agent_calls) if agent_calls else 'None detected'}")
    
    test4_result = {
        "success": True,
        "duration": duration,
        "agents": agent_calls
    }
    
except Exception as e:
    duration = time.time() - start_time
    print(f"\n❌ Test 4 failed: {str(e)}")
    import traceback
    traceback.print_exc()
    
    test4_result = {
        "success": False,
        "duration": duration,
        "error": str(e)
    }

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 5: Unclear Question - Clarification Flow

# COMMAND ----------

print("\n" + "="*80)
print("TEST 5: Unclear Question - Should Request Clarification")
print("="*80)
print("Query: Tell me about cancer patients")
print("-"*80)

start_time = time.time()

try:
    input_example = {
        "input": [
            {"role": "user", "content": "Tell me about cancer patients"}
        ]
    }
    
    responses = []
    agent_calls = []
    
    print("\nAgent Processing:")
    print("-"*80)
    
    for event in AGENT.predict_stream(input_example):
        event_dict = event.model_dump(exclude_none=True)
        responses.append(event_dict)
        
        if 'item' in event_dict:
            item = event_dict['item']
            if isinstance(item, dict) and 'content' in item:
                content = item['content']
                if isinstance(content, str):
                    if '<name>' in content and '</name>' in content:
                        agent_name = content.split('<name>')[1].split('</name>')[0]
                        agent_calls.append(agent_name)
                        print(f"\n🤖 Agent: {agent_name}")
                    else:
                        print(content[:300] + "..." if len(content) > 300 else content)
    
    duration = time.time() - start_time
    
    print(f"\n" + "-"*80)
    print(f"✅ Test 5 completed in {duration:.2f}s")
    print(f"   Agents called: {', '.join(agent_calls) if agent_calls else 'None detected'}")
    
    test5_result = {
        "success": True,
        "duration": duration,
        "agents": agent_calls
    }
    
except Exception as e:
    duration = time.time() - start_time
    print(f"\n❌ Test 5 failed: {str(e)}")
    import traceback
    traceback.print_exc()
    
    test5_result = {
        "success": False,
        "duration": duration,
        "error": str(e)
    }

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Summary

# COMMAND ----------

import pandas as pd

print("\n" + "="*80)
print("TEST SUMMARY")
print("="*80)

# Collect results
all_results = [
    {"Test": "1. Simple Patient Query", **test1_result},
    {"Test": "2. Medication Query", **test2_result},
    {"Test": "3. Cross-Domain Join", **test3_result},
    {"Test": "4. Multiple Spaces", **test4_result},
    {"Test": "5. Clarification Flow", **test5_result},
]

# Create DataFrame
df_results = pd.DataFrame(all_results)

# Calculate summary stats
total_tests = len(all_results)
passed = sum(1 for r in all_results if r.get('success', False))
failed = total_tests - passed
success_rate = (passed / total_tests * 100) if total_tests > 0 else 0

print(f"\nTotal Tests: {total_tests}")
print(f"Passed: {passed} ✅")
print(f"Failed: {failed} ❌")
print(f"Success Rate: {success_rate:.1f}%")

if all_results:
    durations = [r['duration'] for r in all_results if 'duration' in r]
    if durations:
        print(f"\nPerformance Metrics:")
        print(f"  Average Duration: {sum(durations)/len(durations):.2f}s")
        print(f"  Fastest Query: {min(durations):.2f}s")
        print(f"  Slowest Query: {max(durations):.2f}s")

print("\nDetailed Results:")
display(df_results)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Assessment

# COMMAND ----------

print("\n" + "="*80)
print("ASSESSMENT")
print("="*80)

if success_rate >= 80:
    print("\n✅ Excellent! Multi-agent system is working well.")
    print("\n✓ System is ready for:")
    print("  - More comprehensive testing")
    print("  - MLflow logging")
    print("  - Model registration")
    print("  - Deployment to serving endpoint")
    
elif success_rate >= 60:
    print("\n⚠️  Good progress, but some issues to address.")
    print("\nNext steps:")
    print("  - Review failed tests above")
    print("  - Check error messages")
    print("  - Verify Genie space accessibility")
    print("  - Rerun after fixes")
    
else:
    print("\n❌ Significant issues found.")
    print("\nNext steps:")
    print("  - Review all error messages")
    print("  - Check agent.py configuration")
    print("  - Verify vector search is working")
    print("  - Check Genie space IDs")
    print("  - Request help if needed")

print("\n" + "="*80)

# Save results for reference
test_summary = {
    "timestamp": datetime.now().isoformat(),
    "total_tests": total_tests,
    "passed": passed,
    "failed": failed,
    "success_rate": success_rate,
    "tests": all_results
}

# Display final message
if success_rate >= 80:
    displayHTML("""
    <div style='background-color: #d4edda; border: 2px solid #28a745; padding: 20px; border-radius: 5px;'>
        <h2 style='color: #155724; margin-top: 0;'>🎉 SUCCESS! Multi-Agent System is Operational</h2>
        <p style='color: #155724; font-size: 16px;'>
            Your multi-agent system is working correctly!<br><br>
            <strong>Ready for next steps:</strong>
            <ul>
                <li>Run additional test queries</li>
                <li>Log model to MLflow</li>
                <li>Deploy to model serving endpoint</li>
                <li>Set up monitoring and production use</li>
            </ul>
        </p>
    </div>
    """)
elif success_rate >= 60:
    displayHTML("""
    <div style='background-color: #fff3cd; border: 2px solid #ffc107; padding: 20px; border-radius: 5px;'>
        <h2 style='color: #856404; margin-top: 0;'>⚠️ PARTIAL SUCCESS - Review Issues</h2>
        <p style='color: #856404; font-size: 16px;'>
            Some tests passed, but issues were found.<br><br>
            <strong>Action required:</strong>
            <ul>
                <li>Review failed test error messages above</li>
                <li>Check Genie space accessibility</li>
                <li>Verify agent configuration</li>
                <li>Retest after fixes</li>
            </ul>
        </p>
    </div>
    """)
else:
    displayHTML("""
    <div style='background-color: #f8d7da; border: 2px solid #dc3545; padding: 20px; border-radius: 5px;'>
        <h2 style='color: #721c24; margin-top: 0;'>❌ ISSUES FOUND - Troubleshooting Needed</h2>
        <p style='color: #721c24; font-size: 16px;'>
            Multiple tests failed. Review errors above.<br><br>
            <strong>Recommended actions:</strong>
            <ul>
                <li>Check all error messages carefully</li>
                <li>Verify agent.py configuration</li>
                <li>Confirm vector search is working</li>
                <li>Validate Genie space IDs</li>
                <li>Review TEST_EXECUTION_REPORT.md for guidance</li>
            </ul>
        </p>
    </div>
    """)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next Steps
# MAGIC 
# MAGIC Based on your test results:
# MAGIC 
# MAGIC ### If All Tests Passed ✅
# MAGIC 
# MAGIC 1. **Run Notebook 05** - Complete deployment pipeline:
# MAGIC    - Log model to MLflow
# MAGIC    - Register to Model Registry
# MAGIC    - Deploy to serving endpoint
# MAGIC    - Test endpoint
# MAGIC 
# MAGIC 2. **Production Preparation**:
# MAGIC    - Set up monitoring
# MAGIC    - Create user documentation
# MAGIC    - Configure access controls
# MAGIC 
# MAGIC ### If Some Tests Failed ⚠️
# MAGIC 
# MAGIC 1. **Review Error Messages** - Look at the errors above
# MAGIC 2. **Check Configuration** - Verify agent.py settings
# MAGIC 3. **Test Individual Components**:
# MAGIC    - Vector search (run test_vector_search_detailed.py)
# MAGIC    - Genie spaces (verify accessibility)
# MAGIC    - LLM endpoint (check availability)
# MAGIC 4. **Rerun This Notebook** - After fixes
# MAGIC 
# MAGIC ### Additional Testing Ideas
# MAGIC 
# MAGIC Try these custom queries:
# MAGIC - "How many patients were diagnosed with lung cancer in 2023?"
# MAGIC - "Show me patients with abnormal lab results who underwent surgery"
# MAGIC - "What percentage of patients over 60 are on chemotherapy?"

