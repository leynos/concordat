# Concordat implementation roadmap

The roadmap below sequences the delivery of Concordat, the GitHub estate
management suite. Work prioritizes configuration health and consistency,
focusing on merge behaviour, branch governance, and issue prioritization.
Phases build cumulatively; each phase depends on completion of preceding steps.

## Phase 1 – Audit foundations for the GitHub estate

Phase 1 establishes authoritative configuration sources, telemetry, and audit
visibility without altering production behaviour.

### Step: Bootstrap the platform-standards repository

Define the single source of truth for desired organization state and reusable
automation assets.

- [x] Author repository, branch, and team modules in `platform-standards/tofu`
      with merge strategy flags, branch deletion, and default permission maps.
      Acceptance: `tofu validate` and `tofu test` succeed in continuous
      integration using mocked providers.
- [x] Populate canonical workflow, policy, and template directories referenced
      by the Auditor. Acceptance: repository consumers can fetch pinned
      artefacts via the documented manifest path.
- [ ] Check in an initial organization inventory dataset to seed drift reports.
      Acceptance: nightly Auditor dry runs can enumerate the full repository
      catalogue without missing entries.
- [x] Extend `concordat enrol` so that, in addition to writing `.concordat`, it
      opens a pull request in `platform-standards` adding the repository to the
      OpenTofu inventory. Acceptance: enrolling a repository produces both the
      `.concordat` commit (optional push) and a passing IaC PR that runs
      `tofu fmt`, `tflint`, and `tofu validate`.

### Step: Stand up non-blocking audit execution

Surface configuration drift and compliance gaps while keeping enforcement in
evaluate mode.

- [x] Package the Auditor GitHub Action with SARIF output, covering merge mode,
      branch protection, permission, and label checks. Acceptance: scheduled
      runs populate the Code Scanning dashboard with classified findings.
- [ ] Schedule OpenTofu plans against a sandbox organization identity using the
      GitHub provider. Acceptance: nightly plans complete under one hour with
      drift deltas archived.
- [ ] Wire compliance telemetry into the reporting stack, producing a baseline
      scorecard for repository posture. Acceptance: the platform team can rank
      repositories by configuration risk within the dashboard.
- [ ] Publish the `concordat/file` OpenTofu provider data source that evaluates
      Rego planner rules and emits RFC 6902 patches for TOML manifests (e.g.,
      `Cargo.toml`). Acceptance: nightly `tofu plan` consumes the data source
      and fails with a descriptive summary when `patch_count > 0`.

## Phase 2 – Enforce merge and branch governance

Phase 2 activates enforced guardrails for pull requests and branch hygiene,
raising the quality bar while providing remediation tooling.

### Step: Promote merge policy to an enforced ruleset

Enable merge gating based on Auditor status and standardized repository flags.

- [ ] Convert the organization ruleset module to `enforcement = "active"` once
      Phase 1 telemetry shows false positive rates under 5 per cent. Acceptance:
      protected branches require the Auditor check and block merges on
      `error`-level findings.
- [ ] Apply `delete_branch_on_merge = true` across managed repositories via
      OpenTofu. Acceptance: drift reports show zero repositories with the flag
      disabled after `tofu apply`.
- [ ] Restrict merge strategies to the approved set (squash only unless the
      manifest opts into rebase). Acceptance: Auditor reports no repositories
      exposing disallowed merge modes.

### Step: Standardize branch protections

Codify consistent branch rules with measurable compliance.

- [ ] Enforce required status checks, dismissal of stale reviews, and linear
      history on default branches through OpenTofu-managed rulesets. Acceptance:
      GitHub API inspection returns the prescribed configuration for 100 per
      cent of governed repositories.
- [ ] Integrate change control exemptions via `standards-exemptions.yaml`,
      ensuring expiry dates exist for all branch rule relaxations. Acceptance:
      Auditor downgrades exempted findings to `note` while flagging missing or
      expired exemptions.
