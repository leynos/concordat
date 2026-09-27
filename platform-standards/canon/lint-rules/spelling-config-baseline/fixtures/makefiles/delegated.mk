.PHONY: spelling spelling-gate
spelling: spelling-gate ## Enforce spelling

spelling-gate:
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.2" typos-config-builder gate
