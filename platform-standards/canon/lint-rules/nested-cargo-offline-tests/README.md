# nested-cargo-offline-tests

Audits the `cargo` a repository's own tests start, and the test entry point
that runs them. The audit is read-only. It evaluates a
`policy-input/nested-cargo-offline-tests` envelope built by
`concordat artefact rule run`; it never runs Cargo, and it never reads the
network.

## Why

A test that starts its own `cargo` — a nested build, `cargo package`,
`cargo publish --dry-run`, `cargo dylint`, a trybuild case — resolves
dependencies and may update the crates.io index. It does that while holding the
package-cache lock every repository on the host shares. On a host where several
agents build at once, one repository's nested cargo can leave another
repository's gate waiting for many minutes: leynos/whitaker measured a
19-minute wait, and leynos/theoremc was affected too. The durable fix lives in
each repository — leynos/whitaker#518 has the pattern — and this rule finds the
repositories that still need it.

The rule ships at `warning` severity. The estate is surveyed first, each
repository fixes its own findings in its own time, and promotion to `error` is
the one-line parameter change recorded in `docs/roadmap.md` (DF12-109).

## Checks

- **NC-007** (severity from the `severity` parameter, `warning` by default): a
  cargo spawn in test scope is `online` — it carries no `--frozen`, does not
  carry both `--offline` and `--locked`, and does not set `CARGO_NET_OFFLINE` to
  `"true"` or `"1"` on the command itself. The finding is reported at the file
  and line of the spawn.
- **NC-008** (same severity): a test entry point is `absent` — no
  success-gated `cargo fetch --locked` runs before the test command — *and* the
  checkout has at least one spawn or trybuild fact, so there is a nested build
  for the prefetch to protect. The finding is reported at the file and line of
  the entry point: the Makefile `test` target, a selected nextest profile's
  setup script, or a workflow step.
- **EN-001** (same severity, indeterminate): a spawn is `unresolved` (its
  arguments or environment came from a source the reader cannot see), an entry
  point is `uncertain` (its ordering could not be read) *and* the checkout has
  at least one spawn or trybuild fact, or the envelope carries a problem fact.
  The nested-build gate on the entry-point case is the same one NC-008 carries:
  with nothing for a prefetch to protect, an unreadable recipe is not evidence
  against the repository. The rule says it does not know rather than guessing
  either way.
- **EN-001** (always `error`, indeterminate): the input is not a schema-1
  `policy-input/nested-cargo-offline-tests` envelope, a fact carries a state
  this policy does not know, or a fact is missing a field its finding needs
  (`path` and a numeric `line` on a spawn; `path`, `line` and `name` on an
  entry point). A known state is not by itself a usable fact: Rego reads a
  missing key as *undefined* rather than as an error, so the rule body fails,
  the finding silently disappears, and the repository is reported clean on
  evidence nobody judged. A wrong type is as unusable as a missing field — `%d`
  with a string line prints `%!d(string=80)` where a line number belongs — so
  the required-field check also rejects a field of the wrong type, and the
  ordinary findings skip a malformed fact so it is reported once. This says the
  rule could not run, not that the repository is unsafe, so the `severity`
  parameter cannot turn it off.

## What counts

- **Test scope** is `tests` and `test_support`/`test-support` directories
  anywhere in the checkout, whole-file test modules, `#[cfg(test)]` modules and
  their inline children, and files reached from them by `mod name;` or
  `#[path]`. A declaration inherits scope from an enclosing `#[cfg(test)]`
  module, so `#[cfg(test)] mod checks { mod helpers; }` puts
  `src/checks/helpers.rs` in scope although the inner declaration carries no
  attribute of its own. A declaration gated by any other `cfg` is left alone,
  because the reader cannot decide what that gate compiles.
- **Scope follows every hop.** A file brought into scope is itself read for
  declarations, so `src/lib.rs -> src/checks.rs -> src/checks/helpers.rs` all
  count, not just the first two. Where a declaration's children live depends on
  how the compiler reached its file: a crate root (`lib.rs`, `main.rs`,
  `mod.rs`, and any file directly under `tests/`, `benches/`, `examples/` or
  `src/bin/`) and a file named by `#[path]` resolve their children beside
  themselves, while an ordinary library file uses a directory named after it. A
  file that is several of these at once is read both ways and whichever file
  exists is taken.
- **Only files a compiler builds are scanned.** A `<name>.rs` directly under
  `tests/` that no manifest declares as a target is trybuild fixture text or a
  file another test `include!`s. Its `mod` lines bind nothing, so following
  them would report a declaration that no compiler makes. Its spawns are still
  read when the file sits under a `tests` directory, because the rule's own
  test-scope selector names that directory.
- **A `#[path]` on an inline module is not followed.**
  `#[path = "dir"] mod m { … }` redirects its children to `dir/`, and the
  estate has three such occurrences, none in test scope. The reader treats the
  children as inline and reports nothing for them rather than guessing the
  directory.
