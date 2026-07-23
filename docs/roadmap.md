# Concordat implementation roadmap

The roadmap below sequences the delivery of Concordat, the GitHub estate
management suite. Work prioritizes configuration health and consistency,
focusing on merge behaviour, branch governance, and issue prioritization.
Phases build cumulatively; each phase depends on completion of preceding steps.

## 1. Audit foundations for the GitHub estate

Phase 1 establishes authoritative configuration sources, telemetry, and audit
visibility without altering production behaviour.

### 1.1. Bootstrap the platform-standards repository

Define the single source of truth for desired organization state and reusable
automation assets.

- [x] Author repository, branch, and team modules in `platform-standards/tofu`
  with merge strategy flags, branch deletion, and default permission maps.
  Acceptance: `tofu validate` and `tofu test` succeed in continuous integration
  using mocked providers.
- [x] Populate canonical workflow, policy, and template directories referenced
  by the Auditor. Acceptance: repository consumers can fetch pinned artefacts
  via the documented manifest path.
- [ ] Check in an initial organization inventory dataset to seed drift reports.
  Acceptance: nightly Auditor dry runs can enumerate the full repository
  catalogue without missing entries.
- [x] Extend `concordat enrol` so that, in addition to writing `.concordat`, it
  opens a pull request in `platform-standards` adding the repository to the
  OpenTofu inventory. Acceptance: enrolling a repository produces both the
  `.concordat` commit (optional push) and a passing IaC PR that runs `tofu fmt`,
  `tflint`, and `tofu validate`.
- [x] Teach estates about the GitHub namespace they govern by persisting
  `github_owner` in the concordat config file and rejecting enrolments that
  target other owners. Acceptance: `concordat estate init` records the owner and
  `concordat enrol` refuses to add repositories whose slug does not begin with
  it; invoking `concordat ls` without namespaces defaults to the recorded owner.

### 1.2. Introduce canonical artefact management

Provide a first-class workflow for comparing and deploying canonical
platform-standards artefacts (including lint rule packages) from the Concordat
template tree into published platform-standards repositories.

- [x] Ship a manifest-driven artefact status and sync tool with both table and
  interactive user interfaces. Acceptance:
  `python -m scripts.canon_artifacts status <published-root>` prints a stable
  table; `python -m scripts.canon_artifacts tui <published-root>` launches a
  Textual menu when dev dependencies are installed; unit tests cover
  missing/out-of-date cases.
- [x] Document the canonical artefact synchronization workflow in the users'
  guide and design documentation. Acceptance: operators can follow the docs to
  identify drift and deploy updates into a local platform-standards checkout.
- [ ] Integrate the artefact tooling into the main CLI as `concordat artefact`
  with `catalogue`, `estate`, and `rule` command groups. Acceptance:
  `concordat artefact catalogue list` and `concordat artefact estate status`
  behave consistently with the spike tooling and support `--format json` for
  automation.
- [ ] Define a semantically versioned canonical manifest schema (for example
  `schema_version: 2`) that adds `version` while retaining integrity metadata
  (sha256). Acceptance: concordat can read both schema versions and reports
  “template vs published version” in `concordat artefact estate status`.
- [ ] Introduce a lockfile for published estates (for example
  `canon/artefacts.lock.yaml`) that records deployed versions and estate-owned
  configuration overrides. Acceptance: status commands rely on the lockfile for
  version reporting; sync operations update the lockfile deterministically.
- [ ] Define a lint rule package format under `canon/lint-rules/<rule-id>/`
  with a `rule.yaml` entrypoint describing sensor inputs, parameters, and
  mutations. Acceptance: `concordat artefact rule validate <rule-id>` validates
  the rule schema, runs policy tests where present, and surfaces parameter
  defaults and allowed overrides.

### 1.3. Ship the estate execution CLI

Connect the estate configuration template to OpenTofu execution to let
operators preview and apply changes from concordat.

- [x] Cache estate repositories under the concordat X Desktop Group (XDG) cache
  directory (for example, `~/.cache/concordat/estates/<alias>`) and clone the
  cached state into a temporary execution directory for each run. Acceptance:
  repeated executions of the same command reuse the cache, but leave no residue
  in `/tmp` unless `--keep-workdir` is passed.
