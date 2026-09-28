CARGO ?= cargo
RUST_FLAGS ?=
RUST_FLAGS := -D warnings $(RUST_FLAGS)

test:
	RUSTFLAGS="$(RUST_FLAGS)" $(CARGO) test --workspace
