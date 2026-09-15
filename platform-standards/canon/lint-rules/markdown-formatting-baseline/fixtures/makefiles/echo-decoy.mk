MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked

.PHONY: fmt check-fmt

fmt:
	@echo "$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT)"
	@echo "markdownlint-cli2 --fix"

check-fmt:
	@echo "$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT)"
