ifeq ($(CI),true)
COVERAGE_ENV = CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm
else
COVERAGE_ENV = CARGO_PROFILE_DEV_CODEGEN_BACKEND=cranelift
endif

coverage:
	$(COVERAGE_ENV) cargo llvm-cov --lcov
