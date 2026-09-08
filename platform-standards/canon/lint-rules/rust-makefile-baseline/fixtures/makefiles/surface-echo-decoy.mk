WHITAKER ?= pwd

.PHONY: build test lint

build:
	cargo build --manifest-path rust/Cargo.toml

test:
	cargo test --manifest-path rust/Cargo.toml

lint:
	echo cd rust && $(WHITAKER)
