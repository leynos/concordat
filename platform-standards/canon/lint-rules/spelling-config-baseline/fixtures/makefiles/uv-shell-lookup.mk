UV ?= $(shell command -v uv 2>/dev/null || printf '%s/.local/bin/uv' "$$HOME")
BUILDER = $(UV) tool run --python 3.14 --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder
.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	$(BUILDER) gate
