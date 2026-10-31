CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

lint:
	RUSTFLAGS="-D warnings $(STANDARD_RUSTFLAGS)" $(CARGO) clippy --workspace
	cd "$(dir $(CURDIR))" && unset CARGO_ENCODED_RUSTFLAGS && RUSTFLAGS="" DYLINT_RUSTFLAGS="-D warnings" $(WHITAKER) --manifest-path "$(CURDIR)/Cargo.toml" --all -- --all-targets