- [x] Implement `concordat plan`, using tofupy to run `tofu plan` inside the
  execution directory after synthesizing `terraform.tfvars` from the estate
  metadata. Acceptance: the command clones the active estate, writes a tfvars
  file containing the `github_owner` (GitHub owner) value, requires
  `GITHUB_TOKEN`, and exits with the same status code as OpenTofu.
- [x] Implement `concordat apply` with the same workspace preparation but using
  tofupy's apply entrypoint (and support for `--auto-approve`). Acceptance: the
  command reconciles the estate against the cached repository and reports
  success/failure without leaving temporary files behind.

### 1.4. Persist estate tfstate in Scaleway Object Storage

Move OpenTofu state into a shared, versioned backend so operators and
Continuous Integration (CI) jobs never diverge. Remote persistence also unlocks
locking and rollbacks without adding DynamoDB or other AWS-only dependencies.

- [x] Add `platform-standards/tofu/backend.tf` and the accompanying backend
  directory, declaring the `s3` backend with no inline credentials. Acceptance:
  running `tofu init -backend-config backend/scaleway.tfbackend` succeeds
  locally and in CI, and the required OpenTofu version is pinned to `>= 1.12.0`.
- [x] Implement `concordat estate persist` as an interactive workflow that
  prompts for bucket, region, endpoint, and key suffix, validates that the
  Scaleway bucket has versioning enabled, writes `backend/<alias>.tfbackend`
  plus `backend/persistence.yaml`, and opens a pull request with the change.
  Acceptance: pytest-bdd coverage exercises success, validation failure, and
  `--force` replacement flows without leaking credentials to disk.
- [x] Teach `concordat plan`/`concordat apply` to read `persistence.yaml`, pass
  `-backend-config` to `tofu init`, and refuse to run when the expected
  AWS/Scaleway environment variables are missing. Acceptance: integration tests
  confirm remote state is used when configured, local state remains untouched
  otherwise, and logs expose bucket/key details but no secrets.
- [x] Extend `docs/users-guide.md` with operator guidance (sourced from Section
  2.8.4 of the design doc) that explicitly documents: (1) how to set required
  environment variables for AWS (`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`,
  plus optional `AWS_SESSION_TOKEN` for temporary credentials) and for Scaleway
  (`SCW_ACCESS_KEY` + `SCW_SECRET_KEY`, which the CLI maps onto the AWS
  variable names); (2) lock troubleshooting steps and common failure modes; and
  (3) disaster-recovery procedures using bucket versioning, including how to
  locate and restore specific object version IDs. Acceptance:
  `make markdownlint`, `make fmt`, and `make nixie` continue to pass after the
  documentation changes.

### 1.5. Stand up non-blocking audit execution

Surface configuration drift and compliance gaps while keeping enforcement in
evaluate mode.

- [x] Package the Auditor GitHub Action with Static Analysis Results Interchange
  Format (SARIF) output, covering merge mode, branch protection, permission,
  and label checks. Acceptance: scheduled runs populate the Code Scanning
  dashboard with classified findings.
- [ ] Schedule OpenTofu plans against a sandbox organization identity using the
  GitHub provider. Acceptance: nightly plans complete under one hour with drift
  deltas archived.
- [ ] Wire compliance telemetry into the reporting stack, producing a baseline
  scorecard for repository posture. Acceptance: the platform team can rank
  repositories by configuration risk within the dashboard.
- [ ] Publish the `concordat/file` OpenTofu provider data source that evaluates
  Rego planner rules and emits RFC 6902 patches for TOML manifests (e.g.,
  `Cargo.toml`). Acceptance: nightly `tofu plan` consumes the data source and
  fails with a descriptive summary when `patch_count > 0`.

## 2. Enforce merge and branch governance

Phase 2 activates enforced guardrails for pull requests and branch hygiene,
raising the quality bar while providing remediation tooling.

### 2.1. Promote merge policy to an enforced ruleset

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

### 2.2. Standardize branch protections

Codify consistent branch rules with measurable compliance.

- [ ] Enforce required status checks, dismissal of stale reviews, and linear
  history on default branches through OpenTofu-managed rulesets. Acceptance:
  GitHub API inspection returns the prescribed configuration for 100 per cent
  of governed repositories.
- [ ] Integrate change control exemptions via `standards-exemptions.yaml`,
  ensuring expiry dates exist for all branch rule relaxations. Acceptance:
  Auditor downgrades exempted findings to `note` while flagging missing or
  expired exemptions.
