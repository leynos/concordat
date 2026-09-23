include tools.mk

.PHONY: fmt check-fmt

fmt:
	$(MDTABLEFIX) --in-place --git --include-untracked
	markdownlint-cli2 --fix "**/*.md"

check-fmt:
	$(MDTABLEFIX) --check --git --include-untracked
