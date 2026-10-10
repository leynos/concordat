# Concordat developers' guide

This guide documents concordat's internal boundaries: the module contracts that
other modules, tests, and the CLI rely on. It complements
[`docs/users-guide.md`](users-guide.md), which describes CLI behaviour from an
operator's perspective, and [`docs/concordat-design.md`](concordat-design.md),
which specifies the broader estate-audit architecture. Where behaviour
described here is planned rather than shipped, that is called out explicitly;
everything else is derived from the current source.

## Development environment and gates

`uv sync --group dev` installs the development dependency group. The group is
declared in `pyproject.toml` under `[dependency-groups]` as `dev`, and pulls in
pytest, pytest-xdist, pytest-bdd, pytest-asyncio, pytest-mock, ruff, pyright,
pytest-timeout, betamax, hypothesis, textual, and the pinned
`df12-python-lints` plugin at immutable commit
`9c835f35b0f1690597ade799c9c6a30bc5922959` (lock metadata version 0.1.0). The
`Makefile`'s `build` target runs `uv sync --group dev` as part of setting up
the virtual environment.

`make lint` runs the source and snapshot checks sequentially. Ruff provides the
fast source-wide style and correctness pass, including preview, asynchronous,
and NumPy-docstring rules. A pinned Pylint then runs the selected Lading policy
on uv-managed PyPy 3.12; a module that PyPy cannot parse fails the lint rather
than being skipped. A separate CPython 3.14 invocation loads every diagnostic
from the `df12-python-lints` pin, while retaining Concordat's Python 3.13
semantic baseline for version-gated checks. `ambrleaks`, provisioned from the
same immutable release, scans the test tree for unredacted values in Syrupy
snapshots. The spelling subtarget runs the shared `typos-config-builder` gate,
which regenerates `typos.toml` from the live shared dictionary and the
`typos.local.toml` overlay before checking en-GB-oxendict spelling; because the
dictionary is live, `typos.toml` is never drift checked in continuous
integration. Finally, the blocking Skylos 4.33.2 dead-code scan covers only the
production `concordat` and `scripts` packages and excludes `tests`, so
test-only references do not keep production symbols live. The separate df12
process prevents its CPython dependency from changing the PyPy Pylint baseline.

Treat each Skylos report as a dead-code candidate. Remove confirmed dead code.
For a verified dynamic runtime entry point, add a precise rule in
`[tool.skylos.dead_code]` with the fully qualified symbol, its actual type, and
the runtime caller. When an entry-point rule cannot describe the boundary, add
a narrow named exception instead:

```shell
make skylos-allow SYMBOL=symbol REASON="Loaded by plugin registry"
```

The target requires both values to contain at least one non-whitespace
character and records the reason in the version-controlled Skylos documented
allow list. Use `SYMBOL`, not `NAME`: WSL injects `NAME` with the host name.
Record the verified caller and its evidence in the reviewing change. Do not add
bulk or unexplained exceptions; remove an exception when its runtime boundary
disappears.

The Makefile uses `$(SKYLOS_CLI)` only for subcommands and keeps scan-only
options such as `--config-file` in `$(SKYLOS)`. This keeps
`skylos whitelist <symbol> --reason <reason>` in the command order that Skylos
requires.

### Markdown formatting and linting

`make fmt` and `make check-fmt` call the two Markdown tools directly. The
`mdformat-all` wrapper is gone: it hid both tools behind a script whose flags
no audit could read, and `markdown-formatting-baseline` (PD-003, PD-004)
forbids it estate-wide.

Two tools must be on `PATH` before either target runs:

- `mdtablefix` 0.6.0 or later. `--check` and `--git` first appear in that
  release, and continuous integration installs exactly that version from
  `MDTABLEFIX_VERSION` in `.github/workflows/ci.yml`. An older build accepts
  neither flag and the recipe fails at once.
- `markdownlint-cli2`, found through `MDLINT`. The variable probes `PATH`
  first and falls back to the Bun install location, so a Bun-installed linter
  needs no configuration.

Both targets select their files the same way, through
`MDTABLEFIX_SELECT = --git --include-untracked`. That is every Markdown file
Git tracks, plus the untracked files Git does not ignore, so a document is
formatted before it is ever staged and no hidden directory is missed. The
earlier wrapper walked the tree with `fd` and silently skipped `.rules/`.
`MDTABLEFIX_RULES` carries the formatting rules themselves (`--wrap`,
`--renumber`, `--breaks`, `--ellipsis`, `--fences`) and is identical in both
targets, so what `fmt` writes is what `check-fmt` accepts.

The rule requires both flag sets, select and rewrite, on every `mdtablefix`
invocation (parameters `mdtablefix_select_flags` and `mdtablefix_rule_flags`).
A check that omitted the rewrite flags would pass files that `fmt` still
changes, which is how the formatting gate can be green while the estate's
80-column rule goes unenforced.

`fmt` rewrites: `mdtablefix --in-place` then `markdownlint-cli2 --fix`.
`check-fmt` verifies with `mdtablefix --check` and rewrites nothing. Running
`--in-place` anywhere on the `check-fmt` path is itself a PD-002 finding,
because a target asked to verify would be mutating the tree.

`make markdownlint` runs the linter over `**/*.md` for the rule checks that
`--fix` cannot repair. Continuous integration does not call it: PD-006 mandates
that the only Markdown lint in a workflow is
`DavidAnson/markdownlint-cli2-action` at a full commit SHA with
`globs: '**/*.md'`. The action's release carries the linter's whole dependency
graph, so nothing resolves from the npm registry at run time and Dependabot
owns the pin. Both the local gate and the action read the repository's
`.markdownlint-cli2.jsonc`, so the two lint the same files under the same rules.

That file must set `"gitignore": true` (PD-005, since rule 0.3.0).
`markdownlint-cli2` lints every file its glob reaches unless told to honour
`.gitignore`, so without the key `make fmt`'s `--fix` rewrites Git-ignored
Markdown that `mdtablefix --git` selected out. The policy accepts only the
boolean `true`; `fixtures/markdownlint/no_gitignore.jsonc` and
`gitignore_false.jsonc` pin the absent and `false` cases.

`tests/unit/test_repository_markdown_wiring.py` runs the shipped rule over this
checkout, so a change to the `Makefile`, the markdownlint configuration, or the
CI workflow that breaks the mandate fails in this repository's own test suite.

### Coverage workflow contract

Pull-request jobs generate coverage with the baseline written by `main` and
enforce the local ratchet. They do not invoke CodeScene and must not expose
`CS_ACCESS_TOKEN`. `.github/workflows/coverage-main.yml` runs only on pushes to
`main`; it advances the coverage baseline and is the sole CodeScene publisher,
using `mode: upload`.

Four properties of that topology fail quietly rather than loudly, so
`tests/unit/test_coverage_topology_contract.py` asserts them.

- **Pull-request lanes keep their report local.** The shared coverage action
  defaults `publish-artefact` to `"true"`, so a lane that omits the input
  publishes the report and the repository grows a second publisher of the same
  artefact. The pull-request lane sets it to `'false'`; the contract reads the
  effective value, so omitting it fails.
- **Nothing a pull request runs reaches CodeScene.** No workflow a pull
  request can run names `CS_ACCESS_TOKEN` anywhere (a `run` body, an action
  input, `env` at any scope, or `secrets:` forwarding), invokes the uploader,
  or names `codescene.io`. The search reads every key and scalar of the
  document, case-folded, so neither a workflow-level `defaults.run.shell` nor a
  callee's `workflow_call` secret declaration escapes it. No such workflow
  forwards `secrets: inherit` to a remote reusable workflow either: the
  contract cannot read a remote workflow, so inheriting into one hands the
  credential over without its name appearing here.
- **The publisher's upload is guarded on the ref as well as the credential.**
  The push filter constrains the push event only. A `workflow_dispatch` selects
  its own ref, so without `github.ref == 'refs/heads/main'` on the step, a
  dispatch from a feature branch would publish that branch's coverage as the
  trunk's. The contract splits the condition on `&&` and requires the ref
  comparison and the credential check as whole conjuncts. It refuses any
  unquoted `||`, which binds loosest: appending
  `|| github.event_name == 'workflow_dispatch'` leaves the ref comparison in
  the text and makes every conjunct optional, so a substring check passes it.
  Only a disjunct hidden after an extra conjunct proves the refusal: in
  `<guards> && github.actor != 'x' || <dispatch>` every required conjunct stays
  whole, so the split alone would accept it. The credential conjunct is
  `steps.codescene-token.outputs.available == 'true'`, read from an earlier
  step in the same job whose sole command, with no `if:`, is
  `echo "available=${{ secrets.CS_ACCESS_TOKEN != '' }}" >> "$GITHUB_OUTPUT"`.
  GitHub evaluates that expression before the shell starts, so the token enters
  no process. Deleting, altering or skipping the check would leave the upload
  skipping on every run without failing anything, so the contract requires the
  exact command. A script reading the token from `env` is refused for a second
  reason: checked-out branch code would then hold the secret. The upload step
  passes `${{ secrets.CS_ACCESS_TOKEN }}` straight to the action's
  `access-token` input, and no `env` block in the publisher, at workflow, job
  or step scope, names the token. Those two places, the check command and
  `access-token`, are the only ones in the publisher that may name the token at
  all: a `run` body interpolating it puts the secret in a shell process that
  checked-out code can read, and another action's input hands it across a
  boundary nobody approved. The upload action is composite and hands its step's
  `env` to every nested step it runs, and it binds the token itself from the
  input. A Dependabot automerge made with `GITHUB_TOKEN` fires no push, so such
  a merge publishes nothing until the next push to `main`; this is a known
  exception, not a gap to fill with a schedule.
