# rust-makefile-baseline

Audits a Rust repository's root `Makefile` against the estate baseline.
The sensor is a Conftest/Rego policy evaluated over a `policy-input/v1`
envelope built by `concordat artefact rule run`; Makefile facts come from
the pinned `makeutil parse` command, never from re-parsing Make syntax.

## Checks

- **FP-003** (error): the root `Makefile` must exist and define each of
  the required targets (`build`, `test`, `lint` by default)
  unconditionally — a target wrapped entirely in `ifdef`/`ifeq` blocks
  does not count.
- **QG-001** (error): the lint gate must be binding. Noncompliant when
  the gate variable (`WHITAKER` by default) is assigned with the
  environment-overridable `?=` operator, when a lint-path recipe ignores
  errors (`-` prefix), carries a `command -v`/`which` existence guard, or
  suppresses failure with `|| true`, or when no recipe anywhere invokes
  the gate.
- **AP-001** (error, indeterminate): the checkout has no root
  `Cargo.toml`, so Rust applicability cannot be established.
- **EN-001** (error, indeterminate): the policy-input envelope has an
  unknown schema version.

## Verdicts

Findings carry a three-valued `verdict`:

- `noncompliant` — the policy proved a violation.
- `indeterminate` — the policy could not prove compliance and fails
  closed. Triggers: any `include` directive, a recovered (error-tolerant)
  parse, duplicate or double-colon `lint` rules, or gate delegation
  deeper than one prerequisite hop.

A repository is `compliant` only when the finding set is empty.

## Layout

- `rule.yaml` — package manifest (sensor, parameters, defaults).
- `policy/` — the Rego policy and its tests.
- `fixtures/makefiles/` — one small Makefile per behaviour.
- `fixtures/envelopes/` — generated `policy-input/v1` envelopes.
- `fixtures/data.json` — the envelope bundle consumed by
  `conftest verify --data`.
- `fixtures/generate.py` — regenerates the envelopes from the Makefiles
  with the pinned `makeutil`; rerun it whenever the pin or a fixture
  changes.

## Validation

From the repository root:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/rust-makefile-baseline/policy \
  --data platform-standards/canon/lint-rules/rust-makefile-baseline/fixtures/data.json
```
