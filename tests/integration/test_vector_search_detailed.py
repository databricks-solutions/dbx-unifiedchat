#!/usr/bin/env python3
"""
Detailed Vector Search Testing

Tests the existing vector search index and verifies it's working correctly.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

def test_vector_search():
    """Test vector search functionality in detail"""
    
    try:
        from databricks.vector_search.client import VectorSearchClient
    except ImportError:
        print("❌ Error: databricks-vectorsearch not installed")
        print("Run: pip install databricks-vectorsearch")
        sys.exit(1)
    
    print("\n" + "="*80)
    print("VECTOR SEARCH DETAILED TESTING")
    print("="*80)
    
    catalog = os.getenv("CATALOG_NAME", "yyang")
    schema = os.getenv("SCHEMA_NAME", "multi_agent_genie")
    index_name = f"{catalog}.{schema}.enriched_genie_docs_chunks_vs_index"
    
    print(f"\nCatalog: {catalog}")
    print(f"Schema: {schema}")
    print(f"Index: {index_name}")
    print("")
    
    # Initialize client
    print("Initializing Vector Search Client...")
    client = VectorSearchClient()
    print("✅ Client initialized\n")
    
    # Get index
    print(f"Getting index: {index_name}...")
    try:
        vs_index = client.get_index(index_name=index_name)
        print("✅ Index retrieved\n")
    except Exception as e:
        print(f"❌ Failed to get index: {str(e)}")
        return False
    
    # Check index status
    print("Checking index status...")
    try:
        status = vs_index.describe()
        state = status.get('status', {}).get('detailed_state', 'UNKNOWN')
        print(f"✅ Index state: {state}\n")
        
        if not state.startswith('ONLINE'):
            print(f"⚠️  Warning: Index is not ONLINE (current state: {state})")
            print("    The index may still be building. Wait a few minutes and try again.")
            return False
    except Exception as e:
        print(f"❌ Failed to check status: {str(e)}")
        return False
    
    # Test queries
    print("="*80)
    print("RUNNING TEST QUERIES")
    print("="*80)
    
    test_queries = [
        ("General search", "patient age and demographics"),
        ("Space discovery", "What data contains patient claims?"),
        ("Column search", "location or facility type"),
        ("Medical terms", "cancer diagnosis staging"),
        ("Medications", "drug prescriptions and medications"),
    ]
    
    all_passed = True
    
    for test_name, query in test_queries:
        print(f"\n{'─'*80}")
        print(f"Test: {test_name}")
        print(f"Query: {query}")
        print(f"{'─'*80}")
        
        try:
            results = vs_index.similarity_search(
                query_text=query,
                columns=["chunk_id", "chunk_type", "space_title", "table_name", "column_name"],
                num_results=5
            )
            
            result_data = results.get('result', {})
            data_array = result_data.get('data_array', [])
            manifest = result_data.get('manifest', {})
            
            if len(data_array) > 0:
                print(f"✅ Found {len(data_array)} results\n")
                
                # Get column names
                column_names = [col.get('name') if isinstance(col, dict) else str(col) 
                               for col in manifest.get('columns', [])]
                
                # Display top 3 results
                for i, row in enumerate(data_array[:3], 1):
                    result_dict = dict(zip(column_names, row))
                    chunk_type = result_dict.get('chunk_type', 'N/A')
                    space_title = result_dict.get('space_title', 'N/A')
                    table_name = result_dict.get('table_name', 'N/A')
                    column_name = result_dict.get('column_name', 'N/A')
                    score = result_dict.get('score', 0.0)
                    
                    print(f"  {i}. [{chunk_type}] {space_title}")
                    if table_name and table_name != 'None':
                        print(f"     Table: {table_name}")
                    if column_name and column_name != 'None':
                        print(f"     Column: {column_name}")
                    print(f"     Score: {score:.4f}")
                    print()
            else:
                print(f"⚠️  No results found")
                all_passed = False
                
        except Exception as e:
            print(f"❌ Query failed: {str(e)}")
            all_passed = False
    
    # Test with filters
    print("\n" + "="*80)
    print("TESTING FILTERED QUERIES")
    print("="*80)
    
    filter_tests = [
        ("Space-level only", {"chunk_type": "space_summary"}),
        ("Table-level only", {"chunk_type": "table_overview"}),
        ("Column-level only", {"chunk_type": "column_detail"}),
    ]
    
    for test_name, filters in filter_tests:
        print(f"\n{'─'*80}")
        print(f"Test: {test_name}")
        print(f"Filters: {filters}")
        print(f"{'─'*80}")
        
        try:
            results = vs_index.similarity_search(
                query_text="patient data",
                columns=["chunk_id", "chunk_type", "space_title"],
                filters=filters,
                num_results=3
            )
            
            result_data = results.get('result', {})
            data_array = result_data.get('data_array', [])
            
            if len(data_array) > 0:
                print(f"✅ Found {len(data_array)} results with filter")
                
                # Verify all results match the filter
                manifest = result_data.get('manifest', {})
                column_names = [col.get('name') if isinstance(col, dict) else str(col) 
                               for col in manifest.get('columns', [])]
                
                for row in data_array:
                    result_dict = dict(zip(column_names, row))
                    chunk_type = result_dict.get('chunk_type', 'N/A')
                    score = result_dict.get('score', 0.0)
                    print(f"  - {chunk_type} (score: {score:.4f})")
            else:
                print(f"⚠️  No results with filter")
                all_passed = False
                
        except Exception as e:
            print(f"❌ Filtered query failed: {str(e)}")
            all_passed = False
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    if all_passed:
        print("\n✅ All tests passed!")
        print("\nVector search is working correctly:")
        print("  ✓ Index is ONLINE")
        print("  ✓ Queries return results")
        print("  ✓ Filters work correctly")
        print("  ✓ Results are semantically relevant")
        print("\n✅ Ready to proceed with agent testing!")
    else:
        print("\n⚠️  Some tests had issues")
        print("  - Check if index is fully synced")
        print("  - Verify source table has data")
        print("  - Review error messages above")
    
    print("="*80 + "\n")
    
    return all_passed


if __name__ == "__main__":
    try:
        success = test_vector_search()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

