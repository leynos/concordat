# Add a nested-cargo offline check and make warning findings advisory (#326)

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: DRAFT

## Purpose / big picture

Tests that start their own `cargo` process — nested builds, `cargo package`,
`cargo publish`, `cargo dylint`, trybuild — resolve dependencies and update the
crates.io index while holding the shared Cargo package-cache lock. On a host
where many agents share one `$CARGO_HOME`, every later build queues behind that
work; a 19-minute wait was observed in `leynos/whitaker` and the pattern is
documented in `leynos/whitaker#518`. After this change,
`concordat artefact rule run nested-cargo-offline-tests --repo <checkout>`
reports, without running Cargo or touching the network, every Cargo-command
spawn below `tests/`/`test_support/` that can reach the network, and every
entry point (`Makefile` target, nextest setup script, CI workflow step) whose
test ramp starts Cargo before it has prefetched the graph offline. The report
is advisory by default (`warning` severity) so the estate can be surveyed
before the rule is promoted to blocking, and a warning finding must no longer
fail a gate — that is the first task, and it is what lets the survey run safely.

## Constraints

This plan may not modify the following, except where a task explicitly names
them. These are hard invariants; violating one requires escalation, not a
workaround.

- `concordat/rules/packages.py` and
  `concordat/rules/nested_cargo_lock_envelope.py` stay unchanged. New packages
  register through `concordat/rules/nested_cargo_registry.py` only.
- `RuleRunResult.verdict`, `_overall_verdict`, and `render_json` keep their
  current semantics and shape. JSON consumers already depend on
  `{"rule_package", "verdict", "findings"}`.
- Exit code `2` for operational failure is unchanged.
- Existing committed snapshots under `tests/unit/snapshots/` are updated only
  where this plan says so, and every update is justified in `Decision log`.
- `typos.toml` is generated and must never be hand-edited; new vocabulary goes
  in `typos.local.toml` as narrow patterns.
- `NC-001`/`NC-002` behaviour and fixtures keep passing unchanged.
- The branch must not be `main`; commits follow the repository's quality
  gates, run through the `scrutineer` gate-runner.
- No new runtime dependency without escalation.

## Tolerances (exception triggers)

- Scope: if any single task needs more than 1,500 net lines across more than
  12 files, stop and escalate with the file list.
- Interface: if a public Python signature outside the new modules must change
  (other than `runner.RuleRunResult` gaining `blocking` and a new exit rule,
  both mandated), stop and escalate.
- Dependencies: any new Python or Rust dependency stops the work.
- Iterations: if a gate still fails after three distinct fixes, stop and
  report the failing log path.
- Ambiguity: if the issue text and a discovered corpus shape disagree about
  what counts as safe, record both readings in `Decision log` and take the
  conservative one (report `unresolved`, never `offline`); escalate only if the
  conservative reading would contradict an explicit requirement.
- Survey data: if `~/docs/missing-frozen-survey-plan.md` is absent on this
  host, do not fabricate survey numbers. Run the local corpus comparison the
  task defines, record the block in `Decision log`, and state it in the PR.

## Risks

- Risk: the `exit_code` change silently unblocks an estate gate that relies on
  a warning-severity finding failing. Severity: medium Likelihood: low.
  Mitigation: only RT-025 uses `warning` in shipped canon, and the design
  document already treats severity as the promotion dial
  (`docs/concordat-design.md:357`); record the decision and cover mixed
  severities with tests.
- Risk: a text-level Rust reader misclassifies a spawn, producing false
  `online` findings. Severity: high Likelihood: medium. Mitigation:
  conservative classification — anything the reader cannot resolve is
  `unresolved`, never `online`, and `--version`/help are excluded; corpus tests
  over real whitaker and theoremc checkouts pin the outcome.
- Risk: `NC-007`/`NC-008` collide with identifiers used elsewhere in the
  estate. Severity: low Likelihood: low. Mitigation: grep this repository and
  record the check; confirm against the upstream design table before merge.
- Risk: the survey (Task 4) cannot be run because the referenced plan file is
  missing. Severity: medium Likelihood: high (already observed absent).
  Mitigation: keep survey code and tests complete, run the local corpus
  comparison, and report the missing artefact in the PR rather than inventing
  counts.
