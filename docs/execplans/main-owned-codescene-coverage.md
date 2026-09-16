# Keep CodeScene coverage publication on main

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: IN PROGRESS

## Purpose / big picture

Concordat pull requests should enforce coverage through the ratchet produced by
`main`, without sending their reports to CodeScene or running CodeScene's
changed-line gate. After the first pull request, the failing `lint-test` job on
PR #172 no longer attempts to parse Cobertura with `cs-coverage check`; the
existing `coverage-main.yml` workflow remains the only CodeScene publisher.

A second, independent pull request should add a runnable Concordat lint rule
that detects repositories which violate the same estate pattern. Operators
should be able to run that rule against a checkout and receive findings when a
pull-request workflow contacts CodeScene, when a main-only upload is absent, or
when the local coverage ratchet is not wired to a baseline written by `main`.

## Constraints

- Keep the two deliverables on independent branches based on `origin/main`:
  `fix/local-pr-coverage-ratchet` and `rule/main-owned-codescene-coverage`.
- Do not stack either branch on `mdtablefix-check-rollout` or
  `ci-compliance-rfcs`; use those branches only as design evidence.
- Preserve the coverage workload, Cobertura report, and ratchet configuration
  already shared by `.github/workflows/ci.yml` and
  `.github/workflows/coverage-main.yml`.
- Keep `.github/workflows/coverage-main.yml` as the only workflow that receives
  `CS_ACCESS_TOKEN` or invokes the CodeScene upload action.
- The rule must be audit-only and must not mutate consumer repositories.
- Do not add an external dependency or change Concordat's public CLI syntax.
- Follow Red-Green-Refactor for both the workflow repair and the new rule.

## Tolerances (exception triggers)

- Scope: stop if the repair needs more than five tracked files or 250 net new
  lines, excluding this plan.
- Rule scope: stop if the rule needs more than 30 tracked files or 2,500 net
  handwritten lines, excluding generated fixture envelopes.
- Interface: stop if the rule requires a new public CLI command, a persisted
  schema migration, or changes to an existing rule package's input contract.
- Dependencies: stop if any new external dependency is required.
- Iterations: stop if either focused red test remains unexplained after three
  correction attempts.
- Ambiguity: stop if repository evidence cannot distinguish pull-request
  workflows from main-only workflows without interpreting arbitrary shell.

## Risks

- Risk: deleting the CodeScene step may also remove the only coverage gate.
  Severity: high. Likelihood: low. Mitigation: add a contract that positively
  requires the existing `generate-coverage` step and `with-ratchet: true`
  before forbidding PR CodeScene actions.
- Risk: an unmerged RFC currently defines `CV-001` as requiring the defective
  PR CodeScene check. Severity: high. Likelihood: high. Mitigation: give the
  shipped rule an unambiguous corrected identifier and document that the RFC
  branch must reconcile its proposed catalogue before merge; do not silently
  edit another agent's worktree.
- Risk: GitHub Actions YAML permits reusable workflows and expressions that a
  local static rule cannot resolve completely. Severity: medium. Likelihood:
  medium. Mitigation: use three-valued findings and report indeterminate
  workflow shapes rather than guessing.
- Risk: copying the large Markdown-rule branch would couple this rule to
  unrelated, unmerged work. Severity: medium. Likelihood: low. Mitigation:
  implement only the smallest workflow-envelope dispatch required against
  current `main`, following the demonstrated package structure.

## Progress

- [x] (2026-09-16 19:15Z) Confirm the failing run invokes CodeScene in
  `mode: check` with `coverage.xml` and fails parsing that Cobertura report.
- [x] (2026-09-16 19:20Z) Confirm `main` already generates ratcheted PR coverage
  and owns a push-to-main CodeScene upload workflow.
- [x] (2026-09-16 19:25Z) Create two clean git-donkey worktrees from
  `origin/main` at `549b58acd3c3b518d0987ec8982ee4f1a0cadc25`.
