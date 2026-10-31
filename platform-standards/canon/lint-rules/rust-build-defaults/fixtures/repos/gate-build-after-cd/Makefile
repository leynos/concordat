CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

lint:
	cd .. && RUSTFLAGS="-D warnings" $(CARGO) clippy --workspace
	unset CARGO_ENCODED_RUSTFLAGS && RUSTFLAGS="-D warnings" $(CARGO) test --workspace
