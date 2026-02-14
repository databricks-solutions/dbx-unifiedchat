# Tables-to-Genies APX Deployment

## ✅ Deployment Complete

The APX-style FastAPI backend is deployed and running on Databricks.

### App Details

- **Name**: `tables-to-genies-apx`
- **App ID**: `90fda25d-acb1-41c3-9947-a1b72fecf606`
- **Deployment ID**: `01f109e009da19d2bed09d49d784d8d7`
- **Status**: RUNNING ✅
- **Compute**: MEDIUM, ACTIVE ✅
- **URL**: https://tables-to-genies-apx-7474651667509820.aws.databricksapps.com

### API Endpoints

All endpoints follow APX patterns with `response_model` and `operation_id`:

**UC Browser** (`/api/uc/`):
- `GET /api/uc/catalogs` - listCatalogs
- `GET /api/uc/catalogs/{catalog}/schemas` - listSchemas
- `GET /api/uc/catalogs/{catalog}/schemas/{schema}/tables` - listTables
- `GET /api/uc/tables/{fqn}/columns` - getTableColumns
- `POST /api/uc/selection` - saveSelection
- `GET /api/uc/selection` - getSelection

**Enrichment** (`/api/enrichment/`):
- `POST /api/enrichment/run` - runEnrichment
- `GET /api/enrichment/status/{job_id}` - getEnrichmentStatus
- `GET /api/enrichment/results` - listEnrichmentResults
- `GET /api/enrichment/results/{fqn}` - getEnrichmentResult

**Graph** (`/api/graph/`):
- `POST /api/graph/build` - buildGraph
- `GET /api/graph/data` - getGraphData

**Genie Rooms** (`/api/genie/`):
- `POST /api/genie/rooms` - createGenieRoom
- `GET /api/genie/rooms` - listGenieRooms
- `DELETE /api/genie/rooms/{id}` - deleteGenieRoom
- `POST /api/genie/create-all` - createAllGenieRooms
- `GET /api/genie/create-status` - getGenieCreationStatus
- `GET /api/genie/created` - listCreatedGenieRooms

### Testing the API

```bash
# Interactive API docs (Swagger UI)
open https://tables-to-genies-apx-7474651667509820.aws.databricksapps.com/docs

# Test endpoints with curl
curl https://tables-to-genies-apx-7474651667509820.aws.databricksapps.com/api/uc/catalogs
curl https://tables-to-genies-apx-7474651667509820.aws.databricksapps.com/health
```

### Architecture

```
tables_to_genies_apx/
├── src/tables_genies/backend/
│   ├── __init__.py
│   ├── models.py          # Pydantic models (3-model pattern)
│   ├── router.py          # FastAPI routes (all with operation_id)
│   └── main.py            # FastAPI app
├── requirements.txt       # Python dependencies
├── app.yaml               # Databricks App config
└── README.md
```

### APX Pattern Compliance

✅ **3-Model Pattern**: EntityIn, EntityOut, EntityListOut for all resources  
✅ **response_model**: Every route has response_model for OpenAPI generation  
✅ **operation_id**: Every route has operation_id (becomes frontend hook name)  
✅ **Type hints**: Full type safety with Pydantic models  
✅ **Databricks SDK**: Uses Config() for authentication  
✅ **SQL Warehouse**: Configured via app.yaml resources  

### Next Steps

To add the React frontend:

1. Generate OpenAPI client from `/docs/openapi.json`
2. Create React app with TanStack Router
3. Use generated hooks: `useListCatalogs()`, `useRunEnrichment()`, etc.
4. Add Cytoscape.js for graph visualization
5. Deploy updated app

Or use the existing Dash app for immediate UI: https://tables-to-genies-7474651667509820.aws.databricksapps.com

## Troubleshooting

### APX Init Failed
The `apx init` command has a bug where it can't find the Bun binary even when installed via Homebrew. We worked around this by:
1. Installing protobuf: `brew install protobuf`
2. Installing bun: `brew install bun`
3. Manually creating the APX-style project structure
4. Following APX patterns from `~/.cursor/skills/databricks-app-apx/`

### Solution
Built FastAPI backend manually following all APX patterns. The backend is fully APX-compliant and ready for React frontend integration when needed.

---

**Both apps are live:**
- **Dash (Full UI)**: https://tables-to-genies-7474651667509820.aws.databricksapps.com
- **FastAPI (APX-style)**: https://tables-to-genies-apx-7474651667509820.aws.databricksapps.com
