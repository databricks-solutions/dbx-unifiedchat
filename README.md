![DBX-UnifiedChat Logo](docs/logos/dbx-unifiedchat-logo-pacman-eating-data.png)

# DBX-UnifiedChat - Databricks Unified Chat

> A multi-agent system for intelligent cross-domain data queries built with LangGraph, Databricks Genie, Lakebase, and Claude models/skills on Databricks Platform.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Databricks-blue.svg)](LICENSE.md)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.0.30-green.svg)](https://github.com/langchain-ai/langgraph)
[![LangChain](https://img.shields.io/badge/LangChain-≥0.1.0-green.svg)](https://github.com/langchain-ai/langchain)
[![MLflow](https://img.shields.io/badge/MLflow-≥3.6.0-orange.svg)](https://mlflow.org/)
[![Databricks SDK](https://img.shields.io/badge/Databricks%20SDK-≥0.20.0-red.svg)](https://github.com/databricks/databricks-sdk-py)
[![Pydantic](https://img.shields.io/badge/Pydantic-≥2.0.0-blue.svg)](https://github.com/pydantic/pydantic)
[![Claude Models](https://img.shields.io/badge/Claude-Sonnet%2FHaiku_4.5-purple.svg)](https://www.anthropic.com/claude)
[![Claude Skills](https://img.shields.io/badge/Claude-Skills-purple.svg)](https://www.anthropic.com/claude)

---

## Overview

Organizations struggle to query data across multiple domains and data sources, requiring deep SQL expertise and knowledge of complex data schemas. **Databricks Unified Chat** solves this by providing an intelligent multi-agent system that routes natural language queries to the appropriate data sources, synthesizes results, and delivers comprehensive answers.

Built on LangGraph, Databricks Genie and Lakebase, this solution enables business users to ask questions spanning multiple data domains without needing to understand the underlying data architecture or write complex SQL queries.

The supported application workflow now lives under `agent_app/`, which contains
the Databricks App bundle, backend agent runtime, UI, and deployment scripts.
Older root-level deployment and Model Serving paths are no longer part of the
active repository workflow.

> ### Why use DBX-UnifiedChat?
- **Accuracy of Answer** 
    - Validated with customers and partners, e.g., tumor outcome data analysis.
- **Explanation and Curation** 
    - Results are curated and explained by SQL answer returned and associated explanations.
- **Speed**
    - Optimized with parallel/cache/token reduction/architecture design
    - Achieves 1-2 seconds TTFT
    - For complex query across domains, we see it achieves 1/3 to 1/2 of the time of the No/Low-Code custom agent solution.



## Architecture

![Agent Architecture](docs/architecture/architecture_diagram_simple_v2.png)

The system uses a multi-agent architecture powered by LangGraph:

* **Supervisor Agent (multi-purpose)** - Frontend agent that orchestrates the workflow and coordinates handoffs to other agents
* **Thinking & Planning Agent** - Analyzes queries and creates execution plans based on the query intent and context
* **Genie Agents** - Query individual Genie spaces for domain-specific data
* **SQL Synthesis Agent (table route)** - Combines and synthesizes SQL across table data sources using UC Functions (instructed retrieval)
* **SQL Synthesis Agent (genie route)** - Combines and synthesizes SQL across genie space data sources using Genie agents as tools (parallel execution)
* **SQL Execution Agent** - Executes queries and extracts results
* **Summarize Agent** - Summarizes results and formats responses for the user

The system leverages:
* **LangGraph** for agent orchestration and workflow management
* **LangChain** for agent tools and integrations
* **Lakebase** for state management and long/short-term memory
* **Databricks Genie** as Agent/Tool for natural language to SQL conversion
* **UC Functions** as Tools for multi-step instructed retrieval
* **Databricks SDK** for Databricks platform integration
* **Databricks SQL Warehouse** for query execution
* **Model Serving** for model deployment and serving
* **MLflow** for Agent observability, evaluation and model tracking
* **Pydantic** for data validation and configuration
* **Pytest** for testing framework
* **PyYAML** for configuration management
* **Vector Search** for semantic metadata retrieval
* **Unity Catalog** for data governance and metadata management

### Key Technologies Applied:

* **Multi-turn Chatting** - Supports clarification, continue, refine, and new question flows for conversational interactions
* **Meta-question Fast Route** - Optimized path for handling meta-questions about the system itself
* **Multi-step Instructed Retrieval** - Advanced retrieval strategy in table route with step-by-step instructions
* **Parallel GenieAgent Tool Calls** - Concurrent execution of multiple Genie agents for improved performance in Genie route
* **Lakebase with Long/Short-term Memory** - Persistent memory management for maintaining context across conversations

See [Architecture Documentation](docs/ARCHITECTURE.md) for detailed design.



## Presentation

<a href="https://blitzbricksteryy-db.github.io/dbx-unifiedchat/docs/decks/slides_2slide.html" target="_blank">
  <img src="docs/logos/deck_logo.png" width="600px" alt="View Presentation Slides" />
  <br />
  <b>🚀 Click here to view the Interactive Presentation Slides</b>
</a>



## UI Illustration

![UI Tutorial Annotated](docs/UI/UI_tutorial_annotated.png)

---

## Quick Start

### Prerequisites

* Python 3.11 or higher
* Node.js 18 or higher
* `uv`, `npm`, `jq`, and Databricks CLI
* Databricks workspace with:
  * Genie spaces configured
  * SQL Warehouse configured
  * Permissions to deploy Databricks Asset Bundles, Databricks Apps, and Jobs

### Installation

```bash
git clone https://github.com/databricks-solutions/dbx-unifiedchat.git
cd dbx-unifiedchat
```

### Recommended Workflow

#### 1. Use the canonical app bundle in `agent_app`

The supported deployment surface is the Databricks App bundle under `agent_app/`.
It now owns:

* app deployment
* ETL preparation
* shared Lakebase / Unity Catalog bootstrap
* deployment validation

`agent_app/databricks.yml` is the committed, public-safe baseline consumed by
the local bootstrap and bundle deploy flow. Keep real workspace-specific values
in `agent_app/databricks.local.yml` (gitignored), then copy the values you need
back into `agent_app/databricks.yml` before local development or deployment.
Local development still uses `agent_app/.env` as a materialized runtime file
for machine-specific values, auth context, resolved database connection
details, and any local-only overrides.

From a local terminal or CI runner:

```bash
cd agent_app
./scripts/deploy.sh --target dev --run-job full --start-app
```

Useful variations:

* `./scripts/deploy.sh --target prod --run-job full --start-app`
* `./scripts/deploy.sh --target dev --run-job prep`
* `./scripts/deploy.sh --target dev --list-jobs`
* `./scripts/deploy.sh --target prod --sync-workspace --run-job full --ci --skip-bootstrap`

The deploy script validates the bundle, deploys the app resources, runs the prep or
full deployment job graph, and can optionally start the app.

#### 2. Workspace-native operator flow

If you prefer to operate entirely inside Databricks, open
`agent_app/scripts/deploy_notebook.py` and use it as a guided handoff to the
Databricks web terminal. That notebook resolves the active target, prints the
exact `./scripts/deploy.sh ...` command to run, and provides post-deploy
verification.

#### 3. Local app development in `agent_app`

Use the bootstrap/build script once, then use hot reload for normal development.
Both local entrypoints resolve shared settings from `agent_app/databricks.yml`
and sync them into `agent_app/.env` before starting the Python and Node
runtimes.

```bash
cd agent_app

# (optional) Run the ETL pipeline to prepare the metadata and vector index and shared infra for the app to use
./scripts/deploy.sh --target dev --run-job prep

# One-time local bootstrap/build
./scripts/dev-local.sh
# Iterative development with hot reload
./scripts/dev-local-hot-reload.sh
```

Useful options:

* `./scripts/dev-local.sh --profile <profile>`
* `./scripts/dev-local-hot-reload.sh --profile <profile>`
* `./scripts/dev-local-hot-reload.sh --skip-migrate`

#### 4. Single supported deployment surface

Use `agent_app/` for active deployments, validation, and local development.
The older root-level deployment and Model Serving path has been removed from the
supported workflow.

---

## Repository Structure

```text
.
├── etl/                            # Shared ETL notebooks synced by the app bundle
├── agent_app/                      # Canonical Databricks App + deployment bundle
│   ├── databricks.yml              # Canonical app DAB
│   ├── agent_server/               # Multi-agent backend
│   ├── e2e-chatbot-app-next/       # Frontend and app backend
│   ├── workflows/                  # App prep / validation notebook tasks
│   ├── scripts/
│   │   ├── deploy.sh               # Canonical local / CI deploy entrypoint
│   │   ├── dev-local.sh            # One-time local bootstrap/build
│   │   └── dev-local-hot-reload.sh # Local hot-reload workflow
│   ├── resources/                  # App resources + prep/full deployment jobs
│   └── tests/                      # App-specific unit tests
├── docs/                           # Project documentation
└── supplemental_scripts/           # Utility scripts outside deploy flow
```

---

## Documentation

### Getting Started

* [**Development Guide**](docs/DEVELOPMENT_GUIDE.md) - Project setup and workflow overview
* [**Make Instructions**](docs/MAKE_INSTRUCTIONS.md) - `make`-based developer workflow (deploy, local dev, tests)
* [**ETL Guide**](docs/ETL_GUIDE.md) - Metadata indexing workflow used by the app bundle
* [**Local Development Guide**](docs/LOCAL_DEVELOPMENT.md) - Local environment notes
* [**Configuration Reference**](docs/CONFIGURATION.md) - Configuration details across environments

### Reference

* [**Architecture**](docs/ARCHITECTURE.md) - System design and agent workflows
* [**API Reference**](docs/API.md) - Agent APIs and interfaces
* [**Testing Guide**](agent_app/tests/README.md) - Run tests and write new tests
* [**Contributing**](CONTRIBUTING.md) - Contribution guidelines
* `agent_app/scripts/deploy.sh` - Canonical local and CI deployment entry point
* `agent_app/scripts/deploy_notebook.py` - Workspace-native operator handoff
* `agent_app/scripts/dev-local.sh` - Current local bootstrap/build entry point
* `agent_app/scripts/dev-local-hot-reload.sh` - Current hot-reload development entry point

---

## Testing

```bash
cd agent_app
uv sync --dev
uv run pytest tests/ -v
```

See [Testing Guide](agent_app/tests/README.md) for detailed testing documentation.

---

## Configuration

This repository uses a committed bundle baseline plus a local runtime overlay:

| Configuration | Scope | Purpose |
|--------------|-------|---------|
| `agent_app/databricks.yml` | Public-safe deploy + local bootstrap baseline | Canonical dev/prod targets and shared app, ETL, Lakebase, warehouse, MLflow, and Genie settings used by scripts and bundle commands |
| `agent_app/.env` | Local app runtime overlay | Local runtime/auth values plus bundle-derived settings materialized by the local dev scripts |

Update shared environment-aware settings in `agent_app/databricks.yml`. Treat
`agent_app/.env` as local runtime state and local-only overrides.

See [Configuration Guide](docs/CONFIGURATION.md) for more detail.

---

## Examples

### App Deployment

```bash
cd agent_app
./scripts/deploy.sh --target dev --run-job full --start-app
```

### Local Development

```bash
cd agent_app
./scripts/dev-local.sh
./scripts/dev-local-hot-reload.sh
```

---

## What's Included

| Component | Description |
|-----------|-------------|
| **Multi-Agent System** | LangGraph-based agent orchestration with specialized agents |
| **Genie Integration** | Native integration with Databricks Genie spaces |
| **Vector Search** | Semantic routing and metadata retrieval |
| **ETL Pipeline** | Metadata export, enrichment, and vector index build driven from the app bundle |
| **Deployment Tools** | One canonical shell entrypoint plus a guided Databricks notebook handoff |
| **Test Suite** | Supported Python test surface under `agent_app/tests/` |

---

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for:

* Development setup and workflow
* Code style guidelines and testing requirements
* Pull request process
* Community guidelines

For security vulnerabilities, please see our [Security Policy](SECURITY.md).

---

## Support Disclaimer

The content provided here is for **reference and educational purposes only**. It is not officially supported by Databricks under any Service Level Agreements (SLAs). All materials are provided **AS IS**, without any guarantees or warranties, and are not intended for production use without proper review and testing.

The source code in this project is provided under the Databricks License. All third-party libraries included or referenced are subject to their respective licenses. See [NOTICE.md](NOTICE.md) for third-party license information.

If you encounter issues while using this content, please open a GitHub Issue in this repository. Issues will be reviewed as time permits, but there are **no formal SLAs** for support.

---

## License

(c) 2026 Databricks, Inc. All rights reserved.

The source in this project is provided subject to the Databricks License. See [LICENSE.md](LICENSE.md) for details.

**Third-Party Licenses**: This project depends on various third-party packages. See [NOTICE.md](NOTICE.md) for complete attribution and license information.

---

## Acknowledgments

Built with:

* [**LangGraph**](https://github.com/langchain-ai/langgraph) - Agent orchestration and workflow management
* [**Databricks Genie**](https://docs.databricks.com/genie/) - Natural language to SQL conversion
* [**Databricks Vector Search**](https://docs.databricks.com/vector-search/) - Semantic search and retrieval
* [**MLflow**](https://mlflow.org/) - Model deployment and serving
* [**Unity Catalog**](https://docs.databricks.com/data-governance/unity-catalog/) - Data governance and metadata

---

## About Databricks Field Solutions

This repository is part of the [Databricks Field Solutions](https://github.com/databricks-solutions) collection - a curated set of real-world implementations, demonstrations, and technical content created by Databricks field engineers to share practical expertise and best practices.
