.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	uvx --python $(CI_PYTHON) --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder gate
