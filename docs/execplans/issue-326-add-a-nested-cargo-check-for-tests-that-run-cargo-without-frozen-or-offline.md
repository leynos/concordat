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
spawn below `tests/`/`test_support/` that the reader can detect and that can
reach the network, and every entry point (`Makefile` target, nextest setup
script, CI workflow step) whose test ramp starts Cargo before it has prefetched
the graph offline. The report is advisory by default (`warning` severity) so
the estate can be surveyed before the rule is promoted to blocking, and a
warning finding must no longer fail a gate — that is the first task, and it is
what lets the survey run safely.

The audit is a static reading of the source, so its promise is bounded by what
that reading can see. It does not follow a macro that expands to
`Command::new("cargo")`, a `fn` pointer or trait object holding the
construction, or a command assembled in another crate; a spawn it cannot see
draws no finding. An entry point is reported only when the checkout already has
a spawn or trybuild fact for its prefetch to protect, so a checkout whose
spawns are all invisible to the reader is also silent about its entry points.
Both limits are stated in the rule's README, and they are the reason the rule
ships as a survey rather than a proof.

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
      repositories in 26 occurrences, EN-001 19 indeterminate-only
      repositories in 48 occurrences. Promotion to `error` would fail 20 of the
      106 audited repositories: the 16 noncompliant plus the 4 indeterminate
      ones, which is the union of the three rule sets. A mixed-package ledger
      renders the same table through
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
- [x] (2026-10-27) Second full sequential gate on the fixed tree. Six of seven
      green: `make fmt` (no tracked file rewritten), `make check-fmt`,
      `make typecheck`, `make markdownlint` (0 issues, spelling green) and
      `make nixie`. `make lint` failed once on a `docstring-missing-returns`
      the new `_offline_flags` docstring introduced — Ruff aborts the pipeline
      there, so Pylint, df12, ambrleaks, spelling and Skylos never ran — and
      passed on re-run with the `Returns` section added: Ruff clean against
      387 files, pylint 10.00/10 on both the plain and the df12 run, typos
      gate green, Skylos clean. `make test` reported 7 failed, 2813 passed,
      1 skipped: the six `makeutil` fixture-drift failures, plus the
      already-recorded `main-owned-codescene-coverage` OPA deadline flake in
      an untouched package. A Hypothesis `too_slow` health check on
      `test_properties.py::TestLedgerSelection::test_latest_record_is_the_last_appended`
      failed in the first run and cleared in the second; the test passes alone
      in 4.5 s and with its module under `-n auto`, and the host was
      saturated (four compile slots held, 48 requests waiting) during both
      runs.
- [x] (2026-10-27) Third CodeRabbit round: two findings, both verified before
      being fixed. `_requests_errexit` scanned only for the letter `e`, so
      `bash -o errexit {0}` read as not fail-fast and a gated fetch was
      reported `absent`; probed against bash, `-o errexit` stops the script,
      `+o errexit` unsets it, `-O errexit` is rejected, and `--errexit` is not
      a bash option, so only `-` words followed by the exact name count. The
      Rego indeterminate paths read `entry.reason`, `spawn.flags` and
      `spawn.environment` directly, so an incomplete fact left the rule body
      undefined and dropped its EN-001; `object.get` defaults now report the
      absence. Both landed red first, and the policy digest was recomputed.
- [x] (2026-10-27) Third full sequential gate on `c0907e7`. Six of seven
      green: `make fmt` rewrote no tracked file, `make check-fmt`, `make lint`
      (Ruff, pylint 10.00/10 on both runs, ambrleaks, the spelling gate, and
      Skylos), `make typecheck`, `make markdownlint` (0 issues) and
      `make nixie`. `make test` reported 7 failed, 2815 passed, 1 skipped —
      the six known `makeutil` fixture-drift failures and the
      `main-owned-codescene-coverage` OPA deadline flake, with no new failure.
      The pass count rose from 2813 to 2815, which is the two new errexit
      tests; the Hypothesis `too_slow` health check did not recur.
- [x] (2026-10-27) Fourth CodeRabbit round, on the pushed `2cc856e`: two
      findings, both verified before being fixed. `_LET_BINDING` required
      whitespace directly after the `=`, so a path-qualified
      `std::process::Command::new` binding was missed, its later `.arg` calls
      were never collected, and a command carrying `--frozen` was reported
      `online` — a false NC-007, confirmed by a new `path-qualified-binding`
      case that returned `online` before the fix and `offline` after. The
      developer's guide listed `indeterminate` as an example of a blocking
      severity, which contradicts `RuleRunResult.exit_code`; the bullet now
      says an indeterminate finding blocks only when its severity is not
      `warning`.
- [x] (2026-10-27) Fourth full sequential gate on `ec0838d`. Six of seven
      green: `make fmt` rewrote no tracked file, `make check-fmt`, `make lint`
      (Ruff, pylint 10.00/10, ambrleaks, spelling, Skylos), `make typecheck`,
      `make markdownlint` (0 issues) and `make nixie`. `make test` reported
      6 failed, 2817 passed, 1 skipped — only the six known `makeutil`
      fixture-drift failures this time; the `main-owned-codescene-coverage`
      OPA deadline flake and the Hypothesis `too_slow` check did not occur. The
      pass count reconciles exactly: 2815 before, plus the new
      `path-qualified-binding` case, plus the codescene test that had been
      flaking.
- [x] (2026-10-27) Fifth CodeRabbit round, on the pushed `ca5269a`: one
      finding, the same `indeterminate` exit-code ambiguity the developer's
      guide carried, repeated in `docs/concordat-design.md` — which also
      contradicted its own "Evidence versus enforcement" section a few lines
      below. Fixed there too, and a search confirms no other document repeats
      the wording.
- [x] (2026-10-27) Sixth CodeRabbit round, on the pushed `6438697`: one
      finding, verified. `split_top_level` split on commas inside quoted
      literals, so `.env("RUST_LOG", "info,foo=debug")` looked like three
      slots rather than one pair, the command became `unresolved`, and its
      real NC-007 finding was hidden — the error points the safe way but still
      costs a finding. The splitter now tracks quoted state and backslash
      escapes; a new case reported `unresolved` before the change and
      `online` after.
- [x] (2026-10-27) Fifth full sequential gate on `4029121`. Six of seven
      green: `make fmt` rewrote no tracked file, `make check-fmt`, `make lint`
      (Ruff, pylint 10.00/10 on both runs, ambrleaks, spelling, Skylos),
      `make typecheck`, `make markdownlint` (0 issues) and `make nixie`.
      `make test` reported 6 failed, 2818 passed, 1 skipped — again only the
      six known `makeutil` fixture-drift failures, with the codescene flake and
      the Hypothesis `too_slow` check both absent. The pass count moved
      2817 → 2818, exactly the new `comma-inside-an-env-value` case, and the
      three callers of `split_top_level` are covered by suites that stayed
      green.
- [x] (2026-10-27) Seventh CodeRabbit round, on the pushed `10bdf1f`: two
      findings, both on the same line and verified before fixing. The
      `EP-POLICY-1` obligation said every uncertain fact is EN-001, which is
      not what the policy does and not what the Decision log records: the
      uncertain-entry finding is gated on `has_nested_build`, and
      `test_an_uncertain_entry_point_without_a_nested_build_is_not_judged`
      already pins that. The obligation now states the condition and separates
      the four cases it had run together. `make check-fmt` rejected the first
      wording (`+2 -2`) and `make fmt` re-wrapped it; `make check-fmt` and
      `make markdownlint` then both returned 0.
- [x] (2026-10-27) Post-review re-measurement of the PR body's figures, because
      the survey that produced them ran at 03:06–03:07 (reader as of
      `2f1c424`) and four later rounds changed the reader again. Both survey
      halves were re-run over the same 106 mirrors at current HEAD, and the
      rule path is **element-wise identical** to the 03:06 run: same commits,
      same verdicts, same findings list per repository. So the published
      figures stand unchanged — 106 records, 0 errors, 86 compliant, 16
      noncompliant, 4 indeterminate, 90 findings (16 NC-007, 26 NC-008, 48
      EN-001), 6 NC-007 and 15 NC-008 repositories, 19 EN-001 repositories and
      the same 20-repository would-fail union. The envelope-level diff was
      taken against the older 01:08 corpus instead, which predates the
      round-one lexer fixes as well; it shows exactly one repository —
      `wireframe`, whose `test` target now reads as an unresolved `DEV_FAST`
      variable — which is that round's known change and was already
      indeterminate on another fact. Neither baseline moves a count. The three
      pinned real-checkout snapshots were re-audited at current HEAD and
      reproduce their recorded findings line for line. The figures that *were*
      stale were test counts and the review tally, not the survey: `24 Rego
      tests` was 29, `60`/`96` reader tests were 64/106, `43` report tests were
      44, and the review paragraph described one round when there were seven.
