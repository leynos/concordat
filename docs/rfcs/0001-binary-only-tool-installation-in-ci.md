# RFC 0001: Binary-only tool installation in continuous integration

## Preamble

- **RFC number:** 0001
- **Status:** Proposed
- **Created:** 2026-09-16
- **Audit domain:** Toolchain Baseline (design document Section 3.1.3)
- **Check identifiers:** TA-001, TA-002, TA-003
- **Depends on:** the workflow fact envelope and the canon data files for pins
  and floors, both prerequisites recorded in issue #153

## 1. Summary

A continuous integration (CI) job must not compile a tool it merely consumes.
Tools arrive as pinned release archives whose digests are verified against the
publisher's sidecar, obtained through the shared `install-tool` action or
through `cargo-binstall`, and the pin is a commit SHA or an exact version,
never a branch or a bare tag.

This RFC proposes three rule packages in a new `TA` (tool acquisition) family:

- **TA-001** — a job builds a consumed tool from source.
- **TA-002** — a release archive is installed without digest verification
  against the publisher's sidecar.
- **TA-003** — a tool pin is a branch name or a bare tag rather than a commit
  SHA or an exact version.

Two exemptions apply throughout: the repository's own crate under test, and a
build that is the workflow's product.

### 1.1 Why a new family

The catalogue's existing prefixes do not fit. `CI` covers CI/CD integrity as
invocation correctness, `QG` covers whether a gate binds, and `RT` and `PY`
cover per-language configuration floors. How a tool *arrives* is a property of
the toolchain baseline that is language-independent: the same rule governs a
Rust helper crate, a Python console script, and a Node package. Introducing a
prefix for it follows the precedent set in the proposal behind issue #153,
which introduced `MK` for Makefile shape and `CG` for codegen configuration
rather than overloading an existing family.

`TA-001` to `TA-003` are free. They do not collide with QG-001 to QG-004,
CV-001 to CV-004, DB-001 to DB-004, MT-001, LC-001 to LC-003, PY-001 to PY-010,
RT-001 to RT-011, CS-010, or with the FM, MK, CG, BP, CI and QG identifiers
proposed in issue #153.

### 1.2 Relationship to CI-015

CI-015, proposed in issue #153, requires that an install step name an exact
version and that a version supplied through a variable resolve to a literal
that is not `latest`. It asks *which* version. This RFC asks *what arrives*: a
compiled artefact or a verified binary. The two are orthogonal and both
necessary. A `cargo install merman-cli --version 0.7.0` step satisfies CI-015
completely and is the exact defect TA-001 exists to find, because an exactly
versioned source build still costs a compile on every run. Conversely a
digest-verified archive installed at `latest` satisfies TA-002 and fails CI-015.

Where both rules would fire on one step, TA-001 carries the remediation,
because moving the tool to a pinned archive resolves the version question as a
side effect.

## 2. Motivation

The Ubicloud migration campaign found source builds of consumed tools to be
among the largest recurring per-run costs in the estate, and the least visible:
a source build that a cache is supposed to elide looks free in the workflow
text and is paid in full on every run when the cache does not restore.

### 2.1 The measured case: merman-cli in shared-actions

shared-actions built `merman-cli` from source on every pull request. A cache
entry for it was written and never restored: the second run reported "Cache not
found" followed by "Unable to reserve cache … another job may be creating this
cache". The build therefore ran every time, at 5m42 warm and 6m28 cold, against
4m02 to 4m18 for the whole hosted job it sat inside. The workflow text gave no
indication: it named a cache, and the cache appeared to exist.

Issue #483 recorded the finding and pull request #488 replaced the build with
four pinned release archives. Each archive's digest was computed from the
downloaded bytes before being compared with the publisher's sidecar, so the
comparison tested the download rather than restating the publisher's claim. The
`install-tool` action learned `tar.xz`, because merman ships `.tar.xz` and no
aarch64 Linux archive exists at all — a fact the manifest now states rather
than leaving to a failed download to discover. Three `ci.yml` steps were
dropped, the contended cargo cache key was given one owner, and an
install-source contract was added.

### 2.2 The pattern across the estate

Table 1 records the evidence behind each clause.

#### Table 1: Findings behind the tool-acquisition rules

