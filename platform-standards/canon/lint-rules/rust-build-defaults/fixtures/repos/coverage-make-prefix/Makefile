CARGO ?= cargo

coverage:
	CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm \
		RUSTFLAGS="-C link-arg=-fuse-ld=lld" \
		$(CARGO) llvm-cov --lcov --output-path lcov.info