- [ ] Deliver runbooks for resolving merge gate failures, validated with three
  pilot repository teams. Acceptance: post-pilot feedback scores the guidance
  at least 4/5 for clarity.
- [ ] Add the `concordat_file_toml_remediation_pr` resource that applies the
  planned patches with comment-preserving edits, commits to a branch, and opens
  a remediation PR. Acceptance: an operator-triggered `tofu apply` run creates
  a PR touching `Cargo.toml` without altering comments or the default branch
  directly.

## 3. Institutionalize issue prioritization

Phase 3 introduces the canonical priority taxonomy, aligns GitHub Projects with
it, and enforces the standard via automated sync and audit pipelines.

### 3.1. Publish the canonical priority model

Create the single source of truth for priority semantics and expose it to all
downstream automation.

- [ ] Author `canon/priorities/priority-model.yaml` in `platform-standards` with
  the `P0`–`P3` label metadata, Projects v2 field schema, and alias mappings.
  Acceptance: the file ships with unit tests that load and validate its
  structure.
- [ ] Version the model with a Git tag (for example, `priorities/v1`) and
  document the change control process. Acceptance: both OpenTofu modules and
  the Auditor pin to the tag in their configs.
- [ ] Announce the model and migration plan to repository owners, providing a
  playbook for interpreting the new priority names. Acceptance: feedback survey
  records >80 per cent comprehension among pilot teams.

### 3.2. Apply labels and Projects fields declaratively

Roll out the canonical state across the estate using OpenTofu.

- [ ] Deliver `modules/repo-priority-labels` and
  `modules/projects-v2-priority-field` under `platform-standards/tofu`,
  including documentation and examples. Acceptance: `tofu test` succeeds for
  both modules with fixtures representing real repositories and projects.
- [ ] Update the top-level OpenTofu configuration to iterate over the managed
  repository list and relevant Projects v2 boards. Acceptance: a dry run
  `tofu plan` enumerates intended label and field changes without attempting
  out-of-scope mutations.
- [ ] Run a pilot `tofu apply` against 10 per cent of repositories and two
  Projects boards. Acceptance: Auditor drift reports confirm no unexpected
  changes, and affected teams sign off on the new labels.

### 3.3. Wire synchronization and audit enforcement

Keep labels and project fields consistent and make the configuration
non-optional.

- [ ] Publish `canon/.github/workflows/priority-sync.yml`, the reusable GitHub
  workflow that keeps Projects Priority fields and issue labels in sync.
  Acceptance: two pilot repositories consume the workflow and report no sync
  drift over a two-week trial.
- [ ] Extend the Auditor with PR-001 through PR-004 priority checks (as defined
  in the design doc) and ship them initially as warnings. Acceptance: Auditor
  SARIF output shows the new rule IDs with actionable guidance.
- [ ] Open organization-wide PRs (via `multi-gitter`) to adopt the sync workflow
  and raise the Auditor checks to `error` once false positive rates drop under
  five per cent. Acceptance: Code Scanning gates block merges that violate the
  canonical model after the enforcement switch.

## 4. Sustain and expand automation

Phase 4 reduces manual effort and broadens coverage once governance is stable.

### 4.1. Automate safe remediations and onboarding

Scale Concordat with self-service and targeted automation.

- [ ] Identify configuration drifts suitable for automatic correction (for
  example, reenabling branch protection settings) and gate them behind guarded
  `tofu apply` jobs. Acceptance: automated remediations resolve at least 80 per
  cent of recurring drift categories without manual follow-up.
- [ ] Ship a self-service onboarding CLI that provisions manifests, applies the
  required labels, and scaffolds workflows. Acceptance: three pilot teams
  onboard new repositories end-to-end without platform intervention.
- [ ] Implement periodic policy retrospectives using compliance metrics to
  retire redundant checks and capture new governance requirements. Acceptance:
  each quarter concludes with an action list approved by the platform steering
  group.

### 4.2. Enforce quality-gate integrity

Deliver the Quality-Gate Integrity audit domain (design document Section
3.1.1): sensors that detect quality gates which cannot fail or never run, and
actuators that remediate them. Each check ships as a lint rule package under
`canon/lint-rules/` per the Section 2.1.2 format.

