"""
Tables-to-Genies: Multi-page Dash app for creating Genie rooms from UC tables.

Pages:
1. Catalog Browser - Browse and select UC tables
2. Enrichment Runner - Enrich selected tables with metadata
3. Graph Explorer - Visualize table relationships graph
4. Genie Room Builder - Select tables and create room definitions
5. Genie Room Creator - Create Genie rooms on Databricks
"""
import dash
from dash import Dash, html, dcc, callback, Input, Output, State, ctx
import dash_bootstrap_components as dbc
import os
import sys

# Initialize app with bootstrap theme
app = Dash(
    __name__,
    use_pages=False,  # We'll handle routing manually for wizard pattern
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True
)
server = app.server

# Add a test endpoint to verify connectivity
@server.route('/health')
def health():
    """Health check endpoint."""
    return {'status': 'ok', 'message': 'Dash app is running'}, 200

@server.route('/test-databricks')
def test_databricks():
    """Test Databricks connectivity."""
    try:
        from uc_browser import UCBrowser
        uc = UCBrowser()
        catalogs = uc.list_catalogs()
        return {
            'status': 'ok',
            'catalogs_count': len(catalogs),
            'catalogs': [c['name'] for c in catalogs[:5]]
        }, 200
    except Exception as e:
        return {
            'status': 'error',
            'message': str(e)
        }, 500

# Import backend modules (will create these next)
from uc_browser import UCBrowser
from enrichment import EnrichmentRunner
from graph_builder import GraphBuilder
from genie_creator import GenieCreator

# Initialize backend components
uc_browser = UCBrowser()
enrichment_runner = EnrichmentRunner()
graph_builder = GraphBuilder()
genie_creator = GenieCreator()

# Shared state stored in-memory
class AppState:
    def __init__(self):
        self.selected_tables = []
        self.enrichment_results = {}
        self.graph_data = None
        self.genie_rooms = []
        self.created_rooms = []

app_state = AppState()

# Define app layout with wizard navigation
app.layout = dbc.Container([
    dcc.Store(id='current-page', data='catalog-browser'),
    dcc.Store(id='selected-tables-store', data=[]),
    dcc.Store(id='enrichment-results-store', data={}),
    dcc.Store(id='graph-data-store', data=None),
    dcc.Store(id='genie-rooms-store', data=[]),
    
    dbc.Row([
        dbc.Col([
            html.H1("Tables-to-Genies", className="mb-4 mt-4"),
            html.P("Create Genie rooms from Unity Catalog tables", className="text-muted")
        ])
    ]),
    
    # Wizard navigation
    dbc.Row([
        dbc.Col([
            dbc.Nav(
                [
                    dbc.NavItem(dbc.NavLink("1. Browse Catalogs", id="nav-catalog", href="#", active=True)),
                    dbc.NavItem(dbc.NavLink("2. Enrich Tables", id="nav-enrichment", href="#", disabled=True)),
                    dbc.NavItem(dbc.NavLink("3. Explore Graph", id="nav-graph", href="#", disabled=True)),
                    dbc.NavItem(dbc.NavLink("4. Build Rooms", id="nav-builder", href="#", disabled=True)),
                    dbc.NavItem(dbc.NavLink("5. Create Rooms", id="nav-create", href="#", disabled=True)),
                ],
                pills=True,
                fill=True,
            )
        ], width=12, className="mb-4")
    ]),
    
    # Page content
    html.Div(id='page-content'),
    
], fluid=True)

# ============================================================================
# CALLBACKS
# ============================================================================

