.PHONY: fmt check-fmt

fmt:
	mdtablefix --in-place --git --include-untracked --wrap
	markdownlint-cli2 --fix "**/*.md"

check-fmt:
	mdtablefix --check --git --include-untracked --wrap
