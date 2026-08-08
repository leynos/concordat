# Operation Parabellum baseline report

Generated from `docs/parabellum/ledger.jsonl` by
`python -m scripts.parabellum_sweep report`. Do not edit by hand.

Rule package: `rust-makefile-baseline` v0.2.0; makeutil `29fc5a1634ff`.

## Summary

- noncompliant: 1
- compliant: 1
- excluded: 1

Findings by rule:

- QG-001: 1

## Repositories

Table 1: Latest verdict and findings per estate repository.

| Repository | Verdict | Commit | Findings |
| ---------- | ------- | ------ | -------- |
| leynos/alpha | compliant | aaaaaaaaaaaa | none |
| leynos/beta | noncompliant | bbbbbbbbbbbb | QG-001 (noncompliant) soft-skipped lint gate |
| leynos/gauss | excluded |  | test-framework migration in flight |
