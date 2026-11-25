UV ?= uv
BUILDER = "$(UV)" tool run --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder
.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	$(BUILDER) gate
