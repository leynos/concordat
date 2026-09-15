# markdown-formatting-baseline

Audits the Markdown formatting and linting wiring of one checkout against the
estate baseline. The sensor is a Conftest/Rego policy evaluated over a
`policy-input/markdown-formatting-baseline` envelope built by
`concordat artefact rule run`. Makefile facts come from the pinned
`makeutil parse` command, the markdownlint configuration from a JSONC decode,
and each GitHub Actions workflow from a YAML decode; the policy never reparses
any of those formats itself.

The reference wiring is `leynos/netsuke`: `make fmt` and `make check-fmt` call
`mdtablefix` directly over the Git-selected Markdown set, `make fmt` calls
`markdownlint-cli2 --fix` directly, and CI lints Markdown through the upstream
`DavidAnson/markdownlint-cli2-action`, whose release carries the linter's whole
dependency graph so nothing is resolved from the registry at run time.

## Checks

- **FP-003** (error): the root `Makefile` must exist and define each of the
  required targets (`fmt` and `check-fmt` by default).
- **PD-002** (error): a recipe reachable from `check-fmt` must run
  `mdtablefix --check` with the select flags (`--git --include-untracked` by
  default), and its exit status must reach Make.
- **PD-003** (error): a recipe reachable from `fmt` must run
  `mdtablefix --in-place` with the select flags, directly rather than through
  the `mdformat-all` wrapper, and its exit status must reach Make.
- **PD-004** (error): a recipe reachable from `fmt` must run
  `markdownlint-cli2 --fix` directly rather than through the `mdformat-all`
  wrapper, and its exit status must reach Make.
- **PD-005** (error): `.markdownlint-cli2.jsonc` must exist and carry the
  baseline `config` entries verbatim and every baseline `ignores` glob. Further
  rules and globs may be added alongside them; an alternate configuration file
  name (`.markdownlint.yaml`, say) does not satisfy the check and is named in
  the finding.
- **PD-006** (error): CI must lint Markdown through
  `DavidAnson/markdownlint-cli2-action` pinned to a full commit SHA with
  `globs: '**/*.md'`. A `run:` step that installs or invokes
  `markdownlint-cli2`, or drives `make markdownlint`, is noncompliant in every
  workflow it appears in, and a checkout with no such action step at all is
  noncompliant.
- **EN-001** (error, indeterminate): the policy-input envelope has an unknown
  schema version.

The rule applies wherever a Markdown file exists outside version-control,
virtual-environment, dependency, build, and tool-cache directories. A checkout
with no Markdown is compliant with no findings.

## What the policy proves

The recipe checks follow the complete static prerequisite and literal
`$(MAKE) target` closure from `fmt` and `check-fmt`, exactly as
`rust-makefile-baseline` follows it from `lint`. Within that closure, the
policy expands Make variables that have exactly one unconditional, non-`define`
assignment (three substitution passes, so `MDTABLEFIX_SELECT` may itself name a
variable), then proves the tool as the command word at the start of a command
segment: `$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT)`, a literal `mdtablefix`,
a directory-qualified path, or the estate probe
`$(shell command -v markdownlint-cli2 ...)`. Make's recipe prefixes and POSIX
environment assignments before the word are allowed; a mention inside `echo`,
an assignment value, or a comment is not an invocation. The status binds when
the tool's arguments run to the end of the line or to `&&`; a following `;`,
`|`, bare `&`, `|| true`, or the `-` prefix masks it and is noncompliant.

`$(HOME)` and the other process-environment variables (`PATH`, `PWD`, `SHELL`,
`TMPDIR`, `USER`) are rewritten to their shell spelling, so a tool under
`$(HOME)/.cargo/bin/` still reads as the command word.

The policy does not parse shell. A variable it cannot resolve, a conditional
rule or `include` in the closure, a recovered parse, or a dynamic recursive
Make invocation is reported as `indeterminate` rather than guessed. Flags
hidden behind an unresolvable variable are likewise indeterminate.

Workflow facts carry no line numbers, so PD-006 findings cite line `0`. A job
that calls a reusable workflow is not inspected; when no action step exists and
such a job does, the absence is indeterminate rather than proven.

## Verdicts

Findings carry a three-valued `verdict`:

- `noncompliant` — the policy proved a violation.
- `indeterminate` — the policy could not prove compliance and fails closed.

A repository is `compliant` only when the finding set is empty.

## Layout

- `rule.yaml` — package manifest (sensor, input kind, parameters, defaults).
- `policy/` — the Rego policy and its tests.
- `fixtures/makefiles/` — one small Makefile per Makefile behaviour.
- `fixtures/markdownlint/` — markdownlint configurations per behaviour.
- `fixtures/workflows/` — workflow files per behaviour.
- `fixtures/envelopes/` — generated policy-input envelopes, one per scenario.
- `fixtures/data.json` — the envelope bundle consumed by
  `conftest verify --data`.
- `fixtures/generate.py` — lays each scenario out as a checkout and records
  the envelope the production builder produces for it; rerun it whenever the
  `makeutil` pin or a fixture changes.

The canonical `.markdownlint-cli2.jsonc` consumers copy verbatim lives at
`platform-standards/canon/lint/markdown/.markdownlint-cli2.jsonc`.

## Validation

From the repository root:

```shell
uv run python platform-standards/canon/lint-rules/markdown-formatting-baseline/fixtures/generate.py
conftest verify \
  --policy platform-standards/canon/lint-rules/markdown-formatting-baseline/policy \
  --data platform-standards/canon/lint-rules/markdown-formatting-baseline/fixtures/data.json
```
