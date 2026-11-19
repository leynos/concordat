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
  or `ui/` (or named `*_test.rs` or `*_tests.rs`), or the braces of an item
  marked `#[cfg(test)]` in another file (production code after a closed
  `mod tests` is not test code). The marked item's first `{` or `;` is read at
  bracket depth zero, so a `;` inside a signature (`[u8; 4]`, a type in
  parentheses) does not end it early. `build.rs`, `xtask`, hidden, `target`,
  `vendor`, `node_modules`, `build`, `dist` and `venv` trees are skipped;
- a `cargo` spawn counts only with a **compiling subcommand** (`build`,
  `check`, `test`, `run`, `package`, `clippy`, `doc`, `install`, `publish`,
  `bench`, `miri`, `nextest`, `llvm-cov`, `rustdoc`, `rustc`) among **the same
  command's** arguments, read from the spawn to the next `Command::new` or the
  next function, and only before that command's first launch. Arguments are
  read with comments blanked and string contents kept, so a flag in a comment
  is not an argument while a `//` in a string does not hide the rest of the
  line; each argument and launch is attributed to the receiver it is called on,
  so `other.arg("build")` and `command.env("P", other.output())` are not this
  command's, and a receiver reached through a field or a path is declined
  however much whitespace sits around the separator (`holder . command`,
  `support::command`). A name called as a namespace is declined in the same way
  (`command::factory()`), because the path root is a type or module rather than
  the binding, while a single colon is a field initializer, so
  `Settings { status: command }` still passes the command as a value. A bare
  `Command` receiver is a name like any other, so only a `Command` reached
  through `::` is the constructor's namespace:
  `fn probe(Command: &mut Command)` makes `Command.arg(..)` that parameter's
  call, not the command being built. A launch may be spaced on either side of
  its dot or before its parenthesis (`command . status ()`). A command bound to
  a name and handed to a helper defined once in the same file is also read
  against that helper's body, even when the call spans lines; only statements
  on the parameter that receives the command count, so another command built in
  the helper does not contribute. Those statements are read at the call site
  with every use of the parameter taken to be the caller's binding, so a launch
  on either side ends the arguments in the order they run, and a version flag
  added through a renamed parameter still probes the caller's command. A helper
  read that stops inside a block is closed before it is spliced, so a block the
  helper leaves open cannot hide a later unconditional flag. A helper defined
  more than once, or one whose receiving parameter is not a plain `name: Type`
  binding, is declined rather than guessed; so is a call that a binding in the
  caller's own function shadows (a parameter included, even one after a
  method's receiver, a name bound by a parameter pattern such as
  `fn run((configure,): ..)`, a name bound by a destructuring pattern such as
  `let (configure,) = ..`, and a borrowed binding such as
  `let ref configure = ..`), a call written as a path or method
  (`support::configure(..)`, however it is spaced around the separator), a name
  a `use` item could import, or a helper that reassigns the parameter. The scan
  does not resolve imports, and a `use` may name an inline module in the same
  file (`use self::support::configure`) as easily as a helper in another file,
  so an imported name is declined either way; a glob import (`use support::*`,
  `use support::{*}`) spells no name but could bring any in, so it declines
  every handoff in the file. A helper whose body names the caller's binding is
  declined too: rewriting its receiving parameter to that name would make the
  helper's own uses of it read as the caller's. So is a helper whose receiving
  parameter a closure rebinds in its own `|..|` list
  (`|_: (), target: &mut Command| target.arg(..)`), because that `target` is
  the closure's argument, not the command the helper was given. A function item
  ends the arguments however it is qualified (`unsafe fn`, `default fn`,
  `extern "C" fn`), because the qualifiers do not change where the next scope
  begins, and it does so even when it begins on the line that ends the previous
  item (`} fn later(..)`): the boundary is the `fn` keyword, not the start of a
  line.
