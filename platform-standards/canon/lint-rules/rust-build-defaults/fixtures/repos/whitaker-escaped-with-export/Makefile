CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

export RUSTFLAGS = -Dwarnings

lint:
	RUSTFLAGS=-Zthreads=8 NOTE=x\ X=y $(WHITAKER) --all -- --all-targets