- Risk: `pylint` `max-module-lines = 800` is exceeded by the reader modules.
  Severity: low Likelihood: medium. Mitigation: split the reader by concern
  from the start (`nested_cargo_spawns.py`, `nested_cargo_entry_points.py`,
  `nested_cargo_offline_envelope.py`).

## Progress

- [x] (2026-10-25) Task 1: warning severity research recorded, failing tests
      written, `exit_code`/`blocking`/severity column implemented, docs
      updated, gates green, committed as
      `ba9b7295e42bd0d14c0e894908183dbe68b6b735`.
- [x] (2026-10-26) Task 2: spawn reader (46 tests), entry-point reader
      (52 tests), envelope builder (6 tests) and the registry entries are
      written and green, and both readers have been replayed over the whitaker
      and theoremc checkouts. Both readers first breached `pylint`'s 800-line
      module ceiling (1214 and 1072 lines), so each was split by subject with no
      body changes, verified by comparing every function body against the
      pre-split source modulo the intended renames. Modules are now
      `nested_cargo_source.py` (189), `nested_cargo_commands.py` (398),
      `nested_cargo_spawns.py` (700), `nested_cargo_scripts.py` (348) and
      `nested_cargo_entry_points.py` (763). `make check-fmt`, `make lint`
      (including the df12 `R9110` clean run), `make typecheck`,
      `make markdownlint` and `make nixie` pass; `make test` reports only the
      six `makeutil` fixture-drift failures already recorded as pre-existing.
      Committed as 8200b23.
- [x] (2026-10-26) Task 2 remediation (CodeRabbit round 1): redirection-aware
      connector split, nextest name resolution through the definition table,
      workflow shell resolution with `runs-on` fallback, `#[path]` containment,
      envelope test isolation, and the ExecPlan corrections. Tests: 120 in the
      two reader files, all green.
- [x] (2026-10-26) Task 2 reader follow-up: `#[path]` folding (repairs eight
      spurious problems on the fixed whitaker checkout), `env` option
      transparency, `cargo-nextest`/`ntr` recognition, and the `uncertain`
      fallback for an unreadable `test` target. 11 new tests, red first. The
      after-fix whitaker replay moves from a false `absent` to `uncertain` on
      both the Makefile and its delegating workflow step; the concordat tree
      still yields no facts. 173 reader and envelope tests green.
- [ ] Task 3: canon package, policy, fixtures, Rego tests, unit and BDD tests,
      manifest SHA-256, documentation, and the `test_envelope_selection.py`
      entries deferred from Task 2.
- [ ] Task 4: `--package` sweep plumbing, ledger resume key, report filters and
      distinct-repository counts, real-corpus comparison.
- [ ] Delivery: draft PR with session reference.

## Surprises & discoveries

- Observation: 87 `finding(...)` calls appear to pass `"indeterminate"` as
  the severity. Evidence: they use the four-argument helper
  `finding(id, verdict, path, msg)` whose second argument is the verdict, not
  the severity; only `rust-toolchain-policy` and this repository's NC policy
  use five/`error`-fixed forms. Impact: the severity scan must be done
  per-helper, not by grepping the second string argument.
- Observation: `leynos/whitaker#518` is still open, so whitaker `main` has no
  `--frozen`/`--offline`/`CARGO_NET_OFFLINE` anywhere. Impact: the "after"
  corpus case must be synthetic or a patched checkout.
- Observation: `~/docs/missing-frozen-survey-plan.md` does not exist on this
  host. Impact: the estate survey step cannot run here.
- Observation: `makeutil` 0.1.2 is on `PATH`, but every checked-in fixture
  envelope records `makeutil` 0.1.0, so six generator-drift tests fail. They
  fail identically from a clean archive of
  `ba9b7295e42bd0d14c0e894908183dbe68b6b735`, so they predate this branch.
  Impact: the Task 2 gate must attribute those six failures, and regenerating
  the envelopes is a separate change, not part of this issue.
