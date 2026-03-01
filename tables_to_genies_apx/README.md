# Tables-to-Genies APX App

FastAPI backend following APX 3-model pattern with React frontend (TBD).

## Backend Features

- **3-model pattern**: EntityIn, EntityOut, EntityListOut for all resources
- **operation_id**: Every route has operation_id for OpenAPI client generation
- **Type safety**: Full Pydantic models with type hints
- **Databricks SDK**: UC catalog browsing, Genie space creation
- **SQL Warehouse**: Table metadata enrichment

## API Routes

### UC Browser (`/api/uc/`)
- `GET /api/uc/catalogs` - listCatalogs
- `GET /api/uc/catalogs/{catalog}/schemas` - listSchemas
- `GET /api/uc/catalogs/{catalog}/schemas/{schema}/tables` - listTables
- `GET /api/uc/tables/{fqn}/columns` - getTableColumns
- `POST /api/uc/selection` - saveSelection
- `GET /api/uc/selection` - getSelection

### Enrichment (`/api/enrichment/`)
- `POST /api/enrichment/run` - runEnrichment
- `GET /api/enrichment/status/{job_id}` - getEnrichmentStatus
- `GET /api/enrichment/results` - listEnrichmentResults
- `GET /api/enrichment/results/{fqn}` - getEnrichmentResult

### Graph (`/api/graph/`)
- `POST /api/graph/build` - buildGraph
- `GET /api/graph/data` - getGraphData

### Genie Rooms (`/api/genie/`)
- `POST /api/genie/rooms` - createGenieRoom
- `GET /api/genie/rooms` - listGenieRooms
- `DELETE /api/genie/rooms/{id}` - deleteGenieRoom
- `POST /api/genie/create-all` - createAllGenieRooms
- `GET /api/genie/create-status` - getGenieCreationStatus
- `GET /api/genie/created` - listCreatedGenieRooms

## Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Run backend
uvicorn src.tables_genies.backend.main:app --reload --port 8000

# Test API
curl http://localhost:8000/api/uc/catalogs | jq .
curl http://localhost:8000/docs  # Interactive API docs
```

## Deployment

```bash
# Upload to workspace
databricks workspace import-dir . /Workspace/Users/yang.yang@databricks.com/tables_to_genies_apx --profile PROD

# Deploy app
databricks apps deploy tables-to-genies-apx --source-code-path /Workspace/Users/yang.yang@databricks.com/tables_to_genies_apx --profile PROD
```

## Data

Works with 88 synthetic tables across 18 domains in `serverless_dbx_unifiedchat_catalog`:
- Sports: world_cup_2026, nfl, nba
- Science: nasa, drug_discovery, semiconductors
- AI: genai
- Health: nutrition, pharmaceuticals
- Entertainment: iron_chef, japanese_anime, rock_bands
- Insurance: claims, providers
- History: world_war_2, roman_history
- Policy: international_policy
- Mixed: demo_mixed
