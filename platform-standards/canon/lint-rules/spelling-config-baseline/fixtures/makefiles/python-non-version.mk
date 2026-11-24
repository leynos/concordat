.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	uvx --python python3 --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder gate
