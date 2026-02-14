"""
GraphRAG-based table relationship graph builder.

Uses concepts from Microsoft GraphRAG:
- Entity extraction from table/column metadata
- Community detection for clustering related tables
- Semantic relationship discovery
"""
from typing import List, Dict, Any
import networkx as nx
from collections import defaultdict


class GraphRAGTableGraphBuilder:
    """
    Builds table relationship graph using GraphRAG concepts.
    
    Simplified implementation focusing on:
    - Schema/catalog co-location
    - Column name overlap (FK hints)
    - Community detection (Louvain algorithm)
    - Domain clustering
    """
    
    def __init__(self):
        self.graph = None
        self.communities = {}
    
    def extract_entities(self, enriched_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Extract entities from table metadata.
        
        Entities include:
        - Table names
        - Column names (as potential FK indicators)
        - Data types patterns
        - Schema/catalog groupings
        """
        entities = {
            'tables': {},
            'columns': defaultdict(list),
            'schemas': defaultdict(list),
            'catalogs': defaultdict(list),
        }
        
        for table_data in enriched_tables:
            if not table_data.get('enriched'):
                continue
            
            fqn = table_data['fqn']
            catalog = table_data.get('catalog', fqn.split('.')[0])
            schema = table_data.get('schema', fqn.split('.')[1])
            
            # Table entity
            entities['tables'][fqn] = {
                'fqn': fqn,
                'catalog': catalog,
                'schema': schema,
                'column_count': table_data.get('column_count', 0),
                'columns': [col.get('name') for col in table_data.get('columns', [])],
            }
            
            # Column entities (for FK detection)
            for col in table_data.get('columns', []):
                col_name = col.get('name')
                if col_name:
                    entities['columns'][col_name].append(fqn)
            
            # Schema/catalog groupings
            entities['schemas'][schema].append(fqn)
            entities['catalogs'][catalog].append(fqn)
        
        return entities
    
    def detect_relationships(self, entities: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Detect relationships between tables.
        
        Relationships:
        - Same schema (strong relationship)
        - Same catalog (moderate relationship)
        - Shared column names (potential FK)
        - Similar column patterns
        """
        relationships = []
        
        tables = entities['tables']
        table_fqns = list(tables.keys())
        
        for i, fqn1 in enumerate(table_fqns):
            for fqn2 in table_fqns[i+1:]:
                table1 = tables[fqn1]
                table2 = tables[fqn2]
                
                weight = 0
                rel_types = []
                
                # Same schema = strong relationship (5 points)
                if table1['schema'] == table2['schema']:
                    weight += 5
                    rel_types.append('same_schema')
                
                # Same catalog = moderate relationship (2 points)
                elif table1['catalog'] == table2['catalog']:
                    weight += 2
                    rel_types.append('same_catalog')
                
                # Column name overlap (1 point per shared column)
                cols1 = set(table1['columns'])
                cols2 = set(table2['columns'])
                overlap = cols1 & cols2
                if overlap:
                    weight += len(overlap)
                    rel_types.append(f'column_overlap_{len(overlap)}')
                
                # FK hints (column names ending with _id)
                id_cols1 = {c for c in cols1 if c.endswith('_id')}
                id_cols2 = {c for c in cols2 if c.endswith('_id')}
                if id_cols1 & id_cols2:
                    weight += 3
                    rel_types.append('fk_hint')
                
                if weight > 0:
                    relationships.append({
                        'source': fqn1,
                        'target': fqn2,
                        'weight': weight,
                        'types': rel_types
                    })
        
        return relationships
    
    def detect_communities(self, G: nx.Graph) -> Dict[str, int]:
        """
        Detect communities using Louvain algorithm.
        
        Returns mapping of node -> community_id
        """
        try:
            import community as community_louvain
            communities = community_louvain.best_partition(G)
            return communities
        except ImportError:
            # Fallback: use schema as community
            print("community package not available, using schema-based communities")
            communities = {}
            for node, data in G.nodes(data=True):
                communities[node] = hash(data.get('schema', 'default')) % 10
            return communities
    
    def build_graph(self, enriched_tables: List[Dict[str, Any]]) -> nx.Graph:
        """
        Build NetworkX graph with GraphRAG-detected relationships.
        
        Args:
            enriched_tables: List of enriched table metadata dicts
        
        Returns:
            NetworkX Graph with nodes (tables) and edges (relationships)
        """
        # Extract entities
        entities = self.extract_entities(enriched_tables)
        
        # Create graph
        G = nx.Graph()
        
        # Add nodes
        for fqn, table_data in entities['tables'].items():
            G.add_node(fqn, **table_data)
        
        # Add edges based on detected relationships
        relationships = self.detect_relationships(entities)
        for rel in relationships:
            G.add_edge(
                rel['source'],
                rel['target'],
                weight=rel['weight'],
                types=','.join(rel['types'])
            )
        
        # Detect communities
        if G.number_of_nodes() > 0:
            communities = self.detect_communities(G)
            for node, community_id in communities.items():
                G.nodes[node]['community'] = community_id
        
        self.graph = G
        self.communities = self._build_community_hierarchy(G)
        
        return G
    
    def _build_community_hierarchy(self, G: nx.Graph) -> Dict[int, List[str]]:
        """Build hierarchy of communities."""
        hierarchy = defaultdict(list)
        for node, data in G.nodes(data=True):
            community_id = data.get('community', 0)
            hierarchy[community_id].append(node)
        return dict(hierarchy)
    
    def to_cytoscape_format(self) -> Dict[str, Any]:
        """
        Convert graph to Cytoscape.js format.
        
        Returns:
            Dict with elements list for Cytoscape.js
        """
        if not self.graph:
            return {'elements': [], 'node_count': 0, 'edge_count': 0}
        
        elements = []
        
        # Nodes
        for node, data in self.graph.nodes(data=True):
            elements.append({
                'data': {
                    'id': node,
                    'label': data.get('table', node.split('.')[-1]),
                    'catalog': data.get('catalog', ''),
                    'schema': data.get('schema', ''),
                    'column_count': data.get('column_count', 0),
                    'community': data.get('community', 0),
                },
                'classes': f"community-{data.get('community', 0)}"
            })
        
        # Edges
        for source, target, data in self.graph.edges(data=True):
            elements.append({
                'data': {
                    'source': source,
                    'target': target,
                    'weight': data.get('weight', 1),
                    'types': data.get('types', ''),
                }
            })
        
        return {
            'elements': elements,
            'node_count': self.graph.number_of_nodes(),
            'edge_count': self.graph.number_of_edges(),
            'communities': self.communities
        }
    
    def get_node_details(self, table_fqn: str) -> Dict[str, Any]:
        """Get detailed information for a specific node."""
        if self.graph and table_fqn in self.graph.nodes:
            return self.graph.nodes[table_fqn]
        return {}
