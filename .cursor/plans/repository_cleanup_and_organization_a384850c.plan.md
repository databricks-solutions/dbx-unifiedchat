---
name: Repository Cleanup and Organization
overview: Transform the repository into a clean, professional, public-facing codebase ready for deployment and peer collaboration by removing outdated documentation, organizing test files, modularizing the Super_Agent code, and restructuring the ETL pipeline.
todos:
  - id: phase1-docs
    content: "Clean up documentation: remove 148+ .md files, organize architecture diagrams to docs/architecture/, create docs/ structure with CONFIGURATION.md"
    status: completed
  - id: phase2-tests
    content: "Organize tests: create tests/ structure, move all test files"
    status: completed
  - id: phase3-modularize
    content: Extract ALL agent logic from Super_Agent_hybrid.py to src/multi_agent/, update deployment to use code_paths parameter (unified approach)
    status: completed
  - id: phase4-etl
    content: "Reorganize ETL: rename Notebooks_Tested_On_Databricks/ to etl/"
    status: completed
  - id: phase5-notebooks
    content: "Clean up Notebooks/: rename to deploy_agent.py, create test_agent_databricks.py, archive old versions"
    status: completed
  - id: phase6-infrastructure
    content: Create pyproject.toml (rename to multi-agent-genie), organize configs (keep YAML at root for deployment, .env for local)
    status: completed
  - id: phase7-cleanup
    content: Remove old test/verify scripts, consolidate job configs to config/jobs/
    status: completed
  - id: phase8-polish
    content: Create LICENSE, CONTRIBUTING.md, docs/CONFIGURATION.md, docs/LOCAL_DEVELOPMENT.md (peer onboarding guide)
    status: in_progress
isProject: false
---

# Repository Cleanup and Organization Plan

## Overview

Transform this repository from a development workspace into a production-ready, public-facing codebase that is:

- Clean and well-organized
- Easy for new contributors to understand
- Ready for deployment
- Following Python best practices

**CRITICAL: Dual-Purpose Repository**
This repository serves TWO distinct purposes:

1. **Databricks Model Serving Deployment**: Uses `dev_config.yaml` and `prod_config.yaml` for direct deployment via MLflow
2. **Local Development**: Uses `config.py` and `.env` for local iteration and testing

Both configuration systems must be maintained and documented.

## Current State Analysis

**Problems identified:**

- 148+ markdown files (mostly session notes and implementation summaries)
- 16+ test files scattered across root and `Notebooks/`
- `Notebooks/Super_Agent_hybrid.py`: 6,833 lines that needs modularization
- Mixed content in `Notebooks/` directory (ETL, agents, tests, docs)
- Multiple outdated/duplicate agent files
- Inconsistent naming conventions

**What we're keeping:**

- **Configuration files** (dual-purpose):
  - `dev_config.yaml` + `prod_config.yaml` - For Databricks deployment
  - `config.py` + `.env` - For local development
- `kumc_poc/` - Well-structured package (will rename to remove client-specific naming)
- Core agent functionality in `Notebooks/Super_Agent_hybrid.py`
- ETL pipeline in `Notebooks_Tested_On_Databricks/`
- All architecture diagram files (will organize into dedicated folder)
- All plan files in `.cursor/plans/` (will keep organized)

# Overall Instructions

**CRITICAL REQUIREMENTS:**

1. **Dual Configuration System** - Must support both:
  - **Databricks Deployment**: `dev_config.yaml` and `prod_config.yaml` (at root, used by Model Serving)
  - **Local Development**: `config.py` and `.env` (for local iteration)
  - `Notebooks/Super_Agent_hybrid.py` must work with BOTH config systems
2. **Naming Conventions**:
  - Rename `kumc_agent` → `multi_agent` throughout (remove client-specific naming)
  - Keep `kumc_poc/` package but document it's legacy structure
3. **File Organization**:
  - Keep ALL architecture diagram files (svg, png, pdf, mmd, csv) → move to `docs/architecture/`
  - Keep ALL plan files in `.cursor/plans/` (already organized)
4. **Notebooks/deploy_agent.py** (renamed from Super_Agent_hybrid.py):
  - This is the MAIN deployment script
  - Must reference `agent.py` for MLflow deployment
  - Simplified to ~200-300 lines (just imports + deployment code)
  - Archives original Super_Agent_hybrid.py for reference

## Unified Code Structure with Dual Configuration

**KEY INSIGHT**: After researching MLflow best practices, we've decided to **UNIFY the code structure** while keeping configuration separate. Both local development and deployment will use the same modular code from `src/multi_agent/`, but with different configuration systems.

### Why Unified Code Structure?

1. **MLflow Native Support**: The `code_paths` parameter in `mlflow.pyfunc.log_model()` is designed for this exact purpose
2. **Single Source of Truth**: No need to keep a 6,833-line notebook in sync with modular code
3. **Easier Testing**: Test the same code that gets deployed
4. **Better Maintainability**: Smaller, focused modules (<500 lines each)
5. **Databricks Best Practice**: Production agents should use clean, typed Python classes

### Architecture: Unified Code, Separate Config

```
┌──────────────────────────────────────────────────────────┐
│                   SOURCE CODE (UNIFIED)                   │
│   src/multi_agent/  ← Used by BOTH environments         │
│   ├── agents/       (single source of truth)             │
│   ├── core/                                              │
│   ├── tools/                                             │
│   └── utils/                                             │
└──────────────────────────────────────────────────────────┘
                          ↓
         ┌────────────────┴────────────────┐
         ↓                                  ↓
┌─────────────────┐              ┌────────────────────┐
│  LOCAL DEV      │              │  DEPLOYMENT        │
├─────────────────┤              ├────────────────────┤
│ config.py       │              │ dev_config.yaml    │
│ .env            │              │ prod_config.yaml   │
│                 │              │                    │
│ Run:            │              │ Deploy via:        │
│ python -m       │              │ Super_Agent_       │
│ src.multi_agent │              │ hybrid.py notebook │
└─────────────────┘              └────────────────────┘
```

