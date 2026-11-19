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
  with PyO3) with no upper bound, no `ignore` rule that holds a member back and
  no shared multi-ecosystem group. A group cannot select mutually compatible
  latest versions, and an exact pin is the thing a version update replaces. An
  `ignore` rule must name the published package, not an alias.
- **DF-003** A narrow group matches every member of a family, but an earlier
  group takes one of them first. Repair: move it ahead of the shadowing group.
- **DF-004** A derived input declared in `.concordat` no longer matches its
  canonical value: a copied Makefile variable, recipe, workflow environment
  value or action input has drifted. Repair: update the occurrence, or record
  an override with a rationale and a review trigger.
- **DF-005** A documented compatibility bound in `.concordat` is no longer held
  by the declaration: the declaration now admits a version the bound excluded,
  or has removed its ceiling. Repair: restore the bound, or record the
  migration evidence and update the bound.
- **DF-006** The facts could not be established: no decodable Dependabot
  configuration, a manifest or lock that cannot be read, a Cargo manifest
  outside the governed Rust surfaces that declares a package some relationship
  names or cannot be read (a repository that has not declared
  `language.rust.surfaces` is read at its root only), an exception without a
  rationale and a review trigger, or a type identity the lock records no edge
  for. Always indeterminate, never compliant.
- **DF-007** A `type-identity` relationship whose lock resolves an exchanged
  type at more than one identity, or whose declaration resolves to a different
  identity from the one the lock links. Repair: align the requirement with the
  version the other members were built against.
- **DF-008** A Python or Cargo surface is not monitored by its native
  ecosystem, or the same Python requirements are covered by both `pip` and `uv`
  entries. Repair: keep one native entry per surface.
- **DF-009** An action the repository pins by full SHA is not in the reviewed
  SHAs recorded in `.concordat`. Repair: record the review that approved the
  pin, or move to a reviewed commit.

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

A repository that documents a relationship without declaring every member
locally can name it in `family_contracts`; the rule then applies it and reports
a missing member (DF-006) rather than silently skipping it.

## Documented bounds

Record a compatibility bound the repository relies on:

```yaml
dependencies:
  protected_bounds:
    - relationship: pyo3-build-config-family
      package: pyo3
      bound: "<0.29"
      rationale: the extension relies on the 0.28 build interface
      review_trigger: review when the extension migrates to 0.30
```

`manifest` and `evidence` are optional. A bound this audit cannot parse, or a
package that is not a member of the named relationship, is indeterminate
(DF-006), never held.

## Derived inputs

Record a canonical value and every place it is copied:

```yaml
dependencies:
  derived_inputs:
    - id: pyo3-version
      comparator: satisfies
      canonical:
        manifest: Cargo.toml
        package: pyo3
      selectors:
        - kind: makefile-variable
          path: Makefile
          variable: PYO3_VERSION
      rationale: one PyO3 line across the build
      review_trigger: review when PyO3 0.29 is adopted
```

`comparator` is `equal` or `satisfies`. Selector kinds are `makefile-variable`,
`makefile-recipe`, `workflow-env`, `action-input` and `action-ref`; each names
the file and the variable, tool or action it reads. A selector the audit cannot
resolve (an unexpanded expression, a missing file, two disagreeing occurrences)
is indeterminate (DF-006). An occurrence may carry an `override` with
`override_rationale` and `override_review_trigger`.

For an action pin, `canonical` names the action instead of a manifest:

```yaml
      canonical:
        action:
          uses: actions/checkout
          path: .github/workflows/ci.yml
      reviewed:
        - sha: 0123456789abcdef0123456789abcdef01234567
          provenance: reviewed in the pinning change
```

## Adding a relationship

Add an entry to `platform-standards/canon/data/dependency-families.yaml` with
its `id`, `kind`, `members` (published package names), `relation`, `owner` and
`provenance`, then a scenario to `fixtures/generate.py` and a test to the Rego
suite. Record only relationships a repository has declared or documented; the
data carries no estate-wide version number.

## What the rule declines to judge

- Every `pyproject.toml` outside hidden and vendored trees is read, each
  against the `uv.lock` beside it and the Dependabot entries for its own
  directory (an entry for the root does not cover a nested project).
- Multi-ecosystem groups are read for one purpose: members that all join one
  group the file defines, with `patterns` that take each member, arrive as a
  single pull request. That protects a tuple (DF-002) and takes a family
  together (DF-001, DF-003). An entry that names an undefined group, or whose
  `patterns` miss the member, is left outside it. Dependabot builds one job per
  entry and an entry that joins a group runs only that group, so when several
  entries cover a directory the rule reads the first one that processes the
  package: a group entry for what its `patterns` take, otherwise one that does
  not ignore it.
- Only the identities and edges the lock records are read; no registry is
  contacted and no updater is run.
- Whether a failure would follow from an unprotected relationship (runtime
  compatibility) is a matter for the consumer's canary, not this rule.

Regenerate the fixtures with `fixtures/generate.py`.
