# rust-prover-tooling

Audits how a repository that uses Kani or Verus obtains its provers, and that
CI runs the proofs it holds. The audit is read-only. It evaluates a
`policy-input/rust-prover-tooling` envelope built by
`concordat artefact rule run`; it never installs or runs a prover.

## Why

Each repository once grew its own way to install a prover: a Make target, a
workflow step, a Python script that downloads a release. They drifted apart,
and each carried its own cache and checksum logic. The estate now has one tool,
`rust-prover-tools`, and one CI action that installs and caches its provers,
`leynos/shared-actions/.github/actions/install-prover-tools`. The rule holds a
repository to using them.

## What counts as using a prover

A repository is audited when it holds proof code, has a `tools/kani/VERSION` or
`tools/verus/VERSION` pin, names a `rust-prover-tools` revision, or has a
Makefile recipe, workflow step or script that installs or runs a prover. Proof
code is itself a reason to audit: a repository whose proofs nothing runs used
to read as "no prover in use", which let it pass. That is the failure PT-006
names, because proof code that nothing runs is a comment.

## Proof code

Proof code is read from the Rust sources with tree-sitter, never a text search,
so a `#[kani::proof]` in a comment, a string, a doc example or a snapshot is
not proof code, and `use kani::proof as p; #[p]` is. Every `.rs` file outside
`tests`, `test`, `fixtures`, `examples`, `target` and similar trees is parsed
(the trees PT-004 skips), and each file reports at most one entry per kind:

- `kani-harness`: an attribute that resolves to `kani::proof` or
  `kani::proof_for_contract`, through the file's `use` declarations, or a
  `cfg_attr` that names one. A `cfg(kani)` helper module with no harness (for
  example, support code that harnesses import) is not proof code.
- `verus-block`: a `verus!` or `verus_!` macro with a token-tree body, a
  `use vstd::...` declaration, or a `#[verifier::...]` attribute.
- `proof-generator`: a `quote!` or `quote_spanned!` in a proc-macro crate (its
  nearest `Cargo.toml` has `[lib] proc-macro = true`) whose tokens emit
  `#[kani::proof]` or `verus!`. A generator writes harnesses that no ordinary
  build of its own crate exercises, so the generator is proof code. Whether a
  consumer in another repository runs them is out of scope: that needs the
  dependency graph.

A file that cannot be read, or whose syntax error spans text naming a prover
(`kani`, `verus`, `vstd` or `verifier`), is reported as unreadable, which makes
the repository audited and raises EN-001: an unread file is not evidence of
absence. A syntax error elsewhere in a file is ignored, because syntax the
parser predates cannot hide a proof.

## Installer or runner

The facts come from parsers, never a text search. Shell and recipe text is
split into simple commands with `shlex`, and each command's argument vector is
classified:

- an **installer** is `<tool> install` (for `kani`, `verus` or `z3`) under any
  wrapper, `cargo install` or `cargo binstall` of `kani-verifier`, or
  `cargo kani setup`;
- a **runner** is the `verus` binary, `cargo kani`, or
  `prover-tools <tool> run|exec|check-version`.

A command is read as the estate runner only when it names `prover-tools` or
starts with a Make variable standing for it, so `git commit -m 'verus install'`
is not an installer. Leading `env`, `command`, `exec` and similar wrappers are
looked through (including options that take an argument, as in `nice -n 10`). A
`#` begins a comment only at the start of a word, an operator inside quotes
does not split a command, and the body of a here-document is not a command.

A step that only runs proofs on an installed toolchain is therefore compliant,
and `printf 'prover-tools verus install'` is not an installer. A workflow step
that runs `make X` (or a bare `make`, which builds `.DEFAULT_GOAL` or the first
target) is an installer when `X`, or any prerequisite of `X` in the pinned
`makeutil` report, runs an installer or a local installer script: that is how
`make verus` depending on `verus-install` reads, and how a run-only
`make verus` does not. The installer and runner checks read recipes as written:
a Make variable reference is masked, so a recipe that hides the tool name
inside a variable is not seen there.

## Running proofs (PT-006)

PT-006 asks a stricter question than "is a prover used": do the proofs run? A
**proof run** is `cargo kani`, `cargo-kani` or `kani-driver` (not their `setup`,
`list`, version and help forms), `prover-tools kani exec`,
`prover-tools verus run`, the `verus` binary on a Rust proof file, or
`cargo verus verify|focus|build`. Installing a prover and
`prover-tools ... check-version` do not count, and a command that only prints
text is never one.

A workflow step runs proofs when it:

- runs such a command itself;
- runs `make X` where `X`, or a prerequisite, has a recipe that does. For this
  check the Makefile's own variables are expanded (a few levels deep, from the
  `makeutil` report), so a recipe that runs `$(VERUS_RUN) --proof-file x.rs`
  runs what `VERUS_RUN` stands for. A built-in (`$(CURDIR)`) or function
  (`$(shell ...)`) stays masked;
