# nested-cargo-nextest

Audits `.config/nextest.toml` for repositories whose Rust tests start a
compiler. The audit is read-only. It evaluates a
`policy-input/nested-cargo-nextest` envelope built by
`concordat artefact rule run`; it never runs Cargo or nextest.

## Why

A test that runs `cargo build` or `rustc` does compiler work inside a timed
test body. With nextest's defaults a cold build is indistinguishable from a
hang and consumes the test's whole allowance; builds in different tests also
compete for the same cores and the same package-cache lock. netsuke's
nested-Cargo test hit 900 s, and rstest-bdd had about 120 s of slack in 75
minutes. The corpus behind the rule is those two repositories before and after
their fixes, ortho-config's BDD suite today, and catnap's slow-timeout-only
configuration.

## How the rule decides a repository has such tests

A textual scan of `*.rs` files, deliberately narrow so it does not demand
configuration from a repository that needs none:

- only **test code** counts: a file under `tests/`, `test_support/`, `testing/`
  or `ui/` (or named `*_test.rs` or `*_tests.rs`), or text after the first
  `#[cfg(test)]` of another file. `build.rs`, `xtask`, hidden, `target`,
  `vendor`, `node_modules`, `build`, `dist` and `venv` trees are skipped;
- a `cargo` spawn counts only with a **compiling subcommand** (`build`,
  `check`, `test`, `run`, `package`, `clippy`, `doc`, `install`, `publish`,
  `bench`, `miri`, `nextest`, `llvm-cov`, `rustdoc`, `rustc`) among the
  arguments; `cargo metadata` and `cargo config` do not count;
- a `rustc` spawn always counts;
- the file must name `Command`, so `env!("CARGO")` read for a path alone is not
  a spawn;
- Python tests that spawn `cargo` are outside the scan: pytest bounds them.

## Checks

- **NC-004** (error): a non-exempt spawner exists and the configuration is
  missing (`.config/nextest.toml` absent), has no test group with
  `max-threads = 1`, has no profile override placing tests in such a group,
  sets no `slow-timeout` (in a profile or an override), or sets no
  `global-timeout` (in any profile). Each missing piece is its own finding.
- **NC-005** (error): a top-level `tests/<stem>.rs` spawner is not named, by
  binary or by one of its `fn` names, in the filter of an override that uses a
  serial group. Helpers under `src/` or `tests/support/`, and
  `main.rs`/`mod.rs`, are judged at repository level only.
- **EN-001** (error, indeterminate): the envelope is not a schema-1
  `policy-input/nested-cargo-nextest` document, the configuration cannot be
  read, or a source file could not be read or exceeds 1 MiB.

## Exemptions

A test source that starts a compiler but is bounded some other way is listed in
the `exemptions` parameter with its `repository` (`owner/name`, read from the
`origin` remote), its `path` (relative to the root) and a `reason`. An exempt
spawner is not counted at all. An exemption does not apply to another
repository. No exemption is shipped by default.

## What the rule declines to judge

It reads configuration, not behaviour. It does not run nextest, evaluate
filtersets, or check that the timeout values are sensible: whether a period is
long enough for a cold build is a sizing question for the repository. A
compiler started through a library (`trybuild`, `ui_test`) or by a tool the
test invokes is not seen; the Whitaker lints proposed in its RFC 0004 cover the
typed-code half of the problem.
