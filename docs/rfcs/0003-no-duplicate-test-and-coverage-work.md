# RFC 0003: No duplicate test and coverage work in continuous integration

## Preamble

- **RFC number:** 0003
- **Status:** Proposed
- **Created:** 2026-09-16
- **Audit domain:** Quality-Gate Integrity (design document Section 3.1.1)
- **Check identifiers:** QG-005, QG-006, QG-007
- **Depends on:** the workflow fact envelope, a prerequisite recorded in issue
  #153

## 1. Summary

On any one trigger, a test-only lane must not run where a coverage lane already
runs the same test scope under the same nextest profile and the same resolved
feature set. The pre-flight of a publish dry run must not repeat the workspace
check and test that the same job has already run. Per-crate `cargo package` is
always kept, because it is a distinct gate class that no other lane can
substitute for.

This RFC proposes three rule packages in the quality-gate integrity family,
taking the identifiers immediately after QG-004:

- **QG-005** — a test-only lane duplicating a coverage lane on the same
  trigger.
- **QG-006** — two matrix legs whose feature sets resolve to the same set.
- **QG-007** — a publish dry run repeating checks the job has already run.

### 1.1 Why QG-005 to QG-007

QG-001 to QG-004 are the quality-gate integrity rules from the design document:
gate bindingness, pinned installs, rolling-release detection, and test-runner
completeness. Duplicate gate work is the same domain seen from the other side.
Where QG-004 asks whether a suite runs at all, these ask whether it runs twice.
The proposal behind issue #153 deliberately started its own quality-gate
identifiers at QG-010, leaving QG-005 to QG-009 clear in the design document's
band, and these three take the next three.

QG-005 to QG-007 collide with nothing in the catalogue or in issue #153.

### 1.2 Relationship to CI-017

CI-017, proposed in issue #153, notes that `cargo llvm-cov nextest` runs no
doctests, so a repository whose only Linux lane is coverage has examples nobody
compiles. It is the mirror of this RFC and its constraint on it: QG-005 removes
a test-only lane that a coverage lane covers, and CI-017 records one thing a
coverage lane does *not* cover. The two must be read together, and Section
3.2's profile clause is where they meet.

## 2. Motivation

Duplicate gate work is the most expensive defect class the Ubicloud campaign
measured, and the least likely to be noticed, because every lane is green.
Nothing fails, nothing is flaky, and the only symptom is a bill and a wait.

### 2.1 The measured cases

Table 1 records the evidence behind each rule.

#### Table 1: Findings behind the duplicate-work rules

| Repository          | Finding                                                                                                                              | Per-run saving                                                           | Rule   |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------ | ------ |
| axinite #372        | A matrix leg named all-features passed a feature list equal to the default set, so two paid legs ran one suite on every pull request | 1,028 s against 792 s for the surviving leg on the same run              | QG-006 |
| shared-actions #480 | `ci.yml`'s Ubuntu Python-tests leg duplicated the coverage job's suite on the same trigger, missed by the survey                     | 5m14                                                                     | QG-005 |
| rstest-bdd #776     | The publish dry run repeated the check and test pair the job had already run, across three platforms                                 | 706 s, split Ubicloud 246 s, Windows default 269 s, Windows strict 191 s | QG-007 |
| ortho-config #494   | The same duplication on two lanes                                                                                                    | 297 s, being 163 s on Linux and 134 s on Windows                         | QG-007 |

The axinite figure carries a detail worth stating: 1,028 s was the deleted
leg's cost against 792 s for the default leg *on the same run*, so cache warmth
and variance are controlled, and the surviving leg is unchanged at 783 s
afterwards. The saving is the whole of the deleted leg, not a difference
between two differently warmed runs.

### 2.2 Why the feature set must be resolved

The axinite defect is the one that argues for a resolver rather than a text
comparison. The duplicated leg was *named* all-features and passed an explicit
feature list. The list happened to equal Cargo's default feature set for that
workspace, so the two legs compiled and ran the same code under different
names. No text comparison of the two legs would have found it; the leg names
differ, the arguments differ, and only the resolved sets are equal.

Feature sets therefore resolve through Cargo's semantics, including default
features, before comparison. A rule that compared argument strings would pass
axinite verbatim.

### 2.3 Why the dry run survives

