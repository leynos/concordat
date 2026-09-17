# RFC 0002: The sccache configuration ruleset

## Preamble

- **RFC number:** 0002
- **Status:** Proposed
- **Created:** 2026-09-16
- **Audit domain:** Toolchain Baseline (design document Section 3.1.3)
- **Check identifiers:** RT-012, RT-013, RT-014, RT-015, RT-016
- **Incorporates:** CI-016 (proposed in issue #153) as the wrapper-naming
  clause
- **Depends on:** the workflow fact envelope and the canon data files for
  pins, both prerequisites recorded in issue #153

## 1. Summary

sccache has two halves. A wrapper tells `cargo` to route every `rustc`
invocation through the cache, and a backend tells the cache where to keep its
objects. Either half alone is worse than neither: a wrapper without a backend
compiles everything and stores it where nothing persists, paying overhead for
no return, and a backend without a wrapper installs a cache that nothing
consults. Both failures are invisible in the ordinary signal, because a compile
that never reached the wrapper never enters the hit-rate denominator.

This RFC proposes five rule packages continuing the Rust toolchain and
acceleration family, which ends at RT-011:

- **RT-012** — a Rust job that compiles carries both halves.
- **RT-013** — the `setup-rust` pin is one SHA across every same-tree
  reference and is not one of a named set known to export neither half.
- **RT-014** — a statistics step runs after the build through `SCCACHE_PATH`,
  including on failure.
- **RT-015** — each cache key family has exactly one writer per workflow.
- **RT-016** — sccache is not installed into a job that compiles nothing.

The sixth clause of the ruleset, that no workflow may set `RUSTC_WRAPPER` by
bare name, is already CI-016 in the proposal behind issue #153, with the same
whitaker #409 evidence. This RFC does not issue a second identifier for it.
Section 3.6 states how CI-016 composes with the five rules above, because the
ruleset is not complete without it.

### 1.1 Why the RT family

RT-006 to RT-011 are the Rust toolchain and acceleration rules: nightly pin
age, required components, mold and Cranelift development configuration,
Polonius-next, and nextest wiring. Compiler caching is acceleration
configuration for Rust, sits in the same audit domain, and shares RT-008's
sensor surface over `.cargo/config.toml`. Continuing the family at RT-012 keeps
one roadmap item, one section of the design document, and one set of fixtures
together. RT-012 to RT-016 are free and collide with nothing in the catalogue
or in issue #153.

## 2. Motivation

### 2.1 Why the hit rate cannot find these defects

Whitaker's Windows lane set the wrapper to the bare name `sccache` at workflow
level. Switching to the shared action's absolute-path export raised compile
requests from 2,244 to 2,313. Sixty-nine `rustc` invocations had resolved no
wrapper at all and compiled uncached, and the hit rate could not show it
because those invocations never entered the denominator. The lane reported a
healthy percentage throughout.

The lesson generalizes to every clause here. Each defect in this ruleset
removes work from the measurement rather than failing it, so a repository can
report a good hit rate while paying for a cache it is not using. Compare
request counts, not only hit rates, when a wrapper changes.

### 2.2 The measured cases

Table 1 records the evidence behind each rule.

#### Table 1: Findings behind the sccache rules

| Repository                    | Finding                                                                                                                                                                                                                                                                                                                         | Rule                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| pg-embed-setup-unpriv         | `setup-rust` at 074f7d8b defaults `use-sccache` on, and neither `RUSTC_WRAPPER` nor `SCCACHE_GHA_ENABLED` is set anywhere, so every Rust job installs sccache and uses none across `ci`, `coverage-main` and `release`                                                                                                          | RT-012, RT-016         |
| ortho-config #495             | No wrapper and no backend anywhere; `setup-rust` pinned at 32c8ea64, which exports neither half. Twelve references moved to 0e3c4d24, the backend set on three jobs, no bare wrapper, a hermetic pin contract with a named-bad-SHA exception, and a statistics step through `SCCACHE_PATH`. Contracts 34 to 45, seven mutations | RT-012, RT-013, RT-014 |
| whitaker #409                 | The Windows lane's bare-name wrapper left 69 invocations unwrapped; the reference fix landed on shared-actions main at 7cb894fe                                                                                                                                                                                                 | CI-016                 |
| chutoro                       | Sets `RUSTC_WRAPPER` by bare name on an otherwise correct pin, the same shape as whitaker #409; fix pending                                                                                                                                                                                                                     | CI-016, RT-013         |
| shared-actions #483, item two | The `Linux-cargo-<hash>` key is written by two jobs and has never saved, because the coverage job contends for it; the fix is an additive `save-cache` input defaulting to true                                                                                                                                                 | RT-015                 |

The chutoro case is the argument for shipping RT-013 alongside CI-016 rather
than relying on either alone. Chutoro's pin is fixed and correct; the defect is
caller-side. Conversely ortho-config's callers were clean and the pin exported
neither half. A ruleset that checked only one side would report each repository
compliant in turn.

### 2.3 Why the wrapper is never set caller-side

Setting `RUSTC_WRAPPER` in the workflow is the obvious remedy for a pin that
does not export it, and it recreates whitaker #409 exactly. A bare name
resolves through `PATH`, and any invocation that runs before the binary is on
`PATH`, or in an environment where it is not, silently compiles unwrapped. The
absolute path is known only to the action, after it has installed the binary.
The remedy is therefore always to repin, never to add the wrapper line. RT-013
is the rule that makes repinning checkable, and the actuator in Section 3.2
adds the backend variable but never the wrapper.

### 2.4 Ordering inside the action is load-bearing

The backend must be configured before the sccache server starts, because the
server binds its backend at start and a later environment change is invisible
to it. The wrapper must be exported after `SCCACHE_PATH` exists, because the
absolute path is what the export names. On Ubicloud runners the Actions cache
credentials export must run after checkout and before `setup-rust` or any other
step that starts the server. This ordering is a property of the pinned action's
text, which is why RT-013 checks the pin rather than asking each consumer to
reproduce the sequence.

## 3. Rule statement

All five rules evaluate the workflow fact envelope together with the pinned
action's text at the pin. Reading the action text is hermetic: the rule asserts
one SHA across the same-tree references and a short named set of SHAs known to
export neither half, and never fetches the action over the network. A network
fetch would make the rule's verdict depend on the state of a remote repository
at evaluation time, which is not a property of the checkout under audit.

### 3.1 Scope: which jobs are in scope

A job is in scope when it compiles. The rules resolve this from facts: a `run:`
body invoking `cargo build`, `cargo test`, `cargo clippy`, `cargo llvm-cov`,
`cargo nextest`, or a `uses:` step whose action compiles, and the `language`
classification the coverage action itself uses.

Scope is never decided by event name. `setup-rust`'s own event guard excludes
the `release` event, and a release workflow triggered by a tag push or by
dispatch never sees that event, so the guard does not fire there. A release job
is out of scope only when it compiles nothing, and ortho-config's
`verify-published-assets` job is the case that shows the distinction matters:
it dry-runs `binstall`, which compiles. The rule states the compiling fact
rather than citing the event, and the consumer's contract must do the same.

### 3.2 RT-012: both halves on every compiling job

**Sensor.** For each in-scope job, require a configured backend and an
action-exported wrapper.

A configured backend is one of:

- `SCCACHE_GHA_ENABLED` set truthy in the merged workflow, job or step
  environment, which selects the Actions cache backend; or
- `SCCACHE_DIR` set to a path that an `actions/cache` step in the same job
  restores and saves, which selects a local-disk backend with an owner.

Both forms are accepted deliberately. Whitaker's own `setup-sccache` action
exports `SCCACHE_DIR` and the wrapper and never sets `SCCACHE_GHA_ENABLED`,
because on 2026-09-03 the Actions backend's traffic was landing in GitHub's
cache rather than Ubicloud's; `actions/cache` owns the directory under the
`sccache-v1-` key families. A rule that demanded the Actions variable would
report the estate's reference implementation non-compliant, and the estate
would learn to exempt it. The finding is an *unconfigured* backend: neither
variable set, or `SCCACHE_DIR` set with no cache step owning the path.

An action-exported wrapper is `RUSTC_WRAPPER` set by a `setup-rust` reference
at a pin that exports it, or by a repository-local action that exports an
absolute path.

Three caller-side values are possible, and each is assigned to exactly one
rule, so no step can fall between them:

| Caller-side `RUSTC_WRAPPER`                                    | Owning rule | Verdict                                                           |
| -------------------------------------------------------------- | ----------- | ----------------------------------------------------------------- |
| Absent, with an exporting pin                                  | RT-012      | Compliant                                                         |
| Absent, with a pin that exports nothing                        | RT-012      | Non-compliant; the remedy is RT-013's repin, never a wrapper line |
| The literal `sccache`                                          | CI-016      | Non-compliant                                                     |
| An absolute path, or an expression reading the action's output | RT-012      | Compliant, with a note                                            |

The third row is the whitaker #409 defect and belongs to CI-016. The fourth is
the case the first draft left unassigned: a caller-side absolute path resolves
correctly and does not reproduce #409, so it is not a finding. It carries a
note rather than silence because the path is a literal the caller maintains,
and an action upgrade that moves the binary breaks it without touching the
workflow. The note names the exporting pin as the durable form.

RT-012's wrapper predicate is therefore satisfied by presence, and CI-016
decides quality, so the wrapper value of one step is classified by one rule and
yields at most one wrapper finding. Where both could fire on that value, the
more specific rule reports: CI-016 owns the bare-name literal, RT-012 owns
absence. This is a statement about wrapper classification alone, not about the
step's total finding count. RT-012's backend clause is independent, so a
compiling job that sets no backend and names the wrapper as the bare literal
raises both an RT-012 backend finding and a CI-016 wrapper finding, and both
are reported.

**Failure mode.** Where the envelope cannot resolve whether a preceding step
wrote `SCCACHE_GHA_ENABLED` to `GITHUB_ENV`, the verdict is `indeterminate`.

**Actuator.** Add the backend variable at job level, comment-preservingly. The
wrapper line is never added; where the wrapper is absent because the pin does
not export it, the finding is RT-013's and the remediation is the repin.

### 3.3 RT-013: one pin, and not a known-bad one

**Sensor.** Collect every reference to the shared `setup-rust` action and the
coverage actions in the checkout.

**The grouping key is the repository, not the action path.** `setup-rust` and
`upload-codescene-coverage` are two directories of the one `shared-actions`
repository, so a `uses:` SHA pins that repository's commit rather than the
action's. Two paths at two SHAs means the checkout consumes the repository at
two commits, which is the condition the rule exists to report; grouping by path
would call that compliant. Comparison is therefore within one owner and
repository, and a reference to a different repository is a different group and
never compared. This is why `twelve-identical-pins` spans several action paths
of the one repository and must not raise.

Three findings:

- **Divergent pins.** More than one distinct SHA across same-tree references to
  the one repository. A reference that is byte-identical to another but at a
  different SHA is still a divergence; ortho-config moved its
  `dependabot-automerge` reference, byte-identical to its neighbours, purely so
  the one-SHA claim held.
- **A known-bad pin.** The SHA appears in the canon data list of pins known to
  export neither half. 32c8ea64 is the seed entry, recorded from the
  ortho-config survey with the date it was current.
- **A uniformly unknown pin.** Every reference carries one SHA that is neither
  the current pin nor a named-bad one. Reported as `indeterminate` naming the
  SHA, for the reasons below.

**Why a named list rather than a floor.** A floor requires ordering SHAs, which
is not a total order the rule can compute from a checkout. The named set is
small, is canon data with a recorded reason per entry, and fails closed: a pin
outside the set and not equal to the current pin is never a silent pass. Which
of the other two classes it falls into depends on the checkout. Where the
references disagree, the divergence clause reports it; where they agree on that
one SHA, the divergence clause cannot fire and the third class reports it as
`indeterminate`, as Section 3.3 sets out below. The named set therefore removes
the need to guess about a specific pin, not the need for the third class.

**Hermeticity, and why a network fetch is refused.** The contract shape is
ortho-config #495's: assert one SHA across every same-tree reference, and
assert a short named set of SHAs known to export neither half as the exception.
It never fetches the action text over the network. Three reasons, in order of
weight:

1. **The package contract forbids it.** Section 2.1.2 specifies a `conftest`
   sensor as evaluation over structured inputs and a `github-api` sensor as
   pure evaluation over an injected or local snapshot that "never reads
   credentials or makes network calls". A sensor that fetched an action would
   be neither, and would need a credential to read a private action, which no
   sensor in the catalogue is permitted to hold.
2. **The verdict would stop being a property of the checkout.** A fetch makes
   the answer depend on a remote repository's state at evaluation time, so the
   same commit audited twice can yield two verdicts, and a finding cannot be
   reproduced from the evidence the sweep recorded. Replay, which `rule run`
   requires, becomes impossible.
3. **A rate-limited or failed fetch has no safe verdict.** Failing open passes
   every repository during an outage. Failing closed reports the whole estate
   non-compliant on a `403`. Neither is a statement about the repository.

**The uniformly unknown pin.** One case is neither divergent nor known-bad: a
checkout where every reference carries the same SHA that is not the current pin
and not in the named set. There is only one distinct SHA, so the divergence
clause cannot fire, and hermeticity means the rule cannot read that commit to
learn what it exports. This is the third finding class, and it is reported as
an explicit `indeterminate` naming the SHA, not as a pass.

Reporting it as a pass would defeat the rule. Ortho-config's pin, 32c8ea64, was
uniform across the repository before it was surveyed and added to the named
set, so a rule that passed uniform unknown pins would have reported
ortho-config compliant on exactly the configuration RT-013 exists to catch. The
`indeterminate` verdict is what makes the named set self-extending: it is the
signal to survey that pin and record the result, whichever way it goes.

The cost of hermeticity is that the named set must be maintained by hand, and
the rule is honest about the trade. An entry is canon data carrying the reason
and the date it was current, and the set only has to name the pins that a
survey has judged, because everything else is already reported: a divergent pin
by the first clause, and a uniform unknown one by this third class.

**Actuator.** Repin to the SHA in canon data, comment-preservingly.

### 3.4 RT-014: statistics after the build, including on failure

**Sensor.** Each in-scope job must run a statistics step after its compiling
steps, invoking sccache through `SCCACHE_PATH` rather than by name, with an
`if:` that runs the step when the job has failed. The absolute path matters
here for the same reason it matters for the wrapper.

**Why this is a rule and not a nicety.** The second run's warm hit rate is the
only evidence that a cache is working, and a statistics step that is skipped
when the build fails is skipped in exactly the runs where the numbers are
wanted. The estate's warm-proof discipline depends on the counters being
printed, and several of the findings in Table 1 were diagnosed from
`Cache location`, request counts and read-error counts in the job log.

**Exemption.** A job whose compiling step is itself a probe, such as a
`--version` invocation, is out of scope by Section 3.1 and carries no
statistics obligation.

**Actuator.** Append the statistics step. This actuator can act because the
step's text is an estate constant.

### 3.5 RT-015: one writer per cache key family

**Sensor.** For each cache key family in a workflow — the key with its variable
parts abstracted — count the jobs that save it. More than one writer is a
finding.

**Why the writer count is the subject.** The `Linux-cargo-<hash>` key in
shared-actions is written by two jobs and has never saved. Two jobs racing one
immutable key produce a reservation refusal for the loser and, in the observed
case, no saved entry at all. The symptom is a permanently cold restore that
looks like a cache miss rather than a configuration defect, and the merman
finding in RFC 0001 sat behind exactly this shape.

**Actuator.** Set the additive `save-cache` input, defaulting to true, to false
on every writer but the designated one. The designation is a choice the rule
cannot make, so the actuator emits the patch with the designated writer named
in the finding and the remaining writers disabled, and the finding text states
which job was designated and why: the job that runs first on the critical path,
so the others restore rather than race.

### 3.6 CI-016 and the bare wrapper

CI-016 reports any `env` scope setting `RUSTC_WRAPPER` to the literal
`sccache`, and `.cargo/config.toml` setting `rustc-wrapper = "sccache"`. It is
the sixth clause of this ruleset and ships under its own identifier. Two
composition rules apply so that one defect yields one finding:

- A step with a bare-name wrapper is CI-016, not RT-012. RT-012's wrapper
  predicate is satisfied by presence, and CI-016 decides quality.
- A repository with a bare-name wrapper *and* a known-bad pin raises both,
  because the two have different remediations that must both be applied: the
  repin supplies the wrapper, and the bare-name line must be deleted rather
  than left to shadow it.

### 3.7 RT-016: sccache installed where nothing compiles

**Sensor.** A job out of scope by Section 3.1 whose `setup-rust` reference
leaves `use-sccache` at its default of on. The install is pure cost: the binary
is downloaded and a server may start, and no `rustc` invocation exists to route
through it.

**Boundary with RT-012.** The two rules partition rather than overlap. A
compiling job with neither half configured is RT-012. A non-compiling job with
sccache installed is RT-016. pg-embed raises both, on different jobs, and the
findings name different remediations: set the backend on the compiling jobs,
and pass `use-sccache: false` on the rest.

**Actuator.** Set `use-sccache: false` on the step, comment-preservingly.

## 4. Fixtures

Each pair differs in exactly the fact its rule claims to decide.

### Table 2: RT-012 fixtures

| Must raise                                                                                                        | Must not raise                                                                                                                                              | Difference under test                                                                   |
| ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `compiling-job-no-backend`: a job running `cargo test` with the wrapper exported and neither backend variable set | `compiling-job-gha-backend`: the same job with `SCCACHE_GHA_ENABLED: "true"` at job level                                                                   | Backend configured                                                                      |
| `sccache-dir-unowned`: `SCCACHE_DIR` set with no `actions/cache` step for the path                                | `sccache-dir-owned`: the whitaker shape, the same variable with an `actions/cache` step restoring and saving that path under the `sccache-v1-` key families | Whether the directory has an owner                                                      |
| `release-job-binstall-dry-run`: a tag-triggered release job that dry-runs `binstall` with neither half set        | `release-job-uploads-only`: a release job that only uploads prebuilt assets                                                                                 | Whether the job compiles, with both triggered by a tag push                             |
| `caller-side-bare-wrapper`: the job setting `RUSTC_WRAPPER: sccache`                                              | `caller-side-absolute-path`: the same job setting an absolute path                                                                                          | Which rule owns the step; the bare name is CI-016's, the path passes RT-012 with a note |
| `backend-via-github-env`: a preceding step writing `SCCACHE_GHA_ENABLED` to `GITHUB_ENV`                          | —                                                                                                                                                           | Yields `indeterminate`                                                                  |

### Table 3: RT-013 fixtures

| Must raise                                                                                     | Must not raise                                                                                                           | Difference under test                                                                                    |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `two-distinct-pins`: eleven references at 0e3c4d24 and one at an older SHA                     | `twelve-identical-pins`: all twelve at 0e3c4d24                                                                          | Pin divergence, with the reference text byte-identical in both                                           |
| `known-bad-pin`: every reference at 32c8ea64                                                   | `current-pin`: every reference at 0e3c4d24                                                                               | Membership of the named bad set, with pin uniformity identical                                           |
| `uniform-unknown-pin`: all twelve references at one SHA that is neither current nor named-bad  | —                                                                                                                        | Yields `indeterminate` naming the SHA, not a pass; the divergence clause cannot fire on one distinct SHA |
| `network-fetch-required`: a reference to an action outside the tree                            | —                                                                                                                        | Yields `indeterminate` without a fetch; the fixture asserts zero network calls                           |
| `two-paths-two-shas`: `setup-rust` at 0e3c4d24 and `upload-codescene-coverage` at an older SHA | `two-repositories-two-shas`: `shared-actions` uniformly at 0e3c4d24 beside a third-party action at its own unrelated SHA | The grouping key is the repository, not the action path, and not the owner                               |

### Table 4: RT-014, RT-015 and RT-016 fixtures

| Must raise                                                                    | Must not raise                                                         | Difference under test                                                         |
| ----------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `stats-on-success-only`: a statistics step with no failure condition          | `stats-always`: the same step with `if: always()`                      | The failure condition                                                         |
| `stats-by-name`: the statistics step invoking `sccache --show-stats` by name  | `stats-by-path`: the same step invoking `"$SCCACHE_PATH" --show-stats` | Absolute path against name                                                    |
| `stats-before-build`: the statistics step ordered ahead of the compiling step | `stats-after-build`: the same steps in the other order                 | Step order, with both steps present in each                                   |
| `two-writers-one-key`: two jobs saving `Linux-cargo-<hash>`                   | `one-writer-one-key`: the same two jobs, one with `save-cache: false`  | Writer count                                                                  |
| `two-keys-two-writers`: two jobs each saving a distinct key family            | —                                                                      | Must not raise; proves the rule counts writers per family, not jobs that save |
| `non-compiling-job-installs-sccache`: a docs job with `use-sccache` left on   | `non-compiling-job-opts-out`: the same job with `use-sccache: false`   | The input value                                                               |

## 5. Contract mutations

Every mutation is applied in both directions, and a fixture that survives its
own mutation discriminates nothing.

- **RT-012, backend-form mutation.** Narrow the backend predicate to
  `SCCACHE_GHA_ENABLED` alone. The rule must now raise on `sccache-dir-owned`,
  demonstrating that the local-disk form is accepted deliberately rather than
  by omission.
- **RT-012, ownership mutation.** Widen the local-disk form to accept
  `SCCACHE_DIR` with no cache step. The rule must stop raising on
  `sccache-dir-unowned`.
- **RT-012, event-scope mutation.** Replace the compiling-fact scope with an
  event-name guard excluding release workflows. The rule must stop raising on
  `release-job-binstall-dry-run`. This is the mutation the estate's own finding
  argues for: a guard written against the `release` event never fires on a tag
  push.
- **RT-013, sibling-field mutation.** Swap the pinned reference field for a
  sibling field in an otherwise identical expression. The rule must stop
  reporting `two-distinct-pins`, proving the contract reads the reference and
  not its neighbour.
- **RT-013, unknown-pin mutation.** Treat a uniformly unknown pin as
  compliant. The rule must stop reporting `uniform-unknown-pin`. This mutation
  reproduces the state ortho-config was in before its survey, when 32c8ea64 was
  uniform and unrecorded, so a rule that survives it would have passed the
  configuration RT-013 exists to catch.
- **RT-013, grouping-key mutation.** Narrow the grouping key from the
  repository to the action path. The rule must stop reporting
  `two-paths-two-shas`, which is the case where one repository is consumed at
  two commits. Widening it instead, to the owner alone, must start raising on
  `two-repositories-two-shas`, so the key is proved narrow as well as
  sufficient; a key that survives both mutations is comparing something other
  than the repository.
- **RT-012, caller-side-absolute mutation.** Treat a caller-side absolute-path
  wrapper as a finding. The rule must raise on `caller-side-absolute-path`,
  proving the fourth row of the wrapper table is a deliberate pass rather than
  an unconsidered gap.
- **RT-013, hermeticity mutation.** Replace the named bad set with a fetch of
  the action text. The `network-fetch-required` fixture must fail on the
  assertion of zero network calls. The fixture asserts the call count rather
  than the verdict, because a fetching implementation reaches the same verdict
  on a reachable network and the defect is only visible in what it did to get
  there.
- **RT-014, condition mutation.** Drop the failure-condition predicate. The
  rule must stop raising on `stats-on-success-only`.
- **RT-014, path mutation.** Accept the bare name. The rule must stop raising
  on `stats-by-name`. The same mutation applied to CI-016 must make it stop
  raising on a bare wrapper, and the two must be shown to be independent checks
  rather than one predicate used twice.
- **RT-015, counting mutation.** Count jobs that reference a key rather than
  jobs that save it. The rule must now raise on `one-writer-one-key`, since
  both jobs still restore.
- **RT-015, family mutation.** Compare literal key strings rather than
  abstracted families. The rule must stop raising on `two-writers-one-key` when
  the two jobs' keys differ only in a hash component.
- **RT-016, default mutation.** Treat an unset `use-sccache` input as off. The
  rule must stop raising on `non-compiling-job-installs-sccache`, proving the
  rule models the action's declared default rather than the workflow's silence.

The last mutation is the one the estate has already paid for once: a bumped
default went untested because every fixture pinned the old value explicitly. A
fixture for a defaulted input must read the action's declared default from its
manifest, so the pair cannot drift on the next bump.

## 6. Properties

Two of this family's predicates are comparators over collections whose size and
order the fixtures cannot enumerate, so each carries a Hypothesis property test
written from this document, per the developers' guide's discipline for
`tests/unit/test_properties.py`.

- **Pin uniformity (RT-013).** Over a generated multiset of references to the
  one repository: the verdict is invariant under permutation, since a set of
  pins has no order; a multiset containing exactly one distinct SHA never
  yields the divergence finding, which is the invariant that makes the
  `indeterminate` class reachable at all rather than dead text; and every
  multiset yields exactly one of the three classes, so the classes are total
  and disjoint.
- **One writer per cache key family (RT-015).** Over a generated set of jobs
  and cache steps: the count is invariant under job order, and the verdict
  depends only on the number of writers per key family, so adding a reader
  never changes it.

## 7. Open questions

1. **Where the bad-pin set lives and who prunes it.** 32c8ea64 is the seed
   entry. The set is canon data, but nothing yet retires an entry when no
   repository references it. An unpruned set grows without bound and its
   entries stop being reviewed.
2. **Whether RT-014 can assert the counters rather than the step.** A
   statistics step that runs and prints zero requests is as much a defect as no
   step at all, and the estate reads the counters by hand today. Asserting them
   requires a run-log fact, which the workflow envelope does not carry. Whether
   this belongs in a later run-log sensor or stays in the per-repository
   contract is open.
3. **Cache-key families across workflows.** RT-015 counts writers per
   workflow. Two workflows writing one key family race in the same way, and the
   shared-actions finding happened to be within one workflow. Extending the
   count across workflows needs a rule about which workflow owns a family,
   which is a designation the rule cannot make.
4. **The Ubicloud credentials export.** The Actions backend needs
   `ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN` written to `GITHUB_ENV` with
   `ACTIONS_CACHE_SERVICE_V2` cleared, before any step that starts the sccache
   server, and a shared composite action is being added so no repository
   hand-rolls it. Whether the ordering is a sixth rule or an assertion inside
   RT-012 depends on whether that action ships first.

## 8. Alternatives rejected

**One rule with five clauses.** A single `sccache-configuration` package would
share one sensor over the same envelope. Rejected because the five clauses have
four different actuators, and one of them has none. A package whose remediation
depends on which clause fired is five rules with a shared implementation, and
the catalogue is where the distinction has to be visible.

**Fetch the action text at evaluation time.** Reading the pinned action would
let the rule decide what a pin exports rather than consulting a list. Rejected
because the verdict would then depend on a remote repository's state at
evaluation time, the rule could not run offline or on a replayed snapshot, and
a rate-limited fetch would produce an unstable verdict. The named set is small
and its entries carry a recorded reason.

**Require the Actions backend everywhere.** One accepted backend form is
simpler to state and to check. Rejected on the whitaker evidence: the estate's
own reference implementation deliberately uses a local-disk backend under an
`actions/cache` owner, for a measured reason. A rule that reported it would be
exempted, and an exempted rule teaches reviewers that the rule is advisory.

**Set the wrapper caller-side when the pin does not export it.** This is the
one-line fix and it is the defect. whitaker #409 measured 69 unwrapped
invocations from exactly this remedy. Rejected outright, and the RT-012
actuator is specified never to add the wrapper line.

**Judge a cache by its hit rate.** A threshold on the reported hit rate would
be simpler than five structural rules. Rejected because every defect in this
ruleset removes work from the denominator rather than failing it. Whitaker's
Windows lane reported a healthy rate while 69 invocations compiled uncached,
and pg-embed's jobs would have reported no rate at all rather than a bad one.
