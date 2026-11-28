# gate-lint-coverage

Audits what a Rust repository's lint gate checks: which targets, which
features, whether warnings are denied, and whether `cargo doc` is a gate. The
audit is read-only. It evaluates a `policy-input/gate-lint-coverage` envelope
built by `concordat artefact rule run`.

## Why

`gate-workspace-coverage` asks which packages a gate's commands select. A gate
can select every package and still look at little of it. femtologging's gate
read green with no `cargo doc` anywhere, Clippy lanes listed in a script, and
nothing enforcing that the lanes cover every feature or every target
([femtologging#421](https://github.com/leynos/femtologging/issues/421)). The
rule is the standing form of that lesson, across the three shapes a Rust
repository takes:

| Shape                               | Surface                              | How a command reaches it                      |
| ----------------------------------- | ------------------------------------ | --------------------------------------------- |
| Workspace designed as one           | The workspace root                   | `--workspace`, or no manifest named           |
| Workspace grown from a single crate | The workspace root, root package too | A bare command selects the root package alone |
| Rust extension in a Python project  | `rust_extension/Cargo.toml`          | `--manifest-path`, or `cd rust_extension &&`  |

A **surface** is a workspace root with its members, or a package outside every
workspace. Membership is the one `rust-lint-tables` and
`gate-workspace-coverage` use.

## What counts as the gate

The Makefile's `lint` target followed through its prerequisites and `make name`
commands, and every `run` step of every workflow and composite action (each
matrix entry read as a command of its own). The shell is read as
`gate-workspace-coverage` reads it: a command in a branch that cannot run is
not a command, a `cd` in the same shell moves the directory, and `.ONESHELL`
makes a rule's lines one shell. A command is a lint run when it is
`cargo clippy`, `cargo doc`, `whitaker` or `cargo dylint`. A tool the gate does
not run on a surface is not required (DG-004 asks that `make lint` reaches
Clippy at all).

## Findings

| Id     | Meaning                                                                                                                                  |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| LC-001 | No Clippy (or Whitaker) command on a surface checks all targets.                                                                         |
| LC-002 | The features the tool's commands check do not cover the default set and every declared feature.                                          |
| LC-003 | A command does not deny warnings.                                                                                                        |
| LC-004 | `make lint` does not reach `cargo doc --no-deps` over the whole of a surface that holds a library, with `RUSTDOCFLAGS` denying warnings. |
| EN-001 | A command, file or lane list could not be read or placed. The tool on that surface is not judged.                                        |

- **All targets** is `--all-targets`, or all of
  `--lib --bins --tests --benches --examples`. One command on the surface that
  checks all targets is enough.
- **Features**: an `--all-features` run covers every feature and the default
  set; otherwise the union of the `--features`/`-F` lanes must name every
  feature declared in the surface's `[features]` tables (`default` apart), and
  for each package that declares a `default` feature, some lane that selects
  the package must leave its default set on (`--features default` asks for it
  by name). In a workspace of several packages a feature is `package/feature`,
  and `-F a/fast` covers `a`'s `fast` only; a bare `-F fast` covers it in every
  package that declares it, except under resolver 1 (a virtual workspace with no
  `resolver`, or a root package before edition 2021), where it applies to the
  one package the command runs in. In a surface of one package `-F pkg/fast` is
  `fast`. A run covers the features of the packages Cargo selects for it:
  `-p a` names them, `--workspace` takes every package less `--exclude`, a
  command at a member's manifest selects that member (`-p` and `--exclude` take
  glob patterns such as `b*`), and a bare command at the root selects the
  `default-members` (the root package of a workspace that has one, every member
  of a virtual workspace). A `default-members` pattern is not expanded, so a
  bare run there is EN-001.
- **Warnings** are denied by `-D warnings` in `RUSTFLAGS` on the command or
  exported by the Makefile, after `--` for Clippy, or in `.cargo/config.toml`
  `build.rustflags`; `cargo doc` needs `RUSTDOCFLAGS` (rustdoc does not read
  `RUSTFLAGS`). The last level flag for `warnings` decides, so a later
  `-W warnings` undoes an earlier `-D warnings`. A command that assigns the
  variable without the deny has said it does not deny. What a Makefile exports
  reaches the commands its recipes run, not a workflow step that runs `cargo`
  directly. A workflow's own `env` is not read, and it can override
  `.cargo/config.toml`, so a workflow command that says nothing is reported as
  EN-001, not as a violation and not as clean; an exported value the audit
  cannot expand is the same. The last-flag rule applies to exported values too.
- **The rustdoc gate** is judged on the Makefile's `lint` closure. It needs
  `--no-deps`, a selection of every package (`--workspace`, or every package
  named, or a surface of one package) with no `--exclude`, `--bin(s)` or
  `--example(s)`, and `RUSTDOCFLAGS` denying warnings.

Each finding names the surface and the commands, and says what to add.

## Indeterminate, never guessed

A lane list built by a script
(`INPUT_LANES=… uv run --script scripts/lint_rust_lanes.py`) is reported as
EN-001 with the lane inputs it was given: the audit does not read the script,
so it cannot say what targets, features and flags its lanes use. Other causes
are an unresolved word among the arguments (it may stand for a flag), a command
after a `cd` or against a manifest the audit could not place, a manifest no
surface holds, a step that runs for some matrix entries only, a command inside
a shell `if` the audit cannot settle (it may be one of several exclusive lanes,
and a `cd` inside it leaves the commands after it unplaced), a job whose matrix
is computed by an expression and whose steps may lint (a tool name, `make`, or
a script), a Makefile walk that skipped a target (`make -C`), and any
unreadable manifest, workflow or line. These are reported even when the
checkout holds no Cargo surface.

A script is recognized by running it (`python x.py`, `uv run --script x.py`,
`bash x.sh`, `./x.sh`) with its text showing `cargo clippy` (as a shell line or
as the words of a subprocess list) or `whitaker`/`cargo dylint`. A tool that
merely names a script (`ruff format x.py`, `python -m pytest test_x.py`) runs
none of its lanes.

### Measuring a script-built lane list independently

The rule does not interpret the script, because a reader that replays the gate
inherits its blind spots. Three routes measure what the lanes check:

1. A dry run: `make -n lint` (or the script's own dry-run option) prints the
   Cargo command lines, which can be compared with LC-001 to LC-003.
2. The Cargo JSON route: run Clippy per lane with `--message-format json` and
   compare the compiled targets and the `feature="..."` cfgs of each lane with
   `cargo metadata --no-deps`.
3. A convention for the script: print its lanes, one per line, with a flag such
   as `--print-lanes`, so the rule can verify them. This is a proposal for the
   user, not built.

## Parameters

- `severity` (`warning` by default): the severity of every LC finding and of the
  indeterminate findings. An envelope defect is always `error`.
- `exemptions`: surfaces whose Clippy or Whitaker lanes may not check all
  targets (LC-001), such as a lane that must leave out the fixture crates that
  break the lint on purpose. Each names `repository`, `surface` (the manifest),
  `tool`, a `reason` and a `review_trigger`; the policy ignores an entry with a
  blank reason or trigger.

## Severity

The rule ships at `warning` severity so the estate can be surveyed before it
blocks. The `severity` parameter raises the findings to `error` without a code
change.

## Fixtures

`fixtures/generate.py` lays out each scenario as a synthetic checkout and
records the production envelope, so the Rego suite runs on the facts the
command would send. The scenarios cover the three shapes, each with clean
variants, the defects above, indeterminate cases and unreadable inputs.
Regenerate with
`uv run python platform-standards/canon/lint-rules/gate-lint-coverage/fixtures/generate.py`
and run the suite with:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/gate-lint-coverage/policy \
  --data platform-standards/canon/lint-rules/gate-lint-coverage/fixtures/data.json
```
