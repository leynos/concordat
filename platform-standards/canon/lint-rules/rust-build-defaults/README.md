# rust-build-defaults

Audits a Rust checkout against the estate's build standard: the parallel
`rustc` frontend and the `mold` linker as **defaults**, the `rustflags` sources
held equal so neither silently replaces the other, and the Cranelift codegen
backend either configured for the development profile or refused by a recorded,
current exception.

The sensor is a Conftest/Rego policy evaluated over a
`policy-input/rust-build-defaults` envelope built by
`concordat artefact rule run`. Facts come from TOML parsers and a Markdown
heading walk, never from a textual search: `-Zthreads=8` in a comment is not a
configured flag, and a comment containing the word `channel` is not a pinned
channel. Both mistakes were made while surveying the estate for this rule,
which is why the fixtures include each of them.

## Why the configuration file and not the Makefile

The standard is a default because Cargo auto-discovers `.cargo/config.toml`, so
a bare `cargo build` gets it. That is also what makes the clause checkable: a
repository whose flags live behind a `make dev-fast` target has no such file,
and fails on the first two checks alone. The rule therefore reads the files
Cargo and rustup discover, and does not read the Makefile.

## Checks

- **BD-001** (error): every `rustflags` source carries the parallel-frontend
  flag. Applies only where `rust-toolchain.toml` pins a nightly channel;
  `-Zthreads` is a nightly flag, so demanding it of a stable pin would break
  the build rather than accelerate it.
- **BD-002** (error): a target table that applies on Linux carries the linker
  flag, and no source that reaches beyond Linux names it. `mold` ships for
  Linux alone, so naming it unconditionally — or under `cfg(unix)`, which
  macOS builds also take — breaks the platforms it reaches. A target key this
  policy cannot place is `indeterminate`, and while any key is unplaced the
  policy will not conclude that the linker is configured nowhere: the
  unplaceable source might be the Linux table.
- **BD-003** (error): the sources are repeated, not merged. Cargo selects a
  single `rustflags` source rather than merging them — a matching `[target.*]`
  table replaces `[build] rustflags` outright — so a flag named in one source
  and not another vanishes on the platform the other one matches. The linker
  flag is the deliberate exception and is governed by BD-002.
- **BD-004** (error): the backend is the development-profile default, or the
  repository records an exception. Both states are accepted; a repository with
  neither is noncompliant. A package override beneath a profile is not the
  profile's default: Cargo applies it to the named package alone, leaving
  every other development build on the backend it had. An exception document
  the filesystem refused to read decides nothing, and is `indeterminate`.
- **BD-005** (error): a backend selection Cargo cannot honour, or one the
  estate has not adopted. A profile key without `[unstable] codegen-backend =
  true` is refused by Cargo; an `[unstable]` key under a non-nightly pin stops
  the file loading for every consumer.
- **BD-006** (error): the recorded exception names the pinned toolchain
  channel. An exception measured on an older channel no longer covers the
  toolchain the repository builds with, which is what keeps the recorded state
  from drifting quietly behind the pin. Clearing it does not require a fresh
  measurement: a line in the exception section saying what the pin is now,
  whether a measurement was taken on it, and the date any deferral runs to is
  a true statement of the recorded state and satisfies the clause. With no
  channel pinned at all the finding is `indeterminate`.
- **CF-001** (error, indeterminate): `.cargo/config.toml` exists but could not
  be read, parsed, or used, so no clause that reads it can be decided. This
  includes a configuration Cargo itself refuses: `rustflags = ["-Zthreads=8",
  42]` makes Cargo exit, and dropping the offending member to read the rest
  would pass a repository that cannot build at all.
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
placed through its architecture because it names no operating system at all.
A custom JSON target is a path rather than a triple and is left unplaced, even
when it is named after the triple it derives from; so is
`i686-linux-android`, because which component is the operating system and which
the environment is not decidable from a three-part name.

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
  recorded exception with no pinned channel to measure it against.

A repository is `compliant` only when the finding set is empty.

## Known limitation

The linker clause assumes the repository builds for Linux. A repository that
builds for no platform the linker ships on would be reported noncompliant; the
`linker_platforms` parameter exists to make that clause inapplicable, but
per-repository parameter overrides are not yet wired, so today the parameter
can only be changed in this manifest. No such repository exists in the estate.

## Layout

- `rule.yaml` — package manifest (sensor, its declared
  `input: policy-input/rust-build-defaults`, parameters, defaults).
- `policy/` — the Rego policy and its tests.
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
