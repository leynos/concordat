MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked

.PHONY: fmt check-fmt

ifdef FORMAT_MARKDOWN
fmt:
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT)
	markdownlint-cli2 --fix "**/*.md"
endif

check-fmt:
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT)
