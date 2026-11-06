CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

lint:
	RUSTFLAGS="-D warnings $(STANDARD_RUSTFLAGS)" $(CARGO) clippy --workspace
	env -u CARGO_ENCODED_RUSTFLAGS RUSTFLAGS="-D warnings" $(CARGO) clippy --workspace
