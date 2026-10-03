DEV_LINKER_FLAGS ?= $(if $(filter Linux,$(shell uname -s)),-C link-arg=-fuse-ld=mold)
DEV_RUST_FLAGS ?= -D warnings -Z threads=8 $(DEV_LINKER_FLAGS)

lint:
	RUSTFLAGS="$(DEV_RUST_FLAGS)" cargo clippy