- [ ] Add the `github-api` sensor and actuator types to the lint rule package
  contract (Section 2.1.2) before shipping any API-backed check, including the
  observability requirements of Section 3.2.2. The `conftest` sensor over a
  static input tree cannot express checks that read live repository state, and
  deterministic-edit mutations cannot post comments or open issues. Acceptance:
  `concordat artefact rule validate` accepts a package declaring
  `sensor.type: github-api` and `github-api` actuators (`comment`, `issue`);
  `rule run` executes an authenticated query against a recorded fixture and
  `rule mutate` performs the side effect against a mocked API; each emits a
  structured log line carrying the check ID, operation, and entity IDs, and the
  sweep publishes bounded per-check metrics and fires an alert on error or
  incompletion. This item is a prerequisite for CV-003, AM-001, AM-002, DP-001,
  and DP-002.
- [ ] Ship the lint-gate binding rule packages (QG-001 to QG-003): Makefile
  sensors for soft-skipped or environment-overridable lint targets, workflow
  sensors for the hardened pinned-release install step (version-keyed cache,
  shell-variable indirection, `--locked`, binstall-or-build fallback, Cranelift
  preservation), and a rolling-release detector with a suite-ref-pin mutation.
  Acceptance: fixtures reproducing the Whitaker rollout defects (soft-skip
  Makefile, `WHITAKER=true` no-op, git-rev install with stale cache key) each
  raise the intended finding, and the mutations produce the canonical forms.
- [ ] Ship the test-runner completeness rule package (QG-004): sensors for
  nextest-only suites lacking a doctest target, unlocked test-tool installs,
  and missing `TEST_CMD` fallback; mutations patch the Makefile with
  `TEST_CMD`, a `test-doc` target, and aggregate wiring. Acceptance: a fixture
  whose doctests are never executed is detected, and the mutated Makefile runs
  doctests under `make test`.
- [ ] Ship the coverage-pipeline rule packages (CV-001, CV-002, CV-004):
  pull-request jobs must gate via `cs-coverage check` with `fetch-depth: 0`, a
  `project-url`, and `*.info` LCOV naming; a main-only push workflow must
  upload; exactly one ratcheting invocation per job with the baseline written
  on main. Acceptance: fixtures for upload-from-PR, missing main workflow,
  summary-only pins, and PR-scoped baselines each raise findings; mutations
  emit the canonical coverage-main workflow and job patches.
- [ ] Implement the dual-store secret sensor (CV-003) in the Auditor:
  enumerate secret names in the Actions and Dependabot stores via the GitHub
  API and cross-reference every `if: env.X != ''` workflow guard. Acceptance: a
  repository whose guard secret exists in only one store is reported with the
  absent store named; `concordat` gains a provisioning command that sets an
  operator-supplied token in both stores.
- [ ] Implement the automerge-jam and workflow-health sensors (AM-001,
  AM-002) as scheduled Auditor sweeps: Dependabot pull requests `BLOCKED`
  specifically by a stale or timed-out required status check (with all other
  merge requirements satisfied), and workflows whose recent runs uniformly
  conclude `startup_failure`. Acceptance: the AM-001 sensor classifies the
  block cause and comments `@dependabot rebase` only on stale-check jams,
  leaving a fixture blocked by a missing approval untouched; the AM-002
  actuator opens a tracking issue; both actuators are idempotent.
- [ ] Implement the dependency-pin actionability sensors (DP-001, DP-002):
  cross-reference open Dependabot alerts' first patched versions against
  manifest requirements, and detect git-revision pins lacking a
  `TODO(<issue-url>)` resolving to an open issue. Acceptance: a fixture
  manifest pinning below a patched version raises DP-001 with the blocked alert
  numbers; the DP-002 actuator inserts the `TODO` via the comment-preserving
  TOML remediation provider and raises the tracking issue.
- [ ] Ship the Dependabot governance rule packages (DB-001 to DB-004):
  manifest-scan sensor diffing package roots against `dependabot.yml` entries,
  cooldown policy checks (tiered for semver ecosystems, `default-days` for
  non-semver), pinned shared auto-merge workflow verification, and detection of
  lockfile-wide audit steps gating Dependabot pull requests paired with a
  scheduled-audit presence check. Acceptance: fixtures reproducing the estate
  defects (uncovered workspace member, deadlocking audit gate) raise findings,
  and mutations patch `dependabot.yml` and deploy the canonical workflows.
- [ ] Ship the mutation-testing rule package (MT-001): sensors for the
  scheduled workflow calling the pinned shared mutation-testing workflow
  without being merge-blocking; the mutation deploys the canonical workflow.
  Acceptance: a repository without mutation testing raises the finding and the
  deployed workflow passes `act` validation.

