"""
Graph builder module using simplified relationship detection.
"""
from typing import List, Dict, Any
import networkx as nx


class GraphBuilder:
    """Builds table relationship graph."""
    
    def __init__(self):
        self.graph = None
        self.graph_data = None
    
    def build_graph(self, enrichment_results: List[Dict[str, Any]]) -> str:
        """
        Build graph from enriched table metadata.
        Returns job_id for status tracking.
        """
        try:
            # Create NetworkX graph
            G = nx.Graph()
            
            # Add nodes (tables)
            for result in enrichment_results:
                if not result.get('enriched'):
                    continue
                
                fqn = result['fqn']
                parts = fqn.split('.')
                
                G.add_node(fqn, **{
                    'label': parts[2],
                    'catalog': parts[0],
                    'schema': parts[1],
                    'table': parts[2],
                    'column_count': result.get('column_count', 0),
                    'columns': result.get('columns', []),
                })
            
            # Add edges based on simple heuristics
            nodes = list(G.nodes())
            for i, node1 in enumerate(nodes):
                for node2 in nodes[i+1:]:
                    node1_data = G.nodes[node1]
                    node2_data = G.nodes[node2]
                    
                    weight = 0
                    edge_type = []
                    
                    # Same schema = strong relationship
                    if node1_data['schema'] == node2_data['schema']:
                        weight += 5
                        edge_type.append('same_schema')
                    
                    # Same catalog = moderate relationship
                    elif node1_data['catalog'] == node2_data['catalog']:
                        weight += 2
                        edge_type.append('same_catalog')
                    
                    # Column name overlap = potential FK
                    cols1 = {c['name'] for c in node1_data['columns']}
                    cols2 = {c['name'] for c in node2_data['columns']}
                    overlap = cols1 & cols2
                    if overlap:
                        weight += len(overlap)
                        edge_type.append('column_overlap')
                    
                    if weight > 0:
                        G.add_edge(node1, node2, weight=weight, edge_type=','.join(edge_type))
            
            self.graph = G
            
            # Convert to Cytoscape.js format
            self.graph_data = self._to_cytoscape_format(G)
            
            return "graph-1"
            
        except Exception as e:
            print(f"Error building graph: {e}")
            return None
    
    def _to_cytoscape_format(self, G: nx.Graph) -> Dict[str, Any]:
        """Convert NetworkX graph to Cytoscape.js format."""
        elements = []
        
        # Nodes
        for node, data in G.nodes(data=True):
            elements.append({
                'data': {
                    'id': node,
                    'label': data.get('label', node),
                    **data
                },
                'classes': data.get('schema', 'default')
            })
        
        # Edges
        for source, target, data in G.edges(data=True):
            elements.append({
                'data': {
                    'source': source,
                    'target': target,
                    'weight': data.get('weight', 1),
                    'edge_type': data.get('edge_type', '')
                }
            })
        
        return {
            'elements': elements,
            'node_count': G.number_of_nodes(),
            'edge_count': G.number_of_edges()
        }
    
    def get_graph_data(self) -> Dict[str, Any]:
        """Get graph data for visualization."""
        return self.graph_data if self.graph_data else {'elements': [], 'node_count': 0, 'edge_count': 0}
    
    def get_node_details(self, table_fqn: str) -> Dict[str, Any]:
        """Get detailed info for a specific node."""
        if self.graph and table_fqn in self.graph.nodes:
            return self.graph.nodes[table_fqn]
        return {}
