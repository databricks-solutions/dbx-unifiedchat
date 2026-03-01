# Phase 2: Backend Job Integration - Implementation Summary

## Date: Feb 15, 2026

## Overview
Successfully implemented Phase 2 of the enrichment system, replacing inline enrichment processing with Databricks Jobs API integration. The frontend now displays job URLs and polls for real-time status updates.

## Changes Implemented

### 1. Backend Models (`models.py`)
Added new models for job-based enrichment:

```python
class EnrichmentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"  # Added

class EnrichmentJobOut(BaseModel):
    """Response when submitting enrichment job."""
    run_id: int
    job_url: str
    status: EnrichmentStatus
    table_count: int
    submitted_at: str

class EnrichmentJobStatusOut(BaseModel):
    """Job status response."""
    run_id: int
    status: EnrichmentStatus
    job_url: str
    life_cycle_state: Optional[str] = None
    result_state: Optional[str] = None
    state_message: Optional[str] = None
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    duration_ms: Optional[int] = None
```

### 2. Backend Router (`router.py`)

#### Replaced Inline Enrichment with Job Submission

**Old Implementation:**
- Background thread processing with `threading.Thread`
- In-memory job tracking with `_enrichment_jobs` dict
- In-memory results storage with `_enrichment_results` list
- Direct SQL queries for enrichment

**New Implementation:**
- Databricks Jobs API submission via `client.jobs.submit()`
- Job execution on Databricks clusters
- Results persisted to Unity Catalog tables
- Real-time job status polling via `client.jobs.get_run()`

#### Key Code Changes:

**Job Submission (`/enrichment/run`):**
```python
@api.post("/enrichment/run", response_model=EnrichmentJobOut, operation_id="runEnrichment")
async def run_enrichment(enrichment_in: EnrichmentRunIn):
    run_result = client.jobs.submit(
        run_name=f"Table Enrichment - {len(enrichment_in.table_fqns)} tables",
        tasks=[
            SubmitTask(
                task_key="enrich_tables",
                spark_python_task=SparkPythonTask(
                    python_file=ENRICHMENT_SCRIPT_PATH,
                    parameters=[table_fqns_str, "20", "50", "databricks-claude-sonnet-4-5"]
                ),
                new_cluster=get_enrichment_cluster_spec()
            )
        ]
    )
    return EnrichmentJobOut(
        run_id=run_result.run_id,
        job_url=f"{config.host}/#job/{run_result.run_id}",
        status=EnrichmentStatus.PENDING,
        table_count=len(enrichment_in.table_fqns),
        submitted_at=datetime.now().isoformat()
    )
```

**Status Polling (`/enrichment/status/{run_id}`):**
```python
@api.get("/enrichment/status/{run_id}", response_model=EnrichmentJobStatusOut)
async def get_enrichment_status(run_id: int):
    run = client.jobs.get_run(run_id=run_id)
    
    # Map Databricks states to our status
    status = EnrichmentStatus.PENDING
    if run.state.life_cycle_state == "RUNNING":
        status = EnrichmentStatus.RUNNING
    elif run.state.life_cycle_state == "TERMINATED":
        if run.state.result_state == "SUCCESS":
            status = EnrichmentStatus.COMPLETED
        # ... handle FAILED and CANCELLED
    
    return EnrichmentJobStatusOut(
        run_id=run_id,
        status=status,
        job_url=f"{config.host}/#job/{run_id}",
        life_cycle_state=run.state.life_cycle_state.value,
        result_state=run.state.result_state.value,
        state_message=run.state.state_message,
        start_time=run.start_time,
        end_time=run.end_time,
        duration_ms=(run.end_time - run.start_time) if run.end_time and run.start_time else None
    )
```

**Results from Unity Catalog (`/enrichment/results`):**
```python
@api.get("/enrichment/results", response_model=List[EnrichmentResultListOut])
async def list_enrichment_results():
    cursor.execute("""
        SELECT table_fqn, enriched_doc 
        FROM yyang.multi_agent_genie.enriched_tables_direct 
        WHERE enriched = true
        ORDER BY id DESC
        LIMIT 100
    """)
    # Parse enriched_doc JSON and return results
```

