# main-owned-codescene-coverage

Audits the GitHub Actions topology that keeps CodeScene coverage publication on
`main`. The audit is read-only. It evaluates a decoded-workflow envelope built
by `concordat artefact rule run`; it never executes workflow commands or sends
coverage data.

## CV-005

The rule requires all of the following.

Pull-request lanes keep coverage local:

- Every `generate-coverage` step in a pull-request workflow uses
  `with-ratchet: true`, and its effective Rust and Python baseline paths match
  a ratcheting generator in the main publisher.
- Every `generate-coverage` step in a pull-request workflow sets
  `publish-artefact: 'false'`. The action defaults that input to `"true"`, so a
  lane that omits it publishes the report; the rule reads the effective value.
- Pull-request workflows do not invoke a CodeScene action or a direct
  `cs-coverage check`/`upload` command, and do not receive `CS_ACCESS_TOKEN`:
  the rule reads the token in `env` at workflow, job or step level, as an
  action input, and in a `run` body. The token-placement clause below applies
  as well when the same workflow has an explicit upload.

One main-owned publisher writes the baseline and uploads:

- Exactly one workflow with a `push` trigger restricted to `main`, optionally
  alongside `workflow_dispatch`, generates ratcheted coverage and invokes the
  CodeScene action in upload mode (or runs a direct `cs-coverage upload`
  command, which the policy also recognizes as an upload). The action is the
  intended route; a direct command is reported by the token clause below when
  it names the token. The action defaults `mode` to `upload`, so a step that
  omits the input uploads and satisfies this clause; `mode: check` and
  `mode: install` do not. The rule reads the effective mode rather than the
  spelling, because reporting correct wiring as broken only teaches people to
  edit a working workflow to satisfy the audit.
- The publisher declares a `concurrency` block, so two overlapping pushes to
  `main` cannot race to write the baseline every pull request is then measured
  against, and the block queues rather than cancels: `cancel-in-progress` is
  absent or false. A cancelled publisher abandons both its upload and the
  baseline it was writing; a queued one publishes later, and the later push's
  baseline wins. An expression is refused too, because it may evaluate true on
  the very push it matters for.

An uploader names the token only in two kinds of place:

- A workflow that uploads to CodeScene may name `CS_ACCESS_TOKEN` only in the
  check step's command and in the `access-token` input of a CodeScene action
  step (in upload, `check` or `install` mode, when the value is exactly the
  secret; an upload step may itself be the workflow's explicit upload). Every
  key and scalar of the document is read, case-folded, and each other reference
  is reported by its document path. That covers `env` at workflow, job or step
  level, any other `run` body, another action's input, and a condition. A `run`
  body interpolating the secret puts it in a shell process that checked-out
  code can read. An `env` binding reaches every step beneath it, and another
  action's input hands the secret across a boundary nobody approved.
  Static-analysis tools flagged the token in `env` repeatedly, which is what
  this clause settles. A direct `cs-coverage upload` command that names the
  token fails it, because the command line reads the token from its
  environment; upload through the action.

The token's environment is outside this rule. The `codescene` environment and
its branch policy are repository settings, which a checkout cannot show; the
Concordat Auditor checks them as CV-006. This rule does not judge whether the
upload step carries a ref or credential guard, or whether its job declares
`environment: codescene`: publisher shape is out of scope (user rulings of 6
and 28 November), and the rule keeps the off-pull-request-CI and
secret-handling clauses.

The removed installer digest is gone:

- No workflow passes the `installer-checksum` input. The rule flags the input
  wherever it appears, whatever its value: the uploader rejects a non-empty
  value from shared-actions f68e8e2e onwards, which is why the input is being
  removed, and an empty one is a reader waiting to be filled in again.
- No workflow refreshes the CodeScene installer digest. The uploader pins
  `cs-coverage` through its own manifest.

Every ratcheting platform runs on the trunk:

- `generate-coverage` keys its ratchet baseline by `runner.os` and saves it
  only on a push to `main`. A Windows or macOS lane that ratchets on pull
  requests therefore needs the same platform running with the ratchet on the
  trunk push, or its baseline is never written and the pull-request ratchet
  compares against an empty file. There are no unratcheted platform exceptions;
  the fix is the trunk leg.

## What the rule declines to judge

The audit fails closed where a local reading cannot see far enough, and it
reports what it could not see rather than a verdict.

- Malformed workflow YAML and unsupported job shapes are `indeterminate` when
  they could carry pull-request coverage, a main publisher, or decoded
  CodeScene or coverage facts.
- A job that calls a workflow of the same repository
  (`uses: ./.github/workflows/<file>`, no `@ref`) is read as that workflow's
  jobs, under the caller's triggers (a `workflow_call` document has none of its
  own), to three levels. Findings name the caller, which is the document that
  runs. A call that cannot be resolved stays reported by name: another
  repository's workflow, a file the checkout does not hold, a loop, or a chain
  deeper than three levels. The indeterminacy is scoped to that job, so the
  workflow's other jobs are still evaluated. A scheduled support workflow that
  calls a shared automation workflow cannot affect this contract and is left
  alone.
