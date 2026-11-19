# dependabot-update-shape

Audits `.github/dependabot.yml` against the estate's Dependabot update shape.
The audit is read-only. It evaluates an envelope built by
`concordat artefact rule run`: the decoded configuration, each entry's group
names in document order, and the directories under `.github/actions` that hold
an action manifest (`action.yml` or `action.yaml`).

## DB-005

Every `updates` entry must satisfy all of the following.

- **Daily cadence.** `schedule.interval` is `daily`, except in a registered
  private repository, which may also run `weekly` (see
  [Private cadence](#private-cadence)).
- **One catch-all, last.** The entry's last group has `patterns: ["*"]` and
  `update-types: [minor, patch]`, and no other key except the default
  `applies-to: version-updates`. The catch-all therefore groups no major: a
  major that matches no earlier narrow group arrives as its own pull request,
  where its breaking changes get their own review, and one that matches a
  lockstep group moves with that family. Any other key changes what the
  catch-all takes. `applies-to: security-updates` leaves every version update
  ungrouped, and `exclude-patterns`, `dependency-type` and `group-by` each
  carve out a class of dependency that then arrives one pull request apiece.
- **Earlier groups are narrow.** Dependabot assigns a dependency to the first
  group that matches it, so a group before the catch-all must be narrower than
  `*`. It needs a non-empty `patterns` list, and no pattern may consist only of
  wildcards. A lockstep family such as `rstest-bdd*` or `rustcrypto` passes,
  whatever `update-types` it takes: a crate family released in lockstep must
  move together even across a major. A group keyed only on `dependency-type`,
  or on a bare wildcard, is a second catch-all that takes majors first.
- **Local actions are updated.** When the repository has an action manifest
  under `.github/actions`, the `github-actions` entries between them cover `/`
  (the workflows) and every directory holding an action manifest. Coverage is
  read from `directory` and `directories` across all `github-actions` entries.
  A value is read relative to the repository root with or without its leading
  or trailing slash. A `directories` glob follows Dependabot's semantics: `*`
  stays within one path segment and `**` spans any number, so
  `/.github/actions/*` covers `/.github/actions/setup` but not
  `/.github/actions/rust/setup`. A configuration listing only `/` leaves every
  composite action's pinned `uses:` unmanaged and drifting.
- **Cargo versioning.** A `cargo` entry sets `versioning-strategy` only to
  `auto` or `lockfile-only`, or leaves it unset (a null value reads as unset).
  Other ecosystems are not judged on it.

A repository with no Dependabot configuration is compliant. This rule judges
the shape of a configuration, not whether one exists. It also leaves ecosystem
coverage to the Dependabot governance rules: a configuration with no
`github-actions` entry at all is not judged on local actions.

### Private cadence

A private repository whose CI runs on a metered runner may batch its updates
into one weekly run. The exception is opt-in per repository through the rule
manifest's parameters:

- `private_cadence.accepted_intervals` lists the intervals a registered
  repository may use; the shipped default is `[daily, weekly]`.
- `private_cadence.weekly_day` names the weekday a `weekly` entry must use when
  it names one; the shipped default is `monday`.
- `private_repositories` is the registry. Each record carries a `repository`
  slug (`owner/name`), a `rationale`, and a `review_trigger`.

The DB-005 adapter reads the checkout's identity from its Git `origin` remote.
The slug is lower-cased before the comparison, so a checkout of
`Leynos/Dev-Env-Rocky` matches the record `leynos/dev-env-rocky`. A record
registers the repository only when both its rationale and its review trigger
are non-blank; a record nobody must revisit is not a record.

A registered repository accepts every interval in `accepted_intervals`. A
`weekly` entry that names a `schedule.day` must name `weekly_day`, compared
case-insensitively; an entry that omits `day` lets Dependabot choose and is
accepted. The weekday is not checked when `weekly_day` is absent. A
`weekly_day` that is present but blank or not a string is malformed: it does
not silently disable the weekday check, and the cadence falls back to the daily
rule. Every other clause is unchanged: the catch-all, the narrow earlier
groups, local-action coverage, and the Cargo versioning rule all still apply to
a private repository.

The exception fails closed. Any of the following keeps the daily rule:

- no `origin` remote, so the identity is `None`;
- a blank, non-string, unregistered or case-mismatched name (after
  lower-casing, an unregistered name still has no record);
- a registry record with a blank `rationale` or `review_trigger`;
- absent, non-object or malformed parameters, including an
  `accepted_intervals` value that is not a non-empty array of non-blank
  strings, and a `weekly_day` that is present but not a non-blank string.

In each case `accepted_intervals` falls back to `["daily"]`. The audit does not
observe repository visibility. The registry is the only evidence it has, so a
repository that later becomes public must be removed from the registry; its
`review_trigger` records when that review is due.

## What the rule declines to judge

- Multi-ecosystem groups (`multi-ecosystem-groups` and an entry's
  `multi-ecosystem-group`) are not interpreted. An entry that takes its
  schedule from such a group reads as having none, and is reported.
- Group names, labels, cooldowns, `open-pull-requests-limit` and the other
  keys are not audited.
- Repository visibility is not observed. A repository is treated as private
  only when its `origin` slug appears in `private_repositories`, so the
  registry, not GitHub, decides the exception.
- A symbolic link under `.github/actions` is not followed, so an action
  manifest reached only through one is not required to be covered.

The audit fails closed where it cannot read. A configuration that is not UTF-8,
not YAML, not a mapping, or a symbolic link is `indeterminate`, and so is a
repository carrying both `dependabot.yml` and `dependabot.yaml`, since GitHub
reads one and the audit cannot tell which. A `.github/actions` directory that
cannot be listed, or that resolves outside the checkout, is an operational
error.

## Validation

From the repository root, run:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/dependabot-update-shape/policy \
  --data platform-standards/canon/lint-rules/dependabot-update-shape/fixtures/data.json
```

Each fixture is named for the one decision it changes from the compliant
configuration. The private-cadence fixtures are:

- `private_weekly_no_day` and `private_weekly_monday` — accepted; the omitted
  day and the configured day both pass.
- `private_weekly_tuesday` — the weekday finding.
- `private_weekly_bad_catch_all` — the weekly cadence is accepted and the
  group finding remains.
- `private_monthly` — the interval finding names the registry record.
- `private_weekly_unregistered` and `private_weekly_null_name` — the daily
  rule.
