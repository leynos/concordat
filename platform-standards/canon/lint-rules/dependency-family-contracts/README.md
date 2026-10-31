# dependency-family-contracts

Checks that the dependency relationships the estate has recorded are protected
by the repository's Dependabot configuration. The audit is report-only: it
declares no mutations and writes no configuration. It evaluates an envelope
built by `concordat artefact rule run` from the repository's manifests, locks
and Dependabot configuration, plus the relationship data in
`platform-standards/canon/data/dependency-families.yaml`.

A relationship applies only where the repository declares every member, in any
governed manifest: the Cargo surfaces and the member crates of each governed
workspace root (nested and workspace-inherited dependencies included,
`package =` renames resolved to the published name) and the root
`pyproject.toml`. Members are read for these facts only and are not Rust
surfaces, so a workspace needs no member declared in `language.rust.surfaces`.
Facts are collected outside policy evaluation; the policy opens no file and
contacts no registry.

## Rules

- **DF-001** A `family` or `type-identity` relationship whose members no single
  narrow Dependabot group takes ahead of the catch-all, and no narrow group
  could. Repair: add a group for the family.
- **DF-002** A `tuple` of independently versioned packages (for example maturin
  with PyO3) with no upper bound, no `ignore` rule on any member and no shared
  multi-ecosystem group. A group cannot select mutually compatible latest
  versions, and an exact pin is the thing a version update replaces. An
  `ignore` rule must name the published package, not an alias.
- **DF-003** A narrow group matches every member of a family, but an earlier
  group takes one of them first. Repair: move it ahead of the shadowing group.
- **DF-006** The facts could not be established: no decodable Dependabot
  configuration, a manifest or lock that cannot be read, a Python project
  outside the root, a Cargo manifest outside the governed Rust surfaces that
  declares a package some relationship names or cannot be read (a repository
  that has not declared `language.rust.surfaces` is read at its root only), or
  an exception without a rationale and a review trigger. Always indeterminate,
  never compliant.

DF-004 (derived-input equality between a tool pin and its generated inputs) and
DF-005 (a documented bound relaxed without migration evidence) are not yet
implemented.

## Exceptions

A repository may deliberately stay on an older generation. Record it in
`.concordat`:

```yaml
dependencies:
  family_exceptions:
    - relationship: pyo3-build-config-family
      rationale: the extension stays on the 0.28 generation
      review_trigger: review when PyO3 0.30 ships
```

An exception that lacks any of the three fields is reported (DF-006) and does
not exempt the relationship.

## Adding a relationship

Add an entry to `platform-standards/canon/data/dependency-families.yaml` with
its `id`, `kind`, `members` (published package names), `relation`, `owner` and
`provenance`, then a scenario to `fixtures/generate.py` and a test to the Rego
suite. Record only relationships a repository has declared or documented; the
data carries no estate-wide version number.

## What the rule declines to judge

- Only the root `pyproject.toml` is read; a Python project elsewhere is
  reported, not passed.
- Multi-ecosystem groups are read for one purpose: a tuple (DF-002) whose
  members all join one group the file defines, with `patterns` that take each
  member, arrives as a single pull request and counts as protected. The rule
  does not read a group as protecting a family (DF-001), and an entry that
  names an undefined group, or whose `patterns` miss the member, is left
  outside it.
- Whether a failure would follow from an unprotected relationship (runtime
  compatibility) is a matter for the consumer's canary, not this rule.

Regenerate the fixtures with `fixtures/generate.py`.