- [ ] Deliver runbooks for resolving merge gate failures, validated with three
      pilot repository teams. Acceptance: post-pilot feedback scores the
      guidance at least 4/5 for clarity.
- [ ] Add the `concordat_file_toml_remediation_pr` resource that applies the
      planned patches with comment-preserving edits, commits to a branch, and
      opens a remediation PR. Acceptance: an operator-triggered `tofu apply`
      run creates a PR touching `Cargo.toml` without altering comments or the
      default branch directly.

## Phase 3 – Institutionalize issue prioritization

Phase 3 introduces the canonical priority taxonomy, aligns GitHub Projects with
it, and enforces the standard via automated sync and audit pipelines.

### Step: Publish the canonical priority model

Create the single source of truth for priority semantics and expose it to all
downstream automation.

- [ ] Author `canon/priorities/priority-model.yaml` in
      `platform-standards` with the `P0`–`P3` label metadata, Projects v2
      field schema, and alias mappings. Acceptance: the file ships with unit
      tests that load and validate its structure.
- [ ] Version the model with a Git tag (for example, `priorities/v1`) and
      document the change control process. Acceptance: both OpenTofu modules
      and the Auditor pin to the tag in their configs.
- [ ] Announce the model and migration plan to repository owners, providing a
      playbook for interpreting the new priority names. Acceptance: feedback
      survey records >80 per cent comprehension among pilot teams.

### Step: Apply labels and Projects fields declaratively

Roll out the canonical state across the estate using OpenTofu.

- [ ] Deliver `modules/repo-priority-labels` and
      `modules/projects-v2-priority-field` under `platform-standards/tofu`,
      including documentation and examples. Acceptance: `tofu test` succeeds
      for both modules with fixtures representing real repositories and
      projects.
- [ ] Update the top-level OpenTofu configuration to iterate over the managed
      repository list and relevant Projects v2 boards. Acceptance: a dry run
      `tofu plan` enumerates intended label and field changes without
      attempting out-of-scope mutations.
- [ ] Run a pilot `tofu apply` against 10 per cent of repositories and two
      Projects boards. Acceptance: Auditor drift reports confirm no unexpected
      changes, and affected teams sign off on the new labels.

### Step: Wire synchronization and audit enforcement

Keep labels and project fields consistent and make the configuration
non-optional.

- [ ] Publish `canon/.github/workflows/priority-sync.yml`, the reusable
      workflow that keeps Projects Priority fields and issue labels in sync.
      Acceptance: two pilot repositories consume the workflow and report no
      sync drift over a two-week trial.
- [ ] Extend the Auditor with PR-001 through PR-004 priority checks (as
      defined in the design doc) and ship them initially as warnings. Acceptance:
      Auditor SARIF output shows the new rule IDs with actionable guidance.
- [ ] Open organization-wide PRs (via `multi-gitter`) to adopt the sync
      workflow and raise the Auditor checks to `error` once false positive
      rates drop under five per cent. Acceptance: Code Scanning gates block
      merges that violate the canonical model after the enforcement switch.

## Phase 4 – Sustain and expand automation

Phase 4 reduces manual effort and broadens coverage once governance is stable.

### Step: Automate safe remediations and onboarding

Scale Concordat with self-service and targeted automation.

- [ ] Identify configuration drifts suitable for automatic correction (for
      example, reenabling branch protection settings) and gate them behind
      guarded `tofu apply` jobs. Acceptance: automated remediations resolve at
      least 80 per cent of recurring drift categories without manual follow-up.
- [ ] Ship a self-service onboarding CLI that provisions manifests, applies the
      required labels, and scaffolds workflows. Acceptance: three pilot teams
      onboard new repositories end-to-end without platform intervention.
- [ ] Implement periodic policy retrospectives using compliance metrics to
      retire redundant checks and capture new governance requirements.
      Acceptance: each quarter concludes with an action list approved by the
      platform steering group.