| Repository                   | Finding                                                                                                                                                                                          | Rule                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------- |
| shared-actions #483, PR #488 | `merman-cli` built from source on every pull request at 5m42 warm, 6m28 cold, behind a cache entry that wrote but never restored; replaced by four pinned archives with sidecar-verified digests | TA-001, TA-002         |
| pg-embed-setup-unpriv #217   | Prebuilt `pg_worker` assets closed the source build on end-to-end evidence, with sidecars published alongside the archives                                                                       | TA-001, TA-002         |
| corbusier                    | Two workflows still run `cargo install pg-embed-setup-unpriv@0.4.0 --locked`, two minor versions behind the published release, with the consumer pull request pending                            | TA-001                 |
| dev-env-rocky                | `makeutil` built from a pinned revision on the nightly lane (migration survey, 2026-09-14)                                                                                                       | TA-001, TA-003         |
| cuprum #354 to #360          | Dogfooding findings recorded against the runner's own tool acquisition                                                                                                                           | TA-001, TA-002, TA-003 |

The corbusier case is the one that shows why an exact version is not enough on
its own. `cargo install pg-embed-setup-unpriv@0.4.0 --locked` is exactly pinned
and exactly reproducible. It is also a compile of a tool whose publisher ships
verified binaries, and being pinned is precisely what let it sit two minor
versions behind without anyone noticing: nothing moved, so nothing drew
attention. A binary install through the shared manifest makes the version a
single reviewed line in canon data rather than a literal repeated across two
workflows.

### 2.3 Why `--locked` does not settle the question

`cargo install` without `--locked` resolves dependencies afresh, so the build
is not reproducible and the tool's own transitive graph can change without a
commit. Adding `--locked` fixes the reproducibility defect and leaves the cost
defect untouched. This RFC therefore treats `--locked` as irrelevant to TA-001:
a source build of a consumed tool is a finding whether or not it is locked, and
the presence of `--locked` neither raises nor suppresses it.

## 3. Rule statement

All three rules evaluate the workflow fact envelope: for each
`.github/workflows/*.yml` and `.github/actions/*/action.yml`, the parsed jobs
and steps, the merged environment at workflow, job and step scope, every
`uses:` reference split into path and reference, and every `run:` body with
backslash continuations joined. All three fail closed. A `run:` body the
extractor cannot tokenize yields `indeterminate`, never a pass.

### 3.1 TA-001: a job builds a consumed tool from source

**Sensor.** Over every `run:` body with continuations joined, tokenized into
command words, the sensor reports a finding for each of these spellings:

- `cargo install <crate>` and `cargo install <crate>@<version>`, with or
  without `--locked`, `--version` or `--git`;
- `pip install git+<url>` and `pip install <url>#egg=`;
- `uvx --from git+<url>` and `uvx --from <path>`;
- `cargo build -p <tool>` and `cargo build --bin <tool>` where `<tool>` is not
  a member of the repository's own workspace;
- `go install <module>@<ref>` and `npm install` of a git specifier.

The sensor reads indented and semicolon-terminated forms, and forms inside a
`for` loop body whose command word resolves. It anchors on the tokenized
command word, not on the start of a line.

**Known false negative.** A sensor anchored at column zero misses every
indented and chained occurrence. The estate's install steps are routinely
indented under a conditional and chained with `&&`, so a column-zero anchor
would pass the majority of real findings. The rule states the anchor as a
tokenizer requirement precisely so a later reimplementation cannot reintroduce
it silently, and fixture `indented-chained-cargo-install` exists to fail any
implementation that does.

**Exemptions.**

- The repository's own crate under test. A workspace member built by the
  repository that owns it is not a consumed tool.
- A build that is the workflow's product. A release workflow compiling the
  binary it publishes is doing the job it exists for.

Both exemptions resolve from facts, not from a name: membership is read from
the repository's `Cargo.toml` workspace members or its packaging manifest, and
"the workflow's product" means the built path is an input to a publish,
upload-artefact, or release step in the same job.

**Actuator.** None automatic. A finding opens one tracking issue per tool,
naming the `install-tool` manifest entry to add: the tool, the version, the
archive name pattern, the sidecar name, and the targets for which archives
exist. Automatic remediation is withheld because the manifest entry requires a
fact the sensor cannot obtain — whether a published archive exists for each
target the workflow runs on. The merman case is the argument: no aarch64 Linux
archive exists, and a generated manifest entry that assumed one would have
produced a green rule and a red workflow.

### 3.2 TA-002: an archive installed without digest verification

**Sensor.** For each step that downloads a release archive — `curl`, `wget`,
`gh release download`, or an action whose inputs name a URL — require that a
digest be computed from the downloaded bytes and compared with a value obtained
independently of the same download. A digest passed as a literal input to the
shared `install-tool` action satisfies this: the action performs the
comparison. A digest read from a file fetched in the same `curl` call as the
archive does not.

