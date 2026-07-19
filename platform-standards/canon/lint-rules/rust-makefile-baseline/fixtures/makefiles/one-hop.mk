WHITAKER := whitaker

.PHONY: build test lint lint-clippy lint-whitaker

build:
	cargo build --all-targets

test:
	cargo nextest run

lint: lint-clippy lint-whitaker

lint-clippy:
	cargo clippy --all-targets -- -D warnings

lint-whitaker:
	RUSTFLAGS="-D warnings" $(WHITAKER) --all -- --all-targets --all-features
