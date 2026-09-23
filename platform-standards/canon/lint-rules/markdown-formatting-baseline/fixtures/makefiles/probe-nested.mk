MDLINT ?= $(shell command -v markdownlint-cli2 2>/dev/null || echo "$(HOME)/.bun/bin/markdownlint-cli2")
MDTABLEFIX ?= $(shell which mdtablefix 2>/dev/null || echo "$(HOME)/.cargo/bin/mdtablefix")
MDTABLEFIX_SELECT = --git --include-untracked

.PHONY: fmt check-fmt

fmt:
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT)
	@unset FORCE_COLOR; $(MDLINT) --fix "**/*.md"

check-fmt:
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT)