- [x] (2026-10-27) Eighth CodeRabbit round, on the pushed `85bf5c9`: two
      findings, both verified before fixing and both behaviour changes landing
      on tests that failed first.
      The first: `report --package <survey>` defaulted `--output` to the
      tracked `docs/parabellum/baseline-report.md`, so a survey silently
      replaced the baseline with another package's figures, under a heading
      that still called them a baseline. `--output` now resolves inside the
      command, a survey with no explicit path exits 2, and the renderer titles
      a non-default package's report as a survey. The campaign package's
      no-flag invocation is unchanged. Confirming this one first turned up its
      own cause: `DEFAULT_REPORT_PATH` is bound as a default argument at import
      time, so the failing test's `monkeypatch.setattr` could not redirect it
      and the "red" run wrote over the tracked baseline file twice. Both times
      the file was restored with `git checkout --` before continuing, and the
      final implementation reads the module global at call time, which is what
      makes the test able to redirect it.
      The second: `CARGO_NET_OFFLINE` was compared only against `"true"`.
      Cargo's boolean parser also accepts `1`; measured against cargo 1.99.1,
      `1` prints the offline note that `--offline` prints while `yes`, `on`, an
      empty value and `0` do not. So a literal `"1"` was reported `online`
      (hiding an offline command) and a literal `"false"` was reported
      `unresolved` (hiding a real NC-007 as an "arguments the reader cannot
      see" EN-001). Both now read correctly. CodeRabbit's suggested
      `value is None` fixes only the second half, so the measured value set is
      used instead.
      Red-green evidence: `scripts/tests/test_parabellum_cli.py` 1 failed /
      10 passed, then 12 passed; `test_a_survey_report_is_not_titled_a_baseline_report`
      red on the hardcoded heading, then green; the offline cases were already
      red at `unresolved` and green at `offline`/`online`. `scripts/tests/`,
      `tests/unit/test_snapshots.py` and `tests/unit/test_nested_cargo_spawns.py`
      now run 232 passed and 298 passed in the two runs above it; `make
      typecheck` and `ruff check`/`ruff format --check` pass.
- [x] (2026-10-27) Ninth CodeRabbit round, on the pushed `57789cf`: two
      findings, both verified against the code before fixing, and both share
      the same failure mode — a nested build that produces no evidence at all,
      so no finding is emitted rather than a wrong one.
      The first: `#[cfg(test)] mod checks { mod helpers; }` makes the compiler
      build `src/checks/helpers.rs` into the test target, but only
      out-of-line declarations carrying a `#[cfg(test)]` attribute of their own
      were followed. A cargo spawn in the inherited file produced no NC-007.
      The fix adds a second pass over the whole file's now-known scope; a
      declaration whose own attribute run carries any `cfg` is still left to
      the attributed pass, because the reader cannot decide what a non-test
      gate compiles. Red: `assert [] == [('src/checks...s', 'online')]`; the
      two negative controls (module outside test scope, config-gated
      declaration) stayed green.
      The second: cargo renames a dependency with `key = { package = "name" }`,
      so `tb = { package = "trybuild", version = "1" }` declares trybuild under
      the key `tb`. Both readers compared the key directly, so a renamed
      dependency produced no manifest fact and its `tb::TestCases` references
      produced no source fact — and missing trybuild evidence suppresses both
      NC-008 and EN-001 through the policy's `has_nested_build` guard. Each
      entry now resolves through its own `package` field, and a manifest's
      declared names are applied to the sources it governs. Red:
      `assert [] == [('member/Car..., 'manifest')]` and the same for the
      source reference and the sibling-package control.
      Neither fix fits inside pylint's 800-line module cap, so the scope
      machinery moved to `concordat/rules/nested_cargo_modules.py` first, as a
      pure extraction (`e53a314`) with the suites passing unchanged: 179
      passed at that commit, 183 after the scope fix, 189 after the trybuild
      fix. Final red-green per commit: `e53a314` 179 passed, `ed39787` 2
      failed then 70 passed, `bb83e50` 3 failed then 76 passed; the three
      nested-cargo suites run 189 passed and pylint, `ruff check`,
      `ruff format --check` and `ty` all pass.
- [x] (2026-10-27) The users' guide, the design document and the rule README
      now describe the widened evidence: test scope inherited from an
      enclosing `#[cfg(test)]` module, non-test `cfg` gates left unjudged, and
      trybuild recognized under a dependency rename with each manifest's names
      applied only to the sources it governs. `make markdownlint` and
      `make nixie` both exit 0.
- [x] (2026-10-27) Full gate run by `scrutineer` on `b923333`: six of seven
      gates passed first time — `fmt`, `lint` (Pylint 10.00/10 on both
      passes), `typecheck`, `markdownlint`, `nixie`, and `test` at 6 failed /
      2833 passed / 1 skipped. `check-fmt` failed, and it was a genuine
      regression from the documentation commit rather than inherited: both
      `origin/main` copies are mdtablefix-clean under the same pinned 0.6.1,
      while the new paragraphs were hand-wrapped to 80 columns and mdtablefix
      wraps at its own computed width. `make fmt` reflowed both files and
      `make check-fmt` now exits 0 (`b104181`). Lesson: run `make fmt` before
      `make check-fmt`, not after, whenever prose is added by hand.
      The six `make test` failures are the acknowledged makeutil drift, and
      the runner confirmed the count reconciles exactly against the 2840
      collected items with no other failure — local makeutil is `0.1.2` while
      `ci.yml` pins `0.1.0` at digest `99dd28a…`, and none of the five failing
      modules appears in `git diff --name-only origin/main...HEAD`.
- [x] (2026-10-27) Tenth CodeRabbit round, four findings. Two were code and
      two were documentation. The documentation findings were both a counting
      error: the EN-001 row of the survey table said 0 noncompliant / 4
      indeterminate-only / 4 would fail, when the survey data gives 0 / 19 /
      19, and the four-repository list was really nineteen. Recomputing the
      rows from `rule-results.jsonl` per finding verdict rather than per
      repository verdict fixed the table at `:1090` and the mid-plan entry at
      `:183`, and corrected the same table and list in the pull-request body.
      The union arithmetic was always right — 16 noncompliant verdicts plus 4
      indeterminate ones is 20 — which is why the error survived. The reason
      is a count, not containment: with only 16 noncompliant repositories in
      total, at most 16 of the 19 EN-001 repositories can sit inside other
      rules' noncompliant sets, so at least three of them must be repositories
      whose overall verdict is indeterminate. They still fall inside the
      20-repository would-fail union, which is why every estate-level
      cross-check agreed while the rule-level row was wrong.
- [x] (2026-10-27) The first code finding: `command_state` read
      `cargo .args(extra).arg("--frozen")` as offline, but an unreadable word
      may be `--`, after which every later word belongs to the program cargo
      runs. Offline evidence now comes only from the literal prefix, the words
      before the first unreadable argument. The reverse order stays offline,
      because cargo parses `--frozen` before reaching anything unreadable.
      Both orders are parametrized cases; the before case fails against the
      previous reader (`16f4e80`).
- [x] (2026-10-27) The second code finding: scope followed only one hop out of
      an attributed declaration. Files are now rescanned to a fixed point, each
      carrying how it was reached, so `#[path]`-reached files resolve children
      beside themselves and crate roots resolve them in their own directory.
      Verified against the compiler before implementation; a probe package
      confirms `tests/support/probe.rs` needs `tests/support/helpers.rs` and
      rejects `tests/support/probe/helpers.rs` with E0583 (`7073580`).
- [x] (2026-10-27) The fixed-point resolve added twenty-five locals to
      `collect_rust_evidence`, over Pylint's `max-locals = 20`. Extracting
      `_scan_source` and `_discover_scope` brought it under the limit with
      Pylint 10.00/10 on the gate's actual target list, `concordat scripts
      tests`. Pylint's recursive walk does not descend into `tests/unit`,
      which has no `__init__.py`; a probe module planted there is never
      reported, so the 1061-line test module and its `== []` assertions never
      reach this gate. The pre-existing 1258-line
      `test_nested_cargo_entry_points.py` sits in the same position. Recorded
      rather than restructured, because the gate is what governs.
- [x] (2026-10-27) Estate survey re-run on the fixed-point reader, after the
      extraction: 106 records, 0 errors, 90 findings, 86 compliant / 16
      noncompliant / 4 indeterminate, and **zero per-repository differences**
      from the previous reader. The one deliberate behaviour change — reading
      files behind a second hop — adds no finding on the real corpus, because
      the added files are either already reached by another chain or carry no
      spawn. An earlier version of the change did add 21 findings; all 21 were
      false positives from following `mod` lines in files no compiler builds,
      fixed by restricting the scan set. The README now states that
      restriction, the crate-root and `#[path]` child rules, and the one
      compiler rule deliberately left unimplemented (`#[path]` on an inline
      module, three estate occurrences, none in test scope).
- [x] (2026-10-27) Markdown gates on the round's documentation, run in the
      order the plan's own lesson prescribes: `make fmt` first, because
      `mdtablefix` wraps prose at its own computed width rather than at 80
      columns, then `make check-fmt`, `make markdownlint` and `make nixie`.
      `make markdownlint` first failed on spelling: the new progress entry used
      the British `-ise` form of `parametrized`, which the shared en-GB-oxendict
      gate rejects in favour of `-ize`, the form the plan already uses
      elsewhere. A ragged line the reflow left in the Task 4 entry was rejoined
      and the file reflowed again. All four gates then passed.
- [x] (2026-10-27) The three pinned real-checkout snapshots re-audited with
      the final reader, through the shipped CLI rather than through the
      survey driver: whitaker `f7a5a93` noncompliant (NC-007 twice, NC-008
      once, seven EN-001), whitaker `a108b29` indeterminate (five EN-001),
      theoremc `8f48abb` noncompliant (NC-007 twice, NC-008 once, one
      EN-001) — the recorded findings, line for line, unchanged by the
      fixed-point rewrite.
- [x] (2026-10-27) The full estate survey re-run after the documentation
      commit: 106 records, 0 errors, 86 compliant / 16 noncompliant / 4
      indeterminate, 90 findings (16 NC-007, 26 NC-008, 48 EN-001). One
      repository differs from the 03:06 baseline and only in wording: netsuke
      carries nine findings on both runs, and one EN-001 problem sentence
      reads "the mod tests; declaration names no file" where the baseline
      reads "a #[cfg(test)] mod tests; declaration names no file". That
      rewording is from `ed39787`, already committed before the baseline was
      taken; no verdict, count or finding identity moved.
- [x] (2026-10-27) `make lint` failed on the fixed-point code, which had been
      gated for Pylint but never for Skylos: `Reach.merged` was reported
      `SKY-U001` with `references: 0` and `called_by: []`. The call is real
      and static — `nested_cargo_modules.py:641`, `before.merged(observed)` —
      and Skylos records it under the receiver expression instead:
      `concordat.rules.nested_cargo_modules.reaches.get.merged`, because
      `before` is bound from a `dict.get` call it cannot type. Bisected on a
      scratch copy of the module: scanning the file alone reports nothing,
      scanning the package reports the method, and rewriting the receiver as
      an explicit `None` check changes nothing, so the trigger is the
      cross-module reference pass rather than the call's shape. Recorded as a
      typed `method` entry point for the one symbol, naming the call site and
      the mutation that proves it live; a control run with the record removed
      reports this method and a deliberately planted dead sibling, and reports
      nothing else, so the entry is narrow rather than a class-wide
      suppression.
- [x] (2026-10-27) The seeded-fault control turned up a real gap in the
      round's own tests: reducing `Reach.merged` from a union to the last
      observation left all 84 spawn-reader tests green. No test exercised a
      file reached both by `mod name;` and by `#[path]` at once, which is the
      only case where the union matters.
      `test_a_file_reached_both_ways_keeps_every_base` was written first
      against the mutant, failed with no spawn where it expects
      `src/checks/inner.rs`, and passed once the union was restored.
      `test_skylos_lint_contract.py` asserted an exact entry-point set and was
      updated to carry the new typed record, keeping the set independently
      maintained rather than derived from the configuration under test.
- [x] (2026-10-27) Full commit gates on the round's commits, through
      `scrutineer`, sequentially, with each log under `/tmp`: `make check-fmt`,
      `make lint` (all six stages, Skylos included), `make typecheck`,
      `make markdownlint` and `make nixie` all exit `0`. `make test` reports
      the six `makeutil` fixture-drift failures that predate the branch and,
      on one pass, the OPA `eval_cancel_error` flake; the flake cleared on the
      re-run, and no failure is branch-related. The gate run was started
      before a two-line plan correction landed, so the docs-sensitive gates
      were re-run at the corrected commit; the delta touches no Python.
- [x] (2026-10-27) Eleventh CodeRabbit round, foregrounded: two unique
      findings, both real. A hyphenated trybuild alias was matched by its
      manifest key, but Cargo exposes `my-trybuild` to Rust as `my_trybuild`,
      so the pattern named a path no file can spell and the source fact was
      lost. The impact is narrower than the finding claimed — the manifest
      fact is recorded independently, so `has_nested_build` still held and no
      NC-008 went missing — but the evidence that the checkout *uses*
      trybuild was absent, and the new test fails against the previous
      reader. The second finding was documentation drift from the tenth
      round: the code narrowed the offline value set to `"true"` and `"1"`
      while five prose descriptions still named `"true"` alone. Both fixed,
      with the README now stating which spellings leave the network reachable.
- [x] (2026-10-27) The round's commits pushed over SSH as `00ec18a`, with
      `/usr/bin/gh api user` confirming `leynos` and a `git ls-remote`
      matching `git rev-parse HEAD`; the local tracking ref was refreshed with
      a leading-`+` refspec and the branch's upstream set to `origin` by name.
      The PR body was rewritten from `/tmp/pr-body.md` and every count in it
      recomputed from the survey data rather than carried forward.
- [x] (2026-10-27) Twelfth CodeRabbit round, foregrounded: five findings, four
      unique defects, all real and all reproduced before any edit. Each was
      confirmed against the running reader rather than taken from the review
      text: a symlinked `Makefile` and a symlinked `.config/nextest.toml` both
      read silently and produced facts with no error, `_environment_pairs`
      returned `None` for a rustfmt-formatted `.env(...)` call, and
      `success() || github.ref == 'refs/heads/main'` reported `before` on a
      step that GitHub will run after a failed fetch.
- [x] (2026-10-27) The containment fixes, in
      `concordat/rules/nested_cargo_entry_points.py`. `_makefile_entry` and
      `collect_entry_points` now pass their policy input through
      `within_checkout` with the new `read-nested-cargo-makefile` and
      `read-nested-cargo-nextest` operation names, after the `is_file()` check
      because `Path.is_file()` follows symbolic links. Three tests pin the
      behaviour: two refusals and one positive control, so the guard is shown
      to refuse outside paths without refusing legitimate ones.
- [x] (2026-10-27) Findings 2 and 5 are one defect: `_abandons_order` asked
      "does the condition mention `always()`, `failure()` or `cancelled()`",
      which is not the question. GitHub applies its implicit `success()` guard
      only when the `if:` names no status function at all, so
      `success() || github.ref == …` runs after a failed fetch while
      `success() && runner.os == 'Linux'` does not, and a substring test cannot
      tell them apart. `_abandons_order` is deleted. In its place
      `runs_only_after_success` answers from a three-valued recursive-descent
      evaluator over the condition, `_evaluated_after_failure`, which holds
      `success()` false and asks whether the condition could still be true.
      `True` means gated, `False` not, and `None` means the answer depends on
      an operand the reader does not evaluate. The fetch step's own question
      keeps `condition_proves_order`, which is the stricter "runs on every
      run" test; the two questions differ and finding 5 says so explicitly.
- [x] (2026-10-27) Finding 3: the same-step `gates_fetch` proof is now
      conditional on `not has_expression`. A step running
      `cargo fetch --locked ${{ inputs.extra }} && cargo test` reads as gated
      once the expression is stripped, but `inputs.extra` may supply
      `|| true`, which makes a failed fetch non-fatal. The step is reported
      `uncertain` instead.
- [x] (2026-10-27) Finding 4 in `nested_cargo_commands.py`: a single `.env`
      pair split across lines by rustfmt carries a trailing comma, so
      `split_top_level` returns three slots with a blank last one and the
      slot-count guard rejected the pair. That made an unrelated multi-line
      `.env` report the command `unresolved`, and made a multi-line
      `.env("CARGO_NET_OFFLINE", "true",)` lose its offline evidence. The blank
      slot is dropped before counting, and two spawn-reader cases cover both
      consequences.
- [x] (2026-10-27) Red before green: the four nested-cargo modules produced
      six failures for exactly the reasons above before any production edit
      and 229 passes after. The first green run then failed on four of my own
      new parametrized expectations, which asserted `absent` for every compound
      `if:`. Probing `_evaluated_after_failure` directly showed the honest
      verdicts differ — `failure() || success()` is `absent`,
      `success() || github.ref == …` is `uncertain`, `success() && …` is
      `before` — and the table was rewritten to state each one.
- [x] (2026-10-27) Ruff took nine findings from the new code and all are
      fixed: four `returns` sections added, the four boolean-typed parameters
      of `_gated`, `_or_value` and `_and_value` made keyword-only at both
      definition and call site, and the 91-character operand terminator
      wrapped.
- [x] (2026-10-27) The first full gate run went red on pylint `C0302`:
      `nested_cargo_entry_points.py` reached 989 lines against the 800-line
      limit at `pyproject.toml:164`, having been 729 lines at `HEAD`. The
      condition evaluator — the four `if:`-syntax constants and the eleven
      functions from `_condition_body` down to `_skip_space` — was moved
      wholesale into a new `concordat/rules/nested_cargo_conditions.py`, whose
      module docstring records why the two steps ask opposite questions of the
      same syntax. Only `condition_proves_order` and `runs_only_after_success`
      are public there; the entry-points module imports those two and keeps
      `_gated` and `_allows_failure`, which take a step mapping. The module
      dropped to 763 lines. A direct unit module,
      `tests/unit/test_nested_cargo_conditions.py` (25 cases), pins the
      extracted predicate against conditions built to distinguish a three-valued
      answer from a two-valued one, including the conjunction/or-disjunction
      pair that the old substring test could not tell apart.
- [x] (2026-10-27) The same run's `make markdownlint` and `make test` both
      aborted on the `spelling` prerequisite: the hyphenated form of
      "misread" in the Outcomes section is split by the checker into a
      two-letter prefix and "read". Reworded to the unhyphenated spelling.
- [x] (2026-10-27) The round's behaviour changes are documented rather than
      left to the code: the rule README gains two "what counts" bullets (both
      steps' `if:` conditions, and the expression-in-command case), the users'
      guide a paragraph on the test step's question,
      `docs/concordat-design.md` the reasoning for asking the two steps
      different questions, and the developers' guide the builder's side.
- [x] (2026-10-27) The full gate sequence over the fixed tree is green:
      `make check-fmt`, `make lint`, `make typecheck`, `make markdownlint` and
      `make nixie` all pass, and `make test` reports 2882 passed with the six
      known `makeutil` fixture-drift failures and no nested-cargo failure. The
      ruff step of `make lint` took two more findings from the new test module
      — boolean-typed positional parameters again — fixed keyword-only.
- [x] (2026-10-27) CodeRabbit round 13, finding 3: status-function names are
      matched without regard to case. GitHub's expression parser lowercases a
      function name before looking it up, so `Always()` on a test step runs it
      after a failed fetch and `SUCCESS()` names a status function rather than
      leaving the implicit guard in place. `_STATUS_CALL` and `_STATUS_ATOM`
      take `re.IGNORECASE`, the captured name is lowercased before the
      `_FAILED_RUN_STATUS` lookup, and the fetch step's stricter test now
      compares through a dedicated `_SUCCESS_ONLY` pattern instead of a literal
      string, so `Success()` proves the order too. Red first: the pre-fix
      module returned `True` (gated) for all of `Always()`, `${{ ALWAYS() }}`,
      `Failure()` and `SUCCESS()`.
- [x] (2026-10-27) CodeRabbit round 13, finding 1: string literals are data,
      not syntax. `_literal_end` walks a single-quoted literal, treating `''` as
      an escaped quote, and `_without_literals` blanks each literal before the
      status-function search, so `github.event.name == 'always()'` names no
      status function and keeps the implicit `success()` guard.
      `_unknown_operand_end` and `_operand` step over literals, so a `)` or `||`
      inside one no longer ends an operand early. An unclosed literal is an
      expression GitHub would reject, and the answer is `None`.
- [x] (2026-10-27) CodeRabbit round 13, findings 2 and 4 (the same weakness in
      `scripts/tests/test_parabellum_report.py`): the fixture versions are
      raised to `7.1.0`/`7.2.0` and the negative assertion now names the
      fallback's exact spelling — `` `rulename` vX; `` — rather than asserting
      that the manifest version appears nowhere in the report. A future bump to
      either fixture version, or a version that happens to be a substring of
      another number, can no longer fail the test for no reason.
- [x] (2026-10-27) Round 13's behaviour is documented in the rule README
      (case-insensitive names and quoted literals), the users' guide (the same,
      from the reader's side), and `docs/concordat-design.md` (the parser's
      treatment of names and literals, including the unclosed-literal case).
- [x] (2026-10-27) Round 13's full gate sequence, run through `scrutineer` on
      the round's tree: `make check-fmt`, `make lint`, `make typecheck`,
      `make markdownlint` and `make nixie` all pass, and `make test` reports
      2898 passed, 1 skipped, and the six known `makeutil` fixture-drift
      failures with no nested-cargo failure. That is sixteen more passes than
      the previous run, matching the round's sixteen new cases.
- [x] (2026-10-27) Round 14's six findings are implemented. The spawn reader
      now treats a repeated `CARGO_NET_OFFLINE` as last-write-wins, matching
      `Command::env`, while `--frozen` stays irrevocable; a helper with no call
      site in its own file is reported at its definition as `unresolved` rather
      than dropped; the policy rejects a fact missing a field its finding reads,
      or carrying one of the wrong type; the unused `_rule_cells` copy in
      `TestPackageFiltering` is deleted; and the plan's own `EP-EXIT-1` and
      Decision-log entry were corrected to say what `runner.py` actually does —
      `blocking` and `exit_code` never consult the verdict.
- [x] (2026-10-27) Round 14's behaviour is documented in the rule README
      (last-assignment-wins, `--frozen` irrevocable, the required-field check),
      the users' guide (the same, from the reader's side), the developers'
      guide (the reader's contract and the cross-file helper fallback), and
      `docs/concordat-design.md` (both).
- [x] (2026-10-27) The policy's manifest digest is recomputed for the round-14
      edit: `426c83d06819db1549f9abb19ca1cdb4e44ff2e61fa8cca7ae5983a0f9f877f7`.
- [x] (2026-10-27) Round 14's full gate sequence, run through `scrutineer` on
      the round's tree: `make check-fmt`, `make lint`, `make typecheck`,
      `make markdownlint` and `make nixie` all pass, and `make test` reports
      2907 passed, 1 skipped, and the same six known `makeutil` fixture-drift
      failures with no nested-cargo failure. Every collected item is accounted
      for.
- [x] (2026-10-27) Round 14 was committed as `2d32b95` (12 files, +490/−75),
      SSH-pushed, and the tracking ref refreshed with a leading-`+` refspec;
      `ls-remote` reported a remote head equal to the local one. The fifteenth
      CodeRabbit pass then returned four findings, all verified real.
- [x] (2026-10-27) Round 15's four findings are implemented. `env_remove` and
      `env_clear` now count as operations on the `CARGO_NET_OFFLINE` ledger
      rather than being stepped over, so a removal leaves the spawn `online`;
      an unreadable `envs(..)` list, removal name or value records "unknown"
      and makes the spawn `unresolved`; the sticky `is_uncertain` field is
      deleted, because the ledger's `None` already carries that answer and a
      later visible assignment must be able to replace it; and the
      unreached-helper fallback is now restricted to definitions inside test
      scope, so library code stays outside the rule as the README promises.
      Seven new spawn cases and one standalone test failed first, then 105
      spawn tests and 267 nested-cargo and policy tests passed.
- [x] (2026-10-27) Round 15's behaviour is documented in the rule README
      (removal as an operation, unreadable operations as unknown), the users'
      guide (the same, from the reader's side, plus the test-scope limit on the
      helper fallback), the developers' guide (the operation ledger and the
      limit), and `docs/concordat-design.md` (both).