rstest-bdd #777 is the boundary case for QG-007 and the reason the rule is
written as a narrowing rather than a deletion. `cargo package` builds each
crate from its packaged sources in isolation. A symbol that is `pub` inside the
workspace but never re-exported from its crate root compiles under
`cargo test --workspace` and under Clippy, and fails only when a dependent
crate is packaged. `rstest_bdd_patterns::RwLockExt` was missing from the crate
root and reported as "no `RwLockExt` in the root" in two consumers, caught by
the dry run alone.

The publish dry run is therefore a distinct gate class: published visibility.
Any pruning keeps per-crate packaging. The pre-flight skip is safe precisely
because it removes the repeated check and test pair and keeps packaging.

### 2.4 What the skip keeps, stated exactly

A guide that claims the skip "keeps the working-tree guard" is wrong, and the
error was made once during the ortho-config adoption. The lockfile guard is
always kept. The working-tree guard is opt-in through `--forbid-dirty` and runs
only where the publish check flags set it. QG-007's remediation text states
both, because a body that says "no need to repeat tests" must also say what the
step still proves.

## 3. Rule statement

All three rules evaluate the workflow fact envelope, together with `Cargo.toml`
for feature resolution and the lading configuration for QG-007. Suite-command
matching is over whole shell words. A probe, such as a `--version` invocation,
is not a suite run. All three fail closed: a command the extractor cannot
tokenize yields `indeterminate`.

### 3.1 Counting: invocations per job

Every rule here counts invocations per job, not occurrences per file. The
shared-actions finding was one leg of one workflow duplicating one job of the
same workflow on the same trigger, and a file-level count would have reported
it as a single suite command appearing once.

### 3.2 QG-005: a duplicated test lane

**Sensor.** For each trigger, group the jobs that run a test suite. Two jobs
duplicate when all three of the following hold:

- **Same scope.** The resolved package selection is equal: the same workspace
  members, resolved through `--workspace`, `-p` and default-members.
- **Same profile.** The same nextest profile, resolved through `--profile` and
  the profile's own inheritance. A custom profile inherits the default
  profile's overrides, so two legs naming different profiles may still resolve
  to the same effective configuration.
- **Same resolved feature set.** Equal after resolution per Section 2.2.

When a coverage lane and a test-only lane duplicate, the finding names the
test-only lane as the removable one and the coverage lane as its cover.
Coverage is the superset: it runs the suite and produces the report.

**The profile is load-bearing in the remediation.** A coverage lane that
replaces a test lane must keep the profile the test lane used. Nextest's
default profile drops trybuild; `--profile ci` keeps it. Axinite's coverage
lanes keep the profile for exactly this reason, and a remediation that dropped
it would delete a suite while appearing to preserve one. The rule therefore
reports a finding, not a patch, when the two lanes differ in profile: that is
not a duplicate, it is two different suites.

**Exemptions.** A lane on a different trigger is not a duplicate; the rule
groups by trigger because a push lane and a pull-request lane serve different
purposes. A lane on a different platform is not a duplicate, because the
platform is part of the scope.

**Actuator.** None automatic. The finding names the duplicate leg and the
coverage lane that covers it. Removal is withheld because deciding which of two
lanes survives, and whether the survivor's profile must change first, is a
judgement about what the repository intends to gate.

### 3.3 QG-006: two legs, one resolved feature set

**Sensor.** Within one matrix, resolve each leg's feature set including Cargo's
default features. Two legs whose sets are equal are a finding, whatever their
names.

**Resolution requirements.** The resolver reads `default` from every manifest
in the selected packages, expands feature dependencies transitively, and applies
`--no-default-features` and `--all-features` as set operations rather than as
flags to be compared textually. Where a feature list is supplied through an
expression the envelope cannot resolve, the verdict is `indeterminate`.

**Actuator.** None automatic; the finding names both legs and the resolved set
they share.

### 3.4 QG-007: a dry run repeating the job's own work

**Sensor.** Where a job runs a publish dry run after a test suite, require that
the dry run's pre-flight be skipped. For lading, this is the `[preflight] skip`
key, the `--skip-preflight` flag, or the `LADING_SKIP_PREFLIGHT` environment
variable, and the rule reads the lading configuration as well as the workflow
text.

**The skip is set on the dry-run step only.** A repository-wide skip would
disable the pre-flight in a real publish, where nothing precedes it. The sensor
therefore requires the skip to be scoped to the step, and reports a
configuration-file-wide skip as its own finding class.

