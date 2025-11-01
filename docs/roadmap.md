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

- [ ] Author repository, branch, and team modules in `platform-standards/tofu`
      with merge strategy flags, branch deletion, and default permission maps.
      Acceptance: `tofu validate` and `tofu test` succeed in continuous
      integration using mocked providers.
- [ ] Populate canonical workflow, policy, and template directories referenced
      by the Auditor. Acceptance: repository consumers can fetch pinned
      artefacts via the documented manifest path.
- [ ] Check in an initial organization inventory dataset to seed drift reports.
      Acceptance: nightly Auditor dry runs can enumerate the full repository
      catalogue without missing entries.

### Step: Stand up non-blocking audit execution

Surface configuration drift and compliance gaps while keeping enforcement in
evaluate mode.

- [ ] Package the Auditor GitHub Action with SARIF output, covering merge mode,
      branch protection, permission, and label checks. Acceptance: scheduled
      runs populate the Code Scanning dashboard with classified findings.
- [ ] Schedule OpenTofu plans against a sandbox organization identity using the
      GitHub provider. Acceptance: nightly plans complete under one hour with
      drift deltas archived.
- [ ] Wire compliance telemetry into the reporting stack, producing a baseline
      scorecard for repository posture. Acceptance: the platform team can rank
      repositories by configuration risk within the dashboard.

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

## Phase 3 – Institutionalize issue prioritization

Phase 3 introduces the issue taxonomy and routing logic aligned to Concordat's
incident posture.

### Step: Roll out organization-wide labels and policies

Ensure every managed repository exposes consistent priority signals.

- [ ] Create GitHub labels for low (`#64b0bc`), medium (`#f3badd`), high
      (`#f64fbb`), and critical (`#9b360c`) severities via OpenTofu resources.
      Acceptance: Auditor verification finds the colour hex and descriptions
      applied identically across repositories.
- [ ] Add Auditor checks ensuring new issues and pull requests include an
      approved priority label or a documented exemption. Acceptance: 95 per
      cent of issues opened during the pilot window carry a valid priority
      within 48 hours.
- [ ] Document escalation pathways mapping the priority bands to response
      expectations and link them in repository templates. Acceptance: template
      updates propagate through `multi-gitter` pull requests merged by at least
      80 per cent of repositories within the enforcement cohort.

### Step: Embed signals into workflow automation

Connect priority designations to operational tooling.

- [ ] Update notification pipelines so that high and critical issues trigger
      paging hooks, while low and medium route to backlog queues. Acceptance:
      incident drills confirm delivery to the correct channels.
- [ ] Extend dashboards to visualize priority distribution and ageing trends.
      Acceptance: stakeholders can segment issues by priority and repository in
      under five seconds per query.
- [ ] Add backlog hygiene tasks to the compliance scoreboard, flagging issues
      without updates beyond 30 days per priority band. Acceptance: overdue
      issue counts trend downward for two consecutive reporting cycles.

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