This repository serves **THREE WORKFLOWS** with **SHARED CODE** but different configurations:

1. **Local Development**: Fast iteration with `config.py` + `.env`
2. **Databricks Testing**: Test with real services using `dev_config.yaml` (NEW - missing link!)
3. **Production Deployment**: Deploy to Model Serving with `prod_config.yaml`

All three use the same code from `src/multi_agent/` ✅

### Purpose 1: Databricks Model Serving Deployment

**What**: Deploy the agent directly to Databricks Model Serving for production use

**Primary Files**:

- `src/multi_agent/` - **Modular agent code (SHARED with local dev)**
- `Notebooks/deploy_agent.py` - Thin deployment script (~200 lines) - *renamed from Super_Agent_hybrid.py*
- `Notebooks/agent.py` - MLflow wrapper that imports from `src/multi_agent/`
- `dev_config.yaml` - Development environment configuration
- `prod_config.yaml` - Production environment configuration

**Workflow**:

1. Develop/iterate on agent logic in `src/multi_agent/` (locally or in Databricks)
2. Configure deployment via `prod_config.yaml`
3. Open `Notebooks/deploy_agent.py` in Databricks
4. Run deployment cells which use `mlflow.pyfunc.log_model()` with:
  - `python_model="./agent.py"` (wrapper)
  - `code_paths=["../src/multi_agent"]` (packages modular code)
  - `model_config="../prod_config.yaml"` (runtime config)
5. MLflow packages everything together for Model Serving

**Key Point**: 

- Uses `src/multi_agent/` code (SHARED)
- Uses YAML configs (deployment-specific)
- `config.py` and `.env` are **NOT packaged** with the model

### Purpose 2: Local Development & Iteration

**What**: Develop and test agent components locally before deploying

**Primary Files**:

- `src/multi_agent/` - **Modular agent code (SHARED with deployment)**
- `config.py` - Python configuration loader
- `.env` - Local secrets and configuration
- `.env.example` - Template for setting up .env
- `src/multi_agent/main.py` - CLI entry point for local testing

**Workflow**:

1. Edit code in `src/multi_agent/` locally
2. Configure via `.env` file
3. Run locally: `python -m src.multi_agent.main --query "test query"`
4. Run unit tests: `pytest tests/unit/`
5. Run integration tests: `pytest tests/integration/`
6. Once validated, deploy using `Notebooks/deploy_agent.py`
  - **Same code** gets deployed via MLflow's `code_paths` parameter
  - No need to manually sync code between environments

**Key Point**: 

- Uses `src/multi_agent/` code (SHARED)
- Uses `config.py` + `.env` (local-specific)
- YAML configs are **NOT USED** for local dev

### Local Development Path for Peers (Step-by-Step)

```
┌─────────────────────────────────────────────────────────────┐
│              PEER/CONTRIBUTOR WORKFLOW                       │
└─────────────────────────────────────────────────────────────┘

1. Clone Repository
   ↓
   git clone <repo-url>
   cd multi-agent-genie

2. Setup Environment
   ↓
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

3. Configure Local Settings
   ↓
   cp .env.example .env
   # Edit .env with your Databricks credentials
   # See docs/CONFIGURATION.md for help

4. Verify Installation
   ↓
   python -c "from src.multi_agent import *"
   pytest tests/unit/  # Quick verification

5. Start Developing! 🚀
   ↓
   ┌─────────────────────────────────────┐
   │  Edit Code: src/multi_agent/       │
   │  Test: pytest tests/                │
   │  Run: python -m src.multi_agent.main│
   └─────────────────────────────────────┘
   
6. Ready to Deploy?
   ↓
   Open Notebooks/deploy_agent.py in Databricks
   Run deployment cells
   (Uses same code via code_paths parameter!)
```

**Time to First Run**: ~5-10 minutes  
**No Databricks notebook environment needed for local dev!**

## Three Development Workflows

Peers can work in three different modes depending on their needs:

### Workflow 1: Local Development (Fastest Iteration) 🚀

**When to use**: Daily development, unit testing, quick iterations

```bash
# Edit code locally
vim src/multi_agent/agents/supervisor.py

# Test locally
pytest tests/unit/test_supervisor.py -v

# Run agent locally
python -m src.multi_agent.main --query "test"
```

**Pros**:

- ✅ Fastest iteration (no upload/sync delays)
- ✅ Full IDE support (autocomplete, debugging)
- ✅ Works offline (with mocked services)
- ✅ Easy to version control changes

**Cons**:

- ❌ Can't test Databricks-specific services (Genie, Vector Search, etc.)
- ❌ May miss environment-specific issues

---

### Workflow 2: Databricks Notebook Testing (Integration Testing) 🧪

**When to use**: Testing with real Databricks services before deployment

```python
# In Databricks: Open notebooks/test_agent_databricks.py

# 1. Upload your code changes to Databricks workspace
#    (via Databricks CLI or git sync)

# 2. Run test notebook
import sys
sys.path.insert(0, "../src")
from multi_agent.core.graph import create_agent_graph

# 3. Test with real services
agent = create_agent_graph(config)
response = agent.invoke({"input": "test with real Genie"})

# 4. Debug, iterate, repeat
```

**Pros**:

- ✅ Test with real Databricks services (Genie, Vector Search, Lakebase)
- ✅ Catch environment-specific issues early
- ✅ Verify configuration before deployment
- ✅ No deployment overhead (fast testing)

**Cons**:

- ⚠️ Requires syncing code to Databricks workspace
- ⚠️ Slightly slower than local (upload time)

**Setup**:

