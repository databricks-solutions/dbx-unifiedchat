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
#     make doctor          # check your machine has everything required
#     make setup           # install Python + frontend deps, seed databricks.local.yml
#     make dev-local       # local bootstrap (creates agent_app/.env from databricks.yml)
#     make deploy          # validate + preflight + deploy to dev + start the app
#
# README.md canonical deploy path: agent_app/scripts/deploy.sh
#   Routine code-only:   make app-deploy-dev-run   (no ETL prep)
#   Canonical full:      make app-deploy-dev-full  (--run-job full --start-app)
#   Production:          make app-deploy-prod-full
#   CI:                  TARGET=prod make app-deploy-ci
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
  PYTEST         ?= uv run pytest
else ifdef PIP_AVAILABLE
  INSTALLER      ?= $(PYTHON) -m pip
  INSTALLER_NAME := pip
  PYTEST         ?= pytest
else
  INSTALLER      ?= __missing__
  INSTALLER_NAME := none
  PYTEST         ?= pytest
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
	@if [ ! -f "$(APP_DIR)/.env" ] && [ ! -f .env ]; then \
		printf '%b\n' "$(RED)Error: no .env found.$(RESET)"; \
		printf '  Looked for $(APP_DIR)/.env (canonical local runtime overlay) and ./.env (legacy).\n'; \
		printf '  $(APP_DIR)/.env is created automatically by:\n'; \
		printf '    %bmake dev-local%b   — one-time bootstrap\n' "$(CYAN)" "$(RESET)"; \
		printf '  Or set Databricks auth via %bdatabricks auth login%b and rerun.\n' "$(CYAN)" "$(RESET)"; \
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
	@if [ -f "$(APP_DIR)/.env" ]; then \
		printf '  %b$(APP_DIR)/.env:%b present (canonical local runtime overlay)\n' "$(GREEN)" "$(RESET)"; \
	elif [ -f .env ]; then \
		printf '  %b$(APP_DIR)/.env:%b MISSING (legacy ./.env present — local dev creates $(APP_DIR)/.env on first run)\n' "$(YELLOW)" "$(RESET)"; \
	else \
		printf '  %b$(APP_DIR)/.env:%b MISSING — created by %bmake dev-local%b on first run\n' "$(YELLOW)" "$(RESET)" "$(CYAN)" "$(RESET)"; \
	fi
	@if [ -f "$(APP_DIR)/databricks.local.yml" ]; then \
		printf '  %b$(APP_DIR)/databricks.local.yml:%b present (private overlay)\n' "$(GREEN)" "$(RESET)"; \
	elif [ -f "$(APP_DIR)/databricks.local.yml.example" ]; then \
		printf '  %b$(APP_DIR)/databricks.local.yml:%b not yet copied — run %bmake setup%b\n' "$(YELLOW)" "$(RESET)" "$(CYAN)" "$(RESET)"; \
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
	@if [ ! -f "$(APP_DIR)/databricks.local.yml" ] && [ -f "$(APP_DIR)/databricks.local.yml.example" ]; then \
		cp "$(APP_DIR)/databricks.local.yml.example" "$(APP_DIR)/databricks.local.yml"; \
		printf '%b\n' "$(GREEN)Created $(APP_DIR)/databricks.local.yml from example. Fill in your private values, then mirror them into $(APP_DIR)/databricks.yml.$(RESET)"; \
	fi
	@if [ ! -f "$(APP_DIR)/.env" ]; then \
		printf '%b\n' "$(YELLOW)$(APP_DIR)/.env not present yet — local dev scripts create it on first run.$(RESET)"; \
		printf '  Run %bmake dev-local%b after setup to bootstrap $(APP_DIR)/.env from databricks.yml.\n' "$(CYAN)" "$(RESET)"; \
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
	@printf '  2. Authenticate: %bdatabricks auth login --profile <profile>%b (matches profile in $(APP_DIR)/databricks.yml)\n' "$(CYAN)" "$(RESET)"
	@printf '  3. Bootstrap local runtime: %bmake dev-local%b (creates $(APP_DIR)/.env)\n' "$(CYAN)" "$(RESET)"
	@printf '  4. Iterate: %bmake dev-hot-reload%b — or deploy: %bmake deploy%b\n' "$(CYAN)" "$(RESET)" "$(CYAN)" "$(RESET)"

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
	$(call say,$(CYAN)Running unit tests via $(PYTEST)...$(RESET))
	cd $(APP_DIR) && $(PYTEST) tests/unit -v

