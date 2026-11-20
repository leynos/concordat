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
  next function, and only before that command's first launch, so a later launch
  of a reused `Command` on the same path is missed and an empty audit is no
  proof that the test starts no compiler. Arguments are read with comments
  blanked and string contents kept, so a flag in a comment is not an argument
  while a `//` in a string does not hide the rest of the line; each argument
  and launch is attributed to the receiver it is called on, so
  `other.arg("build")` and `command.env("P", other.output())` are not this
  command's, and a receiver reached through a field or a path is declined
  however much whitespace sits around the separator (`holder . command`,
  `support::command`). A name called as a namespace is declined in the same way
  (`command::factory()`), because the path root is a type or module rather than
  the binding, while a single colon is a field initializer, so
  `Settings { status: command }` still passes the command as a value. A bare
  `Command` receiver is a name like any other, so only a `Command` reached
  through `::` is the constructor's namespace, and a path before it
  (`std::process::Command::new(..)`) is part of the type rather than another
  value: `fn probe(Command: &mut Command)` makes `Command.arg(..)` that
  parameter's call, not the command being built, while
  `let mut Command = Command::new(..)` makes `Command.arg(..)` the binding's
  own call. A `let` binding is read when its constructor starts on the next
  line, because the name ends at the `=`, and the name is found in the masked
  source, so a comment between the `=` and the constructor is whitespace as
  Rust treats it. A launch may be spaced on either side of its dot or before
  its parenthesis (`command . status ()`). A `CARGO` lookup passed to the
  constructor (`Command::new(env::var_os("CARGO").unwrap())`) is that
  constructor's argument rather than a window of its own, so the chain's `.arg`
  and `.status` calls are still read. A command bound to a name and handed to a
  helper defined once in the same file is also read against that helper's body,
  even when the call spans lines; only statements on the parameter that
  receives the command count, so another command built in the helper does not
  contribute. Those statements are read at the call site with every use of the
  parameter taken to be the caller's binding, so a launch on either side ends
  the arguments in the order they run, and a version flag added through a
  renamed parameter still probes the caller's command. A helper read that stops
  inside a block is closed before it is spliced, so a block the helper leaves
  open cannot hide a later unconditional flag, and a spliced fragment with no
  call on the command keeps its braces in the order they were written, with the
  `else` between them, so the arms still pair rather than reading as code that
  runs one after the other, and a `return` stays inside the block it was
  written in, because after that block it would end every path. A call that is
  the `return`'s own expression (`return target.arg("--help")`) runs before
  control leaves, so it is not guarded with the tail. A block-level `return`
  also marks the statements after it as the code of the path that skipped it;
  when the helper is spliced, that tail is wrapped in a block of its own and
  the keyword is blanked in the masked form, so the caller's own statements
  after the call stay in the run the early-return path reached. The helper's
  statements are split at a `;` only at bracket depth zero, so a `;` inside a
  block argument (`target.arg({ let flag = "--quiet"; flag }).status()`) keeps
  the launch chained after it on the same statement. A parameter list is split
  on its own commas, so a struct pattern
  (`fn configure(Pair { first, command: other }: Pair, target: &mut Command)`)
  keeps its field comma and a comparison in a const block
  (`fn run(flag: Flag<{ 1 < 2 }>, (configure,): ..)`) is not a generic that
  swallows the parameters after it. A helper defined more than once, one defined
  `async` (its body runs when it is awaited, not at the call), or one whose
  receiving parameter is not a plain `name: Type` binding, is declined rather
  than guessed; so is a call that a binding in the caller's own function
  shadows (a parameter included, even one after a method's receiver, a name
  bound by a parameter pattern such as `fn run((configure,): ..)` or
  `fn run(Callbacks { setup: configure }: ..)`, a name bound by a destructuring
  pattern such as `let (configure,) = ..` or `let Hook(configure) = ..`, a name
  bound by a `for` loop's pattern such as `for (configure, other) in ..`, which
  hides it inside the loop's body only, a closure parameter in any position
  such as `|_: (), configure: fn(&mut Command)|`, a name bound by a `match`
  arm's pattern such as
  `match callback { configure => configure(&mut command) }`, which hides it
  through the whole arm expression, so a later arm's call is not hidden by an
  earlier arm's binding while a call after an earlier call in the same arm has
  closed (`configure => noop() + configure(..)`) still reaches the bound value,
  a `const` or `static` item of that name, mutable or not, in the caller's
  block or a block enclosing it (an item is in scope for its whole block, even
  before its declaration, while a `let` counts only before it and only while
  its own block is open at the call, so one declared in an inner block that has
  closed is out of scope, and a sibling block's item is not in scope at the
  call), and a borrowed binding such as `let ref configure = ..`), a call
  written as a path or method (`support::configure(..)`, however it is spaced
  around the separator), a name a `use` item could import, or a helper that
  reassigns the parameter. The parameters read are those of the innermost
  function whose body holds the call, so a function item declared inside the
  caller is not mistaken for its scope. The scan does not resolve imports, and a
  `use` may name an inline module in the same file
  (`use self::support::configure`) as easily as a helper in another file, so an
  imported name is declined either way; a glob import (`use support::*`,
  `use support::{*}`) spells no name but could bring any in, so it declines
  every handoff in the file. A helper whose body names the caller's binding is
  declined too: rewriting its receiving parameter to that name would make the
  helper's own uses of it read as the caller's. So is a helper whose receiving
  parameter a closure rebinds in its own `|..|` list
  (`|_: (), target: &mut Command| target.arg(..)`), because that `target` is
  the closure's argument, not the command the helper was given. A loop pattern
  that rebinds the receiving parameter (`for (target,) in [(other,)] { .. }`)
  makes the loop's body another command's too, so the read ends at the loop. A
  call written inside a closure body is declined as well, because the body runs
  when the closure is called rather than where it is written. The same holds
  for a helper's own parameter: a use of it inside a closure body, or inside a
  `match` arm whose pattern binds the parameter's name, is not the caller's
  command, so the helper is declined at the statement that holds it, and an
  arm's binding holds through the whole arm expression, so a closed call
  earlier in it does not end the arm. A function item ends the arguments
  however it is qualified (`unsafe fn`, `default fn`, `extern "C" fn`), because
  the qualifiers do not change where the next scope begins, and it does so even
  when it begins on the line that ends the previous item (`} fn later(..)`):
  the boundary is the `fn` keyword, not the start of a line.
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
  (`if command.arg("--version").get_args().next().is_some() { .. }`), and when
  one arm of an `if`/`else` returns, the other arm is the only path that
  reaches the launch, so its words are read as given on every path; an
  `else if` is a condition of its own, so the arm after it is not the only
  path. A `;` inside a block argument of the command's own call does not end
  the statement, so `command.arg({ let flag = "--quiet"; flag }).status()`
  keeps its launch. Every run of a `rustc` command that launches must be a
  probe, while any run of a cargo command that compiles is enough. A command
  must reach a launch inside its read to count at all, for `cargo` and `rustc`
  alike: a command that is built and given words but never run starts no child
  process, a `rustc` command with no launch at all is not a probe, and a launch
  beyond the read is declined rather than guessed at. A later declaration of
  the command's name shadows it inside that block only, while an assignment to
  it ends the read wherever it sits, because the block that ran it may have
  been entered; a closure parameter's scope is the closure itself, so
  `let _noop = |command: &mut Command| {};` does not hide the command from the
  statements after it, while a launch written beside the closure in the same
  statement (`(|command: &mut Command| {}, command.arg("build").status())`) is
  still the caller's; a `;` inside the body splits the statement, not the
  closure's scope, so
  `|command: &mut Command| { command.arg("x"); command.status(); }` stays
  hidden to its closing brace. The whole body is deferred work, whether it
  names a parameter or captures the command
  (`let deferred = || command.arg("build").status();`), and an `async`
  qualifier before the pipes opens a header just as `move` does, so a launch
  inside it is not the caller's either, and an expression-bodied closure ends
  at its `;`, so a launch written after it is. A statement that begins by
  closing a block (`} command.arg("-v")`) is read at the depth it is reached
  at, so its argument is on every path that reaches it rather than only inside
  the block it leaves. The subcommand is the first argument of the run that is
  neither an option nor an option's value, so `--color always nextest run` runs
  `nextest` and `cargo help build` prints help rather than building; a value
  the reader cannot see fills its option's slot rather than ending the read. A
  conditional word never fills an option's value slot, because the path that
  skips its block gives the next word as the value: `--color`, a conditional
  `never`, then `always` is `--color always` on the path that skips the block.
  A conditional word likewise cannot hide the subcommand on the path that skips
  its block, so a `help` inside an `if` leaves an unconditional `build` still
  compiling, and each arm of a fully paired chain is read for its own
  subcommand, so a `metadata` in the `if` arm does not hide the `build` in its
  `else` arm. A run with no unconditional subcommand at all is compiled when
  any path names one, so a `metadata` in one block beside a `build` in another
  still compiles when only the build's block runs; two conditional words before
  a topic are likewise each read as a possible subcommand, so an `install` in
  one block beside a `search` in another compiles. A `-h` or `--help` given on
  every path prints help whatever the subcommand is, while one added inside a
  block does not excuse the build on the path that skips it. A help flag given
  in the block that also holds a conditional subcommand travels with that
  subcommand, so `if show { command.args(["build", "--help"]); }` compiles
  nothing. A help flag a short circuit can skip
  (`query && command.arg("--help")`) covers nothing, because the path that
  takes the left side gives the word without it, and the subcommand is read
  from the words each path gives rather than from one flattened list, so a
  `metadata` after that block is still read on its own. A help flag in one arm
  excuses only the path through that arm, so a `build --help` in the `if` arm
  does not excuse the `check` its `else` arm runs, while a help flag in every
  arm excuses the whole run; an arm that holds only options is still a path
  when the choice is fully paired, so two such arms excuse each other's sibling
  words. Words after a `--` belong to the program the command runs, not to
  cargo, and a `--` given only on some path is still a separator on that path,
  so a help flag written after it is passed to the program and does not excuse
  the build, whether the separator is unconditional or given in a conditional
  block beside the flag
  (`if show { command.args(["test", "--", "--help"]); }`). A word is the text
  its string literal has at run time, so an escape (`\x64`, `\u{64}`) is
  decoded and cannot hide `build` or `--version` from the read, and an escaped
  quote does not end the literal early. A word counts as unconditional only
  when every block open at it is also open at the run's launch, so a flag in
  the branch that launches is on the run's path while one in a sibling block
  that has already closed is not. An option given in a block of its own
  (`if verbose { command.arg("-v") }`) cannot change the subcommand, so it does
  not stop a paired choice beside it being read arm by arm. A flag added on
  only some paths, behind a `&&`/`||` short circuit or inside a block that may
  not run, does not make a run a probe -- except when one fully paired
  `if`/`else` chain gives a probe in every arm, whether that choice is written
  across statements or on one line (`let _ = if c { .. } else { .. };`), since
  then no path reaches the launch without one. Each arm is read with the words
  given before the branch, so a `--print` in one arm and a `cfg` after the
  choice complete each other; a short circuit inside an arm
  (`query && command.arg("-V").get_args().next().is_some()`) can still skip
  that arm's flag, so the arm does not cover its path. `cargo metadata` and
  `cargo config` do not count;
