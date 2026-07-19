# Operation Parabellum: minimal vertical slice across the Rust estate

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
and `Outcomes & retrospective` must be kept up to date as work proceeds.

Status: IN PROGRESS

## Purpose / big picture

Concordat is the treaty: a policy platform that audits and eventually
remediates the leynos GitHub estate. Operation Parabellum is the campaign that
rolls it out. This plan delivers the first end-to-end vertical slice: a local,
audit-only capability that answers, for every Rust repository in the estate,
two questions with evidence:

1. Does the repository's root `Makefile` define the canonical `build`, `test`,
   and `lint` targets unconditionally? (Rule FP-003, already catalogued in
   `docs/concordat-design.md` §3.1.)
2. Is the lint gate binding — that is, can nothing in the `Makefile` silently
   skip or neuter the Whitaker lint run? (Rule QG-001, introduced by this
   slice.)

After this change, a user can run:

```shell
concordat artefact rule run rust-makefile-baseline --repo /path/to/checkout
```

against any local Rust checkout and receive a table (or JSON) of findings with
stable rule identifiers, severities, and source locations; and can run a sweep
script that clones every Rust repository in the estate at a pinned commit,
audits it, and appends one machine-readable record per repository to a campaign
ledger. The observable outcome of the whole plan is a committed baseline
compliance report for roughly fifty Rust repositories.

The slice is deliberately audit-only. No remediation, no pull requests, no
GitHub writes, no OpenTofu changes. Those are later waves; doctrine (policy
semantics) stays under human control throughout.

## Constraints

Hard invariants. Violation requires escalation, not workarounds.

- Audit-only: this slice must not write to any repository other than this one.
  No branches, commits, pull requests, or API mutations against estate
  repositories. Estate access is read-only cloning at pinned commits.
- Policy semantics are doctrine: the meaning of FP-003 and QG-001 may not be
  weakened mid-campaign to get a troublesome repository through. Recurring
  exceptions become design work recorded in `docs/concordat-design.md`, not
  quiet waivers.
- Fail closed: a `makeutil` recovered parse (exit 1), any `Makefile`
  `include` directive, or any construct the fact model cannot prove results in
  an `indeterminate` verdict for the affected rule, never a silent pass.
- Pinned tooling: `makeutil` is pinned to an exact git commit;
  `conftest` stays at the version already used in CI (v0.52.0); the rule
  package carries a SemVer version. A sweep records the versions it ran with.
- Existing public interfaces stay stable: no changes to the existing
  `concordat` commands (`enrol`, `ls`, `disenrol`, `plan`, `apply`,
  `estate ...`), the auditor package, or the OpenTofu configuration under
  `platform-standards/tofu/`.
- All commit gates pass before each commit: `make check-fmt`, `make lint`,
  `make typecheck`, `make test`, and for Markdown `make markdownlint` and
  `make nixie`. Documentation follows `docs/documentation-style-guide.md`
  (en-GB Oxford spelling).
- Test-first: every new Python unit follows Red-Green-Refactor; the CLI
  behaviour is specified by a pytest-bdd feature before implementation; the
  Rego policy is specified by conftest/OPA policy tests and fixtures before the
  policy is written.

## Tolerances (exception triggers)

- Scope: if the concordat-side implementation (excluding fixtures and tests)
  exceeds roughly 1,200 net new lines of Python, stop and escalate — the slice
  is growing beyond "minimal".
- Interface: if delivering the slice requires changing an existing public CLI
  signature or the `.concordat` manifest schema, stop and escalate.
- Dependencies: adding any new Python runtime dependency beyond what
  `pyproject.toml` already declares requires escalation. (Dev-only additions
  for testing are within tolerance if justified in the Decision log.)
- Upstream: if `makeutil` on branch `adr-0001-single-file-gnu-make-parse`
  turns out to lack a fact the policy needs (for example, target-specific
  variables), do not extend concordat with a second Makefile parser — stop and
  escalate so the gap is fixed in `makeutil` itself.
- Iterations: if a red test still fails after three green attempts, or a gate
  fails three consecutive runs for the same cause, stop and escalate.
- Sweep blast radius: if more than 10 of the first 15 swept repositories come
  back `indeterminate`, stop the sweep and escalate — the fact model or policy
  is poorly tuned and continuing would produce a useless baseline.
- Ambiguity: if a policy question admits two materially different readings
  (for example, whether `lint-clippy`-only repositories without Whitaker count
  as noncompliant or merely warned), stop and present options.

## Risks

