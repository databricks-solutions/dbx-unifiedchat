"""
FastAPI routes following APX patterns.
All routes have response_model and operation_id (required for OpenAPI generation).
"""
from fastapi import APIRouter, HTTPException
from typing import List
from .models import *
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks import sql
import uuid
import threading

# Initialize SDK
config = Config()
client = WorkspaceClient(config=config)
warehouse_id = "a4ed2ccbda385db9"

# In-memory storage
_selection: TableSelectionOut = TableSelectionOut(table_fqns=[], count=0)
_enrichment_jobs = {}
_enrichment_results = []
_graph_data: Optional[GraphDataOut] = None
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
# ENRICHMENT ROUTES
# ============================================================================

def _run_enrichment_task(job_id: str, table_fqns: List[str]):
    """Background enrichment task."""
    global _enrichment_results
    
    try:
        conn = sql.connect(
            server_hostname=config.host,
            http_path=f"/sql/1.0/warehouses/{warehouse_id}",
            credentials_provider=lambda: config.authenticate,
        )
        
        for i, fqn in enumerate(table_fqns):
            try:
                parts = fqn.split('.')
                if len(parts) != 3:
                    continue
                
                catalog, schema, table = parts
                
                # Get columns
                cursor = conn.cursor()
                cursor.execute(f"DESCRIBE `{catalog}`.`{schema}`.`{table}`")
                columns_raw = cursor.fetchall()
                cursor.close()
                
                # Sample first column
                if columns_raw:
                    first_col = columns_raw[0][0]
                    cursor = conn.cursor()
                    cursor.execute(f"SELECT `{first_col}` FROM `{catalog}`.`{schema}`.`{table}` LIMIT 5")
                    samples = [str(row[0]) for row in cursor.fetchall()]
                    cursor.close()
                else:
                    samples = []
                
                columns = [
                    ColumnEnriched(
                        name=col[0],
                        type=col[1],
                        comment=col[2] if len(col) > 2 and col[2] else '',
                        sample_values=samples if idx == 0 else []
                    )
                    for idx, col in enumerate(columns_raw[:10])
                ]
                
                result = EnrichmentResultOut(
                    fqn=fqn,
                    catalog=catalog,
                    schema=schema,
                    table=table,
                    column_count=len(columns_raw),
                    columns=columns,
                    enriched=True,
                    timestamp=datetime.now().isoformat()
                )
                
                _enrichment_results.append(result)
                _enrichment_jobs[job_id]['progress'] = i + 1
                
            except Exception as e:
                print(f"Error enriching {fqn}: {e}")
        
        conn.close()
        _enrichment_jobs[job_id]['status'] = EnrichmentStatus.COMPLETED
        
    except Exception as e:
        _enrichment_jobs[job_id]['status'] = EnrichmentStatus.FAILED
        _enrichment_jobs[job_id]['error'] = str(e)


@api.post("/enrichment/run", response_model=EnrichmentStatusOut, operation_id="runEnrichment")
async def run_enrichment(enrichment_in: EnrichmentRunIn):
    """Start enrichment job."""
    job_id = f"enrich-{str(uuid.uuid4())[:8]}"
    
    _enrichment_jobs[job_id] = {
        'status': EnrichmentStatus.RUNNING,
        'progress': 0,
        'total': len(enrichment_in.table_fqns),
        'error': None
    }
    
    thread = threading.Thread(target=_run_enrichment_task, args=(job_id, enrichment_in.table_fqns))
    thread.daemon = True
    thread.start()
    
    return EnrichmentStatusOut(
        job_id=job_id,
        status=EnrichmentStatus.RUNNING,
        progress=0,
        total=len(enrichment_in.table_fqns)
    )


@api.get("/enrichment/status/{job_id}", response_model=EnrichmentStatusOut, operation_id="getEnrichmentStatus")
async def get_enrichment_status(job_id: str):
    """Get enrichment job status."""
    if job_id not in _enrichment_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = _enrichment_jobs[job_id]
    return EnrichmentStatusOut(
        job_id=job_id,
        status=job['status'],
        progress=job['progress'],
        total=job['total'],
        error=job.get('error')
    )


@api.get("/enrichment/results", response_model=List[EnrichmentResultListOut], operation_id="listEnrichmentResults")
async def list_enrichment_results():
    """List all enrichment results."""
    return [EnrichmentResultListOut(
        fqn=r.fqn,
        column_count=r.column_count,
        enriched=r.enriched
    ) for r in _enrichment_results]


@api.get("/enrichment/results/{fqn:path}", response_model=EnrichmentResultOut, operation_id="getEnrichmentResult")
async def get_enrichment_result(fqn: str):
    """Get enrichment result for a specific table."""
    for result in _enrichment_results:
        if result.fqn == fqn:
            return result
    raise HTTPException(status_code=404, detail="Result not found")


# ============================================================================
# GRAPH ROUTES
# ============================================================================

@api.post("/graph/build", response_model=GraphBuildStatusOut, operation_id="buildGraph")
async def build_graph():
    """Build table relationship graph."""
    global _graph_data
    
    if not _enrichment_results:
        raise HTTPException(status_code=400, detail="No enrichment results available")
    
    try:
        # Simplified graph building (full GraphRAG implementation in graph_builder.py)
        import networkx as nx
        
        G = nx.Graph()
        
        # Add nodes
        for result in _enrichment_results:
            parts = result.fqn.split('.')
            G.add_node(result.fqn, **{
                'label': parts[2],
                'catalog': parts[0],
                'schema': parts[1],
                'column_count': result.column_count,
                'community': 0
            })
        
        # Add edges (same schema)
        nodes = list(G.nodes(data=True))
        for i, (node1, data1) in enumerate(nodes):
            for node2, data2 in nodes[i+1:]:
                if data1['schema'] == data2['schema']:
                    G.add_edge(node1, node2, weight=5, types='same_schema')
        
        # Convert to Cytoscape format
        elements = []
        for node, data in G.nodes(data=True):
            elements.append({'data': {'id': node, **data}})
        for source, target, data in G.edges(data=True):
            elements.append({'data': {'source': source, 'target': target, **data}})
        
        _graph_data = GraphDataOut(
            elements=elements,
            node_count=G.number_of_nodes(),
            edge_count=G.number_of_edges()
        )
        
        return GraphBuildStatusOut(job_id="graph-1", status=GraphBuildStatus.COMPLETED)
        
    except Exception as e:
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
