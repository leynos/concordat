# rust-build-defaults

Audits a Rust checkout against the estate's build standard: the parallel
`rustc` frontend and the `mold` linker as **defaults**, the `rustflags` sources
held equal so neither silently replaces the other, and the Cranelift codegen
backend either configured for the development profile or refused by a recorded,
current exception. It also audits the builds that replace those defaults: a
coverage run or dev-profile release build selects LLVM where Cranelift is the
default, and a gate build that assigns `RUSTFLAGS` restates the fast flags.

The sensor is a Conftest/Rego policy evaluated over a
`policy-input/rust-build-defaults` envelope built by
`concordat artefact rule run`. Facts come from TOML parsers, a Markdown heading
walk, the pinned `makeutil parse` report and a YAML decode of each workflow,
never from a textual search: `-Zthreads=8` in a comment is not a configured
flag, and a comment containing the word `channel` is not a pinned channel. Both
mistakes were made while surveying the estate for this rule, which is why the
fixtures include each of them.

## Why the configuration file first, and the Makefile after

The standard is a default because Cargo auto-discovers `.cargo/config.toml`, so
a bare `cargo build` gets it. That is also what makes the clause checkable: a
repository whose flags live behind a `make dev-fast` target has no such file,
and fails on the first two checks alone. BD-001 to BD-006 therefore read only
the files Cargo and rustup discover.

A default holds only where nothing replaces it, and two things do. An assigned
`RUSTFLAGS` replaces every `rustflags` source in the configuration, so a gate
recipe or CI step that assigns it without restating the fast flags turns the
standard off for that build. And `-Cinstrument-coverage` is LLVM-only, so a
coverage run that inherits a Cranelift default fails outright. BD-007 to BD-009
read the Makefile and workflows for those builds. A Makefile `makeutil` refuses
makes only those clauses indeterminate; the others still run. See
[ADR-003](../../../../docs/adr-003-rust-build-defaults-reads-the-builds-that-replace-them.md).

## Checks

- **BD-001** (error): every `rustflags` source carries the parallel-frontend
  flag. Applies only where `rust-toolchain.toml` pins a nightly channel;
  `-Zthreads` is a nightly flag, so demanding it of a stable pin would break
  the build rather than accelerate it.
- **BD-002** (error): a target table that applies on Linux carries the linker
  flag, and no source that reaches beyond Linux names it. `mold` ships for
  Linux alone, so naming it unconditionally — or under `cfg(unix)`, which macOS
  builds also take — breaks the platforms it reaches. A target key this policy
  cannot place is `indeterminate`, and while any key is unplaced the policy
  will not conclude that the linker is configured nowhere: the unplaceable
  source might be the Linux table.
- **BD-003** (error): the sources are repeated, not merged. Cargo selects a
  single `rustflags` source rather than merging them — a matching `[target.*]`
  table replaces `[build] rustflags` outright — so a flag named in one source
  and not another vanishes on the platform the other one matches. The linker
  flag is the deliberate exception and is governed by BD-002.
- **BD-004** (error): the backend is the development-profile default, or the
  repository records an exception. Both states are accepted; a repository with
  neither is noncompliant. A package override beneath a profile is not the
  profile's default: Cargo applies it to the named package alone, leaving every
  other development build on the backend it had. An exception document the
  filesystem refused to read decides nothing, and is `indeterminate`.
- **BD-005** (error): a backend selection that Cargo cannot honour, or that the
  estate has not adopted, is noncompliant. A profile key without
  `[unstable] codegen-backend = true` is refused by Cargo; an `[unstable]` key
  under a non-nightly pin stops the file loading for every consumer. LLVM on a
  profile other than `dev` is not a third backend: it is the dedicated coverage
  profile BD-007 accepts, and is not reported. LLVM on `dev` still is.
- **BD-006** (error): the recorded exception names the pinned toolchain
  channel. An exception measured on an older channel no longer covers the
  toolchain the repository builds with, which is what keeps the recorded state
  from drifting quietly behind the pin. Clearing it does not require a fresh
  measurement: a line in the exception section saying what the pin is now,
  whether a measurement was taken on it, and the date any deferral runs to is a
  true statement of the recorded state and satisfies the clause. With no
  channel pinned at all the finding is `indeterminate`.