- Risk: `makeutil` is on an unmerged branch of a separate repository; the
  branch may be rebased or the PR may change the JSON contract. Severity:
  medium. Likelihood: medium. Mitigation: pin to an exact commit SHA, not the
  branch name; validate every report against
  `schemas/makeutil.parse.v1.schema.json` semantics (check
  `schema_version == 1` and the fields concordat reads); record the pin in the
  ledger so a re-run is reproducible.
- Risk: `makeutil` requires a pinned nightly toolchain and a
  `[patch.crates-io]` fork of `makefile-lossless`, so `cargo install --git` is
  the only install route until a release is tagged. Severity: low. Likelihood:
  high. Mitigation: document the exact install command; treat a tagged
  `makeutil` release with binstall assets as a follow-up, not a blocker.
- Risk: the estate's real Makefiles use constructs the conservative fact
  model cannot prove (heavy `include`, generated targets), producing an
  indeterminate flood. Severity: medium. Likelihood: medium. Mitigation: the
  reference adopters (wireframe, netsuke, weaver) use the simple
  `WHITAKER ?= whitaker` + `lint:` pattern, so canaries should parse cleanly;
  the sweep tolerance above catches a flood early; indeterminate is a
  legitimate campaign datum, not a failure of the slice.
- Risk: QG-001 has no prior design-document draft (only FP-003 is
  catalogued), so its semantics could drift during implementation. Severity:
  medium. Likelihood: medium. Mitigation: Milestone B fixes the QG-001 checks
  in prose and fixtures before any Rego is written, and Milestone F records the
  rule in the design document's check catalogue.
- Risk: cloning ~50 repositories consumes disk and API rate.
  Severity: low. Likelihood: low. Mitigation: shallow clones (`--depth 1`) into
  a scratch directory that the sweep deletes per batch; batches of 10–15; the
  ledger makes re-runs incremental (skip repositories already recorded at the
  same commit).

## Progress

- [x] (2026-07-19 12:00Z) Reconnaissance: surveyed concordat internals and
  the `makeutil` branch; confirmed `makeutil parse` is complete with a v1
  schema, and that concordat has no artefact command group yet.
- [x] (2026-07-19 12:30Z) ExecPlan drafted.
- [x] (2026-07-19 14:10Z) Milestone A: pinned `makeutil` at
  `29fc5a1634ffbaa18a773eed9dff1b2838a45d9c`, installed via
  `cargo install --git`, smoke-tested against this repository's `Makefile`
  (exit 0, `schema_version: 1`, status `complete`).
- [x] (2026-07-19 14:40Z) Milestone B: rule package skeleton, eleven fixture
  Makefiles plus two synthetic envelopes, a checked-in generator
  (`fixtures/generate.py`), and fourteen red policy tests (red run recorded: 14
  tests, 14 failures, missing policy package).
- [x] (2026-07-19 14:55Z) Milestone C: `rust_makefile_baseline.rego` green
  (14/14 via `conftest verify --data fixtures/data.json`); registered in
  `canon/manifest.yaml` as `lint-rule-rust-makefile-baseline` (policy) and
  `...-manifest` (rule.yaml).
- [x] (2026-07-19 15:40Z) Milestone D: `concordat artefact rule run` landed
  (`concordat/rules/` + `artefact`/`rule` sub-apps); 14 unit tests and 5 BDD
  scenarios red-then-green; live checks confirmed all three verdicts and exit
  codes (this repo → AP-001/exit 1; synthetic compliant checkout → exit 0; `?=`
  checkout → QG-001 JSON/exit 1). `OperationalRuleError` maps to exit 2 on
  stderr via an additive branch in `cli.main`.
- [ ] Milestone E: sweep driver and campaign ledger; canary run over five
  repositories.
- [ ] Milestone F: estate sweep in batches; baseline report committed; design
  document and roadmap updated.

## Surprises & discoveries

- Observation: `makeutil` is further along than the conversation that seeded
  this plan assumed — the branch implements the entire ADR-0001 scope with 82
  passing tests, a JSON Schema, snapshot tests, and a hostile-input test proving
  `$(shell touch ...)` is never executed. Evidence:
  `docs/execplans/adr-0001-single-file-gnu-make-parse.md` in that repository
  records "IMPLEMENTATION COMPLETE / AWAITING PR REVIEW". Impact: Milestone A
  is integration only; no upstream feature work needed for this slice.
- Observation: `makefile-lossless` 0.3.40 does not lex the `!=` assignment
  operator; `makeutil` carries a `[patch.crates-io]` fork
  (`leynos/makefile-lossless` rev `8dd35801b75b332c2ac2f995ae398ef8238559fa`)
  to fix it. Evidence: `Cargo.toml` on the `makeutil` branch. Impact: install
  must go via `cargo install --git` so the patch travels with the manifest; a
  plain crates.io install would silently lose `!=` facts.