- **The publisher serializes its baseline writes.** Two pushes to `main` in
  quick succession would otherwise race to write the ratchet baseline that
  every pull request is measured against, and the loser's partial write is the
  one a pull request might restore. Runs are not cancelled
  (`cancel-in-progress: false`): a cancelled publisher abandons both its upload
  and its baseline write. For triggered runs (a push, or a dispatch), a running
  publisher finishes and a newer one waits behind it, replacing any older
  pending run, so the newest triggered run's baseline wins. This is not a
  queue. A manual "Re-run jobs" on an older run is an operator action outside
  that ordering: the re-run keeps its original commit, so it republishes that
  commit's coverage and baseline until the next push supersedes it. Any value
  but an absent one or a literal false counts as cancelling, at the workflow
  level or on a job. The group must resolve the same for every run on `main`,
  whatever the event, because runs in different groups do not wait for each
  other. It may interpolate only `github.workflow`, `github.ref`,
  `github.ref_name` and `github.repository`. A group built on `github.run_id` or
  `github.sha` gives each run a group of its own. One built on
  `github.event_name` separates a dispatch from a push, so an earlier dispatch
  could upload older coverage after a newer push.

Both lanes invoke the coverage action at one pin, and the contract requires it.
The publisher writes the baseline the pull-request lanes are measured against,
so a lane on a different pin can fail a ratchet for a change in the measurement
rather than in the diff. Move the two together.

The contract enumerates workflows rather than naming these two files, and reads
the trigger mapping under both the `on` key and the boolean `True` that
unquoted YAML 1.1 produces. A reader that knows only the string key finds no
triggers, and every clause that filters workflows by trigger then ranges over
an empty set. Every workflow that invokes the uploader is counted, and exactly
one may. The contract then requires that one to push to `main` alone *and*
serve no pull request. Counting first means a second uploader cannot escape the
count by pushing to another branch. The filter must name `main` alone, since
`[main, release]` would publish the release branch as the trunk. Serving no
pull request matters because `ci.yml` declares a push trigger too.

"What a pull request can run" is the transitive closure of the
pull-request-triggered workflows through local reusable-workflow calls. A
workflow declaring only `workflow_call` never matches a pull-request trigger,
yet runs for one whenever a pull-request job calls it, and `secrets: inherit`
hands it the credential. A call is local when it names a path under
`.github/workflows/`, matched by shape rather than by a list of prefixes, after
removing a leading `./` or the documented `$/`. A local path carrying `@ref` is
refused, as is a call to a local workflow that does not exist, so the closure
cannot stop short silently. A workflow declaring its triggers under both `on`
and the boolean `True` is refused too: GitHub merges them, and a reader that
picks one is blind to the other. The readers live in
`tests/unit/coverage_topology_support.py`, with the upload guard and credential
readers in `tests/unit/coverage_credential_support.py`.
`tests/unit/test_coverage_topology_readers.py` and
`tests/unit/test_coverage_credential_readers.py` drive them against synthetic
workflows, because this repository's own files comply and so cannot show that a
reader sees the hazard it exists for.
`tests/unit/test_coverage_topology_properties.py` generates upload conditions
(any conjunct order, any spacing, operators inside quoted strings, and an
unquoted disjunct at any position) and requires the guard reader to agree with
the generator's verdict.

### Gate tool provisioning

Two lanes run the whole pytest suite: `ci.yml`'s `lint-test` job on pull
requests and `coverage-main.yml`'s `coverage-upload` job on pushes to `main`.
Parts of the suite shell out to external programs, so a lane that runs the
suite must also install them. `coverage-main.yml` once installed only the
Makefile parser, and every push to `main` failed in three rule tests with
`conftest is required but was not found on PATH`; no pull request could see it,
because the lane that reports a defect is not the lane that suffers from it.

`tests/unit/test_gate_tool_provisioning_contract.py` holds the contract,
reading the repository through `tests/unit/gate_provisioning_support.py`, whose
recognizers are driven against synthetic input in
`tests/unit/test_gate_provisioning_recognizers.py`. The contract derives the
required tool set from the package rather than restating it, by reading the
`<tool> is required but was not found on PATH` messages that `concordat`
raises, so a newly required tool is covered as soon as it is introduced. It
enumerates the suite lanes from `.github/workflows`, so a workflow added later
is covered on the day it appears. Provisioning is recognized from the shape of
an install command and not from a step's name, so renaming or merging steps
cannot void it. A package manager's `install` verb is that shape, and
`uv run scripts/install_release_binary.py install makeutil` matches it: `uv`
runs a command whose `install` operand names the tool. A tool must be installed
before the step that runs the suite, since installing it afterwards fails
exactly as the publisher did, and every lane must install a shared tool at the
same specification so the two cannot drift apart. A lane that installs with Go
must also run the shared Go setup action, at the same pin, because the runners
carry no toolchain the install can rely on. `make test` lists the same tools as
prerequisites, so the local gate fails by name rather than through unrelated
rule tests.

Add a new external tool in three places together: the package's missing-tool
message, the install step in every suite lane, and the `test` target's
prerequisites.

Both lanes measure coverage on the interpreter they give `setup-python`, and
each coverage step sets `UV_PYTHON` to that version. The declaration alone is
not enough: the shared action builds `.venv-coverage` with `uv venv`, which
takes the newest interpreter uv can find, and `ci.yml`'s tool installs leave a
managed Python 3.14 behind. Until the pin, the pull-request lane measured on
3.14 while the publisher measured on 3.13. Slipcover counts about 1,300 fewer
valid lines on 3.14, so every pull request read roughly 2.5 points below the
baseline and failed the ratchet with no change in coverage.
`tests/unit/test_coverage_interpreter_contract.py` holds each coverage step's
effective `UV_PYTHON` equal to its job's `setup-python` version. Change the two
together.

Only that one interpreter measures coverage, so behaviour that differs between
supported interpreters can still escape both lanes. `concordat.rules` probes
the filesystem through `concordat/rules/fs_probe.py` for exactly this reason.

### Filesystem applicability probes

`concordat/rules/fs_probe.py` decides, in one place, which filesystem failures
count as absence. It offers two shapes over that one decision. `probe_file`
returns a `FileProbe` carrying the filesystem's diagnostic, for callers that
fail a policy clause closed with the reason.
`regular_file_exists(path, *, operation)` raises `OperationalRuleError`
instead, with the caller's `operation` identifier and the path as `resource`,
for callers whose boundary is an operational error. Both report `False` for an
absent path and for a non-regular file, and neither reports an unreadable file
as an absent one, because absence is evidence that a rule does not apply while
an inspection failure is evidence of nothing.

Two callers use it. `concordat.rules.rust_surfaces.root_cargo_toml_exists`
probes the root Cargo manifest under `resolve-rust-surfaces`, and
`build_envelope` probes the root `Makefile` under `parse-makefile`. Do not call
`Path.is_file` or `Path.exists` for applicability evidence: the `pathlib`
probes conflate the two facts, and which failures they swallow differs between
supported interpreters.

## Public runtime boundary

`concordat.hello` is the public greeting entry point. At runtime it selects
`_concordat_rs.hello` when the optional Rust extension is installed and falls
back to `.pure.hello` only when importing `_concordat_rs` itself raises
`ModuleNotFoundError`. A missing dependency reported while importing the native
extension is re-raised, so packaging and environment failures remain visible.
`Hello` in `concordat.runtime` is an internal typing alias, not part of the
public API.

## Platform-standards inventory mutation boundary

`_apply_inventory_change` defines the sequencing contract for inventory pull
request changes:

1. Call the supplied mutation with the configured inventory path and repository
   slug.
2. If it reports no change, return without creating a commit or running
   validation.
3. Commit the changed inventory.
4. Run the validation boundary (`tofu fmt`, its check mode, `tflint`, and
   `tofu validate`).

The helper returns `True` only after a changed inventory has been committed and
validated.

## Canonical artefact TUI refresh boundary

