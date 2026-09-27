TYPOS_VERSION ?= 1.48.0
PATHSPEC_VERSION ?= 1.1.1
TYPOS_CONFIG_BUILDER_COMMIT := d6da92f02240a79a945c835f69bdd08a888da1d0
.PHONY: spelling
spelling:
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder gate
