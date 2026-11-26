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
probe is read both ways. Each entry of a job's `strategy.matrix` (the product
of its axes, with `include` entries merged) is read as a command of its own,
with `${{ matrix.name }}` filled in.

A tool the gate does not run is not required; the rule asks that a tool, once
run, runs over everything.

## What counts as selecting the whole workspace

The member set is the one `cargo metadata --no-deps` reports: the root package,
the directories `workspace.members` selects minus `workspace.exclude`, and the
crates inside the root that a member depends on by path. The commands of one
tool select the whole workspace together when the union of what they select is
that set, less the crates exempted by name (see below). Cargo spells it
`--workspace` or its alias `--all`.

| Command                             | Selects                             |
| ----------------------------------- | ----------------------------------- |
| `--workspace` or `--all`            | Every member, less any `--exclude`. |
| `-p NAME` or `--package NAME`       | The named members.                  |
| Neither, root package and workspace | The root package.                   |
| Neither, with `default-members`     | The default members.                |
| Neither, virtual or single package  | Every member.                       |

For Whitaker, the options before `--` are its own and the options after it are
Cargo's, so only a spelling after the `--` counts. Its own `--all` before the
`--` selects lint libraries, not packages. `cargo test --doc` is a tool of its
own (`test-doc`): doctests are judged apart from unit tests.

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
- an unresolved word that could be part of the package selection (an
  unresolved word beside a spelled-out `--workspace` is read, because it cannot
  narrow it, unless it is the value of `-p`, `--exclude` or `--manifest-path`);
- a command run after a `cd`, or against another manifest;
- a matrix computed by an expression, or a workflow that cannot be decoded;
- a Makefile walk that skipped a target it could not resolve, while some member
  looks uncovered.

A root `Cargo.toml` that cannot be read, or a member manifest without a package
name, is reported the same way.

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
