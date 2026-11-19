# Flag dated toolchain literals that bypass rust-toolchain.toml (#356)

This ExecPlan (execution plan) is a living document. The sections `Purpose`,
`Constraints`, `Decision log`, `Tasks`, `Progress` and `Outcomes` are kept up
to date as work proceeds.

Status: IN PROGRESS

## Purpose

A workflow or Makefile can name a dated nightly literal (`nightly-YYYY-MM-DD`,
or `+nightly-YYYY-MM-DD`) while `rust-toolchain.toml` moves on. Dependabot
bumps the file daily in nightly-pinned repositories, so the literal drifts and
CI silently configures a toolchain the build does not use. wireframe#864 and
mornington#37 are the two observed shapes.

The change adds `RT-028` to the `rust-toolchain-policy` package, starting at
`warning` severity, so the estate can be surveyed before the rule blocks.

## Constraints

- Workflows and Makefiles are parsed (YAML documents, `makeutil` report), not
  matched line by line, so a reformat does not change the verdict.
- Literals compare for dated nightlies only, as RT-027 does: numeric literals
  are usually a host tool's toolchain.
- A literal declared as deliberately separate is not reported: a Makefile
  variable whose value is the literal, and a workflow literal the job or
  document declares through `env`.
- An exemption (the RT-023 shape: a section naming the pinned channel) also
  exempts RT-028.

## Decision log

- Extend the existing `rust-toolchain-policy` package rather than add a new
  one: it already reads the pin and the workflow toolchains, and a second
  package would duplicate that reader.
- Makefile literals are found by expanding `makeutil`'s variable table with
  the existing `makefile_expansion` helper, which captures a top-level
  `NAME ?= nightly-...` declaration, a variable that expands to a literal, and
  an undefined `$(DEV_FAST_TOOLCHAIN)`.
- Workflow literals are read on the raw decoded documents: a later rule
  promotion needs `line` on every finding, which the current `json_safe` facts
  lose.

## Tasks

- [x] Envelope facts (`toolchain_literal_facts.py`) with unit tests.
- [x] RT-028 in the Rego policy with fixture tests.
- [x] BDD scenario through `concordat artefact rule run`.
- [x] README, users' guide, developers' guide, design doc, migration guide.
- [x] Manifest hashes and package version (`0.2.0`).
- [x] Estate survey recorded.
- [ ] Commit gates, CodeRabbit, draft pull request.

## Progress

- Envelope facts landed: `toolchain_literal_facts.py` walks the decoded
  workflows and `makeutil`'s root-`Makefile` report, and reports
  `{reads_pin, literals, errors}`. Sixteen unit tests in
  `tests/unit/test_toolchain_literal_facts.py`.
- `build_toolchain_policy_envelope` carries `literals`; `packages.py` injects
  the observed `makeutil` inspector so the builder never shells out in tests.
- Rego: RT-028 has one `noncompliant` rule and one `indeterminate` rule, both
  gated on `applies` and `has_pin` and the latter also on `not exempt`.
  `literal_severity` is read with `object.get(..., "warning")`, so a partial
  `data.parameters` cannot silently drop the finding.
- Fixtures: `literals` added to all 39, five new ones for the literal shapes.
  The policy suite (24), the BDD suite (9) and the manifest tests pass;
  `make typecheck` passes.
- Documentation updated in the package README, users' guide, developers'
  guide, design doc, migration guide and roadmap.
- CodeRabbit's review of the draft pull request raised thirteen concerns, all
  actioned: a `run` script is now split into shell commands so a quoted date is
  an argument rather than a selection; `${{ matrix.NAME }}` toolchains are
  resolved over every leg; an exempt exception suppresses RT-028's
  indeterminate finding too; the Makefile probe's refusal and a symlink that
  leaves the checkout are reported as errors; a recovered `makeutil` parse is
  reported rather than read as clean; the pin is observed once, by the
  envelope; a Makefile command that is itself a variable is expanded; and the
  survey column reads "Would fail at error". The shell-command reader now lives
  in `toolchain_literal_shell.py`, which keeps `toolchain_literal_facts` under
  pylint's module-length limit.
- A second review, on the refactored head, raised five more, all actioned: a
  here-document body is skipped rather than read as commands; an
  `export RUSTUP_TOOLCHAIN=…` selects; a matrix `include` leg is no longer
  replaced by an axis of the same key; a Make recipe prefix no longer hides a
  `RUSTUP_TOOLCHAIN=` assignment; and an expanded command variable is tokenized
  as a shell command, so a quoted override and several commands in one variable
  are read. Six regression tests cover them.
- A third review raised five more, all actioned: a wrapper option's operand is
  not the program (`env -u RUSTUP_TOOLCHAIN cargo +nightly-...`), an assignment
  after a shell keyword is still read (`if RUSTUP_TOOLCHAIN=... cargo test`), a
  command on a here-document's declaration line still runs while only the body
  is skipped, `<<<` is a here string rather than a here-document, and a recipe
  prefix no longer hides an assignment before a command variable
  (`@RUSTUP_TOOLCHAIN=stable $(CHECK)`). Five regression tests cover them.
