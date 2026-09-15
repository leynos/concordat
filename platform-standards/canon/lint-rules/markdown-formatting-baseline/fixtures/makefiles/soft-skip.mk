MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked

.PHONY: fmt check-fmt

fmt:
	-$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT)
	markdownlint-cli2 --fix "**/*.md" || true

check-fmt:
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT) || true
