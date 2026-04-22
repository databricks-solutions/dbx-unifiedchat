# ==============================================================================
# DBX-UnifiedChat — Developer Makefile
#
# Purpose
#   One-command deployment for green users. The real project lives under
#   agent_app/ (Databricks Asset Bundle + Next.js UI + agent server). This
#   Makefile wraps the CLI so a new user does not need to learn DAB, uv,
#   Databricks CLI flags, or our internal script conventions up front.
#
# First time here? Run:
#     make doctor     # check your machine has everything required
#     make setup      # install Python + frontend deps
#     make deploy     # validate + deploy to dev + start the app
#
# Platforms
#   macOS, Linux: works with the default shell.
#   Windows:      requires Git Bash (or WSL). `make` under cmd.exe or
#                 PowerShell will not work because our scripts need bash.
#                 Doctor target detects this and tells you what to install.
# ==============================================================================

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

# ------------------------------------------------------------------------------
# OS detection
# ------------------------------------------------------------------------------
ifeq ($(OS),Windows_NT)
  DETECTED_OS := Windows
else
  DETECTED_OS := $(shell uname -s 2>/dev/null || echo Unknown)
endif

# On Windows, Python binary is usually `python` (not `python3`).
ifeq ($(DETECTED_OS),Windows)
  PYTHON_DEFAULT := python
else
  PYTHON_DEFAULT := python3
endif

# ------------------------------------------------------------------------------
# Configurable paths — these reflect the *actual* repo layout (post-refactor).
# ------------------------------------------------------------------------------
APP_DIR        := agent_app
AGENT_SRC_DIR  := $(APP_DIR)/agent_server
AGENT_TEST_DIR := $(APP_DIR)/tests
FE_DIR         := $(APP_DIR)/e2e-chatbot-app-next
VENV_DIR       := $(APP_DIR)/.venv

PYTHON ?= $(PYTHON_DEFAULT)
NPM    ?= npm

# ------------------------------------------------------------------------------
# Internal package mirror
#
# Databricks corp machines (Jamf-managed) blackhole pypi.org/npmjs.org/etc.
# in /etc/hosts and require package traffic to go through the internal proxy.
# Default both uv and pip to the proxy; overridable if you're on a non-corp
# machine or pointing at a different mirror.
# ------------------------------------------------------------------------------
UV_INDEX_URL  ?= https://pypi-proxy.dev.databricks.com/simple/
PIP_INDEX_URL ?= https://pypi-proxy.dev.databricks.com/simple/
export UV_INDEX_URL PIP_INDEX_URL

# ------------------------------------------------------------------------------
# Python installer detection: prefer uv > pip
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

# Formatting / linting thresholds (unused unless you install the tools)
LINE_LENGTH     := 100
FLAKE8_MAX_LINE := 120

BASE ?= main

INSTALL_FE_DEPS    ?= true
INSTALL_PRE_COMMIT ?= true

