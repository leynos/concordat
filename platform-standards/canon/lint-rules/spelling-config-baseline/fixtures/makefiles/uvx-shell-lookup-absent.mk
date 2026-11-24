UV := $(shell command -v uvx 2>/dev/null)
.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	$(UV) --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder gate