.PHONY: python-test-integration
python-test-integration: ## Run integration tests (-m integration; needs Databricks auth)
	$(call check_env_file)
	$(call say,$(CYAN)Running integration tests via $(PYTEST)...$(RESET))
	cd $(APP_DIR) && $(PYTEST) -m integration tests/ -v

.PHONY: python-test
python-test: ## Run all Python tests (unit + integration)
	$(call check_env_file)
	$(call say,$(CYAN)Running all Python tests via $(PYTEST)...$(RESET))
	cd $(APP_DIR) && $(PYTEST) tests/ -v

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
preflight: ## Check workspace resources exist before deploying (TARGET=dev|prod, default dev)
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

.PHONY: dab-destroy-dev
dab-destroy-dev: ## Tear down dev bundle resources (referenced by DEPLOY_CHECKLIST §I)
	$(call check_bundle_yaml)
	$(call say,$(RED)About to destroy DEV bundle resources.$(RESET))
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(APP_DIR) && databricks bundle destroy -t dev

# --- Advanced / raw bundle deploy --------------------------------------------
# These bypass `agent_app/scripts/deploy.sh`, so they SKIP preflight, local
# bootstrap, and shared-infra reconciliation. Prefer the app-deploy-* targets
# below for routine deploys. Kept for advanced users debugging terraform.

.PHONY: dab-deploy-dev-raw
dab-deploy-dev-raw: dab-validate ## [advanced] Raw `bundle deploy -t dev` (skips preflight/shared-infra)
	$(call say,$(YELLOW)Raw bundle deploy — preflight and shared-infra are NOT run.$(RESET))
	cd $(APP_DIR) && databricks bundle deploy -t dev
	$(call say,$(GREEN)Bundle deployed to dev.$(RESET))

.PHONY: dab-deploy-prod-raw
dab-deploy-prod-raw: dab-validate ## [advanced] Raw `bundle deploy -t prod` (skips preflight/shared-infra)
	$(call say,$(YELLOW)Raw bundle deploy to PRODUCTION — preflight and shared-infra are NOT run.$(RESET))
	@read -p "Confirm production deploy? [Y/N] " confirm && [ "$$confirm" = "Y" ] || exit 1
	cd $(APP_DIR) && databricks bundle deploy -t prod
	$(call say,$(GREEN)Bundle deployed to prod.$(RESET))

# ==============================================================================
# APP — Full guided deployment via agent_app/scripts/deploy.sh
#
# All targets here delegate to `agent_app/scripts/deploy.sh`, which runs
# preflight + bundle deploy + shared-infra reconciliation. The README's
# canonical recipe is `--run-job full --start-app` (see *-full targets).
# ==============================================================================

.PHONY: app-deploy-dev
app-deploy-dev: ## Deploy app to dev (validate → preflight → deploy → shared-infra; no app start)
	$(call check_bundle_yaml)
	$(call check_tool,databricks,Databricks CLI,brew tap databricks/tap && brew install databricks,winget install Databricks.DatabricksCLI,curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh)
	$(call say,$(CYAN)Deploying app to dev...$(RESET))
	cd $(APP_DIR) && bash scripts/deploy.sh --target dev

.PHONY: app-deploy-dev-run
app-deploy-dev-run: ## Deploy app to dev and start it (validate → preflight → deploy → shared-infra → start)
	$(call check_bundle_yaml)
	$(call check_tool,databricks,Databricks CLI,brew tap databricks/tap && brew install databricks,winget install Databricks.DatabricksCLI,curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh)
	$(call say,$(CYAN)Deploying app to dev (and starting)...$(RESET))
	cd $(APP_DIR) && bash scripts/deploy.sh --target dev --start-app

