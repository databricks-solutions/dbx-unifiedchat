"""
FastAPI routes following APX patterns.
All routes have response_model and operation_id (required for OpenAPI generation).
"""
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from .models import *
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.jobs import Task, NotebookTask, JobEnvironment, Source
from databricks.sdk.service.compute import Environment
from databricks.sdk.service.sql import StatementParameterListItem
from databricks import sql
import uuid
import threading
import os
import json
import asyncio
from datetime import datetime

# Initialize SDK
config = Config()
client = WorkspaceClient(config=config)
warehouse_id = "a4ed2ccbda385db9"

# In-memory storage
_selection: TableSelectionOut = TableSelectionOut(table_fqns=[], count=0)
_graph_data: Optional[GraphDataOut] = None
_graph_build_logs: List[GraphBuildLogOut] = []
_genie_rooms = []
_genie_creation_status: Optional[GenieCreationStatusOut] = None

api = APIRouter(prefix="/api")

# ============================================================================
# UC CATALOG BROWSER ROUTES
# ============================================================================

@api.get("/uc/catalogs", response_model=List[CatalogOut], operation_id="listCatalogs")
async def list_catalogs():
    """List all catalogs."""
    try:
        catalogs = list(client.catalogs.list())
        return [CatalogOut(
            name=cat.name,
            comment=cat.comment,
            owner=cat.owner
        ) for cat in catalogs]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api.get("/uc/catalogs/{catalog}/schemas", response_model=List[SchemaOut], operation_id="listSchemas")
async def list_schemas(catalog: str):
    """List schemas in a catalog."""
    try:
        schemas = list(client.schemas.list(catalog_name=catalog))
        return [SchemaOut(
            name=schema.name,
            catalog_name=schema.catalog_name,
            comment=schema.comment,
            owner=schema.owner
        ) for schema in schemas]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api.get("/uc/catalogs/{catalog}/schemas/{schema}/tables", response_model=List[TableOut], operation_id="listTables")
async def list_tables(catalog: str, schema: str):
    """List tables in a schema."""
    try:
        tables = list(client.tables.list(catalog_name=catalog, schema_name=schema))
        return [TableOut(
            name=table.name,
            catalog_name=table.catalog_name,
            schema_name=table.schema_name,
            table_type=table.table_type.value if table.table_type else 'TABLE',
            comment=table.comment,
            owner=table.owner,
            fqn=f"{table.catalog_name}.{table.schema_name}.{table.name}"
        ) for table in tables]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api.get("/uc/tables/{fqn:path}/columns", response_model=List[ColumnOut], operation_id="getTableColumns")
async def get_table_columns(fqn: str):
    """Get columns for a table."""
    try:
        table = client.tables.get(full_name=fqn)
        if not table.columns:
            return []
        
        return [ColumnOut(
            name=col.name,
            type_text=col.type_text,
            type_name=col.type_name.value if col.type_name else col.type_text,
            comment=col.comment or '',
            nullable=col.nullable if col.nullable is not None else True,
            position=col.position
        ) for col in table.columns]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api.post("/uc/selection", response_model=TableSelectionOut, operation_id="saveSelection")
async def save_selection(selection: TableSelectionIn):
    """Save table selection."""
    global _selection
    _selection = TableSelectionOut(
        table_fqns=selection.table_fqns,
        count=len(selection.table_fqns)
    )
    return _selection


@api.get("/uc/selection", response_model=TableSelectionOut, operation_id="getSelection")
async def get_selection():
    """Get current table selection."""
    return _selection


# ============================================================================
# ENRICHMENT ROUTES - DATABRICKS JOB SUBMISSION
# ============================================================================

# Configuration for enrichment job
ENRICHMENT_SCRIPT_PATH = os.getenv(
    "ENRICHMENT_SCRIPT_PATH", 
    "/Workspace/Users/yang.yang@databricks.com/tables_to_genies/etl/enrich_tables_direct.py"
)

