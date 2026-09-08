WHITAKER ?= whitaker

.PHONY: build test lint lint-rust

build:
	cargo build

test:
	cargo test

lint:
	$(MAKE) lint-rust

lint-rust:
	$(WHITAKER) --all
