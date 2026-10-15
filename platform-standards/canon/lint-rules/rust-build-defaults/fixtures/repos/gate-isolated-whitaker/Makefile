CARGO ?= cargo
WHITAKER ?= whitaker
STANDARD_RUSTFLAGS = -Zthreads=8 -Zcodegen-backend=cranelift -Clink-arg=-fuse-ld=mold

lint:
	RUSTFLAGS="-D warnings $(STANDARD_RUSTFLAGS)" $(CARGO) clippy --workspace
	RUSTFLAGS="-D warnings" $(WHITAKER) --all -- --all-targets

test:
	RUSTFLAGS="-D warnings $(STANDARD_RUSTFLAGS)" python scripts/test_runner.py --cargo "$(CARGO)"

isolated-only: export RUSTFLAGS := -D warnings
isolated-only:
	$(WHITAKER) --all