- **Trybuild evidence** is a manifest dependency on the `trybuild` package —
  under any name the manifest gives it, so `tb = { package = "trybuild" }`
  counts — or a `TestCases` reference in test scope through one of those names.
  A reference through a name the governing manifest does not declare belongs to
  another crate and is not evidence.
- **Fixture package sources are excluded.** A package the existing fixture
  selectors recognize is input to a nested build, not the code that starts one;
  a `cargo` spawn inside it is the build under test. Fixture *manifests* and
  locks are NC-001/NC-002's business, not this rule's.
- **Offline evidence is read from the command itself**: `--frozen`, or
  `--offline` together with `--locked`, or `CARGO_NET_OFFLINE` set to `"true"`
  or `"1"` on that command. Both values are offline to Cargo, which prints its
  offline-mode note for each and leaves the network reachable for `"0"`,
  `"yes"`, `"on"` and the empty string. An inherited environment is not
  evidence: the gate's environment is not the test's.
- **The last `CARGO_NET_OFFLINE` assignment is the one cargo reads.**
  `Command::env` keeps the value given last for a name, so
  `.env("CARGO_NET_OFFLINE", "true")` followed by
  `.env("CARGO_NET_OFFLINE", "0")` leaves the command **online**; the reader
  reports `online`, not `offline`. A call site's own assignment runs after the
  helper it called returns, so the call site wins. The same holds for the pairs
  `.envs([...])` applies in order. A last assignment whose value the reader
  cannot see makes the spawn `unresolved`, because the earlier pair no longer
  describes what cargo will read.
- **`--frozen` is not revocable.** No argument and no environment variable turns
  it off, so a command carrying `--frozen` stays offline whatever an
  environment assignment says. Only the environment is last-write-wins; the
  flag is evidence on its own.
- **A version or help probe is not a spawn.** `cargo --version`,
  `cargo kani --version`, `cargo help` and `cargo build --help` read no
  manifest and touch no network, so they draw no finding. A version flag that
  carries a value is real work: `cargo install --version 1.2.3` still counts.
- **A prefetch counts** only when `cargo fetch --locked` runs before the test
  command and its failure stops the run: `&&` or a fail-fast line sequence. A
  `;`, `||`, a pipeline, a background command or an ignored error proves
  nothing.
- **Both steps' `if:` conditions are read for what they skip.** A workflow
  fetch step is proof only when it runs on every run, so any `if:` other than
  `success()` — bare or wrapped in `${{ }}` — leaves the entry point `absent`.
  The test step is asked the opposite question, whether it can still run after
  a failed fetch. GitHub adds its implicit `success()` check only when the
  condition names no status function, so `success() && runner.os == 'Linux'`
  waits for the fetch and keeps the entry point `before`, while
  `success() || github.ref == 'refs/heads/main'` can run on `main` after a
  failure and makes it `uncertain`. A condition resting on a fact the reader
  does not have is `uncertain` rather than guessed either way. Function names
  are matched without regard to case, as GitHub matches them, so `Success()` and
  `ALWAYS()` read as `success()` and `always()`. A name inside a quoted
  literal is text the condition compares, not a call:
  `github.event.name == 'always()'` still gets the implicit check.
- **A command carrying an expression proves no order.**
  `cargo fetch --locked ${{ inputs.extra }} && cargo test` reads as gated once
  the expression is removed, but the expression may supply the `|| true` that
  makes a failed fetch harmless, so the entry point is `uncertain`.

## Exemptions

An exemption is the NC shape `{repository, path, rationale, review_trigger}`.
`repository` is the `owner/name` slug read from the `origin` remote, and `path`
is the exact repository-relative file: an exemption suppresses the findings for
that file and no other. `rationale` and `review_trigger` must be non-blank; the
policy ignores an entry that leaves either blank, because an exemption nobody
must revisit is not a record.

An exemption suppresses **NC-007 and NC-008 only**. Evidence the reader could
not establish (an `unresolved` spawn, an `uncertain` entry point, a problem
fact) stays reported wherever the rule judges it, so an exempt file cannot hide
the rule's own uncertainty.

The defaults record whitaker's installer, which builds its own copy of the
crate from crates.io and cannot run offline.

## What the rule declines to judge

- **Non-test sources.** A `cargo` spawn in library or binary code — including
  whitaker's `installer/src/builder.rs` — is outside the reader's scope, so the
  default exemption for it is a record rather than a live suppression.
- **What a test does with the output.** The rule judges whether the spawn can
  reach the network, not how long it runs or what it writes.
- **Which profile a hidden command selects.** When a Makefile `test` target's
  command cannot be read, the entry point is `uncertain`; the policy does not
  guess which nextest profile it would have selected.
- **Whether `--frozen` will succeed.** A spawn that carries `--frozen` is
  compliant even if the lock it would need is missing; a failing fetch is a
  test failure, not a policy-input fact.
