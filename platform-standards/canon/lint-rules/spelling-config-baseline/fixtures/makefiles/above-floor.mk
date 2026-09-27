.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.2.0" typos-config-builder gate
