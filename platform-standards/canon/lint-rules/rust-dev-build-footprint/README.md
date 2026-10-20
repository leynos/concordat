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
- **DF-003** requires Cargo's native metadata control on supported nightly
  pins. Old Cargo uses `[unstable] no-embed-metadata = true`; modern Cargo uses
  `[unstable] embed-metadata = false`. Raw `-Zembed-metadata` rustflags are
  rejected: disabling it directly can leave build-script dependencies with
  incomplete metadata, while enabling it contradicts the native setting.
  Stable, beta, and numbered stable pins are exempt from the native control;
  their Cargo ignores `[unstable]` configuration.
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

For dated nightly pins from **2025-06-26 through 2026-07-12**, put this native
Cargo setting in the effective repository Cargo configuration:

```toml
[unstable]
no-embed-metadata = true
```

For dated nightly pins from **2026-07-29 onward**, use the renamed control:

```toml
[unstable]
embed-metadata = false
```

Cargo coordinates full `.rmeta` output with this control. Passing the raw
compiler disabling flag alone is unsafe for build-script dependencies and
cannot establish compliance. Remove raw `-Zembed-metadata` tokens from build,
target, and profile rustflags; keep unrelated compiler options. The native
setting covers all targets, so target rustflags do not duplicate it.

Options within each rustflags source retain the compiler's last assignment.
Profiles from Cargo configuration override the manifest; nested configuration
tables merge and arrays concatenate. Legacy `.cargo/config` takes precedence
over `.cargo/config.toml` in the same directory.

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
errors, but a malformed selected pin remains indeterminate.

The capability windows follow Cargo's original native control and subsequent
rename/configuration changes.[^cargo-history] The transition from **2026-07-13
through 2026-07-28** remains unproven. Earlier, floating, missing, or
unrecognized nightly selections also produce `DF-003: indeterminate`. No
compiler probe or pin upgrade is performed by the audit.

Disabling embedded metadata reduces duplicated metadata in `.rlib` and `.dylib`
files. Cargo ensures full metadata remains available separately for dependent
compilations.[^metadata] The rule does not delete or rewrite build artefacts.

The internal policy-input envelope is version **2** because native Cargo facts
replace the former raw-flag compliance contract. Version 1 input fails closed
with `DF-000`; other rule envelopes are unchanged.

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

[^cargo-history]: Cargo changes
                  [#15378](https://github.com/rust-lang/cargo/pull/15378),
    [#17149](https://github.com/rust-lang/cargo/pull/17149), and
    [#17266](https://github.com/rust-lang/cargo/pull/17266).

[^metadata]: [Cargo unstable features: embed-metadata](https://doc.rust-lang.org/nightly/cargo/reference/unstable.html#embed-metadata).