```bash
# Sync code to Databricks workspace
databricks workspace import-dir src/multi_agent /Workspace/src/multi_agent --overwrite

# Or use Databricks Repos (recommended)
# Fork repo → Link to Databricks Repos → Auto-sync
```

---

### Workflow 3: Production Deployment (Final Step) 🚢

**When to use**: Deploy tested code to Model Serving

```python
# In Databricks: Open notebooks/deploy_agent.py

# 1. Verify code is synced to Databricks
# 2. Configure prod_config.yaml
# 3. Run deployment cells

logged_agent_info = mlflow.pyfunc.log_model(
    python_model="./agent.py",
    code_paths=["../src/multi_agent"],  # Packages your tested code
    model_config="../prod_config.yaml",
    # ...
)

# 4. Deploy to Model Serving
agents.deploy(model_name, version)
```

**Pros**:

- ✅ Production-grade deployment
- ✅ Auto-scaling, monitoring
- ✅ Uses same tested code

**Cons**:

- ⚠️ Slower iteration (deployment takes time)
- ⚠️ Should only deploy tested code

---

### Recommended Development Flow

```
┌─────────────────────────────────────────────────────────┐
│                 TYPICAL PEER WORKFLOW                    │
└─────────────────────────────────────────────────────────┘

1. Local Development (Workflow 1)
   ├─ Write code in src/multi_agent/
   ├─ Unit tests: pytest tests/unit/
   └─ Quick validation: python -m src.multi_agent.main
   
   ↓ (Code looks good locally)

2. Databricks Testing (Workflow 2) [OPTIONAL but RECOMMENDED]
   ├─ Sync code to Databricks workspace
   ├─ Run notebooks/test_agent_databricks.py
   ├─ Test with real Genie spaces, Vector Search
   └─ Debug any environment-specific issues
   
   ↓ (Tests pass in Databricks)

3. Production Deployment (Workflow 3)
   ├─ Update prod_config.yaml if needed
   ├─ Run notebooks/deploy_agent.py
   └─ Deploy to Model Serving
   
   ↓ (Agent is live!)

4. Monitor & Iterate
   └─ Check Model Serving logs → Repeat 1-3 as needed
```

**Key Point**: Workflow 2 (Databricks Testing) is the **missing link** between local dev and deployment. It lets you catch issues with real Databricks services before deploying!

### Quick Reference: What Files Are For What


| File                                 | Deployment   | Databricks Testing | Local Dev    | Purpose                                                    |
| ------------------------------------ | ------------ | ------------------ | ------------ | ---------------------------------------------------------- |
| `src/multi_agent/`                   | ✅ **SHARED** | ✅ **SHARED**       | ✅ **SHARED** | **Modular agent code (single source of truth)**            |
| `Notebooks/deploy_agent.py`          | ✅ Primary    | ❌                  | ❌            | Thin deployment script (~200 lines)                        |
| `Notebooks/test_agent_databricks.py` | ❌            | ✅ Primary          | ❌            | **NEW**: Test modular code in Databricks without deploying |
| `Notebooks/agent.py`                 | ✅ Wrapper    | 📖                 | ❌            | MLflow wrapper, imports from `src/multi_agent/`            |
| `dev_config.yaml`                    | ✅ Dev env    | ✅ Testing          | ❌            | Development/testing environment config (YAML)              |
| `prod_config.yaml`                   | ✅ Prod env   | ❌                  | ❌            | Production environment config (YAML)                       |
| `config.py`                          | ❌            | ❌                  | ✅ Primary    | Python config loader for local dev                         |
| `.env`                               | ❌            | ❌                  | ✅ Primary    | Local secrets and configuration                            |
| `.env.example`                       | ❌            | ❌                  | ✅ Template   | Template for .env setup                                    |
| `src/multi_agent/main.py`            | ❌            | ❌                  | ✅ CLI        | Local CLI entry point                                      |


**KEY INSIGHTS**: 

- The `src/multi_agent/` code is **SHARED** across all three workflows
- `test_agent_databricks.py` is the bridge between local dev and deployment
- Test in Databricks before deploying to catch environment-specific issues!

## KEY ARCHITECTURAL DECISIONS

### Decision 1: Unified Code Structure

#### Summary

**BEFORE** (Original Plan):

- Local Dev: Uses `src/multi_agent/` (modular)
- Deployment: Uses `Notebooks/Super_Agent_hybrid.py` (6,833-line monolith)
- Problem: Two code versions to maintain and keep in sync

**AFTER** (Updated Plan):

- **BOTH** Local Dev AND Deployment: Use `src/multi_agent/` (modular, single source of truth)
- Deployment packages modular code via MLflow's `code_paths` parameter
- Configuration remains separate (YAML for deployment, .env for local)

### Implementation Example

**Current Super_Agent_hybrid.py (line 5627)** - *will be renamed to deploy_agent.py*:

```python
logged_agent_info = mlflow.pyfunc.log_model(
    name="super_agent_hybrid_with_memory",
    python_model="./agent.py",
    input_example=input_example,
    resources=resources,
    model_config="../prod_config.yaml",
    pip_requirements=[...]
)
```

**Updated deploy_agent.py** (renamed from Super_Agent_hybrid.py):

```python
logged_agent_info = mlflow.pyfunc.log_model(
    name="super_agent_hybrid_with_memory",
    python_model="./agent.py",  # Thin wrapper
    code_paths=["../src/multi_agent"],  # 🎯 NEW: Package modular code
    input_example=input_example,
    resources=resources,
    model_config="../prod_config.yaml",
    pip_requirements=[...]
)
```

**Updated Notebooks/agent.py**:

