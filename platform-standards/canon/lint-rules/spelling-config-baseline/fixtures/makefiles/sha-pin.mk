.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@d6da92f02240a79a945c835f69bdd08a888da1d0" typos-config-builder gate