**The contract holds from both ends.** A consumer's contract must assert the
step command as tokens *and* that the recipe hands lading `publish`. Asserting
one end alone passes a step that names the flag and invokes something else, or
a recipe that publishes with the flag stripped. This is the estate rule that a
contract asserts the command rather than an identifier, applied to a two-part
invocation.

**What is never pruned.** Per-crate `cargo package` is kept unconditionally.
The rule reports a finding against any pruning that removes it, and the
remediation text names rstest-bdd #777's unexported symbol as the class of
defect the packaging step alone catches.

**What the skip keeps.** The lockfile guard is always kept. The working-tree
guard is opt-in through `--forbid-dirty` and runs only where the publish check
flags set it. The finding text states both, so no guide repeats the
ortho-config error.

**Actuator.** None automatic. The finding names the duplicated pre-flight work
and the earlier step that already performed it.

## 4. Fixtures

Each pair differs in exactly the fact its rule claims to decide.

### Table 2: QG-005 fixtures

| Must raise                                                                                                               | Must not raise                                                                                                               | Difference under test                               |
| ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `test-lane-duplicates-coverage`: a pull-request test job and a coverage job, same workspace, same profile, same features | `test-lane-different-profile`: the same pair where the test job runs `--profile ci` and the coverage job the default profile | Profile equality, with scope and features identical |
| —                                                                                                                        | `push-and-pull-request-lanes`: the same suite on `push` and on `pull_request`                                                | Trigger grouping; must not raise                    |
| —                                                                                                                        | `linux-and-windows-lanes`: the same suite on two `runs-on` values                                                            | Platform is part of scope; must not raise           |
| —                                                                                                                        | `cargo-nextest-version-probe`: a step running `cargo nextest --version` beside a real suite                                  | A probe is not an invocation; must not raise        |
| `two-invocations-one-job`: one job running the suite twice with identical scope, profile and features                    | `one-invocation-one-job`: the same job running it once                                                                       | Invocation count per job                            |

### Table 3: QG-006 fixtures

| Must raise                                                                                                                           | Must not raise                                                                                        | Difference under test                                        |
| ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `all-features-equals-defaults`: a leg named all-features passing a list equal to the default set, beside a default leg               | `all-features-superset`: the same pair where the list adds one feature outside the default set        | Set equality after resolution, with both leg names unchanged |
| `no-default-features-empty-default`: two legs where one passes `--no-default-features` against a manifest whose default set is empty | `no-default-features-nonempty-default`: the same pair against a manifest with a non-empty default set | Whether the manifest's default set is empty                  |
| `features-from-expression`: a leg whose feature list comes from an unresolvable expression                                           | —                                                                                                     | Yields `indeterminate`                                       |

### Table 4: QG-007 fixtures

| Must raise                                                                                                 | Must not raise                                                               | Difference under test                           |
| ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------- |
| `dry-run-repeats-tests`: a job running the suite then a dry run with the pre-flight enabled                | `dry-run-preflight-skipped`: the same job with the skip on the dry-run step  | The skip, with both jobs running the same suite |
| `skip-set-config-wide`: the skip set in the lading configuration file rather than on the step              | `skip-set-on-step`: the same skip passed to the dry-run step                 | Skip scope, with the dry run identical in both  |
| `pruned-away-packaging`: a pruned dry run that no longer runs `cargo package` per crate                    | `pruned-keeps-packaging`: the same pruning with per-crate packaging retained | Whether packaging survives the pruning          |
| `contract-asserts-flag-only`: a contract asserting the flag but not that the recipe hands lading `publish` | `contract-asserts-both-ends`: the same contract asserting both               | Which ends the contract binds                   |

## 5. Contract mutations

Every mutation is applied in both directions.

- **QG-005, profile mutation.** Drop the profile from the equality predicate.
  The rule must now raise on `test-lane-different-profile`, proving the profile
  is part of the identity rather than incidental. This mutation is the one that
  protects trybuild: a rule indifferent to the profile would recommend deleting
  a lane that runs suites the survivor does not.
- **QG-005, trigger mutation.** Drop the trigger from the grouping. The rule
  must now raise on `push-and-pull-request-lanes`.
