UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
BUILDER = env $(UV_ENV) uv tool run --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder
.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	$(BUILDER) gate
