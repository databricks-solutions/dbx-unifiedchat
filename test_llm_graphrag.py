"""
Test LLM-powered GraphRAG integration.
"""
import asyncio
import json
import sys
from pathlib import Path
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.sql import StatementParameterListItem

# Add GraphRAG module to path
graphrag_path = Path(__file__).parent / "tables_to_genies" / "graphrag"
sys.path.insert(0, str(graphrag_path))

from build_table_graph import GraphRAGTableGraphBuilder


async def test_llm_graphrag():
    """Test the full LLM GraphRAG pipeline."""
    print("🧪 Testing LLM-Powered GraphRAG Integration...")
    print("=" * 70)
    
    # Initialize Databricks Client
    config = Config()
    client = WorkspaceClient(config=config)
    warehouse_id = "a4ed2ccbda385db9"
    table_fqn = "serverless_dbx_unifiedchat_catalog.gold.enriched_table_metadata"

    print(f"\n📊 Fetching full enriched metadata from {table_fqn}...")
    
    try:
        # Fetch enriched tables with full descriptions
        res = client.statement_execution.execute_statement(
            warehouse_id=warehouse_id,
            statement=f"SELECT table_fqn, enriched_doc FROM {table_fqn} WHERE enriched = true LIMIT 5",
            wait_timeout="30s"
        )
        
        if not res.result or not res.result.data_array:
            print("❌ No enriched tables found.")
            return

        print(f"✅ Found {len(res.result.data_array)} enriched tables.")
        
        # Parse enriched data
        enriched_tables_data = []
        for row in res.result.data_array:
            fqn = row[0]
            doc = json.loads(row[1])
            parts = fqn.split('.')
            
            enriched_tables_data.append({
                'fqn': fqn,
                'catalog': parts[0],
                'schema': parts[1],
                'table': parts[2],
                'column_count': doc.get('total_columns', 0),
                'columns': [{'name': col['column_name']} for col in doc.get('enriched_columns', [])],
                'enriched': True,
                'table_description': doc.get('table_description', ''),
                'enriched_columns': doc.get('enriched_columns', [])
            })
        
        # Show sample descriptions
        print("\n📝 Sample Table Descriptions:")
        for table in enriched_tables_data[:2]:
            print(f"  • {table['table']}: {table['table_description'][:100]}...")

        # Define LLM function
        async def llm_func(prompt: str) -> str:
            """LLM function for testing."""
            print(f"\n🤖 LLM Call (prompt length: {len(prompt)} chars)")
            
            def _llm_call():
                local_client = WorkspaceClient(config=Config())
                llm_statement = f"SELECT ai_query('databricks-claude-sonnet-4-5', :prompt) as result"
                param = StatementParameterListItem(name='prompt', value=prompt, type='STRING')
                return local_client.statement_execution.execute_statement(
                    warehouse_id=warehouse_id,
                    statement=llm_statement,
                    parameters=[param],
                    wait_timeout="50s"
                )
            
            llm_res = await asyncio.to_thread(_llm_call)
            
            if llm_res.result and llm_res.result.data_array:
                response = llm_res.result.data_array[0][0]
                print(f"✅ LLM Response (length: {len(response)} chars)")
                return response
            else:
                raise Exception("LLM call returned no results")

        # Build graph with LLM
        print("\n🏗️ Building Graph with LLM-Powered GraphRAG...")
        print("  1. Structural analysis...")
        print("  2. LLM entity extraction...")
        print("  3. LLM semantic relationship detection...")
        
        builder = GraphRAGTableGraphBuilder()
        G = await builder.build_graph(enriched_tables_data, llm_func=llm_func)
        
        print(f"\n✅ Graph Built: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        
        # Show semantic entities
        if builder.semantic_entities:
            print(f"\n🎯 LLM-Extracted Entities ({len(builder.semantic_entities)} tables):")
            for fqn, entities in list(builder.semantic_entities.items())[:3]:
                print(f"  • {fqn.split('.')[-1]}:")
                print(f"    Domain: {entities.get('domain', 'N/A')}")
                print(f"    Concepts: {', '.join(entities.get('concepts', [])[:3])}")
        
        # Show semantic edges
        semantic_edges = [(u, v, d) for u, v, d in G.edges(data=True) if 'semantic' in d.get('types', '')]
        if semantic_edges:
            print(f"\n🔗 Semantic Relationships ({len(semantic_edges)} discovered):")
            for u, v, data in semantic_edges[:5]:
                reason = data.get('semantic_reason', 'N/A')
                weight = data.get('weight', 0)
                print(f"  • {u.split('.')[-1]} <-> {v.split('.')[-1]}")
                print(f"    Weight: {weight}, Reason: {reason}")
        else:
            print("\n⚠️ No semantic edges discovered (LLM may have failed or no relationships found)")
        
        # Convert to Cytoscape format
        print("\n📊 Converting to Cytoscape format...")
        cyto_data = builder.to_cytoscape_format()
        print(f"✅ Generated {len(cyto_data['elements'])} elements")
        
        # Count edge types
        edge_elements = [e for e in cyto_data['elements'] if 'source' in e['data']]
        structural_edges = [e for e in edge_elements if 'semantic' not in e['data'].get('types', '')]
        semantic_edge_elements = [e for e in edge_elements if 'semantic' in e['data'].get('types', '')]
        
        print(f"  • Structural edges: {len(structural_edges)}")
        print(f"  • Semantic edges: {len(semantic_edge_elements)}")
        
        print("\n✅ LLM GraphRAG Integration Test PASSED!")

    except Exception as e:
        print(f"\n❌ Test FAILED: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_llm_graphrag())
