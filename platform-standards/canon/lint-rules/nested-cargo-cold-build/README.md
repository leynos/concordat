# nested-cargo-cold-build

Audits the nested builds a repository's own tests start inside their timed
bodies. The audit is read-only. It evaluates a
`policy-input/nested-cargo-cold-build` envelope built by
`concordat artefact rule run`; it never runs Cargo, and it never reads the
network.

## Why

A timed test that compiles pays for whatever the target directory does not
already hold. When nothing has filled it, the test body compiles every
dependency inside its own deadline and times out although its assertion is
sound. That failure is hard to diagnose: the timeout names the test, not the
cold build, so the person or agent reading it tends to raise the limit, and the
next cold run hits the new one.

leynos/rstest-bdd hit this three times (issue
<https://github.com/leynos/rstest-bdd/issues/910>): `cargo-bdd`'s CLI smoke
test at 180 s, the trybuild suite at 20 minutes and the feature-rebuild test at
300 s. None of them built into a temporary directory. Each built into a shared
or crate-local target that the timed body was the first to fill. The fix
(<https://github.com/leynos/rstest-bdd/pull/916>) prepares the artefacts in a
named test outside the timed body, stamps them, and makes the timed test refuse
to run when the stamp is missing or stale. leynos/ortho-config
(<https://github.com/leynos/ortho-config/issues/611>) had the other defect: a
target directory named after each scenario's temporary directory, so no run
could reuse another's build and eleven of fifteen trees held the identical cold
build.

The rule asks for the fix up front rather than detecting a cold build after the
fact, which a text scan cannot do. It ships at `warning` severity so the estate
can be surveyed first; promotion to `error` is the one-line parameter change
recorded in `docs/roadmap.md` (DF12-109).

## Checks

- **NC-009** (severity from the `severity` parameter, `warning` by default): a
  command in test scope that compiles, or may, builds into a private target
  directory. The finding is reported at the file and line of the command.
- **NC-010** (same severity): a test source compiles in a timed test, and the
  repository declares no preparation outside it. The finding is reported once
  per file, at its first compile, and names every kind of build the file starts
  (`cargo`, `trybuild`, `macrotest`, `helper`). The message says what to do and
  links two examples.
- **EN-001** (same severity, indeterminate): a command that compiles sets its
  target directory from a value the reader cannot follow; a `cargo` command
  with an unseen subcommand has no declared preparation and no compile beside
  it in its crate; or the envelope carries a problem fact (a source the reader
  could not decode).
- **EN-001** (always `error`, indeterminate): the input is not a schema-1
  `policy-input/nested-cargo-cold-build` envelope, a fact carries a target
  state this policy does not know, or a fact is missing a field its finding
  needs. This says the rule could not run, not that the repository is unsafe,
  so the `severity` parameter cannot turn it off.

## What counts as a private target (NC-009)

The reader replays the calls a test makes on one command. Within one source of
the target, the last write wins, as the NC-007 reader does for
`CARGO_NET_OFFLINE`. Across sources, precedence decides and write order does
not: cargo takes the target from `--target-dir`, then `CARGO_TARGET_DIR`, then
`CARGO_BUILD_TARGET_DIR` (cargo reads the last as `build.target-dir`), then the
workspace default. Words after `--` belong to the program cargo runs and are
not read. The reading is one of five states:

- `private`: the value derives from `tempfile::tempdir()`, `TempDir`,
  `tempdir_in`, `mkdtemp` or a `NamedTempFile`, or from a `uuid`, `SystemTime`,
  `process::id()` or a random source. A name bound to one of those in the same
  function counts, followed through three levels of `let` bindings; a `TempDir`
  parameter counts. The same applies when the command's working directory is
  temporary, the command passes no `--manifest-path` (which moves the default
  target to that workspace), and the command can inherit no target: an
  `env_clear()`, or an `env_remove` of **both** `CARGO_TARGET_DIR` and
  `CARGO_BUILD_TARGET_DIR`, with no target set afterwards and no environment
  call the reader cannot read.
- `unknown`: a target is set from a value the reader cannot follow, such as the
  result of a function in another file; it was set before a call the reader
  cannot see through; only `CARGO_BUILD_TARGET_DIR` is set while an inherited
  `CARGO_TARGET_DIR` may take precedence (cargo reads that one first); or an
  environment call the reader cannot read (`.envs(extra)`, `.env(key, value)`)
  may have set a target. This is reported as indeterminate, never as a pass and
  never as a private target. An argument call the reader cannot see (one made
  on only some paths, or handed to another function) may carry `--target-dir`,
  so it leaves the target unknown too, and no `env_clear()` or later variable
  settles it, because `--target-dir` beats every variable. An element of its
  source the reader cannot read is taken as a value only when an option that
  consumes one precedes it (`"--out-dir", path`, `"--manifest-path", path`: an
  option the reader knows takes a value, and not one given its value inline as
  `--out-dir=x`); anywhere else (`[flag, path]`, `["--release", flag]`,
  `["--timings", flag]`, an option the reader has not heard of, `.args(extra)`,
  a handoff) it may be the flag. A `--` that already sent the rest of the line
  to the program cargo runs ends the question. An option spelled out before a
  conditional argument call is not relied on either: the call's own argument
  may already have been the value the option was waiting for, so a later
  unreadable word is taken as the flag rather than as that value. A helper's
  own parameter, including every name a destructuring pattern binds, is unknown
  in the same way: it holds what the caller passed, and an unrelated binding of
  the same name in the caller is never read in its place.
- `dedicated`: a fixed `target/...` directory inside the crate
  (`CARGO_MANIFEST_DIR` joined with `"target/..."`), or a fixed path under the
  system temporary directory (`env::temp_dir().join("scenario")`). It persists
  between runs, so it is warm the second time; whether the first time is paid
  for is NC-010's question, not NC-009's. The system temporary directory is
  shared by every run, so only a unique name inside it is private.
- `shared`: the value reads `CARGO_TARGET_DIR`, `cargo metadata`'s
  `target_directory`, or joins a relative path out of the crate to the workspace
  `target`.
- `inherited`: nothing the command does picks a target, so it builds where its
  parent builds.

`env_remove("CARGO_TARGET_DIR")` alone does not make a command private. It
leaves `CARGO_BUILD_TARGET_DIR` free to name a shared directory, as the review
of whitaker's RFC 0006 observed
(<https://github.com/leynos/whitaker/pull/505#discussion_r4349239940>), so the
reader needs both removals or an `env_clear()`. An `env_clear()` followed by a
write to a target variable reads the write, because that is what the harness in
rstest-bdd does after clearing the environment.

A command that starts a program other than `cargo` is read too, when its chain
sets `CARGO_TARGET_DIR`, `CARGO_BUILD_TARGET_DIR` or `--target-dir`: a test
that runs `cargo-orthohelp` with a target in its environment builds through
that program's own cargo, and the target it passes is the one that matters.
Such a command compiles as far as the reader can tell (`unknown`), so it is
judged.

A command that compiles nothing (`cargo metadata`, `cargo config get`,
`cargo fetch`, `cargo package --list`) is not judged, whatever target it names.

## What counts as prepared (NC-010)

NC-010 does not try to prove a build is cold: a text scan cannot know what an
earlier step left in a target directory. It asks that a test which compiles
declares where the compile work is paid for. A source that compiles in a timed
test is one of:

- a `cargo` command whose arguments start a compile (`build`, `check`, `test`,
  `run`, `package`, `clippy`, `doc`, `rustc`, `install`, `publish`, `bench`,
  `miri`, `nextest`, `llvm-cov`, `rustdoc`). `package --list` and any command
  carrying `--unit-graph` compile nothing and are not builds. A command whose
  subcommand the reader cannot see (`cargo(args)`, `.args(subcommand)`) is a
  build only in a crate that visibly compiles elsewhere through one of the
  other routes here; anywhere else it is reported as indeterminate, because the
  arguments are as likely `metadata` as `build`;
- a `trybuild::TestCases` reference, or a `macrotest::expand*` call. Both build
  in a directory of their own under the target (`<target>/tests/trybuild`,
  `<target>/tests/macrotest`) that the outer build never warms, so each is a
  nested build wherever the outer one runs;
- a call, in a file under a test directory, to a function listed in the
  `compiling_calls` parameter. A harness that builds a binary on the caller's
  behalf hides the `Command` from the reader, so the repository names the
  function.

The compile is *prepared* when any one of these holds:

1. **A nextest setup script.** A `[scripts.setup.<name>]` table the file defines
   is run by a `[[profile.<p>.scripts]]` rule whose `filter` is `all()` or
   names the test binary or the crate's package. Nextest runs it before the
   tests it covers, so the build is paid for outside any test's deadline. Only
   `all()` and a union (`|`, `+`, `or`) of `binary(..)`, `binary_id(..)` and
   `package(..)` selectors are evaluated, each against its own kind of name: a
   `binary(..)` or `binary_id(..)` selector against the test binary's name and a
   `package(..)` selector against the crate's package name, so `package(=ui)`
   does not cover a binary called `ui` in package `app`. A matcher is evaluated
   as nextest reads it (`=text` equal, `~text` contains, `/regex/`, `#glob`,
   and plain text a glob), not searched for the name, so
   `package(/^app[0-9]+$/)` does not cover package `app`. A `binary_id` is the
   package and binary joined by `::` (`app::ui` for `app/tests/ui.rs`), so
   `binary_id(=ui)` names package `ui`'s library, not that binary. A filter
   that negates (`not binary(ui)`), subtracts (`all() - binary(ui)`), intersects
   (`binary(ui) & test(=one)`) or uses another predicate names a binary
   without selecting all of it, so it never counts as coverage and the reader
   reports it as indeterminate. So does a glob using braces or backslash
   escapes, which nextest reads with `globset` and the reader does not (a
   regular expression or an `=` or `~` matcher may hold both). Only a rule in
   the `default` profile counts, because every profile inherits it and a plain
   `cargo nextest run` selects it: a rule under `profile.ci` runs only under
   `--profile ci`, and a rule with a `platform` restriction may not run on the
   platform under test, so each is reported and neither counts. The
   configuration is read only when its path resolves inside the checkout; a
   symlink (to the file or to a directory above it) that leaves it is reported
   and not followed.
2. **A named preparation test in the same crate.** A test function (carrying
   `#[test]`, `#[rstest]`, `#[test_case]` or a `::test` attribute) whose name
   matches a pattern in `preparation_gates.tests`. An ordinary function of the
   same name is not a test and prepares nothing. The test compiles what the
   timed tests would otherwise compile, and the nextest configuration gives it
   a budget of its own, first in file order, in the same serial test group.
3. **A preparation gate called in the same crate.** A call to a function in
   `preparation_gates.functions`: a function that fails the timed test, naming
   the preparing command, when the artefacts are missing or stale.

4. **A Makefile preparation.** The Makefile's `test` target compiles before it
   tests, so every nested build the checkout starts has been paid for outside
   the timed run. That holds when a prerequisite of `test`, an earlier recipe
   line, or an earlier command on the test line joined by `&&` runs a cargo
   command that compiles without running the tests (`build`, `bench`,
   `test --no-run`, `nextest run --no-run`, `nextest archive`; `check` and
   `clippy` leave only metadata, so they do not warm a build), directly or
   through a target it names (`$(MAKE) prepare-nested-builds`). The compile
   must stop the target when it fails: an ignored-error line (`-cargo build`), a
   `;` or `||` join, a command inside an `if` and a build that comes after the
   test command prepare nothing, and a Makefile that declares `.ONESHELL` is
   not read. A build after `||` runs only when the command before it failed, so
   it prepares nothing either. A target that runs tests before it builds is not
   a preparation, and `make -C`, `-f` and `-n` goals are not followed: another
   directory's or Makefile's targets are not this one's, and a dry run builds
   nothing. Make does not order sibling prerequisites (`make -j` runs them
   together), so `test: run-tests prepare` proves nothing for `run-tests`, while
   `test: prepare` with the tests in the recipe, or `run-tests: prepare`,
   does. A line that picks `nextest run` or `test` by probing the machine is
   read in both branches, and the preparation must hold in each. The reader
   fails closed: it counts a line only in the forms described here, exactly.
   `make -t`, `-q` and `-i`, a group of goals on one make line, a `VAR=value`
   override on it, a goal the shell computes, prerequisites that read in
   alternative ways or that the expansion could not resolve, and a prerequisite
   that may run tests without preparing for them are not preparation. The
   preparation must build with the same features and profile as the tests, or
   it fills a different directory (it can even overwrite the binaries the tests
   use). The rule cannot check that, and the repository's own review must.

The defaults for 2 and 3 are the names rstest-bdd chose
(`nested_fixture_preparation$`; `ensure_prepared`, `ensure_prepared_with`,
`ensure_prepared_with_probe`, `prepare_now`, `prepare_now_with_probe`). They
are one example of a general convention, not a special case: a repository that
prepares under other names lists them in the two parameters instead of taking
an exemption. A preparation test and a gate are matched to a source by crate
(the nearest `Cargo.toml` at or above it), not file by file, because one
crate's preparation test commonly stamps several test binaries.

The rule does not check that the preparation is correct, only that it exists.
Whether the preparation test sits in the right serial group, and whether a
timeout is long enough, are NC-004 and NC-005's business
(`nested-cargo-nextest`).

## What counts

- **Test scope** is the one `nested-cargo-offline-tests` reads: `tests` and
  `test_support` directories, whole-file test modules, `#[cfg(test)]` modules
  and the files they declare. Fixture package sources are excluded: they are
  input to a nested build, not the code that starts one.
- **Each process is judged by what the command carried when it started**, and a
  helper that returns a `Command` is read at its call sites, exactly as NC-007
  reads them. A run that adds an argument the reader cannot read is kept as its
  own fact even when its offline state matches an earlier run, because the
  unreadable word may select a different target. So is a run that adds a literal
  `--target-dir` or `--manifest-path`, each of which moves where cargo builds,
  or that supplies a `--target-dir` the run before left waiting: that word is
  the flag's value, not a harmless addition. A command a helper builds whose
  call sites live in another file is read at its definition and its subcommand
  is `unknown`.
- **Names resolve inside one function.** A target value is read through the
  `let` bindings of the function that builds the command, signature included (a
  parameter typed `TempDir` counts), so another test's binding of the same name
  cannot change the answer. A name bound to different expressions within that
  function is not tracked and reads `unknown`.
- **Calls the reader cannot see through** (a handoff such as
  `configure(&mut cmd)`, an `envs(..)` list it cannot read, a conditional call)
  turn an earlier target reading into `unknown` rather than keeping it, because
  the unseen call may have replaced it.

## Exemptions

An exemption is the NC shape `{repository, path, rationale, review_trigger}`.
`repository` is the `owner/name` slug read from the `origin` remote, and `path`
is the exact repository-relative file: an exemption suppresses the findings for
that file and no other. `rationale` and `review_trigger` must be non-blank; the
policy ignores an entry that leaves either blank, because an exemption nobody
must revisit is not a record. Exemptions are separate from those of the other
nested-cargo packages: a file exempt from NC-007 or NC-004 is not exempt here.

An exemption suppresses **NC-009 and NC-010 only**. Evidence the reader could
not establish stays reported, so an exempt file cannot hide the rule's own
uncertainty.

The defaults record netsuke's `tests/locale_stub_ui_tests.rs`, which builds a
generated two-crate workspace of path dependencies under a temporary root on
purpose, to prove that Cargo honours a separate target and build directory.

## What the rule declines to judge

- **How long the cold build takes.** That is a measurement, and NC-005's
  timeouts are the bound. This rule asks only that preparation be declared.
- **A target value computed in another file.** The reader follows `let` bindings
  in the function that sets the target and does not follow a call into another
  function or module; such a value is `unknown` and reported as indeterminate.
  Whitaker's RFC 0006 made the same choice and listed ortho-config's
  `scenario_target_dir` as the case it declines.
- **Whether a shared target is warm.** An inherited or shared target is not a
  finding by itself; NC-010 is where an unprepared build in one is reported.
- **Non-test sources.** A `cargo` spawn in library or binary code is outside the
  reader's scope.