- Observation: rule identifier QG-001 appears nowhere in
  `docs/concordat-design.md`; only FP-003 (canonical Makefile targets) is
  catalogued. Evidence: repository survey, 2026-07-19. Impact: this plan
  introduces QG-001 and must add it to the §3.1 check catalogue (Milestone F)
  so the design document remains the source of truth.
- Observation: referencing rule parameters with `object.get(data, [...])`
  makes OPA treat the whole `data` document as a dependency, which is circular
  once the test package (also under `data`) references the policy. Evidence:
  `rego_recursion_error` from the first green attempt. Impact: parameters are
  read with `default` rules plus scoped `data.parameters.<name>` references
  instead; a pattern to reuse in future rule packages.
- Observation: the fixture generator became a kept artefact
  (`fixtures/generate.py`) rather than the throwaway script the plan imagined,
  because envelopes must be regenerated whenever the `makeutil` pin moves.
  Evidence: Milestone B implementation. Impact: minor scope addition, recorded
  here; the script is stdlib-only.
- Observation: the CmdMox test harness never propagated programmed
  stdout/stderr into successful `CompletedProcess` results, and offered no way
  to ask whether expectations were queued. Evidence: `tests/conftest.py` line
  121 constructed `CompletedProcess` without outputs. Impact: two additive
  harness changes (outputs on the result object and an `is_programmed`
  property); existing consumers unaffected.
- Observation: conftest evaluates namespace `main` by default, so the
  runner must pass `--namespace canon.lint_rules.<rule_id>`; without it a
  failing envelope reports `successes: 0` and exit 0 — a silent false pass.
  Evidence: first live `conftest test` run during Milestone D. Impact: the
  namespace is derived from the rule id in `concordat/rules/runner.py`; worth a
  fixture-backed regression test if a second rule package ever changes the
  convention.

## Decision log

- Decision: name the campaign Operation Parabellum; keep Concordat as the
  steady-state system name. Rationale: the rollout is an estate-reconciliation
  campaign, not a feature of the platform; separating the names keeps campaign
  artefacts (ledger, baseline report) distinct from doctrine. Date/Author:
  2026-07-19, user (from the seeding conversation).
- Decision: the slice is audit-only; remediation (mutation intents, PR
  actuators, the `concordat/file` OpenTofu provider) is out of scope.
  Rationale: a trustworthy baseline must exist before mass remediation;
  audit-only keeps the blast radius at zero while the fact model and policy are
  proven against real Makefiles. Date/Author: 2026-07-19, user (from the
  seeding conversation).
- Decision: use `makeutil parse` (external Rust CLI, pinned commit) as the
  sole source of Makefile facts; concordat never parses Make syntax itself.
  Rationale: one parser, one mutation implementation later, no GNU Make
  semantics reconstructed in Rego or Python; `makeutil` already enforces
  losslessness and inertness with tests. Date/Author: 2026-07-19, user (from
  the seeding conversation).
- Decision: implement the CLI as `concordat artefact rule run <rule-id>`,
  creating the `artefact`/`rule` command groups with only this one command.
  Rationale: roadmap item 1.2 already reserves `concordat artefact` with
  `catalogue`/`estate`/`rule` groups; landing the slice inside that shape
  avoids a throwaway top-level command, while implementing only `rule run`
  keeps the slice minimal. Date/Author: 2026-07-19, Fable (planning).
- Decision: three-valued verdicts (`compliant`, `noncompliant`,
  `indeterminate`); any `include` directive makes QG-001 indeterminate.
  Rationale: until the scanner follows included files, "compliant despite
  unseen source" would be a paper shield; fail closed. Date/Author: 2026-07-19,
  user (from the seeding conversation).
- Decision: root `Cargo.toml` presence is provisional evidence of Rust
  applicability for this slice; `.concordat` `language.primary` remains the
  eventual authority. Rationale: most estate repositories have no `.concordat`
  manifest yet; the sweep must classify without one, but the shortcut is
  recorded here so it cannot fossilize. Date/Author: 2026-07-19, Fable
  (planning).
- Decision: the sweep driver is a script (`scripts/parabellum_sweep.py`), not
  a `concordat` subcommand. Rationale: the sweep is campaign machinery with a
  short life expectancy; the durable capability is `rule run`. A script keeps
  the CLI surface minimal and is within existing repository conventions
  (`scripts/` already hosts operational tooling with tests under
  `scripts/tests/`). Date/Author: 2026-07-19, Fable (planning).