The canonical-artefact TUI's `action_refresh` method recomputes comparisons
from the manifest and published checkout using the existing filters, updates
the comparison state, and clears and repopulates the mounted `DataTable` in
place. Refresh therefore reuses the existing application and table rather than
replacing either widget; the refresh and sync key bindings depend on that
mounted table.

The type checker is pinned, not resolved at run time. `Makefile` declares
`TY_VERSION ?= 0.0.65` and `TY := uv tool run ty@$(TY_VERSION)`; the
`typecheck` target invokes `$(TY)` throughout. An unpinned `ty` meant CI and a
local checkout could run different versions of the tool and disagree about
which diagnostics were real; pinning the version in one Makefile variable, and
having every invocation read it from there, closes that gap. `ty` is
deliberately absent from the Makefile's `TOOLS` list — the CLI tools whose
presence `make` verifies with `command -v` — because it is fetched on demand at
the pinned version via `uv tool run` instead of being expected to already be on
`PATH`.

## XDG layout and owner namespaces

`concordat/xdg.py` is the single source of truth for where concordat reads and
writes. Three roots are resolved from the XDG base-directory environment
variables, each falling back to the conventional default when the variable is
unset, relative, or empty (the XDG specification requires relative base
directories to be ignored):

- `config_root()` — `$XDG_CONFIG_HOME/concordat`, falling back to
  `~/.config/concordat`.
- `cache_root()` — `$XDG_CACHE_HOME/concordat`, falling back to
  `~/.cache/concordat`.
- `state_root()` — `$XDG_STATE_HOME/concordat`, falling back to
  `~/.local/state/concordat`.

Everything owner-specific lives under an `owners/<owner>/` namespace beneath
one of these roots:

- `owner_config_dir` / `owner_config_path` — the owner's estate
  configuration, `owners/<owner>/config.yaml` under the config root.
