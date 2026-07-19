# Operation Parabellum baseline report

Generated from `docs/parabellum/ledger.jsonl` by
`python -m scripts.parabellum_sweep report`. Do not edit by hand.

Rule package: `rust-makefile-baseline` v0.2.0; makeutil `29fc5a1634ff`.

## Summary

- noncompliant: 5
- indeterminate: 8
- compliant: 39

Findings by rule:

- AP-001: 6
- FP-003: 2
- QG-001: 6

## Repositories

| Repository                   | Verdict       | Commit       | Findings                                                                                                                                                 |
| ---------------------------- | ------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| leynos/actix-v2a             | noncompliant  | 12948c9dbb59 | QG-001 (noncompliant) no recipe invokes the "WHITAKER" lint gate                                                                                         |
| leynos/agent-template-rust   | indeterminate | cc4f851a457e | AP-001 (indeterminate) root Cargo.toml is absent; the repository is not provably a Rust project                                                          |
| leynos/agentland             | compliant     | 7fb11f881656 | none                                                                                                                                                     |
| leynos/axinite               | noncompliant  | df4667cfb7ce | FP-003 (noncompliant) required Make target "build" is absent                                                                                             |
| leynos/catnap                | compliant     | a0a305bb855a | none                                                                                                                                                     |
| leynos/chutoro               | compliant     | ab517b16f31b | none                                                                                                                                                     |
| leynos/comenq                | compliant     | 9a03a760687b | none                                                                                                                                                     |
| leynos/corbusier             | compliant     | 6ba4d2c812b5 | none                                                                                                                                                     |
| leynos/cuprum                | indeterminate | ba5e4ab7b9e2 | AP-001 (indeterminate) root Cargo.toml is absent; the repository is not provably a Rust project                                                          |
| leynos/dbar                  | compliant     | 81e4b3df5b78 | none                                                                                                                                                     |
| leynos/ddlint                | compliant     | 5960c362c60b | none                                                                                                                                                     |
| leynos/dear-diary            | compliant     | 2071aa05f8e1 | none                                                                                                                                                     |
| leynos/diesel-cte-ext        | compliant     | 310081f01879 | none                                                                                                                                                     |
| leynos/evert                 | compliant     | e6dfe2cd63fd | none                                                                                                                                                     |
| leynos/femtologging          | indeterminate | 3e3fd54e7559 | AP-001 (indeterminate) root Cargo.toml is absent; the repository is not provably a Rust project                                                          |
| leynos/fingermouse           | compliant     | 033730a1a7af | none                                                                                                                                                     |
| leynos/frankie               | compliant     | b7344707256a | none                                                                                                                                                     |
| leynos/gauss                 | compliant     | e4d9ccf2b1ea | none                                                                                                                                                     |
| leynos/jmap-wasm             | compliant     | b114cba34059 | none                                                                                                                                                     |
| leynos/lag-complexity        | compliant     | d6835944ef5b | none                                                                                                                                                     |
| leynos/lille                 | noncompliant  | e6e8bc21ebff | QG-001 (noncompliant) no recipe invokes the "WHITAKER" lint gate                                                                                         |
| leynos/limela                | compliant     | 4a9750799f04 | none                                                                                                                                                     |
| leynos/mapsplice             | compliant     | 25eb584b9671 | none                                                                                                                                                     |
| leynos/mdast-check           | compliant     | 61300d4b67b0 | none                                                                                                                                                     |
| leynos/monotony              | compliant     | 15a260b20ffc | none                                                                                                                                                     |
| leynos/mpsc-log              | compliant     | f7d9bb30c80d | none                                                                                                                                                     |
| leynos/mriya                 | compliant     | b5bb6ed8cc55 | none                                                                                                                                                     |
| leynos/msgspec-crockford     | indeterminate | b2bd79a44199 | AP-001 (indeterminate) root Cargo.toml is absent; the repository is not provably a Rust project                                                          |
| leynos/mxd                   | compliant     | 58e035f5fc53 | none                                                                                                                                                     |
| leynos/netsuke               | compliant     | 5fdefe7a0d96 | none                                                                                                                                                     |
| leynos/ortho-config          | compliant     | ba16dd7e036f | none                                                                                                                                                     |
| leynos/pg-embed-setup-unpriv | indeterminate | 4f3d289e9318 | QG-001 (indeterminate) Makefile parse was recovered from syntax errors; facts may be incomplete                                                          |
| leynos/podbot                | compliant     | a337fc5f2b85 | none                                                                                                                                                     |
| leynos/prosidy-darn          | indeterminate | 591972c77867 | AP-001 (indeterminate) root Cargo.toml is absent; the repository is not provably a Rust project                                                          |
| leynos/rentaneko             | compliant     | 9ee7110551d2 | none                                                                                                                                                     |
| leynos/repovec-appliance     | noncompliant  | f506390de0a3 | QG-001 (noncompliant) lint-path recipe soft-skips the gate ("command -v")                                                                                |
| leynos/rstest-bdd            | compliant     | 737e48c0c26f | none                                                                                                                                                     |
| leynos/rstest-xfail          | compliant     | 9f96d0d4bd8f | none                                                                                                                                                     |
| leynos/rustxt                | compliant     | 3e3a6e8329fd | none                                                                                                                                                     |
| leynos/shared-actions        | indeterminate | e0d9b652b137 | AP-001 (indeterminate) root Cargo.toml is absent; the repository is not provably a Rust project                                                          |
| leynos/skyjoust              | compliant     | dc5cc1740e27 | none                                                                                                                                                     |
| leynos/spycatcher-harness    | compliant     | 023493fdbc5c | none                                                                                                                                                     |
| leynos/statelet              | compliant     | 66596594e706 | none                                                                                                                                                     |
| leynos/stilyagi              | compliant     | c8319d8d6b38 | none                                                                                                                                                     |
| leynos/tei-rapporteur        | compliant     | fd84e6c133bd | none                                                                                                                                                     |
| leynos/weaver                | compliant     | 10f0da4e602c | none                                                                                                                                                     |
| leynos/whitaker              | indeterminate | 692f654e0cef | QG-001 (indeterminate) the lint target does not reach the gate within one prerequisite hop                                                               |
| leynos/wildside              | noncompliant  | a9bfbea8231d | FP-003 (noncompliant) required Make target "build" is absent; QG-001 (indeterminate) the lint target does not reach the gate within one prerequisite hop |
| leynos/wildside-engine       | compliant     | e6a58454960e | none                                                                                                                                                     |
| leynos/wireframe             | compliant     | d8664d29c41e | none                                                                                                                                                     |
| leynos/ytmusic-wasm          | compliant     | aa4b43b7e852 | none                                                                                                                                                     |
| leynos/zamburak              | compliant     | b7075b7ef4c9 | none                                                                                                                                                     |
