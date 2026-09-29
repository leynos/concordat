.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	typos-config-builder gate --scope all