- **BD-007** (error): where Cranelift is the development default (selected by
  the `dev` profile or by `rustflags`), every coverage path and every
  dev-profile release build selects LLVM. A coverage path is any Makefile
  recipe, on any target, that runs `cargo llvm-cov`, and any workflow step that
  runs it directly. A dev-profile release build is a `cargo build`,
  `cargo zigbuild` or `cross build` without `--release`, `-r` or `--profile`,
  in a workflow triggered by `release` or a tag push. A repository with no such
  path is not applicable.
- **BD-008** (error): a Makefile gate recipe that assigns `RUSTFLAGS` restates
  `-Zthreads=8` (on a nightly pin) and the `mold` linker flag (where the build
  targets Linux). A gate recipe is one in the static closure of `lint`, `test`,
  `typecheck` or `build` (the `gate_targets` parameter). The assignment may be
  a prefix on the recipe, a target-specific assignment, or a Makefile-wide one.
  Coverage recipes and release-profile builds are BD-007's and exempt. A
  Makefile with no such assignment is not applicable.
- **BD-009** (error): a workflow step that runs a cargo gate build directly
  (`build`, `check`, `clippy`, `doc`, `nextest` or `test`, the
  `gate_cargo_subcommands` parameter) with `RUSTFLAGS` set carries the same
  flags. `RUSTFLAGS` is set by the step's own line or `env:`, the job's or
  workflow's `env:`, or the toolchain action. shared-actions `setup-rust`
  exports its `rustflags` input, `-D warnings` by default, to every later step;
  `rustflags: ''` exports nothing, so the configuration applies.
- **CF-001** (error, indeterminate): `.cargo/config.toml` exists but could not
  be read, parsed, or used, so no clause that reads it can be decided. This
  includes a configuration Cargo itself refuses:
  `rustflags = ["-Zthreads=8", 42]` makes Cargo exit, and dropping the
  offending member to read the rest would pass a repository that cannot build
  at all.
- **TC-001** (error, indeterminate): `rust-toolchain.toml` exists but its
  channel could not be classified.
- **AP-001** (error, indeterminate): no `language.rust.surfaces` list was
  declared and the checkout has no root `Cargo.toml`.
- **EN-001** (error, indeterminate): the envelope has an unknown schema
  version or kind, or `cargo`/`cargo.surfaces` has an invalid shape.

## The codegen-backend clause

The estate standard is per repository. Cranelift is the development-profile
default in every repository whose own test suite passes under it. A repository
whose suite fails under it records the failing tests as an exception in its
developers' guide and refuses the backend key by contract, and re-measures on
the next toolchain bump. The rule accepts either state and fails a repository
that has neither.

A recorded exception is recognized as a section of a declared document whose
heading names the backend. The heading is structure and can be read reliably;
the prose beneath it cannot, so the policy does not attempt to verify which
tests are named. What it does check is the channel: an exception that names the
pinned toolchain is current, and one that names an older toolchain is stale.

### What the staleness finding does and does not claim

BD-006 reports that the recorded measurement does not cover the pinned
toolchain. It does not claim that a fresh measurement is owed, because whether
one is depends on decisions the repository has recorded elsewhere and this
policy cannot read them. netsuke is the case that made the distinction
concrete: the backend is shelved there for six months by an explicit ruling,
with a dated reminder issue, so a pin bump inside that window leaves the
recorded measurement stale without obliging anyone to re-run a forty-minute
suite.

Either way the repository owes the same small thing, which is why the finding
is a finding and not a warning: a line in the exception section giving what the
pin is now, whether a measurement was taken on it, and the date any deferral
runs to. netsuke's deferral runs to 2027-03-21 and is tracked by an issue, so a
pin move inside that window is cleared by writing those three facts down rather
than by re-running a forty-minute suite.

Clearing it therefore costs an edit, and the property it preserves is worth the
edit: the failure this clause exists to prevent is a guide that quietly
describes a toolchain the repository no longer pins.

A probe crate's unwind result is evidence about the backend, not a verdict on a
repository, so nothing here reads such a probe. netsuke's exception is the
worked example of the difference. It rested on a three-case probe until
2026-09-21, when its whole suite was measured under Cranelift on the pinned
`nightly-2026-08-23`: 6 of 3308 tests fail, three of them spawned-thread panics
that abort the process, and the LLVM control on the same commit passes all of
them. The measurement confirmed the exception, but the rule would have accepted
either outcome without changing, because it asks only that one of the two
states is recorded.

