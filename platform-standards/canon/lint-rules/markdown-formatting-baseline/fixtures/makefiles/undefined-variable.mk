.PHONY: fmt check-fmt

fmt:
	$(MARKDOWN_FORMATTER) --in-place --git --include-untracked
	$(MARKDOWN_LINTER) --fix "**/*.md"

check-fmt:
	$(MARKDOWN_FORMATTER) --check --git --include-untracked