- Observation: whitaker's `test` target is one `set -eu; \` chain, and the
  Makefile loader keeps each backslash-newline inside the recipe text. Impact:
  the entry-point reader folds continuations before lexing, otherwise the
  recipe's leading newline defeats the assignment matcher and the recipe reads
  as uncertain for the wrong reason.
- Observation: theoremc has no `.config/nextest.toml`, yet an early version of
  the reader emitted a nextest fact pointing at line 1 of that non-existent
  file. Impact: the nextest reader now distinguishes "no file" from "a file it
  cannot read", and an absent file yields no configuration fact at all.
- Observation: `split_commands` read `2>&1` as a background `&`, splitting one
  command into `cargo fetch --locked 2>` and `1`. Impact: the splitter now
  recognizes the redirection forms `2>&1`, `>&2`, `<&0`, `&>file` and
  `&>>file`, and the regression is pinned by a parametrized test.
- Observation: nextest separates a `[[profile.<name>.scripts]]` rule from the
  command it names; `setup` holds a name, and the command lives in
  `[scripts.setup.<name>].command`. Probe against `cargo-nextest 0.9.133`
  confirmed that a rule without `filter` or `platform`, or a `setup` name
  nothing defines, is a configuration nextest refuses. Impact: the reader
  resolves names through the definition table and reports `uncertain` for a
  name it cannot resolve, rather than reading the name as a command and calling
  a correct configuration cold.
- Observation: a multi-line `run` block only stops at a failed line under a
  fail-fast shell. GitHub's defaults are `bash -e {0}` on Linux and macOS and
  `pwsh` on Windows, and a custom template such as `bash {0}` does not stop.
  Impact: the entry-point reader resolves the step, job and workflow `shell`
  declarations and the `runs-on` default, and treats a line break as proof only
  when that shell fails fast.
- Observation: the first version of `_default_shell` read `defaults.run.shell`
  from the wrong level, so every job and workflow default was ignored and the
  runner default always won. Impact: caught by the precedence tests written for
  the shell work; the helper now reads the outer block's `defaults` key.
- Observation: a file reached by a `#[path]` declaration was marked whole-file
  test scope, but if it was also in the initial walk it was scanned again later
  and that stronger claim was overwritten with an empty attribute range list.
  Impact: `collect_rust_evidence` now preserves an existing whole-file scope
  when it rescans, which a containment test exposed.
- Observation: `_refused_candidate` refused every `#[path]` declaration that
  contained `..`, but Rust resolves the path against the declaring file's
  directory, so `src/driver/../tests/behaviour.rs` stays inside the checkout.
  Impact: the fixed whitaker checkout produced eight spurious problems. The
  resolver now folds `.`/`..` before deciding, and only a path that climbs
  above the root (or is absolute, missing, or resolves through a symlink out of
  the checkout) is refused. A regression test declares from a root file, where
  the escape is genuine, so it fails without the fold.
- Observation: the after-fix whitaker checkout reported `absent` for its
  `windows-compat` workflow step, which would have been a false NC-008 against
  the repository that made the prefetch fix. Evidence: the `test` recipe's
  runner is preceded by an unresolved `$(if ...)`, so no test command was
  recognized and `_makefile_entry` returned no fact; `_inherited_state` then
  read that missing fact as `absent`. Impact: an unreadable `test` target now
  yields an `uncertain` fact with the reason, while a target that visibly runs
  no test (`test:\n\tcargo build`) still yields none. The after-fix replay is
  now `uncertain` at the Makefile and at the workflow step, and no `absent`.
- Observation: two further blind spots made realistic recipes unreadable even
  where the expansion settled. `command_start` treated `env` as a bare wrapper,
  so `env -u CARGO_NET_OFFLINE cargo fetch --locked` — the exact mitigation
  whitaker added — read `-u` as the command word; and `_classify` matched only
  the word `cargo`, so a recipe binding `CARGO_NEXTEST ?= cargo-nextest` and
  running `$(CARGO_NEXTEST) nextest run` named no test. Impact: `command_start`
  now skips `env` options, their values, assignments and `--`, and the
  classifier accepts the standalone `cargo-nextest` binary with its
  `nextest run` / `ntr` subcommands. Probes against the installed binaries
  confirmed both spellings.