- [x] (2026-09-16 19:35Z) Draft this ExecPlan.
- [x] (2026-09-16 19:45Z) Receive explicit approval for implementation.
- [x] (2026-09-16 21:10Z) Add the repair contract, remove the PR CodeScene
  step, and reconcile the accepted design and roadmap doctrine.
- [x] (2026-09-16 21:20Z) Pass the focused repair checks and an independent
  scrutineer run across formatting, documentation, typing, and workflow lint.
- [x] (2026-09-16 21:35Z) Record failing workflow-envelope, dispatch, and
  policy tests before implementing the estate rule.
- [x] (2026-09-16 21:55Z) Implement CV-005, its decoded-workflow envelope,
  seven policy fixtures, manifest registration, and operator documentation.
- [x] (2026-09-16 22:05Z) Pass 714 repository tests, seven Conftest cases,
  and the rule branch's lint, format, type, Markdown, and Mermaid gates.
- [ ] Commit and publish each branch, then open two draft pull requests against
  `main`.

## Surprises & discoveries

- Observation: the failing action uploads `coverage.xml` as a GitHub artefact
  before invoking `cs-coverage check` on the same Cobertura file. Evidence:
  Actions run 35104733784, job 104826275145, ends with
  `Failed to parse coverage file ... coverage.xml`. Impact: the failure is the
  obsolete external PR gate, not coverage generation or the local ratchet.
- Observation: Concordat's CI comments already describe the desired topology,
  but a later step contradicts them by invoking CodeScene in `check` mode.
  Evidence: `.github/workflows/ci.yml` lines around `Generate coverage` and
  `Check coverage against CodeScene gates`. Impact: the repair should delete
  contradictory wiring rather than introduce a replacement gate.
- Observation: `ci-compliance-rfcs` proposes `CV-001` as requiring PR
  CodeScene checks, while the Netsuke repair demonstrated that trusted or
  direct PR submissions cannot provide the intended baseline semantics.
  Evidence: `docs/concordat-design.md` on branch `ci-compliance-rfcs` and
  Netsuke PR #724. Impact: the new shipped rule must encode the corrected
  pattern, and the RFC branch will need separate reconciliation by its owner.
- Observation: the same contradictory `CV-001` wording was already present on
  `main`, not only on the unmerged RFC branch. Evidence:
  `docs/concordat-design.md` and `docs/roadmap.md` at the shared merge base.
  Impact: both branches retire that prescription as part of their own bounded
  documentation changes.
- Observation: reusable workflow calls do not expose an inline `steps` list.
  Evidence: the real Concordat CLI witness reports the reusable workflow as
  indeterminate. Impact: CV-005 preserves three-valued evaluation instead of
  treating absent inline steps as proof of compliance or violation.

## Decision log

- Decision: use the existing coverage ratchet as the only PR coverage gate.
  Rationale: it compares against state written by `main` without external
  publication, while CodeScene receives an authoritative report after merges.
  Date/Author: 2026-09-16, user and Codex.
- Decision: deliver the estate rule as a separate PR based on `origin/main`.
  Rationale: policy review should not block the immediate CI repair, and
  neither change should depend on an unmerged example branch. Date/Author:
  2026-09-16, user and Codex.
- Decision: model decoded workflow structure in a dedicated policy envelope.
  Rationale: Rego should evaluate normalized YAML facts rather than parse YAML
  text, matching the `mdtablefix-check-rollout` rule-package pattern.
  Date/Author: 2026-09-16, Codex.

## Outcomes & retrospective

EP-M1 now keeps pull-request coverage entirely on the existing local ratchet
and makes the post-merge CodeScene upload explicit. Its focused contract and
independent gate run passed.

EP-M2 now ships CV-005 as an audit-only, manifest-selected rule. Seven policy
fixtures cover compliance, forbidden pull-request checks and uploads, direct
token use, missing main upload, missing pull-request ratchet, malformed input,
and reusable-workflow uncertainty. The full branch suite passed with 714 tests
and one intentional skip. Publication and hosted pull-request checks remain.

## Context and orientation

