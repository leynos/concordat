WHITAKER := whitaker

.PHONY: build test lint

build:
	cargo build --all-targets

test:
	cargo nextest run

lint:
	command -v whitaker >/dev/null 2>&1 && $(WHITAKER) --all
