MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked

.PHONY: fmt fmt-md check-fmt check-fmt-md

fmt: fmt-python
	$(MAKE) fmt-md

fmt-python:
	ruff format

fmt-md:
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT)
	markdownlint-cli2 --fix "**/*.md"

check-fmt: check-fmt-md
	ruff format --check

check-fmt-md:
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT)