```python
# Notebooks/agent.py - Thin MLflow wrapper
import sys
sys.path.insert(0, "../src")  # Add src to path

from multi_agent.core.graph import create_agent_graph
from multi_agent.core.config import load_config_from_yaml
import mlflow

# Load config from YAML (passed via model_config parameter)
config = load_config_from_yaml(mlflow.models.ModelConfig.get())

# Create agent using modular components
agent = create_agent_graph(config)

# Register with MLflow
mlflow.models.set_model(agent)
```

### Benefits

1. ✅ **Single Source of Truth**: One codebase for both environments
2. ✅ **Native MLflow Support**: `code_paths` parameter designed for this
3. ✅ **Easy Testing**: Test the same code that gets deployed
4. ✅ **Better Maintainability**: Small focused modules (<500 lines)
5. ✅ **Faster Iteration**: No need to sync changes between two codebases

### Decision 2: Rename Super_Agent_hybrid.py → deploy_agent.py

#### Rationale

Since `Super_Agent_hybrid.py` will be simplified from 6,833 lines to ~200-300 lines (just imports + deployment code), the name should reflect its new purpose.

**Why `deploy_agent.py**`:

1. ✅ **Clear Purpose**: Name immediately tells you this file is for deployment
2. ✅ **Avoid Confusion**: Removes assumption that agent logic is in this file
3. ✅ **Better for Public Repo**: New contributors understand the file structure faster
4. ✅ **Follows Convention**: Uses Python verb_noun naming pattern
5. ✅ **Separates Concerns**: Clear distinction between agent code (`src/multi_agent/`) and deployment (`deploy_agent.py`)

**What Happens to Original**:

- Archive `Super_Agent_hybrid.py` (6,833 lines) to `notebooks/archive/` for reference
- Useful for understanding the evolution and for comparison

**Documentation Updates Needed**:

- Update README.md
- Update docs/DEPLOYMENT.md
- Update any references in other documentation

---

## Phase 1: Documentation Cleanup

### Remove Outdated Documentation

Delete all session summary and implementation tracking .md files:

- All `*_SUMMARY.md`, `*_FIX.md`, `*_GUIDE.md` files from root
- All `.md` files from `Notebooks/` directory
- All `.md` files from `Instructions/` directory (archive useful ones first)
- **KEEP**: `README.md`, `.env.example`, architecture diagrams

### Organize Architecture Diagrams

Create `docs/architecture/` directory and move ALL architecture files:

- `architecture_diagram_simple.pdf`
- `architecture_diagram_simple.png`
- `architecture_diagram.mmd`
- `architecture_diagram.svg`
- `architecture_nodes_edges.csv`
- Any `.mmd` files from `Notebooks/`

### Create Essential Documentation Structure

Create new `docs/` directory with:

- `docs/ARCHITECTURE.md` - System architecture and design decisions (reference diagrams)
- `docs/DEPLOYMENT.md` - Deployment instructions for Databricks (cover both YAML and .env configs)
- `docs/CONTRIBUTING.md` - Guidelines for peer contributions
- `docs/API.md` - API reference for agents and tools
- `docs/CHANGELOG.md` - Version history
- `docs/CONFIGURATION.md` - **NEW**: Explain dual config system (YAML vs .env)

### Rewrite Core README

Update `README.md` to include:

- Clear project description and goals (generalized, not client-specific)
- **Dual-purpose explanation**: Databricks deployment vs Local development
- Quick start guides for BOTH use cases:
  - **Quick Start 1: Local Development** (detailed below)
  - **Quick Start 2: Deploy to Databricks** (using YAML configs)
- Architecture diagram reference (link to `docs/architecture/`)
- Configuration guide reference (link to `docs/CONFIGURATION.md`)
- Link to detailed documentation
- Contributing guidelines reference
- License information

### Create Local Development Quickstart Guide

Add a prominent section in README.md and create `docs/LOCAL_DEVELOPMENT.md` with the following workflow:

#### For Peers/Contributors: Local Development Path

**Prerequisites**:

- Python 3.10 or above
- Git
- Databricks workspace access (for testing against real services)
- OR: Mock services for fully local testing (optional)

**Step-by-Step Setup**:

```bash
# 1. Clone the repository
git clone <repo-url>
cd KUMC_POC_hlsfieldtemp  # Or renamed to multi-agent-genie

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # For testing

# 4. Configure environment
cp .env.example .env
# Edit .env with your Databricks credentials and configuration
# See docs/CONFIGURATION.md for details on each variable

# 5. Verify installation
python -c "from src.multi_agent import *; print('✓ Installation successful')"

# 6. Run tests to verify setup
pytest tests/unit/          # Fast unit tests
pytest tests/integration/   # Integration tests (requires Databricks access)

# 7. Run the agent locally
python -m src.multi_agent.main --query "Show me patient data"

# Or use interactive mode
python -m src.multi_agent.main --interactive
```

**Development Workflow**:

```bash
# 1. Create a feature branch
git checkout -b feature/my-new-feature

# 2. Edit code in src/multi_agent/
# Example: Modify an agent in src/multi_agent/agents/

# 3. Test your changes
pytest tests/unit/test_your_feature.py -v
pytest tests/integration/ -v

# 4. Run the agent locally to verify
python -m src.multi_agent.main --query "test query"

# 5. Commit changes
git add .
git commit -m "Add new feature"

# 6. Push and create PR
git push origin feature/my-new-feature
# Create PR on GitHub/GitLab
```

**Key Files for Local Development**:


| File/Directory            | Purpose                                    | Edit Frequency |
| ------------------------- | ------------------------------------------ | -------------- |
| `src/multi_agent/`        | **All agent code** - main development area | ⭐ High         |
| `.env`                    | Local configuration & secrets              | Once (setup)   |
| `config.py`               | Config loader (rarely needs changes)       | Low            |
| `tests/`                  | Unit & integration tests                   | High           |
| `src/multi_agent/main.py` | CLI entry point for local testing          | Medium         |


**Common Development Tasks**:

