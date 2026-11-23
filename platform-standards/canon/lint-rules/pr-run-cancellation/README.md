# pr-run-cancellation

Audits the workflows a repository runs for pull requests. The audit is
read-only. It evaluates a `policy-input/pr-run-cancellation` envelope built by
`concordat artefact rule run`; it never runs a workflow.

The rule implements leynos/concordat#388. That ticket calls its clauses PR-001
and PR-002; the canon already uses the `PR-` prefix for the priority checks, so
the findings here are **RC-001** and **RC-002**.

## Why

Every push to a pull request starts a run against the same ref. GitHub's
concurrency queue already replaces an older *pending* run with the newest one
in the group when `cancel-in-progress` is false, so a push that arrives while
the previous run only waited costs nothing extra. A run that is already
*executing* is left alone: the newest push waits behind it, and each later push
waits in turn. On a metered runner that is paid work for a revision nobody will
merge; on any runner it is latency before the answer the author is waiting for.
`cancel-in-progress` is what stops the executing run. The queue depth is
configurable with `queue: single` (the default) or `queue: max`; the default
keeps at most one pending run per group, which is the behaviour described here.

The reference shape is:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

The `||` fallback gives every push, schedule and dispatch run its own group,
and the event test leaves those runs uncancelled. (The rule judges declared
event names only: every event other than `pull_request` and
`pull_request_target`, including `pull_request_review_comment`, puts the
workflow under the run-id requirement, although some of those events carry a
pull request and select its number at run time. It does not check which branch
of the expression an individual event selects.) A shared fallback such as
`github.ref` would put every push to `main` (and every dispatch on one ref) in
one group. `cancel-in-progress: false` protects only the run that is executing:
GitHub still replaces a *pending* run in the group with a newer one, so with
three quick pushes the middle commit would get no run at all. A workflow that
also runs on `push`, a schedule or a dispatch is therefore required to carry
`github.run_id` in its cancelling group (RC-002, below). A pull-request-only
workflow has no such runs, so the requirement does not apply to it. The group
must still name the pull request explicitly: what `github.ref` holds depends on
the event. An ordinary `pull_request` run has `refs/pull/<number>/merge`, which
already separates pull requests, but a `pull_request_target` run has the base
branch ref, which every pull request targeting that branch shares, so a group
built only from `github.ref` there lets one pull request's run cancel
another's. Requiring the marker makes the intent explicit and holds for both
events.

The event test in this shape covers `pull_request`. A workflow triggered by
`pull_request_target` evaluates it false, so test that event too:

```yaml
cancel-in-progress: ${{ github.event_name == 'pull_request' || github.event_name == 'pull_request_target' }}
```

## Checks

