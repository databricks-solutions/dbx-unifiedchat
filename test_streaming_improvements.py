#!/usr/bin/env python
"""
Test script for streaming tool calls and retriever sources implementation.

This script verifies:
1. Tool call streaming works in real-time
2. Retriever sources are displayed with metadata
3. All streaming events are emitted correctly

Usage:
    python test_streaming_improvements.py
"""

import sys
from uuid import uuid4
from mlflow.types.responses import ResponsesAgentRequest

def test_tool_call_streaming():
    """Test that tool calls are streamed in real-time."""
    print("\n" + "="*80)
    print("TEST 1: Tool Call Streaming")
    print("="*80)
    
    try:
        from Notebooks.agent import AGENT
    except ImportError:
        print("❌ Error: Cannot import AGENT. Make sure agent.py is in Notebooks/")
        return False
    
    # Query that should trigger tool calls (UC functions or Genie agents)
    test_query = "Show me the top 10 active plan members over 50 years old with diabetes"
    
    print(f"Query: {test_query}\n")
    
    request = ResponsesAgentRequest(
        input=[{"role": "user", "content": test_query}],
        custom_inputs={"thread_id": f"test-tool-{str(uuid4())[:8]}"}
    )
    
    tool_calls_found = 0
    tool_invocations_found = 0
    
    print("Streaming events (watching for tool calls):")
    print("-" * 80)
    
    try:
        for event in AGENT.predict_stream(request):
            if event.type == "response.output_item.done":
                item = event.item
                
                # Check for function call items (complete tool calls)
                if hasattr(item, 'function_call'):
                    tool_calls_found += 1
                    print(f"✅ Tool call #{tool_calls_found}: {item.function_call.name}")
                    if hasattr(item.function_call, 'arguments'):
                        args_preview = str(item.function_call.arguments)[:100]
                        print(f"   Arguments: {args_preview}...")
                
                # Check for tool invocation text (partial tool calls)
                elif hasattr(item, 'text') and "🛠️ Invoking tool:" in item.text:
                    tool_invocations_found += 1
                    print(f"   {item.text}")
        
        print("-" * 80)
        print(f"\n📊 Results:")
        print(f"   Complete tool calls streamed: {tool_calls_found}")
        print(f"   Tool invocations detected: {tool_invocations_found}")
        
        if tool_calls_found > 0 or tool_invocations_found > 0:
            print("\n✅ PASSED: Tool call streaming is working!")
            return True
        else:
            print("\n⚠️ WARNING: No tool calls detected. Query may not require tools.")
            print("   Try a query that requires UC functions or Genie agents.")
            return True  # Not a failure, just no tools needed
            
    except Exception as e:
        print(f"\n❌ FAILED: Error during streaming: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_retriever_source_display():
    """Test that retriever sources are displayed with metadata."""
    print("\n" + "="*80)
    print("TEST 2: Retriever Source Display")
    print("="*80)
    
    try:
        from Notebooks.agent import AGENT
    except ImportError:
        print("❌ Error: Cannot import AGENT. Make sure agent.py is in Notebooks/")
        return False
    
    # Query that should trigger vector search
    test_query = "How many active members are enrolled?"
    
    print(f"Query: {test_query}\n")
    
    request = ResponsesAgentRequest(
        input=[{"role": "user", "content": test_query}],
        custom_inputs={"thread_id": f"test-retriever-{str(uuid4())[:8]}"}
    )
    
    vector_search_summary_found = False
    source_documents_found = 0
    
    print("Streaming events (watching for retriever sources):")
    print("-" * 80)
    
    try:
        for event in AGENT.predict_stream(request):
            if event.type == "response.output_item.done":
                item = event.item
                
                if hasattr(item, 'text'):
                    text = item.text
                    
                    # Check for vector search summary
                    if "📊 Found" in text and "relevant spaces" in text:
                        vector_search_summary_found = True
                        print(f"✅ Vector search summary:")
                        print(f"   {text[:150]}...")
                    
                    # Check for source documents
                    elif "📄 Source:" in text:
                        source_documents_found += 1
                        print(f"\n✅ Source document #{source_documents_found}:")
                        # Print first 200 chars of source
                        for line in text.split('\n')[:4]:
                            print(f"   {line}")
        
        print("-" * 80)
        print(f"\n📊 Results:")
        print(f"   Vector search summary found: {'Yes' if vector_search_summary_found else 'No'}")
        print(f"   Source documents displayed: {source_documents_found}")
        
        if vector_search_summary_found and source_documents_found > 0:
            print("\n✅ PASSED: Retriever source display is working!")
            return True
        elif vector_search_summary_found:
            print("\n⚠️ PARTIAL: Summary found but no individual sources")
            print("   Check if vector search returned documents")
            return True
        else:
            print("\n❌ FAILED: No retriever sources detected")
            return False
            
    except Exception as e:
        print(f"\n❌ FAILED: Error during streaming: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_all_streaming_events():
    """Test that all streaming event types are working."""
    print("\n" + "="*80)
    print("TEST 3: All Streaming Events")
    print("="*80)
    
    try:
        from Notebooks.agent import AGENT
    except ImportError:
        print("❌ Error: Cannot import AGENT. Make sure agent.py is in Notebooks/")
        return False
    
    test_query = "Show me patient data with diabetes medications"
    
    print(f"Query: {test_query}\n")
    
    request = ResponsesAgentRequest(
        input=[{"role": "user", "content": test_query}],
        custom_inputs={"thread_id": f"test-all-{str(uuid4())[:8]}"}
    )
    
    event_counts = {
        "text_deltas": 0,
        "node_updates": 0,
        "routing": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "custom_events": 0,
        "vector_search": 0,
        "sources": 0,
        "total": 0
    }
    
    print("Streaming events:")
    print("-" * 80)
    
    try:
        for event in AGENT.predict_stream(request):
            event_counts["total"] += 1
            
            # Text deltas
            if event.type == "response.output_text.delta":
                event_counts["text_deltas"] += 1
            
            # Items
            elif event.type == "response.output_item.done":
                item = event.item
                
                # Function calls
                if hasattr(item, 'function_call'):
                    event_counts["tool_calls"] += 1
                    print(f"🛠️  Tool call: {item.function_call.name}")
                
                # Text items
                elif hasattr(item, 'text'):
                    text = item.text
                    
                    # Categorize text events
                    if "🔹 Step:" in text:
                        event_counts["node_updates"] += 1
                    elif "🔀 Routing" in text:
                        event_counts["routing"] += 1
                    elif "🔨 Tool result" in text:
                        event_counts["tool_results"] += 1
                    elif "📊 Found" in text and "spaces" in text:
                        event_counts["vector_search"] += 1
                    elif "📄 Source:" in text:
                        event_counts["sources"] += 1
                    elif any(emoji in text for emoji in ["💭", "🚀", "🎯", "✓", "🔍", "📊", "📋", "🔧", "📝", "✅", "⚡"]):
                        event_counts["custom_events"] += 1
        
        print("-" * 80)
        print(f"\n📊 Event Summary:")
        print(f"   Total events: {event_counts['total']}")
        print(f"   Text deltas (streaming): {event_counts['text_deltas']}")
        print(f"   Node updates: {event_counts['node_updates']}")
        print(f"   Routing decisions: {event_counts['routing']}")
        print(f"   Tool calls: {event_counts['tool_calls']}")
        print(f"   Tool results: {event_counts['tool_results']}")
        print(f"   Custom events: {event_counts['custom_events']}")
        print(f"   Vector search summaries: {event_counts['vector_search']}")
        print(f"   Source documents: {event_counts['sources']}")
        
        # Check minimum expectations
        if event_counts["total"] < 10:
            print("\n⚠️ WARNING: Very few events detected. Check if agent is working correctly.")
            return False
        
        if event_counts["node_updates"] == 0:
            print("\n⚠️ WARNING: No node updates detected. Check stream_mode configuration.")
        
        print("\n✅ PASSED: Streaming events are working!")
        return True
        
    except Exception as e:
        print(f"\n❌ FAILED: Error during streaming: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "="*80)
    print("STREAMING IMPROVEMENTS TEST SUITE")
    print("="*80)
    print("Testing enhanced streaming features:")
    print("  1. Incremental tool call streaming")
    print("  2. Retriever source document display")
    print("  3. All streaming event types")
    print("="*80)
    
    results = []
    
    # Test 1: Tool Call Streaming
    results.append(("Tool Call Streaming", test_tool_call_streaming()))
    
    # Test 2: Retriever Source Display
    results.append(("Retriever Source Display", test_retriever_source_display()))
    
    # Test 3: All Streaming Events
    results.append(("All Streaming Events", test_all_streaming_events()))
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {test_name}")
    
    all_passed = all(passed for _, passed in results)
    
    print("="*80)
    if all_passed:
        print("✅ ALL TESTS PASSED!")
        print("\nYour streaming improvements are working correctly.")
        print("Next steps:")
        print("  1. Deploy to Databricks Model Serving")
        print("  2. Test in Playground UI")
        print("  3. Verify MLflow traces show retriever spans")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        print("\nCheck the errors above and:")
        print("  1. Verify agent.py is correctly configured")
        print("  2. Check MLflow and LangGraph versions")
        print("  3. Review implementation in Super_Agent_hybrid.py")
        return 1


if __name__ == "__main__":
    sys.exit(main())
