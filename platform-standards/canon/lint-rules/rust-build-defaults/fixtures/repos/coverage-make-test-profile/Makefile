coverage-dev-only:
	CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm cargo llvm-cov --lcov

coverage-both:
	CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm CARGO_PROFILE_TEST_CODEGEN_BACKEND=llvm cargo llvm-cov --lcov