- **RC-001** (warning, noncompliant): a workflow triggered by `pull_request` or
  `pull_request_target` has no cancelling `concurrency` block, at workflow
  level or on each job that is not disabled with `if: false`, for every
  pull-request event it declares. A block whose `cancel-in-progress` is only a
  `github.event_name` test cancels for the events that test selects and leaves
  the others running, so a `pull_request_target`-only workflow guarded with
  `github.event_name == 'pull_request'` is reported. An expression the declared
  event proves false does not cancel either:
  `${{ github.event_name == 'push' && true }}` is false on every pull-request
  run, and
  `${{ github.event_name == 'push' || github.event_name == 'schedule' }}`
  selects neither declared pull-request event. A workflow-level block covers
  every job, and covering every declared event may be split between the levels:
  a workflow block that cancels `pull_request` runs and a job block that cancels
  `pull_request_target` runs cover that job for both. A job that sets literal
  `if: false` is skipped, so it needs no block. A reusable-workflow caller job
  (`uses:`) is judged by its own `concurrency`, not by the jobs inside the
  called workflow, because those jobs are not in this document. One finding per
  workflow, naming every uncovered job. A block whose group carries a value
  that separates runs of the same pull request — `github.run_id`,
  `github.run_number`, `github.run_attempt`, `github.sha` or
  `github.event.pull_request.head.sha` — does not cancel the run that ought to
  be superseded. The next push's first attempt never shares a group containing
  a per-run value, and a group containing the attempt number leaves an older
  run that was re-run untouched. Such a value only counts where the expression
  renders it: the third argument of
  `format('ci-{0}', github.head_ref, github.run_id)` never reaches the group,
  so that group is stable, while the `{1}` in
  `format('ci-{0}-{1}', github.head_ref, github.run_id)` renders it and the
  group varies. The static index form counts the same as the dotted one:
  `github['run_id']` is `github.run_id`. The documented
  `github.head_ref || github.run_id` shape is accepted, because a bare
  `github.head_ref` is non-empty on every pull request and so the fallback is
  never selected. A left branch that is provably falsy is skipped the same way,
  so `false || github.head_ref` and `'' || github.head_ref` still select the
  marker, while a non-empty literal such as `'ci'` does not. A branch guarded
  by a `github.event_name` comparison is judged against every pull-request
  event the workflow declares:
  `github.event_name == 'pull_request' && github.head_ref` is selected on a
  `pull_request` run, while
  `github.event_name == 'push' && github.head_ref || 'ci'` falls through to the
  constant `ci`, and `github.event_name == 'push' && github.head_ref` alone
  renders a boolean. A workflow that also declares `pull_request_target`
  therefore fails the `pull_request`-guarded shape, because that guard selects
  `ci` on the other event's run. A guard that excludes every declared event
  skips the branch it protects, so
  `github.event_name == 'push' && github.head_ref || github.run_id` on a
  `pull_request` workflow selects the run id and the group varies. Any other
  `&&` chain is judged by what the rule can prove about every guard in it: a
  literal `false` anywhere makes the whole conjunction a constant, so
  `github.ref && false && github.head_ref` leaves the marker unselected, while
  a reference GitHub always resolves to a non-empty string (such as
  `github.ref`, `github.sha` or `github.event_name`) selects the value after
  it, as in `github.ref && github.head_ref`. A conjunction whose guards are all
  proven truthy and whose value is the marker is truthy as a whole, so the
  `'ci'` fallback in `github.ref && github.head_ref || 'ci'` can never be
  selected. A guard the rule cannot prove truthy or falsy, such as
  `github.event.pull_request.title && github.head_ref` or
  `github.ref == 'refs/heads/main' && github.head_ref`, leaves the group
  indeterminate. A `||` inside a call argument is that argument's own fallback,
  not a branch of the whole group, so a run value beside it stays in the group;
  when such a nested fallback sits beside a run value the expression renders,
  whether the group varies cannot be read from the text, and the workflow is
  reported indeterminate instead of passing or failing. A run value counts only
  where GitHub evaluates it: a provably-falsy `&&` guard in its own argument
  turns it into a constant, so
  `format('{0}-{1}', github.head_ref, false && github.run_id)` cancels while
  the same expression with `true` does not, and a run value behind a
  constant-first fallback in its own argument, as in
  `format('{0}', 'ci' || github.run_id)`, is not selected.
