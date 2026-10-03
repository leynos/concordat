CARGO ?= cargo
STANDARD_THREADS_FLAG ?= -Zthreads=8
STANDARD_MOLD_FLAG ?= -Clink-arg=-fuse-ld=mold
BUILD_HOST_OS := $(shell uname -s)
STANDARD_RUSTFLAGS = $(STANDARD_THREADS_FLAG)$(if $(filter Linux,$(BUILD_HOST_OS)), $(STANDARD_MOLD_FLAG))
GATE_RUSTFLAGS = RUSTFLAGS="$${RUSTFLAGS:+$$RUSTFLAGS }-D warnings $(STANDARD_RUSTFLAGS)"
DEBUG_RUSTFLAGS = RUSTFLAGS="$${RUSTFLAGS:+$$RUSTFLAGS }$(STANDARD_RUSTFLAGS)"

.PHONY: build lint test typecheck
build: target/debug/app

target/debug/app:
	$(DEBUG_RUSTFLAGS) $(CARGO) build --bin app

lint:
	$(GATE_RUSTFLAGS) $(CARGO) clippy --all-targets

test:
	$(GATE_RUSTFLAGS) $(CARGO) nextest run --workspace

typecheck:
	$(GATE_RUSTFLAGS) $(CARGO) check --all-targets