- Decision: pin `makeutil` at commit
  `29fc5a1634ffbaa18a773eed9dff1b2838a45d9c` (head of
  `adr-0001-single-file-gnu-make-parse` on 2026-07-19). Rationale: Milestone A
  requires an exact, reproducible pin; this SHA is recorded in every ledger
  record. Date/Author: 2026-07-19, Fable (Milestone A).
- Decision: develop the Rego policy against the locally installed conftest
  0.68.2 (OPA 1.15.2) while CI pins v0.52.0; the policy restricts itself to
  `import rego.v1` semantics supported by both. Rationale: the constraint pins
  the CI version, which stays untouched; the local binary predates this work
  and reinstalling downward would disturb other projects on this machine. If a
  syntax incompatibility surfaces in CI, escalate rather than diverge the
  policy. Date/Author: 2026-07-19, Fable (Milestone A).

## Outcomes & retrospective

To be completed at milestone boundaries and at the end of the campaign slice.

## Context and orientation

This repository is Concordat: a Python 3.13 package (`concordat/`) managed with
`uv`, plus canonical platform standards under `platform-standards/canon/` and
OpenTofu configuration under `platform-standards/tofu/`. Key entry points:

- `concordat/cli.py` — the cyclopts CLI. `app = App()` at module level;
  commands are module functions decorated `@app.command()`. A sub-app
  (`estate_app`) is mounted with `app.command(estate_app, name="estate")`; the
  new `artefact` group follows the same pattern. `main(argv)` catches
  `concordat.errors.ConcordatError` and prints `concordat: <message>`.
- `platform-standards/canon/` — canonical artefacts, registered with SHA-256
  digests in `canon/manifest.yaml`. Existing Rego lives under `canon/policies/`
  (packages `canon.opentofu.enrolment`, `canon.rust.lints`,
  `canon.workflows.ci`), each with `_test.rego` companions. There is no
  `canon/lint-rules/` directory yet; roadmap item 1.2 defines its intended
  format (`<rule-id>/rule.yaml` plus `policy/`, `fixtures/`, `README.md`).
- `tests/` — `tests/unit/` (plain pytest), `tests/bdd/` (pytest-bdd feature
  files under `tests/bdd/features/` with `test_*_steps.py` step modules and a
  `cli_invocation` fixture), and a root `tests/conftest.py` providing the
  CmdMox subprocess-mocking harness used to fake external binaries.
- CI already installs `conftest` v0.52.0 (the Open Policy Agent test
  harness — not to be confused with pytest's `conftest.py`).

Terms used below:

- makeutil: a separate Rust repository (`leynos/makeutil`). Its single
  command `makeutil parse PATH` (or `makeutil parse --stdin-filename NAME -`)
  statically parses one GNU Makefile without evaluating it and emits one
  compact JSON document: `schema_version: 1`, `tool`, `source` (path, SHA-256,
  byte length), `parse` (`status`: `complete` or `recovered`, plus
  diagnostics), and ordered fact arrays `rules`, `variables`, `includes`. Each
  rule fact carries targets, prerequisites, `double_colon`, conditional
  ancestry, and recipes with `silent`/`ignore_errors`/`always_execute` flags
  and source locations; each variable fact carries name, operator (one of `=`,
  `:=`, `::=`, `:::=`, `+=`, `?=`, `!=`, or empty for `define`), raw value,
  `exported`, `overridden`, and location; each include fact carries `raw_path`,
  `optional`, and `dynamic`. Exit codes: 0 complete, 1 recovered (JSON still
  emitted), 2 fatal.
- Whitaker: the estate's Dylint lint suite. The estate convention (see the
  wireframe pattern) is `WHITAKER ?= whitaker` in the Makefile and a `lint`
  target that runs `$(WHITAKER)` after clippy.
- FP-003: "a root `Makefile` must exist and define canonical `build`,
  `test`, and `lint` targets" (design document §3.1, severity error).
- QG-001: "the lint gate must be binding" — introduced by this plan. The
  first provable subset: no `?=` assignment of the gate-critical `WHITAKER`
  variable; no ignore-errors (`-`) prefix on lint-path recipes; no `command -v`/
  `which` existence guards or `|| true` suppression in lint-path recipes; the
  `lint` target must reach a `$(WHITAKER)` invocation directly or through
  exactly one prerequisite hop; any `include` directive renders the rule
  indeterminate.
- Campaign ledger: an append-only JSON Lines file, one record per audited
  repository per commit, from which the baseline report is derived.