**Ordering is part of the rule.** Verification after execution is not
verification. A `curl … | bash` step whose digest check follows the pipe has
already run the payload; CI-012 records two such cases in the CodeScene
installer. TA-002 requires the comparison to precede any execution or
extraction of the downloaded bytes, and reports ordering as its own finding
class so the remediation text can name it.

**Exemptions.** A download from a source that supplies no sidecar and no
published digest is `indeterminate`, not a pass, and the remediation asks for
an upstream release request rather than asserting compliance. The estate's own
upstream requests are drafted rather than filed directly; where the publisher
is outside the estate, the tracking issue records the request text.

**Actuator.** None automatic, for the same reason as TA-001: the correct digest
is a fact about a published artefact.

### 3.3 TA-003: a pin that is a branch or a bare tag

**Sensor.** For each acquisition the pin must be a full commit SHA of forty
hexadecimal characters, an exact version string, or a bare tag whose publisher
has a verified immutable-tag mechanism recorded in canon data. That third form
is the single exception, stated here so the rule and the
`uses-verified-immutable-tag` fixture agree; it is set out below. The
acquisitions the sensor reads are:

- a `uses:` reference, split into path and reference;
- a Git acquisition, in every form that selects a revision: `--git` paired with
  `--rev`, with `--branch`, or with `--tag`, and `--git` alone, which selects
  the default branch;
- a `go install` module reference;
- a manifest entry in canon data.

`--branch` and a bare `--git` fail, because both name a moving target. `--rev`
passes only at full length. `--tag` is treated exactly as a `uses:` tag, by the
next paragraph.

A branch name fails. A bare tag fails unless an immutable-tag mechanism is
explicitly verified for that publisher, matching the standard already set for
the shared auto-merge and mutation-testing workflows in DB-003 and MT-001.
Semantic-version and major-version tags do not pass automatically.

**Why an abbreviated SHA fails.** A short SHA is a prefix, and a prefix that is
unique today can become ambiguous as the object database grows, at which point
the reference resolves to a different object or to none. Every fixture in this
RFC therefore writes SHAs at full length, and the abbreviated form appears only
as the subject of the SHA-length mutation in Section 5.

**Exemption.** A reference to a repository within the estate that the same
change set also pins is resolved against the canon pin data rather than
reported, so a coordinated repin is not reported as twenty findings.

**Actuator.** Repin to the SHA recorded in canon data, comment-preservingly.
This is the one actuator in the family that can act, because the target value
is an estate constant rather than a fact about an external artefact.

## 4. Fixtures

Each pair proves the rule narrow as well as sufficient. The must-not-raise
fixture in every pair differs from its partner in exactly the fact the rule
claims to decide, so a rule that raised on both would be shown to discriminate
nothing.

### Table 2: TA-001 fixtures

| Must raise                                                                                                              | Must not raise                                                                                                               | Difference under test                                                       |
| ----------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `cargo-install-consumed-tool`: a lint job running `cargo install merman-cli --version 0.7.0`                            | `install-tool-pinned-archive`: the same job installing merman-cli 0.7.0 through `install-tool` with four digests             | Acquisition mode, with the version identical in both                        |
| `indented-chained-cargo-install`: the same install indented under an `if` and chained with `&&`                         | `echo-mentions-cargo-install`: a step whose `run:` body echoes the string `cargo install merman-cli` in a diagnostic message | Whether the tokenized command word is `cargo`, not whether the text appears |
| `cargo-build-helper-crate`: `cargo build -p xtask-lint` where `xtask-lint` is a path dependency from another repository | `cargo-build-own-workspace-member`: `cargo build -p my-crate` where `my-crate` is a workspace member of the repository       | Workspace membership                                                        |
| `release-job-builds-consumed-tool`: a release job that compiles a consumed linter alongside its product                 | `release-job-builds-its-product`: a release job compiling only the binary it uploads                                         | Whether the built path feeds a publish step                                 |
| `pip-install-git-url`: `pip install git+https://…@main`                                                                 | `pip-install-pinned-wheel`: `pip install tool==1.4.0` from the index                                                         | Source build against published wheel                                        |
| `loop-installs-unresolvable`: a `for` loop whose package token is a shell variable set outside the envelope             | —                                                                                                                            | Yields `indeterminate`; asserted to be neither compliant nor non-compliant  |

### Table 3: TA-002 fixtures

