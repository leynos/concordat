WHITAKER ?= whitaker

.PHONY: build test lint stage

build:
	cargo build --all-targets

test:
	cargo nextest run

lint: stage

stage:
	@:

ifdef CI
stage:
	$(WHITAKER) --all
endif
