CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

export RUSTFLAGS = -Dwarnings

lint:
	env -u CARGO_ENCODED_RUSTFLAGS -u RUSTFLAGS $(WHITAKER) --all -- --all-targets
