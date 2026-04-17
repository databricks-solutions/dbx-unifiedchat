# ==============================================================================
# DBX-UnifiedChat — Developer Makefile
#
# Usage:  make <target>
# Help:   make help
#
# Patterns:
#   - Self-documenting: add '## comment' after any target for auto-help
#   - Namespaced: python-*, fe-*, dab-*, db-* for component clarity
#   - Guard checks: fail fast with clear messages when tools are missing
#   - Composite targets: setup, check, deploy-dev chain multiple steps
# ==============================================================================

SHELL := $(or $(shell command -v bash 2>/dev/null),/bin/bash)
.DEFAULT_GOAL := help

# ------------------------------------------------------------------------------
# OS Detection
# ------------------------------------------------------------------------------

ifeq ($(OS),Windows_NT)
  DETECTED_OS := Windows
else
  DETECTED_OS := $(shell uname -s)
endif

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

PYTHON       ?= python3
NPM          ?= npm
PROJECT_ROOT := $(shell pwd)
SRC_DIR      := src
TEST_DIR     := tests
FE_DIR       := agent_app/e2e-chatbot-app-next
APP_DIR      := agent_app
VENV_DIR     := .venv

# ------------------------------------------------------------------------------
# Python installer detection: prefer uv > pip
# Override from CLI: make python-install INSTALLER="python3 -m pip"
# ------------------------------------------------------------------------------

UV_AVAILABLE  := $(shell command -v uv 2>/dev/null)
PIP_AVAILABLE := $(shell $(PYTHON) -m pip --version 2>/dev/null)

ifdef UV_AVAILABLE
  INSTALLER      ?= uv pip
  INSTALLER_NAME := uv
else ifdef PIP_AVAILABLE
  INSTALLER      ?= $(PYTHON) -m pip
  INSTALLER_NAME := pip
else
  INSTALLER      ?= __missing__
  INSTALLER_NAME := none
endif

# Python formatting / linting
LINE_LENGTH     := 100
FLAKE8_MAX_LINE := 120

# Diff base for lint-diff (override with: make python-lint-diff BASE=my-branch)
BASE ?= main

# Configurable flags (CI can override)
INSTALL_DEV_REQS   ?= true
INSTALL_FE_DEPS    ?= true
INSTALL_PRE_COMMIT ?= true

