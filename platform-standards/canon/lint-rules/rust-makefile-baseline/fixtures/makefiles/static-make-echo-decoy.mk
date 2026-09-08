WHITAKER ?= whitaker

.PHONY: build test lint stage

build:
	cargo build --all-targets

test:
	cargo nextest run

lint:
	echo $(MAKE) stage

stage:
	$(WHITAKER) --all