@api.post("/enrichment/run", response_model=EnrichmentJobOut, operation_id="runEnrichment")
async def run_enrichment(enrichment_in: EnrichmentRunIn):
    """Run table enrichment job on Databricks."""
    
    # Prepare parameters for the job
    table_fqns_str = ','.join(enrichment_in.table_fqns)
    
    # Job name for persistent job definition
    job_name = "Table Enrichment Job"
    
    # Check if job already exists
    existing_jobs = list(client.jobs.list(name=job_name))
    
    if existing_jobs:
        # Use existing job
        job_id = existing_jobs[0].job_id
        print(f"Using existing job: {job_id}")
    else:
        # Create new persistent job using notebook task
        print(f"Creating new job: {job_name}")
        job = client.jobs.create(
            name=job_name,
            tasks=[
                Task(
                    task_key="enrich_tables",
                    notebook_task=NotebookTask(
                        notebook_path=ENRICHMENT_SCRIPT_PATH,
                        source=Source.WORKSPACE
                        # base_parameters will be overridden on each run via run_now()
                    ),
                    environment_key="serverless_env"
                )
            ],
            environments=[
                JobEnvironment(
                    environment_key="serverless_env",
                    spec=Environment(
                        client="1"  # Serverless environment
                    )
                )
            ]
        )
        job_id = job.job_id
        print(f"Created job: {job_id}")
    
    # Run the job with notebook parameters
    run_result = client.jobs.run_now(
        job_id=job_id,
        notebook_params={
            "tables": table_fqns_str,
            "sample_size": "20",
            "max_unique_values": "50",
            "llm_endpoint": "databricks-claude-sonnet-4-5",
            "metadata_table": enrichment_in.metadata_table,
            "chunks_table": enrichment_in.chunks_table,
            "write_mode": enrichment_in.write_mode
        }
    )
    
    # Get run details to access run_page_url
    run_details = client.jobs.get_run(run_id=run_result.run_id)
    job_url = run_details.run_page_url
    
    return EnrichmentJobOut(
        run_id=run_result.run_id,
        job_url=job_url,
        status=EnrichmentStatus.PENDING,
        table_count=len(enrichment_in.table_fqns),
        submitted_at=datetime.now().isoformat()
    )