- [x] (2026-10-27) The plan's opening promise and Outcomes claim are limited to
      constructs the reader can detect, with the blind spots named: macros,
      `fn` pointers and other crates, plus the entry-point suppression when no
      nested-build fact is visible.
- [x] (2026-10-27) Round 15's full gate sequence, run through `scrutineer` on
      the round's tree. The first pass was red at `make lint`: three Ruff FBT
      findings in the new `_record_offline_operation`, because a boolean was
      passed positionally. The parameter is now keyword-only and all six call
      sites pass `offline=...`. The second pass is green: `make check-fmt`,
      `make lint` (Ruff, Pylint 10.00/10, df12-pylint 10.00/10, AMBR leaks,
      spelling, Skylos all clean), `make typecheck`, `make markdownlint` and
      `make nixie` all exit 0, and `make test` reports 2915 passed, 1 skipped
      and the same six known `makeutil` fixture-drift failures. That is eight
      more passes than round 14, matching the round's eight new tests.
- [x] (2026-10-27) Round 14 was committed as `2d32b95`, pushed and verified;
      the fifteenth CodeRabbit pass then returned four findings, and round 15
      implemented them, ran the full gate sequence green (after one Ruff FBT
      fix), and committed as `a56e89a`. The sixteenth pass returned one
      finding.