@callback(
    Output('page-content', 'children'),
    Output('nav-catalog', 'active'),
    Output('nav-enrichment', 'active'),
    Output('nav-enrichment', 'disabled'),
    Output('nav-graph', 'active'),
    Output('nav-graph', 'disabled'),
    Output('nav-builder', 'active'),
    Output('nav-builder', 'disabled'),
    Output('nav-create', 'active'),
    Output('nav-create', 'disabled'),
    Input('current-page', 'data'),
    Input('selected-tables-store', 'data'),
    Input('enrichment-results-store', 'data'),
    Input('graph-data-store', 'data'),
    Input('genie-rooms-store', 'data'),
)
def render_page(page, selected_tables, enrichment_results, graph_data, genie_rooms):
    """Render the current page and update navigation state."""
    
    # Navigation state
    nav_states = {
        'catalog': (page == 'catalog-browser', True, False),  # (active, class, disabled)
        'enrichment': (page == 'enrichment', 'nav-enrichment', len(selected_tables or []) == 0),
        'graph': (page == 'graph-explorer', 'nav-graph', not enrichment_results),
        'builder': (page == 'genie-builder', 'nav-builder', not graph_data),
        'create': (page == 'genie-create', 'nav-create', len(genie_rooms or []) == 0),
    }
    
    # Page content
    if page == 'catalog-browser':
        content = create_catalog_browser_page()
    elif page == 'enrichment':
        content = create_enrichment_page(selected_tables, enrichment_results)
    elif page == 'graph-explorer':
        content = create_graph_explorer_page(graph_data)
    elif page == 'genie-builder':
        content = create_genie_builder_page(graph_data, genie_rooms)
    elif page == 'genie-create':
        content = create_genie_create_page(genie_rooms)
    else:
        content = html.Div("Page not found")
    
    return (
        content,
        nav_states['catalog'][0], 
        nav_states['enrichment'][0], nav_states['enrichment'][2],
        nav_states['graph'][0], nav_states['graph'][2],
        nav_states['builder'][0], nav_states['builder'][2],
        nav_states['create'][0], nav_states['create'][2],
    )

# ============================================================================
# PAGE 1: CATALOG BROWSER
# ============================================================================

def create_catalog_browser_page():
    """Page 1: Browse UC catalogs and select tables."""
    return html.Div([
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader(html.H4("Browse Unity Catalog")),
                    dbc.CardBody([
                        dbc.InputGroup([
                            dbc.Input(
                                id="catalog-search-input",
                                placeholder="Search catalogs (contains ~2700)...",
                                type="text"
                            ),
                            dbc.Button("Search", id="search-catalogs-btn", color="info"),
                        ], className="mb-3"),
                        dbc.Button("Load Default Catalogs (max 100)", id="load-catalogs-btn", color="primary", className="mb-3"),
                        html.P("Note: Loading may take 30-60 seconds. You can also search for specific catalogs.", className="text-muted small"),
                        dcc.Loading(
                            id="loading-catalogs",
                            children=[html.Div(id="catalog-tree")],
                            type="default"
                        ),
                    ])
                ])
            ])
        ], className="mb-3"),
        
        dbc.Row([
            dbc.Col([
                html.Div(id="selection-summary", className="mb-3"),
                dbc.Button("Next: Enrich Tables", id="btn-to-enrichment", color="success", disabled=True)
            ])
        ])
    ])

