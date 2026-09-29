MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked
MDTABLEFIX_RULES = --wrap --renumber --breaks --ellipsis --fences

.PHONY: fmt check-fmt

fmt:
	@echo "$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT)"
	@echo "markdownlint-cli2 --fix"

check-fmt:
	@echo "$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT)"
