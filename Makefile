MDLINT ?= $(shell command -v markdownlint-cli2 2>/dev/null || echo "$(HOME)/.bun/bin/markdownlint-cli2")
NIXIE ?= $(shell which nixie)
MDFORMAT_ALL ?= $(shell which mdformat-all)
VALE ?= $(shell which vale)
TOOLS = $(MDFORMAT_ALL) $(MDLINT) $(NIXIE) uv
VENV_TOOLS = pytest
ACRONYM_SCRIPT ?= scripts/update_acronym_allowlist.py
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
RUFF := $(UV_ENV) uv run ruff
TYPOS_VERSION ?= 1.48.0
TYPOS := uv tool run typos@$(TYPOS_VERSION)
# Pinned so `make typecheck` reports the same diagnostics locally and in
# CI. An unpinned `ty` drifts between machines and hides real findings.
TY_VERSION ?= 0.0.65
TY := uv tool run ty@$(TY_VERSION)
SKYLOS_VERSION = 4.33.2
# Skylos parses source using its own Python AST, so Python 3.14 prevents
# phantom dead-code findings from syntax older tool runtimes cannot parse.
SKYLOS_CLI = $(UV_ENV) uv tool run --python 3.14 --from 'skylos==$(SKYLOS_VERSION)' skylos
SKYLOS = $(SKYLOS_CLI) --config-file pyproject.toml
SKYLOS_PRODUCTION_TARGETS ?= concordat scripts
SKYLOS_EXCLUDE_FOLDERS ?= tests

.PHONY: help all clean build build-release lint fmt check-fmt \
	        markdownlint nixie spelling skylos-allow makeutil test typecheck vale $(TOOLS) \
        $(VENV_TOOLS)

.DEFAULT_GOAL := all

all: build check-fmt test typecheck

.venv: pyproject.toml
	$(UV_ENV) uv venv --clear

build: uv .venv ## Build virtual-env and install deps
	$(UV_ENV) uv sync --group dev

build-release: ## Build artefacts (sdist & wheel)
	python -m build --sdist --wheel

clean: ## Remove build artifacts
	rm -rf build dist *.egg-info \
	  .mypy_cache .pytest_cache .coverage coverage.* \
	  lcov.info htmlcov .venv
	rm -f .typos-oxendict-base.json .typos-oxendict-base.toml
	find . -type d -name '__pycache__' -print0 | xargs -0 -r rm -rf

define ensure_tool
	@command -v $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required, but not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

define ensure_tool_venv
	$(UV_ENV) uv run which $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required in the virtualenv, but is not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

ifneq ($(strip $(TOOLS)),)
$(TOOLS): ## Verify required CLI tools
	$(call ensure_tool,$@)
endif


ifneq ($(strip $(VENV_TOOLS)),)
.PHONY: $(VENV_TOOLS)
$(VENV_TOOLS): ## Verify required CLI tools in venv
	$(call ensure_tool_venv,$@)
endif

fmt: build $(MDFORMAT_ALL) ## Format sources
	$(RUFF) format
	$(RUFF) check --select I --fix
	$(MDFORMAT_ALL)

check-fmt: build ## Verify formatting
	$(RUFF) format --check
	# mdformat-all doesn't currently do checking

lint: build ## Run linters
	$(RUFF) check
	+$(MAKE) spelling
	$(SKYLOS) $(SKYLOS_PRODUCTION_TARGETS) --exclude $(SKYLOS_EXCLUDE_FOLDERS) --category dead_code --gate --format concise --no-upload --no-provenance --no-grep-verify

skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Document one named Skylos exception, not an entry point
	@case "$${SKYLOS_SYMBOL}" in *[![:space:]]*) ;; *) printf "Error: SYMBOL is required for a named whitelist exception\\n" >&2; exit 2;; esac
	@case "$${SKYLOS_REASON}" in *[![:space:]]*) ;; *) printf "Error: REASON is required for a named whitelist exception\\n" >&2; exit 2;; esac
	$(SKYLOS_CLI) whitelist "$${SKYLOS_SYMBOL}" --reason "$${SKYLOS_REASON}"

typecheck: build uv ## Run typechecking
	$(TY) --version
	$(TY) check concordat tests
	PYTHONPATH=scripts $(TY) check scripts

markdownlint: $(MDLINT) ## Lint Markdown files
	$(MDLINT) '**/*.md'
	+$(MAKE) spelling

spelling: ## Enforce en-GB-oxendict spelling in Markdown prose
	@uv run scripts/generate_typos_config.py
	@find . -type f -name '*.md' -not -path './.venv/*' -print0 | \
		xargs -0 -r $(TYPOS) --config typos.toml --force-exclude

nixie: $(NIXIE) ## Validate Mermaid diagrams
	$(NIXIE) --no-sandbox

vale: $(VALE) $(ACRONYM_SCRIPT) ## Check prose
	$(VALE) sync
	uv run --with "git+https://github.com/leynos/concordat-vale.git" $(ACRONYM_SCRIPT)
	$(VALE) --no-global .

makeutil: ## Verify the Makefile parser used by contract tests
	$(call ensure_tool,$@)

test: build uv $(VENV_TOOLS) makeutil ## Run tests
	$(UV_ENV) uv run pytest -v -n auto

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
