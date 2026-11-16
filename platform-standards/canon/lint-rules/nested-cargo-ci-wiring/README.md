# nested-cargo-ci-wiring

Audits the workflows that run nested `cargo` builds. The audit is read-only. It
evaluates a `policy-input/nested-cargo-ci-wiring` envelope built by
`concordat artefact rule run`; it never runs a workflow.

## Why

A nested `cargo` run is a child process: it inherits whatever the CI job
exports. Two wiring mistakes leave it cold. A `CARGO_TARGET_DIR` pointed at a
per-run path means the target tree starts empty on every run, and sccache keys
miss because the paths differ (ortho-config measured 15 trees totalling 9 GB).
A job that opts out of the compiler-cache wrapper and names it nowhere compiles
its child `cargo` without sccache.

## What the shared action does

`leynos/shared-actions/.github/actions/setup-rust` selects the sccache backend,
starts the server and then exports `RUSTC_WRAPPER` through `GITHUB_ENV` unless
its `export-rustc-wrapper` input is `false`. `GITHUB_ENV` reaches every later
step in the job, so a child `cargo` inherits the wrapper with no job-level
`env`. The rule therefore does not require `RUSTC_WRAPPER` at job level; it
requires that an explicit opt-out is not silent.

The action documents the opt-out for nested builds such as trybuild fixtures,
where each compile is a miss that is also stored. That is a trade-off the rule
leaves to the repository: scope the wrapper to the commands that should use it
(`RUSTC_WRAPPER="$SCCACHE_PATH" cargo test`) and the job passes.

## Checks

- **NC-006** (error), temporary target: a workflow sets `CARGO_TARGET_DIR`, in a
  workflow, job or step `env` or as an assignment in a `run` script, to a value
  matching one of the `temporary_patterns` parameter. The defaults are the
  runner's temp directory (`runner.temp`, `RUNNER_TEMP`), the run id, number,
  attempt or commit SHA, `mktemp`, `TMPDIR`, a `/tmp` path and a `$(...)`
  command substitution. Expressions are matched as written, not evaluated. A
  setting is harmless, and not reported, when the job it belongs to is
  **check-only**: it runs cargo, every cargo command
  (`cargo [+toolchain] <subcommand>`) is `check`, `clippy`, `fmt`, `metadata` or
  `tree`, no `run` script mentions `make`, `just`, `nextest`, a test runner,
  an interpreter, a shell, `./script` or a `cargo-*` plugin, and every action
  it uses is a plain set-up one (`checkout`, `setup-rust`, `rust-toolchain`,
  `install-action`, `cache`, artefact transfer). An MSRV `cargo +<msrv> check`
  job is the usual case: it has no nested build to inherit a warm tree, and
  sharing the target would only hold a second toolchain's artefacts. A
  workflow-level setting is harmless only when every job of the workflow is
  check-only. A job the reader cannot place stays subject to the rule.
- **NC-006** (error), silent opt-out: a job has a `setup-rust` step whose
  `export-rustc-wrapper` input is false (`false`, `'false'`, `0`, `no` or
  `off`) and `RUSTC_WRAPPER` appears nowhere in the workflow `env`, the job
  `env`, a step `env` or a `run` script of that job.
- **EN-001** (error, indeterminate): the envelope is not a schema-1
  `policy-input/nested-cargo-ci-wiring` document, or a workflow could not be
  decoded.

## Exemptions

A workflow that needs a private tree on purpose (a benchmark lane) is listed in
the `exemptions` parameter with its `repository` (`owner/name`, read from the
`origin` remote), its `path` (`.github/workflows/<file>`) a `rationale` and a
`review_trigger` (the event after which the exemption is revisited; a blank
rationale or trigger suppresses nothing). An exemption does not apply to
another repository. No exemption is shipped by default.

## What the rule declines to judge

Only root workflows (`.github/workflows/*.yml`) are read; composite actions and
reusable workflows in other repositories are not. A target directory set
through a `with:` input, a Makefile or a `.cargo/config.toml` is not seen.
Whether a workflow runs nested builds at all is not asked: the check is about
wiring that would cripple them if it did.
