RUFF := uv run ruff
MDFORMAT_ALL ?= $(shell which mdformat-all)

.PHONY: fmt check-fmt

fmt: build $(MDFORMAT_ALL)
	$(RUFF) format
	$(MDFORMAT_ALL)

check-fmt: build
	$(RUFF) format --check
	# mdformat-all doesn't currently do checking