- **RC-002** (warning, noncompliant), non-pull-request runs: in a workflow that
  also declares an event other than `pull_request` and `pull_request_target`,
  every cancelling block's `group` must carry `github.run_id` inside a
  `${{ ... }}` expression (outside a quoted literal), so that push, schedule
  and dispatch runs each get a group of their own. A bare `github.ref`
  fallback, or no fallback, is reported, and so is a run id placed after a
  reference that is non-empty on every run (`github.ref || github.run_id`),
  because a `||` chain never reaches it; `github.head_ref || github.run_id` is
  fine, since the head ref is empty outside pull requests. Such runs share a
  group and GitHub drops a pending run when a newer one arrives. The check is
  textual; whether the run id sits in a fallback the pull request never selects
  is judged by RC-001, which rejects a run id that a pull request would select.
  This clause has the same exemptions as the rest of RC-002.
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
  index such as `github['head_ref']` counts as the property it names.
  `github.head_ref` is the source branch name rather than a unique pull-request
  identity, so two pull requests from different forks that both use a branch
  called `feature` share a group; a repository that must keep fork runs apart
  should name `github.event.pull_request.number`. Naming the pull request is
  necessary but not sufficient for isolation. GitHub concurrency groups are
  repository-wide, so two workflows — or two jobs with their own concurrency
  blocks — that build the same group cancel one another's runs even when both
  name a pull request. The reference shape's `github.workflow` prefix keeps
  workflows apart, and a job-level block needs its own job discriminator.
  RC-002 requires only the pull-request identity; the discriminator is a design
  choice the rule does not demand. A pull-request number *plus* a workflow or
  job discriminator avoids both the fork collision and cross-workflow
  cancellation. A marker handed to a function that returns something else does
  not count either: `contains`, `startsWith`, `endsWith`, `hashFiles`,
  `success`, `failure`, `always` and `cancelled` return a boolean or a
  constant, so `${{ contains(github.head_ref, 'release') }}` is a group every
  pull request shares. Every call still open around the marker is examined,
  because an operator applied to any of them replaces the value: an outer call
  that discards it, as in
  `${{ contains(format('{0}', github.head_ref), 'release') }}`, and one that
  renders it, as in
  `${{ format('{0}', format('{0}-{1}', github.head_ref, 'ci') == 'release') }}`,
  both render a boolean. The same argument context judges every occurrence, so
  `${{ contains(github.head_ref, github.head_ref) }}` names nothing even though
  the second occurrence is not the one directly after the call. `!` renders a
  boolean, so `${{ !github.head_ref }}` names nothing, and `&&` yields its
  right operand, so a marker that is the left operand —
  `${{ github.head_ref && 'ci' }}` — names nothing either. A `&&` chain is read
  in full wherever it sits, including inside a call argument and behind an
  event guard: `${{ format('{0}', false && github.head_ref) }}` and
  `${{ github.event_name == 'pull_request' && false && github.head_ref }}` both
  render `false`, so neither names the pull request, while a guard the rule
  cannot prove truthy, such as `github.event.pull_request.title`, makes the
  value behind it indeterminate. Every argument enclosing the marker is read,
  so an outer guard also decides an inner call:
  `${{ format('{0}', false && format('{0}', github.head_ref)) }}` renders
  `false`, while
  `${{ format('{0}', github.ref && format('{0}', github.head_ref)) }}` renders
  the marker. `fromJSON` parses its argument rather than passing it through, so
  a marker inside one does not establish the identity: a branch name that is a
  JSON literal changes value, and `1` and `1e0` both parse to the number `1`,
  which would put two pull requests in one group. `format` is judged by its
  template: the marker names the pull request only when its argument position
  is rendered by a matching `{N}` placeholder, so
  `${{ format('ci-{0}', github.head_ref) }}` counts and
  `${{ format('ci', github.head_ref) }}` does not. `join` is judged the same
  way: a marker in its array argument is joined into the result, but a marker
  in its separator renders only between elements, so whether the group carries
  it depends on how many elements there are and the block is reported
  indeterminate. A marker inside any other call, or one rendered through a
  template the rule cannot read, cannot be classified, and the block is
  reported indeterminate rather than as a pass or a defect. Every branch a pull
  request can select must name it, so a marker that appears only in a fallback,
  as in `${{ 'ci' || github.head_ref }}` or
  `${{ format('{0}', 'ci' || github.head_ref) }}`, does not count, and a branch
  whose value is provably falsy is skipped, so
  `${{ false || github.head_ref }}`, `${{ '' || github.head_ref }}` and
  `${{ format('{0}', false || github.head_ref) }}` name the pull request. A
  fallback the pull request can reach, such as the `'ci'` branch of
  `${{ github.event_name == 'push' && github.head_ref || 'ci' }}`, is a defect.
  A branch after one the pull request always supplies cannot be selected, which
  is why the run value in `${{ github.head_ref || github.run_id }}` is
  harmless, and why `${{ github.head_ref || fromJSON(github.head_ref) }}` is
  compliant; an unrecognized call in a fallback the pull request *can* reach,
  such as the `fromJSON` branch of
  `github.event_name == 'push' && github.head_ref || fromJSON(github.head_ref)`,
  leaves the identity unreadable and is indeterminate instead. Each declared
  event is judged on its own, so one expression body may name the pull request
  for `pull_request` and a different body for `pull_request_target`. A fallback
  is contained by its own argument: a marker in a later argument of the same
  call, as in
  `${{ format('{0}-{1}', github.workflow || 'ci', github.head_ref) }}`, still
  counts. Every cancelling block is checked on its own: the workflow-level
  block when it cancels, and each cancelling job block whether or not the
  workflow block complies, because GitHub applies both concurrency levels. A
  block is judged on the events it actually cancels: one whose
  `cancel-in-progress` guard selects only `pull_request` is reported for that
  event's runs even though RC-001 also fires, while a block that cancels
  nothing is left to RC-001. A block whose group would cancel a *different*
  pull request's run is reported.