@callback(
    Output('catalog-tree', 'children'),
    Input('load-catalogs-btn', 'n_clicks'),
    prevent_initial_call=True
)
def load_catalog_tree(n_clicks):
    """Load the first 100 catalogs with a hierarchy."""
    if not n_clicks:
        return html.Div()
    
    try:
        import sys
        import threading
        import time
        
        result = {'tree': None, 'error': None, 'status': 'loading'}
        
        def load_in_thread():
            try:
                print(f"[DEBUG] Loading catalog hierarchy...", file=sys.stderr, flush=True)
                hierarchy = uc_browser.get_table_hierarchy()
                print(f"[DEBUG] Got hierarchy with {len(hierarchy)} catalogs", file=sys.stderr, flush=True)
                result['tree'] = hierarchy
                result['status'] = 'complete'
            except Exception as e:
                print(f"[ERROR] Failed to load catalogs: {e}", file=sys.stderr, flush=True)
                import traceback
                print(traceback.format_exc(), file=sys.stderr, flush=True)
                result['error'] = str(e)
                result['status'] = 'error'
        
        # Run with timeout
        thread = threading.Thread(target=load_in_thread)
        thread.daemon = True
        thread.start()
        thread.join(timeout=60)  # 60 second timeout for full hierarchy
        
        if thread.is_alive():
            print(f"[ERROR] Catalog loading timed out after 60 seconds", file=sys.stderr, flush=True)
            return html.Div([
                dbc.Alert(
                    [
                        html.H5("Loading Timeout"),
                        html.P("Catalog loading took too long (>60 seconds). The workspace has many catalogs (~2700)."),
                        html.P("Try searching for specific catalogs instead of loading all.")
                    ],
                    color="warning"
                )
            ])
        
        if result['status'] == 'error':
            return html.Div([
                dbc.Alert(
                    [
                        html.H5("Error Loading Catalogs"),
                        html.P(f"Failed: {result['error']}"),
                        html.P("Please check your Databricks credentials.")
                    ],
                    color="danger"
                )
            ])
        
        hierarchy = result['tree']
        
        if not hierarchy:
            return html.Div([
                dbc.Alert(
                    "No catalogs found. Please check Databricks authentication.",
                    color="warning"
                )
            ])
        
        # Build tree UI
        tree_items = []
        for cat_name, cat_data in hierarchy.items():
            schemas_items = []
            for schema_name, schema_data in cat_data['schemas'].items():
                tables_items = []
                for table_name, table_data in schema_data['tables'].items():
                    fqn = table_data['fqn']
                    tables_items.append(
                        html.Li([
                            dcc.Checklist(
                                id={'type': 'table-checkbox', 'index': fqn},
                                options=[{'label': f" {table_name}", 'value': fqn}],
                                value=[],
                                inline=True
                            )
                        ])
                    )
                
                if tables_items:  # Only show schema if it has tables
                    schemas_items.append(
                        html.Details([
                            html.Summary(f"📁 {schema_name} ({len(tables_items)} tables)"),
                            html.Ul(tables_items, style={'list-style': 'none'})
                        ], open=False)
                    )
            
            if schemas_items:  # Only show catalog if it has schemas
                tree_items.append(
                    html.Details([
                        html.Summary(f"🗄️ {cat_name} ({len(schemas_items)} schemas with tables)"),
                        html.Div(schemas_items)
                    ], open=False)
                )
        
        if not tree_items:
            return html.Div([
                dbc.Alert(
                    f"Loaded {len(hierarchy)} catalogs but none have accessible tables.",
                    color="info"
                )
            ])
        
        return html.Div([
            dbc.Alert(f"✓ Loaded {len(tree_items)} catalogs with tables", color="success"),
            html.Div(tree_items)
        ])
        
    except Exception as e:
        import traceback
        import sys
        print(f"[ERROR] Unexpected error in load_catalog_tree: {e}", file=sys.stderr, flush=True)
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return html.Div([
            dbc.Alert(
                [
                    html.H5("Unexpected Error"),
                    html.P(f"An error occurred: {str(e)}")
                ],
                color="danger"
            )
        ])

@callback(
    Output('selected-tables-store', 'data'),
    Output('selection-summary', 'children'),
    Output('btn-to-enrichment', 'disabled'),
    Input({'type': 'table-checkbox', 'index': dash.ALL}, 'value'),
)
def update_selection(checkbox_values):
    """Update selected tables."""
    selected = []
    for values in checkbox_values:
        if values:
            selected.extend(values)
    
    app_state.selected_tables = selected
    
    summary = html.Div([
        dbc.Alert(
            f"Selected {len(selected)} tables",
            color="info" if selected else "secondary"
        )
    ])
    
    return selected, summary, len(selected) == 0

@callback(
    Output('current-page', 'data'),
    Input('btn-to-enrichment', 'n_clicks'),
    prevent_initial_call=True
)
def go_to_enrichment(n_clicks):
    """Navigate to enrichment page."""
    if n_clicks:
        return 'enrichment'
    return 'catalog-browser'

# ============================================================================
# PAGE 2: ENRICHMENT RUNNER
# ============================================================================

