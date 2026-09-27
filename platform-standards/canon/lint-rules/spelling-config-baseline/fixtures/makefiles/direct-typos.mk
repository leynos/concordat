.PHONY: spelling
spelling: ## Enforce spelling
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.2" typos-config-builder gate
	git ls-files -z '*.md' | xargs -0 -r uv tool run typos@1.48.0 --config typos.toml