`.github/workflows/ci.yml` runs on pull requests. Its `Generate coverage` step
uses the shared action with `with-ratchet: true`, producing `coverage.xml` and
enforcing the locally cached baseline. A later
`Check coverage against CodeScene gates` step passes `CS_ACCESS_TOKEN`,
`mode: check`, and the project URL to the CodeScene action. That later step
caused the cited failure.

`.github/workflows/coverage-main.yml` runs on pushes to `main`, generates the
same report and ratchet state, and invokes the CodeScene action in upload mode.
It is the topology to retain.

Concordat lint rules live below
`platform-standards/canon/lint-rules/<rule-id>/`. Each package has a
`rule.yaml`, Rego policy and policy tests, fixtures, generated envelope data,
and a README. `concordat/rules/runner.py` loads a package and builds the input
document that Conftest evaluates. The unmerged `mdtablefix-check-rollout`
branch demonstrates dispatching a package to a dedicated envelope builder for
decoded workflows and other rule-specific facts.

## Conformance basis

- Repository instructions: `AGENTS.md` at
  `549b58acd3c3b518d0987ec8982ee4f1a0cadc25`.
- Architecture source: `docs/concordat-design.md` on `origin/main`. It contains
  the general rule-package and three-valued-verdict architecture but no
  accepted main-owned CodeScene rule.
- Reference implementation shape: `mdtablefix-check-rollout` at
  `2b3386f5539beacac742a6d142e76918ab1fbe20`, used as evidence only.
- Operational failure: GitHub Actions run 35104733784, job 104826275145.
- Prior corrected repository topology: Netsuke PR #724.

Trace links:

```plaintext
COV-PR-LOCAL -> EP-M1 -> workflow contract forbidding PR CodeScene actions
COV-MAIN-PUBLISHER -> EP-M1 -> main workflow contract retaining upload
COV-ESTATE-RULE -> EP-M2 -> rule fixtures and Rego policy
COV-THREE-VALUED -> EP-M2 -> malformed and reusable-workflow fixtures
```

## Verification plan

No arithmetic, mutable protocol, or business-logic lemma is introduced. The
owned domain is a finite set of decoded workflow documents, so deterministic
fixtures covering each structural partition are proportionate. Property,
model-checking, and formal-proof machinery would add no meaningful coverage.

- Obligation: `COV-PR-LOCAL`. PR workflows retain one ratcheting coverage
  generator and contain no CodeScene upload/check action or token exposure.
  Method: deterministic YAML contract test. Rationale: workflow jobs and steps
  are finite decoded mappings. Domain: every job and step in
  `.github/workflows/ci.yml`. Artefact: a focused test selected from
  Concordat's workflow tests or a new narrow contract module. Evidence: it
  initially fails on `Check coverage against CodeScene gates`, then passes
  after deletion. Non-vacuity: the test also requires the named generator and
  exact ratchet input; deleting all coverage steps must fail.
- Obligation: `COV-MAIN-PUBLISHER`. The push-to-main workflow is the only
  CodeScene publisher and retains ratchet baseline publication. Method:
  deterministic YAML contract test. Rationale: exact trigger, action, mode, and
  ratchet inputs are enumerable. Domain: every repository workflow plus the
  `coverage-main` job. Artefact: the same focused contract suite. Evidence: the
  existing main workflow is a positive witness; moving its upload to PR CI or
  deleting it is the negative control. Non-vacuity: the test requires one
  upload action and identifies its workflow and trigger rather than merely
  forbidding actions.
- Obligation: `COV-ESTATE-RULE`. The lint rule classifies compliant, PR-check,
  PR-upload, missing-main-upload, missing-ratchet, malformed, and reusable-only
  repositories correctly. Method: parameterized fixture envelopes plus Conftest
  policy tests and one CLI behavioural test. Rationale: these are finite
  workflow-shape equivalence classes. Domain: the listed fixtures, both YAML
  extensions, multiple workflows/jobs, literal booleans, and absent/malformed
  documents. Artefact: a new `main-owned-codescene-coverage` rule package,
  envelope tests, and runner-dispatch tests. Evidence: fixture/policy tests
  fail before the policy exists, then `conftest verify` and the focused pytest
  modules pass. Non-vacuity: the PR-check and PR-upload fixtures must each emit
  a distinct finding, while a fixture modelled on the repaired Concordat
  workflows must be compliant.
