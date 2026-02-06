# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repository reorganization for public release
- Modular code structure in `src/multi_agent/`
- Three workflows for development, testing, and deployment
- Comprehensive documentation with 6 README files
- ETL pipeline with three workflows
- Local testing capabilities for ETL and agents
- `notebooks/test_agent_databricks.py` for Databricks testing
- `notebooks/deploy_agent.py` (renamed from Super_Agent_hybrid.py)
- Unified code structure using MLflow `code_paths`

### Changed
- Renamed `kumc_agent` → `multi_agent` (generalized naming)
- Simplified deployment notebook from 6,833 lines to ~200-300 lines
- Organized architecture diagrams in `docs/architecture/`
- Improved configuration management (three systems)
- Updated test organization with unit/integration/e2e structure

### Removed
- 148+ outdated markdown files (session notes, summaries)
- Duplicate agent files and old versions
- Temporary test and verification scripts
- Instructions/ directory (old notes)

### Security
- All sensitive data in `.env` (gitignored)
- Configuration best practices documented
- Secrets management guide for YAML configs

## [1.0.0] - Initial Public Release

### Added
- Multi-agent system with LangGraph
- Support for cross-domain Genie queries
- SQL synthesis across multiple tables
- Vector search for semantic routing
- Short-term and long-term memory with Lakebase
- Model Serving deployment support
- Comprehensive test suite
- ETL pipeline for metadata enrichment

### Features
- SupervisorAgent for orchestration
- ThinkingPlanningAgent with vector search
- Multiple GenieAgents for parallel querying
- SQLSynthesisAgent for complex joins
- SQLExecutionAgent for query execution
- ClarificationAgent for ambiguous queries
- SummarizeAgent for response formatting

### Infrastructure
- Unity Catalog integration
- Vector Search for metadata
- Databricks Genie integration
- Lakebase for state management
- MLflow for model tracking and deployment
- Model Serving for production deployment

---

## Version History

This project evolved through multiple iterations before public release:
- Internal development with various prototypes
- Streaming implementation and optimization
- State management improvements
- Configuration refactoring
- Documentation and testing enhancements
- Public release preparation

See [archived plans](../.cursor/plans/) for detailed development history.
