# Canonical Make include for Concordat repositories.

PYTHON ?= python3
UV ?= uv

.PHONY: canon.bootstrap canon.lint canon.test

canon.bootstrap:
	$(UV) sync --group dev

canon.lint:
	$(UV) run ruff check

canon.test:
	$(UV) run pytest -m "not slow"