## Builds that replace the default (BD-007 to BD-009)

### Selecting LLVM for coverage

The estate's Cranelift repositories were surveyed on 2026-09-28. Every workflow
coverage step used shared-actions `generate-coverage`. Eight Makefile coverage
recipes ran `cargo llvm-cov` with no selection at all, and two selected LLVM:
statelet with an environment prefix, and thysalion through a variable. On the
pinned `nightly-2026-05-28`, a Cranelift development profile with
`-Cinstrument-coverage` fails with "`-Cinstrument-coverage` is LLVM specific
and not supported by Cranelift".

A coverage path selects LLVM in any of these ways:

- `CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm`, as a prefix on the command, a
  Makefile-wide `export`, a target-specific assignment on the recipe's target,
  or a single-valued variable that resolves to `llvm`. In a workflow it may
  also come from the step's, job's or workflow's `env:`. Where the `test`
  profile selects Cranelift too, `CARGO_PROFILE_TEST_CODEGEN_BACKEND=llvm` is
  needed as well.
- `-Zcodegen-backend=llvm` in the command's `RUSTFLAGS`. Cargo passes the
  profile's `-Z codegen-backend=cranelift` first and `RUSTFLAGS` after it, and
  rustc takes the last; a probe on the pinned nightly confirmed it.
- `--profile P`, where `P` selects `codegen-backend = "llvm"` in the
  configuration or `Cargo.toml`. `--release` counts too, unless the release
  profile selects Cranelift.
- Where only `rustflags` select Cranelift, any assigned `RUSTFLAGS` that does
  not name Cranelift again, because it replaces the source that did.

`cargo --config profile.dev.codegen-backend=...` is not accepted:
shared-actions dropped that form because `cargo llvm-cov`'s child cargo does
not inherit it.

A shared-actions `generate-coverage` step selects LLVM itself. Since v1.3.13
(2026-04-16) the action detects Cranelift in `.cargo/config.toml` or
`Cargo.toml`. It clears any inherited backend variables, then sets both profile
backends to `llvm`, so an override on the step would be ignored. The rule
accepts the step as it stands. It runs offline, so it cannot prove a pinned SHA
is at or after v1.3.13; the estate's pins all are.

A reference the policy cannot substitute (a variable with conditional or
several assignments) and a workflow value holding `${{ }}` make the verdict
`indeterminate` when they could hold the selection.

### Release builds

A release build is out of BD-007's scope when it builds the release profile,
whatever its `RUSTFLAGS`: `cargo build --release`,
`cross +stable build --release`, `--profile`, and shared-actions
`rust-build-release`, which passes `--release` itself. The Makefile `release`
target is out of scope too. The estate builds it through a pattern rule,
`target/%/$(TARGET):`, whose recipe passes
`$(if $(findstring release,$(@)),--release)`, and a static reading can neither
follow a pattern rule nor evaluate `$@`.

### Restating the fast flags

An assigned `RUSTFLAGS` value is read as everything it could expand to: its own
text, and every assignment of every variable it reaches. The estate template
assigns `RUST_FLAGS` twice (`?=`, then `:=` prepending `-D warnings`), and
netsuke puts the linker flag behind
`$(if $(filter Linux,$(BUILD_HOST_OS)), $(STANDARD_MOLD_FLAG))`. Both are read
without evaluating Make. The reading over-approximates on purpose: a flag named
under a condition or in one branch counts as restated. The rule proves the
flags are carried; BD-002 governs where the linker applies. Flags are compared
normalized, so `-C link-arg=...` and `-Z threads=8` count. A shell expansion
such as `${RUSTFLAGS:+$RUSTFLAGS }` and a Make function joined onto a flag both
end a token.

In a workflow, the linker flag is demanded only where the job's `runs-on` names
a literal Linux runner (`ubuntu-*`, `ubicloud-*`, or a `linux` label). An
expression such as `${{ matrix.os }}` proves nothing, so it is not demanded
there. `-Zthreads=8` is not demanded of a step that runs a non-nightly
toolchain, through `cargo +stable` or setup-rust's `toolchain` input.

## Spellings the rule accepts

