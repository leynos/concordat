LOCAL_TOOL_PATH := $(HOME)/.local/bin
ifeq ($(OS),Windows_NT)
LOCAL_TOOL_ENV =
else
LOCAL_TOOL_ENV = PATH="$(LOCAL_TOOL_PATH)"
endif
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
UV_RUN_ENV = $(LOCAL_TOOL_ENV) $(UV_ENV)
TYPOS_CONFIG_BUILDER_VERSION ?= v0.1.3
TYPOS_CONFIG_BUILDER = $(UV_RUN_ENV) uv tool run --python 3.14 --from \
  "git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_VERSION)" \
  typos-config-builder

.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	$(TYPOS_CONFIG_BUILDER) gate --repository . --scope all
