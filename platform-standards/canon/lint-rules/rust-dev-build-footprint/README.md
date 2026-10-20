# rust-dev-build-footprint

Audits the default development build for every governed Rust workspace or
standalone crate. The rule reads repository-owned TOML files and runs a pure
Conftest policy. It never invokes Cargo, rustc, rustup, or the network, and
does not consult the operator's home Cargo configuration or environment.

```shell
concordat artefact rule run rust-dev-build-footprint --repo /path/to/checkout
```

## Checks

- **DF-001** forbids incremental compilation in the default development build.
  The base profile must disable it. Package and build-script overrides cannot
  enable it again. Cargo's global `[build].incremental` override is honoured; a
  compiler `-Cincremental=...` option can still re-enable it and is reported.
- **DF-002** requires no debug information in the development profile or its
  package and build-script overrides. Accepted disabled profile values are
  `debug = 0`, `debug = false`, and `debug = "none"`. Compiler flags that
  enable debug information, including the standalone `-g` alias, are also
  reported. Stripping final binaries does not satisfy this check: intermediate
  artefacts would still carry debug data.
- **DF-003** requires `-Zembed-metadata=no` in the build rustflags and every
  replacing target rustflags source on a supported nightly toolchain. Split
  `-Z`, `embed-metadata=no` tokens are accepted. Stable, beta, and numbered
  stable pins are exempt and must not receive this unstable option.
- **DF-000** reports unreadable files, invalid envelope shapes, and facts that
  prevent a reliable measurement. Findings are `indeterminate`, rather than a
  silent pass.

Missing base profile settings retain Cargo's defaults: incremental compilation
and debug information are enabled. The test profile inherits dev unless it
explicitly changes those settings. Explicit test, release, benchmark, and
custom-profile settings are outside this rule's scope. No mutation is declared.

## Configuration

At the governing workspace's `Cargo.toml`, or in its auto-discovered Cargo
configuration:

```toml
[profile.dev]
incremental = false
debug = 0
```

For a supported nightly, add the following flag to existing rustflags without
removing the repository's other compiler options:

```toml
[build]
rustflags = ["-Zembed-metadata=no"]
```

Each `[target.*].rustflags` source must retain the metadata option because a
target source replaces the build source. Profile-specific rustflags are
additive: they need not repeat it, but cannot enable incremental compilation,
debug information, or metadata embedding. Options within one source use the
compiler's last assignment. Profiles from Cargo configuration override the
manifest; nested configuration tables merge and arrays concatenate. Legacy
`.cargo/config` takes precedence over `.cargo/config.toml` in the same
directory.

The Rust surface resolver honours `.concordat` declarations, including nested
workspaces. Each surface is measured as a build launched from its directory,
with Cargo configuration inherited from repository ancestors. Workspace member
profiles are not authoritative; declare the governing workspace instead of a
member whose settings Cargo ignores. An exact `workspace.exclude` path makes
that crate standalone, so its own profile is audited. Exclusions are not
interpreted as glob patterns. Symlinks resolving outside the checkout are not
read. Relevant `[env]` overrides produce an indeterminate finding because a
static profile proof cannot establish their effective environment.

## Supported metadata toolchains

Support is decided from the nearest repository-owned `rust-toolchain` or
`rust-toolchain.toml`, not whichever compiler happens to be installed. The
extensionless filename wins when both exist in one directory; it accepts a
plain channel or TOML. A nearer selected pin shadows ancestor pins and their
errors, but a malformed selected pin remains indeterminate. The Rust 1.88.0
source contains the boolean `embed_metadata` option; 1.87.0 does not.[^support]
The audit therefore uses **2025-06-26** as a conservative dated-nightly support
floor. This is not a claim about the first nightly containing the option.

An older, floating, missing, or unrecognized nightly selection leaves support
unproven and produces `DF-003: indeterminate`. The rule does not incorrectly
require an unsupported flag or silently approve an unknown toolchain. The same
capability floor applies to dated nightly channels in either filename.

Disabling embedded metadata reduces duplicated metadata in `.rlib` and `.dylib`
files. The compiler documentation notes that full metadata then belongs in a
separate `.rmeta` file.[^metadata] The rule does not delete or rewrite
artefacts.

## Validation

The unit suite exercises real repository parsing and Conftest evaluation. The
behavioural suite invokes the actual CLI against synthetic checkouts. Both are
hermetic and independent of installed Rust toolchains and host load.

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/rust-dev-build-footprint/policy \
  --data platform-standards/canon/lint-rules/rust-dev-build-footprint/fixtures/data.json
```

The two recorded policy envelopes are checked against their fixture checkouts by
`test_recorded_policy_inputs_match_the_real_sensor`; changing extraction
cannot leave the Rego tests silently exercising obsolete facts.

[^support]: [Rust 1.88.0 compiler options](https://github.com/rust-lang/rust/blob/1.88.0/compiler/rustc_session/src/options.rs)
    and [Rust 1.87.0 compiler options](https://github.com/rust-lang/rust/blob/1.87.0/compiler/rustc_session/src/options.rs).

[^metadata]: [Rust Unstable Book: embed-metadata](https://doc.rust-lang.org/beta/unstable-book/compiler-flags/embed-metadata.html).
