#!/usr/bin/env python3
"""
Multi-Agent System Test Script

Tests the multi-agent system with various query types:
- Simple single-space queries
- Cross-domain queries with joins
- Unclear queries (clarification flow)
- Performance metrics
"""

import os
import sys
import time
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add Notebooks directory to path so we can import agent
sys.path.insert(0, str(Path(__file__).parent / "Notebooks"))

print("\n" + "🤖" * 40)
print("MULTI-AGENT SYSTEM TEST")
print("🤖" * 40 + "\n")

# Test 0: Import Agent
print("="*80)
print("TEST 0: Import Agent Module")
print("="*80)

try:
    from agent import AGENT
    print("✅ Successfully imported AGENT from agent.py")
    print(f"   Agent type: {type(AGENT)}")
    print(f"   Agent class: {AGENT.__class__.__name__}")
except Exception as e:
    print(f"❌ Failed to import agent: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test Suite
test_results = []

def run_test(test_name: str, query: str, expected_agents: List[str] = None) -> Dict:
    """Run a single test query"""
    print(f"\n" + "="*80)
    print(f"TEST: {test_name}")
    print("="*80)
    print(f"Query: {query}")
    print("-"*80)
    
    start_time = time.time()
    
    try:
        input_data = {
            "input": [
                {"role": "user", "content": query}
            ]
        }
        
        # Collect streaming responses
        responses = []
        agent_calls = []
        
        print("\nAgent Response:")
        print("-"*80)
        
        for event in AGENT.predict_stream(input_data):
            event_dict = event.model_dump(exclude_none=True)
            responses.append(event_dict)
            
            # Try to extract agent names from events
            if 'item' in event_dict:
                item = event_dict['item']
                if isinstance(item, dict) and 'content' in item:
                    content = item['content']
                    if isinstance(content, str):
                        # Look for agent markers
                        if '<name>' in content and '</name>' in content:
                            agent_name = content.split('<name>')[1].split('</name>')[0]
                            agent_calls.append(agent_name)
                            print(f"\n🤖 Agent Called: {agent_name}")
                        else:
                            # Print content
                            print(content[:200] + "..." if len(content) > 200 else content)
        
        duration = time.time() - start_time
        
        # Try to get final response
        final_response = None
        for event in reversed(responses):
            if 'item' in event and isinstance(event['item'], dict):
                if 'content' in event['item']:
                    final_response = event['item']['content']
                    break
        
        print(f"\n" + "-"*80)
        print(f"✅ Test completed in {duration:.2f}s")
        
        # Verify expected agents were called
        if expected_agents:
            print(f"\nAgent Verification:")
            for expected in expected_agents:
                if any(expected.lower() in str(agent).lower() for agent in agent_calls):
                    print(f"  ✅ {expected} was called")
                else:
                    print(f"  ⚠️  {expected} not detected")
        
        return {
            "name": test_name,
            "query": query,
            "success": True,
            "duration": duration,
            "agent_calls": agent_calls,
            "response_length": len(str(final_response)) if final_response else 0,
            "response_preview": str(final_response)[:200] if final_response else "No response"
        }
        
    except Exception as e:
        duration = time.time() - start_time
        print(f"\n❌ Test failed: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            "name": test_name,
            "query": query,
            "success": False,
            "duration": duration,
            "error": str(e)
        }


# Run Test Suite
print("\n\n" + "🧪" * 40)
print("RUNNING TEST SUITE")
print("🧪" * 40 + "\n")

# Test 1: Simple single-space query
result1 = run_test(
    "Simple Single-Space Query",
    "How many patients are older than 65 years?",
    expected_agents=["ThinkingPlanning", "GENIE_PATIENT"]
)
test_results.append(result1)
time.sleep(2)  # Brief pause between tests

# Test 2: Another simple query
result2 = run_test(
    "Medication Query",
    "What are the most common medications prescribed?",
    expected_agents=["ThinkingPlanning", "MEDICATIONS"]
)
test_results.append(result2)
time.sleep(2)

# Test 3: Cross-domain query with join
result3 = run_test(
    "Cross-Domain Query with Join",
    "How many patients older than 50 years are on Voltaren?",
    expected_agents=["ThinkingPlanning", "SQLSynthesis", "SQLExecution"]
)
test_results.append(result3)
time.sleep(2)

# Test 4: Multiple spaces without join
result4 = run_test(
    "Multiple Spaces - Verbal Merge",
    "What are the most common diagnoses and what are the most prescribed medications?",
    expected_agents=["ThinkingPlanning"]
)
test_results.append(result4)
time.sleep(2)

# Test 5: Unclear question
result5 = run_test(
    "Unclear Question - Clarification Flow",
    "Tell me about cancer patients",
    expected_agents=["ThinkingPlanning"]
)
test_results.append(result5)

# Print Summary
print("\n\n" + "="*80)
print("TEST SUMMARY")
print("="*80)

passed = sum(1 for r in test_results if r['success'])
failed = sum(1 for r in test_results if not r['success'])
total = len(test_results)
success_rate = (passed / total * 100) if total > 0 else 0

print(f"\nTotal Tests: {total}")
print(f"Passed: {passed} ✅")
print(f"Failed: {failed} ❌")
print(f"Success Rate: {success_rate:.1f}%")

if test_results:
    avg_duration = sum(r['duration'] for r in test_results) / len(test_results)
    max_duration = max(r['duration'] for r in test_results)
    min_duration = min(r['duration'] for r in test_results)
    
    print(f"\nPerformance Metrics:")
    print(f"  Average Duration: {avg_duration:.2f}s")
    print(f"  Fastest Query: {min_duration:.2f}s")
    print(f"  Slowest Query: {max_duration:.2f}s")

print("\nDetailed Results:")
print("-"*80)

for i, result in enumerate(test_results, 1):
    status = "✅ PASS" if result['success'] else "❌ FAIL"
    print(f"\n{i}. {status} | {result['name']}")
    print(f"   Query: {result['query']}")
    print(f"   Duration: {result['duration']:.2f}s")
    
    if result['success']:
        if 'agent_calls' in result and result['agent_calls']:
            print(f"   Agents: {', '.join(result['agent_calls'])}")
        if 'response_preview' in result:
            print(f"   Response: {result['response_preview']}")
    else:
        if 'error' in result:
            print(f"   Error: {result['error']}")

# Save results
results_file = Path("agent_test_results.json")
with open(results_file, 'w') as f:
    json.dump({
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "success_rate": success_rate,
            "avg_duration": avg_duration if test_results else 0,
        },
        "tests": test_results
    }, f, indent=2)

print(f"\n📄 Results saved to: {results_file}")

# Final Assessment
print("\n" + "="*80)
print("ASSESSMENT")
print("="*80)

if success_rate >= 80:
    print("\n✅ Excellent! Multi-agent system is working well.")
    print("   Ready for more comprehensive testing and deployment.")
elif success_rate >= 60:
    print("\n⚠️  Good progress, but some issues to address.")
    print("   Review failed tests and error messages.")
else:
    print("\n❌ Significant issues found.")
    print("   Review errors and check configuration.")

print("\n" + "="*80)
print("\n")

sys.exit(0 if success_rate >= 60 else 1)

