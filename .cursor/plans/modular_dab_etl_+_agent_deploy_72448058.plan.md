---
name: Modular DAB ETL + Agent Deploy
overview: Refactor the existing `databricks.yml` into a modular Databricks Asset Bundle structure, splitting the ETL pipeline and agent deployment into separate resource files under `resources/` so each can be tracked, reasoned about, and triggered independently.
todos:
  - id: update-databricks-yml
    content: "Update databricks.yml: add include directive, add config_file variable per target, remove inline resources block"
    status: completed
  - id: create-etl-resource
    content: Create resources/etl_pipeline.yml with corrected ../ notebook paths
    status: completed
  - id: create-agent-resource
    content: Create resources/agent_deploy.yml with ../ paths and config_file base_parameter wired to ${var.config_file}
    status: completed
  - id: fix-deploy-agent
    content: Update Notebooks/deploy_agent.py and notebook_utils.py to accept config_file as a notebook widget parameter instead of hardcoding prod_config.yaml
    status: completed
  - id: validate-bundle
    content: Run databricks bundle validate to confirm structure is valid
    status: completed
isProject: false
---

# Modular DAB ETL + Agent Deploy

## Current State

All three jobs are defined inline in `[databricks.yml](databricks.yml)`:

- `etl_pipeline` — 3-task chain: `export_genie_spaces` → `enrich_table_metadata` → `build_vector_search_index`
- `agent_integration_test` — runs `Notebooks/test_agent_databricks.py`
- `agent_deploy` — validates then runs `Notebooks/deploy_agent.py`

There is no `resources/` directory yet. This violates the DABs best practice of separating resource definitions from the root config.

## Target Structure

```
databricks.yml                     ← main config: variables + targets + include
resources/
  etl_pipeline.yml                 ← ETL job (3 tasks)
  agent_deploy.yml                 ← integration test + deploy jobs
etl/
  01_export_genie_spaces.py
  02_enrich_table_metadata.py
  03_build_vector_search_index.py
Notebooks/
  test_agent_databricks.py
  deploy_agent.py
prod_config.yaml
```

## Changes

### 1. Update `databricks.yml`

- Add `include: - resources/*.yml` directive after `bundle:`
- Remove the entire inline `resources: jobs:` block (lines 77–168)
- Keep all `variables:`, `targets:`, and `sync:` sections unchanged

### 2. Create `resources/etl_pipeline.yml`

Move the `etl_pipeline` job here. Critical path change — resource files sit one level deep, so all `./etl/` paths become `../etl/`:

```yaml
resources:
  jobs:
    etl_pipeline:
      name: multi_agent_genie_etl_pipeline
      description: >-
        End-to-end ETL pipeline: export Genie spaces, enrich table metadata
        with LLM, and build the vector search index.
      max_concurrent_runs: 1
      tags:
        project: multi_agent_genie
        pipeline: etl
      tasks:
        - task_key: export_genie_spaces
          notebook_task:
            notebook_path: ../etl/01_export_genie_spaces.py
            source: WORKSPACE
            base_parameters:
              catalog_name: ${var.catalog_name}
              schema_name: ${var.schema_name}
              volume_name: ${var.volume_name}
              genie_space_ids: ${var.genie_space_ids}
              databricks_host: ""
              databricks_token: ""
          timeout_seconds: 3600
        - task_key: enrich_table_metadata
          depends_on:
            - task_key: export_genie_spaces
          notebook_task:
            notebook_path: ../etl/02_enrich_table_metadata.py
            source: WORKSPACE
            base_parameters:
              catalog_name: ${var.catalog_name}
              schema_name: ${var.schema_name}
              genie_exports_volume: ${var.genie_exports_volume}
              enriched_docs_table: ${var.enriched_docs_table}
              llm_endpoint: ${var.llm_endpoint}
              sample_size: ${var.sample_size}
              max_unique_values: ${var.max_unique_values}
          timeout_seconds: 7200
        - task_key: build_vector_search_index
          depends_on:
            - task_key: enrich_table_metadata
          notebook_task:
            notebook_path: ../etl/03_build_vector_search_index.py
            source: WORKSPACE
            base_parameters:
              catalog_name: ${var.catalog_name}
              schema_name: ${var.schema_name}
              source_table: ${var.source_table}
              vs_endpoint_name: ${var.vs_endpoint_name}
              embedding_model: ${var.embedding_model}
              pipeline_type: ${var.pipeline_type}
          timeout_seconds: 3600
```

