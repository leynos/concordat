WHITAKER := whitaker

.PHONY: build test lint

build:
	cargo build --all-targets

test:
	cargo nextest run

lint:
	-$(WHITAKER) --all
	$(WHITAKER) check || true
