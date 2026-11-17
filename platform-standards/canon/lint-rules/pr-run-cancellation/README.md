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
  leaves an older run that was re-run untouched. The static index form counts
  the same as the dotted one: `github['run_id']` is `github.run_id`. The
  documented `github.head_ref || github.run_id` shape is accepted, because the
  marker is the whole selected head and so is non-empty on every pull request.
  A head that merely contains the marker, such as
  `github.event_name == 'push' && github.head_ref`, can be false on a pull
  request, so the fallback is examined and an unsettled group is reported
  indeterminate. A `||` inside a call argument is that argument's own fallback,
  not a branch of the whole group, so a run value beside it stays in the group;
  when such a nested fallback sits beside a run value, whether the group varies
  cannot be read from the text, and the workflow is reported indeterminate
  instead of passing or failing.
- **RC-002** (warning, noncompliant): a cancelling block's `group` must name the
  pull request through `github.event.pull_request.number` or `github.head_ref`,
  matched case-insensitively, inside a `${{ ... }}` expression. A group that
  spells the marker as literal text, such as `ci-github.head_ref`, is a
  constant and does not count, and neither does a marker inside a quoted string
  literal, such as `${{ 'github.head_ref' }}`. An occurrence used as a
  comparison operand on either side, such as
  `${{ github.event.pull_request.number > 0 }}` or
  `${{ 0 < github.event.pull_request.number }}`, renders as a boolean, so that
  occurrence names no pull request; parentheses around the operand do not
  change that, and another occurrence in the same group can still supply the
  identity. The marker must be the whole property name: `github.head_ref_name`
  is a different property, which GitHub resolves to an empty string, so a
  longer name that merely starts with the marker does not count, and a static
  index such as `github['head_ref']` counts as the property it names. A marker
  handed to a function that returns something else does not count either:
  `contains`, `startsWith`, `endsWith`, `hashFiles`, `success`, `failure`,
  `always` and `cancelled` return a boolean or a constant, so
  `${{ contains(github.head_ref, 'release') }}` is a group every pull request
  shares. Every call still open around the marker is examined, because an outer
  call that discards the value defeats an inner one that carries it:
  `${{ contains(format('{0}', github.head_ref), 'release') }}` renders a
  boolean. The same argument context judges every occurrence, so
  `${{ contains(github.head_ref, github.head_ref) }}` names nothing even though
  the second occurrence is not the one directly after the call. `!` renders a
  boolean, so `${{ !github.head_ref }}` names nothing, and `&&` yields its
  right operand, so a marker that is the left operand —
  `${{ github.head_ref && 'ci' }}` — names nothing either. `fromJSON` carries
  its argument's value, so a marker inside one still names the pull request.
  `format` is judged by its template: the marker names the pull request only
  when its argument position is rendered by a matching `{N}` placeholder, so
  `${{ format('ci-{0}', github.head_ref) }}` counts and
  `${{ format('ci', github.head_ref) }}` does not. `join` is judged the same
  way: a marker in its array argument is joined into the result, but a marker
  in its separator renders only between elements, so whether the group carries
  it depends on how many elements there are and the block is reported
  indeterminate. A marker inside any other call, or one rendered through a
  template the rule cannot read, cannot be classified, and the block is
  reported indeterminate rather than as a pass or a defect. Only the selected
  value counts, because that is what a pull request carries, so a marker that
  appears only in a fallback, as in `${{ 'ci' || github.head_ref }}` or
  `${{ format('{0}', 'ci' || github.head_ref) }}`, does not count either. A
  fallback is contained by its own argument: a marker in a later argument of
  the same call, as in
  `${{ format('{0}-{1}', github.workflow || 'ci', github.head_ref) }}`, still
  counts. Every cancelling block is checked on its own: the workflow-level
  block when it cancels, and each cancelling job block whether or not the
  workflow block complies, because GitHub applies both concurrency levels. A
  block whose group would cancel a *different* pull request's run is reported.
- **EN-001** (indeterminate): the envelope is not a schema-1
  `policy-input/pr-run-cancellation` document, or a workflow could not be
  decoded, declares no trigger, has a trigger shape the rule cannot classify,
  has no non-empty `jobs` mapping of job objects, has a `concurrency` block the
  rule cannot read, or has a group whose nested fallback or unrecognized call
  leaves its value or its identity unclassifiable. An envelope defect is
  `error`: it says the rule could not run. Every other indeterminate finding
  uses the configured severity.

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

The marker check is syntactic: the rule confirms that the marker is evaluated
in the selected part of the group, unquoted, not directly compared, not
negated, not the left operand of `&&`, and not handed to a call that discards
or replaces its value, or to an argument position whose effect on the value the
text cannot settle, such as `join`'s separator or a `format` template the rule
cannot read. It does not evaluate the expression, so an arithmetic or string
transform the rule does not recognize as a function call is not detected. It
also does not count arguments from their contents, so a marker in a call's
array argument is taken to survive. Selection is judged by position: the text
before a `||` is treated as the value GitHub takes in that argument, without
evaluating truthiness, so a marker that appears only after one does not count,
and a marker that is the right operand of `&&` is taken to be the value when
the left operand is truthy. The fallback is ignored only when the selected head
is the marker alone, which is non-empty on every pull request. Parentheses are
counted to keep a fallback nested in a call argument out of the surrounding
branch and out of later arguments; a nested fallback beside a value that
changes between runs is reported indeterminate rather than judged.
Varying-value detection covers the five values named above; a group made unique
some other way is not detected.

## Evidence

The package's own suite is the specification: `fixtures/data.json` carries the
reference shape, job-level and reusable-caller shapes, every trigger spelling,
each indeterminate shape, each non-cancelling value, and both exemption kinds.
`policy/pr_run_cancellation_test.rego` compares the exact finding profile for
each fixture and patches the reference shape to derive variants.
