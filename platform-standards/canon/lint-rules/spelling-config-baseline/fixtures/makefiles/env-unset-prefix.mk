BUILDER = uv tool run --python 3.14 --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder
.PHONY: spelling
spelling: ## Enforce en-GB-oxendict spelling
	env -u GIT_CONFIG_COUNT $(BUILDER) gate
