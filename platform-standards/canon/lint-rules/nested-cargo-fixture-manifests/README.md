# nested-cargo-fixture-manifests

Audits that Dependabot reaches the standalone fixture manifests that declare
registry dependencies. The audit is read-only. It evaluates a
`policy-input/nested-cargo-fixture-manifests` envelope built by
`concordat artefact rule run`; it never runs Cargo or reads the network.

## Why

A fixture crate, probe or sample outside every workspace is built by nested
`cargo` runs, and its dependencies age like any others. Dependabot reads only
the directories its configuration lists, and `directory: "/"` covers the root
manifest alone. A fixture nobody updates drifts from the workspace, which is
how a nested build ends up compiling different units from the outer one. The
corpus behind the rule is rstest-bdd, whose nested fixtures were added to
`dependabot.yml` in leynos/rstest-bdd#895.

## Checks

- **NC-003** (warning by default): a manifest below the root that no workspace
  owns, and that declares a registry dependency, is not covered by any `cargo`
  entry of `.github/dependabot.yml`. A `directories` entry may be a literal
  path or a glob (`*` within a segment, `**` across segments), as Dependabot
  reads them. With no configuration at all every such manifest is uncovered.
- **EN-001** (indeterminate; an error for an envelope defect, the configured
  severity otherwise): the envelope is not a schema-1
  `policy-input/nested-cargo-fixture-manifests` document, the Dependabot
  configuration cannot be decoded, or a manifest could not be read.

## What counts

- A **candidate** is a `Cargo.toml` below the root that is not a member of any
  workspace and that declares a registry dependency itself, or, if it roots a
  workspace, whose workspace table or members do. A `path`, `git` or
  `workspace = true` dependency has nothing for Dependabot to update.
- Workspace roots and members are not candidates: they are the Dependabot
  update-shape and dependency-family packages' business.
- Hidden directories and `target`, `vendor`, `node_modules`, `build`, `dist`
  and `venv` trees are not entered. Untracked files under other directories are
  read; netsuke's own contract reads only tracked manifests.

## Exemptions

A manifest that is deliberately left alone (a vendored patch, say) is listed in
the `exemptions` parameter with its `repository` (`owner/name`, read from the
`origin` remote), its `path` (the directory, relative to the root), a
`rationale` and a `review_trigger` (the event after which the exemption is
revisited). A blank rationale or trigger suppresses nothing. An exemption does
not apply to another repository. No exemption is shipped by default.

## Severity

The `severity` parameter defaults to `warning`: findings are reported and the
rule's exit status stays 0, so the estate can be surveyed before the rule
blocks. Set it to `error` to enforce the rule. An envelope defect is always an
error.

## What the rule declines to judge

It checks reach, not grouping or schedule: whether the entry that covers a
fixture is well shaped is the update-shape rule's concern. A manifest that
Cargo itself refuses to build (a package below a workspace that is neither a
member nor excluded) is still a candidate here, because the rule asks whether
Dependabot sees the directory, not whether a build can run there.