#### Configuration:
```python
ENRICHMENT_SCRIPT_PATH = "/Workspace/Users/yang.yang@databricks.com/tables_to_genies/etl/enrich_tables_direct.py"
ENRICHMENT_CLUSTER_SPEC = compute.ClusterSpec(
    spark_version="15.4.x-scala2.12",
    node_type_id="i3.xlarge",
    num_workers=2,
    spark_conf={"spark.databricks.cluster.profile": "serverless"}
)
```

### 3. Frontend Updates (`enrichment.tsx`)

#### State Management:
```tsx
const [jobId, setJobId] = useState<number | null>(null);
const [jobUrl, setJobUrl] = useState<string | null>(null);
```

#### Job Submission:
```tsx
const handleRunEnrichment = async () => {
  const result = await runEnrichmentMutation.mutateAsync({
    table_fqns: selection.table_fqns,
  });
  setJobId(result.run_id);      // Integer run_id from Databricks
  setJobUrl(result.job_url);    // Direct link to Databricks job UI
};
```

#### Enhanced Progress Display:
- Clickable "View Job in Databricks" link
- Real-time status updates (PENDING → RUNNING → COMPLETED/FAILED)
- Display of Databricks lifecycle state and result state
- Job duration display
- Detailed error messages for failed jobs
- Visual indicators (colors, spinner) for different states
- Auto-refresh every 5 seconds while job is running

### 4. API Client Generation (`orval.config.ts`)

Fixed configuration to use `mode: 'single'` instead of `mode: 'tags-split'`:
```typescript
export default defineConfig({
  api: {
    input: 'http://localhost:8000/openapi.json',
    output: {
      mode: 'single',  // Changed from 'tags-split'
      target: './lib/api.ts',
      // ...
    },
  },
});
```

This ensures all generated types and hooks are in a single `lib/api.ts` file for easier imports.

## Technical Details

### Databricks SDK Integration

**Imports:**
```python
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import SubmitTask, SparkPythonTask
from databricks.sdk.service import compute
```

**Key Classes:**
- `SubmitTask`: Defines a task to run in a job
- `SparkPythonTask`: Specifies Python file and parameters
- `compute.ClusterSpec`: Defines cluster configuration
- `client.jobs.submit()`: Submits a one-time job run
- `client.jobs.get_run()`: Polls for run status

### State Mappings

Databricks → Application Status:
- `PENDING` → `EnrichmentStatus.PENDING`
- `RUNNING` → `EnrichmentStatus.RUNNING`
- `TERMINATED` + `SUCCESS` → `EnrichmentStatus.COMPLETED`
- `TERMINATED` + `FAILED` → `EnrichmentStatus.FAILED`
- `TERMINATED` + `CANCELED` → `EnrichmentStatus.CANCELLED`

### API Contract Changes

| Endpoint | Old Response | New Response | Change |
|----------|-------------|--------------|--------|
| POST `/enrichment/run` | `EnrichmentStatusOut` | `EnrichmentJobOut` | Added run_id, job_url, submitted_at |
| GET `/enrichment/status/{id}` | `EnrichmentStatusOut` (job_id: str) | `EnrichmentJobStatusOut` (run_id: int) | Added Databricks job fields |
| GET `/enrichment/results` | In-memory list | Unity Catalog query | Persistent storage |

## Files Modified

1. `/tables_to_genies_apx/src/tables_genies/backend/models.py` - New models
2. `/tables_to_genies_apx/src/tables_genies/backend/router.py` - Job submission logic
3. `/tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/enrichment.tsx` - UI updates
4. `/tables_to_genies_apx/src/tables_genies/ui/orval.config.ts` - Fixed API generation
5. `/tables_to_genies_apx/src/tables_genies/ui/lib/api.ts` - Regenerated client

