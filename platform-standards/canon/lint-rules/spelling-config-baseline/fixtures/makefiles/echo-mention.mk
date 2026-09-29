.PHONY: spelling
spelling: ## Enforce spelling
	@echo "running typos through the builder"
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder gate
