.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	uvx --python 3.14 --from "git+https://github.com/leynos/typos-config-builder.git@main" typos-config-builder gate
