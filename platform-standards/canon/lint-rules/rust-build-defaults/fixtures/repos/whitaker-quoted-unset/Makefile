CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

export RUSTFLAGS = -Zthreads=8

lint:
	RUSTFLAGS="-D warnings $(STANDARD_RUSTFLAGS)" $(CARGO) clippy --workspace
	NOTE=" -u RUSTFLAGS " $(WHITAKER) --all -- --all-targets
