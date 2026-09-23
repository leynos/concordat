MDTABLEFIX ?= mdtablefix

.PHONY: fmt check-fmt

fmt:
	$(MDTABLEFIX) --in-place --git
	markdownlint-cli2 "**/*.md"

check-fmt:
	$(MDTABLEFIX) --check --git
