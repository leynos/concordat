# spelling-config-baseline

Audits a checkout's spelling gate against the design of
[typos-config-builder](https://github.com/leynos/typos-config-builder) 0.1.x.
The sensor is a Conftest/Rego policy evaluated over a
`policy-input/spelling-config-baseline` envelope built by
`concordat artefact rule run`. Makefile facts come from the pinned
`makeutil parse` command, each GitHub Actions workflow from a YAML decode,
`typos.local.toml` from a TOML decode, `.gitignore` as its lines, and vendored
machinery as the paths that match the package's globs. The policy never
reparses any of those formats itself.

The reference wiring is `leynos/cuprum`: `make spelling` runs one pinned
command,

```shell
uv tool run --python 3.14 \
  --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.3" \
  typos-config-builder gate --repository . --scope all
```

which regenerates `typos.toml` from the live shared dictionary, runs the
builder's own pinned Typos over the tracked files, and enforces the shared
phrase corrections Typos cannot express.

## Checks

- **PD-007** (error): a recipe reachable from `spelling` runs
  `typos-config-builder gate`, through `uvx` or `uv tool run` with `--from`
  naming the `leynos/typos-config-builder` repository at a release tag
  (`vMAJOR.MINOR.PATCH`) at or above the floor (`v0.1.3` by default), and its
  exit status reaches Make. The floor is the first release that publishes the
  AGENTS.md block PD-013 compares, so the pin and the block move together. A
  commit pin is immutable but is not a release, and cannot be compared with the
  floor. A branch, another repository, an unpinned `typos-config-builder`, the
  default command (which renders `typos.toml` but neither runs Typos nor checks
  phrases), `--check`, and a direct Typos run on the same path are each
  reported in their own right.
- **PD-008** (error): the Makefile assigns none of the legacy pins
  (`TYPOS_VERSION`, `PATHSPEC_VERSION`, or a builder commit variable) and
  defines none of the legacy helper targets (`spelling-helper-test`,
  `spelling-phrase-check`, `spelling-config`).
- **PD-009** (error): the tree carries no vendored machinery: no
  `scripts/generate_typos_config.py`, no `scripts/typos_rollout*.py`, no
  phrase-check script, and none of their tests. The list is the
  `vendored_paths` parameter.
- **PD-010** (error): no workflow step runs Typos directly (a `typos` run, or
  the `crate-ci/typos` action), drift-checks `typos.toml` (the builder's
  `--check`, or `git diff` over the file), runs vendored machinery, or drives a
  legacy helper target. CI runs `make spelling`.
- **PD-011** (error): `.gitignore` ignores `.typos-oxendict-base.json` and
  `.typos-oxendict-base.toml`, the builder's untracked cache. As in Git, the
  last matching pattern decides: a root-anchored line
  (`/.typos-oxendict-base.json`), a `**/` prefix, or a glob such as
  `.typos-oxendict-base.*` ignores the file, and a later `!` pattern that
  matches it unignores it again. Braces are literal, as in Git, so
  `.typos-oxendict-base.{json,toml}` ignores neither file.
- **PD-012** (error): `typos.local.toml` exists and declares `schema = 1`,
  even when it holds nothing else.
- **PD-013** (error): `AGENTS.md` carries exactly one spelling block between
  `<!-- typos-config-builder:agents-md:start -->` and
  `<!-- typos-config-builder:agents-md:end -->`, and the text between the
  markers matches the text typos-config-builder publishes at
  `docs/agents-md-spelling.md` for the pinned release, with whitespace
  normalized. The texts are the `agents_md_blocks` parameter, keyed by release
  tag: a pin compares with the newest text at or below it, a pin older than
  every text compares with the earliest (the block is policy text rather than
  builder behaviour), and a gate whose pin cannot be proven compares with the
  newest. A `make spelling` command or `typos.toml` named outside the markers
  duplicates the block and is reported by line; `typos.local.toml`, where a
  repository may document its own exceptions, is not matched, and duplicates
  are judged only when exactly one well-formed block exists.
- **EN-001** (error, indeterminate): the envelope is not a
  `policy-input/spelling-config-baseline` document at schema version 1.

The rule applies when a checkout has any part of a spelling setup: a `spelling`
target, a `typos.toml` or `typos.local.toml`, vendored machinery, or a workflow
that mentions Typos. A checkout with none of them is compliant with no findings.

## The design this enforces, and issue #118

Issue #118 proposed a committed `typos.toml` checked for drift against the
regenerated one. typos-config-builder 0.1.0 decided the opposite, and this rule
enforces the builder's decision (its `docs/migration-guide-0-1-0.md`,
`docs/migration-guide-0-1-2.md`, and `docs/typos-config-builder-design.md` at
`v0.1.2`). The shared dictionary is live, so a tracked `typos.toml` changes
whenever shared policy changes, and a drift check would fail every consumer on
every dictionary edit. `gate` therefore rewrites `typos.toml` on every run, and
PD-007 and PD-010 refuse `--check` in the Makefile and in CI.