### 3. Create `resources/agent_deploy.yml`

Move `agent_integration_test` and `agent_deploy` jobs here. Notebook paths change from `./Notebooks/` to `../Notebooks/`. The `deploy_agent` task now passes `config_file` so the notebook knows which YAML to load:

```yaml
resources:
  jobs:
    agent_integration_test:
      name: multi_agent_integration_test
      description: Test agent code before deployment
      tasks:
        - task_key: test_agent
          notebook_task:
            notebook_path: ../Notebooks/test_agent_databricks.py
            source: WORKSPACE
            base_parameters:
              config_file: ${var.config_file}
          timeout_seconds: 1200

    agent_deploy:
      name: multi_agent_deploy
      description: Deploy agent to Model Serving
      tasks:
        - task_key: validate_agent
          notebook_task:
            notebook_path: ../Notebooks/test_agent_databricks.py
            source: WORKSPACE
            base_parameters:
              config_file: ${var.config_file}
        - task_key: deploy_agent
          depends_on:
            - task_key: validate_agent
          notebook_task:
            notebook_path: ../Notebooks/deploy_agent.py
            source: WORKSPACE
            base_parameters:
              config_file: ${var.config_file}
          timeout_seconds: 1800
```

### 4. Add `config_file` variable to `databricks.yml`

```yaml
variables:
  config_file:
    description: Path to agent config YAML (relative to notebook workspace location)
    default: "../dev_config.yaml"

targets:
  dev:
    ...
    variables:
      config_file: "../dev_config.yaml"

  prod:
    ...
    variables:
      config_file: "../prod_config.yaml"
```

### 5. Update `Notebooks/deploy_agent.py` and `notebook_utils.py`

`**deploy_agent.py**` — replace the hardcoded config path with a `dbutils.widgets` read:

```python
# Before
config_dict = load_deployment_config("../prod_config.yaml")

# After
dbutils.widgets.text("config_file", "../prod_config.yaml")
config_file = dbutils.widgets.get("config_file")
config_dict = load_deployment_config(config_file)
```

Same pattern for `Notebooks/test_agent_databricks.py` if it also calls `load_deployment_config`.

## Deployment Workflow

```
# Step 1 — validate the whole bundle
databricks bundle validate -t prod

# Step 2 — deploy all resources (uploads files + registers jobs in both modules)
databricks bundle deploy -t prod

# Step 3 — run ETL module first (prerequisite for agent)
databricks bundle run etl_pipeline -t prod
# Runtime: ~30–60 min; builds enriched_genie_docs_chunks + VS index

# Step 4 — once ETL completes, deploy the agent
databricks bundle run agent_deploy -t prod
# Validates agent code then calls deploy_agent.py → MLflow log → UC register → agents.deploy()
```

## Config File Resolution: How the Two Systems Interact

```
databricks bundle run etl_pipeline -t prod
  └─ ${var.catalog_name} → "serverless_dbx_unifiedchat_catalog"   (from targets.prod.variables)
  └─ ${var.llm_endpoint} → "databricks-claude-sonnet-4-5"
  └─ notebook receives these as dbutils.widgets (base_parameters) ✓

databricks bundle run agent_deploy -t prod
  └─ ${var.config_file} → "../prod_config.yaml"                   (from targets.prod.variables)
  └─ deploy_agent.py reads widget → loads prod_config.yaml
  └─ catalog, LLM endpoints, lakebase, genie IDs all from YAML ✓

databricks bundle run agent_deploy -t dev
  └─ ${var.config_file} → "../dev_config.yaml"                    (from targets.dev.variables)
  └─ deploy_agent.py reads widget → loads dev_config.yaml ✓
```

Note: `dev_config.yaml` and `prod_config.yaml` currently have **identical values**. Once the plan is executed, you should update `dev_config.yaml` to point to the dev catalog/schema (e.g., `catalog_name: yyang`) to make the separation meaningful.

## Key Notes

- **Path rule**: `../` prefix required in `resources/*.yml` because the file is one directory deep from the bundle root
- **ETL config source**: DABs variables (`${var.*}`) resolved per target — already correct
- **Agent config source**: `config_file` DABs variable → notebook widget → `load_deployment_config()` — fixed by this plan
- **Independent triggering**: After `bundle deploy`, each module runs independently via `bundle run etl_pipeline` or `bundle run agent_deploy`