- **EN-001** (indeterminate): the envelope is not a schema-1
  `policy-input/pr-run-cancellation` document, or a workflow could not be
  decoded, declares no trigger, has a trigger shape the rule cannot classify,
  has no non-empty `jobs` mapping of job objects, has a `concurrency` block the
  rule cannot read, or has a group whose nested fallback, a `&&` guard the rule
  cannot prove truthy, or an unrecognized call in a branch a pull request can
  reach leaves its value or its identity unclassifiable. Identity is judged on
  the events a block actually cancels, so a call the rule cannot classify that
  belongs only to an event the block leaves uncancelled does not make the
  cancelling event's group indeterminate. An envelope defect is `error`: it
  says the rule could not run. Every other indeterminate finding uses the
  configured severity.

A workflow with no `pull_request` or `pull_request_target` trigger is not
audited. That includes push-only, schedule-only and `workflow_call`-only
workflows; a workflow that carries a PR trigger alongside another trigger is
audited.

## Relationship to the coverage rule

leynos/concordat#388 asks for a dual-trigger workflow that also satisfies the
existing coverage topology. `main-owned-codescene-coverage` keeps CodeScene
publication in a main-only workflow: its main-only trigger accepts exactly a
`push` to `main`, optionally with `workflow_dispatch`, and it reports any
pull-request workflow that invokes CodeScene or receives `CS_ACCESS_TOKEN`. A
single document therefore cannot be both a dual-trigger workflow and the
coverage publisher. The reconciliation is to keep the publisher main-only and
let the dual-trigger workflows carry the cancellation shape:

- the main-only publisher runs coverage generation and upload, and is not
  audited here because it declares no pull-request trigger;
- each dual-trigger workflow uses the reference concurrency shape and does not
  invoke CodeScene.

`tests/unit/test_pr_run_cancellation_coverage_interaction.py` evaluates both
policies over one checkout: a main-only publisher plus a dual-trigger workflow
is compliant under both, while moving CodeScene into the dual-trigger workflow
keeps cancellation compliant and is reported by the coverage rule. Neither rule
is weakened to force a single-document shape.

## What counts as cancelling

- `cancel-in-progress: true`, or a string that trims and folds to `true`.
- An expression, unless the rule can prove it false for the run that matters.
  Most expressions keep the conservative reading, because they may evaluate
  true on the run that matters. The rule proves these false, and therefore not
  cancelling:
  - a trimmed literal-false expression, `${{ false }}`;
  - an expression that is nothing but `github.event_name` tests, evaluated on
    each declared pull-request event: `${{ github.event_name == 'pull_request' }}`
    cancels a `pull_request` run and does not cancel a `pull_request_target`
    run of the same workflow, while a `||` chain of such tests covers the
    events it selects, so the two-event guard above cancels both;
  - an expression the declared event makes false. `&&` short-circuits, so a
    conjunction whose guard is an event test that excludes this event, or a
    literal GitHub treats as falsy, is false whatever follows it:
    `${{ github.event_name == 'push' && true }}` and
    `${{ github.event_name == 'push' && false }}` never cancel a
    `pull_request` run. A `||` chain is false only when every branch is, so
    `${{ github.event_name == 'push' || github.event_name == 'schedule' }}`
    does not cancel a `pull_request` run either, and a conjunction whose
    guards all hold is as false as the value after them, as in
    `${{ true && false }}`.

  **Unsettled expressions are treated as cancelling.** The rule does not
  evaluate arbitrary expressions, and it reports a defect only where the text
  proves one. `${{ contains(github.event_name, 'push') || false }}` is false on
  a pull-request run, but the rule cannot read the event test inside `contains`
  and keeps the conservative reading, so the expression is not reported. A
  value written in a shape the rule cannot settle is not detected; write a
  shape RC-001 understands, or use `cancel-in-progress: true`.
