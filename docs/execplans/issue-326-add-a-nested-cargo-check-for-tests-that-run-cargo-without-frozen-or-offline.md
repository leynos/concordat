# Add a nested-cargo offline check and make warning findings advisory (#326)

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision log`,
`Outcomes & retrospective`, `Conformance basis`, and `Verification plan` must
be kept up to date as work proceeds.

Status: IMPLEMENTED, green after review

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
  counts. Outcome: triggered, and mitigated as planned — the local-mirror
  survey ran over 106 repositories with zero operational errors.
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
- [x] (2026-10-26) Task 2 probe-style follow-up: `command_state` now excludes
      plugin version probes (`cargo kani --version`) and treats a value-carrying
      `--version` as real work. Five cases added, red first; theoremc's replay
      drops from four spawns to three, whitaker's is unchanged.
- [x] (2026-10-26) Task 3: canon package `nested-cargo-offline-tests` created
      with `rule.yaml` (schema 1, version 0.1.0, default severity `warning`, a
      shipped exemption for whitaker's installer), the six-argument `finding`
      helper carrying `line`, 13 fixtures and 23 Rego tests. The Rego suite was
      run red against a missing policy (23 failures) and then green. Both
      canon artefacts are registered with recomputed SHA-256; the deferred
      `test_envelope_selection.py` rows are added; `test_nested_cargo_offline_rule.py`
      (16 tests) pins the whitaker before/after pair, theoremc's forwarded
      arguments and plugin probe, the survey-mode exit status, severity
      promotion, exemption isolation in both directions, and the namespace. A
      five-scenario BDD feature drives the real CLI. Developer, user, design
      and roadmap documents are updated; the design note that "every
      `finding(...)` call is `error` except RT-025" is corrected.
- [x] (2026-10-26) Task 3 gates: `make check-fmt`, `make lint` (all six stages,
      Skylos included), `make typecheck`, `make markdownlint` and `make nixie`
      pass. `make test` is red only on the seven pre-existing failures recorded
      in Surprises & discoveries: six `makeutil` fixture-drift tests and the
      `main-owned-codescene-coverage` conftest deadline flake. The gate surfaced
      two Ruff docstring findings in the new unit test (fixed), two spelling
      errors from an abbreviated commit SHA in this plan's prose (fixed by
      writing the full SHA), and a genuinely dead constant,
      `NON_RESOLVING` in `concordat/rules/nested_cargo_commands.py`, left behind
      by `935acee` when `_is_probe` replaced its only reader. The constant is
      removed; 172 reader tests and 28 nested-cargo package tests remain green.
- [x] (2026-10-27) Task 4 tooling: `--package` on both `parabellum_sweep`
      subcommands, threaded through `clone_and_audit` to `run_rule` and stored
      on every record; the resume key in `_already_ledgered` is now repository,
      commit, package and package version; `render_report` filters by package
      before selecting each repository's latest record, keeps the occurrence
      counts, and adds a captioned `## Repositories by rule` table of
      distinct-repository counts. Red first: 27 focused failures, all missing
      API or missing table. Two test-harness bugs surfaced in the green phase —
      `TestCloneAndAuditPackage`'s fake git inspected `argv[:1]` after `_git`
      had already prepended the binary, and `TestPackageSelection` had no
      audit-boundary fixture, so it attempted a real clone and timed out at
      30 seconds. Both fixed; 116 focused tests green. The first full gate run
      then failed on one Skylos `SKY-U001` for `_RuleTally.severity`; the
      analyser's own JSON shows it resolves `tally.severity()` inside
      `_rule_rows` to a module-level `tally.severity`, so the method records no
      static caller. Recorded as a typed entry point for that single
      fully-qualified method (Decision log), with the contract test's
      expected-symbol set updated; the targeted Skylos gate and the six Skylos
      contract tests are green.
