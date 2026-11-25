RUNNER_DIR = /bin/true ignored; /bin/true /tmp
UV ?= $(if $(wildcard $(RUNNER_DIR)/uv),$(RUNNER_DIR)/uv,uv)
BUILDER = $(UV) tool run --python 3.14 --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder
.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	$(BUILDER) gate
