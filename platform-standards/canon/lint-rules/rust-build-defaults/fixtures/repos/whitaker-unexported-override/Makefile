CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

CARGO_PROFILE_DEV_CODEGEN_BACKEND := llvm

lint:
	RUSTFLAGS="-D warnings $(STANDARD_RUSTFLAGS)" $(CARGO) clippy --workspace
	RUSTFLAGS= $(WHITAKER) --all -- --all-targets
