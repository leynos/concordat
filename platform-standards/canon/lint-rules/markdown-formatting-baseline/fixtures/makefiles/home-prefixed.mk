MDTABLEFIX ?= $(HOME)/.cargo/bin/mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked

.PHONY: fmt check-fmt

fmt:
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT)
	${HOME}/.bun/bin/markdownlint-cli2 --fix "**/*.md"

check-fmt:
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT)
