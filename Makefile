.PHONY: install-dev test-unit test-integration test-e2e lint typecheck clean run-daemon build-example help

PYTHON := python3
PYTEST  := $(PYTHON) -m pytest
PYBOX   := $(PYTHON) -m pybox.cli.main

##@ Development

install-dev: ## Install the package in editable mode with dev dependencies
	pip3 install --break-system-packages -e ".[dev]" || pip3 install -e ".[dev]"

##@ Testing

test-unit: ## Run unit tests (no root required)
	$(PYTEST) tests/unit/ -v --tb=short -q

test-integration: ## Run integration tests (requires root)
	$(PYTEST) tests/integration/ -v --tb=short -q

test-e2e: ## Run end-to-end tests (requires root and network)
	$(PYTEST) tests/e2e/ -v --tb=short -q

test-all: test-unit test-integration ## Run unit + integration tests

##@ Code Quality

lint: ## Run ruff linter
	$(PYTHON) -m ruff check pybox/ tests/

lint-fix: ## Run ruff with auto-fix
	$(PYTHON) -m ruff check --fix pybox/ tests/

typecheck: ## Run mypy strict type checker
	$(PYTHON) -m mypy pybox/

format: ## Format code with ruff
	$(PYTHON) -m ruff format pybox/ tests/

##@ Runtime

run-daemon: ## Start pyboxd in the foreground
	pyboxd

build-example: ## Build the example image from examples/boxfile.toml
	pybox build -f boxfile.toml -t example-app:latest

##@ Cleanup

clean: ## Remove build artefacts and __pycache__ dirs
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete

clean-data: ## WARNING: Remove all PyBox container and image data
	@echo "This will remove /var/lib/pybox — are you sure? [y/N]"
	@read ans; [ "$$ans" = "y" ] && sudo rm -rf /var/lib/pybox || echo "Aborted"

##@ Help

help: ## Show this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} \
		/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 } \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)

.DEFAULT_GOAL := help