def create_enrichment_page(selected_tables, enrichment_results):
    """Page 2: Run enrichment on selected tables."""
    return html.Div([
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader(html.H4("Table Metadata Enrichment")),
                    dbc.CardBody([
                        html.P(f"Selected {len(selected_tables or [])} tables for enrichment"),
                        dbc.Button("Run Enrichment", id="run-enrichment-btn", color="primary", className="mb-3"),
                        html.Div(id="enrichment-progress"),
                        html.Div(id="enrichment-results"),
                    ])
                ])
            ])
        ], className="mb-3"),
        
        dbc.Row([
            dbc.Col([
                dbc.Button("Back", id="btn-enrich-back", color="secondary", className="me-2"),
                dbc.Button("Next: Explore Graph", id="btn-to-graph", color="success", disabled=True)
            ])
        ])
    ])

@callback(
    Output('enrichment-progress', 'children'),
    Output('enrichment-results', 'children'),
    Output('enrichment-results-store', 'data'),
    Output('btn-to-graph', 'disabled'),
    Input('run-enrichment-btn', 'n_clicks'),
    State('selected-tables-store', 'data'),
    prevent_initial_call=True
)
def run_enrichment(n_clicks, selected_tables):
    """Run enrichment job."""
    if not n_clicks or not selected_tables:
        return html.Div(), html.Div(), {}, True
    
    try:
        job_id = enrichment_runner.run_enrichment(selected_tables)
        
        # Poll status (simplified - in production use interval callback)
        import time
        for _ in range(60):
            status = enrichment_runner.get_status(job_id)
            if status['status'] in ['completed', 'failed']:
                break
            time.sleep(1)
        
        results = enrichment_runner.get_results()
        app_state.enrichment_results = {r['fqn']: r for r in results}
        
        progress_text = html.Div([
            dbc.Alert(f"✓ Enrichment completed: {len(results)} tables", color="success")
        ])
        
        results_table = dbc.Table([
            html.Thead([
                html.Tr([
                    html.Th("Table"),
                    html.Th("Columns"),
                    html.Th("Status")
                ])
            ]),
            html.Tbody([
                html.Tr([
                    html.Td(r['fqn']),
                    html.Td(r.get('column_count', 0)),
                    html.Td("✓" if r.get('enriched') else "✗")
                ])
                for r in results
            ])
        ], bordered=True, hover=True, striped=True)
        
        return progress_text, results_table, {r['fqn']: r for r in results}, False
        
    except Exception as e:
        return html.Div(f"Error: {e}", style={'color': 'red'}), html.Div(), {}, True

@callback(
    Output('current-page', 'data', allow_duplicate=True),
    Input('btn-to-graph', 'n_clicks'),
    Input('btn-enrich-back', 'n_clicks'),
    prevent_initial_call=True
)
def enrichment_navigation(next_clicks, back_clicks):
    """Handle navigation from enrichment page."""
    if ctx.triggered_id == 'btn-to-graph' and next_clicks:
        return 'graph-explorer'
    elif ctx.triggered_id == 'btn-enrich-back' and back_clicks:
        return 'catalog-browser'
    return dash.no_update

# ============================================================================
# PAGE 3: GRAPH EXPLORER (Simplified)
# ============================================================================

def create_graph_explorer_page(graph_data):
    """Page 3: Visualize table relationship graph."""
    return html.Div([
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader(html.H4("Table Relationship Graph")),
                    dbc.CardBody([
                        dbc.Button("Build Graph", id="build-graph-btn", color="primary", className="mb-3"),
                        html.Div(id="graph-status"),
                        html.Div(id="graph-viz", style={'height': '600px'}),
                    ])
                ])
            ])
        ], className="mb-3"),
        
        dbc.Row([
            dbc.Col([
                dbc.Button("Back", id="btn-graph-back", color="secondary", className="me-2"),
                dbc.Button("Next: Build Genie Rooms", id="btn-to-builder", color="success")
            ])
        ])
    ])

