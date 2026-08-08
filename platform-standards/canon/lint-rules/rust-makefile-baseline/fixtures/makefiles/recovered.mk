WHITAKER := whitaker

build:
	cargo build --all-targets

test:
	cargo nextest run

lint
	$(WHITAKER) --all