The estate to be swept is the Rust ("cargo ecosystem") portion of the Whitaker
rollout inventory, approximately 52 repositories: the reference adopters
(wireframe, netsuke, weaver, whitaker itself) plus tiers one to five and
axinite — limela, jmap-wasm, ytmusic-wasm, dbar, mdast-check, rentaneko,
rstest-xfail, statelet, evert, mpsc-log, prosidy-darn, agent-template-rust,
msgspec-crockford, monotony, agentland, cuprum, shared-actions, catnap, rustxt,
diesel-cte-ext, fingermouse, lag-complexity, dear-diary, skyjoust, comenq,
actix-v2a, mriya, mapsplice, repovec-appliance, stilyagi, lille, zamburak,
wildside-engine, spycatcher-harness, pg-embed-setup-unpriv, tei-rapporteur,
ddlint, femtologging, podbot, mxd, ortho-config, chutoro, gauss, frankie,
rstest-bdd, corbusier, wildside, and axinite. The sweep records per-repository
exclusions (for example, repositories whose Rust is vendored or incidental) in
the ledger with a reason rather than silently skipping.

## Plan of work

The work proceeds in six milestones. Each ends with a validation gate; do not
proceed past a failed gate.

### Milestone A: pin and install makeutil (integration only)

Resolve the head commit of `leynos/makeutil` branch
`adr-0001-single-file-gnu-make-parse` to an exact SHA and record it in this
plan's Decision log. Install with:

```shell
cargo install --git https://github.com/leynos/makeutil \
  --rev <PINNED_SHA> makeutil
```

(The repository pins its own nightly via `rust-toolchain.toml` and carries the
`makefile-lossless` fork patch in its manifest, so this command is
self-contained. Use the shared default Cargo cache; if another Cargo job holds
the package-cache lock, wait for it.)

Smoke-test: `makeutil parse Makefile` in this repository's root must exit 0 and
emit JSON with `"schema_version":1`. This milestone changes no tracked files
except the Decision log entry recording the pin.

### Milestone B: rule package skeleton, fixtures, and red tests

Create `platform-standards/canon/lint-rules/rust-makefile-baseline/`:

- `rule.yaml` — following the design document §2.1.2 format:
  `schema_version: 1`, `id: rust-makefile-baseline`, `version: 0.1.0`,
  `sensor: {type: conftest, policy: policy/, tests: [policy/]}`, and a
  `parameters` block declaring `gate_variable` (default `WHITAKER`) and
  `required_targets` (default `[build, test, lint]`). No `mutations` block —
  audit-only.
- `README.md` — what the rule checks, the three-valued verdict, and the
  FP-003/QG-001 identifiers.
- `fixtures/` — policy-input JSON documents (the envelope defined in
  Interfaces below), one per behaviour, generated by running the real
  `makeutil` against small fixture Makefiles kept alongside them:
  `compliant.mk` (wireframe pattern minus `?=`), `missing-target.mk`,
  `conditional-lint.mk` (`lint` inside `ifdef`), `overridable-gate.mk`
  (`WHITAKER ?= whitaker`), `soft-skip.mk` (`command -v whitaker && ...`),
  `suppressed.mk` (`|| true` and `-` prefix), `one-hop.mk`
  (`lint: lint-whitaker` delegation, compliant), `two-hop.mk` (delegation too
  deep, indeterminate), `with-include.mk` (indeterminate), and `recovered.mk`
  (broken syntax; the envelope carries `parse.status: recovered`).
- `policy/rust_makefile_baseline_test.rego` — the red stage: OPA unit tests
  asserting, for each fixture, the exact expected set of findings (rule id,
  severity, verdict contribution). Written before the policy; running the test
  command below must fail because `policy/rust_makefile_baseline.rego` does not
  yet exist.

Validation (red): from the repository root,

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/rust-makefile-baseline/policy \
  | tee /tmp/conftest-verify-concordat-parabellum-vertical-slice.out
