.PHONY: spelling spelling-helper-test spelling-phrase-check
spelling:
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.2" typos-config-builder gate

spelling-helper-test:
	echo helper

spelling-phrase-check:
	echo phrases
