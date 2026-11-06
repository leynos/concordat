CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

export CARGO_ENCODED_RUSTFLAGS = -Zthreads=8

lint:
	env -u CARGO_ENCODED_RUSTFLAGS RUSTFLAGS= $(WHITAKER) --all -- --all-targets
