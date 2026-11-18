# Dependency-family contract pilot assessment

Report-only audit of four pinned consumers against the
`dependency-family-contracts` rule package. No consumer configuration was
written and no scheduled or estate-wide CI was dispatched.

- Date: 2026-11-19.
- Rule package: `dependency-family-contracts` v0.2.0.
- Relationship data:
  `platform-standards/canon/data/dependency-families.yaml`, SHA-256
  `530fd6e2d52a6047fe85cd9a7405aff4d39d59ee1e39c5274e7684628abde1a4`.
- Ledger: `ledger.jsonl`; rendered report: `report.md`.

## Input revisions

Each consumer is fetched at the exact commit below, not at its default-branch
head, so the result can be reproduced after the branches move.

| Consumer       | Revision                                   |
| -------------- | ------------------------------------------ |
| cuprum         | `db4b14dfccc270e1387a3fe9713b14e071f2a062` |
| femtologging   | `33858df8a078a861982afc5fd1dbfd733293dd84` |
| tei-rapporteur | `eaea7024a73aa8cb2d7a35c22d0d92fb13ccd14a` |
| netsuke        | `e52d3ef49429392eebeb484aa4d6622ca79782dd` |

## Results

| Consumer       | Verdict       | Findings                                                                                               |
| -------------- | ------------- | ------------------------------------------------------------------------------------------------------ |
| cuprum         | indeterminate | DF-006: `rust/cuprum-rust/Cargo.toml` declares `pyo3` outside the governed Rust surfaces               |
| femtologging   | noncompliant  | DF-008: no native `uv` entry; DF-006: `maturin-pyo3` and `pytest-pytest-bdd` are unjudgeable           |
| tei-rapporteur | noncompliant  | DF-002: `maturin-pyo3` has no upper bound and no breaking-class `ignore` rule on any member            |
| netsuke        | noncompliant  | DF-008: no native `pip` entry; DF-006: `build_slice_fixture/` and `test_support/` manifests are unread |

## Hypothesis assessment

### H1: broad grouping leaves documented relationships unprotected

- State: **supported (not falsified)**.
- Evidence kind: static rule run over the pinned revisions.
- tei-rapporteur: `maturin-pyo3` is a `tuple` of independently versioned
  packages, and neither member carries an upper bound or an `ignore` rule for
  its breaking update class (DF-002). Its existing grouping does not protect
  the relationship the issue names.
- femtologging and netsuke: the root Python surface is covered only by groups
  for other ecosystems (`github-actions`, `rust-toolchain`, and for
  femtologging a `pip` entry). The native ecosystem is not monitored (DF-008),
  so no group protects it at all.
- cuprum: inconclusive. The native crate's manifest sits outside the governed
  surfaces, so coverage was not judged rather than reported compliant.
- Confidence: high for the observed configurations, limited to these
  revisions. No repository among the four already protected the relationship,
  so no duplicate rule is proposed.

### H2: matching the policy prevents relevant failures

- State: **untested**.
- The static run proves authored coverage, bounds and declaration equality. It
  does not prove that a matched family builds or behaves correctly, and this
  pilot ran no mixed-version build.
- Owning work: `shared-actions#588` for executable canaries; the consumer owns
  application semantics. No "grouping fixes everything" claim is made.

### H3: nested, inherited or aliased dependencies escape discovery

- State: **supported (not falsified)**.
- Evidence kind: static rule run plus generator mutation fixtures.
- cuprum's `rust/cuprum-rust/Cargo.toml` and netsuke's
  `build_slice_fixture/Cargo.toml` and `test_support/Cargo.toml` are real
  nested manifests that declare catalog members (`pyo3`, `ortho_config`,
  `sha2`) but lie outside the repository's declared Rust surfaces. Each
  produced DF-006 instead of a pass, so the escape is safe: missing evidence is
  indeterminate.
- Renamed (`pyo3-serde` to `serde-pyobject`), workspace-inherited, optional
  feature-lane and target-specific edges are exercised by the generator
  fixtures, not by a consumer canary.
- Confidence: high for the discovery boundary; medium for the unresolved
  consumer edges, whose locks were not read because the surfaces were
  undeclared.

## Per-consumer evidence

| Consumer       | Contract kind           | Observed                       | Missing evidence                                  | Owning work                |
| -------------- | ----------------------- | ------------------------------ | ------------------------------------------------- | -------------------------- |
| cuprum         | type identity (PyO3)    | nested manifest, unread        | `language.rust.surfaces` declaration              | cuprum#589, concordat#116  |
| femtologging   | tuple, release family   | no native `uv` monitoring      | native Dependabot entry; tuple bounds             | femtologging#430           |
| tei-rapporteur | tuple (maturin/PyO3)    | no bound or ignore on a member | migration evidence                                | tei-rapporteur#185         |
| netsuke        | type identity, features | two nested manifests, unread   | `language.rust.surfaces`; native `pip` monitoring | netsuke#477, concordat#116 |

All four findings are open, consumer-owned work. The pilot writes nothing to
those repositories.

## Observations only

These were read from linked issues and manifests, not reproduced as experiments
here.

- Cyclopts behaviour boundaries (lading#340, ghillie#181, polythene#124) are
  not a numerical co-version requirement and receive no static check.
- Corbusier's authored action-pin equality (#197–201, tracked in #220) is
  represented by the existing CV-005 reader and reviewed pins; runtime coupling
  remains unresolved.
- MXD#626 separates the submitted dependency graph from the graph actually
  executed; provenance must precede any resolving warm-up.
- Wildside Engine#174 records that bincode 3.0.0 is an unsuitable retirement
  release; no decoder vulnerability is claimed.
- typos-config-builder#71 concerns an archived consumer whose current source
  carries a local exception; no redundant migration is proposed.
- Ghillie#32 and concordat#34 show that a package-name and version comparison
  is insufficient without path and group scope.
- cuprum#593 shows one lock exercised through different compiler, command,
  feature and stage subsets; a green production subset does not certify the
  development graph.
- TEI#190 and TEI#148 separate a source-level API break and an observed first
  blocker from dependency causality; static grouping cannot repair either.
- femtologging#430 advances an exact development pin inside an unchanged
  build-system range; constraint form and effective selection matter, and
  textual inequality alone is not drift.

## What the pilot proves and does not prove

- Proves: the policy reads real pinned trees, refuses to pass when facts are
  missing, and reports the four findings above with paths and a smallest repair.
- Does not prove: runtime compatibility, updater scheduling or PR
  classification, or that a group update would succeed.
- No consumer configuration, workflow or dependency was modified.

## Reproducing

```shell
uv run python -m scripts.parabellum_sweep \
  --estate docs/parabellum/dependency-family-pilot/estate.yaml \
  --ledger docs/parabellum/dependency-family-pilot/ledger.jsonl \
  --rule-package dependency-family-contracts
uv run python -m scripts.parabellum_sweep report \
  --ledger docs/parabellum/dependency-family-pilot/ledger.jsonl \
  --output docs/parabellum/dependency-family-pilot/report.md
```