- **Not cancelling:** an absent value, `false`, the string `"false"`, a value
  that is neither `true` nor an expression, an event test that excludes a
  declared event, the string shorthand `concurrency: <group>`, which leaves
  `cancel-in-progress` at its default of false, and any group built from a
  value that separates runs of the same pull request (`github.run_id`,
  `github.run_number`, `github.run_attempt`, `github.sha` or
  `github.event.pull_request.head.sha`), which the run that ought to be
  superseded never shares, unless the expression never renders it:
  `format('ci-{0}', github.head_ref, github.run_id)` cancels and
  `format('ci-{0}-{1}', github.head_ref, github.run_id)` does not. A `||` chain
  is judged by the branch GitHub selects: `github.head_ref || github.run_id`
  keeps its pull-request branch, while `github.run_id || github.head_ref` does
  not. A guard on a branch is evaluated against each declared pull-request
  event: an `github.event_name` comparison by that event, a literal `false` as
  a constant, and a guard the rule cannot prove truthy or falsy as
  indeterminate. Every guard in a `&&` chain is evaluated, so one falsy guard
  anywhere leaves the group constant.

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
the workflow name, is not judged. GitHub applies concurrency groups
repository-wide, so two workflows that both build a bare pull-request group, or
two jobs with separate blocks that build the same group, cancel one another's
runs. The reference shape's `github.workflow` prefix separates workflows, and a
job-level block needs its own discriminator; the rule neither requires nor
checks either. A compliant RC-002 therefore establishes that the group names
the pull request, not that two different workflows or jobs stay out of each
other's groups.

The marker check is syntactic: the rule confirms that the marker is evaluated
in the selected part of the group, unquoted, not directly compared, not
negated, not the left operand of `&&`, not the operand of an operator applied
to any call around it, and not handed to a call that discards or replaces its
value, or to an argument position whose effect on the value the text cannot
settle, such as `join`'s separator or a `format` template the rule cannot read.
It does not evaluate the expression, so an arithmetic or string transform the
rule does not recognize as a function call is not detected. It also does not
count arguments from their contents, so a marker in a call's array argument is
taken to survive. Selection is judged by position and by the guards the rule
can evaluate: the text before a `||` is treated as the value GitHub takes in
that argument unless it is provably falsy, a branch is reachable while an
earlier branch may be falsy, and an `github.event_name` comparison on a branch
is decided against each pull-request event the workflow declares. Guards in
every call argument enclosing the marker are read, not only top-level ones. A
fallback is ignored when it cannot be reached, as in
`github.head_ref || github.run_id`, or when the guard protecting it holds; a
guard the rule cannot evaluate, such as a comparison against `github.ref`, is
reported indeterminate. A marker that is the value of a `&&` chain is taken to
be selected when every guard before it is truthy: a literal `false` anywhere
makes the conjunction a constant, the literal `true` and the references GitHub
always resolves to a non-empty string select the value, and any other guard is
reported indeterminate. Parentheses are counted to keep a fallback nested in a
call argument out of the surrounding branch and out of later arguments; a
nested fallback beside a value that changes between runs, and that the
expression renders, is reported indeterminate rather than judged. Varying-value
detection covers the five values named above, and only where an enclosing call
does not discard them; a group made unique some other way is not detected.

## Evidence

The package's own suite is the specification: `fixtures/data.json` carries the
reference shape, job-level and reusable-caller shapes, every trigger spelling,
each indeterminate shape, each non-cancelling value, the expressions a declared
event proves false, and both exemption kinds.
`policy/pr_run_cancellation_test.rego` compares the exact finding profile for
each fixture and patches the reference shape to derive variants.