# Color codes (use printf '%b' for cross-shell rendering)
GREEN  := \033[32m
YELLOW := \033[33m
RED    := \033[31m
CYAN   := \033[36m
BOLD   := \033[1m
RESET  := \033[0m

define say
	@printf '%b\n' "$(1)"
endef

# ------------------------------------------------------------------------------
# Guard helpers
# ------------------------------------------------------------------------------
# check_tool <cmd> <human-name> <macOS install> <Windows install> <Linux install>
define check_tool
	@if ! command -v $(1) >/dev/null 2>&1; then \
		printf '%b\n' "$(RED)Missing required tool: $(2) ($(1))$(RESET)"; \
		printf '\n'; \
		printf '  %bmacOS:%b   %s\n'   "$(CYAN)" "$(RESET)" "$(3)"; \
		printf '  %bWindows:%b %s\n'   "$(CYAN)" "$(RESET)" "$(4)"; \
		printf '  %bLinux:%b   %s\n\n' "$(CYAN)" "$(RESET)" "$(5)"; \
		exit 1; \
	fi
endef

define check_env_file
	@if [ ! -f .env ]; then \
		printf '%b\n' "$(RED)Error: .env not found at repo root.$(RESET)"; \
		printf '  This file holds your Databricks workspace URL, token, and UC names.\n'; \
		printf '  Ask a teammate for a sample, or see README.md for required variables.\n'; \
		exit 1; \
	fi
endef

define check_fe_deps
	@if [ ! -d "$(FE_DIR)/node_modules" ]; then \
		printf '%b\n' "$(RED)Frontend dependencies not installed.$(RESET)"; \
		printf '  Run: %bmake fe-install%b\n' "$(CYAN)" "$(RESET)"; \
		exit 1; \
	fi
endef

define check_bundle_yaml
	@if [ ! -f "$(APP_DIR)/databricks.yml" ]; then \
		printf '%b\n' "$(RED)Missing $(APP_DIR)/databricks.yml — cannot locate bundle.$(RESET)"; \
		exit 1; \
	fi
endef

# ==============================================================================
# DOCTOR — one-shot prerequisite check
# ==============================================================================

.PHONY: doctor
doctor: ## Check every prerequisite and print OS-specific install hints
	@printf '%b\n' "$(CYAN)$(BOLD)Environment check$(RESET)"
	@printf '  OS:            %s\n' "$(DETECTED_OS)"
	@if [ "$(DETECTED_OS)" = "Windows" ]; then \
		if [ -z "$$MSYSTEM" ] && [ -z "$$WSL_DISTRO_NAME" ]; then \
			printf '%b\n' "$(RED)On Windows, this Makefile must be run from Git Bash, MSYS2, or WSL.$(RESET)"; \
			printf '  Install Git for Windows (includes Git Bash): https://git-scm.com/download/win\n'; \
			printf '  Or install WSL:                             https://learn.microsoft.com/windows/wsl/install\n'; \
			exit 1; \
		fi; \
	fi
	@printf '\n'
	@printf '%b\n' "$(CYAN)$(BOLD)Required tools$(RESET)"
	@$(MAKE) -s _doctor-tool TOOL=$(PYTHON) LABEL="Python 3.11+" \
		MAC="brew install python@3.11" \
		WIN="winget install Python.Python.3.11  (or https://www.python.org/downloads/)" \
		LIN="sudo apt install python3 python3-venv  (or your distro equivalent)"
	@$(MAKE) -s _doctor-tool TOOL=databricks LABEL="Databricks CLI (v0.294+)" \
		MAC="brew tap databricks/tap && brew install databricks" \
		WIN="winget install Databricks.DatabricksCLI" \
		LIN="curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh"
	@$(MAKE) -s _doctor-tool TOOL=node LABEL="Node.js 20+" \
		MAC="brew install node" \
		WIN="winget install OpenJS.NodeJS" \
		LIN="curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install nodejs"
	@$(MAKE) -s _doctor-tool TOOL=$(NPM) LABEL="npm (ships with Node)" \
		MAC="reinstall Node: brew install node" \
		WIN="reinstall Node via winget" \
		LIN="reinstall Node"
	@$(MAKE) -s _doctor-tool TOOL=git LABEL="git" \
		MAC="brew install git" \
		WIN="winget install Git.Git" \
		LIN="sudo apt install git"
	@printf '\n'
	@printf '%b\n' "$(CYAN)$(BOLD)Recommended tools$(RESET)"
	@if command -v uv >/dev/null 2>&1; then \
		printf '  %buv:%b            %s\n' "$(GREEN)" "$(RESET)" "$$(uv --version)"; \
	else \
		printf '  %buv:%b            not installed (pip will be used as fallback)\n' "$(YELLOW)" "$(RESET)"; \
		printf '    Install uv (recommended — 10x faster than pip):\n'; \
		printf '      macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh\n'; \
		printf '      Windows:      powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"\n'; \
	fi
	@printf '\n'
	@printf '%b\n' "$(CYAN)$(BOLD)Project files$(RESET)"
	@if [ -f .env ]; then \
		printf '  %b.env:%b          present\n' "$(GREEN)" "$(RESET)"; \
	else \
		printf '  %b.env:%b          MISSING — required for local dev (see README)\n' "$(YELLOW)" "$(RESET)"; \
	fi
	@if [ -f "$(APP_DIR)/databricks.yml" ]; then \
		printf '  %bDAB bundle:%b    %s\n' "$(GREEN)" "$(RESET)" "$(APP_DIR)/databricks.yml"; \
	else \
		printf '  %bDAB bundle:%b    MISSING at $(APP_DIR)/databricks.yml\n' "$(RED)" "$(RESET)"; \
	fi
	@if [ -f "$(APP_DIR)/pyproject.toml" ]; then \
		printf '  %bpyproject:%b     %s\n' "$(GREEN)" "$(RESET)" "$(APP_DIR)/pyproject.toml"; \
	else \
		printf '  %bpyproject:%b     MISSING at $(APP_DIR)/pyproject.toml\n' "$(RED)" "$(RESET)"; \
	fi
	@printf '\n'
	@printf '%b\n' "$(CYAN)$(BOLD)Databricks authentication$(RESET)"
	@if command -v databricks >/dev/null 2>&1; then \
		if databricks auth describe --output text >/dev/null 2>&1; then \
			printf '  %bauth:%b          OK\n' "$(GREEN)" "$(RESET)"; \
		else \
			printf '  %bauth:%b          not configured\n' "$(YELLOW)" "$(RESET)"; \
			printf '    Run: %bdatabricks auth login --host <workspace-url>%b\n' "$(CYAN)" "$(RESET)"; \
			printf '    Or set a profile matching the one in $(APP_DIR)/databricks.yml (e.g. dbx-unifiedchat-dev)\n'; \
		fi; \
	fi
	@printf '\n'
	@printf '%b\n' "$(GREEN)Doctor check complete.$(RESET)"

# Internal helper: print status of one tool with OS-specific install commands.
.PHONY: _doctor-tool
_doctor-tool:
	@if command -v $(TOOL) >/dev/null 2>&1; then \
		printf '  %b%s:%b %s\n' "$(GREEN)" "$(LABEL)" "$(RESET)" "$$($(TOOL) --version 2>&1 | head -1)"; \
	else \
		printf '  %b%s:%b MISSING\n' "$(RED)" "$(LABEL)" "$(RESET)"; \
		if [ "$(DETECTED_OS)" = "Darwin" ]; then \
			printf '    Install: %s\n' "$(MAC)"; \
		elif [ "$(DETECTED_OS)" = "Windows" ]; then \
			printf '    Install: %s\n' "$(WIN)"; \
		else \
			printf '    Install: %s\n' "$(LIN)"; \
		fi; \
	fi

# ==============================================================================
# SETUP & ONBOARDING
# ==============================================================================

.PHONY: ensure-installer
ensure-installer: ## Verify uv/pip exists; guide user to install uv if not
	@if [ "$(INSTALLER_NAME)" = "none" ]; then \
		printf '%b\n' "$(RED)No Python package installer found (neither uv nor pip).$(RESET)"; \
		printf '\n  Install uv (recommended):\n'; \
		printf '    macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh\n'; \
		printf '    Windows:      powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"\n'; \
		printf '    Homebrew:     brew install uv\n\n'; \
		printf '  Or install pip: %s -m ensurepip --upgrade\n\n' "$(PYTHON)"; \
		exit 1; \
	else \
		printf '%b\n' "$(GREEN)Using $(INSTALLER_NAME) as Python installer.$(RESET)"; \
	fi

.PHONY: setup
setup: ## First-time onboarding: Python deps, frontend deps, pre-commit hooks
	$(call say,$(CYAN)Setting up development environment...$(RESET))
	@if [ ! -f .env ]; then \
		printf '%b\n' "$(YELLOW).env not found at repo root.$(RESET)"; \
		printf '  Copy values from a teammate or see README for required keys.\n'; \
		printf '  Continuing — some targets (make dev, integration tests) will fail without it.\n'; \
	fi
	@$(MAKE) python-install
ifeq ($(INSTALL_FE_DEPS),true)
	@$(MAKE) fe-install
endif
ifeq ($(INSTALL_PRE_COMMIT),true)
	@if command -v pre-commit >/dev/null 2>&1; then \
		pre-commit install || true; \
		printf '%b\n' "$(GREEN)Pre-commit hooks installed.$(RESET)"; \
	else \
		printf '%b\n' "$(YELLOW)pre-commit not found — skipping hooks (optional).$(RESET)"; \
	fi
endif
	@printf '\n%b\n' "$(GREEN)Setup complete.$(RESET)"
	@printf 'Next:\n'
	@printf '  1. Run: %bmake doctor%b — verify your environment\n' "$(CYAN)" "$(RESET)"
	@printf '  2. Ensure .env exists with Databricks credentials\n'
	@printf '  3. Run: %bmake deploy%b — validate + deploy + start app on dev\n' "$(CYAN)" "$(RESET)"

.PHONY: python-install
python-install: ensure-installer ## Install agent_app Python package (editable, with dev deps)
	$(call check_tool,$(PYTHON),Python 3.11+,brew install python@3.11,winget install Python.Python.3.11,sudo apt install python3 python3-venv)
	@if [ ! -f "$(APP_DIR)/pyproject.toml" ]; then \
		printf '%b\n' "$(RED)$(APP_DIR)/pyproject.toml not found.$(RESET)"; \
		exit 1; \
	fi
	@if [ "$(INSTALLER_NAME)" = "uv" ] && [ ! -d "$(VENV_DIR)" ]; then \
		printf '%b\n' "$(CYAN)Creating virtual environment in $(VENV_DIR)...$(RESET)"; \
		cd $(APP_DIR) && uv venv .venv; \
	fi
	$(call say,$(CYAN)Installing Python deps from $(APP_DIR) via $(INSTALLER_NAME)...$(RESET))
	@$(MAKE) -s _check-pypi || exit 1
	@attempt=1; max_attempts=3; ok=0; \
	while [ $$attempt -le $$max_attempts ]; do \
		if [ "$(INSTALLER_NAME)" = "uv" ]; then \
			( cd $(APP_DIR) && uv sync --dev ) && ok=1 && break; \
		else \
			( cd $(APP_DIR) && $(INSTALLER) install -e '.[dev]' ) && ok=1 && break; \
			( cd $(APP_DIR) && $(INSTALLER) install -e . ) && ok=1 && break; \
		fi; \
		printf '%b\n' "$(YELLOW)Install attempt $$attempt failed. Retrying in 3s...$(RESET)"; \
		attempt=$$((attempt+1)); \
		sleep 3; \
	done; \
	if [ "$$ok" != "1" ]; then \
		printf '\n%b\n' "$(RED)Python dependency install failed after $$max_attempts attempts.$(RESET)"; \
		$(MAKE) -s _install-failure-hints; \
		exit 1; \
	fi
	$(call say,$(GREEN)Python dependencies installed.$(RESET))

# Quick reachability check for the configured index — warn fast but don't block.
.PHONY: _check-pypi
_check-pypi:
	@if ! command -v curl >/dev/null 2>&1; then exit 0; fi; \
	if ! curl -fsS --max-time 5 -o /dev/null "$(UV_INDEX_URL)" 2>/dev/null; then \
		printf '%b\n' "$(YELLOW)Warning: index $(UV_INDEX_URL) unreachable in 5s. Attempting anyway (uv/pip may have a cache)...$(RESET)"; \
	fi

# Actionable hints shown when the install fails or the index is unreachable.
.PHONY: _install-failure-hints
_install-failure-hints:
	@printf '\n%bCurrent index:%b %s\n' "$(CYAN)" "$(RESET)" "$(UV_INDEX_URL)"
	@printf '\n%bLikely causes:%b\n' "$(CYAN)" "$(RESET)"
	@printf '  1. Off corp network/VPN — the Databricks pypi proxy requires VPN.\n'
	@printf '  2. Corporate proxy/firewall blocking outbound HTTPS.\n'
	@printf '  3. Jamf /etc/hosts block and no internal mirror configured.\n'
	@printf '  4. Offline or flaky wifi / DNS resolution failure.\n'
	@printf '\n%bTry one of:%b\n' "$(CYAN)" "$(RESET)"
	@printf '  # Check connectivity to the configured index\n'
	@printf '    %bcurl -v %s%b\n' "$(CYAN)" "$(UV_INDEX_URL)" "$(RESET)"
	@printf '\n'
	@printf '  # Reconnect VPN (the Databricks pypi proxy is VPN-only), then retry:\n'
	@printf '    %bmake python-install%b\n' "$(CYAN)" "$(RESET)"
	@printf '\n'
	@printf '  # Override the index (e.g. off-corp with access to public PyPI):\n'
	@printf '    %bUV_INDEX_URL=https://pypi.org/simple/ make python-install%b\n' "$(CYAN)" "$(RESET)"
	@printf '\n'
	@printf '  # If behind a separate HTTP(S) proxy, export before retrying:\n'
	@printf '    %bexport HTTPS_PROXY=http://your-proxy:8080%b\n' "$(CYAN)" "$(RESET)"
	@printf '    %bexport HTTP_PROXY=http://your-proxy:8080%b\n' "$(CYAN)" "$(RESET)"
	@printf '    %bmake python-install%b\n' "$(CYAN)" "$(RESET)"
	@printf '\n'

.PHONY: fe-install
fe-install: ## Install frontend (Next.js) dependencies
	$(call check_tool,node,Node.js 20+,brew install node,winget install OpenJS.NodeJS,sudo apt install nodejs npm)
	$(call check_tool,$(NPM),npm,comes with Node,comes with Node,comes with Node)
	$(call say,$(CYAN)Installing frontend dependencies...$(RESET))
	cd $(FE_DIR) && $(NPM) install
	$(call say,$(GREEN)Frontend dependencies installed.$(RESET))

# ==============================================================================
# LINT / FORMAT / TEST  (operate on agent_app/agent_server + agent_app/tests)
# ==============================================================================

.PHONY: python-fmt
python-fmt: ## Auto-format Python code (black + isort)
	$(call say,$(CYAN)Formatting Python code...$(RESET))
	@command -v black >/dev/null 2>&1 || { printf '%b\n' "$(YELLOW)black not installed — run: $(INSTALLER) install black isort$(RESET)"; exit 0; }
	black $(AGENT_SRC_DIR)/ $(AGENT_TEST_DIR)/ --line-length=$(LINE_LENGTH)
	isort $(AGENT_SRC_DIR)/ $(AGENT_TEST_DIR)/ --profile=black --line-length=$(LINE_LENGTH)

.PHONY: python-lint
python-lint: ## Check Python formatting and lint (black/isort/flake8)
	$(call say,$(CYAN)Linting Python code...$(RESET))
	@command -v black >/dev/null 2>&1 || { printf '%b\n' "$(YELLOW)black not installed — skipping$(RESET)"; exit 0; }
	black $(AGENT_SRC_DIR)/ $(AGENT_TEST_DIR)/ --check --line-length=$(LINE_LENGTH)
	isort $(AGENT_SRC_DIR)/ $(AGENT_TEST_DIR)/ --check-only --profile=black --line-length=$(LINE_LENGTH)
	flake8 $(AGENT_SRC_DIR)/ $(AGENT_TEST_DIR)/ --max-line-length=$(FLAKE8_MAX_LINE)

.PHONY: python-test-unit
python-test-unit: ## Run unit tests (agent_app/tests/unit)
	$(call say,$(CYAN)Running unit tests...$(RESET))
	cd $(APP_DIR) && pytest tests/unit -v

.PHONY: python-test
python-test: ## Run all Python tests
	$(call check_env_file)
	$(call say,$(CYAN)Running all Python tests...$(RESET))
	cd $(APP_DIR) && pytest tests/ -v

# ==============================================================================
# FRONTEND
# ==============================================================================

.PHONY: fe-dev
fe-dev: ## Start frontend dev server (Next.js client + server)
	$(call check_fe_deps)
	$(call say,$(CYAN)Starting frontend dev server...$(RESET))
	cd $(FE_DIR) && $(NPM) run dev

.PHONY: fe-build
fe-build: ## Build frontend for production
	$(call check_fe_deps)
	cd $(FE_DIR) && $(NPM) run build

.PHONY: fe-lint
fe-lint: ## Lint frontend code (Biome)
	$(call check_fe_deps)
	cd $(FE_DIR) && $(NPM) run lint

.PHONY: fe-fmt
fe-fmt: ## Format frontend code (Biome)
	$(call check_fe_deps)
	cd $(FE_DIR) && $(NPM) run format

.PHONY: fe-test
fe-test: ## Run frontend e2e tests (Playwright)
	$(call check_fe_deps)
	cd $(FE_DIR) && $(NPM) test

# ==============================================================================
# DATABASE (Drizzle ORM inside the Next.js app)
# ==============================================================================

.PHONY: db-migrate
db-migrate: ## Run database migrations (Drizzle)
	$(call check_fe_deps)
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
	$(call say,$(RED)Warning: this will reset the database.$(RESET))
	@read -p "Continue? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(FE_DIR) && $(NPM) run db:reset

# ==============================================================================
# DAB — Databricks Asset Bundles  (all run from $(APP_DIR) where databricks.yml lives)
# ==============================================================================

.PHONY: preflight
preflight: ## Check workspace resources exist before deploying (target: dev)
	$(call check_bundle_yaml)
	$(call check_tool,databricks,Databricks CLI,brew tap databricks/tap && brew install databricks,winget install Databricks.DatabricksCLI,curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh)
	cd $(APP_DIR) && uv run --quiet python scripts/preflight.py --target $${TARGET:-dev}

.PHONY: preflight-prod
preflight-prod: ## Check workspace resources exist against the prod target
	$(call check_bundle_yaml)
	cd $(APP_DIR) && uv run --quiet python scripts/preflight.py --target prod

.PHONY: dab-validate
dab-validate: ## Validate the DAB bundle in agent_app/
	$(call check_bundle_yaml)
	$(call check_tool,databricks,Databricks CLI,brew tap databricks/tap && brew install databricks,winget install Databricks.DatabricksCLI,curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh)
	$(call say,$(CYAN)Validating DAB bundle in $(APP_DIR)/...$(RESET))
	cd $(APP_DIR) && databricks bundle validate

.PHONY: dab-deploy-dev
dab-deploy-dev: dab-validate ## Deploy DAB to dev target (validates first)
	$(call say,$(CYAN)Deploying bundle to dev...$(RESET))
	cd $(APP_DIR) && databricks bundle deploy -t dev
	$(call say,$(GREEN)Bundle deployed to dev.$(RESET))

.PHONY: dab-deploy-prod
dab-deploy-prod: dab-validate ## Deploy DAB to prod target (confirms first)
	$(call say,$(YELLOW)You are about to deploy to PRODUCTION.$(RESET))
	@read -p "Confirm production deploy? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(APP_DIR) && databricks bundle deploy -t prod
	$(call say,$(GREEN)Bundle deployed to prod.$(RESET))

.PHONY: dab-destroy-dev
dab-destroy-dev: ## Tear down dev bundle resources
	$(call check_bundle_yaml)
	$(call say,$(RED)About to destroy DEV bundle resources.$(RESET))
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(APP_DIR) && databricks bundle destroy -t dev

# ==============================================================================
# APP — Full guided deployment via agent_app/scripts/deploy.sh
# ==============================================================================

.PHONY: app-deploy-dev
app-deploy-dev: ## Deploy Databricks App to dev (validate + deploy + shared-infra job)
	$(call check_bundle_yaml)
	$(call check_tool,databricks,Databricks CLI,brew tap databricks/tap && brew install databricks,winget install Databricks.DatabricksCLI,curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh)
	$(call say,$(CYAN)Deploying app to dev...$(RESET))
	cd $(APP_DIR) && bash scripts/deploy.sh --target dev

.PHONY: app-deploy-dev-run
app-deploy-dev-run: ## Deploy Databricks App to dev AND start it
	$(call check_bundle_yaml)
	$(call check_tool,databricks,Databricks CLI,brew tap databricks/tap && brew install databricks,winget install Databricks.DatabricksCLI,curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh)
	$(call say,$(CYAN)Deploying app to dev (and starting)...$(RESET))
	cd $(APP_DIR) && bash scripts/deploy.sh --target dev --start-app

.PHONY: app-deploy-dev-full
app-deploy-dev-full: ## Deploy + run full post-deploy job graph + start app (dev)
	$(call check_bundle_yaml)
	$(call say,$(CYAN)Full dev deploy: bundle + full job + start...$(RESET))
	cd $(APP_DIR) && bash scripts/deploy.sh --target dev --run-job full --start-app

.PHONY: app-deploy-prod
app-deploy-prod: ## Deploy Databricks App to prod
	$(call check_bundle_yaml)
	$(call say,$(YELLOW)About to deploy app to PRODUCTION.$(RESET))
	@read -p "Confirm production deploy? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(APP_DIR) && bash scripts/deploy.sh --target prod

.PHONY: app-deploy-prod-run
app-deploy-prod-run: ## Deploy Databricks App to prod AND start it
	$(call check_bundle_yaml)
	$(call say,$(YELLOW)About to deploy app to PRODUCTION and start it.$(RESET))
	@read -p "Confirm production deploy? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(APP_DIR) && bash scripts/deploy.sh --target prod --start-app

.PHONY: app-list-jobs
app-list-jobs: ## List available bundle jobs (aliases + keys)
	$(call check_bundle_yaml)
	cd $(APP_DIR) && bash scripts/deploy.sh --target dev --list-jobs

# ==============================================================================
# COMPOSITE TARGETS  (the stuff a newbie should actually run)
# ==============================================================================

.PHONY: deploy
deploy: doctor app-deploy-dev-run ## The one-command path: doctor → deploy → start app on dev
	$(call say,$(GREEN)$(BOLD)Dev app is deployed and starting.$(RESET))
	$(call say,Watch the URL printed above to confirm the app is live.)

.PHONY: fmt
fmt: python-fmt fe-fmt ## Format all code (Python + frontend)

.PHONY: lint
lint: python-lint fe-lint ## Lint all code (Python + frontend)

.PHONY: check
check: ## Pre-push: Python lint + unit tests (no Databricks needed)
	$(call say,$(CYAN)Running pre-push checks...$(RESET))
	@$(MAKE) -s python-lint || { printf '%b\n' "$(RED)Python lint failed$(RESET)"; exit 1; }
	@$(MAKE) -s python-test-unit || { printf '%b\n' "$(RED)Unit tests failed$(RESET)"; exit 1; }
	$(call say,$(GREEN)All checks passed.$(RESET))

# ==============================================================================
# UTILITIES
# ==============================================================================

.PHONY: clean
clean: ## Remove Python build artifacts and caches
	$(call say,$(CYAN)Cleaning build artifacts...$(RESET))
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .mypy_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf build/ dist/ .coverage htmlcov/
	$(call say,$(GREEN)Clean complete.$(RESET))

.PHONY: clean-all
clean-all: clean ## Deep clean — also removes .venv and frontend node_modules
	$(call say,$(YELLOW)Deep cleaning (venv + node_modules)...$(RESET))
	@read -p "This will remove $(VENV_DIR) and $(FE_DIR)/node_modules. Continue? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	rm -rf $(VENV_DIR) $(FE_DIR)/node_modules
	$(call say,$(GREEN)Deep clean complete. Run 'make setup' to reinstall.$(RESET))

.PHONY: info
info: ## Show environment snapshot
	@printf '%b\n' "$(CYAN)$(BOLD)Environment Info$(RESET)"
	@printf '  OS:           %s\n' "$(DETECTED_OS)"
	@printf '  Shell:        %s\n' "$$SHELL"
	@printf '  Python:       %s\n' "$$($(PYTHON) --version 2>&1 || echo 'not found')"
	@printf '  Installer:    %s\n' "$(INSTALLER_NAME)"
	@printf '  Index URL:    %s\n' "$(UV_INDEX_URL)"
	@printf '  uv:           %s\n' "$$(uv --version 2>&1 || echo 'not installed')"
	@printf '  pip:          %s\n' "$$($(PYTHON) -m pip --version 2>&1 | head -1 || echo 'not installed')"
	@printf '  Node:         %s\n' "$$(node --version 2>&1 || echo 'not installed')"
	@printf '  npm:          %s\n' "$$($(NPM) --version 2>&1 || echo 'not installed')"
	@printf '  Databricks:   %s\n' "$$(databricks --version 2>&1 || echo 'not installed')"
	@printf '  Git branch:   %s\n' "$$(git branch --show-current 2>&1)"
	@printf '  .env:         %s\n' "$$([ -f .env ] && echo present || echo MISSING)"
	@printf '  DAB bundle:   %s\n' "$$([ -f $(APP_DIR)/databricks.yml ] && echo $(APP_DIR)/databricks.yml || echo MISSING)"
	@printf '  venv:         %s\n' "$$([ -d $(VENV_DIR) ] && echo present || echo not found)"
	@printf '  node_modules: %s\n' "$$([ -d $(FE_DIR)/node_modules ] && echo present || echo not installed)"

# ==============================================================================
# HELP
# ==============================================================================

.PHONY: help
help: ## Show this help message
	@printf '\n%b\n' "$(CYAN)$(BOLD)DBX-UnifiedChat$(RESET) — Developer Makefile"
	@printf '\n'
	@printf '%bQuick start (newbies, read this):%b\n' "$(YELLOW)" "$(RESET)"
	@printf '  1. %bmake doctor%b    — check prerequisites (tells you what to install)\n' "$(CYAN)" "$(RESET)"
	@printf '  2. %bmake setup%b     — install Python + frontend deps\n' "$(CYAN)" "$(RESET)"
	@printf '  3. %bmake deploy%b    — deploy app to dev and start it\n' "$(CYAN)" "$(RESET)"
	@printf '\n'
	@printf '%bAll targets:%b\n' "$(YELLOW)" "$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@printf '\n'

