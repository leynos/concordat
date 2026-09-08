WHITAKER ?= whitaker

.PHONY: build test lint lint-first lint-second

build:
	cargo build --all-targets

test:
	cargo nextest run

lint:
	$(MAKE) lint-first && $(MAKE) lint-second

lint-first:
	$(WHITAKER) --all

lint-second:
	$(WHITAKER) --all
