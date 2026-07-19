.PHONY: build test

build:
	cargo build --all-targets

test:
	cargo nextest run
