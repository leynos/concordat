WHITAKER := whitaker

.PHONY: build test lint stage-one stage-two

build:
	cargo build --all-targets

test:
	cargo nextest run

lint: stage-one

stage-one: stage-two

stage-two:
	$(WHITAKER) --all
