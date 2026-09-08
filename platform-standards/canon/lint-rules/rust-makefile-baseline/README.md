# rust-makefile-baseline

Audits the governed Rust Cargo surfaces and root `Makefile` against the estate
baseline. The sensor is a Conftest/Rego policy evaluated over a
`policy-input/v1` envelope built by `concordat artefact rule run`; Makefile
facts come from the pinned `makeutil parse` command, never from reparsing Make
syntax.

## Checks

- **FP-003** (error): the root `Makefile` must exist and define each of
  the required targets (`build`, `test`, `lint` by default) unconditionally — a
  target wrapped entirely in `ifdef`/`ifeq` blocks does not count.
- **QG-001** (error): the lint gate must be binding. The policy follows the
  complete static prerequisite and literal `$(MAKE) target` closure from
  `lint`. A recursive edge is proven only for a complete sequence of literal
  `$(MAKE) target` commands joined by `&&`; a textual reference, or a command
  whose error can be masked, is not an edge. `${MAKE}` is recognized but is
  deliberately unproven until the static grammar supports that spelling. It is
  noncompliant when a reachable recipe ignores errors (`-` prefix), carries a
  `command -v`/`which` existence guard, suppresses failure with `|| true`, or
  no reachable recipe invokes the gate. The gate variable's `?=` assignment
  (`WHITAKER ?= whitaker`) is the sanctioned estate pattern — local override
  permitted, CI installs the real binary — and is deliberately not a finding
  (doctrine decision, 2026-07-19).
- **AP-001** (error, indeterminate): no `language.rust.surfaces` list was
  declared and the checkout has no root `Cargo.toml`, so Rust applicability
  cannot be established.
- **EN-001** (error, indeterminate): the policy-input envelope has an unknown
  schema version, or `cargo`/`cargo.surfaces` has an invalid shape.

## Verdicts

Findings carry a three-valued `verdict`:

- `noncompliant` — the policy proved a violation.
- `indeterminate` — the policy could not prove compliance and fails
  closed. Triggers: any `include` directive, a recovered (error-tolerant)
  parse, duplicate or double-colon `lint` rules, a conditional rule in the
  static `lint` closure, or dynamic/unproven recursive Make.

## Declaring nested Rust

Declare non-root Cargo manifests in `.concordat`. The list is authoritative, so
an explicit empty list opts the checkout out of Rust governance. A nested
surface needs a direct gate invocation qualified by `cd <directory> &&` or a
direct `--manifest-path <path>` argument. The policy does not parse shell:
surface-looking text in an `echo`, assignment, quoted value, or other command
is indeterminate rather than proof. Every declared surface must be reachable
from `lint`. When root and nested surfaces are both declared, a root-qualified
gate must remain in the root context; `cd` or `--manifest-path` for a nested
surface does not also qualify the root surface. The additive `cargo.surfaces`
envelope field preserves v0.2 schema-1 replay by falling back to a root surface
only when that field is absent; a v0.3 explicit empty list never takes that
fallback. Accepted surface paths are normalized to their repository-relative
POSIX spelling before duplicate checks and envelope emission, so `./Cargo.toml`
and `Cargo.toml` name the same governed surface. Control characters are
rejected before path construction and diagnostics.

```yaml
language:
  rust:
    surfaces:
      - path: rust/Cargo.toml
        role: workspace
```

A repository is `compliant` only when the finding set is empty.

## Layout

- `rule.yaml` — package manifest (sensor, parameters, defaults).
- `policy/` — the Rego policy and its tests.
- `fixtures/makefiles/` — one small Makefile per behaviour.
- `fixtures/envelopes/` — generated `policy-input/v1` envelopes.
- `fixtures/data.json` — the envelope bundle consumed by
  `conftest verify --data`.
- `fixtures/generate.py` — regenerates the envelopes from the Makefiles
  with the pinned `makeutil`; rerun it whenever the pin or a fixture changes.

## Validation

From the repository root:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/rust-makefile-baseline/policy \
  --data platform-standards/canon/lint-rules/rust-makefile-baseline/fixtures/data.json
```
