"""
Table metadata enrichment module.
Adapts etl/02_enrich_table_metadata.py to work with direct table FQNs.
"""
from databricks import sql
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from typing import List, Dict, Any
import os
import pandas as pd
import threading


class EnrichmentRunner:
    """Runs metadata enrichment on selected tables."""
    
    def __init__(self):
        self.config = Config()
        self.client = WorkspaceClient(config=self.config)
        
        # Get SQL warehouse ID from environment or config
        self.warehouse_id = os.getenv('DATABRICKS_WAREHOUSE_ID', 'a4ed2ccbda385db9')
        
        # Job tracking
        self.jobs = {}
        self.job_counter = 0
    
    def run_enrichment(self, table_fqns: List[str]) -> str:
        """
        Start enrichment job for selected tables.
        Returns job_id for status tracking.
        """
        self.job_counter += 1
        job_id = f"enrich-{self.job_counter}"
        
        self.jobs[job_id] = {
            'status': 'running',
            'progress': 0,
            'total': len(table_fqns),
            'results': {},
            'error': None
        }
        
        # Run enrichment in background thread
        thread = threading.Thread(
            target=self._run_enrichment_task,
            args=(job_id, table_fqns)
        )
        thread.daemon = True
        thread.start()
        
        return job_id
    
    def _run_enrichment_task(self, job_id: str, table_fqns: List[str]):
        """Background task for enrichment."""
        try:
            conn = sql.connect(
                server_hostname=self.config.host,
                http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
                credentials_provider=lambda: self.config.authenticate,
            )
            
            for i, fqn in enumerate(table_fqns):
                try:
                    parts = fqn.split('.')
                    if len(parts) != 3:
                        continue
                    
                    catalog, schema, table = parts
                    
                    # Get table metadata
                    cursor = conn.cursor()
                    cursor.execute(f"DESCRIBE `{catalog}`.`{schema}`.`{table}`")
                    columns = cursor.fetchall()
                    cursor.close()
                    
                    # Sample first column value
                    if columns:
                        first_col = columns[0][0]
                        cursor = conn.cursor()
                        cursor.execute(f"SELECT `{first_col}` FROM `{catalog}`.`{schema}`.`{table}` LIMIT 5")
                        samples = [row[0] for row in cursor.fetchall()]
                        cursor.close()
                    else:
                        samples = []
                    
                    self.jobs[job_id]['results'][fqn] = {
                        'fqn': fqn,
                        'column_count': len(columns),
                        'columns': [{'name': col[0], 'type': col[1], 'comment': col[2] or ''} for col in columns[:10]],
                        'sample_values': samples,
                        'enriched': True
                    }
                    
                except Exception as e:
                    self.jobs[job_id]['results'][fqn] = {
                        'fqn': fqn,
                        'error': str(e),
                        'enriched': False
                    }
                
                self.jobs[job_id]['progress'] = i + 1
            
            conn.close()
            self.jobs[job_id]['status'] = 'completed'
            
        except Exception as e:
            self.jobs[job_id]['status'] = 'failed'
            self.jobs[job_id]['error'] = str(e)
    
    def get_status(self, job_id: str) -> Dict[str, Any]:
        """Get enrichment job status."""
        return self.jobs.get(job_id, {'status': 'not_found'})
    
    def get_results(self) -> List[Dict[str, Any]]:
        """Get all enrichment results."""
        results = []
        for job_id, job in self.jobs.items():
            if job['status'] == 'completed':
                results.extend(job['results'].values())
        return results
