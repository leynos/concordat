CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Clink-arg=-fuse-ld=mold

lint:
	RUSTFLAGS="-D warnings $(STANDARD_RUSTFLAGS)" $(CARGO) clippy --workspace
	RUSTFLAGS= CARGO_PROFILE_TEST_CODEGEN_BACKEND=llvm $(WHITAKER) --all -- --all-targets
