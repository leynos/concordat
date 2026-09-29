ifeq ($(CI),true)
BUILDER_VERSION = v0.1.3
else
BUILDER_VERSION = v0.1.4
endif

.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@$(BUILDER_VERSION)" typos-config-builder gate
