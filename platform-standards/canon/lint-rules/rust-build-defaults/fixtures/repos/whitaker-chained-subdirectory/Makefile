CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

lint:
	RUSTFLAGS="-D warnings $(STANDARD_RUSTFLAGS)" $(CARGO) clippy --workspace
	cd crates && RUSTFLAGS="" DYLINT_RUSTFLAGS="-D warnings" $(WHITAKER) --all -- --all-targets