## Decision log

- Decision: warning-severity findings are advisory and no longer block a
  gate; `exit_code` is `0` when the verdict is compliant or every finding is
  `warning`, otherwise `1`. Rationale: severity is the documented enforcement
  dial (`docs/concordat-design.md:357`), RT-025 is the only warning producer,
  and the new package must ship as a survey-mode warning. Verdict and JSON keep
  their meaning, so evidence is preserved while enforcement changes.
  Date/Author: 2026-10-25, Claude Code on behalf of leynos.
- Decision: the Rust reader reports `unresolved` for anything whose offline
  evidence it cannot prove. Rationale: a false `online` finding would push a
  reading of "fix the test" onto code that is already safe; `indeterminate`
  preserves the question without asserting it. Date/Author: 2026-10-25.
- Decision: a selected nextest profile with setup scripts is itself an entry
  point, reported at the line of its `[[profile.<name>.scripts]]` table, or of
  its `[profile.<name>]` heading when there is no table. Rationale: the
  prefetch a profile needs is the prefetch its setup scripts need, and the
  policy has to be able to name the file and line a reader should fix; a
  profile selected with `--profile` but configured only through `[profile]` is
  still a place the checkout runs tests. Date/Author: 2026-10-26.
- Decision: only the `all()` filter is treated as configuration the reader can
  reason about; a profile whose setup scripts are all narrower than `all()` is
  `absent`, not `uncertain`. Rationale: `all()` is the one filter every
  selected test satisfies, so ordering it before the test command is provably a
  prefetch. A narrower rule still leaves the tests outside it with a cold
  cache, and that is what `absent` means; calling it `uncertain` would hide a
  missing prefetch behind a reading that never becomes a finding. The reader
  reserves `uncertain` for a configuration it cannot read at all. Date/Author:
  2026-10-26, revised after review.
- Decision: the shared walker in `dependency_family_discovery.py` grew a
  `walk_files(checkout, problems, *, label=None)` primitive, and `nested_files`
  became a filter over it. Rationale: the offline reader walks every file in
  the checkout rather than hunting one name, and a second directory walker
  would have had to re-derive the skip list and the unreadable directory
  contract. `label` keeps the existing problem sentence unchanged for existing
  callers. Date/Author: 2026-10-26.
- Decision: `nested_cargo_offline_envelope.py` ignores its parameters rather
  than passing them to a reader. Rationale: exemptions, severity and the
  verdict belong to the policy, which already receives the whole manifest; a
  builder that read the exemptions could disagree with the policy about which
  facts exist. Date/Author: 2026-10-26.
- Decision: `tests/unit/test_envelope_selection.py` gains its
  `nested-cargo-offline-tests` rows in Task 3, not here. Rationale:
  `rule_package_dir` raises `OperationalRuleError` when the canon package has no
  `policy/` directory, so naming the package before Task 3 creates it fails
  the suite. Date/Author: 2026-10-26.
- Decision: a `test` target that exists but whose command cannot be read is
  itself an `uncertain` entry point, rather than no entry point. Rationale: no
  entry point is indistinguishable from "this target runs no tests", and a
  workflow step delegating to it inherits `absent`. That is a positive claim of
  a cold cache from evidence the reader never saw. The distinction is kept
  narrow: only a recursive `make`, an unreadable command, or `.ONESHELL`
  produces the fallback, so `test:\n\tcargo build` still reports nothing.
  Date/Author: 2026-10-26.
- Decision: `env` is walked for its options rather than treated as a plain
  wrapper, and the standalone `cargo-nextest` binary is recognized as a test
  command. Rationale: both spellings appear in the corpus and both were being
  misreported — the first hid the prefetch whitaker added, the second hid the
  test itself. Recognizing them is a reader fix, not a policy change: the fact
  set gets truer, and the policy's rules are unchanged. Date/Author: 2026-10-26.
