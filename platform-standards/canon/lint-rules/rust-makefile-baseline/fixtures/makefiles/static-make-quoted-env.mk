WHITAKER ?= whitaker

.PHONY: build test lint hidden actual

build:
	cargo build

test:
	cargo test

lint:
	NOTE="$(MAKE) hidden" $(MAKE) actual

hidden:
	$(WHITAKER) --all

actual:
	cargo clippy --all-targets -- -D warnings