The issue's comment asked that phrase corrections be preserved and the shared
base be selected immutably:

- **Phrase corrections are preserved** because the builder owns them. `gate`
  runs `check-phrases` over every tracked file after Typos (PD-007 requires
  `gate`, not the render-only default command), and no vendored generator
  remains to drop the `[phrases.corrections]` table as the frozen
  `typos_rollout.py` did (PD-009).
- **The pinned code is immutable, but the shared dictionary deliberately is
  not.** PD-007 requires a release tag of the builder, so the generator,
  merger, renderer, Typos version, and phrase checker a repository runs are
  fixed until the repository bumps the pin. The dictionary, by design, is the
  live `main` of `leynos/agent-helper-scripts`: an edit reaches every consumer
  on its next run, with no consumer change and no builder release. The builder
  offers `--source` for a repository that needs another authority; this rule
  does not require it, because the estate's decision is that dictionary edits
  apply everywhere at once.

## What the policy proves

The recipe check follows the complete static prerequisite and literal
`$(MAKE) target` closure from `spelling`, exactly as
`markdown-formatting-baseline` follows it from `fmt`. Within that closure, the
policy expands Make variables that have exactly one unconditional, non-`define`
assignment over three passes, joining continuation lines as Make does. A
variable assigned under conditions whose every value is empty or only
environment assignments (cuprum's `LOCAL_TOOL_ENV`) can only prefix a command
with its environment, and is erased for the proof. The builder is then proved
as the command at the start of a command segment, after Make's recipe prefixes,
environment assignments, and the uv runner with its options; the `--from` spec
is read from the runner's options. The status binds when the builder's
arguments run to the end of the line or to `&&`; a following `;`, `|`, bare `&`,
`|| true`, or the `-` prefix masks it.

Typos counts as run directly wherever it is a word on a recipe or `run:` line:
the command word, an argument to `xargs` or `env`, or a tool a uv runner names
(`uv tool run typos@1.48.0`). `typos.toml` and `typos-config-builder` are other
words, and a line that only prints (`echo`, `printf`) is a mention.

A variable the policy cannot resolve, a conditional rule or `include` in the
closure, a recovered parse, or a dynamic recursive Make invocation is
`indeterminate` rather than guessed, as is a workflow or overlay that cannot be
decoded. A workflow that cannot be decoded might run Typos, so when it is the
checkout's only possible spelling evidence the repository's scope is itself
`indeterminate`, never passed as out of scope. Workflow facts carry no line
numbers, so PD-010 findings cite line `0`.

## Verdicts

Findings carry a three-valued `verdict`:

- `noncompliant`: the policy proved a violation.
- `indeterminate`: the policy could not prove compliance and fails closed.

A repository is `compliant` only when the finding set is empty.

## Layout

- `rule.yaml`: package manifest (sensor, input kind, parameters, defaults).
- `policy/`: the Rego policy and its tests.
- `fixtures/makefiles/`, `fixtures/workflows/`, `fixtures/overlays/`,
  `fixtures/gitignores/`: one small file per behaviour.
- `fixtures/envelopes/`: generated policy-input envelopes, one per scenario.
- `fixtures/data.json`: the envelope bundle consumed by
  `conftest verify --data`.
- `fixtures/generate.py`: lays each scenario out as a checkout and records
  the envelope the production builder produces for it; rerun it whenever the
  `makeutil` pin or a fixture changes.

## Validation

From the repository root:

```shell
uv run python platform-standards/canon/lint-rules/spelling-config-baseline/fixtures/generate.py
conftest verify \
  --policy platform-standards/canon/lint-rules/spelling-config-baseline/policy \
  --data platform-standards/canon/lint-rules/spelling-config-baseline/fixtures/data.json
```
