WHITAKER ?= whitaker

.PHONY: build test lint

build:
	cargo build --all-targets

test:
	cargo nextest run

lint:
	command -v $(WHITAKER) >/dev/null 2>&1 || exit 0; $(WHITAKER) --all