### 4.3. Enforce licensing integrity and toolchain baselines

Deliver the Licensing Integrity and Toolchain Baseline audit domains (design
document Sections 3.1.2 and 3.1.3): licence presence, currency, and
declared-licence consistency for every repository, and language toolchain
floors pinned to the `leynos/agent-template-python` and
`leynos/agent-template-rust` templates. Each check ships as a lint rule package
under `canon/lint-rules/` per the Section 2.1.2 format.

- [ ] Ship the licensing rule packages (LC-001 to LC-003): root `LICENSE`
  presence, copyright year matched against the latest commit's committer year,
  and SPDX identity of each `LICENSE` cross-referenced against manifest and
  README declarations under the nearest-ancestor rule. Acceptance: fixtures for
  a missing `LICENSE`, a stale year range, and a manifest declaring a different
  licence from its governing `LICENSE` each raise the intended finding; the
  LC-002 mutation extends the year range, and LC-001 degrades to a tracking
  issue when no manifest names a licence. The LC-002 year comparator and the
  LC-003 nearest-ancestor resolver carry Hypothesis property tests for the
  invariants in Section 3.1.4 (totality and monotonicity of resolution, LC-002
  mutation idempotence), with mutmut confirming the properties bite.
- [ ] Build the Python applicability sensor and vendor the
  `agent-template-python` baseline: enumerate `*.py` files, `pyproject.toml`
  manifests, Python-invoking workflow steps, and Ansible plugin directories;
  extract the template's ruff and pylint baselines at a pinned tag into
  rule-package data. Acceptance: fixtures modelling incidental Python (a Rust
  repository with helper scripts, a Python-implemented GitHub Action, an
  Ansible collection) are all detected as in scope, and the vendored baseline
  regenerates deterministically from the pinned tag.
- [ ] Ship the Python formatting and linting rule packages (PY-001 to
  PY-005): ruff format and check wiring bound into the format and lint gates,
  pylint present via `pylint-pypy-shim`, and both configurations matching or
  exceeding the vendored template baseline. Acceptance: fixtures with a missing
  format target, a soft-skipped lint step, a disabled template rule, and an
  over-broad ignore list each raise findings; mutations patch the configuration
  without disturbing comments.
- [ ] Ship the Python documentation, version-floor, and tooling rule packages
  (PY-006 to PY-010): interrogate at `fail-under = 100`, a `requires-python`
  floor of at least 3.12, version declarations reconciled against that floor
  (scalar declarations equal the floor; matrices keep their minimum at the
  floor with no lower entry), and pytest-xdist and ty wiring with the exemption
  path honoured. Acceptance: a fixture declaring 3.11 in CI against a 3.12
  manifest floor raises PY-008 naming the divergent file, while a fixture whose
  CI matrix tests 3.12 and 3.13 against a 3.12 floor raises nothing; an
  exempted fixture downgrades PY-009 to `note`. The PY-008
  version-reconciliation comparator carries Hypothesis property tests for the
  floor and matrix invariants of Section 3.1.4 (including the metamorphic
  relations: a version above the floor never becomes a finding, a version below
  it always does).
- [ ] Ship the Rust formatting and linting rule packages (RT-001 to RT-005):
  rustfmt wiring and template-matched configuration, clippy presence with
  `[lints]` entries at the template level or stricter, and Whitaker presence
  delegating gate bindingness to `rust-makefile-baseline`. Acceptance: fixtures
  with drifted `rustfmt.toml` keys and a downgraded `[lints]` entry each raise
  findings; mutations restore the canonical values comment-preservingly.
- [ ] Ship the Rust toolchain and acceleration rule packages (RT-006 to
  RT-011): nightly pins no older than one year, required toolchain components,
  mold and Cranelift development configuration, Polonius-next for
  application-only repositories, and nextest via the canonical `TEST_CMD`
  fallback. Acceptance: fixtures for a stale nightly, a missing `rust-analyzer`
  component, and a binary-only crate without Polonius-next each raise findings;
  RT-006 opens a tracking issue rather than patching the pin, and the RT-011
  mutation reuses the QG-004 Makefile patch. The RT-006 nightly-age comparator
  carries a Hypothesis property test asserting the one-year boundary of Section
  3.1.4.