```

fails with the tests unable to resolve the policy package.

### Milestone C: green Rego policy and canon registration

Write `policy/rust_makefile_baseline.rego`, package
`canon.lint_rules.rust_makefile_baseline`, with `deny contains finding` rules
producing structured findings
`{rule_id, severity, verdict, path, line, message}`:

- FP-003: missing root Makefile (the envelope says so), or any of
  `required_targets` absent from the rule facts, or present only under a
  non-empty conditional ancestry.
- QG-001 (noncompliant): a variable fact named `gate_variable` with operator
  `?=`; a lint-path recipe with `ignore_errors: true`; a lint-path recipe whose
  text matches a bounded set of guard patterns (`command -v`, `which ... ||`,
  `|| true`); no `$(WHITAKER)` invocation reachable from `lint` within one
  prerequisite hop.
- QG-001 (indeterminate): any include fact; `parse.status == "recovered"`;
  a `lint` rule with `double_colon: true` or duplicate `lint` definitions.

"Lint-path recipe" means: a recipe belonging to a rule whose targets include
`lint`, or to a rule named by `lint`'s prerequisites (one hop). The policy
computes this from the fact arrays; it performs no regex reconstruction of Make
semantics beyond the bounded guard patterns, which operate on recipe text the
parser has already isolated.

Register the package in `platform-standards/canon/manifest.yaml` as a new
artefact (type `opa-policy` or a new `lint-rule` type if the manifest validator
requires distinct handling — prefer the existing type and escalate if it does
not fit) with its SHA-256 digest, following the existing generator workflow
(`python -m scripts.canon_artifacts`).

Validation (green): the `conftest verify` command from Milestone B passes,
every fixture asserting its exact finding set. Commit gates pass.

### Milestone D: the `concordat artefact rule run` command

Red first: add `tests/bdd/features/rule_run.feature` (specification below in
Validation) with step module `tests/bdd/test_rule_run_steps.py`, and unit tests
under `tests/unit/` for the new modules. External binaries (`makeutil`,
`conftest`) are faked with the CmdMox harness from `tests/conftest.py`;
fixtures reuse the Milestone B envelopes. Run the focused tests and observe
them fail for want of the implementation.

Then implement, in a new module `concordat/rules/` (grouped by feature per
`AGENTS.md`):

- `concordat/rules/makefile_facts.py` —
  `inspect_makefile(path) -> MakefileFacts`: runs `makeutil parse`, enforces a
  timeout, validates
  `schema_version == 1`, maps exit 1 to a recovered-status facts object and
  exit 2 to `ConcordatError`.
- `concordat/rules/envelope.py` — builds the `policy-input/v1` envelope
  (Interfaces section) from a checkout path: root `Cargo.toml` presence and
  parsed content (stdlib `tomllib`), root `Makefile` facts, repository metadata.
- `concordat/rules/runner.py` — resolves the rule package directory under
  `platform-standards/canon/lint-rules/`, writes the envelope to a temporary
  file, invokes `conftest test --policy <pkg>/policy --output json <envelope>`,
  parses findings, computes the overall verdict, renders `table` (default) or
  `json` output.
- `concordat/cli.py` — mount `artefact_app = App()` via
  `app.command(artefact_app, name="artefact")`, and `rule_app` within it, so
  the invocation is
  `concordat artefact rule run <rule-id> --repo PATH --format {table,json}`.
  Exit codes: 0 no findings (compliant); 1 at least one finding, including
  indeterminate verdicts (fail closed); 2 operational failure (missing tools,
  unreadable checkout, envelope construction failure) via `ConcordatError`.

Validation: focused red tests now pass; full commit gates pass.

### Milestone E: sweep driver, ledger, and canary run

Add `scripts/parabellum_sweep.py` with tests in
`scripts/tests/test_parabellum_sweep.py` (red first, CmdMox-faked `git` and
audit invocations). Behaviour: read a repository list from
`docs/parabellum/estate.yaml` (committed in this milestone; each entry `name`,
optional `excluded: reason`); for each non-excluded repository, shallow-clone
`https://github.com/leynos/<name>` at the default branch into the scratch
directory, resolve `HEAD` to a SHA, run the Milestone D command with
`--format json`, append one record to `docs/parabellum/ledger.jsonl` (schema in
Interfaces), and delete the clone. Idempotence: a repository already in the
ledger at the same commit SHA is skipped unless `--force`.

Canary run (live, five repositories): wireframe, netsuke, weaver, statelet,
fingermouse — two reference adopters expected compliant-or-nearly, one
known-clean small repo, one hand-grown repo, plus netsuke's richer Makefile.
Review the five ledger records by hand: verdicts must match what reading the
Makefiles predicts. Any mismatch is a policy or fact-model bug; fix before
proceeding (this is the go/no-go gate for Milestone F).

### Milestone F: estate sweep, baseline report, doctrine updates

Run the sweep over the full estate list in batches of 10–15, observing the
sweep tolerance. Then:

- Generate `docs/parabellum/baseline-report.md` from the ledger (a small
  reporting function in `scripts/parabellum_sweep.py`): per-repository
  verdicts, finding counts by rule, and an indeterminate list with reasons.
- Update `docs/concordat-design.md` §3.1: add QG-001 to the check catalogue
  and note FP-003's first automated implementation.