@api.get("/enrichment/status/{run_id}", response_model=EnrichmentJobStatusOut, operation_id="getEnrichmentStatus")
async def get_enrichment_status(run_id: int):
    """Get Databricks job status."""
    
    try:
        run = client.jobs.get_run(run_id=run_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Job run not found: {str(e)}")
    
    # Map Databricks states to our status
    life_cycle_state = run.state.life_cycle_state.value if run.state.life_cycle_state else None
    result_state = run.state.result_state.value if run.state.result_state else None
    
    status = EnrichmentStatus.PENDING
    if life_cycle_state == "RUNNING" or life_cycle_state == "PENDING":
        status = EnrichmentStatus.RUNNING
    elif life_cycle_state == "TERMINATING":
        status = EnrichmentStatus.RUNNING  # Still considered running
    elif life_cycle_state == "TERMINATED":
        if result_state == "SUCCESS":
            status = EnrichmentStatus.COMPLETED
        elif result_state == "FAILED":
            status = EnrichmentStatus.FAILED
        elif result_state == "CANCELED":
            status = EnrichmentStatus.CANCELLED
        else:
            status = EnrichmentStatus.FAILED  # Default to failed for unknown result states
    elif life_cycle_state == "SKIPPED":
        status = EnrichmentStatus.CANCELLED
    elif life_cycle_state == "INTERNAL_ERROR":
        status = EnrichmentStatus.FAILED
    
    # Use the correct job URL from Databricks
    job_url = run.run_page_url
    
    return EnrichmentJobStatusOut(
        run_id=run_id,
        status=status,
        job_url=job_url,
        life_cycle_state=life_cycle_state,
        result_state=result_state,
        state_message=run.state.state_message,
        start_time=run.start_time,
        end_time=run.end_time,
        duration_ms=(run.end_time - run.start_time) if run.end_time and run.start_time else None
    )


@api.get("/enrichment/results", response_model=List[EnrichmentResultListOut], operation_id="listEnrichmentResults")
async def list_enrichment_results():
    """List enriched tables from Unity Catalog."""
    
    try:
        # Use Statement Execution API (non-blocking)
        statement = """
            SELECT table_fqn, enriched_doc 
            FROM serverless_dbx_unifiedchat_catalog.gold.enriched_table_metadata 
            WHERE enriched = true
            ORDER BY id DESC
            LIMIT 100
        """
        
        # Run blocking SDK call in thread pool
        def _fetch():
            # Create a fresh client for this thread
            local_client = WorkspaceClient(config=Config())
            return local_client.statement_execution.execute_statement(
                warehouse_id=warehouse_id,
                statement=statement,
                wait_timeout="30s"
            )
            
        res = await asyncio.to_thread(_fetch)
        
        results = []
        if res.result and res.result.data_array:
            for row in res.result.data_array:
                table_fqn = row[0]
                enriched_doc = json.loads(row[1])
                
                results.append(EnrichmentResultListOut(
                    fqn=table_fqn,
                    column_count=enriched_doc.get('total_columns', 0),
                    enriched=enriched_doc.get('enriched', False),
                    columns=[col['column_name'] for col in enriched_doc.get('enriched_columns', [])]
                ))
        
        return results
        
    except Exception as e:
        print(f"Error fetching enrichment results: {e}")
        return []


@api.get("/enrichment/results/{fqn:path}", response_model=EnrichmentResultOut, operation_id="getEnrichmentResult")
async def get_enrichment_result(fqn: str):
    """Get enrichment result for specific table from Unity Catalog."""
    
    try:
        # Use Statement Execution API (non-blocking)
        statement = f"SELECT enriched_doc FROM serverless_dbx_unifiedchat_catalog.gold.enriched_table_metadata WHERE table_fqn = '{fqn}'"
        
        def _fetch():
            local_client = WorkspaceClient(config=Config())
            return local_client.statement_execution.execute_statement(
                warehouse_id=warehouse_id,
                statement=statement,
                wait_timeout="30s"
            )
            
        res = await asyncio.to_thread(_fetch)
        
        if not res.result or not res.result.data_array:
            raise HTTPException(status_code=404, detail=f"Table {fqn} not found")
        
        enriched_doc = json.loads(res.result.data_array[0][0])
        
        # Convert to response model
        return EnrichmentResultOut(
            fqn=enriched_doc['table_fqn'],
            catalog=enriched_doc['catalog'],
            schema=enriched_doc['schema'],
            table=enriched_doc['table'],
            column_count=enriched_doc['total_columns'],
            columns=[
                ColumnEnriched(
                    name=col['column_name'],
                    type=col['data_type'],
                    comment=col.get('enhanced_comment', col.get('comment', '')),
                    sample_values=col.get('sample_values', [])
                )
                for col in enriched_doc['enriched_columns'][:10]
            ],
            enriched=enriched_doc['enriched'],
            timestamp=enriched_doc['enrichment_timestamp']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching result: {str(e)}")


# ============================================================================
# GRAPH ROUTES
# ============================================================================

def _add_graph_log(message: str, level: str = "info"):
    """Add a log entry for graph building."""
    global _graph_build_logs
    _graph_build_logs.append(GraphBuildLogOut(
        timestamp=datetime.now().strftime("%H:%M:%S"),
        message=message,
        level=level
    ))
    print(f"[{level.upper()}] {message}")

@api.get("/graph/build-logs", response_model=List[GraphBuildLogOut], operation_id="getGraphBuildLogs")
async def get_graph_build_logs():
    """Get graph build logs."""
    return _graph_build_logs

@api.post("/graph/build", response_model=GraphBuildStatusOut, operation_id="buildGraph")
async def build_graph():
    """Build table relationship graph using LLM-powered GraphRAG approach."""
    global _graph_data, _graph_build_logs
    
    _graph_build_logs = []
    _add_graph_log("Starting LLM-powered GraphRAG graph build process...")
    
    # Fetch full enriched documents with table descriptions
    _add_graph_log("Fetching full enrichment metadata from Unity Catalog...")
    
    try:
        # Fetch full enriched_doc JSON
        statement = """
            SELECT table_fqn, enriched_doc 
            FROM serverless_dbx_unifiedchat_catalog.gold.enriched_table_metadata 
            WHERE enriched = true
            ORDER BY id DESC
            LIMIT 100
        """
        
        def _fetch():
            local_client = WorkspaceClient(config=Config())
            return local_client.statement_execution.execute_statement(
                warehouse_id=warehouse_id,
                statement=statement,
                wait_timeout="30s"
            )
            
        res = await asyncio.to_thread(_fetch)
        
        if not res.result or not res.result.data_array:
            _add_graph_log("No enrichment results available", level="error")
            raise HTTPException(status_code=400, detail="No enrichment results available")
        
        _add_graph_log(f"Found {len(res.result.data_array)} enriched tables.")
        
        # Import GraphRAG module
        import sys
        from pathlib import Path
        
        current_file = Path(__file__).resolve()
        root_path = current_file.parents[5]
        graphrag_path = root_path / "tables_to_genies" / "graphrag"
        
        if not graphrag_path.exists():
            possible_roots = [Path.cwd(), Path.home() / "CursorProjects/KUMC_POC_hlsfieldtemp"]
            for pr in possible_roots:
                gp = pr / "tables_to_genies" / "graphrag"
                if gp.exists():
                    graphrag_path = gp
                    break
        
        if not graphrag_path.exists():
            _add_graph_log(f"Error: GraphRAG path does not exist: {graphrag_path}", level="error")
            raise HTTPException(status_code=500, detail=f"GraphRAG path not found: {graphrag_path}")
            
        if str(graphrag_path) not in sys.path:
            sys.path.insert(0, str(graphrag_path))
        
        from build_table_graph import GraphRAGTableGraphBuilder
        
        _add_graph_log("Initializing GraphRAG Table Graph Builder...")
        builder = GraphRAGTableGraphBuilder()
        
        # Parse enriched documents
        _add_graph_log("Parsing enriched metadata (descriptions, columns)...")
        enriched_tables_data = []
        for row in res.result.data_array:
            table_fqn = row[0]
            enriched_doc = json.loads(row[1])
            parts = table_fqn.split('.')
            
            enriched_tables_data.append({
                'fqn': table_fqn,
                'catalog': parts[0],
                'schema': parts[1],
                'table': parts[2],
                'column_count': enriched_doc.get('total_columns', 0),
                'columns': [{'name': col['column_name']} for col in enriched_doc.get('enriched_columns', [])],
                'enriched': enriched_doc.get('enriched', False),
                'table_description': enriched_doc.get('table_description', ''),
                'enriched_columns': enriched_doc.get('enriched_columns', [])
            })
        
        # Define LLM function wrapper
        async def llm_func(prompt: str) -> str:
            """Call LLM via ai_query through statement execution."""
            _add_graph_log("Calling LLM for analysis...")
            
            def _llm_call():
                local_client = WorkspaceClient(config=Config())
                # Use ai_query with Claude Sonnet
                llm_statement = "SELECT ai_query('databricks-claude-sonnet-4-5', :prompt) as result"
                param = StatementParameterListItem(name='prompt', value=prompt, type='STRING')
                return local_client.statement_execution.execute_statement(
                    warehouse_id=warehouse_id,
                    statement=llm_statement,
                    parameters=[param],
                    wait_timeout="50s"
                )
            
            llm_res = await asyncio.to_thread(_llm_call)
            
            if llm_res.result and llm_res.result.data_array:
                return llm_res.result.data_array[0][0]
            else:
                raise Exception("LLM call returned no results")
        
        # Build graph with LLM-powered semantic analysis
        _add_graph_log("Building graph with structural + semantic analysis...")
        _add_graph_log("  → Phase 1: Structural analysis (schema, columns, FK hints)...")
        _add_graph_log("  → Phase 2: LLM entity extraction...")
        _add_graph_log("  → Phase 3: LLM semantic relationship detection...")
        
        G = await builder.build_graph(enriched_tables_data, llm_func=llm_func)
        
        _add_graph_log(f"Graph constructed: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.")
        
        # Log semantic entities if extracted
        if builder.semantic_entities:
            _add_graph_log(f"LLM extracted entities for {len(builder.semantic_entities)} tables.")
            sample_fqn = list(builder.semantic_entities.keys())[0]
            sample_entities = builder.semantic_entities[sample_fqn]
            _add_graph_log(f"  Example: {sample_fqn.split('.')[-1]} → domain: {sample_entities.get('domain', 'N/A')}")
        
        # Count semantic edges
        semantic_edge_count = sum(1 for _, _, data in G.edges(data=True) if 'semantic' in data.get('types', ''))
        if semantic_edge_count > 0:
            _add_graph_log(f"Discovered {semantic_edge_count} semantic relationships via LLM.", level="success")
            
            # Log sample semantic relationships
            for u, v, data in list(G.edges(data=True))[:3]:
                if 'semantic' in data.get('types', ''):
                    reason = data.get('semantic_reason', 'N/A')
                    _add_graph_log(f"  {u.split('.')[-1]} <-> {v.split('.')[-1]}: {reason}")
        
        # Detect communities
        _add_graph_log("Running community detection...")
        num_communities = len(set(G.nodes[n].get('community', 0) for n in G.nodes()))
        _add_graph_log(f"Identified {num_communities} distinct communities.")
        
        # Convert to Cytoscape format
        _add_graph_log("Formatting graph for visualization...")
        graph_output = builder.to_cytoscape_format()
        
        _graph_data = GraphDataOut(
            elements=graph_output['elements'],
            node_count=graph_output['node_count'],
            edge_count=graph_output['edge_count'],
            communities=graph_output.get('communities')
        )
        
        _add_graph_log("LLM-powered GraphRAG build completed successfully.", level="success")
        return GraphBuildStatusOut(job_id="graph-1", status=GraphBuildStatus.COMPLETED)
        
    except Exception as e:
        import traceback
        _add_graph_log(f"Error building graph: {str(e)}", level="error")
        _add_graph_log(f"Traceback: {traceback.format_exc()}", level="error")
        raise HTTPException(status_code=500, detail=str(e))


@api.get("/graph/data", response_model=GraphDataOut, operation_id="getGraphData")
async def get_graph_data():
    """Get graph data."""
    if not _graph_data:
        raise HTTPException(status_code=404, detail="Graph not built yet")
    return _graph_data


# ============================================================================
# GENIE ROOM ROUTES
# ============================================================================

@api.post("/genie/rooms", response_model=GenieRoomOut, operation_id="createGenieRoom")
async def create_genie_room(room_in: GenieRoomIn):
    """Create a Genie room definition."""
    room = GenieRoomOut(
        id=f"room-{str(uuid.uuid4())[:8]}",
        name=room_in.name,
        tables=room_in.table_fqns,
        table_count=len(room_in.table_fqns)
    )
    _genie_rooms.append(room)
    return room


@api.get("/genie/rooms", response_model=List[GenieRoomListOut], operation_id="listGenieRooms")
async def list_genie_rooms():
    """List all planned Genie rooms."""
    return [GenieRoomListOut(
        id=r.id,
        name=r.name,
        table_count=r.table_count
    ) for r in _genie_rooms]


@api.delete("/genie/rooms/{room_id}", operation_id="deleteGenieRoom")
async def delete_genie_room(room_id: str):
    """Delete a planned room."""
    global _genie_rooms
    _genie_rooms = [r for r in _genie_rooms if r.id != room_id]
    return {"message": "Room deleted"}


def _create_rooms_task():
    """Background task for creating Genie spaces."""
    global _genie_creation_status
    
    try:
        for i, room in enumerate(_genie_creation_status.rooms):
            try:
                room['status'] = 'creating'
                
                # Create Genie space
                space = client.genie.create_space(
                    display_name=room['name'],
                    description=f"Genie space with {room['table_count']} tables"
                )
                
                # Update with table identifiers
                client.genie.update_space(
                    id=space.id,
                    display_name=room['name'],
                    table_identifiers=room['tables'],
                    sql_warehouse_id=warehouse_id
                )
                
                room['status'] = 'created'
                room['space_id'] = space.id
                room['url'] = f"https://{config.host}/sql/genie/{space.id}"
                
            except Exception as e:
                room['status'] = 'failed'
                room['error'] = str(e)
        
        _genie_creation_status.status = 'completed'
        
    except Exception as e:
        _genie_creation_status.status = 'failed'


@api.post("/genie/create-all", response_model=GenieCreationStatusOut, operation_id="createAllGenieRooms")
async def create_all_genie_rooms():
    """Create all planned Genie rooms."""
    global _genie_creation_status
    
    if not _genie_rooms:
        raise HTTPException(status_code=400, detail="No rooms planned")
    
    _genie_creation_status = GenieCreationStatusOut(
        status='creating',
        rooms=[{
            'id': r.id,
            'name': r.name,
            'tables': r.tables,
            'table_count': r.table_count,
            'status': 'pending',
            'space_id': None,
            'url': None,
            'error': None
        } for r in _genie_rooms]
    )
    
    thread = threading.Thread(target=_create_rooms_task)
    thread.daemon = True
    thread.start()
    
    return _genie_creation_status


@api.get("/genie/create-status", response_model=GenieCreationStatusOut, operation_id="getGenieCreationStatus")
async def get_genie_creation_status():
    """Get creation status."""
    if not _genie_creation_status:
        raise HTTPException(status_code=404, detail="No creation in progress")
    return _genie_creation_status


@api.get("/genie/created", response_model=List[CreatedGenieRoomOut], operation_id="listCreatedGenieRooms")
async def list_created_genie_rooms():
    """List created Genie rooms."""
    if not _genie_creation_status or _genie_creation_status.status != 'completed':
        return []
    
    return [CreatedGenieRoomOut(
        id=r['id'],
        name=r['name'],
        space_id=r['space_id'],
        url=r['url'],
        table_count=r['table_count'],
        status=GenieRoomStatus.CREATED
    ) for r in _genie_creation_status.rooms if r['status'] == 'created']


def _execute_preview_query(table_fqn: str):
    """Helper function to execute preview queries using Spark SQL."""
    from pyspark.sql import SparkSession
    from datetime import date, datetime
    from decimal import Decimal
    
    # Get or create Spark session
    spark = SparkSession.builder.getOrCreate()
    
    # Get row count
    count_df = spark.sql(f"SELECT COUNT(*) as count FROM {table_fqn}")
    row_count = count_df.collect()[0][0]
    
    # Get sample rows
    sample_df = spark.sql(f"SELECT * FROM {table_fqn} LIMIT 10")
    
    # Get column names
    columns = sample_df.columns
    
    # Convert rows to dictionaries with JSON-serializable types
    rows = []
    for row in sample_df.collect():
        row_dict = {}
        for col in columns:
            val = row[col]
            
            # Convert to JSON-serializable types
            if val is None:
                row_dict[col] = None
            elif isinstance(val, (date, datetime)):
                row_dict[col] = val.isoformat()
            elif isinstance(val, Decimal):
                row_dict[col] = float(val)
            elif isinstance(val, (bytes, bytearray)):
                row_dict[col] = val.hex()
            elif isinstance(val, (list, dict)):
                row_dict[col] = str(val) if len(str(val)) < 1000 else str(val)[:1000] + "..."
            else:
                val_str = str(val)
                row_dict[col] = val_str if len(val_str) < 1000 else val_str[:1000] + "..."
        
        rows.append(row_dict)
    
    return row_count, columns, rows

@api.get("/enrichment/preview/{table_fqn:path}", response_model=TablePreviewOut, operation_id="previewEnrichmentTable")
async def preview_enrichment_table(table_fqn: str):
    """Preview a table from Unity Catalog."""
    
    try:
        # Parse table FQN
        parts = table_fqn.split(".")
        if len(parts) != 3:
            raise HTTPException(status_code=400, detail="Table FQN must be in format catalog.schema.table")
        
        catalog_name, schema_name, table_name = parts
        
        # Run blocking SDK call in thread pool to avoid blocking event loop
        row_count, columns, rows = await asyncio.to_thread(_execute_preview_query, table_fqn)
        
        return TablePreviewOut(
            table_fqn=table_fqn,
            row_count=row_count,
            columns=columns,
            sample_rows=rows
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to preview table: {str(e)}")
