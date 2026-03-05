"""
Unity Catalog browser module.
Provides methods to list catalogs, schemas, tables, and columns.
"""
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config, DatabricksError
from typing import List, Dict, Any
import os
import sys


class UCBrowser:
    """Unity Catalog browser using Databricks SDK."""
    
    def __init__(self):
        try:
            # Use PROD profile explicitly
            print(f"[INFO] Initializing Databricks client with PROD profile", file=sys.stderr, flush=True)
            self.config = Config(profile="PROD")
            self.client = WorkspaceClient(config=self.config)
            # Test connection
            self.client.workspace.get_status("/")
            print(f"[INFO] Connected to Databricks PROD successfully", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[ERROR] Failed to initialize Databricks client: {e}", file=sys.stderr, flush=True)
            self.client = None
            self.config = None
    
    def list_catalogs(self) -> List[Dict[str, Any]]:
        """List all catalogs in the workspace."""
        if not self.client:
            print(f"[ERROR] Databricks client not initialized", file=sys.stderr, flush=True)
            raise Exception("Databricks client not initialized. Check your credentials and configuration.")
        try:
            # Limit to first 100 catalogs to improve performance
            # In production, may want to add filtering or pagination UI
            catalogs = []
            for i, cat in enumerate(self.client.catalogs.list()):
                if i >= 100:  # Limit to first 100 catalogs
                    print(f"[INFO] Limiting to 100 catalogs (found more)", file=sys.stderr, flush=True)
                    break
                catalogs.append({
                    'name': cat.name,
                    'comment': cat.comment or '',
                    'owner': cat.owner or '',
                })
            print(f"[INFO] Listed {len(catalogs)} catalogs", file=sys.stderr, flush=True)
            return catalogs
        except Exception as e:
            print(f"Error listing catalogs: {e}", file=sys.stderr, flush=True)
            raise
    
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
        Includes timeout and error handling to prevent hanging.
        """
        import signal
        
        def timeout_handler(signum, frame):
            raise TimeoutError("Catalog hierarchy retrieval timed out after 30 seconds")
        
        # Set timeout for the entire operation
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(30)
        
        try:
            hierarchy = {}
            
            print(f"[DEBUG] Listing catalogs...", file=sys.stderr, flush=True)
            catalogs = self.list_catalogs()
            print(f"[DEBUG] Found {len(catalogs)} catalogs", file=sys.stderr, flush=True)
            
            for catalog in catalogs:
                cat_name = catalog['name']
                print(f"[DEBUG] Processing catalog: {cat_name}", file=sys.stderr, flush=True)
                hierarchy[cat_name] = {
                    'meta': catalog,
                    'schemas': {}
                }
                
                try:
                    schemas = self.list_schemas(cat_name)
                    print(f"[DEBUG] Found {len(schemas)} schemas in {cat_name}", file=sys.stderr, flush=True)
                    
                    for schema in schemas:
                        schema_name = schema['name']
                        hierarchy[cat_name]['schemas'][schema_name] = {
                            'meta': schema,
                            'tables': {}
                        }
                        
                        try:
                            tables = self.list_tables(cat_name, schema_name)
                            print(f"[DEBUG] Found {len(tables)} tables in {cat_name}.{schema_name}", file=sys.stderr, flush=True)
                            
                            for table in tables:
                                table_name = table['name']
                                hierarchy[cat_name]['schemas'][schema_name]['tables'][table_name] = table
                        except Exception as e:
                            print(f"[ERROR] Failed to list tables in {cat_name}.{schema_name}: {e}", file=sys.stderr, flush=True)
                            continue
                            
                except Exception as e:
                    print(f"[ERROR] Failed to list schemas in {cat_name}: {e}", file=sys.stderr, flush=True)
                    continue
            
            print(f"[DEBUG] Hierarchy retrieved successfully", file=sys.stderr, flush=True)
            return hierarchy
            
        except TimeoutError as e:
            print(f"[ERROR] {e}", file=sys.stderr, flush=True)
            raise
        finally:
            signal.alarm(0)  # Cancel the alarm