# Color codes for output
GREEN  := \033[32m
YELLOW := \033[33m
RED    := \033[31m
CYAN   := \033[36m
RESET  := \033[0m

# ------------------------------------------------------------------------------
# Guard checks
# ------------------------------------------------------------------------------

define check_command
	@if ! command -v $(1) &> /dev/null; then \
		echo -e "$(RED)Error: '$(1)' is not installed.$(RESET)"; \
		echo "  $(2)"; \
		exit 1; \
	fi
endef

define check_env_file
	@if [ ! -f .env ]; then \
		echo -e "$(RED)Error: .env file not found.$(RESET)"; \
		echo "  Run 'make setup' or copy .env.example:"; \
		echo "    cp .env.example .env"; \
		exit 1; \
	fi
endef

define check_fe_deps
	@if [ ! -d "$(FE_DIR)/node_modules" ]; then \
		echo -e "$(RED)Error: Frontend dependencies not installed.$(RESET)"; \
		echo "  Run 'make fe-install' first."; \
		exit 1; \
	fi
endef

# ==============================================================================
# SETUP & ONBOARDING
# ==============================================================================

.PHONY: ensure-installer
ensure-installer: ## Check for uv/pip; offer to install uv if neither is found
	@if [ "$(INSTALLER_NAME)" = "none" ]; then \
		echo -e "$(RED)No Python package installer found (neither uv nor pip).$(RESET)"; \
		echo ""; \
		echo "  uv is the recommended installer (fast, reliable)."; \
		echo ""; \
		read -p "Install uv now? [Y/n] " answer; \
		case "$$answer" in \
			[nN]*) \
				echo -e "$(YELLOW)Aborted. Install uv or pip manually, then re-run.$(RESET)"; \
				echo ""; \
				echo "  macOS / Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh"; \
				echo "  Windows (PowerShell): powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\""; \
				echo "  Homebrew:       brew install uv"; \
				exit 1 ;; \
			*) \
				echo -e "$(CYAN)Installing uv...$(RESET)"; \
				if [ "$(DETECTED_OS)" = "Windows" ]; then \
					echo "Detected Windows — running PowerShell installer..."; \
					powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"; \
				else \
					curl -LsSf https://astral.sh/uv/install.sh | sh; \
				fi; \
				echo ""; \
				echo -e "$(GREEN)uv installed successfully.$(RESET)"; \
				echo ""; \
				echo -e "$(YELLOW)Next step:$(RESET) ensure uv is on your PATH, then re-run your command."; \
				echo ""; \
				if [ "$(DETECTED_OS)" = "Darwin" ] || [ "$(DETECTED_OS)" = "Linux" ]; then \
					echo "  Option 1: Restart your terminal"; \
					echo "  Option 2: Run this in your current shell:"; \
					echo ""; \
					echo "    export PATH=\"\$$HOME/.local/bin:\$$PATH\""; \
				elif [ "$(DETECTED_OS)" = "Windows" ]; then \
					echo "  Restart Git Bash / your terminal so the new PATH takes effect."; \
				fi; \
				echo ""; \
				exit 1 ;; \
		esac; \
	else \
		echo -e "$(GREEN)Using $(INSTALLER_NAME) as Python installer.$(RESET)"; \
	fi

.PHONY: setup
setup: ## Full onboarding: .env, Python deps, frontend deps, pre-commit hooks
	@echo -e "$(CYAN)Setting up development environment...$(RESET)"
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo -e "$(YELLOW)Created .env from .env.example — edit it with your credentials.$(RESET)"; \
	else \
		echo -e "$(GREEN).env already exists, skipping.$(RESET)"; \
	fi
	@$(MAKE) python-install
ifeq ($(INSTALL_FE_DEPS),true)
	@$(MAKE) fe-install
endif
ifeq ($(INSTALL_PRE_COMMIT),true)
	@if command -v pre-commit &> /dev/null; then \
		pre-commit install; \
		echo -e "$(GREEN)Pre-commit hooks installed.$(RESET)"; \
	else \
		echo -e "$(YELLOW)pre-commit not found, skipping hooks. Install with: $(INSTALLER) install pre-commit$(RESET)"; \
	fi
endif
	@echo ""
	@echo -e "$(GREEN)Setup complete.$(RESET) Next steps:"
	@echo "  1. Edit .env with your Databricks credentials"
	@echo "  2. Run 'make python-test-unit' to verify installation"
	@echo "  3. Run 'make help' to see all available targets"

.PHONY: python-install
python-install: ensure-installer ## Install Python package in editable mode with dev dependencies
	$(call check_command,$(PYTHON),Install Python 3.10+: https://www.python.org/downloads/)
	@if [ "$(INSTALLER_NAME)" = "uv" ] && [ ! -d "$(VENV_DIR)" ]; then \
		echo -e "$(CYAN)Creating virtual environment via uv...$(RESET)"; \
		uv venv $(VENV_DIR); \
	fi
	@echo -e "$(CYAN)Installing Python dependencies via $(INSTALLER_NAME)...$(RESET)"
ifeq ($(INSTALL_DEV_REQS),true)
	$(INSTALLER) install -e ".[dev]"
else
	$(INSTALLER) install -e .
endif
	@echo -e "$(GREEN)Python dependencies installed (via $(INSTALLER_NAME)).$(RESET)"

.PHONY: fe-install
fe-install: ## Install frontend (Next.js) dependencies
	$(call check_command,node,Install Node.js 18+: https://nodejs.org/)
	$(call check_command,$(NPM),npm should come with Node.js)
	@echo -e "$(CYAN)Installing frontend dependencies...$(RESET)"
	cd $(FE_DIR) && $(NPM) install
	@echo -e "$(GREEN)Frontend dependencies installed.$(RESET)"

# ==============================================================================
# PYTHON — Lint, Format, Type Check
# ==============================================================================

.PHONY: python-fmt
python-fmt: ## Auto-format Python code (black + isort)
	@echo -e "$(CYAN)Formatting Python code...$(RESET)"
	black $(SRC_DIR)/ $(TEST_DIR)/ --line-length=$(LINE_LENGTH)
	isort $(SRC_DIR)/ $(TEST_DIR)/ --profile=black --line-length=$(LINE_LENGTH)

.PHONY: python-lint
python-lint: ## Run all Python linters (black --check, isort --check, flake8)
	@echo -e "$(CYAN)Linting Python code...$(RESET)"
	black $(SRC_DIR)/ $(TEST_DIR)/ --check --line-length=$(LINE_LENGTH)
	isort $(SRC_DIR)/ $(TEST_DIR)/ --check-only --profile=black --line-length=$(LINE_LENGTH)
	flake8 $(SRC_DIR)/ $(TEST_DIR)/ --max-line-length=$(FLAKE8_MAX_LINE)

.PHONY: python-lint-diff
python-lint-diff: ## Lint only Python files changed vs. BASE branch (default: main)
	@echo -e "$(CYAN)Linting changed Python files (vs $(BASE))...$(RESET)"
	@CHANGED_FILES=$$(git diff --name-only --diff-filter=d $(BASE)... -- '*.py' | tr '\n' ' '); \
	if [ -z "$$CHANGED_FILES" ]; then \
		echo -e "$(GREEN)No Python files changed vs $(BASE).$(RESET)"; \
	else \
		echo "Files: $$CHANGED_FILES"; \
		black $$CHANGED_FILES --check --line-length=$(LINE_LENGTH) || true; \
		flake8 $$CHANGED_FILES --max-line-length=$(FLAKE8_MAX_LINE) || true; \
	fi

.PHONY: python-types
python-types: ## Run mypy type checking
	@echo -e "$(CYAN)Running type checks...$(RESET)"
	mypy $(SRC_DIR)/ --ignore-missing-imports

# ==============================================================================
# PYTHON — Testing
# ==============================================================================

.PHONY: python-test-unit
python-test-unit: ## Run unit tests (fast, no Databricks required)
	@echo -e "$(CYAN)Running unit tests...$(RESET)"
	pytest $(TEST_DIR)/ -m unit -v

.PHONY: python-test-integration
python-test-integration: ## Run integration tests (requires Databricks connection)
	$(call check_env_file)
	@echo -e "$(CYAN)Running integration tests...$(RESET)"
	pytest $(TEST_DIR)/ -m integration -v

.PHONY: python-test-e2e
python-test-e2e: ## Run end-to-end tests (full system)
	$(call check_env_file)
	@echo -e "$(CYAN)Running e2e tests...$(RESET)"
	pytest $(TEST_DIR)/ -m e2e -v

.PHONY: python-test
python-test: ## Run all Python tests
	$(call check_env_file)
	@echo -e "$(CYAN)Running all Python tests...$(RESET)"
	pytest $(TEST_DIR)/ -v

.PHONY: python-coverage
python-coverage: ## Run tests with coverage report
	@echo -e "$(CYAN)Running tests with coverage...$(RESET)"
	pytest $(TEST_DIR)/ -m unit -v --cov=$(SRC_DIR)/multi_agent --cov-report=term-missing

# ==============================================================================
# FRONTEND — Lint, Format, Build, Test
# ==============================================================================

.PHONY: fe-dev
fe-dev: ## Start frontend dev server (Next.js client + server)
	$(call check_fe_deps)
	@echo -e "$(CYAN)Starting frontend dev server...$(RESET)"
	cd $(FE_DIR) && $(NPM) run dev

.PHONY: fe-build
fe-build: ## Build frontend for production
	$(call check_fe_deps)
	@echo -e "$(CYAN)Building frontend...$(RESET)"
	cd $(FE_DIR) && $(NPM) run build

.PHONY: fe-lint
fe-lint: ## Lint frontend code (Biome)
	$(call check_fe_deps)
	@echo -e "$(CYAN)Linting frontend code...$(RESET)"
	cd $(FE_DIR) && $(NPM) run lint

.PHONY: fe-fmt
fe-fmt: ## Format frontend code (Biome)
	$(call check_fe_deps)
	@echo -e "$(CYAN)Formatting frontend code...$(RESET)"
	cd $(FE_DIR) && $(NPM) run format

.PHONY: fe-test
fe-test: ## Run frontend e2e tests (Playwright)
	$(call check_fe_deps)
	@echo -e "$(CYAN)Running frontend tests...$(RESET)"
	cd $(FE_DIR) && $(NPM) test

# ==============================================================================
# DATABASE (Drizzle ORM)
# ==============================================================================

.PHONY: db-migrate
db-migrate: ## Run database migrations (Drizzle)
	$(call check_fe_deps)
	@echo -e "$(CYAN)Running database migrations...$(RESET)"
	cd $(FE_DIR) && $(NPM) run db:migrate

.PHONY: db-generate
db-generate: ## Generate migration files from schema changes
	$(call check_fe_deps)
	cd $(FE_DIR) && $(NPM) run db:generate

.PHONY: db-studio
db-studio: ## Open Drizzle Studio (database browser)
	$(call check_fe_deps)
	cd $(FE_DIR) && $(NPM) run db:studio

.PHONY: db-reset
db-reset: ## Reset database (destructive!)
	$(call check_fe_deps)
	@echo -e "$(RED)Warning: This will reset the database.$(RESET)"
	@read -p "Continue? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(FE_DIR) && $(NPM) run db:reset

# ==============================================================================
# LOCAL DEVELOPMENT
# ==============================================================================

.PHONY: dev
dev: ## Run agent locally with LangGraph dev server
	$(call check_env_file)
	@echo -e "$(CYAN)Starting LangGraph dev server...$(RESET)"
	langgraph dev

.PHONY: dev-query
dev-query: ## Run a test query locally (usage: make dev-query Q="your question")
	$(call check_env_file)
	@if [ -z "$(Q)" ]; then \
		echo -e "$(YELLOW)Usage: make dev-query Q=\"your question here\"$(RESET)"; \
		exit 1; \
	fi
	$(PYTHON) -m src.multi_agent.main --query "$(Q)"

# ==============================================================================
# DAB — Databricks Asset Bundles
# ==============================================================================

.PHONY: dab-validate
dab-validate: ## Validate DAB configuration
	$(call check_command,databricks,Install Databricks CLI: https://docs.databricks.com/dev-tools/cli/install.html)
	@echo -e "$(CYAN)Validating DAB configuration...$(RESET)"
	databricks bundle validate

.PHONY: dab-deploy-dev
dab-deploy-dev: dab-validate ## Deploy agent system to dev (validates first)
	@echo -e "$(CYAN)Deploying to dev...$(RESET)"
	databricks bundle deploy
	@echo -e "$(GREEN)Deployed to dev.$(RESET)"

.PHONY: dab-deploy-prod
dab-deploy-prod: dab-validate ## Deploy agent system to production (validates first)
	@echo -e "$(YELLOW)Deploying to PRODUCTION...$(RESET)"
	@read -p "Confirm production deployment? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	databricks bundle deploy -t prod
	@echo -e "$(GREEN)Deployed to production.$(RESET)"

.PHONY: dab-etl
dab-etl: ## Run the full ETL pipeline (export -> enrich -> index)
	$(call check_command,databricks,Install Databricks CLI: https://docs.databricks.com/dev-tools/cli/install.html)
	@echo -e "$(CYAN)Running ETL pipeline...$(RESET)"
	databricks bundle run etl_pipeline
	@echo -e "$(GREEN)ETL pipeline complete.$(RESET)"

# ==============================================================================
# APP — Databricks App Deployment
# ==============================================================================

.PHONY: app-deploy-dev
app-deploy-dev: ## Deploy Databricks App to dev
	$(call check_command,databricks,Install Databricks CLI: https://docs.databricks.com/dev-tools/cli/install.html)
	@echo -e "$(CYAN)Deploying app to dev...$(RESET)"
	cd $(APP_DIR) && ./scripts/deploy.sh --target dev

.PHONY: app-deploy-dev-run
app-deploy-dev-run: ## Deploy Databricks App to dev and start it
	$(call check_command,databricks,Install Databricks CLI: https://docs.databricks.com/dev-tools/cli/install.html)
	@echo -e "$(CYAN)Deploying app to dev and starting...$(RESET)"
	cd $(APP_DIR) && ./scripts/deploy.sh --target dev --run

.PHONY: app-deploy-prod
app-deploy-prod: ## Deploy Databricks App to production
	$(call check_command,databricks,Install Databricks CLI: https://docs.databricks.com/dev-tools/cli/install.html)
	@echo -e "$(YELLOW)Deploying app to PRODUCTION...$(RESET)"
	@read -p "Confirm production app deployment? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(APP_DIR) && ./scripts/deploy.sh --target prod

.PHONY: app-deploy-prod-run
app-deploy-prod-run: ## Deploy Databricks App to production and start it
	$(call check_command,databricks,Install Databricks CLI: https://docs.databricks.com/dev-tools/cli/install.html)
	@echo -e "$(YELLOW)Deploying app to PRODUCTION and starting...$(RESET)"
	@read -p "Confirm production app deployment? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(APP_DIR) && ./scripts/deploy.sh --target prod --run

# ==============================================================================
# COMPOSITE TARGETS
# ==============================================================================

.PHONY: fmt
fmt: python-fmt fe-fmt ## Format all code (Python + frontend)

.PHONY: lint
lint: python-lint fe-lint ## Lint all code (Python + frontend)

.PHONY: test
test: python-test fe-test ## Run all tests (Python + frontend)

.PHONY: check
check: ## Pre-push check: lint + unit tests (fast, no Databricks needed)
	@echo -e "$(CYAN)Running pre-push checks...$(RESET)"
	@echo ""
	@echo "--- Python lint ---"
	@$(MAKE) python-lint || (echo -e "$(RED)Python lint failed$(RESET)" && exit 1)
	@echo ""
	@echo "--- Python unit tests ---"
	@$(MAKE) python-test-unit || (echo -e "$(RED)Python tests failed$(RESET)" && exit 1)
	@echo ""
	@if [ -d "$(FE_DIR)/node_modules" ]; then \
		echo "--- Frontend lint ---"; \
		$(MAKE) fe-lint || (echo -e "$(RED)Frontend lint failed$(RESET)" && exit 1); \
	else \
		echo -e "$(YELLOW)Skipping frontend lint (node_modules not installed)$(RESET)"; \
	fi
	@echo ""
	@echo -e "$(GREEN)All checks passed.$(RESET)"

.PHONY: check-diff
check-diff: ## Quick check: lint only changed files + unit tests
	@echo -e "$(CYAN)Running quick checks on changed files...$(RESET)"
	@$(MAKE) python-lint-diff
	@$(MAKE) python-test-unit
	@echo -e "$(GREEN)Quick checks passed.$(RESET)"

.PHONY: deploy-dev
deploy-dev: check dab-deploy-dev ## Full dev deploy: check + validate + deploy
	@echo -e "$(GREEN)Dev deployment complete (with checks).$(RESET)"

# ==============================================================================
# UTILITIES
# ==============================================================================

.PHONY: clean
clean: ## Remove build artifacts, caches, and compiled files
	@echo -e "$(CYAN)Cleaning build artifacts...$(RESET)"
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf build/ dist/ .coverage htmlcov/ .langgraph_api/
	@echo -e "$(GREEN)Clean complete.$(RESET)"

.PHONY: clean-all
clean-all: clean ## Deep clean: also remove venv and node_modules
	@echo -e "$(YELLOW)Deep cleaning (includes venv and node_modules)...$(RESET)"
	@read -p "This will remove .venv and node_modules. Continue? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	rm -rf $(VENV_DIR)
	rm -rf $(FE_DIR)/node_modules
	@echo -e "$(GREEN)Deep clean complete. Run 'make setup' to reinstall.$(RESET)"

.PHONY: info
info: ## Show current environment and configuration summary
	@echo -e "$(CYAN)Environment Info$(RESET)"
	@echo "  OS:          $(DETECTED_OS)"
	@echo "  Python:      $$($(PYTHON) --version 2>&1 || echo 'not found')"
	@echo "  Installer:   $(INSTALLER_NAME) ($$(uv --version 2>&1 || $(PYTHON) -m pip --version 2>&1 | head -1 || echo 'not available'))"
	@echo "  uv:          $$(uv --version 2>&1 || echo 'not installed')"
	@echo "  pip:         $$($(PYTHON) -m pip --version 2>&1 | head -1 || echo 'not installed')"
	@echo "  Node:        $$(node --version 2>&1 || echo 'not found')"
	@echo "  npm:         $$($(NPM) --version 2>&1 || echo 'not found')"
	@echo "  Databricks:  $$(databricks --version 2>&1 || echo 'not installed')"
	@echo "  Git branch:  $$(git branch --show-current 2>&1)"
	@echo "  .env:        $$([ -f .env ] && echo 'exists' || echo 'MISSING — run make setup')"
	@echo "  venv:        $$([ -d $(VENV_DIR) ] && echo 'exists' || echo 'not found')"
	@echo "  node_modules:$$([ -d $(FE_DIR)/node_modules ] && echo 'exists' || echo 'not installed')"

# ==============================================================================
# HELP
# ==============================================================================

.PHONY: help
help: ## Show this help message
	@echo ""
	@echo -e "$(CYAN)DBX-UnifiedChat$(RESET) — Developer Makefile"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-22s$(RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo -e "$(YELLOW)Examples:$(RESET)"
	@echo "  make setup              # First-time onboarding"
	@echo "  make check              # Pre-push validation (lint + tests)"
	@echo "  make dev                # Start LangGraph dev server"
	@echo "  make deploy-dev         # Full dev deploy (check + validate + deploy)"
	@echo "  make dev-query Q=\"...\"  # Run a test query locally"
	@echo ""
