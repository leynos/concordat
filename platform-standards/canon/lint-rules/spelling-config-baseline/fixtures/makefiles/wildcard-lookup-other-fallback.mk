UV ?= $(if $(wildcard $(HOME)/.local/bin/uv),$(HOME)/.local/bin/uv,true)
BUILDER = $(UV) tool run --python 3.14 --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder
.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	$(BUILDER) gate