- [x] (2026-10-27) Round 16's finding is implemented: the three exemption tests
      in `nested_cargo_offline_tests_test.rego` now exercise the property they
      name. Two used `named_elsewhere`, whose repository is `leynos/other`,
      while their exemptions named `leynos/example` — so the exemption could
      not match whatever the blank field were compared against, and both tests
      would pass with the rationale and review-trigger checks deleted. The
      file-scope test also compared only rule IDs, and two of the fixture's
      three findings are NC-007, so a path-blind match would still satisfy it.
      All three now use `exempt_spawn` (repository `leynos/example`) and assert
      `[rule_id, path]` pairs. Verified by mutation: with both blank-field
      checks removed, the old tests pass 38/38 and the new ones fail exactly
      the two; with `exemption.path == path` removed, only the new file-scope
      test fails alongside the pre-existing named-exemption test.
- [x] (2026-10-27) Round 16 was gated green (five gates clean, `make test` at
      the 2915 baseline), committed as `f7d461d`, pushed and verified, and the
      PR body updated. The seventeenth CodeRabbit pass returned one finding.
- [x] (2026-10-27) Round 17's finding is implemented: `_top_level_groups`
      ignores text after the closing bracket, where `_unwrap` already rejects
      it. `.envs([("CARGO_NET_OFFLINE", "true")].into_iter().chain(extra))`
      read as a literal list and reported `offline`, though `extra`'s pairs run
      after the visible one and may set the variable back. Probing
      `_environment_pairs` confirmed the reading before the fix and `None`
      after it; trailing whitespace still reads. The new case
      `envs-list-with-trailing-expression` failed first, then passed, and the
      spawn suite is 106/106. Four documents name the shape.
- [x] (2026-10-27) Round 17 was gated green after `make fmt` reflowed four
      documents and a code-span wrap I introduced was reworded, committed as
      `3d887af`, pushed and verified, and the PR body updated. The eighteenth
      CodeRabbit pass returned five findings, which are three distinct issues:
      the survey-union explanation (twice), conditional calls on a bound
      command, and `state` narrowing in a comprehension.
- [x] (2026-10-27) Round 18's conditional-call finding is implemented.
      `collect_variable` scanned the whole remaining block for `name.` and
      passed every hit to `follow_chain`, so
      `if frozen { cmd.arg("--frozen"); }` recorded the flag as unconditional
      and classified the spawn `offline`. A probe confirmed the reading in both
      shapes — an argument call and an environment call — before the fix. A hit
      whose enclosing block differs from the binding's block now goes to
      `follow_chain(..., conditional=True)`, which records it through
      `_record_unseen`: an argument method appends a `None` word, an
      environment method or removal records an unknown ledger entry, and a
      method that changes neither is stepped over. The two new state-changing
      cases and the negative control `conditional-bound-status` failed first
      and pass, and two further cases pin that an earlier unconditional
      `--frozen` survives a conditional call while an earlier unconditional
      environment assignment does not. The spawn suite is 111/111.
- [x] (2026-10-27) The mutation that proves those tests bite: reverting
      `collect_variable` to unconditional gives 3 failed / 108 passed, exactly
      the three cases written for the defect, with both negative controls still
      green. `--frozen`'s irrevocability is what makes
      `conditional-bound-flag-after-frozen` a real distinction rather than a
      duplicate: the recorded flag still proves offline mode, so the reader
      does not lose evidence it legitimately has.
- [x] (2026-10-27) The `# noqa: SLF001` I first wrote for the external call to
      `_record_offline_operation` was unnecessary and is gone. Pylint's
      `messages control` disables `all` and re-enables a named list that has no
      protected-access rule, and Ruff's `select` list has no `SLF` family, so
      nothing would have flagged it — but a public seam is the better shape
      anyway. `CargoCommand` now exposes `mark_arguments_unknown` and
      `mark_environment_unknown`, each a one-line statement of the fact being
      recorded, and `_record_unseen` uses them. A checker that did not exist
      yet would otherwise have made the private access a latent gate failure.
- [x] (2026-10-27) The `state` narrowing finding is applied as written:
      `(state := command_state(command)) is not None and state != "offline"`.
      The old membership test works under the interpreter, but the mutable set
      does not retain its literal members for negative narrowing, so a strict
      checker can still pass `str | None` to `_fact`.
- [x] (2026-10-27) The survey-union explanation is corrected at both
      occurrences. The old text said the 19 EN-001 repositories sit inside
      other rules' noncompliant sets; there are only 16 noncompliant
      repositories overall, so at most 16 of the 19 can, and at least three
      must be overall-indeterminate. Both places now give the count-based
      reason, and the 20-repository would-fail union is unchanged.
- [x] (2026-10-27) Round 18's behaviour is documented in the rule README, the
      users' guide, the developers' guide and the design document, each naming
      the conditional-call shape and the method that steps over.
- [x] (2026-10-27) Round 18's full gate sequence. `make fmt` rewrote no tracked
      file. `make check-fmt`, `make lint`, `make typecheck`,
      `make markdownlint` and `make nixie` all exit `0`; `make lint` reports
      Ruff clean, pylint 10.00/10 on both the plain and the df12 run, AMBR
      leaks and the spelling gate clean, and Skylos clean. `make test` reports
      6 failed, 2921 passed, 1 skipped. The six failures are the known
      `makeutil` fixture drift and nothing else: the installed binary is
      `0.1.2` while the workflows pin `0.1.0`, which is the cause those
      assertions name, and `git diff origin/main...HEAD` over the five
      affected test modules is empty, so this branch cannot have caused them.
      The pass count reconciles exactly against the round-17 baseline of 2915:
      plus one round-17 case and five round-18 cases.
- [x] (2026-10-27) Nineteenth CodeRabbit round, foregrounded: two findings,
      which are one defect. `collect_variable` matched only `name.` calls, so
      `configure(&mut cmd)` — a mutable borrow handed to another function that
      may set `--frozen` or `CARGO_NET_OFFLINE` — was invisible and the spawn
      read `online`. That is an NC-007 accusation from a guess, which the module
      promises never to make. Probed before the fix: the handoff shape returned
      `online` while the direct `cmd.arg("--frozen")` form returned `offline`.
      `_handoff(name)` now matches the borrow and records both an unreadable
      word and an unknown environment entry, so the spawn is `unresolved`.
      Four cases were written red first: the handoff, a negative control on an
      inline construction, and two ordering cases — `--frozen` before the
      handoff stays `offline` because nothing revokes it, while an offline
      environment before it does not, because the callee may rewrite the
      variable. Mutation-verified: disabling the new branch fails exactly the
      two state-changing cases and leaves both controls green. Two negative
      probes confirm the pattern is name-exact — `fill(&mut other)` and
      `fill(&mut cmdx)` leave the command `online`. The behaviour is documented
      in the rule README, the users' guide, the developers' guide and the design
      document.
- [x] (2026-10-27) The estate survey was re-run rather than assumed, because
      the handoff is the first review fix that could change a published count.
      A fleet scan found exactly one mirrored repository with the shape,
      `ortho-config`, using `CargoEnvironment::apply(&mut command)`. The re-run
      over the same 106 commits confirms the impact is confined to occurrences:
      NC-007 falls from 16 to 11 and EN-001 rises from 48 to 53, while verdicts
      (86 compliant, 16 noncompliant, 4 indeterminate), finding totals (90) and
      the 20-repository would-fail union are all unchanged. Only one repository
      reaches a different rule path, and its verdict does not change. The PR
      body's occurrence sentence was recomputed from the new ledger, with the
      reason for the movement stated.
- [x] (2026-10-27) Twentieth CodeRabbit round: two findings, both documentation
      accuracy, both verified against the code before being fixed. The users'
      guide listed a compliant result as a separate reason for exit `0`, which
      reads as an exception a compliant-with-findings result would slip
      through. `exit_code` consults only `blocking`, `blocking` consults only
      severity, and `_overall_verdict` returns `compliant` only for an empty
      finding set, so the rule is stated as severity-only with that fact named.
      The plan's survey tallies still carried the pre-round-19 occurrence
      counts while a later record carried the updated ones; both now read 11
      NC-007, 26 NC-008 and 53 EN-001. The round's gate sequence is green on
      the same six known `makeutil` failures. One `test_clippy_exception` case
      failed once and then passed three times in a row, alone and as a module;
      it shells out to the drifting `makeutil` binary with the host loaded, so
      it is a transient rather than a regression.
- [x] (2026-10-28) Twenty-first CodeRabbit round: one finding, already true of
      the users' guide and re-raised at plan level. `EP-EXIT-1` quantified over
      "every result" without excluding the case where no result is produced, so
      it read as if `exit_code == 2` violated the obligation. The plan now
      scopes the obligation to a successfully evaluated `RuleRunResult` and
      names exit `2` as caller-assigned. The same round's gate sequence is
      green on the same six known `makeutil` failures, and the PR body carries
      the round note.
