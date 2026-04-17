# CLAUDE.md — DBX-UnifiedChat

## What is this project?

A production-ready multi-agent system for intelligent cross-domain data queries on Databricks. Business users ask natural language questions spanning multiple data domains — the system plans, routes, synthesizes SQL, executes, and summarizes answers using a LangGraph workflow backed by Claude models via Databricks Model Serving.

## Tech stack

- **Backend**: Python 3.10+, LangGraph 0.0.30+, LangChain, Pydantic 2.0+
- **Frontend**: Next.js 18+ (TypeScript 5.9+), Drizzle ORM, Biome linter
- **Platform**: Databricks (Unity Catalog, Genie, Vector Search, Lakebase, Model Serving, MLflow)
- **LLMs**: Claude models via `databricks-langchain` (`ChatDatabricks`), NOT direct Anthropic API
- **Deployment**: Databricks Asset Bundles (DAB), Databricks Apps
- **CI/CD**: GitHub Actions

## Key commands

All commands are available via `make help`. Common workflows:

```bash
# Onboarding (first time)
make setup                    # creates .env, installs Python + frontend deps, pre-commit hooks

# Daily development
make dev                      # start LangGraph dev server
make dev-query Q="question"   # run a test query locally
make fe-dev                   # start Next.js frontend dev server

# Code quality
make fmt                      # auto-format all code (Python + frontend)
make lint                     # lint all code
make python-lint-diff         # lint only changed files (fast)
make check                    # pre-push: lint + unit tests
make check-diff               # quick: lint changed files + unit tests

# Testing
make python-test-unit         # unit tests (no Databricks needed)
make python-test-integration  # integration tests (needs Databricks)
make python-test              # all Python tests
make fe-test                  # frontend Playwright tests

# Deployment
make dab-validate             # validate DAB configuration
make dab-deploy-dev           # deploy agent to dev (validates first)
make dab-deploy-prod          # deploy agent to prod (with confirmation)
make dab-etl                  # run full ETL pipeline
make app-deploy-dev-run       # deploy + start Databricks App (dev)
make deploy-dev               # full: check + validate + deploy

# Database
make db-migrate               # run Drizzle migrations
make db-studio                # open Drizzle Studio

# Utilities
make info                     # show environment info
make clean                    # remove build artifacts
```

Raw commands are documented in the Makefile itself. Run `make help` for the full list.

## Directory layout

```
src/multi_agent/              # Core agent system (THE source of truth)
  agents/                     # Agent node implementations
    clarification.py          # Intent detection, clarity check, meta-question handling
    planning.py               # Query analysis, vector search, route selection
    sql_synthesis.py          # SQL generation (table route + genie route)
    sql_execution.py          # SQL warehouse execution
    summarize.py              # Result formatting and explanation
  core/
    config.py                 # Centralized config (AgentConfig dataclass)
    state.py                  # AgentState TypedDict, ConversationTurn
    graph.py                  # LangGraph workflow definition and compilation
    base_agent.py             # Shared agent utilities
  tools/
    uc_functions.py           # Unity Catalog function invocations
  utils/                      # Intent detection, conversation, SQL extraction

agent_app/                    # Databricks App deployment
  agent_server/               # Backend server (mirrors src/)
  e2e-chatbot-app-next/       # Next.js frontend
    client/                   # React components
    server/                   # Next.js API routes
    packages/                 # Shared libraries

tables_to_genies_apx/         # Table enrichment & Genie creation app
etl/                          # ETL pipeline (export → enrich → index)
Notebooks/                    # Databricks notebooks (agent, deploy)
tests/                        # unit/, integration/, e2e/
resources/                    # DAB resource definitions (YAML)
docs/                         # Architecture, configuration, deployment guides
```

## Architecture patterns

### Agent workflow (LangGraph)

The graph follows this flow:
```
clarification → planning → sql_synthesis_(table|genie) → sql_execution → summarize
```

- Each agent is a node function or subgraph registered in `core/graph.py`
- Routing is state-driven via `next_agent` field in `AgentState`
- State is a flat `TypedDict` (`AgentState` in `core/state.py`) — NOT a Pydantic model
- Clarification uses `interrupt()` for human-in-the-loop (requires checkpointer)

### Agent pattern

Agents use `ChatDatabricks` from `databricks-langchain`:
```python
from databricks_langchain import ChatDatabricks
llm = ChatDatabricks(endpoint=config.llm.planning_endpoint)
```

Each agent:
1. Reads from `AgentState` fields
2. Calls LLM with structured output (TypedDict schemas, not Pydantic)
3. Sets `next_agent` to route downstream
4. Returns state update dict

### Configuration

Three config paths, all load the SAME agent code from `src/multi_agent/`:
- **Local dev**: `.env` → `AgentConfig.from_env()`
- **Databricks test**: `dev_config.yaml` → `AgentConfig.from_model_config()`
- **Production**: `prod_config.yaml` → `AgentConfig.from_model_config()`

LLM endpoints are diversified per agent role (fast Haiku for simple tasks, Sonnet for accuracy-critical).

### Naming conventions

- UC names use **short names** everywhere; FQ names derived at runtime via `@property` methods
- Agent-specific LLM endpoints: `LLM_ENDPOINT_{ROLE}` (e.g., `LLM_ENDPOINT_PLANNING`)
- Genie space IDs are comma-separated strings in config

## Important conventions

- **Line length**: 100 (black), 120 (flake8)
- **Imports**: isort with black profile
- **Type hints**: Optional but encouraged; `mypy` configured but lenient (`disallow_untyped_defs = false`)
- **Pre-commit hooks**: black, flake8, pytest-unit
- **Test markers**: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`, `@pytest.mark.slow`
- **No secrets in code**: `.env` is gitignored; use Databricks secrets for YAML configs
- **Frontend**: Biome for linting/formatting (not ESLint/Prettier)

## Deployment targets

| Target | Workspace | Default |
|--------|-----------|---------|
| `dev`  | `fevm-serverless-dbx-unifiedchat-dev.cloud.databricks.com` | Yes |
| `prod` | `fevm-serverless-dbx-unifiedchat.cloud.databricks.com` | No |

## Gotchas

- The `agents/` directory has pairs of files (e.g., `planning.py` + `planning_agent.py`) — the `_agent.py` files contain the class/logic, the plain `.py` files export the node function used by the graph
- `AgentState` uses `Annotated[List, operator.add]` for `messages` — this means messages accumulate, not replace
- `GraphInput` is the minimal input schema for LangGraph Studio / API; `AgentState` is the full internal state
- Lakebase is required for production (checkpoints + long-term memory); local dev uses `MemorySaver`
- Vector search index name is derived: `{catalog}.{schema}.{source_table}_vs_index`
