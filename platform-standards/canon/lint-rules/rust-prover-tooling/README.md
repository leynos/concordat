# rust-prover-tooling

Audits how a repository that uses Kani or Verus obtains its provers. The audit
is read-only. It evaluates a `policy-input/rust-prover-tooling` envelope built
by `concordat artefact rule run`; it never installs or runs a prover.

## Why

Each repository once grew its own way to install a prover: a Make target, a
workflow step, a Python script that downloads a release. They drifted apart,
and each carried its own cache and checksum logic. The estate now has one tool,
`rust-prover-tools`, and one CI action that installs and caches its provers,
`leynos/shared-actions/.github/actions/install-prover-tools`. The rule holds a
repository to using them.

## What counts as using a prover

A repository is audited when it has a `tools/kani/VERSION` or
`tools/verus/VERSION` pin, names a `rust-prover-tools` revision, or has a
Makefile recipe, workflow step or script that installs or runs a prover. Proof
code that nothing installs or runs (a repository with no prover job in CI) is
not audited: adding the job would cost CI minutes the repository has not chosen
to spend.

## Installer or runner

The facts come from parsers, never a text search. Shell and recipe text is
split into simple commands with `shlex`, and each command's argument vector is
classified:

- an **installer** is `<tool> install` (for `kani`, `verus` or `z3`) under any
  wrapper, `cargo install` or `cargo binstall` of `kani-verifier`, or
  `cargo kani setup`;
- a **runner** is the `verus` binary, `cargo kani`, or
  `prover-tools <tool> run|exec|check-version`.

A step that only runs proofs on an installed toolchain is therefore compliant,
and `printf 'prover-tools verus install'` is not an installer. A workflow step
that runs `make X` is an installer when `X`, or any prerequisite of `X` in the
pinned `makeutil` report, runs an installer: that is how `make verus` depending
on `verus-install` reads, and how a run-only `make verus` does not. A Make
variable reference is not expanded, so a recipe that hides the tool name inside
a variable is not seen.

## Checks

- **PT-001** (error): every `rust-prover-tools` revision the checkout names
  (`tools/rust-prover-tools/REF`, a literal `rust-prover-tools@<sha>` in a
  Makefile variable or a workflow command) is the same commit, and equals the
  `prover_tools_ref` parameter. No revision at all is also noncompliant.
  Bumping the estate's pin is a change to the parameter's default.
- **PT-002** (error): no workflow step installs a prover itself: neither a `run`
  command nor a `make` target whose prerequisites install. Install through the
  shared action.
- **PT-003** (error): every use of `install-prover-tools` is pinned by a full
  40-digit commit SHA. Not waivable.
- **PT-004** (error): no local installer script. A file under `scripts/`,
  `.github/scripts/` or `tools/` whose name pairs `install`, `setup` or
  `bootstrap` with `kani`, `verus` or `z3`; a shell script containing an
  installer command; or a Python script holding a string constant that begins
  with the Kani or Verus release URL (read from the `ast`, so a comment does
  not count). Scripts under `tests/`, `test/` and `fixtures/` directories are
  not read.
- **PT-005** (error): no integration tests of prover installation, found by
  file name only: a file under `tests/` whose name pairs `install` with `kani`,
  `verus`, `z3` or `prover`. The installer is owned and tested in
  `rust-prover-tools`. Not waivable.
- **EX-001** (error): a recorded exception is not in force (see below).
- **EN-001** (error, indeterminate): the envelope is not a schema-1
  `policy-input/rust-prover-tooling` document; a workflow could not be decoded;
  a workflow runs `make` and the Makefile could not be parsed; or a file the
  rule reads could not be read.

## Exceptions

A repository that stays on another revision on purpose records the exception as
data in `.concordat`, as `rust-build-defaults` does for BD-010:

```yaml
language:
  rust:
    prover_tools:
      exceptions:
        - clauses: [PT-001, PT-002]
          pin: 9c4a27a8
          record: docs/adrs/adr-0011-keep-rust-prover-tools-at-9c4a27a8.md
          rationale: The Kani provenance records name 9c4a27a8.
          review_trigger: Revisit at the Kani evidence recapture.
```

An exception waives PT-001, PT-002 and PT-004 (the `waivable_clauses`
parameter) only while it is in force: every revision the checkout names starts
with `pin` (at least eight digits), the `record` is a file in the checkout, the
rationale and review trigger are not blank, and every clause is waivable.
Moving the pin therefore reopens the question rather than leaving a stale
waiver. An exception that is not in force waives nothing and is reported as
EX-001.

## What the rule declines to judge

Only root workflows (`.github/workflows/*.yml`) are read; composite actions are
not. A Make recipe that reaches an installer only through a variable is not
seen. Whether a repository's proofs are sound is not asked.