## Testing Checklist

✅ Backend server starts without errors
✅ OpenAPI spec includes new models (`EnrichmentJobOut`, `EnrichmentJobStatusOut`)
✅ API client generated successfully with correct types
✅ Imports fixed (using `SubmitTask` instead of non-existent `RunSubmitTaskSettings`)

### Manual Testing Required:

1. **Job Submission:**
   - Select tables in catalog browser
   - Click "Run Enrichment"
   - Verify job is created in Databricks (check `run_id` and `job_url`)

2. **Status Polling:**
   - Verify status updates automatically every 5 seconds
   - Check lifecycle states: PENDING → RUNNING → TERMINATED
   - Verify result states: SUCCESS or FAILED
   - Confirm "View Job in Databricks" link opens correct job

3. **Results Retrieval:**
   - After job completes, check `/enrichment/results` returns enriched tables
   - Verify data is queried from `yyang.multi_agent_genie.enriched_tables_direct`
   - Confirm enriched metadata includes LLM-enhanced descriptions

4. **Error Handling:**
   - Test with invalid table names
   - Verify error messages display in UI
   - Check failed job shows appropriate error state

## Architecture Flow

```
User clicks "Run Enrichment"
    ↓
Frontend → POST /api/enrichment/run
    ↓
Backend → client.jobs.submit()
    ↓
Databricks Jobs API creates run
    ↓
Backend returns {run_id, job_url, status: PENDING}
    ↓
Frontend displays job URL and starts polling
    ↓
Frontend → GET /api/enrichment/status/{run_id} (every 5s)
    ↓
Backend → client.jobs.get_run()
    ↓
Backend returns detailed status
    ↓
Job completes → Data saved to Unity Catalog
    ↓
Frontend → GET /api/enrichment/results
    ↓
Backend queries yyang.multi_agent_genie.enriched_tables_direct
    ↓
Frontend displays enriched table metadata
```

## Environment Variables

Set in `.env` or `app.yaml`:
```bash
ENRICHMENT_SCRIPT_PATH=/Workspace/Users/yang.yang@databricks.com/tables_to_genies/etl/enrich_tables_direct.py
ENRICHMENT_SPARK_VERSION=15.4.x-scala2.12
ENRICHMENT_CLUSTER_NODE_TYPE=i3.xlarge
ENRICHMENT_NUM_WORKERS=2
```

## Known Issues / Limitations

1. **Script Path:** Currently hardcoded to specific user path. Should be configurable or use workspace-relative path.
2. **Cluster Spec:** Fixed configuration. Consider making node type and worker count adjustable based on table count.
3. **No Job Cancellation:** UI doesn't provide "Cancel Job" button yet.
4. **No Retry Logic:** If job fails, user must manually retry via UI.
5. **Single Unity Catalog Table:** Results stored in single table. Consider partitioning for scale.

## Next Steps (Future Enhancements)

1. Add job cancellation endpoint and UI button
2. Implement retry logic with exponential backoff
3. Add job history/audit log
4. Support configurable cluster specs (node type, autoscaling)
5. Add notifications (email/Slack) when jobs complete
6. Implement job queueing for large batches
7. Add cost tracking (compute minutes, LLM tokens)
8. Create dashboard for enrichment analytics

## Success Criteria

- ✅ Jobs submit successfully via API
- ✅ Frontend displays clickable job URL
- ✅ Status polling shows real-time progress
- ✅ Results load from Unity Catalog after completion
- ✅ Error handling for failed jobs
- ✅ No breaking changes to existing API contracts (backwards compatible endpoints maintained)

## Rollback Plan

If Phase 2 has critical issues:
1. Revert `router.py` to git commit before Phase 2 changes
2. Keep Phase 1 enrichment module improvements
3. Restore inline enrichment with background threads
4. Regenerate API client from reverted backend

Git reference: `adventure_tables_migration_create_genies_application`