- runs a repository script whose commands include one, read from shell commands
  and, for Python scripts, from `ast` list literals (`["cargo", "kani", ...]`)
  and from a command string handed to a process call (`subprocess.run`,
  `os.system`); a string that is only printed is not a run. A script that runs
  another script runs what that script runs; or
- sits in a workflow that installs through the shared action and runs
  `make kani*` or `make verus*` whose recipe does real work (more than
  printing, installing or checking a version). A proof generator's harnesses
  are verified inside a test suite, so no recipe names `cargo kani`; this is
  the shape theoremc uses. `cargo kani list` and `setup` do no work, so a
  target that holds only those does not count.

A run counts for the proof code of the same prover: a Kani harness needs a Kani
run and a Verus block a Verus run, so a repository whose only run is Verus does
not pass for its Kani harnesses. A generator is satisfied by either. A `make`
with `-n`, `-q` or `-t` prints, tests or touches; it does not run.

## Checks

- **PT-001** (error): every `rust-prover-tools` revision the checkout names
  (`tools/rust-prover-tools/REF`, a literal `rust-prover-tools@<ref>` in a
  Makefile variable or recipe, a workflow command or a shell script) is the
  same commit, and equals the `prover_tools_ref` parameter. The reference is
  read whole, so a branch name or `<sha>-patched` fails; a `$(shell ...)`
  reference is not a literal, and the REF file is the pin there. No revision at
  all is also noncompliant. Bumping the estate's pin is a change to the
  parameter's default.
- **PT-002** (error): no workflow step installs a prover itself: neither a `run`
  command, a `make` target whose prerequisites install, nor a step that runs a
  local installer script (so waiving PT-004 for the script does not clear the
  step). Install through the shared action.
- **PT-003** (error): every use of `install-prover-tools` is pinned by a full
  40-digit commit SHA. Not waivable.
- **PT-004** (error): no local installer script. A file under `scripts/`,
  `.github/scripts/` or `tools/` whose name pairs `install`, `setup` or
  `bootstrap` with `kani`, `verus` or `z3`; a shell script containing an
  installer command; or a Python script holding a string constant that begins
  with a Kani or Verus release-download URL (read from the `ast`, so a comment
  does not count, and a homepage or issue link does not either). Scripts under
  `tests/`, `test/` and `fixtures/` directories are not read.
- **PT-005** (error): no integration tests of prover installation, found by
  file name only: a file under `tests/` whose name pairs `install` with `kani`,
  `verus`, `z3` or `prover`. The installer is owned and tested in
  `rust-prover-tools`. Not waivable.
- **PT-006** (error): proof code is run. A repository that holds proof code has
  at least one workflow step that runs the proofs (see above). Not waivable:
  the only remedies are to run the proofs or to remove the code. It reports the
  first proof-code file and its kind. When a workflow could not be read, the
  Makefile could not be parsed, or a `make` step builds another directory's
  Makefile, whether CI runs the proofs is unknown and the finding is EN-001
  (indeterminate), not PT-006.
- **EX-001** (error): a recorded exception is not in force (see below).
- **EN-001** (error, indeterminate): the envelope is not a schema-1
  `policy-input/rust-prover-tooling` document; a workflow could not be decoded;
  the Makefile could not be parsed (its pins are unknown, and so is what a
  workflow's `make` steps install); a `make` step builds a Makefile other than
  the root one; or a file the rule reads could not be read. A failed read makes
  the repository audited for this finding even when nothing else shows it uses
  a prover, because an unread file is not evidence of absence.

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
parameter; PT-003, PT-005 and PT-006 are never waivable) only while it is in
force: every revision the checkout names starts with `pin` (at least eight
digits), the `record` is a file in the checkout, the rationale and review
trigger are not blank, and every clause is waivable. Moving the pin therefore
reopens the question rather than leaving a stale waiver. An exception that is
not in force waives nothing and is reported as EX-001.

A script is matched against the local installers from the directory its step
runs in (a `working-directory` or a `cd`), and a leading `NAME=value` is not
the command. A command a wrapper moves (`env -C dir ...`) runs from a directory
the rule cannot resolve.

A `make` step uses the root Makefile only when it runs from the repository
root: a step with a `working-directory` (its own, or from `defaults.run`), or a
`make` after a `cd` in the same script, builds another directory's Makefile,
which the rule does not read. The operands of Make options (`make -j 2`,
`make -C dir`) are not goals.

## What the rule declines to judge

Only root workflows (`.github/workflows/*.yml`) are read; composite actions are
not. A Make recipe that reaches an installer only through a variable is not
seen by PT-002, and PT-006 expands only variables the Makefile itself assigns.
A script that builds its command from variables, or runs proofs through a
program the rule does not know, is not seen as running them. A variable given
on the `make` command line (`make verify RUN=echo`) is not applied to the
expansion. Imports are resolved per file, not per module, so an alias imported
in one module also resolves in another module of the same file, and any
`<path>::verus!` is read as a Verus block. A proof generator is detected only
in a proc-macro crate; a repository that consumes someone else's generator is
not judged. Whether a repository's proofs are sound is not asked.