@callback(
    Output('graph-status', 'children'),
    Output('graph-viz', 'children'),
    Output('graph-data-store', 'data'),
    Input('build-graph-btn', 'n_clicks'),
    State('enrichment-results-store', 'data'),
    prevent_initial_call=True
)
def build_graph(n_clicks, enrichment_results):
    """Build the table relationship graph."""
    if not n_clicks or not enrichment_results:
        return html.Div(), html.Div(), None
    
    try:
        results_list = list(enrichment_results.values())
        job_id = graph_builder.build_graph(results_list)
        graph_data = graph_builder.get_graph_data()
        
        status_msg = dbc.Alert(
            f"✓ Graph built: {graph_data['node_count']} nodes, {graph_data['edge_count']} edges",
            color="success"
        )
        
        # Simple text representation (dash-cytoscape will be added later)
        viz = html.Div([
            html.P(f"Graph with {graph_data['node_count']} tables and {graph_data['edge_count']} relationships"),
            html.P("Interactive Cytoscape.js visualization will be added in next iteration.")
        ])
        
        return status_msg, viz, graph_data
        
    except Exception as e:
        return html.Div(f"Error: {e}", style={'color': 'red'}), html.Div(), None

@callback(
    Output('current-page', 'data', allow_duplicate=True),
    Input('btn-to-builder', 'n_clicks'),
    Input('btn-graph-back', 'n_clicks'),
    prevent_initial_call=True
)
def graph_navigation(next_clicks, back_clicks):
    """Handle navigation from graph page."""
    if ctx.triggered_id == 'btn-to-builder' and next_clicks:
        return 'genie-builder'
    elif ctx.triggered_id == 'btn-graph-back' and back_clicks:
        return 'enrichment'
    return dash.no_update

# ============================================================================
# PAGE 4: GENIE ROOM BUILDER
# ============================================================================

def create_genie_builder_page(graph_data, genie_rooms):
    """Page 4: Select tables and create room definitions."""
    return html.Div([
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader(html.H4("Build Genie Rooms")),
                    dbc.CardBody([
                        html.H5("Select tables for a Genie room:"),
                        dcc.Dropdown(
                            id='room-table-selector',
                            multi=True,
                            placeholder="Select tables...",
                            options=[{'label': elem['data']['id'], 'value': elem['data']['id']} 
                                   for elem in (graph_data or {}).get('elements', []) 
                                   if 'source' not in elem['data']]  # Only nodes, not edges
                        ),
                        dbc.Input(id='room-name-input', placeholder="Enter room name", className="mt-3"),
                        dbc.Button("Add Room", id="add-room-btn", color="primary", className="mt-2"),
                        html.Div(id="room-add-feedback", className="mt-2"),
                    ])
                ])
            ], width=6),
            
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader(html.H4("Planned Rooms")),
                    dbc.CardBody([
                        html.Div(id="planned-rooms-list")
                    ])
                ])
            ], width=6)
        ], className="mb-3"),
        
        dbc.Row([
            dbc.Col([
                dbc.Button("Back", id="btn-builder-back", color="secondary", className="me-2"),
                dbc.Button("Next: Create Rooms", id="btn-to-create", color="success", disabled=True)
            ])
        ])
    ])

@callback(
    Output('genie-rooms-store', 'data'),
    Output('room-add-feedback', 'children'),
    Output('room-name-input', 'value'),
    Output('room-table-selector', 'value'),
    Output('btn-to-create', 'disabled'),
    Input('add-room-btn', 'n_clicks'),
    State('room-name-input', 'value'),
    State('room-table-selector', 'value'),
    State('genie-rooms-store', 'data'),
    prevent_initial_call=True
)
def add_genie_room(n_clicks, room_name, selected_table_fqns, current_rooms):
    """Add a planned Genie room."""
    if not n_clicks or not room_name or not selected_table_fqns:
        return current_rooms or [], html.Div(), dash.no_update, dash.no_update, True
    
    room = genie_creator.add_room(room_name, selected_table_fqns)
    app_state.genie_rooms = genie_creator.get_rooms()
    
    feedback = dbc.Alert(f"✓ Added room: {room_name}", color="success", dismissable=True)
    
    return app_state.genie_rooms, feedback, "", [], len(app_state.genie_rooms) == 0