- Decision: a nextest profile selected by a Makefile `test` target whose state
  is `uncertain` still yields no nextest fact, because the profile name was
  never read. Rationale: guessing which profile a hidden command selects would
  invent a fact; the Makefile fact already says the entry point is uncertain,
  which is the honest report. This is recorded as a residual limit, not a
  defect to fix in Task 3. Date/Author: 2026-10-26.

## Outcomes & retrospective

Not yet started.

## Context and orientation

`concordat` is a Python policy platform.
`concordat artefact rule run <id> --repo <path>` builds a JSON envelope in
Python (`concordat/rules/*.py`), runs Conftest over a Rego policy in
`platform-standards/canon/lint-rules/<id>/policy/`, and renders findings.
`concordat/rules/runner.py` owns evaluation, the `Finding` and `RuleRunResult`
types, `exit_code`, and the table/JSON renderers.
`concordat/rules/nested_cargo.py` holds shared helpers for the nested-cargo
family, and `concordat/rules/nested_cargo_lock_envelope.py` plus
`nested_cargo_registry.py` are the template for a new package in that family.

The existing sibling rule `nested-cargo-fixture-lockfiles` (NC-001/NC-002)
keeps a fixture's lockfile aligned with the workspace graph. This plan adds its
complement: a check on test-time `cargo` invocations and on the entry points
that start them, so a nested build either runs offline against the shared cache
or is reported.

Terms used below. A *spawn* is a literal `Command::new("cargo")` (or an
`env!`/`option_env!`-derived Cargo path) in test-scope Rust. *Offline evidence*
is `--frozen`, or both `--offline` and `--locked`, or a command-local
`CARGO_NET_OFFLINE="true"`. An *entry point* is a Make target, a nextest
setup-script, or a workflow step that ramps a test command.

## Conformance basis

The governing artefacts are the GitHub issue `leynos/concordat#326`, the
whitaker pattern fix `leynos/whitaker#518`, the existing NC package
(`platform-standards/canon/lint-rules/nested-cargo-fixture-lockfiles/`), and
`docs/concordat-design.md` (DF12-109 entry at `docs/roadmap.md:538`). No
separate Terms of Reference or ADR exists for this repository; the design
document and the issue text are the contract.

Trace chain:

```plaintext
ISSUE-326-T1 -> EP-M1 -> tests/unit/test_rule_exit_contract.py
ISSUE-326-T2 -> EP-M2 -> tests/unit/test_nested_cargo_spawns.py, test_nested_cargo_offline_envelope.py
ISSUE-326-T3 -> EP-M3 -> tests/unit/test_nested_cargo_offline_rule.py, tests/bdd/features/nested_cargo_offline_tests_rule_run.feature
ISSUE-326-T4 -> EP-M4 -> scripts/tests/test_parabellum_sweep.py, scripts/tests/test_parabellum_report.py
```

## Verification plan

Obligation `EP-EXIT-1`: for every result, `exit_code == 0` iff the verdict is
compliant or every finding carries severity `warning`; otherwise `1`.

- Method: parameterized unit tests over a finite partition.
- Rationale: the input space is a small product of verdicts and severity
  multisets, so enumeration is exhaustive for the contract.
- Domain: no findings; all-error; all-warning; mixed error+warning across
  noncompliant and indeterminate; a finding with an unknown severity label.
- Artefact: `tests/unit/test_rule_exit_contract.py`.
- Evidence: new tests fail against the current `exit_code` before the change
  and pass after.
- Non-vacuity: each partition has a witness; the all-warning noncompliant case
  currently exits `1`, so it fails for the intended reason.

Obligation `EP-EXIT-2`: `blocking` is `True` exactly when `exit_code != 0`, and
`render_table` shows each finding's severity without changing `render_json`.

- Method: unit tests plus the existing snapshot test.
- Artefact: `tests/unit/test_rule_exit_contract.py`,
  `tests/unit/test_snapshots.py` (snapshot updated for the new column only).
- Non-vacuity: a mixed-severity table must show both `error` and `warning`.

Obligation `EP-SPAWN-1`: a spawn is `offline` only with proven offline
evidence; any indeterminate argument or environment source without it is
`unresolved`; a literal clean spawn is `online`.

