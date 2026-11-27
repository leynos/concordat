# gate-workspace-coverage

Audits that every package-selecting command a Rust repository's gate runs
selects every workspace member. The audit is read-only. It evaluates a
`policy-input/gate-workspace-coverage` envelope built by
`concordat artefact rule run`.

## Why

Four tools narrow to less than a workspace without saying so: `cargo clippy`,
`cargo nextest run`, `cargo test` (with and without `--doc`) and Whitaker. In a
workspace whose root is also a package, a bare command selects the root package
alone. `workspace.default-members` and a `-p` list narrow it further. The gate
reads green over the members it selected and says nothing of the rest. Whitaker
skipped every non-root member of the estate's workspaces for months this way,
and the macro-aware complexity survey shared the blind spot.

The rule is the standing form of the lesson: a tool is rolled out when it
checks the whole workspace, which is shown by comparing what the gate selects
with the members Cargo reports, not by the gate passing.

## What counts as the gate

The gate is the Makefile's `lint` and `test` targets, followed through their
prerequisites and `make name` commands, and every `run` step of every workflow
(`.github/workflows`) and composite action (`.github/actions`). Make variables
are expanded; an `$(if $(shell cargo nextest --version),nextest run,test)`
probe is read both ways. Each entry of a job's `strategy.matrix` is read as a
command of its own, with `${{ matrix.name }}` filled in. The entries are
GitHub's: the product of the axes, less the `exclude` entries, with each
`include` entry added to every original combination it does not contradict (a
later `include` may overwrite what an earlier one added) or standing as a
combination of its own when it fits none.

A workflow step or job whose `if` is the constant false is not a command. A
condition that reads the matrix (`if: matrix.package == 'app'`) makes the step
run for some combinations only, so its commands are indeterminate. Any other
condition is taken to hold on some run: the rule judges the commands a gate
has, not the schedule they run on. A Makefile recipe inside an `ifeq` or
similar block, and the commands of a prerequisite that only such a block adds,
are indeterminate too, because whether they apply depends on Make state the
audit does not evaluate. A probe `if` yields two readings of one line, and one
run takes one of them. A tool a probe runs in some readings but not all, or
runs differently in them (`-p a` against `-p b`, or `test -p b` against
`nextest run -p b`), is left to the probe: every invocation of it in the
Makefile is indeterminate. The exception is a tool whose every invocation is a
probe's and selects the whole workspace (a probe that only picks the runner,
`nextest run` or `test`, with `--workspace`) and that no other command of the
gate, in CI, also runs: it covers everything whenever it runs, and a tool that
does not run is not required. In a shell script, the constant conditions `true`,
`:` and `false` are known, across however many lines they span, along with
their `elif` and `else` branches: the branches that cannot run are not
commands. Any other condition is taken to hold on some run when it has no
`elif` or `else`; one that has is a fork, where one run takes one branch, so
every command inside it is indeterminate. Under `.ONESHELL` a recipe is one
shell, so a `cd` on one line moves the lines after it, and a line that cannot
tell which earlier probe reading ran starts as moved if any did.

A tool the gate does not run is not required; the rule asks that a tool, once
run, runs over everything.

## What counts as selecting the whole workspace

The member set is the one `cargo metadata --no-deps` reports: the root package,
the directories `workspace.members` selects minus `workspace.exclude`, and the
crates inside the root that a member depends on by path. The commands of one
tool select the whole workspace together when the union of what they select is
that set, less the crates exempted by name (see below). Cargo spells it
`--workspace` or its alias `--all`. A `-p` or `--exclude` selector may be a
glob pattern (`internal-*`), which names every member it matches, and a version
suffix (`name@1.2.3`) is dropped.

| Command                             | Selects                             |
| ----------------------------------- | ----------------------------------- |
| `--workspace` or `--all`            | Every member, less any `--exclude`. |
| `-p NAME` or `--package NAME`       | The named members.                  |
| Neither, root package and workspace | The root package.                   |
| Neither, with `default-members`     | The default members.                |
| Neither, virtual or single package  | Every member.                       |

For Whitaker, `--all` and `--lib NAME` are its own and select lint libraries,
not packages. Every other option, before or after the `--`, goes to Cargo
(measured: `whitaker --all -p app --` checks only `app`, and
`whitaker --all --workspace --` checks every member), so `--workspace` counts
on either side of the separator and `--all` does not. `cargo test --doc` is a
tool of its own (`test-doc`): doctests are judged apart from unit tests.

