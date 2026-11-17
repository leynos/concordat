# pr-run-cancellation

Audits the workflows a repository runs for pull requests. The audit is
read-only. It evaluates a `policy-input/pr-run-cancellation` envelope built by
`concordat artefact rule run`; it never runs a workflow.

The rule implements leynos/concordat#388. That ticket calls its clauses PR-001
and PR-002; the canon already uses the `PR-` prefix for the priority checks, so
the findings here are **RC-001** and **RC-002**.

## Why

Every push to a pull request starts a run against the same ref. Without
concurrency cancellation the earlier runs keep the runners busy on a commit
nobody will merge, and the newest run queues behind them. On a metered runner
that is paid work for a superseded revision; on any runner it is latency before
the answer the author is waiting for.

The reference shape is:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

The `||` fallback keeps push runs on `main` in the same group as each other, so
a push run supersedes the previous push run while the event test leaves push
runs uncancelled. The group must still name the pull request explicitly: what
`github.ref` holds depends on the event. An ordinary `pull_request` run has
`refs/pull/<number>/merge`, which already separates pull requests, but a
`pull_request_target` run has the base branch ref, which every pull request
targeting that branch shares, so a group built only from `github.ref` there
lets one pull request's run cancel another's. Requiring the marker makes the
intent explicit and holds for both events.

The event test in this shape covers `pull_request`. A workflow triggered by
`pull_request_target` evaluates it false, so test that event too:

```yaml
cancel-in-progress: ${{ github.event_name == 'pull_request' || github.event_name == 'pull_request_target' }}
```

## Checks

- **RC-001** (warning, noncompliant): a workflow triggered by `pull_request` or
  `pull_request_target` has no cancelling `concurrency` block, at workflow
  level or on each job that is not disabled with `if: false`. A workflow-level
  block covers every job. A job that sets literal `if: false` is skipped, so it
  needs no block. A reusable-workflow caller job (`uses:`) is judged by its own
  `concurrency`, not by the jobs inside the called workflow, because those jobs
  are not in this document. One finding per workflow, naming every uncovered
  job. A block whose group carries a value that separates runs of the same pull
  request — `github.run_id`, `github.run_number`, `github.run_attempt`,
  `github.sha` or `github.event.pull_request.head.sha` — does not cancel the
  run that ought to be superseded. The next push's first attempt never shares a
  group containing a per-run value, and a group containing the attempt number
  leaves an older run that was re-run untouched. The documented
  `github.head_ref || github.run_id` shape is accepted, because the
  pull-request branch is the one GitHub selects.
- **RC-002** (warning, noncompliant): a cancelling block's `group` must name the
  pull request through `github.event.pull_request.number` or `github.head_ref`,
  matched case-insensitively, inside a `${{ ... }}` expression. A group that
  spells the marker as literal text, such as `ci-github.head_ref`, is a
  constant and does not count, and neither does a marker inside a quoted string
  literal, such as `${{ 'github.head_ref' }}`. A marker used as a comparison
  operand on either side, such as `${{ github.event.pull_request.number > 0 }}`
  or `${{ 0 < github.event.pull_request.number }}`, renders as a boolean, so
  every pull request shares the group; the rule treats such a marker as naming
  no pull request. Only the first `||` branch counts, because that is the one a
  pull request selects, so a marker that appears only in a constant-first
  fallback, as in `${{ 'ci' || github.head_ref }}`, does not count either.
  Every cancelling block is checked on its own: the workflow-level block when
  it cancels, and each cancelling job block whether or not the workflow block
  complies, because GitHub applies both concurrency levels. A block whose group
  would cancel a *different* pull request's run is reported.
- **EN-001** (indeterminate): the envelope is not a schema-1
  `policy-input/pr-run-cancellation` document, or a workflow could not be
  decoded, declares no trigger, has a trigger shape the rule cannot classify,
  has no non-empty `jobs` mapping of job objects, or has a `concurrency` block
  the rule cannot read. An envelope defect is `error`: it says the rule could
  not run. Every other indeterminate finding uses the configured severity.

A workflow with no `pull_request` or `pull_request_target` trigger is not
audited. That includes push-only, schedule-only and `workflow_call`-only
workflows; a workflow that carries a PR trigger alongside another trigger is
audited.

## What counts as cancelling

- `cancel-in-progress: true`, or a string that trims and folds to `true`.
- Any expression other than a trimmed literal-false expression, because it may
  evaluate true on the run that matters. `${{ false }}` is the one expression
  the rule treats as not cancelling.
- **Not cancelling:** an absent value, `false`, the string `"false"`, a value
  that is neither `true` nor an expression, the string shorthand
  `concurrency: <group>`, which leaves `cancel-in-progress` at its default of
  false, and any group built from a value that separates runs of the same pull
  request (`github.run_id`, `github.run_number`, `github.run_attempt`,
  `github.sha` or `github.event.pull_request.head.sha`), which the run that
  ought to be superseded never shares. A `||` chain is judged by the branch
  GitHub selects: `github.head_ref || github.run_id` keeps its pull-request
  branch, while `github.run_id || github.head_ref` does not.

`concurrency` values that are not a non-empty string or an object with a
non-empty string `group` and a boolean-or-string `cancel-in-progress` are not
guessed at: the workflow is reported indeterminate and RC-001/RC-002 are not
stated from it. If any concurrency block in a workflow is unreadable, the whole
workflow's substantive judgement waits.

## Exemptions

`exemptions` lists exact workflows, each with its `repository` (`owner/name`,
read from the `origin` remote), its `path` (`.github/workflows/<file>`), a
`rationale` and a `review_trigger` (the event after which the exemption is
revisited). `repository_exemptions` does the same for a whole repository, each
with a `repository`, `rationale` and `review_trigger`. Both text fields must be
non-blank and the repository identity must be usable; an entry that leaves one
blank suppresses nothing, because an exemption nobody must revisit is not a
record.

An exemption suppresses RC-001 and RC-002 only. Indeterminate evidence stays
reported, since an exemption is an agreement about a known noncompliance, not
about evidence the reader could not establish.

## Severity

The package ships at `warning`, so the estate can be surveyed before it blocks.
Set the `severity` parameter to `error` to make RC-001 and RC-002 block without
a code change. Promotion is a separate decision made once the survey is clean.

## What the rule declines to judge

Only root workflows (`.github/workflows/*.yml` and `*.yaml`) are read. A
cancellation block set inside a reusable workflow in another repository, a
composite action, or a called workflow is outside the audited document. Whether
a group is *unique enough* beyond naming the pull request, such as including
the workflow name, is not judged.

The marker check is syntactic: the rule confirms that the marker appears in the
selected part of the group, unquoted and not directly compared, not that the
expression preserves the pull request's identity. Passing the marker to a
function that returns something else, such as
`contains(github.head_ref, 'release')`, is not detected. Branch selection is
judged by position: the first `||` branch is treated as the one GitHub selects,
without evaluating truthiness, so a marker that appears only in a later branch
does not count. Varying-value detection covers the five values named above; a
group made unique some other way is not detected.

## Evidence

The package's own suite is the specification: `fixtures/data.json` carries the
reference shape, job-level and reusable-caller shapes, every trigger spelling,
each indeterminate shape, each non-cancelling value, and both exemption kinds.
`policy/pr_run_cancellation_test.rego` compares the exact finding profile for
each fixture and patches the reference shape to derive variants.
