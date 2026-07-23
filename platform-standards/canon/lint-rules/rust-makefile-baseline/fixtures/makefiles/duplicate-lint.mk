WHITAKER := whitaker

.PHONY: build test lint

build:
	cargo build --all-targets

test:
	cargo nextest run

lint::
	cargo clippy --all-targets -- -D warnings

lint::
	$(WHITAKER) --all