A command that narrows is not a finding while another command of the same tool
covers the rest. The `whitaker` repository lints every crate with one command
and the two crates that keep deliberately violating fixtures with another.

## Findings

| Id     | Meaning                                                                     |
| ------ | --------------------------------------------------------------------------- |
| GC-001 | The commands of a tool leave workspace members out.                         |
| EN-001 | A gate command, file or workspace could not be read, so nothing is guessed. |

GC-001 names the members left out, the commands that narrow, and the fenced
examples in `AGENTS.md` and `docs/` that show the same narrowing, so the fix
updates what contributors copy.

### Indeterminate, never guessed

A tool is reported as indeterminate, and not judged, when the audit cannot
place one of its commands:

- a wrapper script (a `.py`, `.sh` or `.bash` file named for a gate tool) that
  selects the packages itself;
- an unresolved word among the Cargo arguments, even beside a spelled-out
  `--workspace`, because it may stand for `--exclude b`, and even after an
  option such as `--profile` or `--color`: a Make reference is substituted as
  text and an unquoted shell variable splits into words, so neither is one
  value. Whitaker's own `--all` and `--lib NAME` are no selection, but an
  unresolved word after `--lib` is not trusted to be one name. A Whitaker
  command with no `--` and an unresolved word may be given the separator by it;
- a command word, or Cargo subcommand, left unresolved beside words only a gate
  tool takes, even when another command on the same line was read;
- unreadable input anywhere in the gate: no tool is called uncovered while a
  file or line that might hold its covering command could not be read;
- a command run after a `cd` (in the same line or an earlier line of one CI
  step), in a step or job with a `working-directory`, under Cargo's global
  `-C`, or against a manifest other than the root one;
- a matrix with an axis computed by an expression, or a workflow that cannot
  be decoded;
- a Makefile walk that skipped a target it could not resolve, while some member
  looks uncovered.

A root `Cargo.toml` that cannot be read, or a member manifest without a package
name, is reported the same way.

### Repositories that stay indeterminate by design

Each of these gates holds a command the audit refuses to guess at, for the
reason given. Reading them would mean evaluating Make, GitHub expressions or
another directory's workspace, so the rule reports `EN-001` and a person checks
the gate by hand. The reasons name the construct; they are not defects in the
repositories.

| Repository  | Why the audit stays indeterminate                                                                                                              |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| axinite     | A tool is given a `--manifest-path` outside the root manifest, and its matrices are expressions.                                               |
| chutoro     | The nextest steps take their flags from variables, and the matrices are expressions.                                                           |
| combobulate | The profile-measurements job runs in another directory, so its workspace is not the root one.                                                  |
| ddlint      | The Makefile walk skips a target it cannot resolve, while some member looks uncovered.                                                         |
| evert       | The Makefile wraps each cargo command in a `define` function (`$(call ...)`) that the audit does not evaluate.                                 |
| netsuke     | A step runs `cd test_support` first, and another uses a PowerShell `@suite` splat.                                                             |
| whitaker    | The test recipe has unresolved references (`$(HOME)`) in quoted strings that hold parentheses, and one could close a string and run a command. |
| wildside    | The gate uses `make -C` and `cd`, and the nextest word is a variable.                                                                          |

## Exemptions

`exemptions` names a workspace member the gate may leave out: its repository
(`owner/name`), the crate, the tools it applies to (all when absent), a
rationale and a review trigger. The policy ignores an entry that leaves the
rationale or the review trigger blank, because an exemption nobody must revisit
is not a record. Example, for a crate the suite's pinned toolchain cannot
compile:

```yaml
exemptions:
  - repository: leynos/wildside-engine
    crate: wildside-scorer
    tools: [whitaker]
    rationale: >-
      The suite's pinned nightly cannot compile a const fn this crate needs.
    review_trigger: review when the suite's nightly moves
```

## Severity

The rule ships at `warning` severity so the estate can be surveyed before it
blocks. The `severity` parameter raises GC-001 and the indeterminate findings to
`error` without a code change. An envelope defect is always `error`.

## Scope

Only the root workspace is audited. A repository with no root `Cargo.toml` is
not. A repository that builds in Netsuke's `Netsukefile` rather than a Makefile
has no gate this rule reads (RFC-0004).

## Fixtures

`fixtures/generate.py` lays out each scenario as a synthetic checkout and
records the production envelope, so the Rego suite runs on the facts the
command would send. Regenerate with
`uv run python platform-standards/canon/lint-rules/gate-workspace-coverage/fixtures/generate.py`.
