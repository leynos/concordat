.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.2" typos-config-builder gate --scope all
