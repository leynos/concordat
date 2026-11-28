# nested-cargo-test-builds

Audits why a repository's tests spend real compilation. The audit is read-only.
It evaluates a `policy-input/nested-cargo-test-builds` envelope built by
`concordat artefact rule run`; it never runs Cargo, and it never reads the
network.

## Why

The other nested-cargo rules judge how a test's nested build behaves: whether
it is offline, whether it builds into a warm target, whether its deadline fits.
None asks whether the test should spend a compile at all. A compile is worth
its cost when the build is the thing under test: a macro's diagnostics, a
`cargo package` verification, a Cargo plugin driven through Cargo. It is not
when the build only exists to feed the test machinery.

leynos/rstest-bdd had such a test. It ran a real `cargo build --frozen` of a
scratch crate, under a 120 s bound, only to show that the evidence machinery
behind a prepared-fixture judgement (a compile counter, a report writer and
reader, a workflow script) copes with a real non-zero count. It tested the test
infrastructure, not rstest-bdd, and it failed a `make test` when the nested
build outran its bound. It was deleted in
<https://github.com/leynos/rstest-bdd/pull/989>, and the user ruled that this
class of test is for concordat to prevent.

The rule ships at `warning` severity. The estate is surveyed first, and
promotion to `error` is the one-line parameter change recorded in
`docs/roadmap.md` (DF12-109).

## The declaration file

A repository with real compiles in its tests lists them in
`.concordat/nested-builds.toml`, one `[[builder]]` table each:

```toml
[[builder]]
path = "crates/x/tests/support/build.rs"   # exact file, relative to the checkout
function = "build_fixture"                 # optional: omit to cover the file
subject = "macro-expansion"                # what the build is the test of
covered_by = "make:prepare-nested-fixtures"
```

A *builder* is the function that owns the spawn, or the file that holds a
trybuild or macrotest suite. Tests that call a builder inherit its declaration,
so the file stays short and does not drift with line numbers.

`subject` comes from a closed vocabulary, because free text would let a
rationale argue its way past the rule:

- allowed: `compiler-diagnostics`, `macro-expansion`, `generated-code`,
  `cargo-behaviour`, `packaging`, `build-script`, `product-cli`,
  `dependency-graph`;
- prohibited: `test-harness`, `evidence-writer`, `timeout-machinery`,
  `preparation-stamp`, `compile-counter`.

`covered_by` names the warm-up that pays for the build, in one of three forms:
`make:<goal>` (a goal the Makefile's `test` target depends on, transitively, or
calls through `make`), `nextest-setup` (a nextest setup script whose rule
selects the builder's binary), or `test:<name>` (a function the checkout
defines).

## Checks

- **NC-011** (severity from the `severity` parameter, `warning` by default): a
  builder in test scope has no declaration, or a declaration matches no builder
  (it is stale). An undeclared builder is reported at the file and line of its
  first spawn.
- **NC-012** (same severity, never exempt): a declaration whose subject is a
  prohibited one, or a function that writes a scratch manifest into a temporary
  directory, builds it, and mentions one of the configured `harness_paths`. The
  second form is reported whatever the repository declares, and both halves
  must be visible: a scratch crate alone, or a harness name alone, is not
  evidence. The fix is to give the harness a synthetic input.
- **NC-013** (same severity): a declaration's `covered_by` does not resolve to a
  Makefile goal that runs before `test`, a nextest setup script that selects
  the binary, or an existing function.
- **EN-001** (same severity, indeterminate): a spawn starts a program or
  subcommand the reader cannot see; a declaration names a subject outside both
  lists, or a warm-up the reader cannot check (an unknown form, or a Makefile
  it cannot parse); the declaration file does not parse; or the envelope
  carries a problem fact.
- **EN-001** (always `error`, indeterminate): the input is not a schema-1
  `policy-input/nested-cargo-test-builds` envelope, or a fact is missing a
  field its finding needs. This says the rule could not run, not that the
  repository is unsafe, so the `severity` parameter cannot turn it off.

## What counts

The reader is the one the other nested-cargo rules use, so test scope, fixture
exclusion and `#[cfg(test)]` handling are theirs.

- **Inventoried:** a test-scope `Command` that compiles (or may), whether it
  names `cargo`, `rustc`, `rustdoc` or `env!("CARGO")`; a shell (`sh -c`, `cmd`,
  `powershell`) whose arguments name `cargo` or `rustc` or cannot be read,
  which is indeterminate; a `trybuild` or `macrotest` reference; and, in a
  whole-file test source, a `cargo_metadata::MetadataCommand` use inside a
  function, which resolves the dependency graph under the shared package-cache
  lock. A command whose program is a variable (`Command::new(binary)`) is not
  inventoried: most such commands run the product binary.
- **Not inventoried:** a version or help probe, `cargo fetch`,
  `cargo package --list`, `--unit-graph`, `assert_cmd`'s `cargo_bin` (it runs a
  binary the outer build already made), and the build inside a nested-build
  fixture package, which is the build under test.
- **What it can decide:** that a spawn exists, which function owns it, whether a
  scratch manifest and a temporary directory sit in that function, and which
  configured harness names it mentions.
- **What it cannot decide:** whether a build is warm (a run-time fact), and
  whether it is really the thing under test (a semantic one, which is why the
  repository declares the subject). A test that only calls a helper in another
  crate produces no fact in its own file. If the helper returns a `Command` and
  its own file has no call site, the reader reports an unreadable builder at
  the helper's definition when that is in test scope (indeterminate); if the
  helper runs cargo itself, the reader sees the spawn and reports it as an
  undeclared builder (NC-011). A spawn a macro generates, and one made through
  `sh -c`, are indeterminate, never guessed. A harness test whose spawn sits in
  a helper while the harness calls sit in the test escapes NC-012's second
  form, though not NC-011.
- **Python and shell tests** are out of scope, as for NC-004: pytest bounds
  them.

## Exemptions

An exemption is the NC shape `{repository, path, rationale, review_trigger}`.
It suppresses NC-011 and NC-013 for that exact file and nothing else: NC-012
and every indeterminate finding stay reported. A blank rationale or review
trigger is ignored, because an exemption nobody must revisit is not a record.
