MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked
MDLINT ?= markdownlint-cli2

.PHONY: fmt check-fmt

fmt:
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT) || true; $(MDTABLEFIX) --version
	$(MDLINT) --fix "**/*.md" || true; $(MDLINT) --version

check-fmt:
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT) || true; $(MDTABLEFIX) --version