- **QG-005, counting mutation.** Count occurrences per file rather than
  invocations per job. The rule must stop raising on `two-invocations-one-job`.
  This is the mutation the shared-actions finding argues for; a file-level
  count missed that defect in the original survey.
- **QG-005, word-boundary mutation.** Replace whole-shell-word matching with a
  substring match. The rule must now raise on `cargo-nextest-version-probe`.
- **QG-006, textual mutation.** Compare feature-list arguments as text rather
  than as resolved sets. The rule must stop raising on
  `all-features-equals-defaults`. A rule that survives this mutation with its
  suite green is matching names, and axinite is the proof that names lie.
- **QG-006, default mutation.** Resolve feature sets without Cargo's default
  features. The rule must stop raising on `all-features-equals-defaults` and
  must start raising on `all-features-superset`, so the mutation is visible
  from both sides.
- **QG-007, scope mutation.** Accept a configuration-file-wide skip. The rule
  must stop raising on `skip-set-config-wide`.
- **QG-007, packaging mutation.** Remove the packaging-retention predicate.
  The rule must stop raising on `pruned-away-packaging`.
- **QG-007, one-end mutation.** Assert only the step command, or only the
  recipe's `publish` subcommand. The rule must stop raising on
  `contract-asserts-flag-only`, proving both ends are load-bearing.

## 6. Open questions

1. **Trigger equality for composite events.** A lane keyed on
   `pull_request` and a lane keyed on `push` to the default branch are
   different triggers by Section 3.2, and a lane keyed on both is a duplicate
   of each on one event and not the other. Whether the rule should report a
   partial duplicate, and with what severity, is unresolved.
2. **Reusable-workflow callers.** A caller passing a feature list through
   `with:` needs the callee resolved to compare sets. Where the callee is not
   in the checkout the verdict is `indeterminate`, which will be the common
   case for estate-shared workflows and may make the rule quiet in exactly the
   repositories that call them most.
3. **Coverage tools other than nextest.** QG-005's profile clause is written
   for nextest. A repository running `cargo test` under a coverage wrapper has
   no profile to compare, and the equality predicate degenerates to scope and
   features. Whether that degenerate form is sound enough to report on is open.
4. **Whether QG-005 should ever patch.** The actuator is withheld because the
   choice of survivor is a judgement. If the estate settles a default —
   coverage survives, test-only lanes are removed, profile carried across — the
   rule could emit the patch. The axinite case suggests the profile
   carry-across is the part that would go wrong.
5. **Interaction with CI-017.** Removing a test-only lane in favour of a
   coverage lane removes the doctest execution CI-017 asks for, where the
   removed lane ran plain `cargo test`. The two rules must not be able to
   recommend opposite changes on one repository, and the ordering between them
   needs stating before either ships.

## 7. Alternatives rejected

**Compare suite commands as text.** A string comparison of the two legs'
commands is trivial to implement. Rejected on the axinite evidence: the
duplicate leg's command differed from its twin in every character that mattered
to a reader and in nothing that mattered to the compiler. A textual rule
reports this repository compliant.

**Delete the publish dry run entirely.** The dry run is the most expensive step
in several release lanes, and its check and test pair genuinely repeat earlier
work. Rejected because per-crate packaging is a gate class nothing else covers;
rstest-bdd #777's unexported symbol passed the workspace test and Clippy and
failed only in packaging. The narrowing, not the deletion, is the remediation.

**Let the repository's contract carry all of this.** Each finding above was
fixed with a contract test in its own repository. Rejected for QG-006 and
QG-007, whose correct answers are the same for every repository: two legs with
one resolved feature set are always duplicate, and a dry run always keeps
packaging. QG-005 is the closer call, because which lane survives is a
judgement, and it is resolved by shipping the rule without an actuator: the
duplicate is an estate-wide fact, and the choice of remedy stays local.

**Set the lading skip in the configuration file.** One line in `lading.toml` is
simpler than a flag on a step. Rejected because the same configuration governs
the real publish, where no earlier step has run the suite and the pre-flight is
the only check there is. The skip belongs where the duplication is.

**Threshold on total job minutes.** A rule reporting workflows above a duration
budget would catch every case in Table 1 and need no resolver. Rejected because
it reports the symptom without naming the cause, cannot distinguish a slow
suite from a duplicated one, and would flag the repositories with the largest
legitimate suites first.