| Must raise                                                                                                 | Must not raise                                                                                                                   | Difference under test                                       |
| ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `archive-without-digest`: `curl -L …/tool.tar.xz` then `tar xf` with no comparison                         | `archive-with-sidecar-digest`: the same download, digest computed from the bytes and compared with the sidecar before extraction | Presence of the comparison                                  |
| `digest-after-execution`: `curl … \| bash` followed by a digest check of the same URL                      | `digest-before-execution`: download, compare, then execute the saved file                                                        | Ordering, with both steps present in each                   |
| `digest-from-same-fetch`: the digest read from a file fetched in the same `curl` invocation as the archive | `digest-from-sidecar`: the digest fetched as its own sidecar request                                                             | Independence of the compared value                          |
| `no-published-sidecar`: a publisher shipping no sidecar and no digest                                      | —                                                                                                                                | Yields `indeterminate` with an upstream-request remediation |

### Table 4: TA-003 fixtures

| Must raise                                                                                          | Must not raise                                                                                             | Difference under test                                       |
| --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `uses-branch-ref`: `uses: leynos/shared-actions/.github/actions/setup-rust@main`                    | `uses-sha-ref`: the same path at `@0e3c4d24a7f1b5e9c83d26f04b7a1e58d9c3f260`, forty hexadecimal characters | Reference kind                                              |
| `uses-bare-tag`: the same path at `@v2` with no immutable-tag mechanism verified                    | `uses-verified-immutable-tag`: the same tag where the publisher's immutability is recorded in canon data   | Whether immutability is verified, not whether a tag is used |
| `git-rev-branch`: `--git <url> --branch main`                                                       | `git-rev-sha`: `--git <url> --rev 7cb894fe2a1d6035c8f49b7e0d23a86154fc9b7d`                                | Reference kind, with the same `--git` URL in both           |
| `git-no-revision`: `--git <url>` with no revision selector, which takes the default branch          | `git-rev-sha`: as above                                                                                    | Whether a revision is selected at all                       |
| `git-abbreviated-rev`: `--git <url> --rev 7cb894fe`                                                 | `git-rev-sha`: as above                                                                                    | SHA length, with the same commit named in both              |
| `coordinated-repin`: two references to an estate repository that canon data pins in the same change | —                                                                                                          | Resolved against canon data, not reported                   |

## 5. Contract mutations

A rule that ships without a mutation that defeats it has not been shown to
bite. Each mutation below is applied to the rule's own fixtures, and the rule
must report a finding on the mutated input in both directions: the must-raise
fixture must stop raising when the rule is weakened, and the must-not-raise
fixture must start raising when the rule is widened.

- **TA-001, anchor mutation.** Re-anchor the sensor at column zero. The rule
  must fail `indented-chained-cargo-install`. A sensor that still passes its
  whole suite after this mutation is matching text the fixtures do not exercise.
- **TA-001, substring mutation.** Replace command-word tokenization with a
  substring match on `cargo install`. The rule must now raise on
  `echo-mentions-cargo-install`, proving the tokenizer is load-bearing.
- **TA-001, exemption widening.** Widen the workspace-member exemption to any
  crate named in any manifest in the checkout. The rule must stop raising on
  `cargo-build-helper-crate`.
- **TA-001, product widening.** Widen "the workflow's product" to any step in
  the workflow rather than the same job. The rule must stop raising on
  `release-job-builds-consumed-tool`.
- **TA-002, ordering mutation.** Remove the ordering predicate, so presence of
  a digest anywhere in the step satisfies the rule. The rule must stop raising
  on `digest-after-execution`. This is the mutation the estate's own history
  argues for: a check that only asserts a digest exists passes the CodeScene
  installer defect verbatim.
- **TA-002, independence mutation.** Accept a digest obtained from the same
  request as the archive. The rule must stop raising on
  `digest-from-same-fetch`.
- **TA-003, tag mutation.** Accept any tag as a pin. The rule must stop
  raising on `uses-bare-tag` while continuing to raise on `uses-branch-ref`,
  showing the two fixtures are not interchangeable.
- **TA-003, SHA-length mutation.** Accept an abbreviated SHA. The rule must
  stop raising on `git-abbreviated-rev`, since an abbreviation is not a stable
  identifier across a growing object database. `uses-sha-ref` and `git-rev-sha`
  must continue to raise nothing under the mutation, which is what proves the
  two fixtures differ in length alone and not in some other property.
- **TA-003, revision-selector mutation.** Accept `--git` with no revision
  selector. The rule must stop raising on `git-no-revision`, proving the sensor
  reads the absence of a selector rather than only the presence of a bad one.