- a `rustc` spawn counts, unless every run of that same command is given a
  version flag (`--version`, `-V`, `-vV`), a help flag (`-h`, `--help`) or a
  `--print` of a table that needs no compile (`sysroot`, `target-libdir`,
  `host-tuple`, `crate-name`, `cfg`, `target-list`, `target-cpus`,
  `target-features`, `code-models`, `relocation-models`, `tls-models`,
  `file-names`): such a probe exits at once. A kind may name an output file
  after a second `=`, in the joined form (`--print=cfg=out.txt`) or the
  two-word one (`--print`, `cfg=out.txt`), which writes the answer there and
  still compiles nothing. A `--print` query may be split across calls
  (`.arg("--print").arg("cfg")`), which asks the same question as one array,
  but its two halves must be adjacent: a word the reader cannot see between them
  (`.args(["--print", print_kind, "cfg"])`), or one given only inside a block
  (`.arg("--print")`, then `if link { .arg("link-args") }`, then
  `.arg("cfg")`), may be `link-args`, so the query is not a probe. Comments and
  other statements do not count, and `--print=link-args` and
  `--print=native-static-libs` still do, because they build the crate;
- the file must name `Command`, so `env!("CARGO")` read for a path alone is not
  a spawn, and a constructor written in a comment or inside a string is not one
  either, however the file names `Command`;
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
  not name the spawner, and a group of predicates inside one negation
  (`!(binary(ui) | binary(other))`) excludes every name it holds. Negations
  nest and cancel in pairs, so `not(not(binary(ui)))` names `ui` again, and a
  difference (`binary(ui) - test(compiles)`,
  `binary(ui) and not test(compiles)`) excludes its right operand as a negation
  does, leaving `ui` selected. Parentheses are balanced rather than matched
  lazily, and a backslash escapes the next character wherever it sits, so a
  `\(` or `\)` in a matcher neither opens nor closes a group, while a matcher's
  `/regex/` text is skipped as they are counted, because a regex holds brackets
  of its own (`!(test(/foo[)]/) | binary(ui))`). A `/` opens a regex only at
  the first non-space character of a matcher's argument, so a slash in a
  `~contains` argument (`test(~src/foo)`) is data and hides no bracket or name
  beside it. Helpers under `src/` or `tests/support/`, and `main.rs`/`mod.rs`,
  are judged at repository level only.
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