- [x] (2026-10-28) CodeScene `Code Health Review (main)` fails on this PR and is
      recorded as an advisory exception rather than a regression. It is a
      whole-PR gate, not a per-commit delta: it reports 12 new files below code
      health 10.00, led by `nested_cargo_entry_points.py` (7.22) and
      `nested_cargo_commands.py` (7.74), and names the same set on every commit
      checked, from `af25553` through `95e4e50` — all twelve commits in the
      branch's recent history. No documentation commit changes it, and `main`
      carries no equivalent check, so there is no baseline it regressed against.
      `main` is also unprotected, so the gate is advisory and blocks nothing.
      Refactoring those readers to satisfy it is a separate change.
- [x] (2026-10-30) Codex review round: two correctness findings, each
      reproduced by a failing test before its fix. (1) Command state was merged
      across runs, so `cmd.arg("build"); cmd.status(); cmd.arg("--frozen");
      cmd.status();` reported only the protected run. The reader now snapshots
      its ledger at each `.status()`, `.output()` and `.spawn()` and reports one
      fact per distinct state. `absorb` carries helper evidence into every
      snapshot, a handoff is applied at its source position so a run before
      `configure(&mut cmd)` is judged on what it carried, and `_absorbed`
      requires every read to be offline. Two new `CASES` rows pin a flag added
      between runs and one revoked between runs. (2) A fetch inside a shell
      branch was credited as a prefetch: `if false; then cargo fetch --locked;
      fi` proved nothing, but only connectors had been inspected. Each script
      command now carries the construct path of its enclosing `if`, `while`,
      `until`, `for`, `case` and `select` bodies. A prefetch counts only when
      its construct path encloses the test command's, and a fetch in a sibling
      branch makes the step `uncertain` rather than `before`; a conditional
      fetch step does not protect a later step, which stays `absent`. Three
      entry-point tests pin the outcomes, including the same-branch control
      that still reads `before`.
- [x] (2026-10-30) The `make test` OPA flake is now explained rather than
      retried. The tester gives each Rego test rule a hard five-second deadline
      and exposes no flag to change it (`--timeout` is not a `conftest verify`
      option; OPA 1.15.2, Conftest 0.68.2). A rule that evaluates in
      milliseconds crosses that deadline when the process is starved of CPU, so
      `eval_cancel_error: context deadline exceeded` appears while six xdist
      workers and another agent's build saturate the host. Pinning the suite
      with an `xdist_group` marker narrowed the fan-out but did not stop the
      failure, because the other workers still competed. `make test` now runs
      the policy module in a second, serial invocation after the parallel run,
      so the process has the machine to itself. The continuous-integration
      workflow already passes `pytest-workers: ''`, so this changes nothing
      there.
- [x] (2026-10-30) The six `makeutil` fixture-drift failures are a host pin
      mismatch, not a regression. The checked-in envelopes record `makeutil`
      0.1.0 and the host binary reports 0.1.2. Installing the pinned 0.1.0 into
      `.makeutil-pin` and prefixing `PATH` with it for `make test` turns all six
      green, so the suite runs green in full rather than with an accepted
      exception. The pin directory is scratch and is not committed.

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
- Observation: Rust resolves a `#[path]`-reached file's children **beside** it,
  not under a stem directory. Evidence: a probe package whose `tests/it.rs`
  declares `#[path = "support/probe.rs"] mod probe;` compiles with
  `tests/support/helpers.rs` and fails with E0583 when the child is at
  `tests/support/probe/helpers.rs`. Impact: module resolution needs to know
  *how* a file was reached, not just where it is, so each file carries a
  `Reach` of `by_name` and `by_path` and offers every base its reach permits.
  An ordinary non-root library file still owns its stem directory, and a crate
  root resolves against its own directory.
- Observation: following `mod` declarations in files no compiler builds invents
  findings. Evidence: the first fixed-point reader raised the estate survey
  from 90 findings to 111. Both new ones traced to text that is never compiled
  — axinite's `tests/support/e2e.rs` (no `tests/support.rs` or
  `tests/support/mod.rs` declares it) and wildside's
  `backend/tests/support/entrypoint.rs`, whose declaration sits in a
  `macro_rules!` body that is `include!`d. Impact: the scan set is restricted
  to ordinary sources plus whole-file sources that are Cargo targets. The
  survey then matched the previous reader exactly, 90 findings with zero
  per-repository differences, and a probe confirms a spawn in an unbuilt
  `tests/ui/foo.rs` is still read while its `mod helpers;` reports nothing.
- Observation: `pylint <dir>` only recurses where a package exists. Evidence:
  a probe module planted in `tests/unit/` produced no message from
  `pylint tests`, while `pylint tests/unit` reported it immediately; the
  directory has no `__init__.py`. Impact: `make lint` never sees
  `tests/unit/test_nested_cargo_spawns.py`, so its length and its `== []`
  assertions are not gate failures. The pre-existing 1258-line
  `test_nested_cargo_entry_points.py` confirms the same boundary.
- Observation: replaying recorded edits from the session transcript restores
  lost work but not reliably. Evidence: after a `git checkout` discarded two
  unstaged modules, replaying every recorded `Edit` rebuilt them, but the
  replay applied edits in transcript order without regard to which ones later
  text had superseded, so it reintroduced an inline loop that a later edit had
  extracted away and left one stale `_is_crate_root` reference. Impact: the
  replay is a recovery aid, not an oracle. The recovered tree was accepted only
  after the full test suite passed *and* the estate survey reproduced the
  baseline exactly, which is the check that would have caught any semantic
  drift.
- Observation: a recovered tree can pass every test it was written with and
  still be untested at the seam the recovery put back. Evidence: the
  fixed-point code was recovered, then extended, and all 84 spawn-reader tests
  passed with `Reach.merged` silently reduced to its second argument. Only a
  seeded mutation found that; no test had ever reached a file both ways.
  Impact: a mutation is the only thing that distinguishes "the suite covers
  this code" from "the suite exercises this code". The both-ways test now
  carries it, and the merge's two flags are each held by a case that fails when
  that flag is dropped.
- Observation: Skylos resolves a method call through the receiver expression,
  not through the receiver's type, so a method called on the result of a
  `dict.get` gets no reference at all. Evidence: `before = reaches.get(...)`
  followed by `before.merged(observed)` is recorded as
  `concordat.rules.nested_cargo_modules.reaches.get.merged` with
  `references: 0`, while `Reach.merged` itself is reported `SKY-U001`. The
  repository already carries the same shape as a recorded entry point for
  `_RuleTally.severity`, called as `tally.severity` from `_rule_rows`. Impact:
  the call was rewritten in a probe to an explicit `None` check — the shape the
  router's own guidance prefers — and the finding did not change, so no
  receiver rewrite clears it. The method is reached only through that one
  expression, so a typed entry point for the one symbol is the narrow honest
  record, and the contract test carries it alongside the `_RuleTally` one.
- Observation: the ninth round's rename fix and the eleventh round's alias fix
  are the same question asked at two different layers, and only the first was
  covered. The ninth round taught the reader that a dependency *key* is not the
  package it names, so `tb = { package = "trybuild" }` counts as trybuild. That
  fix compared the resolved package fine, but the alias it then matched against
  source was still the key as written, and Cargo renames a key's hyphens to
  underscores for the Rust-visible name. Evidence: a `my-trybuild` rename with a
  `my_trybuild::TestCases` call produced the manifest fact and no source fact.
  Impact: resolving a manifest entry to its package and resolving that entry to
  its *source spelling* are separate transformations, and a rename fix that
  stops at the first one leaves the second untested.
- Observation: a scoping fix can quietly outrun the prose that describes it.
  The tenth round narrowed `CARGO_NET_OFFLINE` to the values Cargo's boolean
  parser accepts and recorded the measurement in a code comment, but five
  documents still described the old single value. Evidence: the README, users'
  guide, developers' guide, design document and this plan's glossary each said
  `"true"` alone, while `_OFFLINE_VALUES` held `{"1", "true"}`. Impact: when a
  round changes a *value set* rather than a mechanism, the prose is part of the
  change surface, and `grep` for the constant is the check that catches it.
- Observation: "contains `always()`" is not the same predicate as "can run
  after a failure", and the difference needs three values, not two. Evidence:
  `success() && runner.os == 'Linux'` gates nothing in the sense the old
  substring test looked for — it names no abandonment marker — yet it does wait
  for earlier steps, while `success() || github.ref == …` names no marker
  either and does not. A marker test can only answer "mentions a status
  function", and both conditions do. Impact: any reader of a GitHub `if:` has
  to evaluate the expression, and the honest verdict for an operand it cannot
  see is `None` rather than either boolean. The parametrized test table was
  itself wrong on the first pass for exactly this reason: I asserted `absent`
  for four conditions the evaluator correctly called `uncertain`, so the table
  had to be corrected rather than the code.
- Observation: `Path.is_file()` is a containment hole wherever it precedes a
  read of a policy input. Evidence: a `Makefile` and a `.config/nextest.toml`
  symlinked to files outside the checkout were both read without error and
  produced facts attributed to the repository under audit, while
  `_read_workflow` — which goes through `within_checkout` — refused the same
  trick. Impact: the containment contract is per-input, not per-envelope, and a
  reader added after the workflows were guarded can silently miss it.
- Observation: a review's stated consequence can fail to reproduce while the
  defect underneath it is real. Evidence: the thirteenth round's finding 1 said
  `github.event.name == 'x || always()'` returns an ungated answer because the
  literal's `||` ends the unknown operand early. Probing the pre-fix module gave
  `None` for that exact string — over-conservative, not a false `absent` —
  while `'a)b'` and `'a || b'` did derail the parser in the ways the finding
  described. Impact: the fix was made for the reason the probes demonstrated,
  and the difference is recorded here rather than the review's wording being
  restated as fact.
- Observation: a case-insensitivity bug is invisible to a test table written
  in one case. Evidence: the twenty-five-case evaluator table, the behavioural
  `if:` table and three standalone tests all spelled every status function in
  lower case, so `Always()` returning `True` (gated) survived thirteen review
  rounds and a full green gate. Impact: when a syntax is matched against a
  fixed vocabulary, at least one row has to spell that vocabulary differently
  from its documentation.
- Observation: a Rego policy can lose a finding without failing. Evidence: the
  `uncertain` entry-point finding read `entry.name` directly, so the test that
  removed the name did not produce a malformed-fact finding — it produced *no*
  finding, because the missing key left the rule body undefined. A second probe
  showed the ordinary NC-007 clause still firing on a fact whose `line` was the
  string `"80"`, printing `%!d(string=80)` where a location belongs. Impact:
  the same undefined-semantics trap the state check guarded against sits one
  level down, in every field a finding reads, and the fix has to cover both the
  drop and the duplicate.
- Observation: a documented contract can be wrong in a way no test notices.
  Evidence: `EP-EXIT-1` and the Decision log both said `exit_code` depends on
  the verdict as well as on severity; `RuleRunResult.blocking` has always
  ignored the verdict entirely. Impact: the plan is the artefact a later reader
  trusts, so a claim about behaviour needs checking against the code that
  implements it, not only against the tests that pass.