- Update `docs/roadmap.md`: mark the delivered fraction of item 1.2 (rule
  package format and `rule run`) and add the audit baseline as evidence.
- Record campaign outcomes in this plan's `Outcomes & retrospective`.

Escalation follows the sweep, not the reverse: repositories that fail for
structural reasons (no Makefile at all, non-Cargo layouts) become ledger
exclusions or findings, and patterns recurring across many repositories become
candidate doctrine changes listed in the retrospective — they are not fixed ad
hoc during this slice.

## Concrete steps

All commands run from the repository root
(`/home/leynos/Projects/concordat.worktrees/parabellum-vertical-slice`) unless
stated. Long outputs go through `tee` to
`/tmp/<action>-concordat-parabellum-vertical-slice.out`. Gate runs are
delegated to the scrutineer subagent where available.

1. Milestone A:

   ```shell
   gh api repos/leynos/makeutil/commits/adr-0001-single-file-gnu-make-parse \
     --jq .sha
   cargo install --git https://github.com/leynos/makeutil --rev <SHA> makeutil
   makeutil parse Makefile | head -c 200
   ```

   Expect the last command to print a JSON prefix beginning
   `{"schema_version":1,"tool":{"name":"makeutil"`.

2. Milestone B: create the rule package files, generate fixture envelopes
   with a small throwaway script that wraps `makeutil parse` output in the
   envelope (checked in under `fixtures/` as JSON), then run the red
   `conftest verify` command shown in the milestone and confirm it fails for
   the expected reason (missing policy package). Commit the red state only
   together with the green policy in Milestone C (policy tests and policy land
   as one gated commit; the red run is recorded in this plan).

3. Milestone C: write the policy, re-run `conftest verify` until green,
   run `python -m scripts.canon_artifacts` to refresh `manifest.yaml`, run full
   gates, commit.

4. Milestone D: add the feature file and failing tests; run
   `uv run pytest tests/bdd/test_rule_run_steps.py tests/unit -k rule -v` and
   observe failures; implement; re-run focused tests, then full gates; commit.
   Then one live end-to-end check:

   ```shell
   uv run concordat artefact rule run rust-makefile-baseline --repo . ; echo $?
   ```

   This repository is a Python project (no root `Cargo.toml`), so expect an
   applicability finding and exit 1 — which itself exercises the
   not-a-Rust-repo path.

5. Milestone E: add `docs/parabellum/estate.yaml` and the sweep script
   (tests red, then green); canary:

   ```shell
   uv run python -m scripts.parabellum_sweep \
     --only wireframe,netsuke,weaver,statelet,fingermouse \
     | tee /tmp/sweep-canary-concordat-parabellum-vertical-slice.out
   ```

   Expect five new lines in `docs/parabellum/ledger.jsonl`. Review by hand;
   commit ledger and code after gates.

6. Milestone F: run the sweep in batches
   (`--batch-size 15`), regenerate the baseline report, update the design
   document and roadmap, run gates, commit. Commit the ledger after each batch
   so progress survives interruption.

## Validation and acceptance

The BDD specification driving Milestone D,
`tests/bdd/features/rule_run.feature`:

```gherkin
Feature: Rust Makefile baseline rule run
  Auditing a local checkout against the rust-makefile-baseline rule package.

  Scenario: compliant repository
    Given a checkout with a root Cargo.toml
    And a Makefile whose facts match the "compliant" fixture
    When I run "concordat artefact rule run rust-makefile-baseline --repo ."
    Then the exit status is 0
    And the table output reports zero findings

  Scenario: environment-overridable lint gate
    Given a checkout with a root Cargo.toml
    And a Makefile whose facts match the "overridable-gate" fixture
    When I run "concordat artefact rule run rust-makefile-baseline --repo ."
    Then the exit status is 1
    And the output contains a QG-001 finding citing the Makefile line of
      the "?=" assignment

  Scenario: include renders the gate unprovable
    Given a checkout with a root Cargo.toml
    And a Makefile whose facts match the "with-include" fixture
    When I run "concordat artefact rule run rust-makefile-baseline --repo ."
    Then the exit status is 1
    And the output reports QG-001 as indeterminate

  Scenario: missing canonical target
    Given a checkout with a root Cargo.toml
    And a Makefile whose facts match the "missing-target" fixture
    When I run "concordat artefact rule run rust-makefile-baseline --repo ."
    Then the exit status is 1
    And the output contains an FP-003 finding naming the absent target

  Scenario: makeutil is not installed
    Given a checkout with a root Cargo.toml and a root Makefile
    And no makeutil executable is available
    When I run "concordat artefact rule run rust-makefile-baseline --repo ."
    Then the exit status is 2
    And stderr explains that makeutil is required
```

