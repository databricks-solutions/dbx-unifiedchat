"""
Unity Catalog browser module.
Provides methods to list catalogs, schemas, tables, and columns.
"""
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from typing import List, Dict, Any
import os


class UCBrowser:
    """Unity Catalog browser using Databricks SDK."""
    
    def __init__(self):
        # Use Config() for automatic credential detection
        self.config = Config()
        self.client = WorkspaceClient(config=self.config)
    
    def list_catalogs(self) -> List[Dict[str, Any]]:
        """List all catalogs in the workspace."""
        try:
            catalogs = list(self.client.catalogs.list())
            return [{
                'name': cat.name,
                'comment': cat.comment or '',
                'owner': cat.owner or '',
            } for cat in catalogs]
        except Exception as e:
            print(f"Error listing catalogs: {e}")
            return []
    
    def list_schemas(self, catalog_name: str) -> List[Dict[str, Any]]:
        """List all schemas in a catalog."""
        try:
            schemas = list(self.client.schemas.list(catalog_name=catalog_name))
            return [{
                'name': schema.name,
                'catalog_name': schema.catalog_name,
                'comment': schema.comment or '',
                'owner': schema.owner or '',
            } for schema in schemas]
        except Exception as e:
            print(f"Error listing schemas in {catalog_name}: {e}")
            return []
    
    def list_tables(self, catalog_name: str, schema_name: str) -> List[Dict[str, Any]]:
        """List all tables in a schema."""
        try:
            tables = list(self.client.tables.list(
                catalog_name=catalog_name,
                schema_name=schema_name
            ))
            return [{
                'name': table.name,
                'catalog_name': table.catalog_name,
                'schema_name': table.schema_name,
                'table_type': table.table_type.value if table.table_type else 'TABLE',
                'comment': table.comment or '',
                'owner': table.owner or '',
                'fqn': f"{table.catalog_name}.{table.schema_name}.{table.name}"
            } for table in tables]
        except Exception as e:
            print(f"Error listing tables in {catalog_name}.{schema_name}: {e}")
            return []
    
    def get_table_columns(self, catalog_name: str, schema_name: str, table_name: str) -> List[Dict[str, Any]]:
        """Get column details for a table."""
        try:
            table = self.client.tables.get(
                full_name=f"{catalog_name}.{schema_name}.{table_name}"
            )
            if table.columns:
                return [{
                    'name': col.name,
                    'type_text': col.type_text,
                    'type_name': col.type_name.value if col.type_name else col.type_text,
                    'comment': col.comment or '',
                    'nullable': col.nullable if col.nullable is not None else True,
                    'position': col.position,
                } for col in table.columns]
            return []
        except Exception as e:
            print(f"Error getting columns for {catalog_name}.{schema_name}.{table_name}: {e}")
            return []
    
    def get_table_hierarchy(self) -> Dict[str, Any]:
        """
        Get full catalog > schema > tables hierarchy.
        Returns nested dict for tree view rendering.
        """
        hierarchy = {}
        
        catalogs = self.list_catalogs()
        for catalog in catalogs:
            cat_name = catalog['name']
            hierarchy[cat_name] = {
                'meta': catalog,
                'schemas': {}
            }
            
            schemas = self.list_schemas(cat_name)
            for schema in schemas:
                schema_name = schema['name']
                hierarchy[cat_name]['schemas'][schema_name] = {
                    'meta': schema,
                    'tables': {}
                }
                
                tables = self.list_tables(cat_name, schema_name)
                for table in tables:
                    table_name = table['name']
                    hierarchy[cat_name]['schemas'][schema_name]['tables'][table_name] = table
        
        return hierarchy