- Observation: a "sticky uncertainty" flag cannot substitute for a ledger.
  Evidence: the reader carried `is_uncertain`, set by an unreadable value and
  never cleared, intended to stop a later visible `"0"` from claiming the
  command was read. The round-15 case
  `env-offline-unreadable-then-literal-online` expected `online` and got
  `unresolved`, because the flag outlived the assignment that answered the
  question. Probing `_environment_pairs('name, "0"')` showed it already returns
  `[(None, "0")]`, so the flag added nothing and the field was deleted. Impact:
  the honest representation is one entry per operation — definite offline,
  definite online, or unknown — with the last one winning. A flag that only
  accumulates cannot express "this later operation replaced that one".
- Observation: the rule's own scope promise can be broken by a fallback added
  for a different reason. Evidence: the round-14 cross-file helper fallback
  reported any helper with no call site in its file, including one defined in
  `src/builder.rs`. That is an unsuppressable EN-001 on library code the README
  says the rule does not judge. Impact: a new "report it rather than drop it"
  branch needs the same scope test as the findings it feeds; the guard is
  `scope.contains(definition.start)`, and it is the definition's location, not
  the call site's, that decides.
- Observation: an exemption test can pass without comparing the field it
  names, because the repository comparison rejects the entry first. Evidence:
  two of the three exemption tests used the `named_elsewhere` fixture, whose
  repository is `leynos/other`, and paired it with an exemption naming
  `leynos/example`. Deleting both the rationale and the review-trigger checks
  from `is_exempt` left all 38 tests passing. The third test compared only rule
  IDs, and two of `exempt_spawn`'s three findings are NC-007, so a match that
  ignored the path still satisfied it; deleting `exemption.path == path` failed
  only the pre-existing named-exemption test. Impact: a fixture and the
  exemption under test have to agree on every field that is not the subject of
  the test, and the assertion has to name the finding's path as well as its
  rule ID. Re-run against the `exempt_spawn` fixture, the same two mutants fail
  exactly the tests written for them.
- Observation: a helper can reject trailing text while the function that
  replaces it forgets to. Evidence: `_unwrap` returns its input unchanged when
  anything follows the closing bracket, and its docstring says so; that is
  exactly why `_top_level_groups` had to make the same check, and it did not.
  `_environment_pairs('[("CARGO_NET_OFFLINE", "true")].into_iter().chain(extra)')`
  returned `[('CARGO_NET_OFFLINE', 'true')]`, so `command_state` answered
  `offline` for a list whose appended pairs run afterwards. Impact: two
  functions reading the same bracket shape need the same rejection rule, and
  the second one is the one that decides whether a *list* is readable at all.
- Observation: a block-aware scan can still read a conditional call as
  unconditional. Evidence: `collect_variable` bounds its scan to the binding's
  own block, which stops it reading past a rebinding, but a hit inside a nested
  `if`, `match` or closure is still inside those bounds.
  `if frozen { cmd.arg("--frozen"); }` therefore recorded the flag and the
  spawn read `offline` although the flag exists on one path of two. Impact:
  stopping the scan at the right *place* is not the same as reading the right
  *shape*; the block a hit sits in has to be compared with the binding's, and a
  hit that differs is evidence of unreadability rather than evidence of a flag.
- Observation: the existing word and ledger machinery already had the right
  way to say "a call the reader cannot see". Evidence: an unreadable `.args`
  argument already appends a `None` word, and an unreadable environment
  operation already records `offline=None`; both make `command_state` answer
  `unresolved`. The first design added a `has_unseen_call` flag and a
  `mark_unseen` method, and only one of the five new tests failed against it,
  because a conditional environment call left an earlier `"true"` standing.
  Impact: reusing the two existing representations fixed the behaviour without
  a new state field, and the five-case table — not the first test that failed —
  decided the design.
- Observation: a `# noqa` can be a claim about a checker that was never
  looking. Evidence: the first version of `_record_unseen` called
  `command._record_offline_operation` from outside the class and carried
  `# noqa: SLF001`, but Ruff's `select` list has no `SLF` family and Pylint
  re-enables a named list with no protected-access rule, so the suppression
  guarded nothing. Impact: check what the gates actually enable before
  suppressing, and prefer a public seam when the call crosses a class boundary —
  `mark_arguments_unknown` and `mark_environment_unknown` say what is being
  recorded as well as allowing it.

## Decision log

- Decision: warning-severity findings are advisory and no longer block a
  gate; `exit_code` is `0` when every finding is `warning`, otherwise `1`. The
  verdict is not consulted, so an all-warning `noncompliant` result exits `0`
  while still recording `noncompliant`. Rationale: severity is the documented
  enforcement dial (`docs/concordat-design.md:357`), RT-025 is the only warning
  producer, and the new package must ship as a survey-mode warning. Verdict and
  JSON keep their meaning, so evidence is preserved while enforcement changes.
  Date/Author: 2026-10-25, Claude Code on behalf of leynos.
- Decision: the Rust reader reports `unresolved` for anything whose offline
  evidence it cannot prove. Rationale: a false `online` finding would push a
  reading of "fix the test" onto code that is already safe; `indeterminate`
  preserves the question without asserting it. Date/Author: 2026-10-25.
- Decision: a repeated `CARGO_NET_OFFLINE` assignment is last-write-wins, and
  `--frozen` is irrevocable. Rationale: `Command::env` keeps the value given
  last for a name and `envs` applies its pairs in order, so treating any earlier
  `"true"` as proof would report `offline` for a command cargo runs online;
  the reverse mistake is impossible for `--frozen`, because no argument and no
  environment variable turns it off. The reader tracks the latest assignment
  explicitly, including a call site's over the helper's, and a last assignment
  it cannot see is `unresolved` rather than the earlier pair speaking for it.
  Date/Author: 2026-10-27, Claude Code on behalf of leynos.
- Decision: a cargo-starting helper with no call site in its own file is
  reported at its definition as `unresolved`, not dropped and not reported
  `online`. Rationale: the helper may be called from another file, whose
  arguments and environment the reader cannot see, and a call site proven
  offline does not carry the helper's other call sites with it; `online` would
  assert a defect on a guess, `unresolved` keeps the construction in the audit
  without asserting one. This also removes the cross-file helper from the
  design document's list of remaining limits. Date/Author: 2026-10-27, Claude
  Code on behalf of leynos.
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
- Decision: errexit is recognized from the letter `e` in a short option *and*
  from a short option ending in `o` followed by the word `errexit`. Rationale:
  `-o errexit` is the long spelling of `-e` and stops the script, so a template
  written that way is fail-fast and a fetch-first block under it gates the
  test. Only `-` words are considered because `+o errexit` unsets the option,
  `-O errexit` is rejected by bash, and `--errexit` is not an option at all;
  all three were checked against the installed bash rather than assumed.
  Date/Author: 2026-10-27.
- Decision: every fact field an indeterminate message quotes is read through
  `object.get` with a default. Rationale: the earlier fail-closed fix covered a
  missing `state`, but the message itself still read `entry.reason`,
  `spawn.flags` and `spawn.environment` directly, so a fact missing one of
  those left the rule body undefined and dropped the EN-001 it had just been
  made to report. Fail-closed has to reach the whole message, not only the key
  that selects the rule. Date/Author: 2026-10-27.
- Decision: module resolution takes a record of how a file was reached rather
  than deriving a single base directory from its path. Rationale: the compiler
  uses three different bases for the same-looking file. A crate root resolves
  against its own directory; a file named by `#[path]` resolves against its
  parent, not a stem directory; an ordinary library file resolves against the
  stem directory. A file may be more than one at once — `tests/it.rs` is a
  target and can also be another test's `#[path]` target — and every reading is
  compiled, so the resolver offers each permitted base and keeps the one that
  exists on disk. Date/Author: 2026-10-27.
- Decision: the discovery loop rescans a file whenever its scope or reach
  changes, and reports only the last scan's problems. Rationale: a file may be
  read before the declaration that promotes it to test scope, or before the
  `#[path]` that changes where its children live. Reporting each pass's
  problems produced a spurious "names no file" for `tests/support/probe.rs`,
  because the early pass had not yet learned its reach. A fixed point plus
  per-file problem replacement reports what the settled state says and nothing
  else. Date/Author: 2026-10-27.
- Decision: only sources the compiler builds are scanned for `mod`
  declarations; a whole-file test source is included only when it is a Cargo
  target. Rationale: a `<name>.rs` under `tests/` that no manifest names is
  fixture text. Following its `mod` lines reported declarations that no
  compiler makes — 21 false findings across the estate, every one traced to
  such a file. The file's spawns are still read, because the rule's test-scope
  selector names the directory; only its declarations are ignored. Date/Author:
  2026-10-27.
- Decision: `#[path]` on an inline module is not implemented. Rationale: it
  does set the children's base directory, verified by compiling netsuke's
  `#[path = "../../src/cli"] mod cli { ... }` shape, but the estate has three
  occurrences, all in netsuke and none in test scope. The reader treats those
  children as inline and reports nothing rather than resolving a directory it
  does not model. The README states the limitation so a future occurrence is a
  known gap rather than a silent wrong answer. Date/Author: 2026-10-27.
- Decision: `Reach.merged` is recorded as a typed method entry point rather
  than rewritten to avoid the false positive. Rationale: the method is live — a
  mutation of its union fails a test — so deleting it or inlining it would be
  shaping production code around a scanner's limitation, which the router
  forbids. Rewriting the receiver does not help either; a probe shows an
  explicit `None` check before the call leaves the finding unchanged. The
  record names the call site it hides behind (`reaches.get.merged`) and the
  mutation that proves the method runs, which is what the repository already
  asks of its other two Skylos entry points. The alternative — a bare `merged`
  whitelist entry — would have suppressed any future `merged` method in the
  package; the control run confirms this one suppresses exactly the one symbol
  while a planted dead sibling is still reported. Date/Author: 2026-10-27.
- Decision: the trybuild alias set keeps the manifest spelling and normalizes
  hyphens at pattern-build time, in `_test_literal`, rather than normalizing
  when `_aliases_by_directory` records the key. Rationale: the hyphen rule
  belongs to how Rust names a crate in source, which is exactly what
  `_test_literal` is building a pattern for, and a reader of that function
  needs the rule there. Normalizing at collection would spread the
  transformation across two functions and leave the stored names looking like
  Rust paths when they are manifest keys. The set stays keyed by the manifest
  spelling so it can still be compared against dependency entries. Date/Author:
  2026-10-27.