- A fourth review raised five more, all actioned: a backslash-escaped `"` no
  longer closes a double-quoted word; a command substitution is read as the
  command it runs, even inside double quotes; wrapper option arities follow the
  wrapper, so `sudo -n cargo` is read while `xargs -n 1 cargo` still consumes
  its operand; `cat<<EOF` opens a here-document without spaces around the
  operator; and `program_position` unwraps wrappers, so `env $(CHECK)` expands
  the command the variable names. Five regression tests cover them.
- A fifth review raised four more, all actioned: a parenthesized group runs
  its contents; a backquoted substitution runs its command; a word split by a
  backslash-newline is joined rather than broken; and an unquoted here-document
  body is read for the substitutions it expands, while a quoted delimiter keeps
  them literal. Five regression tests cover them.

## Lessons

- The workflow action input is not a shell command: `with.toolchain` and the
  `uses` ref must be read as values, not fed to `command_selections`. The first
  unit-test failure was exactly that.
- Declaration is per referenced name. A set of every variable that holds a
  date wrongly excused `A ?= nightly-...` / `B=$(A)` / `cargo +$(B)`: the
  recipe writes `B`, which is not assigned the date, so it is reported at its
  expanded value.
- `makeutil` reads recipes as written, so the expansion helper is what turns
  `$(NAME)` into the literal; `shown()` restores unresolved placeholders so a
  reference with no assignment and no includes is reported as written.
- Ruff rejects mixed tabs and spaces in Python literals, so the Makefile test
  fixtures spell tabs as `"\t"`.
- The pinned `ty` in `make typecheck` (0.0.65) is the authority; a newer local
  `uv run ty` reports unrelated pre-existing diagnostics.
- A token split on whitespace cannot tell a quoted date from a selection, and
  a matrix toolchain has no text until the matrix is read. Both were found by
  reading the rule against the workflows it is meant to judge, not by the tests
  that shipped with the first draft.
- A `dict` literal assigned to a name typed as a TypedDict is a `dict[str, str]`
  to `ty`; construct the TypedDict explicitly (`LiteralError(path=..., ...)`).
- Reading shell text is a state machine, not a series of string replacements.
  Every review round found another construct — quoting, a command's argument
  position, a here-document body, a recipe prefix — that a simpler reader
  mistook. Splitting the scanning state into `_Scan` keeps the character loop
  within pylint's branch limit without hiding the rules it applies.
- A here-document body starts on the line *after* its declaration, so the
  skip must run at the newline and not at every separator:
  `cat <<EOF; cargo +nightly-… test` still runs `cargo`, and the body after it
  is data.
- A wrapper's operand looks like a program to a reader that only unwraps
  options: `env -u RUSTUP_TOOLCHAIN cargo ...` must consume `-u`'s operand
  before it decides what the program is. Each wrapper has its own arities,
  though, so one shared table reads `sudo -n cargo ...` as `sudo -n` taking
  `cargo` as an operand.
- Quoting protects text from the shell, not from the reader: `$(...)` executes
  inside double quotes, and `\"` inside them is not the closing quote.
  Reconstructing a word after the quotes are gone cannot tell either case, so
  both are decided while the scanner still knows the quote state.
- Skipping a region is not the same as ignoring it: a here-document body is
  not commands, but an unquoted delimiter still expands the substitutions in
  it, so the body is read for those while its lines stay data. The same
  distinction decides whether a word is split: a backslash-newline joins its
  two halves, a space does not.

## Estate survey (2026-11-19)

`uv run python -m scripts.parabellum_sweep --package rust-toolchain-policy`
audited all 52 repositories of `docs/parabellum/estate.yaml` at their
default-branch heads, and
`... report --package rust-toolchain-policy --output docs/parabellum/rust-toolchain-policy-survey.md`
rendered the survey; the records sit in the campaign ledger beside the
`rust-makefile-baseline` campaign.

| Rule   | Severity | Repositories | Findings |
| ------ | -------- | ------------ | -------- |
| RT-022 | error    | 2            | 2        |
| RT-025 | warning  | 22           | 22       |
| RT-026 | error    | 7            | 7        |
| RT-027 | error    | 2            | 2        |
| RT-028 | warning  | 2            | 2        |

RT-028 fires twice, both on a literal written inline in
`.github/workflows/ci.yml`:

- gauss selects `nightly-2026-08-07` while the pin is `1.99.1`.
- rstest-bdd selects `nightly-2026-08-07` while the pin is `stable` (which is
  itself RT-022).

Both are also RT-027 findings, because CI resolved a date the pin does not
name. That overlap is expected: RT-027 catches the drift that is already
visible, and RT-028 catches the literal that will drift as soon as the pin
moves. No repository that RT-027 passes draws RT-028, so promoting the clause to
`error` after these two literals read the pin adds no new blockers beyond the
RT-027 failures they already carry.

cuprum is RT-022 (no root `rust-toolchain.toml`) rather than an RT-028 case:
its fast toolchain lives below the `rust/` directory the rule declines to
judge, so no literal reached the reader. Its declared-variable shape is covered
by the unit and BDD tests instead. mornington is not in the estate manifest, so
its `release.yml` shape could not be surveyed here.
