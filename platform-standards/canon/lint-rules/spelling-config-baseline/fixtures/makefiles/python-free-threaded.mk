.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	uvx --python cpython-3.14.2-linux-x86_64-gnu --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder gate