- each launch is read as its own **run**. A launch reached with no block open
  ends the read, because everything after it is a reuse; a launch inside a
  block ends only that branch, so the statements after the block and the `else`
  arm beside it are the first arguments of the next run, while an `else` that
  closes an inner `if` the launch is not in is code after the run. A `return`
  inside a block ends its path in the same way, wherever it is written, so a
  helper that returns early does not hide the caller's later run, and a run
  that leaves a branch still inherits the arguments given before it, because
  they are on every path that reaches it -- including those a branch condition
  adds, since the condition runs before either arm
  (`if command.arg("--version").get_program().is_some() { .. }`). Every run of a
  `rustc` command must be a probe, while any run of a cargo command that
  compiles is enough. A later declaration of the command's name shadows it
  inside that block only, while an assignment to it ends the read wherever it
  sits, because the block that ran it may have been entered. The subcommand is
  the first argument of the run that is neither an option nor an option's
  value, so `--color always nextest run` runs `nextest` and `cargo help build`
  prints help rather than building; a value the reader cannot see fills its
  option's slot rather than ending the read. A conditional word cannot hide the
  subcommand on the path that skips its block, so a `help` inside an `if`
  leaves an unconditional `build` still compiling, and each arm of a fully
  paired chain is read for its own subcommand, so a `metadata` in the `if` arm
  does not hide the `build` in its `else` arm. A `-h` or `--help` given on
  every path prints help whatever the subcommand is, while one added inside a
  block does not excuse the build on the path that skips it. A help flag in one
  arm excuses only the path through that arm, so a `build --help` in the `if`
  arm does not excuse the `check` its `else` arm runs, while a help flag in
  every arm excuses the whole run. Words after a `--` belong to the program the
  command runs, not to cargo. A word is the text its string literal has at run
  time, so an escape (`\x64`, `\u{64}`) is decoded and cannot hide `build` or
  `--version` from the read, and an escaped quote does not end the literal
  early. A word counts as unconditional only when every block open at it is
  also open at the run's launch, so a flag in the branch that launches is on
  the run's path while one in a sibling block that has already closed is not. A
  flag added on only some paths, behind a `&&`/`||` short circuit or inside a
  block that may not run, does not make a run a probe -- except when one fully
  paired `if`/`else` chain gives a probe in every arm, whether that choice is
  written across statements or on one line
  (`let _ = if c { .. } else { .. };`), since then no path reaches the launch
  without one. Each arm is read with the words given before the branch, so a
  `--print` in one arm and a `cfg` after the choice complete each other; a
  short circuit inside an arm (`query && command.arg("-V")`) can still skip
  that arm's flag, so the arm does not cover its path. `cargo metadata` and
  `cargo config` do not count;
- a `rustc` spawn counts, unless every run of that same command is given a
  version flag (`--version`, `-V`, `-vV`), a help flag (`-h`, `--help`) or a
  `--print` of a table that needs no compile (`sysroot`, `target-libdir`,
  `host-tuple`, `cfg`, `target-list`, `target-cpus`, `target-features`,
  `code-models`, `relocation-models`, `tls-models`): such a probe exits at
  once. A `--print` query may be split across calls
  (`.arg("--print").arg("cfg")`), which asks the same question as one array,
  but its two halves must be adjacent: a word the reader cannot see between them
  (`.args(["--print", print_kind, "cfg"])`), or one given only inside a block
  (`.arg("--print")`, then `if link { .arg("link-args") }`, then
  `.arg("cfg")`), may be `link-args`, so the query is not a probe. Comments and
  other statements do not count, and `--print=link-args` and
  `--print=native-static-libs` still do, because they build the crate;
- the file must name `Command`, so `env!("CARGO")` read for a path alone is not
  a spawn;
- Python tests that spawn `cargo` are outside the scan: pytest bounds them.

## Checks

- **NC-004** (warning by default): a non-exempt spawner exists and the
  configuration is missing (`.config/nextest.toml` absent), has no test group
  with `max-threads = 1`, has no profile override placing tests in such a
  group, sets no `slow-timeout` (in a profile or an override), or sets no
  `global-timeout` (in any profile). Each missing piece is its own finding.
- **NC-005** (warning by default): a top-level `tests/<stem>.rs` spawner is not
  named, by binary or by one of its `fn` names, in the filter of an override
  that uses a serial group. A predicate the filter negates (`not(binary(ui))`,
  `!test(compiles)`) excludes that name rather than selecting it, so it does
  not name the spawner. Helpers under `src/` or `tests/support/`, and
  `main.rs`/`mod.rs`, are judged at repository level only.
- **EN-001** (indeterminate; an error for an envelope defect, the configured
  severity otherwise): the envelope is not a schema-1
  `policy-input/nested-cargo-nextest` document, the configuration cannot be
  read, or a source file could not be read or exceeds 1 MiB.

## Exemptions

A test source that starts a compiler but is bounded some other way is listed in
the `exemptions` parameter with its `repository` (`owner/name`, read from the
`origin` remote), its `path` (relative to the root), a `rationale` and a
`review_trigger` (the event after which the exemption is revisited). A blank
rationale or trigger suppresses nothing. An exempt spawner is not counted at
all. An exemption does not apply to another repository. Four ship by default:
netsuke's single-file `rustc` compile tests and its `cargo package --list`
smoke test (ADR-0059 keeps them in the default profile), and shared-actions'
toy-app test, which no nextest lane runs.

## Severity

The `severity` parameter defaults to `warning`: findings are reported and the
rule's exit status stays 0, so the estate can be surveyed before the rule
blocks. Set it to `error` to enforce the rule. An envelope defect is always an
error.

## What the rule declines to judge

It reads configuration, not behaviour. It does not run nextest, evaluate
filtersets, or check that the timeout values are sensible: whether a period is
long enough for a cold build is a sizing question for the repository. A
compiler started through a library (`trybuild`, `ui_test`) or by a tool the
test invokes is not seen; the Whitaker lints proposed in its RFC 0004 cover the
typed-code half of the problem. The scan resolves no Rust names or types: when
a helper's definition is ambiguous it declines the handoff, which can miss
compiler work in that helper.
