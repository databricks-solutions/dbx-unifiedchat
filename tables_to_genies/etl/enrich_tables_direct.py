"""
Enrichment module adapted from etl/02_enrich_table_metadata.py.
Works with direct table FQNs instead of Genie space.json exports.
"""
from databricks import sql
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from typing import List, Dict, Any
import json


class TableEnricher:
    """Enriches tables with metadata, samples, and LLM-enhanced descriptions."""
    
    def __init__(self, warehouse_id: str = "a4ed2ccbda385db9"):
        self.config = Config()
        self.client = WorkspaceClient(config=self.config)
        self.warehouse_id = warehouse_id
        self.llm_endpoint = "databricks-claude-sonnet-4-5"
    
    def get_table_metadata(self, table_fqn: str) -> List[Dict[str, Any]]:
        """Get column metadata for a table using SQL warehouse."""
        try:
            conn = sql.connect(
                server_hostname=self.config.host,
                http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
                credentials_provider=lambda: self.config.authenticate,
            )
            
            cursor = conn.cursor()
            cursor.execute(f"DESCRIBE {table_fqn}")
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            
            columns = []
            for row in rows:
                if row[0] and row[1] and not row[0].startswith('#'):
                    columns.append({
                        'col_name': row[0],
                        'data_type': row[1],
                        'comment': row[2] if len(row) > 2 else ''
                    })
            
            return columns
            
        except Exception as e:
            print(f"Error getting metadata for {table_fqn}: {e}")
            return []
    
    def sample_column_values(self, table_fqn: str, column_name: str, sample_size: int = 20) -> List[Any]:
        """Sample distinct values from a column."""
        try:
            conn = sql.connect(
                server_hostname=self.config.host,
                http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
                credentials_provider=lambda: self.config.authenticate,
            )
            
            cursor = conn.cursor()
            query = f"""
            SELECT DISTINCT `{column_name}` 
            FROM {table_fqn} 
            WHERE `{column_name}` IS NOT NULL 
            LIMIT {sample_size}
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            
            return [str(row[0]) for row in rows]
            
        except Exception as e:
            print(f"Error sampling {column_name} from {table_fqn}: {e}")
            return []
    
    def enrich_table(self, table_fqn: str) -> Dict[str, Any]:
        """
        Enrich a single table with metadata and samples.
        
        Args:
            table_fqn: Fully qualified table name
        
        Returns:
            Enriched table metadata dict
        """
        print(f"Enriching {table_fqn}")
        
        parts = table_fqn.split('.')
        if len(parts) != 3:
            return {'table_fqn': table_fqn, 'error': 'Invalid FQN', 'enriched': False}
        
        catalog, schema, table = parts
        
        # Get column metadata
        columns = self.get_table_metadata(table_fqn)
        
        # Enrich columns with samples
        enriched_columns = []
        for col in columns[:10]:  # Limit to first 10 columns for speed
            enriched_col = col.copy()
            
            # Sample values
            samples = self.sample_column_values(table_fqn, col['col_name'], sample_size=5)
            enriched_col['sample_values'] = samples
            
            enriched_columns.append(enriched_col)
        
        return {
            'table_fqn': table_fqn,
            'catalog': catalog,
            'schema': schema,
            'table': table,
            'column_count': len(columns),
            'enriched_columns': enriched_columns,
            'enriched': True,
            'timestamp': str(sql.Timestamp.now()) if hasattr(sql, 'Timestamp') else ""
        }
    
    def enrich_tables(self, table_fqns: List[str]) -> List[Dict[str, Any]]:
        """
        Enrich multiple tables.
        
        Args:
            table_fqns: List of fully qualified table names
        
        Returns:
            List of enriched table metadata dicts
        """
        enriched_tables = []
        
        for fqn in table_fqns:
            try:
                enriched = self.enrich_table(fqn)
                enriched_tables.append(enriched)
            except Exception as e:
                print(f"Error enriching {fqn}: {e}")
                enriched_tables.append({
                    'table_fqn': fqn,
                    'error': str(e),
                    'enriched': False
                })
        
        return enriched_tables
    
    def save_enriched_tables(self, enriched_tables: List[Dict[str, Any]], target_table: str = "serverless_dbx_unifiedchat_catalog.multi_agent_genie.enriched_tables_direct"):
        """
        Save enriched tables to Unity Catalog table via SQL warehouse.
        
        Args:
            enriched_tables: List of enriched table dicts
            target_table: Target UC table for storage
        """
        try:
            conn = sql.connect(
                server_hostname=self.config.host,
                http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
                credentials_provider=lambda: self.config.authenticate,
            )
            
            cursor = conn.cursor()
            
            # Create table if not exists
            cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {target_table} (
                table_fqn STRING,
                enriched_doc STRING,
                enrichment_timestamp TIMESTAMP
            )
            """)
            
            # Insert enriched data
            for enriched in enriched_tables:
                enriched_json = json.dumps(enriched)
                cursor.execute(f"""
                INSERT INTO {target_table}
                VALUES ('{enriched['table_fqn']}', '{enriched_json}', current_timestamp())
                """)
            
            cursor.close()
            conn.close()
            
            print(f"✓ Saved {len(enriched_tables)} enriched tables to {target_table}")
            
        except Exception as e:
            print(f"Error saving enriched tables: {e}")