- [x] (2026-10-27) Task 4 survey, run locally because the SRE plan file is
      absent (Decision log). The real runner audited all 106 locally mirrored
      bare repositories: zero operational errors, and every finding carried
      severity `warning`, so all 106 runs exited `0` — the survey mode works
      end to end. The first run, against the pre-review reader, found 53
      compliant, 37 indeterminate, 16 noncompliant and 144 findings. The
      re-run after the CodeRabbit fixes — whose entry-point EN-001 is now gated
      on `has_nested_build` and whose lexer reads `--config` values — finds 86
      compliant, 4 indeterminate, 16 noncompliant and 90 findings. Per rule on
      the re-run: NC-007 reached 6 repositories in 16 occurrences, NC-008 15
      repositories in 26 occurrences, EN-001 4 indeterminate-only repositories
      (ddlint, monotony, vtcode, wireframe) in 48 occurrences. Promotion to
      `error` would fail 20 of the 106 audited repositories: the 16
      noncompliant plus the 4 indeterminate ones, which is the union of the
      three rule sets. A mixed-package ledger renders the same table through
      the new filter, with only the selected package's records counted.
- [x] (2026-10-27) Task 4 real-checkout validation. whitaker `f7a5a93` (main
      before #518) is `noncompliant`: NC-007 on `common/tests/i18n_packaging.rs:58`
      and `crates/no_std_fs_operations/tests/integration_exclusion.rs:166`,
      NC-008 on `.config/nextest.toml:6`, seven EN-001 facts. The `#518` head
      `a108b29` is `indeterminate`: both NC-007s are gone (`--frozen` added)
      and the NC-008 is gone (a new `fetch` prerequisite), leaving five EN-001
      facts. theoremc `8f48abb` is `noncompliant`: NC-007 twice, NC-008 once,
      one EN-001.
- [x] (2026-10-27) Task 4 comparison with the 24 October survey. The two
      figures are repository counts of *any* nested-cargo evidence, not of
      NC-007/NC-008 findings. On the same 106 mirrors the raw greps find 10
      repositories with test-scope cargo spawns and 17 with trybuild, and the
      local mirrors cover 106 of the 180 non-fork, non-archived repositories;
      scaled by that coverage the counts become about 17 direct-spawn
      repositories and about 29 trybuild repositories, against the recorded
      about 20 and about 30. The difference is coverage, not disagreement;
      the rule's counts are a floor.
- [x] (2026-10-27) Delivery: draft pull request
      [#327](https://github.com/leynos/concordat/pull/327) opened against
      `main` with the survey counts, the real-checkout before/after evidence,
      the survey-coverage caveat and the Lody session reference in its body.
- [x] (2026-10-27) CodeRabbit review of
      [#327](https://github.com/leynos/concordat/pull/327)
      returned 12 findings; all were verified against the code and accepted,
      and each has its own red-then-green test where it changes behaviour.
      Fixed: cargo global options before the subcommand (4 tests); a `let`
      rebinding no longer credits the previous command (1); `--help`/`-h` join
      the probe flags (3); a nextest setup command is one argv, not a shell
      script (2); nextest `inherits` chains including the implicit `default`
      (4); a string `runs-on` label is classified as a one-item label list
      (2); a refused Makefile makes a delegating `make test` step `uncertain`
      (1); a conditional fetch step no longer proves the order (2); the report
      header names the ledger's versions rather than the manifest's (2 new
      report tests); and the uncertain-entry EN-001 is gated on
      `has_nested_build` (1 Rego test, 1 fixture).
- [x] (2026-10-27) Survey re-run after those fixes, then the PR body rewritten
      from it. The gate on the entry-point EN-001 is the decisive change: 33 of
      the 45 entry-point-uncertain repositories had no spawn and no trybuild, so
      they drop out entirely, and EN-001 falls from 52 indeterminate-only
      repositories to 4. The lexer fixes also caught `wireframe`, whose
      `test` target names an unresolved `DEV_FAST` variable through a
      `--config` value the old reader had not treated as one; that repository
      is now EN-001 indeterminate, so it was already in the union on another
      fact. The union of repositories promotion to `error` would fail falls
      from 53 to 20, and the noncompliant sets are unchanged at 6 NC-007 and
      15 NC-008 repositories. Re-auditing the three real-checkout snapshots
      with the fixed reader reproduces the recorded findings exactly.
- [x] (2026-10-27) Full sequential commit gate under the `scrutineer`
      subagent. Six of seven gates are green: `make fmt`, `make check-fmt`,
      `make lint` (Ruff, pylint 10.00/10, df12, ambrleaks, spelling and Skylos
      all clean), `make typecheck`, `make markdownlint` (0 issues) and
      `make nixie`. `make test` is red with exactly the six anticipated
      `makeutil` fixture-drift failures and nothing else — 6 failed, 2803
      passed, 1 skipped — and the `main-owned-codescene-coverage` OPA deadline
      flake did not recur. `make fmt` rewrote four files (Ruff reflows in
      `nested_cargo_scripts.py`, `nested_cargo_nextest.py` and
      `test_parabellum_report.py`, prose reflow in the rule README) and the
      reformatted ExecPlan, so the gate validated the formatted tree.
- [x] (2026-10-27) The formatter rewrites were committed as `4355bf8` and the
      gate record as `49328f7`, both pushed and verified against the remote
      head. A second `coderabbit review --agent --committed --base origin/main`
      returned seven unique findings, each reproduced before being fixed: a
      `#` in a shell script read as a live command; a `--` separator ignored by
      `_offline_flags`; the workflow `defaults` block passed one level too
      shallow to `effective_shell`; the report header naming the checked-out
      `MAKEUTIL_REV` rather than the revisions its records carry; two Rego
      rules that failed open on a fact with no `state` key; one design-doc
      miscount; and one ExecPlan decision that contradicted a later one. Each
      fix landed red first, and the Rego fix recomputed the manifest digest for
      `lint-rule-nested-cargo-offline-tests`.
- [ ] (2026-10-27) Remaining for delivery: re-gate the fixed tree, commit, push
      over SSH, and re-review.

## Surprises & discoveries

- Observation: 87 `finding(...)` calls appear to pass `"indeterminate"` as
  the severity. Evidence: they use the four-argument helper
  `finding(id, verdict, path, msg)` whose second argument is the verdict, not
  the severity; only `rust-toolchain-policy` and this repository's NC policy
  use five/`error`-fixed forms. Impact: the severity scan must be done
  per-helper, not by grepping the second string argument.
- Observation: `leynos/whitaker#518` is an open issue, not a pull request, but
  its branch
  `issue-518-fetch-once-then-run-nested-cargo-calls-frozen-so-tests-never-wait-on-the-package-cache-lock`
  exists at `a108b29bebb4f6657748173378589d7b07f2d32c` and its merge base with
  `main` is `f7a5a93132e989490021cbacbb9cc865e40a467f`, so the branch head and
  current `main` form a genuine before/after pair rather than a synthetic one.
  The fix is not merged: a fresh fetch of `origin/main` still reports both
  NC-007s and the NC-008. Impact: the Task 4 validation uses the real pair, and
  the local state of whitaker `main` remains the known-bad case.
- Observation: three runner probes disagree with a naive reading of the survey,
  and each was traced to its cause rather than assumed. Impact: (a) `netsuke`
  has no literal `Command::new("cargo")` yet is NC-008, because it spawns
  `Command::new(cargo())` through a helper at
  `tests/build_module_slice_ui_tests.rs:102` and
  `tests/command_env_ui_tests.rs:75`/`:178` — two `unresolved` spawns and an
  `absent` workflow entry point, which is a true positive; (b) `wildside` is
  the one repository in the 20-repository union that carries no EN-001, because
  its trybuild evidence produces NC-008 alone; (c) `theoremc` `origin/main` has
  moved from `8f48abb` to `70926a18`, where the fixture-crate spawn at
  `tests/common/fixture_crate.rs:224` is a new NC-007 — the survey snapshot is
  a floor, not a current total.
- Observation: an early raw-grep pass reported "0 repositories with test-scope
  cargo spawns", which was a regular-expression artefact rather than a finding.
  `git grep -E` is POSIX ERE: `\s` is not reliably a class and `(?:...)` is not
  a group, and the pattern silently matched nothing. Impact: every corpus grep
  was rewritten with `[[:space:]]` and `(...)?`; the corrected pattern finds
  test-scope cargo spawns in 10 repositories, 20 occurrences, and trybuild in
  17. A zero from a POSIX-ERE pattern must be confirmed, never trusted.
- Observation: the local mirror fleet holds 106 of the 180 non-fork,
  non-archived `leynos` repositories (`gh repo list` reports 210 in total), and
  the checked-in estate manifest names 52. Impact: the survey counts are a
  floor over about 59 per cent of the eligible estate and must be presented as
  such; they are not an estate total, and the PR says so.
- Observation: the whitaker `#518` recipe is a long `set -eu; …` chain with
  `$(if …)` conditionals, so the entry-point reader recognizes no test command
  in it and reports the `test` target `uncertain` ("a recipe command could not
  be read") rather than `before`, even though its new `fetch` prerequisite
  proves a success-gated `cargo fetch --locked`. This is a reader limitation,
  not a repository defect: the checkout is genuinely fixed, and the residual
  fact is `indeterminate`, which is the conservative reading the Constraints
  require. Impact: recorded as a residual limit; the after-fix validation is
  still decisive because the NC-007 and NC-008 findings it was written to
  observe both disappear.
- Observation: `~/docs/missing-frozen-survey-plan.md` does not exist on this
  host, so the planned SRE-driven sweep could not be used. Impact and
  resolution: the Task 4 survey ran instead as a local-mirror survey through
  the shipped runner (`concordat artefact rule run nested-cargo-offline-tests`)
  over all 106 repositories mirrored under `~/.lody/repos`, with 0 operational
  errors. The evidence and its limits are recorded in the Task 4 Progress
  entries and in the PR's Survey section: it is a floor over 106 of the 180
  eligible repositories, not an estate total. The comparison against the 24
  October figures was done as prescribed and every apparent outlier was traced;
  see the PR's "Comparison with the 24 October survey" section.
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
- Observation: `command_state` excluded a spawn only when `words[0]` was
  `--version`, `-V` or `help`, so theoremc's
  `Command::new("cargo").args(["kani", "--version"])` at
  `tests/theorem_file_macro_bdd/cargo_runner.rs:114` was reported `online`.
  Evidence: running `cargo kani --version` in an empty directory with no
  manifest prints `cargo-kani 0.67.0`, exits 0 and makes zero network syscalls
  under `strace -e trace=network`; a built-in subcommand given `--version` (for
  example `cargo build --version`) exits 1 without reading a manifest, and
  `cargo --version`, `cargo help`, `cargo build --help`, `cargo -vV` and
  `cargo tree --version` all exit 0 with no network. Impact: the exclusion now
  scans the flags before `--` for `--version`/`-V`, and only those, because a
  version flag that carries a value is real work:
  `cargo install --version 1.2.3` and `cargo add --version 1.2.3` consume the
  value and reach the registry. `help` remains a probe only as the first word,
  since `cargo run help` passes `help` to the binary under test after building
  it. Four new cases pin the behaviour (plugin version, plugin short version,
  `cargo run help`, `cargo test -- --version`) plus the value-carrying form;
  theoremc's spawn count drops from four to three and whitaker's is unchanged.
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
- Observation:
  `tests/unit/test_lint_rule_policies.py::test_the_package_policy_suite_passes[main-owned-codescene-coverage]`
  failed under `make test` with
  `Error: running verification: run test: eval_cancel_error: context deadline exceeded`,
  and the identical failure reproduces when `conftest verify` is run by hand.
  Evidence: the package's files are byte-identical to the base commit
  `ba9b7295e42bd0d14c0e894908183dbe68b6b735` (`git diff HEAD --` is empty and a
  `git archive` copy `diff -r`s clean), and the extraction of that archive from
  the base commit passes 98/98 while two consecutive hand runs against the
  worktree also pass 98/98. Impact: the failure tracks machine load, not the
  branch — OPA cancels an evaluation that overruns its deadline when the host
  is busy — so it is recorded as an environmental flake in an untouched
  package, and the gate is re-run rather than the policy changed.
- Observation: the Skylos dead-code stage reported `SKY-U003` for
  `NON_RESOLVING` in `concordat/rules/nested_cargo_commands.py`. Evidence:
  `git log -S` shows the constant entering at `8200b23` guarding the first-word
  probe check, and `935acee` replacing every reference to it with the
  `_is_probe` helper while leaving the declaration behind; a tree-wide search
  finds no reader. Impact: the constant is removed rather than allowed, since
  it is genuinely dead rather than a runtime entry point, and the removal was
  confirmed against all 172 reader tests and the 28 package tests.

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
- Decision: a version or help probe is excluded by scanning the flags before
  `--` for `--version`/`-V`, not by looking at the first word, and a flag with
  a non-flag word after it is not a probe. Rationale: the first-word rule
  missed plugin probes such as theoremc's `cargo kani --version`, which was
  reported `online` and would have become a false NC-007 in the Task 4 corpus
  comparison. The value rule keeps `cargo install --version 1.2.3` and
  `cargo add --version 1.2.3`, which are real registry work, out of the
  exclusion; the `--` rule keeps `cargo test -- --version` from being read as a
  cargo probe, and `help` stays a first-word-only probe because
  `cargo run help` builds and runs the program. Every branch was probed against
  the installed cargo, wrapper included. Date/Author: 2026-10-26.
- Decision: the report's package filter runs before the latest-record
  selection, not after, and its default is `RULE_PACKAGE` rather than "all
  packages". Rationale: the ledger is shared by campaigns, so selecting the
  latest record first would let a later survey record hide the baseline's
  earlier verdict for the same repository and the baseline report would
  silently lose a repository it audited. Defaulting to `RULE_PACKAGE` keeps
  `docs/parabellum/baseline-report.md` meaning the baseline campaign; an "all
  packages" default would mix two campaigns into a file titled after one of
  them. A survey report is requested explicitly with `--package`. Date/Author:
  2026-10-27.
- Decision: `rule_version` is read from the package manifest at audit time and
  the `RULE_VERSION` constant is deleted. Rationale: the constant had drifted to
  `0.2.0` while `rust-makefile-baseline/rule.yaml` declared `0.3.2`, so every
  new record would have named a policy version that never judged it — and the
  version is now part of the resume key, so a stale constant would also have
  suppressed legitimate re-audits. What the regenerated report then claims is
  settled by the later header decision: it names the versions its records
  carry, so it keeps reading `v0.2.0` — the version of the latest record per
  repository — until a re-audit writes records stamped by a newer manifest.
  Date/Author: 2026-10-27.
- Decision: "would fail" counts any repository with noncompliant or
  indeterminate-only evidence for a rule, whatever the finding's current
  severity. Rationale: a `warning`-severity noncompliant finding does not block
  today, which is exactly why the survey exists — it is the evidence a
  promotion-to-error decision is made on. Counting only `error`-severity
  findings would report zero for the survey that the promotion decision depends
  on. Date/Author: 2026-10-27.
- Decision: `would_fail` is the union of the noncompliant and
  indeterminate-only repository sets rather than a separate counter. Rationale:
  a repository with both kinds of evidence for one rule must be counted once;
  deriving the number from the two displayed sets also means the "would fail"
  column can never disagree with the columns beside it. Date/Author: 2026-10-27.
- Decision: `_RuleTally.severity` is recorded as a typed Skylos entry point,
  not as a named whitelist exception and not by inlining the method. Rationale:
  the method has a real runtime caller — `_rule_rows` calls `tally.severity()`
  at `scripts/parabellum_report.py:194`, and its value reaches the checked-in
  report and snapshot — but Skylos 4.33.2's own JSON records that call as the
  module-level name `tally.severity`, so the method itself has empty
  `called_by`. A one-symbol, correctly typed entry point describes that
  analyser limitation precisely; a whitelist entry is the last resort for a
  false positive no entry point can describe, and inlining would delete a
  cohesive method to satisfy a scanner. The contract test's expected
  entry-point set is extended with the same symbol so the exemption cannot
  widen silently. Date/Author: 2026-10-27.
- Decision: the Task 4 survey is run locally over the 106 mirrored
  repositories, not from `~/docs/missing-frozen-survey-plan.md` on the SRE
  host, because that file does not exist on this host (Surprises & discoveries)
  and the Tolerances forbid inventing its numbers. Rationale: the task's
  comparison needs real rule output over real checkouts, and the mirrors supply
  both. The substitute is stated plainly wherever the counts appear, including
  the PR, so no reader mistakes it for the SRE sweep. Date/Author: 2026-10-27.
- Decision: the 24 October survey figures ("about 20 with direct spawns and
  about 30 with trybuild") are read as repository counts of any nested-cargo
  evidence, not as NC-007/NC-008 finding counts, and the comparison is made
  against the raw greps rather than against the rule's tallies. Rationale: the
  rule reports *unsafe* spawns and *missing* prefetch, so its NC-007 count of 6
  and NC-008 count of 15 answer a narrower question than "which repositories
  have a nested cargo build at all"; comparing them directly would read a
  correct narrowing as a 70 per cent regression. The raw greps on the same 106
  mirrors give 10 direct-spawn and 17 trybuild repositories, which at the
  106-of-180 coverage becomes about 17 and about 29 — consistent with the
  recorded about 20 and about 30 within the coverage bound. Date/Author:
  2026-10-27.
- Decision: the survey evidence is reported as a floor with its coverage
  stated, not as an estate total. Rationale: 106 of the 180 eligible
  repositories are mirrored locally, and a count presented without that bound
  would be read as complete and would understate the promotion-to-error impact.
  Date/Author: 2026-10-27.
- Decision: `-h` is a probe flag alongside `--help`, `--version` and `-V`.
  Rationale: measured on cargo 1.99.1, `cargo build -h`, `cargo build --help`
  and `cargo -h` all exit `0` without building anything, so none of them can
  take the package-cache lock. CodeRabbit's review of #327 raised `--help` and
  raised `-h` as a separate finding that asked to exclude it; excluding it
  would report a help command as an online spawn, which is a false NC-007. Both
  findings therefore resolve the same way, and the exclusion suggestion is
  declined on the measurement rather than on preference. Date/Author:
  2026-10-27.
- Decision: an unreadable entry point is reported only where a nested build
  exists, matching NC-008's gate. Rationale: with no spawn and no trybuild,
  there is nothing a prefetch could warm, so the finding would ask a repository
  to fix a problem it does not have; the survey showed 33 of the 45
  entry-point-uncertain repositories in that state. NC-008 has carried this
  gate since the package was written, and the asymmetry was an oversight in the
  uncertain branch rather than a deliberate distinction. Date/Author:
  2026-10-27.
- Decision: the report header names the versions its records carry, falling
  back to the manifest only for an empty selection. Rationale: the ledger is
  append-only and a campaign re-runs an updated package over part of the
  estate, so one report can cover two versions; the checked-in report had
  already drifted to claiming `v0.3.2` while its records held `0.1.0` and
  `0.2.0`. Naming the manifest described records the report was not showing.
  Date/Author: 2026-10-27.
- Decision: the header names the `makeutil` revisions its records carry too,
  on the same reasoning and through a `_makeutil_revisions` helper beside
  `_versions`. Rationale: `MAKEUTIL_REV` is the revision this checkout would
  stamp on a new audit, so a ledger written by another worktree or before a
  submodule update carries a different one — all 104 records hold
  `29fc5a1634ff` while the header claimed the constant's `d33acd3ecb8d`. The
  checked-in `docs/parabellum/baseline-report.md` was regenerated and now
  matches. Date/Author: 2026-10-27.
- Decision: a fact with no `state` key, or one that is not an object at all, is
  reported as an envelope defect rather than skipped. Rationale: Rego leaves
  `not spawn.state in {...}` undefined when the key is absent, so the original
  rules failed open and a repository whose envelope carried an unclassified
  fact would have been reported clean. The sentinel is read with `object.get`
  behind an `is_object` guard, because `object.get` on a non-object is a hard
  type error that aborts the whole query. Date/Author: 2026-10-27.

## Outcomes & retrospective

The four planned tasks are implemented and validated. The runner no longer
fails a gate on a warning finding; the new package reports every test-scope
cargo spawn and every test entry point a checkout has, and ships as a survey.
The real runner over 106 mirrored repositories finds 16 repositories with an
unsafe nested cargo run or a cold test entry point and a further 4 where the
reader could not prove either way, which is the evidence a promotion decision
needs and is more than the checked-in fixtures could have shown. The review
round moved the sensitive numbers a long way — 37 indeterminate repositories
became 4 — which is itself the lesson that a permissive indeterminate branch
inflates exactly the count a promotion decision is made on.

Three lessons are worth carrying forward. First, a survey of a text-level
reader is only as good as its negative controls: three of the four probes that
looked wrong (the POSIX-ERE grep, the `cargo()` helper in `netsuke`, and
`theoremc`'s moved `main`) were mistakes in the *measurement*, not in the rule,
and each was settled by reading the checkout rather than by adjusting the
count. Second, a reader that cannot read a recipe must say so: the whitaker
`#518` target is genuinely fixed, but its rewritten recipe is unreadable to the
Make lexer, and the honest output there is `indeterminate`. Third, a local
mirror fleet is a usable sample but not a census; reporting its 106-of-180
coverage beside every count is what keeps a floor from being read as a total.

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

Survey scratch, kept under `/tmp` and not committed: the envelope survey
(`/tmp/corpus-survey/survey.py`, `results.jsonl`), the rule survey
(`/tmp/corpus-survey/run_rule_survey.py`, `rule-results.jsonl`, 106 records and
zero errors), the raw-grep baseline (`raw_counts.tsv`), the mixed-package
ledger used to exercise the report filter (`/tmp/corpus-survey/ledger.jsonl`)
and the rendered reports (`survey-report.md`, `survey-report-full.md`). The
checkout snapshots are `/tmp/nc-audit-whitaker-before`,
`/tmp/nc-audit-whitaker-after`, `/tmp/nc-audit-theoremc`,
`/tmp/nc-main-whitaker` and `/tmp/nc-main-theoremc`.

The survey was run twice: once against the reader as first reviewed (106
records, 0 errors, 144 findings) and once against the reader after the
CodeRabbit fixes (106 records, 0 errors, 90 findings). Both runs used the
shipped `concordat artefact rule run` path over the same 106 mirrors at
identical commits, so the difference between them is the code, not the corpus.
The tallies below are the post-fix run, which is the one reported in the pull
request; the pre-fix figures are kept in `Progress` for the before/after
comparison.

Survey tallies over the 106 mirrored repositories, with every finding at
severity `warning`:

| Rule   | Noncompliant repositories | Indeterminate-only repositories | Would fail |
| ------ | ------------------------- | ------------------------------- | ---------- |
| EN-001 | 0                         | 4                               | 4          |
| NC-007 | 6                         | 0                               | 6          |
| NC-008 | 15                        | 0                               | 15         |

The three sets are not disjoint; their union is 20 repositories, and every one
of those is a repository whose overall verdict is noncompliant or
indeterminate. Occurrence counts are 16 NC-007, 26 NC-008 and 48 EN-001.

The EN-001 indeterminate-only repositories are `ddlint`, `monotony`, `vtcode`
and `wireframe`. `vtcode`'s two facts are reader problems rather than
repository defects — a `#[cfg(test)]` item never closes in two files — and the
rest are Makefile or workflow recipes the lexer cannot reduce to one command.

Real-checkout validation, from the shipped CLI:

```plaintext
whitaker f7a5a93  noncompliant  NC-007 x2, NC-008 x1, EN-001 x7
whitaker a108b29  indeterminate              EN-001 x5
theoremc 8f48abb  noncompliant  NC-007 x2, NC-008 x1, EN-001 x1
```

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
