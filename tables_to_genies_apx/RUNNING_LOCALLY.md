# 🚀 APX App is Running Locally!

## Access Points

| Service | URL | Purpose |
|---------|-----|---------|
| **Frontend** | http://localhost:3000 | React UI - Browse Catalogs, Enrich, Graph, Genie Rooms |
| **Backend API** | http://localhost:8000 | FastAPI server with all endpoints |
| **API Documentation** | http://localhost:8000/docs | Interactive Swagger UI for testing endpoints |
| **Health Check** | http://localhost:8000/health | Backend health status |

## What's Running

```
Both Servers Started Successfully ✨
├── FastAPI Backend (PID: 21220)
│   ├── Location: src/tables_genies/backend/main.py
│   ├── Port: 8000
│   ├── Auto-reload: ON
│   └── Features: UC Catalog, Enrichment, Graph, Genie Room APIs
│
└── Vite Dev Server (Frontend)
    ├── Location: src/tables_genies/ui/
    ├── Port: 3000 (Vite allocated this)
    ├── HMR: ON (Hot Module Replacement)
    └── Features: 5-page wizard with Tailwind CSS + Cytoscape.js
```

## Testing the App

### 1. **Open Frontend**
```bash
open http://localhost:3000
```

### 2. **Test Backend API**
```bash
# Health check
curl http://localhost:8000/health | jq .

# List catalogs
curl http://localhost:8000/api/uc/catalogs | jq .

# Interactive API docs
open http://localhost:8000/docs
```

### 3. **Frontend Workflow**
1. Navigate to **Browse Catalogs** - Select tables from Unity Catalog
2. Go to **Enrich Tables** - Run metadata enrichment
3. Visit **Explore Graph** - View table relationships
4. Build **Genie Rooms** - Group tables for Genie spaces
5. Create **Rooms** - Batch create Genie spaces

## Development

### Frontend Changes
- Edit files in `src/tables_genies/ui/routes/` or `src/tables_genies/ui/lib/`
- Changes appear instantly in browser (HMR)
- Check browser console (F12) for errors

### Backend Changes
- Edit files in `src/tables_genies/backend/`
- Server auto-restarts on file changes
- Check terminal output for logs and errors

### API Endpoints Reference

**UC Catalog:**
- GET `/api/uc/catalogs` - List catalogs
- GET `/api/uc/catalogs/{catalog}/schemas` - List schemas
- GET `/api/uc/catalogs/{catalog}/schemas/{schema}/tables` - List tables
- POST `/api/uc/selection` - Save selection
- GET `/api/uc/selection` - Get selection

**Enrichment:**
- POST `/api/enrichment/run` - Start enrichment
- GET `/api/enrichment/status/{job_id}` - Check status
- GET `/api/enrichment/results` - Get results

**Graph:**
- POST `/api/graph/build` - Build graph
- GET `/api/graph/data` - Get graph data

**Genie:**
- POST `/api/genie/rooms` - Create room
- GET `/api/genie/rooms` - List rooms
- DELETE `/api/genie/rooms/{id}` - Delete room
- POST `/api/genie/create-all` - Create all
- GET `/api/genie/create-status` - Check status

## Stopping the Servers

```bash
# In the terminal running run-dev.sh:
Ctrl + C

# This will gracefully shut down both backend and frontend
```

## Troubleshooting

### Backend won't connect to Databricks
```bash
# Check your Databricks config
echo $DATABRICKS_HOST
echo $DATABRICKS_TOKEN

# Or use a profile
export DATABRICKS_CONFIG_PROFILE=PROD
```

### Frontend shows blank page
1. Open browser DevTools (F12)
2. Check Console tab for errors
3. Check Network tab - look for failed API calls
4. Verify backend is running: `curl http://localhost:8000/health`

### Port already in use
```bash
# Kill existing process
lsof -i :3000  # or :8000
kill -9 <PID>
```

### Need to rebuild frontend after npm changes
```bash
cd src/tables_genies/ui
bun run build
```

## Next Steps

### Deploy to Production
```bash
# From project root
databricks bundle validate
databricks bundle deploy -t prod
```

### Add More Features
- [ ] Add more shadcn/ui components (Table, Badge, Progress)
- [ ] Improve error handling (Toast notifications)
- [ ] Add form validation
- [ ] Persist state to Delta tables
- [ ] Add user authentication
- [ ] Dark mode toggle

---

**Happy Development! 🎉**

The app is fully functional locally with live reload for both backend and frontend.
