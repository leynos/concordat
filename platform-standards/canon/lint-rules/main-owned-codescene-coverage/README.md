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
  `cs-coverage check`/`upload` command, and do not receive `CS_ACCESS_TOKEN`.

One main-owned publisher writes the baseline and uploads:

- Exactly one workflow with a `push` trigger restricted to `main`, optionally
  alongside `workflow_dispatch`, generates ratcheted coverage and invokes the
  CodeScene action in upload mode, or runs a direct `cs-coverage upload`
  command. The action defaults `mode` to `upload`, so a step that omits the
  input uploads and satisfies this clause; `mode: check` and `mode: install`
  do not. The rule reads the effective mode rather than the spelling, because
  reporting correct wiring as broken only teaches people to edit a working
  workflow to satisfy the audit.
- That workflow's upload step is guarded on `github.ref == 'refs/heads/main'`
  as well as on the credential. A `workflow_dispatch` selects a ref, and the
  push filter says nothing about it, so a dispatch from a feature branch would
  otherwise publish that branch's coverage as the trunk's. A push filter of
  `branches: [main]` is therefore no substitute: it restricts pushes and says
  nothing about dispatches. The comparison must be one whole `&&` conjunct of
  the condition, and a condition carrying an unquoted `||` guards nothing:
  `... && github.ref == 'refs/heads/main' ||
  github.event_name == 'workflow_dispatch'` contains the comparison while
  making it optional, which is exactly the dispatch this clause exists to stop.
- The publisher declares a `concurrency` block, so two overlapping pushes to
  `main` cannot race to write the baseline every pull request is then measured
  against, and the block queues rather than cancels: `cancel-in-progress` is
  absent or false. A cancelled publisher abandons both its upload and the
  baseline it was writing; a queued one publishes later, and the later push's
  baseline wins. An expression is refused too, because it may evaluate true on
  the very push it matters for.

The removed installer digest is gone:

- No workflow passes the `installer-checksum` input. The rule flags the input
  wherever it appears, whatever its value: the uploader rejects a non-empty
  value from shared-actions f68e8e2e onwards, which is why the input is being
  removed, and an empty one is a reader waiting to be filled in again.
- No workflow references the `CODESCENE_CLI_SHA256` variable, and no workflow
  refreshes the CodeScene installer digest. The uploader pins `cs-coverage`
  through its own manifest; the variable has no remaining reader.

Every ratcheting platform runs on the trunk:

- `generate-coverage` keys its ratchet baseline by `runner.os` and saves it
  only on a push to `main`. A Windows or macOS lane that ratchets on pull
  requests therefore needs the same platform running with the ratchet on the
  trunk push, or its baseline is never written and the pull-request ratchet
  compares against an empty file. There are no unratcheted platform
  exceptions; the fix is the trunk leg.

## What the rule declines to judge

The audit fails closed where a local reading cannot see far enough, and it
reports what it could not see rather than a verdict.

- Malformed workflow YAML and unsupported job shapes are `indeterminate` when
  they could carry pull-request coverage, a main publisher, or decoded
  CodeScene or coverage facts.
- A job that delegates to a reusable workflow is reported by name. The
  indeterminacy is scoped to that job: a reusable job hides its own steps, not
  the document, so the workflow's other jobs are still evaluated. Treating the
  whole file as unreadable would make the rule silent about a pull-request
  lane it can see perfectly well, which is what happens in a repository whose
  matrix delegates one platform leg. A scheduled support workflow that calls a
  shared automation workflow cannot affect this contract and is left alone.
- A coverage job's `runs-on` is classified from its literal labels; a label
  list is a conjunction and is read as one runner's description. A label
  written as an expression is classified only when every literal it could
  select classifies, and all of them agree, which is the case for the common
  fork fallback between two Linux labels. A mixed expression, or one naming a
  label this rule does not recognize, stays `indeterminate` rather than being
  guessed.
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
therefore never a mechanical merge on green: it takes a full review round,
zero unresolved threads, and a walkthrough marker naming the head with a clean
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