.PHONY: app-deploy-dev-full
app-deploy-dev-full: ## Dev: README canonical (deploy + full job graph: ETL prep + validation + start)
	$(call check_bundle_yaml)
	$(call say,$(CYAN)Full dev deploy: bundle + full job graph + start...$(RESET))
	cd $(APP_DIR) && bash scripts/deploy.sh --target dev --run-job full --start-app

.PHONY: app-deploy-prod
app-deploy-prod: ## Deploy app to prod (no app start)
	$(call check_bundle_yaml)
	$(call say,$(YELLOW)About to deploy app to PRODUCTION.$(RESET))
	@read -p "Confirm production deploy? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(APP_DIR) && bash scripts/deploy.sh --target prod

.PHONY: app-deploy-prod-run
app-deploy-prod-run: ## Deploy app to prod and start it
	$(call check_bundle_yaml)
	$(call say,$(YELLOW)About to deploy app to PRODUCTION and start it.$(RESET))
	@read -p "Confirm production deploy? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	cd $(APP_DIR) && bash scripts/deploy.sh --target prod --start-app

.PHONY: app-deploy-prod-full
app-deploy-prod-full: ## Prod: README canonical (deploy + full job graph: ETL prep + validation + start)
	$(call check_bundle_yaml)
	$(call say,$(YELLOW)Full PRODUCTION deploy: bundle + full job graph + start...$(RESET))
	@read -p "Confirm production deploy? [Y/N] " confirm && [ "$$confirm" = "Y" ] || exit 1
	cd $(APP_DIR) && bash scripts/deploy.sh --target prod --run-job full --start-app

.PHONY: app-deploy-ci
app-deploy-ci: ## CI deploy (TARGET=dev|prod, default dev): --sync-workspace --run-job full --ci --skip-bootstrap
	$(call check_bundle_yaml)
	$(call say,$(CYAN)CI deploy to $${TARGET:-dev}...$(RESET))
	cd $(APP_DIR) && bash scripts/deploy.sh --target $${TARGET:-dev} --sync-workspace --run-job full --ci --skip-bootstrap --start-app

.PHONY: etl-prep
etl-prep: ## Run ETL prep job graph only (TARGET=dev|prod, default dev): --run-job prep
	$(call check_bundle_yaml)
	$(call say,$(CYAN)Running ETL prep job graph for target $${TARGET:-dev}...$(RESET))
	cd $(APP_DIR) && bash scripts/deploy.sh --target $${TARGET:-dev} --run-job prep

.PHONY: app-list-jobs
app-list-jobs: ## List bundle jobs (TARGET=dev|prod, default dev)
	$(call check_bundle_yaml)
	cd $(APP_DIR) && bash scripts/deploy.sh --target $${TARGET:-dev} --list-jobs

# ==============================================================================
# LOCAL DEV — wraps agent_app/scripts/dev-local*.sh (LOCAL_DEVELOPMENT.md)
# ==============================================================================

.PHONY: dev-local
dev-local: ## Local one-time bootstrap/build (creates agent_app/.env from databricks.yml, starts backend + UI)
	$(call check_bundle_yaml)
	$(call say,$(CYAN)Running local bootstrap via $(APP_DIR)/scripts/dev-local.sh...$(RESET))
	cd $(APP_DIR) && bash scripts/dev-local.sh

.PHONY: dev-hot-reload
dev-hot-reload: ## Local hot-reload dev loop (use after dev-local)
	$(call check_bundle_yaml)
	$(call say,$(CYAN)Starting hot-reload dev loop...$(RESET))
	cd $(APP_DIR) && bash scripts/dev-local-hot-reload.sh