```bash
# Run specific test
pytest tests/unit/test_agents.py::test_supervisor_agent -v

# Run with coverage
pytest --cov=src.multi_agent tests/

# Format code
black src/ tests/
isort src/ tests/

# Lint code
flake8 src/ tests/
mypy src/

# Run agent with debug logging
DEBUG=1 python -m src.multi_agent.main --query "test"

# Test against local mock services (if configured)
MOCK_MODE=1 python -m src.multi_agent.main --query "test"
```

**What You DON'T Need for Local Dev**:

- ❌ `Notebooks/deploy_agent.py` - Only for deployment
- ❌ `dev_config.yaml` / `prod_config.yaml` - Only for deployment
- ❌ Databricks notebook environment
- ❌ Model Serving endpoint

**What You DO Need for Local Dev**:

- ✅ `src/multi_agent/` - All agent code
- ✅ `.env` - Your local configuration
- ✅ `config.py` - Config loader
- ✅ Python virtual environment
- ✅ Test data (in `tests/fixtures/`)

**Testing in Databricks (Before Deploying)**:

If you want to test your code changes in Databricks environment:

```bash
# Option 1: Use Databricks CLI to sync code
databricks workspace import-dir src/multi_agent /Workspace/src/multi_agent --overwrite

# Option 2: Use Databricks Repos (Recommended)
# 1. Fork/clone repo in GitHub
# 2. Link to Databricks Repos
# 3. Code auto-syncs when you push

# Then in Databricks, open notebooks/test_agent_databricks.py
# and run it to test with real services
```

This lets you test with real Databricks services (Genie, Vector Search, Lakebase) before deploying to Model Serving!

## Phase 2: Testing Organization

### Create Test Directory Structure

```
tests/
├── __init__.py
├── conftest.py (shared fixtures)
├── unit/
│   ├── __init__.py
│   ├── test_agents.py
│   ├── test_tools.py
│   └── test_config.py
├── integration/
│   ├── __init__.py
│   ├── test_agent_workflow.py
│   └── test_vector_search.py
└── e2e/
    ├── __init__.py
    └── test_multi_agent_system.py
```

### Move Test Files

- Move all `test_*.py` from root to `tests/integration/`
- Move test files from `[Notebooks/](Notebooks/)` to appropriate test directories
- Consolidate duplicate tests
- Remove outdated test files: `test_case_sensitivity_fix.py`, `test_json_serialization.py`, etc.

### Update Test Configuration

- Merge test fixtures into `tests/conftest.py`
- Update import paths in all test files
- Create `tests/README.md` with test execution instructions

## Phase 3: Agent Code Modularization (UNIFIED APPROACH)

**CRITICAL CHANGE**: Based on MLflow best practices and the `code_paths` parameter support, we will use modular code for BOTH local development AND deployment. This creates a single source of truth.

**Benefits**:

- Single codebase for both environments
- MLflow natively supports this via `code_paths` parameter
- Easier to maintain and test
- Follows Databricks production best practices

### Create Source Package Structure

```
src/
└── multi_agent/  # RENAMED from kumc_agent
    ├── __init__.py
    ├── agents/
    │   ├── __init__.py
    │   ├── supervisor.py
    │   ├── thinking_planning.py
    │   ├── genie.py
    │   ├── sql_synthesis.py
    │   ├── sql_execution.py
    │   ├── clarification.py
    │   └── summarize.py
    ├── core/
    │   ├── __init__.py
    │   ├── state.py
    │   ├── graph.py
    │   ├── config.py  # Supports BOTH .env and YAML configs
    │   └── config_loader.py  # NEW: Unified config loader
    ├── tools/
    │   ├── __init__.py
    │   ├── vector_search.py
    │   └── uc_functions.py
    └── utils/
        ├── __init__.py
        ├── conversation.py
        └── memory.py
```

### Refactor and Rename Super_Agent_hybrid.py (UNIFIED APPROACH)

**NEW STRATEGY**: Extract all agent logic and rename the deployment file for clarity.

**Steps**:

1. **Extract ALL agent logic** from `Notebooks/Super_Agent_hybrid.py` (6,833 lines) to `src/multi_agent/`
2. **Simplify and rename** `Super_Agent_hybrid.py` → `deploy_agent.py`:
  - Configuration cells (using dev_config.yaml/prod_config.yaml)
  - Imports from `src/multi_agent/`
  - MLflow logging/deployment code
  - Example usage/testing cells
  - Target: Reduce from 6,833 lines to ~200-300 lines
3. **Archive original**: Keep `Super_Agent_hybrid.py` in `notebooks/archive/` for reference
4. **Update `Notebooks/agent.py**` to import from `src/multi_agent/`
5. **Use `code_paths` in MLflow deployment** to package modular code

**Rationale for rename**:

