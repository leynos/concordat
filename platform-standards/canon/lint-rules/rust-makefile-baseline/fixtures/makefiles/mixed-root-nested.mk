WHITAKER ?= whitaker

.PHONY: build test lint

build:
	cargo build

test:
	cargo test

lint:
	cd rust && $(WHITAKER) --all
