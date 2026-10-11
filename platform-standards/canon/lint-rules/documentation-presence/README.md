# documentation-presence

Audits a checkout's documentation-presence gates: Python docstring coverage
(DG-001, DG-002) and Rust `missing_docs` (DG-003, DG-004). The Rust rules are
described under "Rust checks" below.

The Python gate is interrogate. The tool is
[interrogate](https://interrogate.readthedocs.io/), which measures docstring
*presence* (Ruff's `D` rules keep owning docstring *style*); the standard is
that every Python file a repository owns, whether package, tests, scripts,
benchmarks or code that a GitHub Actions workflow or composite action runs, is
covered, so that no definition ships undocumented. The model repositories are
[leynos/cuprum](https://github.com/leynos/cuprum) (ADR-004) and
[leynos/dev-env-rocky](https://github.com/leynos/dev-env-rocky).

The sensor is a Conftest/Rego policy evaluated over a
`policy-input/documentation-presence` envelope built by
`concordat artefact rule run`. Makefile facts come from the pinned
`makeutil parse` command; the envelope builder expands the Makefile's
variables, follows `lint` through its prerequisites and `make` commands, and
records each `interrogate` invocation it finds (paths, threshold, verbose
level, relaxing flags, whether its recipe guards against `MISSED` rows).
`pyproject.toml` (or a TOML file named by `--config`) supplies the
`[tool.interrogate]` table, and a walk of the tree supplies the Python files.
The policy reasons only over those facts.

The reference wiring is cuprum's variable plus a guarded recipe:

```make
INTERROGATE_VERSION ?= 1.7.0
INTERROGATE ?= uv tool run --from 'interrogate==$(INTERROGATE_VERSION)' interrogate
INTERROGATE_TARGETS ?= benchmarks conftest.py cuprum scripts tests

lint: interrogate

interrogate:
	@report="$$($(INTERROGATE) -vv --fail-under 100 $(INTERROGATE_TARGETS))"; \
	  status=$$?; \
	  printf '%s\n' "$$report"; \
	  test "$$status" -eq 0 || exit "$$status"; \
	  if printf '%s\n' "$$report" | grep -q '| *MISSED |'; then exit 1; fi
```

## Checks

- **DG-001** (error): a command that `make lint` reaches runs interrogate over
  every Python file the repository owns. Specifically:
  - the root `Makefile` has a `lint` target, and a recipe reachable from it
    (through prerequisites or a `make target` command) runs interrogate;
  - the threshold is at least `required_threshold` (100), from `--fail-under`
    or `fail-under` in the configuration; no threshold is a failure, because
    interrogate's default is 80;
  - no relaxing flag is used, on the command line or in `[tool.interrogate]`
    (`--ignore-init-method`, `--ignore-nested-functions`, `--ignore-private` and
    the rest, or a non-empty `ignore-regex`); ignoring `@typing.overload` stubs
    is not a relaxation, because Ruff forbids a docstring on them;
  - nothing is excluded except paths matching `exempt_pattern` (test data under
    `fixtures`, vendored and cached trees);
  - every owned Python file (all `*.py` outside the pruned trees, including
    `.github/**`) is under one run's paths and not excluded from it. Files under
    an `exempt_pattern` path are not required.
- **DG-002** (error): the run is exact. interrogate rounds its percentage to one
  decimal, so `--fail-under 100` passes a lone gap in a repository with
  thousands of definitions (cuprum passed with three). The run must print every
  definition (verbose level 2: `-vv`, `-v -v`, or `verbose = 2` in the
  configuration) and the recipe must fail on any `MISSED` row. The audit
  accepts a check that turns a match into a failing status: a negated pipeline
  (`! ... | grep -q MISSED`),
  `if ... grep ... MISSED ...; then ...; exit 1; fi`, or
  `grep ... MISSED ... && exit 1`, in the same target's recipe. A `grep` whose
  result is ignored (`|| true`, or a bare `grep`) is not a guard.
- **EN-001** (error, indeterminate): the envelope is not a
  `policy-input/documentation-presence` document at schema version 1.

DG-001 and DG-002 apply when the checkout owns at least one Python file outside
the exempt trees; a checkout with none is compliant with them and has no
findings from them. DG-003 and DG-004 apply independently, when the checkout
owns a Rust library crate (see "Rust checks").

## Rust checks

The standard is that every library crate a repository owns denies the
`missing_docs` lint, so an undocumented public item fails the build rather than
accumulating. Private items are not asked for, so
`clippy::missing_docs_in_private_items` is not part of the rule. The envelope's
`rust` section lists each library crate (a package with `src/lib.rs` or a
`[lib]` table, outside `fixtures`, `examples`, `templates`, `vendor`,
`third_party`, `patches` and `target` trees) with the three places that can set
the lint, and `clippy_runs` lists the clippy invocations `make lint` reaches.

The reference wiring is a workspace table that every member inherits:

```toml
# Cargo.toml (workspace root)
[workspace.lints.rust]
missing_docs = "deny"

# crates/core/Cargo.toml
[lints]
workspace = true
```

A single crate sets `[lints.rust] missing_docs = "deny"` itself.

- **DG-003** (error): every library crate ends with `missing_docs` at `deny` or
  `forbid`. A crate-root inner attribute beats the manifest, as in rustc, so
  the last `missing_docs` attribute at the root decides when there is one;
  otherwise the crate's own `[lints.rust]` level applies, or the workspace
  level when the crate opts in with `[lints] workspace = true` (a workspace
  table alone is not inherited). `warn` and `allow` fail. The one accepted
  exception is a crate-root `#![expect(missing_docs, reason = "...")]` for
  items a derive macro generates and the crate cannot document; an `expect`
  without a `reason` fails. `#![cfg_attr(not(test), deny(missing_docs))]`
  counts as a deny; any other `cfg_attr` condition leaves a configuration
  unchecked and is indeterminate, as is a manifest that is not valid TOML or a
  library root that is absent. Module-level and item-level suppressions hide
  items from a crate whose root denies the lint, so any `allow(missing_docs)` or
  `expect(missing_docs)` (`#[...]` or `#![...]`, also inside `cfg_attr`) in
  the library's source tree (the directory of its root, usually `src/`) needs a
  `reason`; one without fails. The reason is what the rule checks: that the
  code is generated or macro-produced (diesel's `schema.rs` is the model) is
  for the reviewer. Tests, benches and examples are separate crates and are not
  scanned.
- **DG-004** (error): a clippy invocation (`cargo clippy`, `$(CARGO) clippy`) is
  reachable from `make lint`, so the gate compiles the crates under the lint
  level. No root `Makefile`, no `lint` target, or no clippy run in the targets
  it reaches fails. A run is a simple command whose command word is `cargo`
  (also through `env`, `time` or `rustup run`) with `clippy` as its subcommand,
  so `rustup component add clippy`, a `cargo-clippy` probe and `$(MAKE) clippy`
  are not runs (the last leads to a target the reachability walk reads in its
  own right). Other parts of the line, such as a `RUSTFLAGS` value built with
  `$(if ...)` or `$(shell ...)` as the estate's Makefiles have, do not change
  which tool runs. A command reached only through a variable named for clippy
  (`$(CLIPPY_CMD)`), or a line the lexer rejects, is indeterminate. A recorded
  clippy-gate exception (below) stands in for the recipe line.

### The clippy-gate exception

A repository can have a reason to run clippy through a wrapper script, such as
femtologging's lint lanes for PyO3 and the toolchain. The audit cannot see
inside a script, so the estate's exception idiom (set by
`rust-toolchain-policy`) applies: a reasoned record in the consumer repository.
Add a section to `docs/developers-guide.md` whose heading contains "Clippy
gate" and whose body says why, naming the script in backticks:

```markdown
## Clippy gate exception

Clippy runs through `scripts/lint_rust_lanes.py`, because the PyO3 lanes need
a toolchain that a plain `cargo clippy` recipe line cannot select.
```

DG-004 accepts the record only while a named script exists inside the checkout,
mentions clippy as a cargo subcommand (`cargo clippy`, or `"clippy"` handed to
cargo as an argument), and is named by a recipe that `make lint` reaches. A
record whose scripts fail any of these is noncompliant and the finding says
which; a section that names no script is noncompliant. The audit checks no more
than that: it does not read what the script does with clippy. A recipe that
already runs `cargo clippy` needs no record, and a stale one is ignored.

The Rust rules apply when the checkout owns at least one library crate (or a
Cargo manifest the audit cannot read).

## Indeterminate verdicts

The policy reads Make text, not a running Make. It reports `indeterminate`,
never a pass, when it cannot prove a verdict: an interrogate invocation built
from a variable that does not expand to one value (assigned twice, conditional,
or a Make function other than `dir`, `notdir`, `lastword`, `firstword`,
`abspath`, `realpath` and `strip`), a recipe the shell lexer rejects, a
configuration file that is missing, not TOML or malformed, a threshold that is
not a number, or a `lint` target whose prerequisites cannot be resolved when no
run was found.

## Rule numbering

The package uses its own `DG-` prefix (documentation gate). `DP-` was already
taken by the dependency-pin actionability checks in the design document. DG-001
and DG-002 are the Python rules; DG-003 and DG-004 are the Rust rules.

## Fixtures and tests

`fixtures/generate.py` lays out one synthetic checkout per scenario, runs the
production builder over it, and records the envelope under
`fixtures/envelopes/` and in `fixtures/data.json`. Run the suite from the
repository root:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/documentation-presence/policy \
  --data platform-standards/canon/lint-rules/documentation-presence/fixtures/data.json
```

Regenerate the fixtures after changing the envelope builder or a scenario:

```shell
uv run python platform-standards/canon/lint-rules/documentation-presence/fixtures/generate.py
```

The pinned `makeutil` must be on `PATH`. The manifest digests in
`platform-standards/canon/manifest.yaml` must be recomputed after editing
`rule.yaml` or the policy.