Red-Green-Refactor evidence to record as work proceeds:

- Red: the focused pytest command and the `conftest verify` command fail
  before implementation, for the reasons stated in Milestones B and D.
- Green: the same commands pass after the minimal implementation.
- Refactor: `make check-fmt lint typecheck test markdownlint nixie` all
  pass after cleanup (run sequentially, via scrutineer).

Quality criteria (what "done" means for the slice):

- Tests: `make test` passes, including the new unit tests, BDD scenarios,
  and sweep-script tests; `conftest verify` over the rule package passes.
- Lint/typecheck: `make lint` and `make typecheck` pass; `mbake validate`
  is not applicable (no Makefile changes here).
- Behaviour: the canary ledger records match a human reading of the five
  Makefiles; the full-estate ledger contains one record per non-excluded
  repository; `docs/parabellum/baseline-report.md` exists and is derived solely
  from the ledger.
- Documentation: users' guide gains a `rule run` section; design document
  gains QG-001; roadmap reflects delivery; this plan's living sections are
  current.

## Idempotence and recovery

Every milestone is re-runnable. `cargo install` overwrites the previous binary;
fixture generation is deterministic given the pinned `makeutil`;
`conftest verify` and pytest are read-only; the sweep skips already-ledgered
`(repository, commit)` pairs and deletes clones after use, so an interrupted
batch is resumed by re-running the same command. The ledger is append-only — a
re-audit at a new commit appends a new record rather than rewriting history;
the report generator takes the latest record per repository. Nothing in this
slice mutates any remote repository, so there is no rollback surface beyond
`git revert` in this repository.

## Artefacts and notes

Expected canary evidence shape (one ledger line, wrapped for reading):

```json
{"schema_version": 1, "repository": "leynos/wireframe",
 "commit_sha": "<sha>", "audited_at": "2026-07-19T15:00:00Z",
 "rule_package": "rust-makefile-baseline", "rule_version": "0.1.0",
 "makeutil_rev": "<pin>", "verdict": "compliant", "findings": []}
```

## Interfaces and dependencies

External tools (pinned): `makeutil` at the SHA recorded in the Decision log
once Milestone A resolves it; `conftest` v0.52.0 (as in CI); `git` and `gh` as
present on the host. No new Python runtime dependencies: TOML parsing uses
stdlib `tomllib`; YAML uses the existing `ruamel.yaml`; subprocess handling
uses the stdlib.

The policy-input envelope, `policy-input/v1`, produced by
`concordat/rules/envelope.py` and consumed by the Rego package and all fixtures:

```json
{
  "schema_version": 1,
  "kind": "policy-input/rust-makefile-baseline",
  "repository": {"path": ".", "name": null},
  "applicability": {
    "root_cargo_toml": true,
    "root_makefile": true
  },
  "cargo": {"parsed": {}},
  "makefile": {}
}
```

where `makefile` is the verbatim `makeutil` v1 report (or `null` when no root
Makefile exists) and `cargo.parsed` is the `tomllib` load of the root
`Cargo.toml` (or `null`). The envelope is versioned; policies must reject
unknown major versions.

Python signatures that must exist at the end of Milestone D:

```python
# concordat/rules/makefile_facts.py
def inspect_makefile(path: Path, *, timeout: float = 10.0) -> MakefileFacts: ...

# concordat/rules/envelope.py
def build_envelope(checkout: Path) -> dict[str, object]: ...

# concordat/rules/runner.py
def run_rule(rule_id: str, checkout: Path, *, output: str = "table") -> RuleRunResult: ...
```

`MakefileFacts` and `RuleRunResult` are frozen dataclasses in the same modules;
`RuleRunResult` carries `verdict`, `findings`, and `exit_code`.

Ledger record schema (`docs/parabellum/ledger.jsonl`, one JSON object per line):
`schema_version` (1), `repository` (owner/name), `commit_sha`, `audited_at`
(UTC ISO-8601), `rule_package`, `rule_version`, `makeutil_rev`, `verdict`
(`compliant` | `noncompliant` | `indeterminate` | `excluded` | `error`),
`findings` (array of `{rule_id, severity, verdict, path, line, message}`), and
optional `exclusion_reason` or `error_detail`.

## Revision note

2026-07-19: initial draft, produced from the seeding conversation, the Whitaker
rollout snapshot (`~/docs/whitaker-roll-out.md`), a survey of this repository,
and a survey of `leynos/makeutil` branch `adr-0001-single-file-gnu-make-parse`.
No implementation has begun; the plan awaits approval.
