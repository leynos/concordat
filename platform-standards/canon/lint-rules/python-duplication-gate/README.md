# python-duplication-gate

Audits the scope a Python repository gives the shared `df12-duplication` gate
(leynos/df12-python-lints) in its `[tool.nose]` table. The audit is read-only.
It evaluates a `policy-input/python-duplication-gate` envelope built by
`concordat artefact rule run`.

## Why

`df12-duplication` validates the table's shape, refuses a scan that selects
nothing at all and refuses a saturated report, but it cannot know repository
policy. A repository could cap the report so lower-ranked duplication passes
unadjudicated, let the scan reach its test scaffolding (which repeats on
purpose), or keep a mistyped second root that selects nothing. Those rules used
to live in a local test in one repository; they belong to the estate, so they
are enforced here instead, and no repository needs local nose test code.

## What counts as using the gate

A repository is audited when its `pyproject.toml` has a `[tool.nose]` table:
that is what `df12-duplication` itself requires, so the table cannot be present
without the gate being configured. A repository with no `pyproject.toml`, or no
such table, is not audited. A Make recipe or workflow that mentions
`df12-duplication` is not a signal; the command fails without the table anyway.

## How the facts are read

The facts come from parsers, never a text search. `pyproject.toml` is decoded
with `tomllib`. The scan is then reproduced as the detector does it: each root
is walked (never following symbolic links), and the `exclude` patterns are
matched as gitignore patterns with `pathspec`'s `GitIgnoreSpec`, so `**` and a
trailing `/` mean what git means by them.

These details were measured against nose 0.20.0, not assumed:

- Patterns are matched against each file's path **relative to the scan root**,
  not the repository root. With the root `pkg`, `unittests/**` excludes
  `pkg/unittests`, while `pkg/tests/**` excludes nothing. A file root (such as
  `openai_test_types.py`) is matched by its file name.
- Every entry is a plain exclusion. `!` is not negation (`["**", "!m.py"]`
  selects nothing) and `#` does not start a comment, so a leading `!` or `#` is
  matched literally.
- Hidden files and directories are skipped, and nothing else is pruned by name:
  `vendor`, `venv`, `node_modules` and `site-packages` are scanned unless an
  exclusion covers them.
- Python source is `.py` and `.pyi`. The detector also reads other languages,
  but this is the Python gate, so a root that selects only other languages
  selects nothing under NS-003.

`[tool.nose].exclude` is the only exclusion the rule reads. nose also honours
`.gitignore` files inside a root; the rule does not model them, so a test tree
that is hidden only by one is reported as selected. A clean checkout holds no
gitignored files, so this matters only for a dirty working tree.

## Checks

- **NS-001** (error): `top` is the integer `0`. An unset `top` leaves nose on
  its own view of 30 families, and a positive `top` reports only that many, so
  lower-ranked duplication passes unadjudicated.
- **NS-002** (error): no Python file in a test tree is selected by the scan. A
  test tree is a directory whose name is in the `test_directory_names`
  parameter (default `tests`, `test`, `unittests`, `testing`), found below a
  scan root, as the root itself, or among the root's own path components (a root
  `tests/helpers`). The judgement is per file, however the exclusion was
  written, and a test tree with no Python file has nothing to cover. The
  finding is per root: it gives the count of selected test files and one
  example, not a complete list, so rerun the audit after each fix. A root that
  is itself a test tree is reported here while it is selected, and under NS-003
  if the excludes empty it. `behaviour` is left out of the default names: in
  some repositories it is a tree of BDD step definitions the gate should scan.
- **NS-003** (error): every scan root exists and selects at least one Python
  file (`.py` or `.pyi`) after `exclude`. The detector refuses a scan that
  selects nothing overall; this holds each root to it.
- **NS-004** (error, indeterminate): `pyproject.toml` is not valid TOML, or
  `[tool.nose]` is not a table, or `roots` is not a non-empty list of
  repository-relative strings, or `exclude` is not a list of valid gitignore
  patterns, or `top` is not an integer. The scope is unknown, so no other
  verdict is given beyond a positive `top`.
- **EN-001** (error, indeterminate): the envelope has an unknown schema version
  or kind.

No clause is waivable: an uncapped, test-free, non-empty scan is what the gate
means, so the rule reads no exception data.

## Parameters

- `test_directory_names`: the directory names NS-002 treats as test trees. The
  envelope builder uses them when it decides which selected files are test
  files, so a different list changes the envelope, not only the policy.

## Where Skylos settings would go

This rule is about nose only. If a policy gap appears for `[tool.skylos]`, it
belongs in a sibling rule such as `python-deadcode-gate` with its own checks,
not here.

## Fixtures

`fixtures/generate.py` lays out one synthetic checkout per scenario and records
the envelope the production builder produces for it in `fixtures/data.json`,
which `policy/python_duplication_gate_test.rego` evaluates. Regenerate with:

```shell
uv run python \
    platform-standards/canon/lint-rules/python-duplication-gate/fixtures/generate.py
```
