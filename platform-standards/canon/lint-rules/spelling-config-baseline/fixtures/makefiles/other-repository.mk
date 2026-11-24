.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	uvx --python 3.14 --from "git+https://github.com/example/typos-config-builder.git@v0.1.3" typos-config-builder gate
