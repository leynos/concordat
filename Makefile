MDLINT ?= $(shell command -v markdownlint-cli2 2>/dev/null || echo "$(HOME)/.bun/bin/markdownlint-cli2")
# `make fmt` and `make check-fmt` call mdtablefix directly. `--git` selects the
# Markdown files Git tracks and `--include-untracked` adds the untracked files
# Git does not ignore, so a new document is formatted before it is staged.
# Both modes need mdtablefix 0.6.0 or later; CI pins the version in
# MDTABLEFIX_VERSION in .github/workflows/ci.yml.
MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked
MDTABLEFIX_RULES = --wrap --renumber --breaks --ellipsis --fences
NIXIE ?= $(shell which nixie)
VALE ?= $(CURDIR)/.vale/tools/vale-bin/vale
VALE_PYTHON ?= 3.13
VALE_VERSION := 3.22.0
VALE_ASSET := vale_3.22.0_Linux_64-bit.tar.gz
VALE_SHA256 := 52f5cd0314a1b7384cac6aa102a68193977312f6ba9c9f3ae001b5deec8e3a10
VALE_RELEASES := https://github.com/vale-cli/vale/releases/download
VALE_ARCHIVE_FORMAT := tar.gz
VALE_ARCHIVE_MEMBER := vale
VALE_ARCHIVE_MEMBER_SIZE := 45954304
VALE_HELPER_REF := 031bad66e27daf137f5c4acd8f0c2d8201c3c726
UV ?= uv
VALE_STYLE_ARCHIVE = $(UV_ENV) $(UV) run --isolated --no-project \
	--python "$(VALE_PYTHON)" --with cyclopts==4.25.2 \
	python -m scripts.vale_style_archive
TOOLS = $(MDLINT) $(NIXIE) uv
VENV_TOOLS = pytest
ACRONYM_SCRIPT ?= scripts/update_acronym_allowlist.py
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
RUFF := $(UV_ENV) uv run ruff
TYPOS_CONFIG_BUILDER_VERSION ?= v0.1.3
TYPOS_CONFIG_BUILDER = uv tool run --from \
	"git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_VERSION)" \
	typos-config-builder
# Keep Pylint independent from the project virtual environment: a pinned
# Pylint runs as a uv tool on managed PyPy 3.12.  The interpreter is pinned to
# its minor version so a new PyPy cannot change the parsed grammar silently.
PYLINT_PYTHON ?= pypy@3.12
PYLINT_VERSION ?= 4.0.9
PYLINT_TARGETS ?= concordat scripts tests
PYLINT = $(UV_ENV) uv tool run --managed-python --python $(PYLINT_PYTHON) --from 'pylint==$(PYLINT_VERSION)' pylint
# Run the df12 plugin in a separate CPython 3.14 process.  Keeping its
# dependency out of the PyPy Pylint tool avoids interpreter and plugin version
# skew.
DF12_PYTHON_LINTS_REF ?= 9c835f35b0f1690597ade799c9c6a30bc5922959
DF12_PYTHON_LINTS = git+https://github.com/leynos/df12-python-lints.git@$(DF12_PYTHON_LINTS_REF)
DF12_PYTHON ?= 3.14
DF12_PYLINT_TARGETS ?= concordat scripts
DF12_PYLINT_MESSAGES = R9101,C9102,R9103,R9104,C9105,C9106,C9107,R9108,R9109,R9110,R9111,R9112,C9112
DF12_PYLINT = $(UV_ENV) uv run --isolated --python $(DF12_PYTHON) --with '$(DF12_PYTHON_LINTS)' pylint \
	--disable=all --load-plugins=df12_python_lints --py-version=3.13 \
	--enable=$(DF12_PYLINT_MESSAGES)
AMBRLEAKS = $(UV_ENV) uv tool run --python $(DF12_PYTHON) \
	--from '$(DF12_PYTHON_LINTS)' ambrleaks
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
	        markdownlint nixie spelling skylos-allow makeutil conftest test typecheck \
	        install-vale install-vale-styles check-vale vale $(TOOLS) \
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

fmt: build ## Format sources
	$(RUFF) format
	$(RUFF) check --select I --fix
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)
	@unset FORCE_COLOR; $(MDLINT) --fix "**/*.md"

check-fmt: build ## Verify formatting
	$(RUFF) format --check
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)

lint: build ## Run linters
	$(RUFF) check
	$(PYLINT) $(PYLINT_TARGETS)
	$(DF12_PYLINT) $(DF12_PYLINT_TARGETS)
	$(AMBRLEAKS) tests
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

spelling: ## Enforce en-GB-oxendict spelling
	$(TYPOS_CONFIG_BUILDER) gate --repository .

nixie: $(NIXIE) ## Validate Mermaid diagrams
	$(NIXIE) --no-sandbox

install-vale: ## Install the pinned Vale binary on Linux x86_64
	@case "$$(uname -s)/$$(uname -m)" in \
	  Linux/x86_64) ;; \
	  *) printf "Error: install-vale supports Linux x86_64 only\n" >&2; exit 2;; \
	esac
	@mkdir -p "$(CURDIR)/.vale/tools"
	@touch "$(CURDIR)/.vale/tools/github_path"
	RUNNER_TEMP="$(CURDIR)/.vale/tools" \
	GITHUB_PATH="$(CURDIR)/.vale/tools/github_path" \
	VALE_VERSION="$(VALE_VERSION)" \
	VALE_ASSET="$(VALE_ASSET)" \
	VALE_SHA256="$(VALE_SHA256)" \
	VALE_RELEASES="$(VALE_RELEASES)" \
	VALE_ARCHIVE_FORMAT="$(VALE_ARCHIVE_FORMAT)" \
	VALE_ARCHIVE_MEMBER="$(VALE_ARCHIVE_MEMBER)" \
	VALE_ARCHIVE_MEMBER_SIZE="$(VALE_ARCHIVE_MEMBER_SIZE)" \
	$(UV_ENV) $(UV) run --isolated --python "$(VALE_PYTHON)" \
	  scripts/install_release_binary.py install vale
	$(VALE_STYLE_ARCHIVE) install

install-vale-styles: ## Install the verified Vale style package
	$(VALE_STYLE_ARCHIVE) install

check-vale: ## Check that the optional Vale binary is installed
	@command -v "$(VALE)" >/dev/null 2>&1 && \
	  test -x "$$(command -v "$(VALE)")" || { \
	    printf "%s\n" "Error: Vale is required for prose inspection; run 'make install-vale' first" >&2; \
	    exit 1; \
	  }
	$(VALE_STYLE_ARCHIVE) check

vale: check-vale ## Run optional legacy Vale prose checks
	"$(VALE)" sync
	$(UV_ENV) $(UV) run --isolated --python "$(VALE_PYTHON)" \
	  --with 'git+https://github.com/leynos/concordat-vale.git@$(VALE_HELPER_REF)' \
	  python "$(ACRONYM_SCRIPT)"
	"$(VALE)" --no-global .

makeutil: ## Verify the Makefile parser used by contract tests
	$(call ensure_tool,$@)

conftest: ## Verify the policy engine the lint-rule suites run under
	$(call ensure_tool,$@)

test: build spelling uv $(VENV_TOOLS) makeutil conftest ## Run tests
	$(UV_ENV) uv run pytest -v -n auto

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
