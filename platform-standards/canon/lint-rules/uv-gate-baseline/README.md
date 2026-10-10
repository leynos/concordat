# uv-gate-baseline

Audits a checkout's use of [uv](https://docs.astral.sh/uv/) against the vendored
`uv_gate` helper, the canonical copy of which lives at `uv_gate/uv_gate.py` in
leynos/shared-actions. The helper runs uv from a cleaned environment and the
global cache, offline first, with at most one online step; this rule holds a
repository to using it and to nothing that undermines it.

The sensor is a Conftest/Rego policy evaluated over a
`policy-input/uv-gate-baseline` envelope built by
`concordat artefact rule run`. Makefile facts come from the pinned
`makeutil parse` command, each GitHub Actions workflow and composite action
from a YAML decode, `pyproject.toml` from a TOML decode, and the helper as the
SHA-256 of its bytes. The policy never reparses any of those formats itself.

The reference wiring is one variable and recipes that go through it:

```make
UV_GATE ?= python3 scripts/uv_gate.py

prepare:
	$(UV_GATE) prepare --group dev

lint: prepare
	$(UV_GATE) run --group dev -- ruff check .

spelling:
	$(UV_GATE) tool --from 'git+https://github.com/leynos/typos-config-builder.git@v0.1.3' -- typos-config-builder gate
```

## Checks

- **UV-001** (error): `scripts/uv_gate.py` exists and its SHA-256 is one of the
  canonical digests in the `gate_digests` parameter, keyed by helper version. A
  hand-edited, re-wrapped or stale copy is drift. With no digests the check is
  indeterminate rather than clean.
- **UV-002** (error): nothing assigns `UV_CACHE_DIR` or `UV_TOOL_DIR`: not a
  Makefile variable, not an assignment inside another variable's value or a
  recipe, not an `env:` key in a workflow or composite action, and not a script
  line that sets them. The helper selects the global cache itself.
- **UV-003** (error): no recipe runs `uv run`, `uv sync`, `uv tool run` or
  `uvx` (also as `$(UV)`) except through the helper. `UV_GATE`, when assigned,
  holds exactly `python3 scripts/uv_gate.py` (the `gate_command` parameter);
  assigning it twice is indeterminate. A Makefile with an `include`, a parse
  that was only recovered, or a workflow that cannot be decoded is
  indeterminate, because recipes may be hidden.
- **UV-004** (error): `uv.lock` exists whenever `pyproject.toml` does.
- **UV-005** (error): no recipe that reaches uv passes `--refresh`,
  `--upgrade` or `-U`, runs `uv lock`, runs `uv cache clean` or `prune`, or
  retries (`retry`, `retries`, `until`). Targets in the `maintenance_targets`
  parameter (`lock` by default) may lock and upgrade; nothing may purge the
  cache or retry.
- **UV-006** (error): every tool spec the repository runs is pinned: a name
  with `==` or `@` and an exact version (not `latest`), or `git+URL@` with a
  full 40-digit commit. A release tag (`@vMAJOR.MINOR.PATCH`) is also accepted
  for the Git repositories in `release_tag_tools`. A spec that names a variable
  the policy cannot resolve to one value is indeterminate.
- **UV-007** (error): a Git dependency in `pyproject.toml` (a requirement with
  `git+`, or a `[tool.uv.sources]` Git entry) is pinned to a full commit. A tag
  or a branch can move. A published wheel (any non-Git requirement) needs no
  pin here. An undecodable `pyproject.toml` is indeterminate.
- **EN-001** (error, indeterminate): the envelope is not a
  `policy-input/uv-gate-baseline` document at schema version 1.

The rule applies when a checkout uses uv: a recipe or variable with a uv
command, a workflow or action that runs one, a `uv.lock`, or the helper itself.
A checkout with none of them is compliant with no findings.

## Adding a canonical digest

When shared-actions publishes a new `uv_gate.py`:

1. Compute its SHA-256 (`sha256sum uv_gate/uv_gate.py`) from the merged file.
2. Add it to `gate_digests` in `rule.yaml` under a new version key, and keep the
   older keys for as long as any repository still vendors those copies: a copy
   passes while its digest is listed and fails UV-001 once it is dropped.
3. Refresh `tests/fixtures/uv_gate/uv_gate.py.canon` if the behavioural tests
   should exercise the new file (a test checks that it matches a listed digest).
4. Regenerate the fixtures (`fixtures/generate.py`) and recompute the
   `manifest.yaml` digest for `rule.yaml`.

## The release-tag exception, and why

`spelling-config-baseline` (PD-007) requires typos-config-builder to be pinned
to a release tag at or above its floor, and rejects the commit form, because a
commit cannot be compared with the floor and the AGENTS.md block moves with the
release. A rule here that demanded a commit SHA for every Git tool would
contradict it. So `release_tag_tools` lists
`github.com/leynos/typos-config-builder`, and a tag is accepted for that
repository only; the floor is that rule's. Every other Git tool, including
df12-python-lints, needs a full commit. No rule requires a tag for
df12-python-lints, and concordat itself pins it by commit.

## What the checks cannot see

The policy reads Make text, not a running Make. A recipe built from a variable
with more than one assignment cannot be resolved, so a bare `$(UV)` that is not
single-valued is still judged as `uv`. The helper refuses unpinned tools and
forbidden flags at run time as well, so a spec the policy cannot see is still
caught when it runs.

## Fixtures and tests

`fixtures/generate.py` lays out one synthetic checkout per scenario, runs the
production builder over it, and records the envelope under
`fixtures/envelopes/` and in `fixtures/data.json`. The scenarios vendor a
stand-in helper, so the bundle's `gate_digests` names its digest, not the
canonical one. Run the suite from the package directory:

```shell
cd platform-standards/canon/lint-rules/uv-gate-baseline
conftest verify --policy policy --data fixtures/data.json
```