- Method: parameterized unit tests over handwritten Rust fixtures, plus
  corpus tests over real whitaker and theoremc checkouts.
- Rationale: the reader is a text-level approximation whose risky direction is
  false `online`; the partition covers each evidence form and each uncertainty
  form, and the corpus test anchors it to real code.
- Domain: `--frozen`; `--offline --locked`; `--offline` alone;
  `CARGO_NET_OFFLINE` command-local and inherited; `--version`/`-V`/`help`;
  `env!("CARGO")`; non-literal `.arg`; helper functions and call sites;
  trybuild dependency tables; out-of-line `#[cfg(test)]` modules.
- Artefact: `tests/unit/test_nested_cargo_spawns.py`.
- Evidence: red first; then green; corpus cases pin whitaker's four direct
  spawns as `online` and theoremc's helpers as spawns.
- Non-vacuity: the "inherited env does not count" case must still report
  `online`, proving the exclusion is exercised.

Obligation `EP-ENTRY-1`: `&&` and fail-fast sequencing prove prefetch order;
`;` does not; Make prerequisite and recipe order is read with the existing
loader; unresolved expansion, `.ONESHELL`, and recursive Make stay uncertain.

- Method: parameterized unit tests for shell lexing, Make parsing, nextest
  setup scripts, and workflow order, plus a replay over the whitaker and
  theoremc checkouts.
- Artefact: `tests/unit/test_nested_cargo_entry_points.py`.
- Evidence: 81 tests green. Whitaker **before** #518 yields three facts —
  `uncertain` for the `test` target (its recipe names `$(if …)`, which the
  expander declines to evaluate), `absent` for the `.config/nextest.toml`
  `default` profile on line 6, and `uncertain` for the `windows-compat`
  workflow job. Whitaker **after** #518 yields two — `uncertain` for the `test`
  target at line 207 and `uncertain` for the `windows-compat` step at line 631
  — because the fetched-and-then-hidden shape cannot be proven from text alone.
  Theoremc yields one fact, `absent` for its `test` target on line 27. Every
  checkout produces no problems.
- Non-vacuity: a `cargo test; cargo fetch --locked` case must stay uncertain,
  which fails if `;` were treated as ordering. The whitaker replay is the
  negative control for continuation folding: without it that same target reports
  `uncertain` for a reason ("the test command is unreadable") that is false,
  rather than for the reason the recipe actually gives. The after-fix replay is
  the negative control for the unreadable-target fallback: reporting no fact
  there lets the workflow step inherit `absent` and manufactures an NC-008
  against a repository that fixed the problem.

Obligation `EP-ENVELOPE-1`: the envelope carries schema version, kind, the
origin slug or `null`, and every fact list from both readers, and never drops a
reader's problem.

- Method: unit tests over temporary checkouts.
- Artefact: `tests/unit/test_nested_cargo_offline_envelope.py` (6 tests).
- Evidence: green; an unparsable Makefile survives as a problem and produces
  no entry point, and supplied parameters change nothing.
- Non-vacuity: the four-list test fails if any reader's output is dropped, and
  the problem test fails if a reader swallows its failure.

Obligation `EP-POLICY-1`: the policy emits NC-007 for `online` spawns and
NC-008 for absent entry-point prefetch only when spawn or trybuild evidence
exists; every unresolved/uncertain/problem fact is EN-001 indeterminate; an
envelope defect is an error-severity EN-001.

- Method: Rego unit tests over `fixtures/data.json`, plus the real runner.
- Artefact: `policy/nested_cargo_offline_tests_test.rego`,
  `tests/unit/test_nested_cargo_offline_rule.py`.
- Non-vacuity: an exemption suppresses NC-007/NC-008 for one directory and not
  another; a fixture-lock-only checkout produces no offline facts.

Axioms: Conftest 0.68.2 semantics for `deny`/`data.parameters`; Cargo's meaning
of `--frozen`, `--offline`, `--locked`; nextest's setup-script execution order;
GitHub Actions step ordering. Third-party internals are not verified; the
repository-owned readers and policy are.

## Plan of work

Stage A (done): research warning severities and corpus shapes.

