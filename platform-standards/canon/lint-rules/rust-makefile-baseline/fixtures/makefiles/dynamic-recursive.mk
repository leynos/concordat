WHITAKER ?= whitaker

.PHONY: build test lint

build:
	cargo build

test:
	cargo test

lint:
	$(MAKE) -C rust lint