The consumer's own workflow contract must fail each of these mutations
independently of the rule package, following the estate rule that a contract
asserts the command rather than an identifier and is proved by mutation in both
directions.

## 6. Properties

The fixtures above are examples, and two predicates in this family are
comparators over an unbounded input space, so each also carries a Hypothesis
property test written from this document rather than from the implementation,
in the discipline the developers' guide sets for `tests/unit/test_properties.py`
(a property that restates the implementation's pattern proves nothing).

- **The pin-form predicate (TA-003).** Over generated references: acceptance is
  total, so every generated string yields a verdict and none raises; a
  forty-character hexadecimal SHA is always accepted; and every proper prefix
  of an accepted SHA is always rejected. The last is the metamorphic form of
  Section 3.3's abbreviated-SHA argument, and it is the property an
  implementation that merely matched a hexadecimal pattern would fail.
- **The digest-ordering predicate (TA-002).** Over generated step sequences:
  the verdict is invariant under inserting steps unrelated to the archive
  between the digest comparison and the execution, and moving the execution
  before the comparison always raises. Ordering, not co-presence, is what the
  rule decides, and a rule that only checked both steps exist would pass the
  first property and fail the second.

## 7. Open questions

1. **Where the manifest lives.** TA-001's remediation names an `install-tool`
   manifest entry, which implies canon data holds the tool catalogue. The
   proposal behind issue #153 already asks for `canon/pins/shared-actions.yaml`
   for the pin list. Whether the tool catalogue is a second file under
   `canon/pins/` or a first-class artefact type in the manifest is unresolved.
2. **Detecting a cache that never restores.** The merman finding was not that
   a source build existed but that its cache never restored, and the rule
   proposed here would have found the build without needing the cache evidence.
   Whether a separate sensor should report a cache key that is written and
   never restored, which is a run-log fact rather than a workflow-text fact, is
   left to the sccache ruleset in RFC 0002, where the single-writer clause
   covers the same failure from the writing side.
3. **Estate-external publishers.** TA-002's `indeterminate` verdict for a
   publisher with no sidecar leaves a real gap with no owner. The standing
   estate rule forbids filing issues on repositories outside `leynos/`, so the
   remediation can only draft the request. Whether the rule should carry a
   severity that escalates when the same publisher appears in many repositories
   is open.
4. **Windows and macOS archive coverage.** The merman case found that no
   aarch64 Linux archive existed. Whether the rule should cross-reference the
   `runs-on` values of the jobs that install a tool against the targets the
   manifest declares, and report a workflow that installs a tool on a target
   with no archive, is a natural extension that needs the runner-label canon
   data that issue #153 also requests.

## 8. Alternatives rejected

**Require `--locked` and stop there.** This is the smallest change and the one
the estate reached for first. It fixes reproducibility and leaves the compile
in place, which is the cost. shared-actions' merman build was exactly versioned
and still cost 5m42 on every pull request. Rejected because the rule would have
reported shared-actions compliant throughout the period the defect was being
measured.

**Fold the rules into CI-015.** CI-015 already inspects install steps, so one
rule could carry both concerns. Rejected because the two have different
remediations, different actuators, and different exemptions. CI-015 can patch a
version literal; TA-001 cannot patch anything without a fact about published
archives. A rule with two remediations and two actuator classes is two rules
sharing a sensor, and the catalogue is the wrong place to hide that.

**Ban `cargo install` outright.** A single prohibition is simpler to implement
and to explain. Rejected because it reports the estate's own release workflows
and the crates under test, which would train reviewers to add exemptions until
the rule means nothing. The two exemptions in Section 3.1 are the minimum that
leaves the rule true.

**Verify signatures rather than digests.** Signature verification is stronger
and would subsume TA-002. Rejected for now because the estate's publishers ship
sha256 sidecars and no minisign sidecars by design, and a rule demanding a
mechanism nothing publishes would be indeterminate everywhere. If publishers
adopt signing, TA-002's comparison predicate extends to accept a verified
signature in place of a digest without changing the rule's shape.

**Rely on the per-repository contract alone.** Each of the findings above was
fixed with a contract test in its own repository, which is the estate's normal
mechanism. Rejected because the acquisition mode has one canonical answer for
every repository, needs no measurement, and fails closed when the extractor
cannot read a step. That is the `rust-makefile-baseline` pattern, and the
proposal behind issue #153 places rules of exactly this shape on the rule
surface rather than the contract surface.