Cargo reads `rustflags` as an array or as one space-separated string, and reads
`-C` and its value as either one token or two. The estate writes the same
linker flag three of those ways today. The policy compares normalized flags, so
all three are the same flag, and a target table keyed on an explicit Linux
triple satisfies the Linux condition exactly as a `cfg(target_os = "linux")`
key does.

Target keys are placed, not pattern-matched. A key is read as a target triple
only when it has three or four components and each is a bare identifier, and it
is placed only when exactly one of those components names an operating system
this reader knows. So `x86_64-unknown-linux-gnu` applies only on Linux,
`aarch64-apple-darwin` applies elsewhere, and `wasm32-unknown-unknown` is
placed through its architecture because it names no operating system at all. A
custom JSON target is a path rather than a triple and is left unplaced, even
when it is named after the triple it derives from; so is `i686-linux-android`,
because which component is the operating system and which the environment is
not decidable from a three-part name.

Among `cfg` expressions, `cfg(target_os = "linux")` applies only on Linux and
`cfg(unix)` applies on Linux and beyond it. Anything this reader will not
evaluate — a negation, a disjunction, a `target_env` predicate — is left
unplaced, because `cfg(not(target_os = "linux"))` contains the Linux predicate
while applying everywhere except Linux, and a substring test reads it exactly
backwards.

## Verdicts

Findings carry a three-valued `verdict`:

- `noncompliant` — the policy proved a violation.
- `indeterminate` — the policy could not prove compliance and fails closed.
  Triggers: an unparsable configuration or toolchain file, a target key that
  cannot be placed on or off Linux, an unreadable exception document, and a
  recorded exception with no pinned channel to measure it against. For BD-007
  to BD-009, also a Makefile `makeutil` refused or recovered from, an
  `include`, a computed `$(MAKE) $(VAR)` or `-C`/`-f` delegation from a gate
  target, and a selection or `RUSTFLAGS` value that cannot be read.

A repository is `compliant` only when the finding set is empty.

## Known limitations

A `makeutil` parse that recovered from a construct it does not understand is
`indeterminate` for BD-007 and BD-008, even where the Makefile is valid.
netsuke's Makefile reads `indeterminate` for BD-008 today for that reason. The
pinned `makeutil` 0.1.0 does not parse its `unexport RUSTDOC_FLAGS` directive
([makeutil#25](https://github.com/leynos/makeutil/issues/25)), and reports the
recovery at unrelated lines. makeutil
[#45](https://github.com/leynos/makeutil/pull/45), released in 0.1.1, fixes it,
and netsuke then parses completely. Everything the pinned release does parse is
compliant, and the verdict clears when concordat repins `makeutil`.

BD-009 covers steps that run cargo directly. A `make` step whose recipe assigns
no `RUSTFLAGS` still inherits the toolchain action's export, and neither clause
reports it. A `RUSTFLAGS` a step writes to `$GITHUB_ENV` is not read either.

The linker clause assumes the repository builds for Linux. A repository that
builds for no platform the linker ships on would be reported noncompliant; the
`linker_platforms` parameter exists to make that clause inapplicable, but
per-repository parameter overrides are not yet wired, so today the parameter
can only be changed in this manifest. No such repository exists in the estate.

## Layout

- `rule.yaml` — package manifest (sensor, its declared
  `input: policy-input/rust-build-defaults`, parameters, defaults).
- `policy/` — the Rego policy and its tests: `rust_build_defaults.rego` holds
  BD-001 to BD-006, `build_paths_make.rego` the Makefile half of BD-007 and
  BD-008, and `build_paths_workflows.rego` the workflow half of BD-007 and
  BD-009.
- `fixtures/repos/` — one miniature checkout per behaviour.
- `fixtures/envelopes/` — generated `policy-input/rust-build-defaults`
  envelopes.
- `fixtures/data.json` — the envelope bundle consumed by
  `conftest verify --data`.
- `fixtures/generate.py` — regenerates the envelopes by running the production
  envelope builder over each fixture checkout; rerun it whenever a fixture or
  the builder changes.

## Validation

From the repository root:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/rust-build-defaults/policy \
  --data platform-standards/canon/lint-rules/rust-build-defaults/fixtures/data.json
```

Against a real checkout:

```shell
concordat artefact rule run rust-build-defaults --repo /path/to/checkout
```