@callback(
    Output('planned-rooms-list', 'children'),
    Input('genie-rooms-store', 'data')
)
def render_planned_rooms(rooms):
    """Render the list of planned rooms."""
    if not rooms:
        return html.P("No rooms planned yet", className="text-muted")
    
    room_cards = []
    for room in rooms:
        room_cards.append(
            dbc.Card([
                dbc.CardHeader(html.H6(room['name'])),
                dbc.CardBody([
                    html.P(f"Tables: {room['table_count']}"),
                    html.Small(', '.join(room['tables'][:3]) + ("..." if len(room['tables']) > 3 else ""), className="text-muted")
                ])
            ], className="mb-2")
        )
    
    return html.Div(room_cards)

@callback(
    Output('current-page', 'data', allow_duplicate=True),
    Input('btn-to-create', 'n_clicks'),
    Input('btn-builder-back', 'n_clicks'),
    prevent_initial_call=True
)
def builder_navigation(next_clicks, back_clicks):
    """Handle navigation from builder page."""
    if ctx.triggered_id == 'btn-to-create' and next_clicks:
        return 'genie-create'
    elif ctx.triggered_id == 'btn-builder-back' and back_clicks:
        return 'graph-explorer'
    return dash.no_update

# ============================================================================
# PAGE 5: GENIE ROOM CREATOR
# ============================================================================

def create_genie_create_page(genie_rooms):
    """Page 5: Create all planned Genie rooms."""
    return html.Div([
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader(html.H4("Create Genie Rooms")),
                    dbc.CardBody([
                        html.P(f"Ready to create {len(genie_rooms or [])} Genie rooms"),
                        dbc.Button("Create All Rooms", id="create-all-btn", color="success", className="mb-3"),
                        html.Div(id="creation-status"),
                        html.Div(id="created-rooms-list"),
                    ])
                ])
            ])
        ], className="mb-3"),
        
        dbc.Row([
            dbc.Col([
                dbc.Button("Back", id="btn-create-back", color="secondary")
            ])
        ])
    ])

@callback(
    Output('creation-status', 'children'),
    Output('created-rooms-list', 'children'),
    Input('create-all-btn', 'n_clicks'),
    prevent_initial_call=True
)
def create_all_rooms(n_clicks):
    """Create all planned Genie rooms."""
    if not n_clicks:
        return html.Div(), html.Div()
    
    try:
        job_id = genie_creator.create_all_rooms()
        
        # Poll status (simplified)
        import time
        for _ in range(60):
            status = genie_creator.get_creation_status()
            if status['status'] in ['completed', 'failed']:
                break
            time.sleep(2)
        
        status_msg = dbc.Alert(
            f"✓ Room creation completed",
            color="success"
        )
        
        created_rooms = genie_creator.get_created_rooms()
        
        room_cards = []
        for room in created_rooms:
            room_cards.append(
                dbc.Card([
                    dbc.CardHeader(html.H6(room['name'])),
                    dbc.CardBody([
                        html.P(f"Tables: {room['table_count']}"),
                        html.A("Open Genie Space", href=room['url'], target="_blank", className="btn btn-primary btn-sm")
                    ])
                ], className="mb-2")
            )
        
        return status_msg, html.Div(room_cards)
        
    except Exception as e:
        return html.Div(f"Error: {e}", style={'color': 'red'}), html.Div()

@callback(
    Output('current-page', 'data', allow_duplicate=True),
    Input('btn-create-back', 'n_clicks'),
    prevent_initial_call=True
)
def create_navigation(back_clicks):
    """Handle navigation from create page."""
    if back_clicks:
        return 'genie-builder'
    return dash.no_update

# ============================================================================
# RUN APP
# ============================================================================

if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=8084,
        debug=True
    )