- Decision: the test step's `if:` is judged by evaluating the condition with
  `success()` held false, not by matching status-function names. Rationale:
  GitHub's implicit `success()` guard applies only when the condition names no
  status function, so the names alone say nothing about whether the step waits
  for a failed fetch; `success() && X` waits and `success() || X` does not.
  Three-valued evaluation answers the actual question, and returns `None` where
  the condition depends on `github.ref` or `inputs.*`, which the rule reports as
  `uncertain` rather than guessing. The fetch step keeps the stricter "runs on
  every run" test in `condition_proves_order`, because a fetch that sometimes
  skips warms nothing on the runs it skips; the two steps ask different
  questions of the same syntax, so one predicate cannot serve both.
  Date/Author: 2026-10-27.
- Decision: the `if:`-condition evaluator lives in its own module,
  `concordat/rules/nested_cargo_conditions.py`, rather than inside the
  entry-point reader. Rationale: pylint's 800-line module limit
  (`pyproject.toml:164`) flagged the reader at 989 lines, but the split earns
  its place on its own terms — the evaluator is a self-contained expression
  parser with its own vocabulary and no reader state, and both files are easier
  to follow apart. Only `condition_proves_order` and `runs_only_after_success`
  cross the boundary; everything else is private to the evaluator. The
  extraction is covered directly by
  `tests/unit/test_nested_cargo_conditions.py`, so the moved code is tested
  where it now lives rather than only through the reader. Date/Author:
  2026-10-27.
- Decision: status-function names are matched case-insensitively, and the
  fetched step's "exactly `success()`" test is a `re.IGNORECASE` pattern rather
  than a comparison against the literal string. Rationale: GitHub's expression
  parser lowercases the identifier before looking it up, so `Always()` and
  `success()` are the same function to the runner; a case-sensitive reader
  reports a step that runs after a failed fetch as gated, which is the one
  direction this rule must never be wrong in. A second pattern,
  `_SUCCESS_ONLY`, keeps the fetch step's stricter question honest: reusing
  `_STATUS_ATOM` there would need a group comparison anyway, and it would match
  `failure()` and `always()`, which are precisely the conditions a fetch step
  may not carry. Date/Author: 2026-10-27.
- Decision: string literals are tracked while parsing, and an unclosed literal
  makes the condition unreadable (`None`) rather than being read to the end of
  the expression. Rationale: `'...'` is data, so a `)` or `||` inside one must
  not end an operand, and a status function's name inside one must not count as
  a call — `github.event.name == 'always()'` compares text and leaves GitHub's
  implicit guard in place, which is the opposite verdict from treating it as a
  call. An unclosed literal is an expression GitHub itself would refuse to
  evaluate, so no reading of it describes a run that could happen; reporting
  `uncertain` is the honest answer. Date/Author: 2026-10-27.
- Decision: the report renderer's version test uses fixture versions far from
  any plausible manifest version, and its negative assertion names the
  fallback's full spelling instead of searching the report for the manifest
  version. Rationale: the test's subject is which versions the header names,
  not which versions the package might one day declare. Searching for the bare
  manifest version couples the test to a number that changes for unrelated
  reasons and would fail the day a fixture matched it. Date/Author: 2026-10-27.
- Decision: the offline question is tracked as a ledger of *operations* rather
  than of assignments, with `env_remove` and `env_clear` recorded as definite
  "online" and an unreadable operation recorded as "unknown". Rationale:
  `Command::env_remove` removes a name and `Command::env_clear` removes the
  lot, so both undo an earlier `env` assignment instead of making a new one;
  reading only assignments let
  `.env("CARGO_NET_OFFLINE", "true").env_remove(..)` come out `offline`. An
  unreadable `envs(..)` argument may itself name the variable or set it back,
  and it runs after everything before it, so it has to replace the previous
  answer rather than be skipped. Date/Author: 2026-10-27, Claude Code on behalf
  of leynos.
- Decision: the sticky `is_uncertain` field is deleted rather than kept
  alongside the ledger. Rationale: the ledger's `None` already means "the last
  operation was unreadable", and a flag that never clears prevents a later
  visible assignment from answering the question the flag was protecting.
  `_environment_pairs` already reports an unreadable name as `(None, value)`
  and an unreadable value as `(name, None)`, so the field was redundant as well
  as wrong. Date/Author: 2026-10-27, Claude Code on behalf of leynos.
- Decision: the unreached-helper fallback applies only to a helper whose
  *definition* is inside test scope. Rationale: the fallback exists so a
  test-scope construction never drops out of the audit, but a helper in library
  code is outside the reader's scope by the rule's own promise, and its call
  sites are visible where they are made. Reporting it would put an
  unsuppressable EN-001 on a file the rule says it leaves alone. Date/Author:
  2026-10-27, Claude Code on behalf of leynos.
- Decision: the three exemption tests move from the `named_elsewhere` fixture
  to `exempt_spawn` and assert `[rule_id, path]` pairs rather than rule IDs.
  Rationale: an exemption test only exercises the field it varies if the
  fixture's repository and path already agree with the exemption's. With
  `named_elsewhere` (repository `leynos/other`) and an exemption naming
  `leynos/example`, the repository comparison rejected the entry first, so the
  blank rationale and blank review trigger were never reached; mutation of
  `is_exempt` left all 38 tests green. The path component in the assertion is
  needed because two of the fixture's three findings share rule NC-007.
  Date/Author: 2026-10-27, Claude Code on behalf of leynos.
- Decision: `_top_level_groups` treats an `envs` list followed by any
  non-whitespace as unreadable, rather than reading the literal list and
  ignoring the rest. Rationale: the trailing expression is evaluated by Rust
  and its pairs are applied after the literal ones, so a visible `"true"`
  followed by `.chain(extra)` does not establish what cargo reads. This is the
  same rejection `_unwrap` already applies to a bracketed group, and it keeps
  the module's promise never to report `offline` on a guess. Trailing
  whitespace is still accepted, because rustfmt leaves the closing bracket at
  the end of a line. Date/Author: 2026-10-27, Claude Code on behalf of leynos.
- Decision: a call on a bound command whose enclosing block differs from the
  binding's is recorded as an unseen operation, not as evidence. Rationale: the
  call runs on only some paths, so `if frozen { cmd.arg("--frozen"); }` would
  otherwise make the spawn `offline` on a guess — the exact class of false
  negative the rule exists to avoid. An argument method appends a `None` word
  and an environment method or removal records `offline=None` in the ledger,
  reusing the two representations an unreadable `.args` and an unreadable
  `.envs` already use, so `command_state` needs no change. `--frozen` remains
  irrevocable, so an unconditional flag recorded before the conditional call
  still proves offline mode. Date/Author: 2026-10-27, Claude Code on behalf of
  leynos.
- Decision: `CargoCommand` exposes `mark_arguments_unknown` and
  `mark_environment_unknown` rather than letting `_record_unseen` reach its
  private `_record_offline_operation`. Rationale: the call crosses a class
  boundary from a module-level helper, and a named method states which fact is
  being recorded. The `# noqa: SLF001` that first guarded it was removed
  because no enabled Ruff or Pylint rule reports protected access; the seam is
  about clarity, not about silencing a gate. Date/Author: 2026-10-27, Claude
  Code on behalf of leynos.

## Outcomes & retrospective

The four planned tasks are implemented and validated. The runner no longer
fails a gate on a warning finding; the new package reports every test-scope
cargo spawn and every test entry point a checkout has *that the reader can
detect*, and ships as a survey. What the reader cannot detect — a construction
behind a macro, a `fn` pointer or another crate — is stated as a limit rather
than counted as compliant, and an entry point is reported only where the
checkout has a spawn or trybuild fact for its prefetch to protect. The real
runner over 106 mirrored repositories finds 16 repositories with an unsafe
nested cargo run or a cold test entry point and a further 4 where the reader
could not prove either way, which is the evidence a promotion decision needs
and is more than the checked-in fixtures could have shown. The review round
moved the sensitive numbers a long way — 37 indeterminate repositories became 4
— which is itself the lesson that a permissive indeterminate branch inflates
exactly the count a promotion decision is made on.

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

The survey was re-run once more after the last four review fixes, and the
figures held: the rule path is element-wise identical to the run the PR body
quotes, so 90 findings and 20 would-fail repositories are unchanged. That is
worth recording because it distinguishes two kinds of review finding. A fix
that changes a verdict on real checkouts is a measurement correction and
invalidates the published counts; a fix that needs a construct the corpus does
not contain is a latent defect that the fixtures — not the survey — have to
guard. Four of the six reader defects found after the first survey were of the
second kind, which is the argument for keeping both the fixture corpus and the
estate sweep rather than treating either as sufficient.

The tenth review round produced a third kind, and it is the one worth the most
attention: a **counting** defect in the written report rather than in the rule.
The EN-001 row claimed 4 indeterminate-only repositories where the survey data
gives 19, and the pull-request body carried the same error with a four-name
list. Nothing in the rule was wrong; the prose was. Two things made it survive
a full review cycle. The estate-level union — 20 would-fail repositories — was
correct throughout: there are only 16 noncompliant repositories in total, so at
most 16 of the 19 EN-001 repositories can sit inside other rules' noncompliant
sets, and at least three of them must be repositories whose overall verdict is
indeterminate. Either way they fall inside the 16-plus-4 union, so every
cross-check at the repository level agreed. And the four names listed were the
repositories whose *overall* verdict was indeterminate, which is a different
question from the rule-level count the table's column names. A rule-level table
needs to be recomputed from the findings, not carried from the verdict summary;
the lesson is to derive every cell from `rule-results.jsonl` at write time and
to reconcile it against the independent report renderer, which computes the
same table and would have disagreed.

The same round's two code findings are a different lesson. Both were latent: no
corpus repository has an unreadable argument before `--frozen`, and the module
hops the fixed point adds carry no spawn on the estate. So a survey that
reports "90 findings, unchanged" cannot distinguish a correct fix from a
missing one, and the fixture tests are what carry the guarantee. The
matched-pair argument case and the compiler probes exist precisely because the
corpus is silent here.

Delivery then produced the sharpest lesson of the plan, about the tests rather
than the rule. A mutation that reduced `Reach.merged` to its second argument —
discarding the first entirely — left all 84 spawn-reader tests green. Every
test that reached a file at all reached it one way, so the union had never been
exercised; the corpus cannot catch it either, because no surveyed repository
reaches one file by both a plain `mod` and a `#[path]`. The suite was green and
the code was wrong, and only a seeded fault told them apart. This is why the
plan's verification obligations ask for a negative control beside each
guarantee: a passing suite is evidence that the code runs, not that it is
right, and the two are easy to confuse when the fixture corpus is quiet.

The eleventh round adds a smaller one about transformations. The ninth round
established that a dependency key is not the package it names; the eleventh
found that a package name is not the spelling source uses either, because Cargo
maps a key's hyphens to underscores. One rename therefore passes through two
distinct transformations — entry to package, package to source name — and the
first round that fixed renaming covered only the first. The general form is
that a value which is rewritten on its way to a comparison needs a test at each
hop, not one at the end.

