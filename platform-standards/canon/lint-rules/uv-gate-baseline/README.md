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
	uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" typos-config-builder gate
```

The `spelling` recipe runs the builder directly, at a release tag, because
`spelling-config-baseline` (PD-007) requires exactly that shape and the helper
refuses a Git spec that is not a full commit. See "The release-tag exception"
below.

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
  assigning it twice, or once conditionally or in a `define`, is indeterminate.
  A uv named by path (`/usr/bin/uv run`) counts as uv. A Makefile with an
  `include`, or a parse that was only recovered, is indeterminate whether or
  not other evidence shows that the repository uses uv, because `makeutil` does
  not follow includes and so cannot prove that none of the recipes reaches uv.
  A workflow that cannot be decoded is indeterminate too.
- **UV-004** (error): `uv.lock` exists whenever `pyproject.toml` does.
- **UV-005** (error): no recipe that reaches uv passes `--refresh`,
  `--upgrade` or `-U`, runs `uv lock`, runs `uv cache clean` or `prune`, or
  retries (`retry`, `retries`, `until`). Targets in the `maintenance_targets`
  parameter (`lock` by default) may lock and upgrade; nothing may purge the
  cache or retry.
- **UV-006** (error): every tool spec the repository runs is pinned: a name
  with `==` or `@` and an exact version (not `latest`), or `git+URL@` with a
  full 40-digit commit, optionally followed by a fragment such as
  `#subdirectory=packages/x` (written `\#` in a Make recipe, which counts as
  the same pin). A release tag (`@vMAJOR.MINOR.PATCH`) is also accepted for the
  Git repositories in `release_tag_tools`, but only when the tool is run
  directly (`uvx`, `uv tool run`): a tag routed through the helper is refused,
  because the helper itself rejects it. A Git requirement may carry an
  environment marker after the commit (`; python_version < '3.13'`). A spec
  that names a variable the policy cannot resolve to one value is indeterminate.
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
repository only; the floor is that rule's.

The canonical helper, though, refuses any Git spec that is not a full commit
(`validate_request` rejects it before uv is looked up). So the exception is
narrow on both sides: UV-006 accepts the tag only for a direct invocation,
UV-003 does not call a direct invocation of a listed tool at a release tag a
bypass, and a tag routed through `$(UV_GATE) tool` is refused by UV-006, since
it could never run. PD-007 needs no change: it already recognizes the direct
`uvx` recipe, and the same checkout passes both packages (a behavioural test
audits it with both). The exception names typos-config-builder alone (the
`release_tag_tools` parameter), because that is the one tool PD-007 governs:
any other tool run directly with `uvx` or `uv tool run`, tag-pinned or not, is
still a UV-003 bypass, and any other tag pin is still a UV-006 failure. Every
other Git tool, including df12-python-lints, needs a full commit; no rule
requires a tag for df12-python-lints, and concordat itself pins it by commit.

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
