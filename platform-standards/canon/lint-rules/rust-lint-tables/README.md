# rust-lint-tables

Audits that every Cargo package a repository owns compiles under the estate's
Rust lint baseline: the Clippy, rustc and rustdoc tables. The audit is
read-only. It evaluates a `policy-input/rust-lint-tables` envelope built by
`concordat artefact rule run`, with the baseline loaded beside the rule's
parameters as `data.baseline`.

## Why

A Rust package can pass every gate while compiling under almost no lints.
femtologging's extension kept two Clippy lints and two rustc lints beside
netsuke's forty-odd, with no rustdoc table, no `unknown_lints`, no
`missing_assert_message` and no `unsafe_code = "forbid"`; nothing flagged it
because nothing compared a manifest with the baseline
([femtologging#420](https://github.com/leynos/femtologging/issues/420)). The
shape of the repository decides how the tables reach a package, and each shape
can lose them differently:

| Shape                               | How a package gets its tables                  | How it loses them                                     |
| ----------------------------------- | ---------------------------------------------- | ----------------------------------------------------- |
| Workspace designed as one           | `[workspace.lints]`, members opt in            | A member that does not set `[lints] workspace = true` |
| Workspace grown from a single crate | Root package's own `[lints.*]`, then members   | Members added later have none; the root never opts in |
| Rust extension in a Python project  | Own `[lints.*]` in `rust_extension/Cargo.toml` | The crate has a thin table, or none                   |

The rule resolves what Cargo does and compares the result with the baseline, so
the same finding codes cover all three.

## The baseline

The baseline is data, in [`data/`](data/):

- `baseline.toml` is paste-ready. A workspace copies its
  `[workspace.lints.*]` tables into the root manifest and adds
  `[lints] workspace = true` to every member, the root package included. A
  package outside any workspace copies the same tables as `[lints.*]`.
- `extras.toml` holds what a paste cannot say: the cognitive-complexity
  alternative and the PyO3 divergences.
- `baseline.json` is derived from the two by `data/generate.py`. The rule and
  the root lint policy read it; a unit test fails when it is stale.

A repository may be stricter than a level, and `forbid` satisfies `deny`. The
level is what the rule checks: `check-cfg` under `unexpected_cfgs` is the
repository's own list, and `pedantic` must keep priority -1.

### Updating the baseline

1. Edit `data/baseline.toml` (and `extras.toml` if needed) in a pull request.
2. Run
   `uv run python platform-standards/canon/lint-rules/rust-lint-tables/data/generate.py`
   and `fixtures/generate.py`.
3. Run the estate survey with the changed baseline and put the count per code
   in the pull request body. A lint that fails a large part of the estate needs
   a rollout decision, not only a merge.
4. New entries ship at `warning`. Raising the severity, or fixing repositories,
   is a separate decision.

A toolchain bump that renames or removes a lint is a baseline change:
`unknown_lints` and `renamed_and_removed_lints` are in the baseline so the
build says so.

### PyO3 divergences

The canon defines the divergences every PyO3 extension needs once, in
`extras.toml`'s `pyo3_divergences` list (`lint`, `level`, `reason`,
`evidence`), so a repository never invents its own. The list is empty because
measurement shows none are needed at table level: cuprum, stilyagi, tei-py and
femtologging's lint branch carry the full tables, with `unsafe_code = "forbid"`
where they set it, over PyO3 0.2x and 0.30. The friction PyO3 does cause,
`clippy::too_many_arguments` on `#[pyfunction]` wrappers, is handled at the
item with `#[expect(..., reason = "...")]`, which no manifest rule reads. A
package depends on PyO3 when any dependency table names `pyo3`, directly or
renamed.

## What counts as a package

Every `Cargo.toml` with a `[package]` table outside the fixture, vendored and
build-output trees (`fixtures`, `vendor`, `third_party`, `patches`, `templates`,
`examples`, `target`, and the trees every envelope prunes). A virtual
workspace root is not a package. Membership is Cargo's, applied to each
workspace root, not only the checkout's root: the same member rules as
`gate-workspace-coverage`. A crate no workspace lists is standalone.
`default-members` does not narrow the audit: every member is governed, whatever
a bare command selects.

How a package's levels are resolved:

| State          | Meaning                                                                                    |
| -------------- | ------------------------------------------------------------------------------------------ |
| `inherited`    | `[lints] workspace = true` under a workspace that has `[workspace.lints]`                  |
| `own`          | The package's own `[lints.*]` tables apply                                                 |
| `dangling`     | `[lints] workspace = true` with no `[workspace.lints]` above it                            |
| `not_opted_in` | Its workspace has `[workspace.lints]`; the package has no tables and no `workspace = true` |
| `none`         | No lint tables apply                                                                       |

## Findings

| Id     | Meaning                                                                                                          |
| ------ | ---------------------------------------------------------------------------------------------------------------- |
| LT-001 | A package has no lint tables that apply to it (`none`, `dangling` or `not_opted_in`).                            |
| LT-002 | Its effective tables miss or weaken a baseline entry.                                                            |
| LT-003 | `unsafe_code` is not `forbid`, and the package is not a named boundary crate.                                    |
| LT-004 | A boundary crate does not set `deny`, has no Miri command the gate reaches, names no ADR, or names no package.   |
| EN-001 | A manifest or configuration file could not be read, or the gate could not be read where an answer depends on it. |

Each finding names the package and its manifest and says what to change. LT-002
lists every missing or weakened lint with the level it should have and the
level it has. The fix is to set it where the package gets its tables: the
workspace root for an inherited package, its own manifest otherwise.

### Cognitive complexity: two ways to meet one requirement

`clippy::cognitive_complexity` is required at `deny`, with
`cognitive-complexity-threshold = 9` in `clippy.toml` (the existing root lint
policy checks the threshold). It is not required where Whitaker's
`macro_aware_cognitive_complexity` gates the budget instead, and the rule
credits that only when all of the following hold:

- `dylint.toml` opts the lint in (`[whitaker] experimental`), with
  `max_complexity` a TOML integer from 1 to 9 as the only key of its table;
- Clippy's threshold is removed, so one gate owns the budget;
- Whitaker runs over the whole workspace: the resolved commands that run in the
  root directory select every member of the root workspace between them
  (`--workspace` less its `--exclude` list, or `-p` lists), and there is no
  Whitaker command the reader could not place. A command run after `cd` does
  not count. This is a close reading of `gate-workspace-coverage` for Whitaker,
  which stays the authority.
- The package is in the root workspace.

When the lint is configured but the gate has lines the reader could not place
and none it can prove covers the workspace, the rule reports EN-001 and neither
requires nor credits the Clippy lint.

### unsafe_code and boundary crates

`unsafe_code = "forbid"` everywhere, except a boundary crate named in the
`boundary_crates` parameter. A boundary crate sets `deny`, so each use needs
its own reviewed `#[expect(unsafe_code, reason = "...")]`; has a `cargo miri`
command in a target `make lint` or `make test` reaches (with prerequisites) or
in a workflow or composite-action `run` step; and names the ADR that records
why it needs `unsafe`. axinite's ADR 0015 and wireframe's ADR 0016 are the
models. A boundary crate is not required to forbid anything, and a crate that
forbids `unsafe` is reported for being on the list.

## Parameters

- `severity` (`warning` by default): the severity of every LT finding and of the
  indeterminate findings. An envelope defect is always `error`.
- `exemptions`: reviewed divergences, each with `repository` (`owner/name`),
  `crate` (the package name), `lint` (`clippy::name`, `rust::name` or
  `rustdoc::name`), an optional `level` (the weakest level the package may then
  set; absent means the lint is not required of it), a `reason` and a
  `review_trigger`. The policy ignores an entry with a blank reason or trigger,
  because a divergence nobody must revisit is not a record. `unsafe_code` is
  not exemptable here.
- `boundary_crates`: `repository`, `crate` and `adr` for each boundary crate.

```yaml
exemptions:
  - repository: leynos/example
    crate: example-ffi
    lint: clippy::missing_const_for_fn
    reason: >-
      The generated wrappers cannot be const.
    review_trigger: review when the generator moves
boundary_crates:
  - repository: leynos/example
    crate: example-ffi
    adr: ADR 0015
```

## Indeterminate, never guessed

An unreadable manifest (invalid TOML, not UTF-8, resolving outside the
checkout) is EN-001. The packages that could be read are still judged. A gate
the readers could not place makes only the two gate facts indeterminate: a
missing Miri command (when a workflow, an unresolved Makefile walk or an
expression-built matrix might hold one) and the Whitaker credit above.

## Severity

The rule ships at `warning` severity so the estate can be surveyed before it
blocks. The `severity` parameter raises the findings to `error` without a code
change.

## Fixtures

`fixtures/generate.py` lays out each scenario as a synthetic checkout and
records the production envelope, so the Rego suite runs on the facts the
command would send. The scenarios cover the three shapes, each with a clean
variant, the defects above and unreadable inputs. Regenerate with
`uv run python platform-standards/canon/lint-rules/rust-lint-tables/fixtures/generate.py`
and run the suite with:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/rust-lint-tables/policy \
  --data platform-standards/canon/lint-rules/rust-lint-tables/fixtures/data.json
```