- `owner_credentials_path` — `owners/<owner>/credentials.yaml` (see
  [Credentials](#credentials)).
- `owner_cache_dir` / `owner_estates_cache_dir` — `owners/<owner>/estates`
  under the cache root, holding cloned estate repositories.
- `owner_state_dir` / `owner_runs_dir` — `owners/<owner>/runs` under the
  state root, holding throwaway OpenTofu working trees.

The OpenTofu provider plugin cache (`tofu_plugin_cache_dir`,
`$XDG_CACHE_HOME/concordat/tofu/plugin-cache`) is the one cache path that is
*not* owner-namespaced: provider binaries are the same regardless of which
owner's estate is being planned.

The **active owner** — the owner selected by `concordat owner use <owner>` — is
not itself namespaced. It is the single `github_owner` key in the **headline
configuration file**, `$XDG_CONFIG_HOME/concordat/config.yaml`
(`headline_config_path`). `get_active_owner` reads that key; `set_active_owner`
validates the owner and rewrites the file, preserving any other keys already
present so the headline file can grow additional settings without one writer
clobbering another's.

Every owner-derived path is built through `validate_owner`, which accepts names
that begin and end with an alphanumeric character and may contain alphanumerics
and hyphens internally, including doubled internal hyphens (the pattern is
`^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$`). Owner names reach this
validation before they are joined into a filesystem path, so a malformed owner
argument fails fast rather than producing a path that quietly bypasses
namespacing.

### Legacy-flat migration

Before owner namespacing existed, concordat wrote estates directly into the
same file that is now the headline config:
`$XDG_CONFIG_HOME/concordat/config.yaml`. **The legacy flat file and the XDG
headline config are the same `config.yaml`** — migration does not move data
between files so much as separate two concerns that used to share one file.

`concordat.estate_config.migrate_legacy_config` is the explicit, side-effecting
migration step, invoked once at CLI bootstrap (`concordat.cli.main`) so that
`default_config_path` stays a pure read-only query for every command. It is a
no-op once an active owner is already configured or once the flat file has no
`estate` section. If the estate section cannot be attributed to exactly one
owner, migration raises an error:

- `_derive_owner_from_estates` collects every `github_owner` recorded across
  the legacy estates. The legacy format permitted estates for more than one
  owner in one file; migrating such a section under the first owner encountered
  would silently misplace the other owners' estates, so mixed-owner input is
  rejected with an error rather than migrated.

When migration proceeds, the steps run in this deliberate order:

1. **Write the owner-scoped config.** The estate section is written whole
   into `owners/<owner>/config.yaml`.
2. **Set the active owner.** The headline file's `github_owner` key is set
   to the derived owner.
3. **Remove the legacy estate section last.** The `estate` key is dropped
   from the flat file (rewriting it if other keys remain, deleting it outright
   if the estate section was its only content).

This order is load-bearing, and the ordering is deliberate rather than
incidental:

- The active owner is what points `default_config_path` at the newly
  migrated, owner-scoped file. Setting it only *after* the owner-scoped write
  is complete means a reader never observes an active owner whose file is not
  yet populated.
- Owner-scoped writing and `set_active_owner` may fail, but both occur before
  legacy removal, so the legacy section remains available for recovery. Once
  the owner-scoped location is active, cleanup is the only failure tolerated:
  it is deliberately last. Were the legacy section removed first, a failure
  between that removal and the owner-scoped write would leave the estates in
  neither place the CLI looks: the flat file no longer holds them, and no
  active owner yet selects the owner-scoped file — an unrecoverable state,
  since the migration loader then finds no estate section to retry from. With
  cleanup last, a failure there instead leaves the estate data duplicated
  (already live in the owner-scoped file, still present in the stale legacy
  section) — duplicated but reachable beats complete but invisible.

Because the legacy file and the headline config are one and the same, step 2
(`set_active_owner`) writes into the very file step 3 is about to edit. Cleanup
therefore reloads the file's current contents from disk
(`_current_legacy_data`) rather than reusing the snapshot read before step 2:
rewriting that earlier snapshot would silently erase the `github_owner` key
step 2 just wrote, and deleting the file outright (when the estate section was
its only original content) would discard the key entirely.

## Credentials

`concordat/credentials.py` resolves secrets in a fixed, three-level priority
order, from highest to lowest:

1. **An explicit CLI flag** (for example `--github-token`), resolved by
   the CLI layer itself, in `concordat/cli.py`.
2. **A process environment variable** (for example `GITHUB_TOKEN`).
3. **The active owner's credentials file**,
   `$XDG_CONFIG_HOME/concordat/owners/<owner>/credentials.yaml`.

`credential_environment` implements the lower two levels: it overlays the
process environment with values loaded from the owner's credentials file, using
`dict.setdefault` so an environment variable that is already set is never
overridden by the file. `concordat.cli._github_token_fallback` (and equivalent
per-command fallbacks) call this only when the CLI flag itself is absent,
giving the full three-level order.

Only the names in `CREDENTIAL_KEYS` are honoured — `GITHUB_TOKEN`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`,
`SCW_ACCESS_KEY`, `SCW_SECRET_KEY`, `SPACES_ACCESS_KEY_ID`, and
`SPACES_SECRET_ACCESS_KEY` — anything else in the file is silently ignored.

Two defensive details are worth knowing when working on this module:

- **Group- or world-accessible files are refused, not read.** `load_credentials`
  checks the file's mode bits before parsing it; any group or world permission
  bit (including setuid/setgid) raises `InsecureCredentialsError` rather than
  reading a file that might be readable by other local users. The fix is
  `chmod 600`.
- **Only genuine non-blank strings are honoured; non-string values are
  dropped, not coerced.** `_recognized_credentials` requires both the key and
  the value to be `str` instances, and the value to be non-blank after
  stripping. A credential becomes an environment variable, so coercion would be
  actively harmful: an empty `KEY:` would coerce to the literal string
  `"None"`, and a YAML `false` would coerce to `"False"` — either handed to a
  remote as though it were a real secret. Non-string values (a YAML `null`
  under a key, a boolean, a number) are therefore dropped rather than
  stringified.

Concordat never writes this file; it is entirely operator-managed.

## API boundaries

Four modules define the layering between "what path does this data live at" and
"what does OpenTofu actually do with it":

- **`concordat.credentials`** and **`concordat.xdg`** have no dependency on
  git or provisioning code. `concordat.xdg` is pure path/config arithmetic;
  `concordat.credentials` builds on it for owner resolution but touches no git
  state. This keeps both modules importable from anywhere else in the codebase
  without pulling in `pygit2`.
- **`concordat/estate_cache.py`** owns the git-backed cache of estate
  repositories. Two entry points are deliberately split by side effect:
  - `cache_destination(record, cache_directory=None)` is a **pure path
    query**. It resolves the owner-namespaced cache path for a
    `EstateRecord` (or, when `cache_directory` is supplied as a test seam,
    a path directly under it) and does **not** touch the filesystem — it
    does not create the returned directory or its parents.
  - `ensure_estate_cache(record, cache_directory=None)` is the
    side-effecting counterpart: it calls `cache_destination`, then creates
    the destination's parent directory before cloning or refreshing the
    repository there. The docstring in the module notes this split
    explicitly — the parent is created "where the clone is about to need
    it", not earlier.
- **`concordat/estate_execution.py`** builds on `estate_cache` to run
  `tofu plan` / `tofu apply`. It wraps `ensure_estate_cache` so that
  `EstateCacheError` surfaces to callers as `EstateExecutionError`, keeping one
  error hierarchy per layer. `estate_workspace` is the context manager that
  ties caching, temp-workspace cloning (`clone_into_temp`), and cleanup
  together; it resolves the owner's XDG state `runs/` directory the same way
  `estate_cache` resolves the owner's cache directory, so kept workdirs
  (`--keep-workdir`) land somewhere predictable.

### Estate module boundaries

`concordat.estate` is the public façade for estate management; the modules
below sit beneath it, and `concordat.estate` imports each of them, never the
reverse:

- **`concordat/estate_config.py`** — configuration persistence and
  migration: loading and writing the owner-scoped estate configuration, the
  legacy-flat migration (see [Legacy-flat migration](#legacy-flat-migration)),
  and owner normalization.
- **`concordat/estate_errors.py`** — the estate exception taxonomy. It is a
  leaf module with no dependency on git or GitHub code, so any other layer can
  import it without risking an import cycle.
- **`concordat/estate_git.py`** — git operations behind `concordat estate init`
  and `concordat ls`: remote probing, inventory collection from a clone, and
  template bootstrapping for a new estate. It knows nothing about the GitHub
  API or the estate-init decision flow.
- **`concordat/estate_github.py`** — the GitHub API calls concordat makes
  when an estate repository must be created, and the translation of github3's
  authentication failures into the estate error taxonomy. It knows nothing
  about git or the estate-init decision flow.
- **`concordat/estate_repository.py`** — the *decisions*
  `concordat estate init` makes (which owner an estate belongs to, whether its
  remote needs provisioning), delegating the *how* to `estate_github` and
  `estate_git`. Its imports are deliberate, not incidental: it is the single
  lookup site the `concordat.estate` façade calls through, and the single seam
  the test suite monkeypatches (see
  [Module-level monkeypatch seams](#module-level-monkeypatch-seams)).

## `concordat artefact rule run`

`concordat/rules/runner.py`, `concordat/rules/packages.py`, and
`concordat/rules/envelope.py` implement the rule-run subcommand exposed as
`concordat artefact rule run <rule-id>`.

The split is by question asked. `packages.py` answers three about a rule
package without evaluating one: where its policy lives, what its manifest
declares, and which policy-input envelope it is audited over. `runner.py`
answers the fourth, what Conftest made of that envelope, and imports the rest.
Where a test patches depends on what it is testing, and the answer is not
simply "where the name is defined". `runner` imports `rule_package_dir` and
`rule_parameters` into its own bindings, so a test of how `runner` uses them
patches `runner._rule_package_dir` or `runner._rule_parameters`; patching
`packages` leaves `runner`'s bindings pointing at the originals. `run_rule`
captures `default_envelope_builder` as a default argument at definition time,
so substituting the resolver means passing `envelope_builder=` rather than
patching either module. Patch `packages` when testing the package helpers
themselves, including the mappings and the manifest reader.

### The policy envelope

`build_envelope` (in `envelope.py`) assembles a
`policy-input/rust-makefile-baseline` document (schema version 1) describing
one local checkout: root `Cargo.toml` and `Makefile` compatibility facts, the
resolved `cargo.surfaces` list, and the validated `makeutil` report for the root
`Makefile` (or `None`). `rust_surfaces.resolve_rust_surfaces` is the shared
Rust applicability boundary: `.concordat` `language.rust.surfaces` is
authoritative, including an empty list, while an absent declaration retains the
root-`Cargo.toml` fallback. This document is handed to Conftest as the input
under audit. The added `cargo.surfaces` field is backward-compatible within
schema version 1: policy replay of a v0.2 envelope without it retains the root
surface when `root_cargo_toml` is true. A present `cargo.surfaces` field must
be an array and `cargo` must be an object; malformed recorded evidence yields a
structured EN-001 indeterminate finding instead of silently selecting the
fallback or causing the policy evaluator to fail.

### Policy-input kinds and dispatch

Each rule manifest names the envelope its sensor evaluates under `sensor.input`.
`packages.rule_manifest` reads the manifest, and a package not registered by
identifier reaches its builder through `INPUT_KIND_ENVELOPE_BUILDERS`, keyed by
that declared kind; "Choosing the envelope for a package" below gives the
routes. A package declaring no kind, or one no builder produces, is an
`OperationalRuleError` rather than a guess: falling back would hand one policy
the document another was written for, and a policy that cannot find its own
facts reports a compliance it never established. The Makefile-centred kinds are
listed here; the CodeScene coverage and Dependabot kinds are described after
"Choosing the envelope for a package" below:

- `policy-input/rust-makefile-baseline` — `envelope.build_envelope`, above.
- `policy-input/rust-build-defaults` —
  `envelope.build_build_defaults_envelope`, below.
- `policy-input/whitaker-provisioning` —
  `whitaker_provisioning_envelope.build_whitaker_provisioning_envelope`. It
  reuses the CV-005 workflow decoder for `.github/workflows`, decodes every
  composite action manifest in the checkout the same way, and carries every
  Makefile, and every script under `.github/`, `bin/`, `ci/`, `scripts/` or
  `tools/` with a common interpreter suffix or a shebang, or at the root with a
  shebang, as text. A directory that cannot be listed raises an
  `OperationalRuleError`, because skipping it would hide a script. Dependency,
  build and cache directories are pruned, test code is left out, a binary is
  left out, a symlink within the checkout is read as its target, and a script
  that cannot be read is carried with its `error` for an indeterminate finding.
  The repository slug comes from the `origin` remote through
  `parse_github_slug`, so the policy's producer and exemption parameters can
  match it. The package is registered by identifier and declares the kind. The
  list of accepted `install-whitaker` revisions is derived by
  `concordat.rules.whitaker_revisions` (owner: the whitaker-provisioning
  package; callers: `scripts/whitaker_revisions.py` and its tests only). It
  walks shared-actions main first-parent and keeps each commit that is an
  approved root, or descends from one with the action directory's Git tree id
  equal to that root's. After shared-actions gains commits, run
  `uv run python scripts/whitaker_revisions.py sync <shared-actions clone>`,
  then commit the regenerated `rule.yaml` and refresh its digest in the canon
  manifest. `check <clone>` exits 1 and names each missing or non-derivable
  revision when the manifest's membership has drifted (use it in review; it
  compares which revisions are listed, not their order, so a reordered manifest
  passes with status 0), and `list <clone>` prints what the clone derives; each
  takes `--tip <ref>` and exits 2 when the clone or manifest cannot be read. To
  approve a changed action, add the reviewed commit to `install_whitaker_roots`
  first. Git read failures while walking the history surface as
  `OperationalRuleError`, never a traceback.
- `policy-input/markdown-formatting-baseline` —
  `markdown_envelope.build_markdown_envelope`. Alongside the same `makeutil`
  report for the root `Makefile`, it carries `.markdownlint-cli2.jsonc` decoded
  by `concordat/rules/jsonc.py` (comments and trailing commas stripped outside
  string literals, then strict JSON), the names of any alternate markdownlint
  configuration files present, and every workflow under `.github/workflows`
  decoded as YAML 1.2. A file that exists but cannot be decoded is carried with
  its `error` so the policy reports an indeterminate finding; a file that
  cannot be opened at all is operational. Applicability is content-driven: any
  Markdown file outside the pruned dependency, build, and cache directories
  brings the checkout into scope. `markdown-formatting-baseline` is not
  registered by identifier: it declares this kind, which is the ordinary shape
  for a package bringing its own builder. It also carries `action_pins`, what
  each full-SHA pin of `DavidAnson/markdownlint-cli2-action` names. The builder
  leaves it empty; the rule-run command fills it in a separate step
  ([ADR-002](adr-002-resolve-action-pins-against-github.md)).

- `policy-input/spelling-config-baseline` —
  `spelling_envelope.build_spelling_envelope`. It reuses `markdown_envelope`'s
  containment guard and readers: the same `makeutil` report for the root
  `Makefile` and the same decoded workflows, plus `typos.local.toml` decoded
  with `tomllib`, the root `.gitignore` as its stripped non-blank lines, the
  root `AGENTS.md` text, and the repository-relative paths that match the
  manifest's `vendored_paths` globs (the walk prunes the same directories and
  never follows links). Applicability is decided in the policy, from any part
  of a spelling setup. The package is registered by identifier, because its
  builder reads the `vendored_paths` parameter, and it also declares its kind.
  The canonical AGENTS.md texts reach the policy as the `agents_md_blocks`
  manifest parameter; the fixture generator copies them into `data.json`'s
  `parameters`, so the Rego suite compares against the runner's text.

The Markdown package's `fixtures/generate.py` lays each scenario out as a
temporary checkout and records what `build_markdown_envelope` produces, so the
checked-in envelopes are exactly the production builder's output;
`tests/unit/test_markdown_fixture_generator.py` fails if they drift.

Pin resolution is split so that building an envelope stays a query:

- `concordat/rules/action_pins.py` is the transport-free domain: the
  `PinResolution` fact, the `(repository, sha) -> PinResolution` resolver
  signature, and `pinned_shas` and `resolve_pins`, which collect the distinct
  full-SHA pins of an action and ask a resolver about each once.
- `concordat/rules/github_pins.py` is the adapter. `GithubPinResolver` asks
  the shared `concordat.auditor.github.GithubClient` for `git/commits/{sha}`,
  then `git/tags/{sha}`, and translates every reply, including refusals,
  transport failures and malformed bodies, into a resolution. It builds its
  client on the first lookup, so a checkout with no pinned action reads no
  credentials, and logs each lookup's outcome and duration at debug level on the
  `concordat.rules.github_pins` logger.
- `build_markdown_envelope` never calls a resolver and leaves `action_pins`
  empty. `markdown_envelope.with_action_pins` is the command step that calls
  one, and `packages.resolving_pins` composes it onto any envelope builder,
  reading the action from the package's `markdownlint_action` parameter.
- `concordat artefact rule run` is the only production caller. It builds the
  `GithubPinResolver` with a client factory that reads the GitHub token, and
  turns an unreadable credentials file into an operational failure (exit 2).
  `--github-api-url` points it at another API root, which is how the end-to-end
  tests reach the local double in `tests/helpers/github_api.py`.

The command authenticates with `GITHUB_TOKEN` or the concordat credentials, and
otherwise with `gh auth token --hostname`, for the host `cli._github_host`
derives from `--github-api-url` (`api.github.com` is `github.com`), so a token
never reaches a host it was not issued for. `cli._gh_cli_token` returns `None`,
and logs why at debug level, when `gh` is missing, logged out or slow. The
shared `GithubClient` raises `GithubRateLimitError`, a `GithubForbiddenError`,
for a 429 or a 403 with `X-RateLimit-Remaining: 0`; the adapter records it as
an unresolved pin with `rate_limited: true`, and PD-006 reports that apart from
an unknown pin, naming the remedy. `GithubPinResolver` keeps every answer for
its own lifetime, so a pin repeated in one run costs one lookup, and logs each
reuse at debug level.

`concordat/rules/pin_cache.py` keeps definite answers between runs, because a
sweep audits many repositories that pin the same few actions and each run is a
new process. `cached_resolver` wraps any resolver: it reads `PinCache` first,
and after a lookup writes the answer back. The command composes it around
`GithubPinResolver`, so the per-run memo stays the inner layer. The policy in
short: cache a commit or a tag object, never an unresolved pin; key by the API
root and `owner/repository/sha` (lower-cased, and only when the repository is a
plain `owner/name` pair and the pin is forty hex digits, since both become path
segments); write through a sibling temporary file and a rename. `PinCache.get`
is a query that returns a `CacheRead` (`hit`, `miss`, `corrupt`, `unreadable` or
`bypassed`) and changes nothing; `cached_resolver` discards a corrupt entry
with `PinCache.discard` and logs each outcome at debug level.
`default_directory` takes the environment as a mapping, so tests inject it, and
`tests/conftest.py` points every test's cache at a temporary directory so a run
never writes to the host's cache. The entry format is recorded in ADR-002.

Regenerate the Markdown fixtures with the `makeutil` release CI pins, not
whichever `makeutil` is first on `PATH`: a newer build reports some Makefiles
differently and rewrites envelopes the change never touched. Install the pin
into a scratch directory with the values from `.github/workflows/ci.yml`, then
put it first on `PATH`:

```shell
rm -f .makeutil-pin/path  # the installer appends to it
MAKEUTIL_VERSION=0.1.0 \
MAKEUTIL_ASSET=makeutil-x86_64-unknown-linux-musl \
MAKEUTIL_SHA256=99dd28a138dbe07e88e4dc5dd3954e6b29b46cc959635311d326cb537253115d \
MAKEUTIL_RELEASES=https://github.com/leynos/makeutil/releases/download \
RUNNER_TEMP="$PWD/.makeutil-pin" GITHUB_PATH="$PWD/.makeutil-pin/path" \
  uv run scripts/install_release_binary.py install makeutil
PATH="$(cat .makeutil-pin/path):$PATH" uv run python \
  platform-standards/canon/lint-rules/markdown-formatting-baseline/fixtures/generate.py
```

Offline resolvers live with their callers: the fixture generator answers from
its `PIN_TABLE` through `resolve_from_table`, and the unit tests pass their
own. A rule package that needs the same fact composes `resolving_pins` or calls
`with_action_pins` with the command's resolver, rather than calling the API
from its envelope builder.

### Choosing the envelope for a package

A rule package reads the facts its checks need, and those differ. `run_rule`
takes an `envelope_builder` resolver and calls whatever it is given;
`default_envelope_builder` is the composition layer that maps a package to its
builder and supplies the manifest parameters that builder needs. Package
selection therefore stays in one place, and a caller — a test included —
substitutes a resolver rather than reaching into the mappings below.

The resolver takes two routes and has no third. Both mappings live in
`packages.py`. `PACKAGE_ENVELOPE_BUILDERS` maps a package identifier to its
builder and is the complete list of packages, not the exceptions to a default.
A package absent from it may instead declare `sensor.input` in its own
`rule.yaml`, naming an envelope kind that `INPUT_KIND_ENVELOPE_BUILDERS` knows;
that is the route for a package whose input is a shape another package already
builds, and it needs no Python change. A package matching neither is refused
with an `OperationalRuleError` naming it, the registered packages, and the
declarable kinds.

A package that needs facts neither existing envelope carries brings its own
builder, and adds one entry to `INPUT_KIND_ENVELOPE_BUILDERS` keyed by the kind
its envelope emits. It then declares that kind in its own `rule.yaml` and needs
no entry in the identifier mapping at all. That is the ordinary shape for a new
package: one line here, one line in its manifest, and no mechanism of its own.

The two mappings are therefore not the same set. Every builder reachable by
identifier is also reachable by its kind, so a package's envelope is one
another package could declare; the reverse does not hold, because a
declared-only package appears in the kind mapping alone.

**Every shipped package declares or registers.** There is no third state and no
default, so a manifest written before `sensor.input` existed is refused rather
than quietly given the envelope it used to receive by accident. That is the
point of the rule: the package that most needs refusing is the one nobody
remembered to wire up, and a default is precisely what hides it.

There is deliberately no fallback. An earlier version of this resolver sent an
unregistered package to `build_envelope`, on the reasoning that existing
packages should be left untouched. That is a fail-open default inside a
fail-closed audit: a package whose policy expects one envelope, handed another,
does not degrade — it answers confidently about a document it was never written
for. `rust-build-defaults` expects a different document from
`rust-makefile-baseline`, so a registration mistake would have turned into an
`EN-001` finding about the wrong input rather than a failure to audit. Refusing
costs one line in a mapping or one line in a manifest; the alternative costs a
verdict nobody can trust.

`main-owned-codescene-coverage` takes its own too.
`build_codescene_coverage_envelope` (in `codescene_coverage_envelope.py`)
assembles a `policy-input/main-owned-codescene-coverage` document containing
every root `.github/workflows/*.yml` and `*.yaml` file. Each fact records
decoded YAML or the content error that prevented decoding, so the CV-005 policy
returns an indeterminate verdict for one file rather than failing the whole
run. The package is registered by identifier and declares the same kind in its
`rule.yaml`, so both routes reach one builder. The adapter in `packages.py`
accepts the manifest parameters and ignores them: CV-005 declares no tunables,
because every clause it states is a property of the estate's topology rather
than something a repository may configure.

`dependabot-update-shape` takes its own as well. `build_dependabot_envelope` (in
`dependabot_envelope.py`) assembles a `policy-input/dependabot-update-shape`
document holding the decoded `.github/dependabot.yml` (or `.yaml`), each
entry's group names in document order, and every directory under
`.github/actions` that holds an action manifest. The group order is carried
separately because a Rego object keeps none, and Dependabot assigns a
dependency to the first group that matches it. A configuration that is not
UTF-8, not YAML, not a mapping or a symbolic link, and a checkout carrying both
spellings, is recorded with its reason for an indeterminate verdict. A
`.github/actions` directory that cannot be listed, is occupied by a file, or
resolves outside the checkout raises `OperationalRuleError` with
`operation="read-local-actions"`; the walk does not follow symbolic links. The
package is registered by identifier and declares its kind, and DB-005 declares
no tunables.

Workflow discovery in that builder is explicitly fallible. An absent
`.github/workflows` directory is a repository with no workflows and yields an
empty list. A path that is not a directory, a directory that cannot be
enumerated, an entry whose status cannot be read, a file that cannot be read,
and a directory that resolves outside the checkout each raise
`OperationalRuleError` with `operation="read-workflow"` and the affected path.
Returning an empty list for any of those would report the one repository shape
the reader could not see as being in perfect order, clearing every clause of
the rule at once.

`rust-build-defaults` is the first package to take its own. Its envelope
(`build_build_defaults_envelope`) carries the facts Cargo and rustup
auto-discover, and the Makefile and workflows whose builds replace them:

- `cargo_config` — the `rustflags` sources Cargo would consult, each with its
  normalized flags and whether it applies on Linux, only on Linux, or could not
  be placed at all; every codegen-backend selection with the route that made
  it; and the `[unstable]` gate. A configuration Cargo would refuse to load,
  including a `rustflags` array with a non-string member, is reported as a
  parse error rather than read around.
- `toolchain` — the pinned channel and its classification, which decides
  whether the nightly-only clauses apply.
- `exceptions` — one scan per document declared by the rule's
  `exception_documents` parameter, listing the sections whose heading names the
  backend and the channel spellings each section's body contains.
- `makefile` — the root Makefile's `makeutil` report, read through
  `markdown_envelope`'s containment guard, or `None` when there is none.
- `makefile_error` — why `makeutil` refused the Makefile, or `None`. A refusal
  is carried rather than raised, because only BD-007 and BD-008 read the
  Makefile; raising would make BD-001 to BD-006 unrunnable against that
  checkout too. A Makefile that resolves outside the checkout still raises.
- `workflows` — every `.github/workflows` file decoded as YAML 1.2, or its
  decoding error, from `markdown_envelope`'s public `load_workflows`. Each fact
  also carries `decode_category`, a fixed word for why it did not decode
  (`invalid YAML`, `not UTF-8 text`, `not a mapping` or `unreadable`), set by
  the envelope builder so the policy never depends on the reader's wording.

BD-001 to BD-006 read only the first three. The standard is a default because
Cargo auto-discovers `.cargo/config.toml`, so a repository whose flags live
behind an opt-in Make target has no such file and fails on that alone. BD-007
to BD-009 read the rest, because an assigned `RUSTFLAGS` replaces that default
and a coverage build cannot use a Cranelift one. ADR-003 records the change.

### The shared checkout readers in `markdown_envelope`

The Markdown, spelling and build-defaults envelope builders read the same files
through one guarded set of functions in `concordat/rules/markdown_envelope.py`.
Import them by name; they are public because three builders share them, and a
private-name import across modules is the pattern concordat#264 removes.

- `resolved_root(checkout)` returns the checkout with every symbolic link in
  its own path resolved. It raises `OperationalRuleError` (operation
  `resolve-checkout`) if the path cannot be resolved. Resolve once, then pass
  the result to the other functions.
- `within_checkout(root, path, operation)` returns whether *path* exists and
  resolves inside *root*. A missing path returns `False`; a path that resolves
  outside the checkout raises `OperationalRuleError` naming *operation*. Every
  policy input is read through it, because the readers follow symbolic links
  and a checkout could otherwise carry another file's contents into an audit
  that may be published.
- `is_file(path, operation)` returns whether *path* is a regular file. Absence
  is `False`; a probe that cannot answer (a permission error, say) raises,
  because reading that as absence would report a broken checkout as compliant.
- `load_workflows(checkout, root)` returns one fact per file under
  `.github/workflows`, sorted by name, each decoded as YAML 1.2 or carrying its
  decoding error. It returns an empty list when there is no workflows
  directory, and raises `OperationalRuleError` if the directory or a file
  resolves outside the checkout or the directory cannot be listed.

### Observing `makeutil` runs and undecodable workflows

The envelope builders are pure queries. `inspect_makefile` reads no clock and
writes no log, and `_build_workflows` only reads and tags workflow facts. Each
builder (`build_envelope`, `build_markdown_envelope`, `build_spelling_envelope`
and `build_build_defaults_envelope`) takes a keyword `inspect` of type
`MakefileInspector`, defaulting to the pure `inspect_makefile`.
`build_build_defaults_envelope` also takes
`report_undecodable(path, category)`, which defaults to `None` and so reports
nothing.

Observation is wired in at the command boundary, `rules/packages.py`, which
passes `makefile_observed.inspect_makefile_observed` as `inspect` and
`envelope.log_undecodable_workflow` as `report_undecodable`.
`inspect_makefile_observed` times the query with an injected `clock` and passes
one `MakefileParseEvent` (operation, tool, outcome, elapsed seconds) to an
injected `emit`; the default emitter writes a debug log record. The outcome is
a fixed word (`complete`, `recovered`, `refused`, `timeout`, `launch-failure` or
`error`), never the tool's output, which can quote Makefile content.

`MakefileRefusedError` (an `OperationalRuleError`) is raised for `makeutil`
exit status 2. `build_build_defaults_envelope` carries it as `makefile_error`
rather than raising; every other `makeutil` failure still raises (ADR-003).

Tests call a builder directly to prove it is pure, pass a stub `inspect` or a
list's `append` as `report_undecodable` to observe it, and use a fake clock with
`inspect_makefile_observed`.

### Absence is not a read failure

`concordat/rules/fs_probe.py` exists because `Path.is_file()` answers "does not
exist" and "the filesystem refused to say" with the same `False`. A rule fact
built on that probe reports an unreadable checkout as a compliant absence,
which is the one answer a fail-closed audit must never give by accident.
`probe_file` separates the two: callers turn an absence into whatever their
clause means by it, and a refusal into a fail-closed fact carrying the reason.
Every new reader in the build-defaults envelope uses it.

`probe_dir` and `probe_any` answer the same way for a directory and for any
entry, over the core `probe_file` uses, so a file where a directory is expected
is a refusal naming the occupant, exactly as a directory where a file is
expected is. `probe_symlink` is the exception, and deliberately: it asks what
kind of entry is there, without following it, so an entry of another kind is an
answer rather than an occupied path. The Markdown envelope uses all four,
because from Python 3.14 the suppression is total: `exists`, `is_file`,
`is_dir`, and `is_symlink` now swallow every `OSError` the operating system
raises. This package supports 3.13 and later, so before that change the same
unreadable checkout raised on one interpreter and read as empty on the other.
The Markdown envelope translates a reported refusal into the
`OperationalRuleError` its callers already expect, and `packages.rule_manifest`
does the same, so a package whose `rule.yaml` cannot be read cannot silently
lose its parameters and its declared policy input.

Absence is narrower than it first looks, and the boundary took a second pass to
get right. It is `ENOENT` with nothing behind it, and `ENOTDIR` because a
component of the path is not a directory. Everything else is a refusal,
including two shapes a bare `stat` plus a regular-file test reports as empty: a
dangling symbolic link, where `lstat` succeeds and `stat` does not, and a
directory or other non-regular file where a file is expected. Both are occupied
paths. Reading either as an absence turns a broken checkout into a repository
that simply never wrote the file, which is the compliant answer rather than the
true one.

The same holds one level up. `lstat` does not follow the final component but
does resolve every component above it, so a dangling `.github` makes
`.github/workflows` raise exactly the error an absent directory raises. A
missing-file error is therefore not read as absence until the nearest existing
ancestor is shown to resolve; an ancestor that does not resolve is named in the
refusal. A caller whose own read has already failed with a missing-file error,
such as the CV-005 workflow-directory reader, asks `probe_file` whether
anything is there and keys on `read_error is None` rather than on `present`,
which answers the different question of whether a regular file is there.

### Tool dependencies

Two external tools must be on `PATH`:

- **`makeutil`** (`concordat/rules/makefile_facts.py`) — the sole means by
  which concordat inspects a `Makefile`; the module docstring states plainly
  that "Concordat never parses GNU Make syntax itself". `makeutil parse` is run
  with a 10-second default timeout, and its exit code (0 = complete parse, 1 =
  recovered parse) must agree with the `parse.status` field of its own JSON
  report, or the report is rejected as internally inconsistent. CI installs a
  released static binary rather than compiling it: `ci.yml` and
  `coverage-main.yml` run `scripts/install_release_binary.py install makeutil`,
  which downloads `MAKEUTIL_ASSET` from release `v$MAKEUTIL_VERSION` over HTTPS
  and checks it against the `MAKEUTIL_SHA256` digest pinned beside the version,
  not against the release's own `.sha256` file, so a replaced asset fails the
  install. The file is renamed into place only after the digest matches, so a
  failed download or a mismatch installs nothing;
  `scripts/tests/test_install_release_binary.py` runs each path. Bump the
  version and the digest together; `tests/unit/test_skylos_lint_contract.py`
  holds both workflows to the same release, digest and install command. CI used
  to compile makeutil from a commit SHA, and a check refused any SHA missing
  from makeutil's `main`, because an orphaned commit installs only until GitHub
  garbage-collects it. That check was retired with the commit pin. A release
  asset is not garbage-collected like an orphaned commit, and the pinned digest
  now guarantees that the installed binary is the one released, so no commit
  pin remains for the check to guard. A local `makeutil` built from another
  revision also reports `0.1.0`, so `--version` cannot show that it is not the
  release, yet its diagnostic locations differ and the checked-in fixture
  envelopes then fail their regeneration comparison
  (`tests/unit/test_markdown_fixture_generator.py`). That failure names the
  pinned release, asset and digest, read from `ci.yml` by
  `tests/unit/makeutil_pin.py`; install that asset and compare its SHA-256
  instead of regenerating the envelopes.
- **`conftest`** (`concordat/rules/runner.py`) — evaluates the envelope
  against the rule package's Rego policy, with a 60-second timeout
  (`CONFTEST_TIMEOUT`).

### Running a package's own policy tests

Each rule package ships a Conftest/Rego test file beside its policy, and those
tests are where a clause's semantics are pinned. `make test` runs them:
`tests/unit/test_lint_rule_policies.py` discovers every package with a
`policy/` directory and invokes `conftest verify` over the package's
`fixtures/data.json`. A guard test asserts the discovery finds the shipped
packages, so the parametrization cannot pass over an empty list.

Before that module existed nothing ran them. The continuous-integration policy
step covers the OpenTofu policies alone, so every clause pinned by a lint-rule
Rego test was pinned by a test that never executed.

### The `OperationalRuleError` contract

`OperationalRuleError` (`concordat/errors.py`) is raised whenever rule
evaluation could not run at all — as distinct from a policy finding, which is a
successful evaluation that happens to report noncompliance. It carries three
pieces of context:

- `operation` — a stable identifier for the failing action (for example
  `"load-rule-package"`, `"invoke-conftest"`, `"parse-cargo-toml"`).
- `tool` — the external program involved (`"makeutil"`, `"conftest"`,
  `"git"`), or `None` when no external tool was involved (for example, an
  invalid rule-package identifier).
- `resource` — the affected path or identifier, or `None`.

### Verdicts and exit codes

`RuleRunResult.verdict` is one of three values, reduced from the individual
findings by `_overall_verdict`:

- `compliant` — no findings at all.
- `noncompliant` — at least one finding carries verdict `noncompliant`.
- `indeterminate` — findings exist, but none is `noncompliant` (the policy
  could not prove compliance, and fails closed rather than passing).

The `rule_run` CLI command maps these, plus operational failure, onto three
exit codes: `0` compliant, `1` at least one finding (including indeterminate,
which fails closed), `2` operational failure — an uncaught
`OperationalRuleError` is caught in `concordat.cli.main` and converted to exit
code 2, printed to standard error, distinct from the `1` that `ConcordatError`
maps to.

### Rule package identifier validation

Rule package identifiers are validated against a canonical pattern — lower-case
ASCII words joined by single hyphens (`^[a-z0-9]+(?:-[a-z0-9]+)*$`) —
**before** any filesystem access. `_rule_package_dir` then joins the validated
identifier to the packages root and confirms the resolved path stays under that
root even though the pattern alone already excludes traversal characters; the
containment check exists so that a future loosening of the pattern cannot
silently reach outside the root. The root itself comes from
`_rule_packages_dir()`, a cached lookup performed on first use rather than at
import, so a missing or unreadable rule tree surfaces when a rule runs instead
of when the module is imported.

### Conftest exit codes

Only Conftest exit codes `0` (no policy failures) and `1` (policy failures) are
treated as policy verdicts; both are expected to emit a JSON result document on
stdout. Any other exit code — a malformed policy, a bad flag, a missing input
file — means Conftest did not evaluate the policy at all, even if it printed
something on stdout that looks like JSON. `_require_policy_exit_code` rejects
those with an `OperationalRuleError` rather than risk decoding an operational
failure as though it were a clean run.

### Packaging: installed package data and the source distribution

The build backend is setuptools (`[build-system]` in `pyproject.toml`:
`requires = ["setuptools>=61.0", "wheel"]`,
`build-backend = "setuptools.build_meta"`).

`[tool.setuptools] packages` lists every shipped package explicitly:
`concordat`, `concordat.auditor`, `concordat.persistence`, `concordat.rules`,
`concordat.canon`. The list is explicit rather than `packages.find` because
`concordat.canon` is an out-of-tree data package that has to be named — that
rules out `find`, so the in-tree subpackages are listed alongside it rather
than discovered automatically.

`[tool.setuptools.package-dir]` maps
`"concordat.canon" = "platform-standards/canon"`, and
`[tool.setuptools.package-data]` ships
`"concordat.canon" = ["lint-rules/**/*"]`. So the canon lint-rule tree lands
under `concordat/canon/lint-rules` in the wheel, which is the point:
`concordat artefact rule run` has to work from an installed wheel, not only a
source checkout. The runner resolves the rule-package tree through
`importlib.resources`, with a source-checkout fallback — see
`_rule_packages_dir()` above.

`MANIFEST.in` controls the source distribution, and grafts three trees:
`platform-standards`, `scripts`, and `tests`. This preserves the sdist contents
the previous build backend shipped, per the file's own comment.

## The Concordat Auditor and CV-006

The Auditor (`python -m concordat.auditor`) checks repository settings that no
checkout carries. `cli.main` builds one `AuditContext` either from live API
reads (`_context_from_live_api`) or from a recorded JSON snapshot
(`_context_from_snapshot`), then runs every check the registry in
`checks.build_registry` holds and writes SARIF. A check is a `CheckDefinition`
for the rule catalogue plus a function from the context to a list of `Finding`s.

CV-006 lives in `concordat/auditor/codescene_environment.py` and follows the
same split. `fetch(client, owner, name)` reads the settings into a frozen
`CodesceneCredentials`, which `AuditContext.codescene` carries (None when a
snapshot has no `codescene` section). `run(context)` judges it without further
reads, so the check is tested over plain states.

- **Subject.** `workflow_uploads` parses each root workflow and matches
  executable steps only: the upload action with an effective `mode` of
  `upload`, or a `run` command line starting `cs-coverage upload`. Comments and
  `check` or `install` modes do not make a repository a subject.
- **Reads.** `GithubClient` gains `workflow_texts`, `environment` (a 404 is
  None, the environment's absence), `environment_branch_policies`,
  `environment_secret_names` and `repository_secret_names`. The listings wrap
  their entries in an object, so `_paginate_key` follows the `next` links that
  `_paginate` follows for bare lists. Secret names are read, never values.
- **Failures.** `_request` raises `GithubForbiddenError` for 401 and 403,
  `GithubNotFoundError` for 404, and `GithubError` for anything else.
  `_SettingsReader.read` records any `GithubError` against the read's label
  instead of raising, and an unparsable workflow does the same, so CV-006
  reports `indeterminate` and the rest of the audit still runs.
- **Statuses.** Each finding carries a `status` property. A missing
  environment is reported alone; otherwise the policy status (if any) and the
  secret status (if any) are reported together, so a rollout reads as progress.

Adding a settings check follows the same shape: a state dataclass filled by a
`fetch` that never raises for an unreadable resource, a pure `run`, a
registration in `build_registry`, and a snapshot section parsed in `cli.py`.

## Parabellum boundaries

`scripts/parabellum_sweep.py` is the campaign driver for auditing the Rust
estate under `rust-makefile-baseline`. Its boundaries with the rest of the
codebase are:

### Manifest schema and identifier validation

The estate manifest (`docs/parabellum/estate.yaml` by default) is a mapping
with an `owner` key and a `repositories` list of `{name, excluded?}` entries
(`load_estate`, `Estate`, `EstateEntry`). Both `owner` and each repository
`name` are validated against dedicated patterns before being used to build a
URL or a clone-directory path:

- `_OWNER_PATTERN` — GitHub-owner shaped: alphanumerics and hyphens, no
  leading/trailing hyphen, capped at 39 characters (GitHub's own owner length
  limit). This is a stricter, length-bounded sibling of `concordat.xdg`'s
  `_OWNER_PATTERN`, which has no length cap; the two are independent patterns
  maintained separately, not a shared constant.
- `_REPO_NAME_PATTERN` — alphanumerics, dot, underscore, and hyphen, 1–100
  characters, with at least one non-dot character (so `.` and `..` cannot be
  smuggled in as a "repository name" that later becomes a clone-directory
  component).

Both are checked again in `clone_and_audit` immediately before the values are
interpolated into a clone URL and a scratch-directory path, not only at
manifest-load time — belt and braces for any caller reaching `clone_and_audit`
directly rather than through `load_estate`.

### The append-only ledger and its idempotency rule

`docs/parabellum/ledger.jsonl` (by default) is an append-only JSON Lines file:
one JSON object per line, never rewritten or truncated (`_append_record` opens
the file in append mode and writes exactly one record per call). Each record is
durable — flushed to disk — before the sweep moves on to the next repository,
because auditing the whole estate takes many minutes and clones over the
network; an interrupted sweep resumes from where the ledger left off rather
than restarting.

**Idempotency rule:** a repository is skipped, rather than re-audited, when the
ledger already holds a record for that repository at the same `commit_sha`
(`_already_ledgered`). Excluded entries use a variant of this rule keyed on
`verdict == "excluded"` instead of a commit, since an exclusion has no commit
to compare against. `--force` bypasses the commit-based skip (but not the
exclusion skip, which unconditionally prevents duplicate exclusion records for
the same repository).

### The git boundary

All git operations funnel through the module-private `_git` helper, which
shells out to the `git` binary (`subprocess.run`, fixed argv, no shell) with a
300-second timeout (`GIT_TIMEOUT`). Every call site supplies an `operation` and
`resource` for the resulting `OperationalRuleError` if the command is missing,
times out, or exits non-zero. Two call sites build on `_git`:

- `resolve_head(owner, name)` runs `git ls-remote <url> HEAD` to obtain the
  default-branch head SHA **without cloning** — used to decide, cheaply,
  whether a repository has already been ledgered at its current head before
  paying for a clone.
- `clone_and_audit(owner, name)` performs a shallow, single-branch clone
  (`git clone --depth 1 --quiet`) into a temporary directory, resolves `HEAD`
  there with `git rev-parse HEAD`, and hands the checkout to
  `concordat.rules.run_rule` for the `rust-makefile-baseline` audit. The sweep
  is audit-only: nothing here ever writes to an estate repository.

## Property tests and the bounded reachability contract

### Hypothesis property tests

`tests/unit/test_properties.py` holds concordat's Hypothesis-based property
tests. Its module docstring states the discipline the whole file follows:
"where a property restates a regex, it is written from the specification rather
than the implementation's pattern, so the two can disagree" — a property test
that reimplements the code under test proves nothing, so each property is
derived from the documented rule instead. The file covers:

- **the owner-name grammar** (`TestOwnerNames`) — acceptance against
  `xdg.validate_owner` agrees with a grammar written independently of
  `_OWNER_PATTERN`, well-formed names round-trip unchanged, and no name
  containing a path separator is ever accepted;
- **credential filtering** (`TestCredentialFiltering`) — every entry that
  survives `credentials._recognized_credentials` has a recognized key and a
  trimmed, non-blank string value;
- **rule-package identifiers** (`TestRulePackageIdentifiers`) — acceptance
  by `runner._validated_rule_id` agrees with the canonical hyphenated-words
  grammar, and a rejected identifier never reaches the filesystem;
- **manifest repository names** (`TestManifestRepositoryNames`) — every
  name accepted by `sweep._validated_identifier` is a single, safe path
  component that cannot escape the directory it is joined to; and
- **ledger record selection** (`TestLedgerSelection`) — over a generated
  append-only history, the latest record for a repository is the last one
  appended.

`test_a_component_joined_to_a_root_stays_inside_it` joins a generated name to a
real directory rather than a `tmp_path` fixture: Hypothesis rejects
function-scoped fixtures, since they would be created once and then shared
across every generated example rather than being fresh per example.

### Property tests that evaluate a policy

A property that judges a Rego policy must not start one Conftest process per
generated case: at 80 cases that took 12 s alone and passed the suite's 30 s
`pytest-timeout` on a loaded host, and widening the timeout would only hide the
cost. Draw a fixed-size list of cases instead
(`st.lists(…, min_size=n, max_size=n)`) and evaluate it with
`invoke_conftest_batch` from `tests/unit/conftest_batch_support.py`, which runs
one process over every envelope and returns one result list per envelope. It
matches results to envelopes by the file name Conftest reports, not by
position, and raises an `OperationalRuleError` when an envelope has no result,
so a dropped result cannot pass as a clean one. The helper is private to the
runner and is for tests that judge many envelopes against one rule; production
callers evaluate one checkout and use `_invoke_conftest`. Keep one
single-envelope test on that path as a smoke test.

The BD-008 properties and the BD-009 setup-ordering property
(`test_build_defaults_workflow_properties.py`) go through the public
`runner.run_rule` one example at a time, with a small `max_examples`, because
each example needs a real envelope and one Conftest run.

### The bounded Rego reachability test

The rule package's `policy/rust_makefile_baseline_test.rego`, under the
`-- bounded reachability contract --` banner, enumerates `lint` prerequisite
chains of increasing depth over one envelope. In the shipped
`rust-makefile-baseline` v0.2.0 rule package, QG-001 proves gate delegation
within one prerequisite hop, so this suite pins the boundary between "provable"
and "indeterminate" rather than sampling it: depth 0 (a direct gate invocation)
and depth 1 (one hop of delegation) are compliant, and every deeper chain is
indeterminate. This one-hop bound is the semantics of the shipped v0.2.0 rule
package only. `docs/concordat-design.md` §2.2.1 specifies, but has not shipped,
a v0.3.0 that widens QG-001's delegation proof from one prerequisite hop to a
full static closure over the parsed Makefile: the closure's edges are a rule's
prerequisites plus any recipe line invoking `$(MAKE) <literal-target>` in the
same file. That closure is cycle-safe and needs no depth bound because every
edge is a fact from the single parsed file; dynamic edges (`$(MAKE) $(VAR)`,
`$(MAKE) -C`, recursive make into other files) and includes stay indeterminate.
Under v0.3.0 the `two_hop` fixture's expectation changes from indeterminate to
compliant. `build` and `test` targets are kept present in every case, so FP-003
stays silent and QG-001 is the only variable under test.

QG-001's shell readings (rule version 0.3.2) are pinned in two places.
`policy/rust_makefile_baseline_precision_test.rego` holds named Rego cases for
each reading in both directions, including every hole a review found. The
second is `tests/unit/test_rust_makefile_baseline_shell_properties.py`, which
generates bounded recipes and uses bash as an independent oracle: it runs each
recipe with stub tools and asserts that whatever the policy accepts, bash
cannot skip. A change to any of the three readings should add a named Rego case
and, where the shape can be generated, widen the property's fragments.

This policy suite is not wired into the Makefile. It is run directly with
Conftest:

```shell
cd platform-standards/canon/lint-rules/rust-makefile-baseline
conftest verify --policy policy --data fixtures/data.json
```

## Test seams and subprocess contracts

The suite substitutes real subprocesses and network access with two distinct
mechanisms, depending on whether the code under test shells out directly or
calls another concordat function that does.

### `cmd_mox`: the subprocess-mocking harness

`tests/conftest.py` defines a small, purpose-built `CmdMox` harness (not the
similarly-named third-party `cmdmox` library) and exposes it as the `cmd_mox`
pytest fixture. It monkeypatches `subprocess.run` globally for the duration of
a test (`CmdMox.replay`), so it intercepts *any* subprocess invocation —
`makeutil`, `conftest`, `git` — regardless of which module issued it.
Expectations are queued with a fluent builder:

```python
cmd_mox.mock("conftest").with_args("test", "--policy", ...).returns(
    exit_code=0, stdout="[]"
)
```

Each queued expectation is consumed in order (`collections.deque`); an
unexpected command, a command-name mismatch, or an argument mismatch raises
immediately, and any expectations left unconsumed at the end of a test raise via
`CmdMox.verify`.

### Module-level monkeypatch seams

Where concordat code calls another concordat function directly (rather than
shelling out), tests patch that function on the module attribute the caller
actually resolves at call time — which is not always the function's *defining*
module:

- **`concordat.estate_repository._probe_remote`** — `estate_repository.py`
  imports `_probe_remote` from `concordat.estate_git` and calls it as a bare
  name, so patching `concordat.estate_git._probe_remote` would leave
  `estate_repository`'s already-bound reference untouched. A comment above the
  import states explicitly that tests must patch
  `estate_repository._probe_remote` (along with `._build_client` and
  `._create_repository`) — the name as it appears in `estate_repository`'s own
  namespace.
- **`scripts.parabellum_sweep.resolve_head`** — `resolve_head` is defined
  directly in `parabellum_sweep.py`, so there is no import indirection to worry
  about: tests import the module (commonly aliased `sweep`) and monkeypatch
  `sweep.resolve_head` directly, replacing the network-touching `git ls-remote`
  call with a fixed SHA or a function that raises `OperationalRuleError`,
  without needing `cmd_mox` at all.

The rule of thumb: if the code shells out via `subprocess.run`, reach for
`cmd_mox`; if it calls a sibling concordat function, monkeypatch that function
on the module that does the calling.