The twelfth round is the clearest case yet for writing the test's claim down
before writing the test. Four of my own new `if:` expectations asserted
`absent` for conditions the evaluator correctly called `uncertain`, and the
test failed on its first green run — not because the code was wrong, but
because I had guessed at the semantics twice: once when I first misread
GitHub's implicit-`success()` rule, and again when I assumed every compound
condition would land in the same bucket. Probing the evaluator directly with
each expression settled it in a minute and would have settled it before the
test existed. The rule for a truth table over someone else's language is to
derive each row from a probe, not from what the rows ought to be.

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
`CARGO_NET_OFFLINE` set to `"true"` or `"1"`. An *entry point* is a Make
target, a nextest setup-script, or a workflow step that ramps a test command.

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

Obligation `EP-EXIT-1`: for every successfully evaluated `RuleRunResult`,
`exit_code == 0` iff every finding carries severity `warning` — equivalently,
iff `blocking` is false; otherwise `1`. The verdict plays no part: an
all-warning noncompliant result exits `0` and still records `noncompliant`,
while an error-severity finding exits `1` whatever the verdict says.
Operational failure is outside this obligation: a run that never produces a
result exits `2`, and that code is assigned by the caller rather than by
`RuleRunResult.exit_code`.

- Method: parameterized unit tests over a finite partition.
- Rationale: the input space is a small product of verdicts and severity
  multisets, so enumeration is exhaustive for the contract.
- Domain: no findings; all-error; all-warning; mixed error+warning across
  noncompliant and indeterminate; a finding with an unknown severity label. The
  all-warning noncompliant cell and the no-findings cell are the two that
  distinguish verdict-sensitive from severity-only behaviour.
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
  `online`, proving the exclusion is exercised. The two argument-order cases
  are a matched pair that cannot both pass under a reader that ignores order:
  `args(extra).arg("--frozen")` must be `unresolved` and
  `arg("--frozen").args(extra)` must be `offline`. The first fails against the
  previous reader and the second already passed, so the pair also proves the
  fix is the ordering and not a blanket refusal to read flags after an unknown
  word. The hyphenated trybuild alias is a second matched pair: the rename
  `my-trybuild = { package = "trybuild" }` with a `my_trybuild::TestCases` call
  must yield both a manifest fact and a source fact, and it yields the source
  fact only when the key is normalized to Rust's spelling. It fails against the
  previous reader with the manifest fact alone, and the plain `trybuild` and
  `tb` aliases already passed, so it pins the hyphen hop rather than alias
  handling in general. The conditional-call cases are a third matched set. A
  conditional `.arg("--frozen")` and a conditional
  `.env("CARGO_NET_OFFLINE", "true")` must both be `unresolved`, while a
  conditional `.status()` — which changes neither the words nor the environment
  — must stay `online`, so the set cannot be satisfied by refusing to read
  every call in a nested block. Two further cases split by evidence form: a
  conditional argument call after an unconditional `--frozen` stays `offline`,
  because the flag is irrevocable, while a conditional environment call after
  an unconditional `"true"` is `unresolved`, because the variable is
  last-write-wins and the unseen call may run after it. Reverting
  `collect_variable` to unconditional fails exactly the three state-changing
  cases and leaves both controls green.

Obligation `EP-SCOPE-1`: a file named by a declaration in test scope is test
code; its own declarations are followed in turn, so scope closes transitively;
the base directory for a declaration is every base its file's reach permits —
its own directory for a crate root or a `#[path]` target, its stem directory
for an ordinary file — and no base outside the checkout is ever read.

- Method: unit tests over temporary checkouts, one per compiler rule, each
  named for the rule it pins; a compiler probe for each resolution claim; and
  the estate survey as the wide regression control.
- Artefact: `tests/unit/test_nested_cargo_spawns.py` (eight scope tests) and
  the probe packages under `/tmp/rustpath-*`.
- Evidence: green. The compiler probe compiles
  `#[path = "support/probe.rs"] mod probe;` with the child at
  `tests/support/helpers.rs` and rejects `tests/support/probe/helpers.rs` with
  E0583, so the `by_path` base is measured rather than assumed. The
  `tests/helpers.rs` crate-root case fails with "names no file" when
  `CRATE_ROOT_DIRECTORIES` is emptied, which is the seeded-fault control. The
  estate survey reports the same 90 findings, repository by repository, as the
  reader before this change.
- Non-vacuity: the late-promotion test
  (`src/lib.rs -> src/zebra.rs -> src/zebra/helpers.rs`) fails if the fixed
  point stops after one hop. The `#[path]`-reached test fails if children are
  resolved against a stem directory. The scanner-set test — a spawn in an
  unbuilt `tests/ui/foo.rs` is read, its `mod helpers;` is not — fails in both
  directions: dropping the whole file loses the spawn, and scanning its
  declarations invents a problem. The both-ways test (`mod checks;` plus
  `#[path = "checks.rs"] mod alias;` from one file) fails if `Reach.merged`
  replaces its flags instead of unioning them, which is the seeded fault that
  proved the merge live: with the union reduced to the last observation,
  `src/checks/inner.rs` loses its base and the test reports no spawn where it
  expects one.
- Residual gap: `#[path]` on an inline module changes its children's base
  directory, and the reader does not model that. Three estate occurrences
  exist, all outside test scope. This is a stated limitation, not a claimed
  guarantee; the README records it.

Obligation `EP-ENTRY-1`: `&&` and fail-fast sequencing prove prefetch order;
`;` does not; Make prerequisite and recipe order is read with the existing
loader; unresolved expansion, `.ONESHELL`, and recursive Make stay uncertain.

- Method: parameterized unit tests for shell lexing, Make parsing, nextest
  setup scripts, and workflow order, plus a replay over the whitaker and
  theoremc checkouts.
- Artefact: `tests/unit/test_nested_cargo_entry_points.py`.
- Evidence: 106 tests green. Whitaker **before** #518 yields three facts —
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

Obligation `EP-ENTRY-2`: a workflow test step is `before` only when an earlier
fetch is proved and the step's own `if:` cannot run after that fetch failed.

- Method: parameterized unit tests over a finite truth table of conditions,
  each row a distinct semantic case.
- Rationale: the condition grammar is open-ended, but the reader evaluates
  only the part that decides whether a step can outlive a failed fetch, and
  that part partitions into "always runs", "never runs", and "depends on an
  operand the reader cannot see". One witness per partition pins the mapping.
- Domain: no `if:`; a bare `success()`; a wrapped `${{ success() }}`; a
  non-status condition such as `runner.os == 'Linux'`; `failure()`; `always()`;
  `failure() || success()`; `success() || github.ref == …`;
  `cancelled() || success()`; `success() && runner.os == 'Linux'`.
- Artefact: `tests/unit/test_nested_cargo_entry_points.py` holds the
  behavioural rows; `tests/unit/test_nested_cargo_conditions.py` holds 25
  direct cases over the extracted predicate after the `C0302` split, including
  two for the fetch step's separate question.
- Evidence: the six-row table reports `absent` for `failure()`,
  `failure() || success()` and `github.ref == … || always()`; `uncertain` for
  `success() || github.ref == …` and `cancelled() || success()`; `before` for
  `success() && runner.os == 'Linux'`. The three standalone tests cover the
  unwrapped `success()`, the wrapped `${{ success() }}` and the non-status
  condition. The direct cases add the pairs that separate three-valued from
  two-valued logic: `success() && (failure() || github.ref == …)` is gated while
  `success() || github.ref == …` is not settled.
- Non-vacuity: every row would flip under a substring test for status-function
  names, and the `success() && …` row is the negative control that a blanket
  "names a status function, so not gated" rule would report `absent`. The
  by-hand probe of `_evaluated_after_failure` that fixed my first, wrong table
  is the evidence that the rows are independently reachable rather than all
  falling through one branch.

Obligation `EP-CONTAIN-1`: a `Makefile` or `.config/nextest.toml` that resolves
outside the checkout raises `OperationalRuleError`, which the CLI turns into
exit code 2, and one inside the checkout is still read.

- Method: unit tests over temporary checkouts, one per input, with a positive
  control.
- Artefact: `tests/unit/test_nested_cargo_entry_points.py`.
- Evidence: both symlink cases raise `OperationalRuleError` matching
  "outside the checkout"; the control still reports the `test` target.
- Non-vacuity: without the guard the two cases return facts silently, which is
  what the pre-fix probe showed. The control fails if the guard is replaced by
  an unconditional refusal.

Obligation `EP-ENVELOPE-1`: the envelope carries schema version, kind, the
origin slug or `null`, and every fact list from both readers, and never drops a
reader's problem.

- Method: unit tests over temporary checkouts.
- Artefact: `tests/unit/test_nested_cargo_offline_envelope.py` (7 tests).
- Evidence: green; an unparsable Makefile survives as a problem and produces
  no entry point, and supplied parameters change nothing.
- Non-vacuity: the four-list test fails if any reader's output is dropped, and
  the problem test fails if a reader swallows its failure.

Obligation `EP-POLICY-1`: the policy emits NC-007 for `online` spawns and
NC-008 for absent entry-point prefetch only when spawn or trybuild evidence
exists; every unresolved spawn and every recorded problem is EN-001
indeterminate; an uncertain entry point is EN-001 indeterminate only when
`has_nested_build` is true, because without a nested build the prefetch it
might have missed would warm nothing; a fact whose `state` is outside the known
vocabulary is an error-severity EN-001; and an envelope defect is an
error-severity EN-001.

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
- `make check-fmt`, `make lint`, `make typecheck`, `make markdownlint`, and
  `make nixie` pass. `make test` passes in full when the pinned `makeutil`
  0.1.0 leads `PATH`: the checked-in envelopes record 0.1.0, and the host
  binary reports 0.1.2, so the six fixture-regeneration tests fail on drift
  alone. They reproduce unchanged on `origin/main` in a scratch worktree, which
  is how the drift was told apart from a regression.

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
| EN-001 | 0                         | 19                              | 19         |
| NC-007 | 6                         | 0                               | 6          |
| NC-008 | 15                        | 0                               | 15         |

The three sets are not disjoint; their union is 20 repositories, and every one
of those is a repository whose overall verdict is noncompliant or
indeterminate. Occurrence counts are 11 NC-007, 26 NC-008 and 53 EN-001; the
nineteenth round's mutable-reference handoff moved two `ortho-config` spawns
from NC-007 to EN-001 without changing any repository-level count.

The 19 EN-001 indeterminate-only repositories are `axinite`, `chutoro`,
`ddlint`, `diesel-cte-ext`, `mdtablefix`, `monotony`, `netsuke`, `ortho-config`,
`pg-embed-setup-unpriv`, `podbot`, `rstest-bdd`, `shared-actions`, `stilyagi`,
`theoremc`, `thysalion`, `vtcode`, `weaver`, `whitaker` and `wireframe`.
`vtcode`'s two facts are reader problems rather than repository defects — a
`#[cfg(test)]` item never closes in two files — and the rest are Makefile or
workflow recipes the lexer cannot reduce to one command.

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
