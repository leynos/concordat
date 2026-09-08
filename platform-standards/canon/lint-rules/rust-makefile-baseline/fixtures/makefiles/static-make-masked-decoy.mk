WHITAKER ?= whitaker

.PHONY: build test lint stage

build:
	cargo build --all-targets

test:
	cargo nextest run

lint:
	$(MAKE) stage || true

stage:
	$(WHITAKER) --all