- Obligation: `COV-THREE-VALUED`. Unsupported or undecodable workflow shapes
  fail closed as indeterminate rather than compliant. Method: explicit
  malformed and reusable-workflow fixtures. Rationale: static analysis cannot
  soundly infer delegated workflow contents. Domain: YAML parse failures and
  job-level `uses` declarations. Artefact: envelope unit tests and policy
  fixtures. Evidence: each fixture produces the expected indeterminate rule
  identifier. Non-vacuity: a normal local-step workflow remains decidable and
  compliant.

External axioms are limited to GitHub Actions' decoded YAML model, the pinned
shared actions' documented `with-ratchet` and CodeScene modes, and Conftest's
documented evaluation contract. The plan verifies Concordat's own projections
and policies, not those tools' internals.

## Plan of work

Stage A completes discovery and this plan. It ends at the approval gate.

Stage B works in `fix/local-pr-coverage-ratchet`. Add the smallest workflow
contract that requires the local PR ratchet, forbids PR CodeScene integration,
and requires the main upload. Run it red, delete only the obsolete step from
`.github/workflows/ci.yml`, update affected documentation, and rerun the
focused test.

Stage C works independently in `rule/main-owned-codescene-coverage`. Add tests
and fixtures first for the workflow envelope and rule classifications. Run them
red. Implement the smallest envelope builder and runner dispatch, then add the
manifest, Rego policy, policy tests, fixture generator/data, README, design
catalogue entry, and user/developer guidance. Rerun focused tests after each
coherent slice.

Stage D runs `make fmt`, `make check-fmt`, `make lint`, `make typecheck`,
`make test`, `make markdownlint`, and `make nixie` in each affected worktree as
applicable. Review each branch from its merge base, commit atomically, push,
and create two draft pull requests against `main`.

## Milestones and plateaus

- Identifier and outcome: `EP-M1`, Concordat PR CI uses only the main-derived
  local ratchet, while main remains the sole CodeScene publisher. Requirements
  and gaps: `COV-PR-LOCAL`, `COV-MAIN-PUBLISHER`. Acceptance evidence: focused
  workflow test and full repair-branch gates. Conformance check: no coverage
  selection, threshold, dependency, public interface, or external setting
  changes. Recovery: revert the workflow/test commit together. Remaining gaps:
  estate repositories are not yet audited automatically. Compatibility
  decision: none; workflow steps are private CI configuration.
- Identifier and outcome: `EP-M2`, operators can run an audit-only rule that
  enforces main-owned CodeScene coverage publication. Requirements and gaps:
  `COV-ESTATE-RULE`, `COV-THREE-VALUED`. Acceptance evidence: Conftest
  fixtures, focused pytest/BDD tests, CLI witness, and full rule-branch gates.
  Conformance check: rule-package architecture and three-valued verdicts remain
  intact; no public CLI signature or dependency changes. Recovery: revert the
  independent rule commit without affecting EP-M1. Remaining gaps: the unmerged
  RFC branch must reconcile its contrary CV-001 wording separately.
  Compatibility decision: none; the new rule identifier has no consumers.

## Revision note

2026-09-16: Initial draft records the two independent deliverables, the
contrary unmerged RFC, the workflow-envelope approach, and the verification
obligations. Implementation awaits explicit approval.

2026-09-16: Approval moved the plan into execution. EP-M1 started with the
workflow contract described above; no scope or verification obligation changed.

2026-09-16: EP-M1 implementation removed the obsolete pull-request CodeScene
check while retaining the local ratchet and explicit main-only upload. The
design catalogue and roadmap now describe that topology.

2026-09-16: EP-M2 added the decoded-workflow envelope and CV-005 policy on an
independent branch. Real-repository evidence made reusable workflow calls
indeterminate, preserving the plan's three-valued classification requirement.