- `deploy_agent.py` clearly indicates its purpose (deployment only)
- Removes confusion about where agent logic lives (it's in `src/multi_agent/`)
- Better for public-facing repository

Extract from `Notebooks/Super_Agent_hybrid.py` (6,833 lines) into modules:

1. **Agent classes** → `src/multi_agent/agents/`:
  - Extract agent definitions (functions or classes)
  - Each agent gets its own module (supervisor, thinking_planning, genie, etc.)
  - Include agent-specific tools and helper functions
2. **State management** → `src/multi_agent/core/state.py`:
  - State classes and type definitions
  - State initialization and update logic
3. **Graph construction** → `src/multi_agent/core/graph.py`:
  - LangGraph workflow definition
  - Node routing logic
  - Conditional edges
4. **Configuration** → `src/multi_agent/core/config.py`:
  - Copy logic from root `config.py`
  - **Enhance to support BOTH config methods**:
    - Load from `.env` (local dev)
    - Load from YAML files (deployment)
  - Keep all configuration dataclasses
5. **Tools** → `src/multi_agent/tools/`:
  - Extract vector search tools from `Notebooks/agent_uc_functions.py`
  - Extract UC functions
6. **Utilities** → `src/multi_agent/utils/`:
  - Conversation management from `kumc_poc/conversation_models.py`
  - Memory management utilities

### Create Entry Points

**For Local Development**: Create `src/multi_agent/main.py`:

- Imports all modularized components
- Provides CLI for running agent locally
- Uses `config.py` + `.env` for configuration
- Example: `python -m src.multi_agent.main --query "Show me patient data"`

**For Deployment**: Update `Notebooks/agent.py`:

- Thin wrapper that imports from `src/multi_agent/`
- Implements `mlflow.pyfunc.ResponsesAgent` interface
- Loads config from YAML files (dev_config.yaml / prod_config.yaml)
- Example structure:
  ```python
  # Notebooks/agent.py
  import sys
  sys.path.append("../src")  # Add src to path

  from multi_agent.agents.supervisor import SupervisorAgent
  from multi_agent.core.graph import create_agent_graph
  from multi_agent.core.config import load_config_from_yaml

  # Load config from YAML (passed via model_config parameter)
  config = load_config_from_yaml(...)

  # Create agent using modular components
  agent = create_agent_graph(config)
  mlflow.models.set_model(agent)
  ```

**For MLflow Deployment**: Update `Notebooks/deploy_agent.py` (renamed from Super_Agent_hybrid.py) line ~5627:

```python
logged_agent_info = mlflow.pyfunc.log_model(
    name="super_agent_hybrid_with_memory",
    python_model="./agent.py",  # Imports from src/multi_agent/
    code_paths=["../src/multi_agent"],  # 🎯 Package modular code
    input_example=input_example,
    resources=resources,
    model_config="../prod_config.yaml",
    pip_requirements=[...]
)
```

## Phase 4: ETL Pipeline Organization

### Rename and Restructure ETL Directory

Rename `[Notebooks_Tested_On_Databricks/](Notebooks_Tested_On_Databricks/)` → `etl/`:

```
etl/
├── README.md
├── 01_export_genie_spaces.py
├── 02_enrich_table_metadata.py
├── 03_build_vector_search_index.py
└── test_uc_functions.py
```

### Update ETL Files

- Rename files to be more descriptive:
  - `01_Export_Genie_Spaces.py` → `01_export_genie_spaces.py`
  - `02_Table_MetaInfo_Enrichment.py` → `02_enrich_table_metadata.py`
  - `03_VS_Enriched_Genie_Spaces.py` → `03_build_vector_search_index.py`
  - `test_uc_functions.py` → move to `tests/integration/`
- Create `etl/README.md` documenting:
  - Execution order and dependencies
  - Prerequisites and environment setup
  - Expected inputs/outputs for each script

## Phase 5: Notebooks Directory Cleanup

### Reorganize Notebooks

```
notebooks/
├── README.md
├── examples/
│   ├── 01_quick_start_demo.py
│   ├── 02_multi_agent_query_example.py
│   └── 03_advanced_usage.py
└── development/
    └── (keep experimental notebooks here)
```

### Consolidate Working Files

**Rename Primary Deployment File**:

- Rename `Super_Agent_hybrid.py` → `deploy_agent.py` (clear, descriptive name)
- Simplified to ~200-300 lines (imports + deployment only)

**Archive Old Versions**:
Create `notebooks/archive/` directory and move:

- Original `Super_Agent_hybrid.py` (6,833 lines) - keep for reference
- `Super_Agent(out of dated).py`
- `Super_Agent_hybrid_no_class.py`
- `Super_Agent_hybrid_local_dev.py`
- `Super_Agent_langgraph_multiagent_genie.py`
- Duplicate framework files

**Keep in Notebooks/**:

- `deploy_agent.py` - Main deployment script
- `agent.py` - MLflow wrapper
- `test_agent_databricks.py` - **NEW**: Test modular code in Databricks notebooks
- Create `examples/` subdirectory for demo notebooks

**Create test_agent_databricks.py**:
This notebook allows peers to test the modular code in Databricks environment before deploying:

```python
# Databricks notebook source
# DBTITLE 1,Test Modular Agent in Databricks (No Deployment)

# Add src to path
import sys
sys.path.insert(0, "../src")

# Import modular agent code
from multi_agent.core.graph import create_agent_graph
from multi_agent.core.config import load_config_from_yaml

# Load configuration (use dev_config.yaml for testing)
config = load_config_from_yaml("../dev_config.yaml")

# Create agent from modular components
agent = create_agent_graph(config)

# Test the agent interactively
test_query = "Show me patient data"
response = agent.invoke({"input": test_query})
print(response)

# Run more tests, debug, iterate...
# This is for TESTING only, not deployment
```

**Purpose**: 

- Test modular code changes in Databricks environment
- Debug issues with Databricks-specific services (Genie, Vector Search, etc.)
- Verify configuration before deploying
- Iterate quickly without deploying to Model Serving

## Phase 6: Project Infrastructure

### Create Python Package Configuration

Create `pyproject.toml` at root:

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "multi-agent-genie"  # RENAMED from kumc-agent
version = "1.0.0"
description = "Multi-Agent System for Cross-Domain Queries with Databricks Genie"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    # List from requirements.txt
]
```

### Organize Configuration Files

**KEEP at root** (used for deployment):

- `dev_config.yaml` - Development environment config for Databricks
- `prod_config.yaml` - Production environment config for Databricks
- `.env.example` - Template for local development

**KEEP at root** (used for local dev):

- `config.py` - Python configuration loader
- `.env` - Local development secrets (gitignored)

**Consolidate job configs**:

- Move `job_config_*.json` files → `config/jobs/`
- Document which configs are for what purpose

### Update .gitignore

Add/ensure these entries:

- `.env` (keep secrets out of git)
- `Workspace/` (temporary workspace files)
- Standard Python ignores (`__pycache__/`, `*.pyc`, `.venv/`, etc.)
- `data/` (local data directory)
- `.databricks/` (Databricks CLI cache)

### Create Development Setup

- `Makefile` or `scripts/setup.sh` for environment setup
  - Include steps for BOTH config methods
  - Document when to use which approach
- Development dependencies in `requirements-dev.txt`
- Pre-commit hooks configuration

## Phase 7: Cleanup Old Files

### Remove Temporary/Generated Files

- ~~Architecture diagram files~~ **KEEP ALL** - already moved to `docs/architecture/` in Phase 1
- Old job config files in root (moved to `config/jobs/`)
- `test_results.json`
- `Workspace/` directory (add to `.gitignore` or delete if not needed)

### Remove Duplicate/Outdated Scripts

From root directory:

- `verify_*.py` scripts
- `test_*.py` files (already moved to `tests/`)
- `rename_routes.py`
- `register_uc_functions.py` (move to `scripts/`)
- `upload_to_databricks.py` (move to `scripts/` )
- `run_agent_test_databricks.py` (move to `scripts/`)

## Phase 8: Final Polish

### Create Standard Repository Files

- `LICENSE` - Choose appropriate open-source license
- `CONTRIBUTING.md` - Contribution guidelines (reference LOCAL_DEVELOPMENT.md)
- `CODE_OF_CONDUCT.md` - Community standards
- `docs/LOCAL_DEVELOPMENT.md` - **NEW**: Complete guide for peers doing local development
  - Step-by-step setup instructions
  - Common development tasks
  - Troubleshooting guide
  - What files to edit vs. what to ignore
- `.github/` - GitHub-specific files:
  - `workflows/ci.yml` - Continuous integration
  - `ISSUE_TEMPLATE/` - Issue templates
  - `PULL_REQUEST_TEMPLATE.md` - PR template

### Update Import Paths

- Update all notebooks to import from `src.kumc_agent`
- Update test files with new import paths
- Ensure backwards compatibility if needed

### Documentation Review

- Ensure all new docs are complete
- Add architecture diagrams to docs
- Cross-link documentation appropriately
- Update all references from `Super_Agent_hybrid.py` to `deploy_agent.py`:
  - README.md
  - docs/DEPLOYMENT.md
  - docs/ARCHITECTURE.md
  - Any other documentation files
- Spell check and grammar review

## Expected Final Structure

```
KUMC_POC_hlsfieldtemp/  # Consider renaming to multi-agent-genie later
├── .cursor/
│   └── plans/  # All plan files preserved
├── .github/
│   └── workflows/
│       └── ci.yml
├── config/
│   └── jobs/  # Consolidated job configs
│       ├── job_config_etl.json
│       └── job_config_deployment.json
├── docs/
│   ├── architecture/  # All architecture diagrams
│   │   ├── architecture_diagram.svg
│   │   ├── architecture_diagram.png
│   │   ├── architecture_diagram.pdf
│   │   ├── architecture_diagram.mmd
│   │   └── *.mmd (other diagrams)
│   ├── ARCHITECTURE.md
│   ├── DEPLOYMENT.md
│   ├── CONFIGURATION.md  # NEW: Explains dual config system
│   ├── LOCAL_DEVELOPMENT.md  # NEW: Complete local dev guide for peers
│   ├── CONTRIBUTING.md
│   ├── API.md
│   └── CHANGELOG.md
├── etl/
│   ├── README.md
│   ├── 01_export_genie_spaces.py
│   ├── 02_enrich_table_metadata.py
│   └── 03_build_vector_search_index.py
├── notebooks/
│   ├── README.md
│   ├── deploy_agent.py  # PRIMARY deployment script (renamed from Super_Agent_hybrid.py)
│   ├── test_agent_databricks.py  # NEW: Test modular code in Databricks without deploying
│   ├── agent.py  # MLflow wrapper for deployment
│   ├── archive/  # Historical versions for reference
│   │   ├── Super_Agent_hybrid.py  # Original 6,833-line version
│   │   ├── Super_Agent(out of dated).py
│   │   └── *.py  # Other old versions
│   └── examples/
│       ├── 01_quick_start_demo.py
│       └── 02_advanced_usage.py
├── scripts/
│   ├── setup.sh
│   ├── deploy.sh
│   ├── register_uc_functions.py
│   └── upload_to_databricks.py
├── src/
│   └── multi_agent/  # RENAMED from kumc_agent
│       ├── agents/
│       ├── core/
│       │   ├── config.py  # Supports both .env and YAML
│       │   └── config_loader.py
│       ├── tools/
│       └── utils/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── .env  # Local dev secrets (gitignored)
├── .env.example  # Template for local dev
├── .gitignore
├── config.py  # Local dev config loader (Python)
├── databricks.yml
├── dev_config.yaml  # Databricks dev deployment config
├── prod_config.yaml  # Databricks prod deployment config
├── LICENSE
├── Makefile
├── pyproject.toml
├── README.md
├── requirements.txt
└── requirements-dev.txt
```

**Key Points:**

- **Dual Config System**: Both YAML (deployment) and .env (local dev) configs at root
- **Notebooks/Super_Agent_hybrid.py**: Primary deployment artifact (preserved)
- **src/multi_agent/**: Modular code for local development
- **docs/architecture/**: All diagram files organized
- **.cursor/plans/**: All plan files preserved

## Migration Strategy

### Safety First

1. **Create a backup branch** before starting: `git checkout -b backup-pre-cleanup`
2. Work in phases, committing after each major change
3. Test BOTH deployment and local dev workflows after changes
4. Keep a rollback plan

### Critical Preservation Rules

**UNIFIED CODE STRUCTURE**:

- `src/multi_agent/` must be importable from BOTH:
  - Local development: `from src.multi_agent import ...`
  - Deployment: Via `code_paths` parameter in MLflow (packaged with model)
- ALL agent logic moves to `src/multi_agent/` (single source of truth)

**DO NOT BREAK DEPLOYMENT**:

- `Notebooks/deploy_agent.py` (renamed from Super_Agent_hybrid.py) must successfully:
  - Import from `../src/multi_agent/`
  - Reference `./agent.py` in MLflow log_model (line ~5632)
  - Reference `../prod_config.yaml` in model_config (line ~5637)
  - Include `code_paths=["../src/multi_agent"]` in log_model
- Archive original `Super_Agent_hybrid.py` to `notebooks/archive/` for reference
- `dev_config.yaml` and `prod_config.yaml` must stay at root
- `Notebooks/agent.py` must:
  - Import from `../src/multi_agent/`
  - Implement MLflow ResponsesAgent interface

**DO NOT BREAK LOCAL DEV**:

- `config.py` must work with `.env` files
- `python -m src.multi_agent.main` must run successfully
- All tests in `tests/` must pass with imports from `src.multi_agent`

### Testing Strategy

After each phase:

1. **Test Deployment Path**:
  - Verify `Notebooks/deploy_agent.py` can reference configs
  - Check YAML config files are accessible  
  - Validate MLflow deployment code has `code_paths` parameter
2. **Test Databricks Testing Path** (NEW - Critical for Peers):
  - Upload `src/multi_agent/` to Databricks workspace
  - Run `Notebooks/test_agent_databricks.py` in Databricks
  - Verify imports from `../src/multi_agent/` work
  - Test with real Databricks services (Genie, Vector Search)
  - Confirm `dev_config.yaml` loads correctly
3. **Test Local Dev Path**:
  - Run `python -c "from src.multi_agent import *"` to check imports
  - Verify `config.py` can load `.env`
  - Run unit tests: `pytest tests/unit/`
  - Run CLI: `python -m src.multi_agent.main --query "test"`
4. **Test ETL** (Phase 4):
  - Verify ETL scripts still run after renaming
5. **Integration Tests**:
  - Run full test suite: `pytest tests/`
  - Verify all THREE workflows work:
    - Local: config.py + .env
    - Databricks Testing: dev_config.yaml
    - Deployment: prod_config.yaml
6. **Three-Workflow End-to-End Test**:
  - **Workflow 1 (Local)**: Clone → setup → run in <10 min
  - **Workflow 2 (Databricks Test)**: Sync → test in notebook → verify
  - **Workflow 3 (Deploy)**: deploy_agent.py → Model Serving
7. **Peer Onboarding Test** (Critical for Public Repo):
  - Have someone unfamiliar clone repo
  - Follow `docs/LOCAL_DEVELOPMENT.md` step-by-step
  - Time them: Should be running in ~10 minutes
  - Have them try Databricks testing workflow
  - Document any confusion or missing steps
  - Verify they can:
    - Install dependencies
    - Configure `.env`
    - Run tests locally
    - Run agent locally
    - Sync to Databricks and test there (Workflow 2)
    - Make a code change and see it work in both environments

## Success Criteria

**Code Structure**:

- ✅ ALL agent logic extracted to `src/multi_agent/` (<500 lines per file)
- ✅ `Notebooks/Super_Agent_hybrid.py` renamed to `deploy_agent.py` and reduced to ~200-300 lines
- ✅ Original `Super_Agent_hybrid.py` archived to `notebooks/archive/` for reference
- ✅ `Notebooks/agent.py` imports from `src/multi_agent/`
- ✅ `src/multi_agent/` code works in BOTH environments (unified approach)

**Configuration**:

- ✅ Dual configuration system working:
  - YAML files (dev_config.yaml, prod_config.yaml) for deployment
  - Python config (config.py + .env) for local dev
- ✅ `docs/CONFIGURATION.md` clearly explains unified code + separate configs

**Deployment**:

- ✅ MLflow log_model uses `code_paths=["../src/multi_agent"]` to package modular code
- ✅ Model can be deployed to Databricks Model Serving successfully
- ✅ `Notebooks/deploy_agent.py` line ~5632: `python_model="./agent.py"` path works
- ✅ `Notebooks/deploy_agent.py` line ~5637: `model_config="../prod_config.yaml"` path works

**Databricks Testing** (New Workflow):

- ✅ `Notebooks/test_agent_databricks.py` can import from `../src/multi_agent/`
- ✅ Agent runs successfully in Databricks notebook environment
- ✅ Can test with real Databricks services (Genie, Vector Search, Lakebase)
- ✅ Config loads correctly from `dev_config.yaml`

**Local Development**:

- ✅ `python -m src.multi_agent.main` runs successfully
- ✅ All imports from `src.multi_agent` work correctly
- ✅ Unit tests passing: `pytest tests/unit/`
- ✅ Integration tests passing: `pytest tests/integration/`

**Organization**:

- ✅ Zero markdown files in root (except README.md)
- ✅ All test files in `tests/` directory with proper structure
- ✅ ETL pipeline in `etl/` directory with clear documentation
- ✅ All architecture diagrams organized in `docs/architecture/`
- ✅ All plan files preserved in `.cursor/plans/`

**Documentation**:

- ✅ Professional README with getting started for ALL THREE workflows
- ✅ `docs/LOCAL_DEVELOPMENT.md` - Complete local dev guide for peers
- ✅ `docs/CONFIGURATION.md` explains unified code approach
- ✅ `docs/DEPLOYMENT.md` covers MLflow code_paths usage and Databricks testing
- ✅ Clear documentation for new contributors
- ✅ Verified a peer can:
  - Go from clone to local running in ~10 minutes
  - Sync to Databricks and test there
  - Deploy to Model Serving