- A coverage job's `runs-on` is classified from its literal labels; a label
  list is a conjunction and is read as one runner's description. A label
  written as an expression is classified from the literals in value position,
  those that directly follow `&&`, `||` or the opening `${{`; a literal beside
  a comparison, such as `'dependabot[bot]'`, is an operand and not a runner. It
  classifies only when every such literal classifies and all of them agree,
  which is the case for the common fork fallback between two Linux labels. A
  mixed expression, one naming a label this rule does not recognize, or a
  `matrix` reference with no literal, stays `indeterminate` rather than being
  guessed.

### Named exemptions

`exemptions` in the rule's parameters list workflows that test the CodeScene
actions themselves, each with its `repository` (`owner/name`, read from the
`origin` remote), the exact `path`, a `rationale` and a `review_trigger`. Both
text fields must be non-blank. An exemption suppresses only "pull-request
workflow invokes CodeScene" and "CodeScene publication is not restricted to a
main-only workflow"; token placement, the installer digest and every other
clause stay audited, and an exempt workflow must not receive the real
`CS_ACCESS_TOKEN` on pull requests. Two workflows of `leynos/shared-actions`
are exempt today: the offline action tests on pull requests, and the parser
proof, which a hand dispatch runs from `main` only.

- A repository that generates no coverage anywhere is not a subject of this
  rule: it has nothing for a publisher to publish.

### The trigger key is read under two spellings

GitHub spells the trigger key `on`. A YAML 1.1 loader resolves that unquoted
key to the boolean `True`, which JSON renders as the key `"true"`. A reader
that knows only the string key finds no triggers at all, and every
trigger-derived clause above then passes over an empty set with nothing to
report. The policy reads both spellings, and the fixture suite carries a
compliant and a non-compliant repository written with each.

## Adopting this rule

An adoption **removes a quality gate from the pull-request lane**. It is
therefore never a mechanical merge on green: it takes a full review round, zero
unresolved threads, and a walkthrough marker naming the head with a clean
pre-merge table, in every repository that adopts it. A pin or version move that
changes no behaviour is mechanical; this is not one.

Two operational facts follow an adoption. They are consequences to expect, not
clauses this rule audits.

- The `CodeScene Code Coverage (main)` check never completes on a pull
  request afterwards, because it waits for an upload no pull-request lane
  sends. It is not a required check anywhere in the estate, and a merge is not
  blocked on it.
- A CodeScene project whose gates configuration is absent fails `check` mode on
  every pull request. That is a property of the project's configuration in
  CodeScene, not of the repository's workflows, and adopting this rule removes
  the `check` invocation that surfaced it.

## Validation

From the repository root, run:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/main-owned-codescene-coverage/policy \
  --data platform-standards/canon/lint-rules/main-owned-codescene-coverage/fixtures/data.json

concordat artefact rule run main-owned-codescene-coverage --repo /path/to/checkout
```
