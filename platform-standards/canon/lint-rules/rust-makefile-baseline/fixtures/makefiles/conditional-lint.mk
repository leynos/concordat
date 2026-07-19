WHITAKER := whitaker

.PHONY: build test lint

build:
	cargo build --all-targets

test:
	cargo nextest run

ifdef CI
lint:
	$(WHITAKER) --all
endif
