include common.mk
.PHONY: spelling
spelling:
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.2" typos-config-builder gate
