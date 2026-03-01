# LLM-Powered GraphRAG Implementation Summary

## Overview

Successfully implemented LLM-based entity extraction and semantic relationship detection for the GraphRAG table relationship graph builder. The system now uses both structural analysis (schema/column patterns) and semantic analysis (LLM-powered) to discover relationships between tables.

## Implementation Details

### 1. GraphRAG Module Enhancement (`tables_to_genies/graphrag/build_table_graph.py`)

**New Async Methods:**

- `extract_entities_with_llm(enriched_tables, llm_func)` - Extracts semantic entities from table descriptions:
  - **Input**: Enriched table metadata with LLM-generated descriptions
  - **Output**: Dictionary mapping table FQN → {domain, concepts, themes}
  - **Example**: `{"catalog.schema.table": {"domain": "healthcare", "concepts": ["patient", "claim"], "themes": ["transactional"]}}`

- `detect_semantic_relationships(entities_by_table, llm_func)` - Identifies semantic relationships:
  - **Input**: Extracted entities for all tables
  - **Output**: List of relationship edges with confidence scores and reasons
  - **Example**: `{"source": "adjusters", "target": "claimants", "confidence": 8, "reason": "Both handle insurance claims lifecycle"}`

**Updated Method:**

- `build_graph(enriched_tables, llm_func=None)` - Now async and accepts optional LLM function:
  - Runs structural analysis (schema co-location, column overlap, FK hints)
  - Optionally runs LLM-based semantic analysis
  - Merges semantic edges into the graph with distinct edge types
  - Falls back gracefully if LLM analysis fails

### 2. Backend Router Update (`tables_to_genies_apx/src/tables_genies/backend/router.py`)

**Key Changes:**

- **Full Metadata Fetch**: Now retrieves complete `enriched_doc` JSON including:
  - `table_description` - LLM-synthesized table summaries
  - `enriched_columns` - Column details with LLM-enhanced comments
  
- **LLM Function Wrapper**: Implements async `llm_func` that:
  - Calls `ai_query('databricks-claude-sonnet-4-5', prompt)` via Statement Execution API
  - Uses proper `StatementParameterListItem` for parameter passing
  - Runs non-blocking via `asyncio.to_thread()`
  - 50-second timeout (maximum allowed by API)

- **Enhanced Logging**: Tracks all LLM analysis steps:
  - Entity extraction progress
  - Semantic relationship discovery
  - Sample outputs for verification

### 3. UI Enhancement (`tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/graph-explorer.tsx`)

**Visual Distinction:**

- **Semantic Edges** (LLM-discovered):
  - Purple color (#a855f7)
  - Dashed line style
  - Arrow indicators
  - Higher opacity (0.8)
  
- **Structural Edges** (schema/column-based):
  - Gray color (#cbd5e1)
  - Solid line style
  - Lower opacity (0.6)

**Legend**: Displays count of semantic vs structural relationships when semantic edges are present.

## Test Results

Test execution (`test_llm_graphrag.py`) verified:

✅ **LLM Entity Extraction** - Successfully extracted entities for 5 tables:
- Domain classification (e.g., "artificial_intelligence", "sports", "healthcare")
- Business concepts (e.g., "language_model", "championship", "vitamin")
- Data themes (e.g., "benchmark", "performance_evaluation", "dietary_guideline")

✅ **Semantic Relationship Discovery** - Found 1 semantic relationship:
- Source: `adjusters` ↔ Target: `claimants`
- Weight: 15
- Reason: "Both are core entities in the insurance claims domain... representing complementary operational roles in the claims lifecycle"

✅ **Graph Construction**:
- 5 nodes (tables)
- 10 total edges (9 structural + 1 semantic)
- Community detection applied
- Cytoscape format generated successfully

## Architecture

```
┌─────────────────────────┐
│ Enriched Table Metadata │
│ (Unity Catalog)         │
└───────────┬─────────────┘
            │
            ├─────────────────────┐
            │                     │
            ▼                     ▼
    ┌───────────────┐     ┌──────────────────┐
    │  Structural   │     │   LLM Entity     │
    │   Analysis    │     │   Extraction     │
    │               │     │  (Claude Sonnet) │
    └───────┬───────┘     └────────┬─────────┘
            │                      │
            │                      ▼
            │              ┌──────────────────┐
            │              │  LLM Semantic    │
            │              │  Relationships   │
            │              │  (Claude Sonnet) │
            │              └────────┬─────────┘
            │                       │
            └───────────┬───────────┘
                        ▼
                ┌───────────────┐
                │  NetworkX     │
                │  Graph Merge  │
                └───────┬───────┘
                        │
                        ▼
                ┌───────────────┐
                │  Community    │
                │  Detection    │
                └───────┬───────┘
                        │
                        ▼
                ┌───────────────┐
                │  Cytoscape.js │
                │  Visualization│
                └───────────────┘
```

## Key Technical Decisions

1. **No `graphrag` Package**: Avoided the Microsoft GraphRAG library (heavy dependency, version conflicts) in favor of direct LLM calls via Databricks Foundation Models.

2. **Statement Execution API**: Used non-blocking `statement_execution.execute_statement()` wrapped in `asyncio.to_thread()` for async LLM calls.

3. **Proper Parameter Passing**: Used `StatementParameterListItem` objects for safe parameter injection into SQL statements.

4. **Graceful Degradation**: System falls back to structural-only graph if LLM analysis fails.

5. **Edge Type Merging**: When semantic and structural edges overlap, they're combined with augmented weights and dual type labels.

## Files Modified

- `tables_to_genies/graphrag/build_table_graph.py` - Added LLM entity extraction and semantic relationship detection
- `tables_to_genies_apx/src/tables_genies/backend/router.py` - Updated build_graph endpoint with LLM integration
- `tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/graph-explorer.tsx` - Added visual distinction for semantic edges
- `test_llm_graphrag.py` - Created comprehensive integration test (retained for future verification)

## Next Steps

The LLM-powered GraphRAG integration is complete and ready for use. Users can now:

1. Navigate to the "Explore Graph" page
2. Click "Build Graph" to trigger LLM-powered analysis
3. View the graph with visually distinct semantic (purple dashed) and structural (gray solid) edges
4. Hover/click on semantic edges to see LLM-generated relationship reasons

The system will automatically enrich the graph with semantic relationships discovered by Claude Sonnet 4.5, providing deeper insights into table relationships beyond simple structural patterns.
