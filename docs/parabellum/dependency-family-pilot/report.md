# Operation Parabellum baseline report

Generated from the campaign ledger by
`python -m scripts.parabellum_sweep report`. Do not edit by hand.

Rule package: `dependency-family-contracts` v0.2.0; makeutil `d33acd3ecb8d`.

## Summary

- noncompliant: 3
- indeterminate: 1

Findings by rule:

- DF-002: 1
- DF-006: 5
- DF-008: 2

## Repositories

Table 1: Latest verdict and findings per estate repository.

| Repository            | Verdict       | Commit       | Findings                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| --------------------- | ------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| leynos/cuprum         | indeterminate | db4b14dfccc2 | DF-006 (indeterminate) dependency facts could not be established: rust/cuprum-rust/Cargo.toml is a Cargo manifest outside the governed Rust surfaces and declares pyo3; declare language.rust.surfaces in .concordat to read it                                                                                                                                                                                                                                                                                                                                                                                                        |
| leynos/femtologging   | noncompliant  | 33858df8a078 | DF-006 (indeterminate) maturin-pyo3 cannot be judged: no uv entry covers /; DF-008 (noncompliant) pyproject.toml is not monitored by its native uv ecosystem (covered by: github-actions, pip, rust-toolchain); add a native uv entry; DF-006 (indeterminate) pytest-pytest-bdd cannot be judged: no uv entry covers /                                                                                                                                                                                                                                                                                                                 |
| leynos/netsuke        | noncompliant  | e52d3ef49429 | DF-006 (indeterminate) dependency facts could not be established: build_slice_fixture/Cargo.toml is a Cargo manifest outside the governed Rust surfaces and declares ortho_config; declare language.rust.surfaces in .concordat to read it; DF-006 (indeterminate) dependency facts could not be established: test_support/Cargo.toml is a Cargo manifest outside the governed Rust surfaces and declares sha2; declare language.rust.surfaces in .concordat to read it; DF-008 (noncompliant) pyproject.toml is not monitored by its native pip ecosystem (covered by: cargo, github-actions, rust-toolchain); add a native pip entry |
| leynos/tei-rapporteur | noncompliant  | eaea7024a73a | DF-002 (noncompliant) maturin-pyo3: maturin, pyo3 are independently versioned and no member has an upper bound or a breaking-class ignore rule across all its declarations; add a documented bound or a reviewed exception; migration evidence: <https://github.com/leynos/shared-actions/issues/588>                                                                                                                                                                                                                                                                                                                                  |
