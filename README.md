# Multi-Agent System for Cross-Domain Queries

> A production-ready multi-agent system built with LangGraph and Databricks Genie

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

## 🎯 Two-Phase System

This repository contains a complete data-to-deployment pipeline:

### Phase 1: ETL Pipeline (Data Preparation) - **Run First**
Prepare enriched metadata and vector search index for agent queries
- Export Genie space metadata
- Enrich table metadata with samples and statistics
- Build vector search index for semantic retrieval

### Phase 2: Agent System (Query & Inference) - **After ETL**
Multi-agent system for intelligent cross-domain queries
- Semantic query routing across multiple data sources
- SQL synthesis and execution
- Context-aware responses with reasoning

## 🚀 Quick Start Guides

### Getting Started: Choose Your Workflow

#### 📊 Phase 1: ETL Pipeline

```bash
# See etl/README.md for complete ETL guide
cd etl/
# Run locally with sample data
python local_dev_etl.py --all --sample-size 10
```

**ETL Workflows**:
1. **Local Testing**: Test transformations with sample data
2. **Databricks Testing**: Test on real services with small dataset  
3. **Production**: Full pipeline on complete dataset

📖 **Complete Guide**: [etl/README.md](etl/README.md)

---

#### 🤖 Phase 2: Agent Development (After ETL Completes)

**1️⃣ Local Development** (Fastest - Daily work)
```bash
# Clone and setup
git clone <repo-url>
cd KUMC_POC_hlsfieldtemp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit with your credentials

# Run agent locally
python -m src.multi_agent.main --query "Show me patient data"
```
⏱️ **Time**: ~5 minutes | 📖 **Guide**: [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md)

**2️⃣ Test in Databricks** (Before deploying)
```bash
# Sync code to Databricks
databricks workspace import-dir src /Workspace/src --overwrite

# Open in Databricks: notebooks/test_agent_databricks.py
# Test with real Genie spaces, Vector Search, etc.
```
⏱️ **Time**: ~10 minutes | 📖 **Guide**: [notebooks/README.md](notebooks/README.md)

**3️⃣ Deploy to Production**
```bash
# Open in Databricks: notebooks/deploy_agent.py
# Run deployment cells → Deploy to Model Serving
```
⏱️ **Time**: ~15 minutes | 📖 **Guide**: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

## 📋 Complete Development Flow

```
┌─────────────────────────────────────────────────────────┐
│ STEP 1: ETL Pipeline (Data Preparation)                 │
│ Run etl/ scripts to prepare data                        │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│ STEP 2: Agent Development (Query & Inference)           │
│ Local Dev → Databricks Test → Deploy                   │
│    ↓              ↓              ↓                       │
│   Fast       Real Services  Production                  │
└─────────────────────────────────────────────────────────┘
```

## 🏗️ Architecture

The system uses a multi-agent architecture with specialized agents:
- **SupervisorAgent**: Orchestrates workflow
- **ThinkingPlanningAgent**: Query analysis and planning
- **GenieAgents**: Query individual Genie spaces
- **SQLSynthesisAgent**: Combines SQL across spaces
- **SQLExecutionAgent**: Executes synthesized queries

[See Architecture Diagrams](docs/architecture/)

## 📂 Repository Structure

```
.
├── etl/                    # Phase 1: ETL Pipeline
│   ├── README.md          # ETL 3 workflows guide
│   ├── local_dev_etl.py   # Local ETL testing
│   └── *.py               # ETL notebooks
├── notebooks/             # Phase 2: Agent workflows
│   ├── README.md          # Agent test & deploy guide
│   ├── deploy_agent.py    # Deployment script
│   └── test_agent_databricks.py  # Testing notebook
├── src/multi_agent/       # Shared agent code
│   ├── README.md          # Code structure guide
│   ├── agents/            # Individual agents
│   ├── core/              # Core infrastructure
│   ├── tools/             # Agent tools
│   └── utils/             # Utilities
├── tests/                 # Test suite
│   ├── README.md          # Testing guide
│   ├── unit/              # Unit tests
│   ├── integration/       # Integration tests
│   └── e2e/               # End-to-end tests
├── docs/                  # Documentation
│   ├── architecture/      # Architecture diagrams
│   ├── LOCAL_DEVELOPMENT.md
│   ├── DEPLOYMENT.md
│   └── CONFIGURATION.md
├── config/                # Configuration files
├── dev_config.yaml        # Databricks dev config
├── prod_config.yaml       # Databricks prod config
├── config.py              # Local dev config loader
└── .env.example           # Local dev template
```

## 📚 Documentation

### Essential Guides
- [**ETL Pipeline Guide**](etl/README.md) - Run ETL first (3 workflows)
- [**Local Development**](docs/LOCAL_DEVELOPMENT.md) - Complete setup for peers
- [**Databricks Testing**](notebooks/README.md) - Test before deploying
- [**Deployment Guide**](docs/DEPLOYMENT.md) - Deploy to Model Serving
- [**Configuration**](docs/CONFIGURATION.md) - Three config systems explained

### Reference
- [**Architecture**](docs/ARCHITECTURE.md) - System design
- [**API Reference**](docs/API.md) - Agent APIs
- [**Contributing**](CONTRIBUTING.md) - How to contribute

## 🔧 Configuration

This repository supports three configuration systems:

| Config System | Used By | Purpose |
|--------------|---------|---------|
| `dev_config.yaml` | Databricks testing | Development environment config |
| `prod_config.yaml` | Databricks deployment | Production environment config |
| `config.py` + `.env` | Local development | Local dev configuration |

All three use the **same agent code** from `src/multi_agent/`

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for details.

## 🧪 Testing

```bash
# Run all tests
pytest

# Run specific test suites
pytest tests/unit/              # Fast unit tests
pytest tests/integration/        # Integration tests  
pytest tests/e2e/               # End-to-end tests

# Run with coverage
pytest --cov=src.multi_agent tests/
```

See [tests/README.md](tests/README.md) for complete testing guide.

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Development workflow
- Code style guidelines
- Pull request process
- Community guidelines

## 📄 License

This project is licensed under the Apache License 2.0 - see [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

Built with:
- [LangGraph](https://github.com/langchain-ai/langgraph) - Agent orchestration
- [Databricks](https://databricks.com/) - Platform and Genie
- [MLflow](https://mlflow.org/) - Model deployment

---

**Ready to get started?** Begin with the [ETL Pipeline Guide](etl/README.md) to prepare your data! 🚀
