build: target/release/app

target/release/app:
	RUSTFLAGS="" cargo build --release

test: coverage

coverage:
	CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm RUSTFLAGS="-D warnings" cargo llvm-cov

bench:
	RUSTFLAGS="-D warnings" cargo bench
