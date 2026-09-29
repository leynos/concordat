MDTABLEFIX ?= mdtablefix

.PHONY: fmt check-fmt

fmt:
	$(MDTABLEFIX) --in-place --git --include-untracked --wrap --renumber --breaks --ellipsis --fences
	markdownlint-cli2 --fix "**/*.md"

check-fmt
	$(MDTABLEFIX) --check --git --include-untracked --wrap --renumber --breaks --ellipsis --fences