Stage B (Task 1, red): add `tests/unit/test_rule_exit_contract.py` covering
EP-EXIT-1/2, run it, confirm failures on the all-warning cases.

Stage C (Task 1, green): in `concordat/rules/runner.py` add `WARNING_SEVERITY`,
a `blocking` property, the new `exit_code`, and a severity column in
`render_table`; update the table snapshot; update `docs/developers-guide.md`
"Verdicts and exit codes" and `docs/concordat-design.md` with the decision;
update the CLI docstring in `concordat/cli.py`. Gate and commit.

Stage D (Task 2): write failing tests for the spawn reader, entry-point reader,
and envelope; implement the three modules; register in
`nested_cargo_registry.py`; extend `tests/unit/test_envelope_selection.py`;
gate and commit.

Stage E (Task 3): write failing Rego tests and unit/BDD tests; build the canon
package; register both artefacts with SHA-256 in
`platform-standards/canon/manifest.yaml`; write README and documentation; gate
and commit.

Stage F (Task 4): write failing sweep/report tests; implement `--package`, the
ledger resume key, report filters and counts; run the local corpus comparison;
gate and commit.

Stage G: CodeRabbit review, gate re-run, draft PR.

## Concrete steps

All commands run from the worktree root.

```plaintext
uv run pytest tests/unit/test_rule_exit_contract.py -v
make check-fmt
make lint
make typecheck
make test
```

Commit gate runs are delegated to `scrutineer` with logs under `/tmp`.

## Validation and acceptance

- `uv run pytest tests/unit/test_rule_exit_contract.py` fails before the
  `exit_code` change on the all-warning cases and passes after.
- `concordat artefact rule run nested-cargo-offline-tests --repo <fixture>`
  prints a table whose rows carry severity, and exits `0` when every finding is
  a warning.
- The BDD feature `nested_cargo_offline_tests_rule_run.feature` passes for
  offline-safe, warning NC-007, missing-prefetch NC-008, and unreadable
  evidence.
- `make check-fmt`, `make lint`, `make typecheck`, `make test`,
  `make markdownlint`, and `make nixie` pass.

## Idempotence and recovery

Every step is a normal edit plus a gate run. A failed gate is retried after a
fix; commits are small so a bad change can be reverted with `git revert`. No
destructive commands are planned.

## Artefacts and notes

To be filled as evidence is captured.

## Interfaces and dependencies

- `concordat/rules/runner.py`: `WARNING_SEVERITY`, `RuleRunResult.blocking`,
  revised `RuleRunResult.exit_code`, severity column in `render_table`.
- `concordat/rules/nested_cargo_spawns.py`:
  `collect_rust_evidence(checkout, manifests, problems) -> RustEvidence`,
  `collect_trybuild_manifests(checkout, manifests, fixtures)` and
  `fixture_directories(checkout, manifests)`.
- `concordat/rules/nested_cargo_entry_points.py`:
  `collect_entry_points(checkout, problems) -> list[EntryPointFact]`.
- `concordat/rules/nested_cargo_nextest.py`:
  `read_nextest(checkout, problems) -> NextestConfig`, and the
  `NextestConfig.state(name)` reader that turns a selected profile into
  `before`, `absent` or `uncertain`.
- `concordat/rules/nested_cargo_shells.py`:
  `effective_shell(step, job, workflow, runs_on) -> ShellRule`, which resolves
  the `shell` a `run` block shares and whether it stops at the first failing
  line.
- `concordat/rules/nested_cargo_scripts.py`:
  `script_commands(text, *, newline_gates)` and
  `split_commands(text, *, newline_gates)`, which let the caller say whether a
  line break is proof that the earlier line succeeded.
- `concordat/rules/nested_cargo_offline_envelope.py`:
  `ENVELOPE_KIND = "policy-input/nested-cargo-offline-tests"`,
  `ENVELOPE_SCHEMA_VERSION = 1`,
  `build_nested_cargo_offline_envelope(checkout, parameters=None)`.
- `concordat/rules/nested_cargo_registry.py`: registers package
  `nested-cargo-offline-tests` and the new kind.

## Revision note

Initial draft, 2026-10-25.