.PHONY: deploy-notebook
deploy-notebook: ## Print path + instructions for the workspace-native operator notebook
	@printf '%b\n' "$(CYAN)$(BOLD)Workspace-native deploy notebook$(RESET)"
	@printf '  Path: %b$(APP_DIR)/scripts/deploy_notebook.py%b\n' "$(CYAN)" "$(RESET)"
	@printf '\n  How to use:\n'
	@printf '    1. Open the notebook in your Databricks workspace.\n'
	@printf '    2. Set widgets: project_dir, target, deploy_mode, sync_first, run_after.\n'
	@printf '    3. Run the preflight cell.\n'
	@printf '    4. Copy the printed %b./scripts/deploy.sh ...%b command into the Databricks web terminal.\n' "$(CYAN)" "$(RESET)"
	@printf '    5. Re-run the verification cell after the terminal command finishes.\n'
	@printf '\n  Reference: docs/DEPLOYMENT.md (Workspace-Native Operator Path)\n'

# ==============================================================================
# COMPOSITE TARGETS  (the stuff a newbie should actually run)
# ==============================================================================

.PHONY: deploy
deploy: doctor app-deploy-dev-run ## One-command dev path: doctor → validate → preflight → deploy → shared-infra → start
	$(call say,$(GREEN)$(BOLD)Dev app is deployed and starting.$(RESET))
	$(call say,Watch the URL printed above to confirm the app is live.)
	$(call say,For metadata refresh add the ETL prep step: $(CYAN)make etl-prep$(RESET) or use $(CYAN)make app-deploy-dev-full$(RESET).)

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
	@printf '  agent_app/.env: %s\n' "$$([ -f $(APP_DIR)/.env ] && echo present || echo 'MISSING (run make dev-local)')"
	@printf '  ./.env:        %s\n' "$$([ -f .env ] && echo present || echo absent)"
	@printf '  databricks.local.yml: %s\n' "$$([ -f $(APP_DIR)/databricks.local.yml ] && echo present || echo absent)"
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
	@printf '%bQuick start (read this first):%b\n' "$(YELLOW)" "$(RESET)"
	@printf '  1. %bmake doctor%b           — check prerequisites\n' "$(CYAN)" "$(RESET)"
	@printf '  2. %bmake setup%b            — install Python + frontend deps, seed databricks.local.yml\n' "$(CYAN)" "$(RESET)"
	@printf '  3. %bdatabricks auth login --profile <profile>%b — auth against the workspace in $(APP_DIR)/databricks.yml\n' "$(CYAN)" "$(RESET)"
	@printf '  4. %bmake dev-local%b        — local bootstrap (creates $(APP_DIR)/.env)\n' "$(CYAN)" "$(RESET)"
	@printf '  5. %bmake dev-hot-reload%b   — iterate locally, OR\n' "$(CYAN)" "$(RESET)"
	@printf '     %bmake deploy%b           — deploy to dev (DEPLOY_CHECKLIST §F path)\n' "$(CYAN)" "$(RESET)"
	@printf '\n'
	@printf '%bDeployment paths (mirrors README.md / docs/DEPLOYMENT.md):%b\n' "$(YELLOW)" "$(RESET)"
	@printf '  Routine code-only:        %bmake app-deploy-dev-run%b   (no ETL prep)\n' "$(CYAN)" "$(RESET)"
	@printf '  Canonical full deploy:    %bmake app-deploy-dev-full%b  (README recipe: --run-job full)\n' "$(CYAN)" "$(RESET)"
	@printf '  ETL prep only:            %bmake etl-prep%b             (TARGET=dev|prod)\n' "$(CYAN)" "$(RESET)"
	@printf '  Prod canonical:           %bmake app-deploy-prod-full%b\n' "$(CYAN)" "$(RESET)"
	@printf '  CI runner:                %bTARGET=prod make app-deploy-ci%b\n' "$(CYAN)" "$(RESET)"
	@printf '  Workspace operator path:  %bmake deploy-notebook%b\n' "$(CYAN)" "$(RESET)"
	@printf '\n'
	@printf '%bAll targets:%b\n' "$(YELLOW)" "$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}'
	@printf '\n'
