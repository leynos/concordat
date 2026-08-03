# Concordat developers' guide

This guide documents concordat's internal boundaries: the module contracts
that other modules, tests, and the CLI rely on. It complements
[`docs/users-guide.md`](users-guide.md), which describes CLI behaviour from
an operator's perspective, and
[`docs/concordat-design.md`](concordat-design.md), which specifies the
broader estate-audit architecture. Where behaviour described here is planned
rather than shipped, that is called out explicitly; everything else is
derived from the current source.

## Development environment and gates

`uv sync --group dev` installs the development dependency group. The group
is declared in `pyproject.toml` under `[dependency-groups]` as `dev`, and
pulls in pytest, pytest-xdist, pytest-bdd, pytest-asyncio, pytest-mock,
ruff, pyright, pytest-timeout, betamax, hypothesis, and textual. The
`Makefile`'s `build` target runs `uv sync --group dev` as part of setting up
the virtual environment.

The type checker is pinned, not resolved at run time. `Makefile` declares
`TY_VERSION ?= 0.0.65` and `TY := uv tool run ty@$(TY_VERSION)`; the
`typecheck` target invokes `$(TY)` throughout. An unpinned `ty` meant CI and
a local checkout could run different versions of the tool and disagree
about which diagnostics were real; pinning the version in one Makefile
variable, and having every invocation read it from there, closes that gap.
`ty` is deliberately absent from the Makefile's `TOOLS` list — the CLI
tools whose presence `make` verifies with `command -v` — because it is
fetched on demand at the pinned version via `uv tool run` instead of being
expected to already be on `PATH`.

## XDG layout and owner namespaces

`concordat/xdg.py` is the single source of truth for where concordat reads
and writes. Three roots are resolved from the XDG base-directory environment
variables, each falling back to the conventional default when the variable
is unset, relative, or empty (the XDG specification requires relative base
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

The OpenTofu provider plugin cache
(`tofu_plugin_cache_dir`, `$XDG_CACHE_HOME/concordat/tofu/plugin-cache`) is
the one cache path that is *not* owner-namespaced: provider binaries are the
same regardless of which owner's estate is being planned.

The **active owner** — the owner selected by `concordat owner use <owner>` —
is not itself namespaced. It is the single `github_owner` key in the
**headline configuration file**, `$XDG_CONFIG_HOME/concordat/config.yaml`
(`headline_config_path`). `get_active_owner` reads that key; `set_active_owner`
validates the owner and rewrites the file, preserving any other keys already
present so the headline file can grow additional settings without one writer
clobbering another's.

Every owner-derived path is built through `validate_owner`, which accepts
names that begin and end with an alphanumeric character and may contain
alphanumerics and hyphens internally, including doubled internal hyphens
(the pattern is `^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$`). Owner names
reach this validation before they are joined into a filesystem path, so a
malformed owner argument fails fast rather than producing a path that
quietly bypasses namespacing.

### Legacy-flat migration

Before owner namespacing existed, concordat wrote estates directly into the
same file that is now the headline config:
`$XDG_CONFIG_HOME/concordat/config.yaml`. **The legacy flat file and the XDG
headline config are the same `config.yaml`** — migration does not move data
between files so much as separate two concerns that used to share one file.

`concordat.estate_config.migrate_legacy_config` is the explicit,
side-effecting migration step, invoked once at CLI bootstrap
(`concordat.cli.main`) so that `default_config_path` stays a pure read-only
query for every command. It is a no-op once an active owner is already
configured, once the flat file has no `estate` section, or once the estate
section cannot be attributed to exactly one owner:

- `_derive_owner_from_estates` collects every `github_owner` recorded across
  the legacy estates. The legacy format permitted estates for more than one
  owner in one file; migrating such a section under the first owner
  encountered would silently misplace the other owners' estates, so
  mixed-owner input is rejected with an error rather than migrated.

When migration proceeds, the steps run in this deliberate order:

1. **Write the owner-scoped config.** The estate section is written whole
   into `owners/<owner>/config.yaml`.
2. **Set the active owner.** The headline file's `github_owner` key is set
   to the derived owner.
3. **Remove the legacy estate section last.** The `estate` key is dropped
   from the flat file (rewriting it if other keys remain, deleting it
   outright if the estate section was its only content).

This order is load-bearing, and the ordering is deliberate rather than
incidental:

- The active owner is what points `default_config_path` at the newly
  migrated, owner-scoped file. Setting it only *after* the owner-scoped
  write is complete means a reader never observes an active owner whose
  file is not yet populated.
- Cleanup is the only step allowed to fail, because it is placed last. Were
  the legacy section removed first, a failure between that removal and the
  owner-scoped write would leave the estates in neither place the CLI
  looks: the flat file no longer holds them, and no active owner yet
  selects the owner-scoped file — an unrecoverable state, since the
  migration loader then finds no estate section to retry from. With cleanup
  last, a failure there instead leaves the estate data duplicated (already
  live in the owner-scoped file, still present in the stale legacy
  section) — duplicated but reachable beats complete but invisible.

Because the legacy file and the headline config are one and the same, step 2
(`set_active_owner`) writes into the very file step 3 is about to edit.
Cleanup therefore reloads the file's current contents from disk
(`_current_legacy_data`) rather than reusing the snapshot read before step 2:
rewriting that earlier snapshot would silently erase the `github_owner` key
step 2 just wrote, and deleting the file outright (when the estate section
was its only original content) would discard the key entirely.

## Credentials

`concordat/credentials.py` resolves secrets in a fixed, three-level priority
order, from highest to lowest:

1. **An explicit CLI flag** (for example `--github-token`), resolved by
   the CLI layer itself, in `concordat/cli.py`.
2. **A process environment variable** (for example `GITHUB_TOKEN`).
3. **The active owner's credentials file**,
   `$XDG_CONFIG_HOME/concordat/owners/<owner>/credentials.yaml`.

`credential_environment` implements the lower two levels: it overlays the
process environment with values loaded from the owner's credentials file,
using `dict.setdefault` so an environment variable that is already set is
never overridden by the file. `concordat.cli._github_token_fallback` (and
equivalent per-command fallbacks) call this only when the CLI flag itself is
absent, giving the full three-level order.

Only the names in `CREDENTIAL_KEYS` are honoured — `GITHUB_TOKEN`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`,
`SCW_ACCESS_KEY`, `SCW_SECRET_KEY`, `SPACES_ACCESS_KEY_ID`, and
`SPACES_SECRET_ACCESS_KEY` — anything else in the file is silently ignored.

Two defensive details are worth knowing when working on this module:

- **Group- or world-accessible files are refused, not read.** `load_credentials`
  checks the file's mode bits before parsing it; any group or world
  permission bit (including setuid/setgid) raises
  `InsecureCredentialsError` rather than reading a file that might be
  readable by other local users. The fix is `chmod 600`.
- **Only genuine non-blank strings are honoured; non-string values are
  dropped, not coerced.** `_recognised_credentials` requires both the key
  and the value to be `str` instances, and the value to be non-blank after
  stripping. A credential becomes an environment variable, so coercion would
  be actively harmful: an empty `KEY:` would coerce to the literal string
  `"None"`, and a YAML `false` would coerce to `"False"` — either handed to
  a remote as though it were a real secret. Non-string values (a YAML `null`
  under a key, a boolean, a number) are therefore dropped rather than
  stringified.

Concordat never writes this file; it is entirely operator-managed.

## API boundaries

Four modules define the layering between "what path does this data live
at" and "what does OpenTofu actually do with it":

- **`concordat.credentials`** and **`concordat.xdg`** have no dependency on
  git or provisioning code. `concordat.xdg` is pure path/config
  arithmetic; `concordat.credentials` builds on it for owner resolution but
  touches no git state. This keeps both modules importable from anywhere
  else in the codebase without pulling in `pygit2`.
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
  `EstateCacheError` surfaces to callers as `EstateExecutionError`,
  keeping one error hierarchy per layer. `estate_workspace` is the
  context manager that ties caching, temp-workspace cloning
  (`clone_into_temp`), and cleanup together; it resolves the owner's XDG
  state `runs/` directory the same way `estate_cache` resolves the owner's
  cache directory, so kept workdirs (`--keep-workdir`) land somewhere
  predictable.

### Estate module boundaries

`concordat.estate` is the public façade for estate management; the modules
below sit beneath it, and `concordat.estate` imports each of them, never
the reverse:

- **`concordat/estate_config.py`** — configuration persistence and
  migration: loading and writing the owner-scoped estate configuration, the
  legacy-flat migration (see [Legacy-flat
  migration](#legacy-flat-migration)), and owner normalization.
- **`concordat/estate_errors.py`** — the estate exception taxonomy. It is a
  leaf module with no dependency on git or GitHub code, so any other layer
  can import it without risking an import cycle.
- **`concordat/estate_git.py`** — git operations behind `concordat estate
  init` and `concordat ls`: remote probing, inventory collection from a
  clone, and template bootstrapping for a new estate. It knows nothing
  about the GitHub API or the estate-init decision flow.
- **`concordat/estate_github.py`** — the GitHub API calls concordat makes
  when an estate repository must be created, and the translation of
  github3's authentication failures into the estate error taxonomy. It
  knows nothing about git or the estate-init decision flow.
- **`concordat/estate_repository.py`** — the *decisions* `concordat estate
  init` makes (which owner an estate belongs to, whether its remote needs
  provisioning), delegating the *how* to `estate_github` and `estate_git`.
  Its imports are deliberate, not incidental: it is the single lookup site
  the `concordat.estate` façade calls through, and the single seam the
  test suite monkeypatches (see [Module-level monkeypatch
  seams](#module-level-monkeypatch-seams)).

## `concordat artefact rule run`

`concordat/rules/runner.py` and `concordat/rules/envelope.py` implement the
rule-run subcommand exposed as `concordat artefact rule run <rule-id>`.

### The policy envelope

`build_envelope` (in `envelope.py`) assembles a `policy-input/
rust-makefile-baseline` document (schema version 1) describing one local
checkout: whether a root `Cargo.toml` and `Makefile` exist, the parsed
`Cargo.toml` table (or `None`), and the validated `makeutil` report for the
`Makefile` (or `None`). Root `Cargo.toml` presence is documented as
*provisional* evidence of Rust applicability — the `.concordat` manifest
remains the eventual authority, per the module docstring, which points at
"the Parabellum ExecPlan decision log" for that decision. This document is
handed to Conftest as the input under audit.

### Tool dependencies

Two external tools must be on `PATH`:

- **`makeutil`** (`concordat/rules/makefile_facts.py`) — the sole means by
  which concordat inspects a `Makefile`; the module docstring states
  plainly that "Concordat never parses GNU Make syntax itself". `makeutil
  parse` is run with a 10-second default timeout, and its exit code
  (0 = complete parse, 1 = recovered parse) must agree with the `parse.status`
  field of its own JSON report, or the report is rejected as internally
  inconsistent.
- **`conftest`** (`concordat/rules/runner.py`) — evaluates the envelope
  against the rule package's Rego policy, with a 60-second timeout
  (`CONFTEST_TIMEOUT`).

### The `OperationalRuleError` contract

`OperationalRuleError` (`concordat/errors.py`) is raised whenever rule
evaluation could not run at all — as distinct from a policy finding, which
is a successful evaluation that happens to report noncompliance. It carries
three pieces of context:

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
exit codes: `0` compliant, `1` at least one finding (including
indeterminate, which fails closed), `2` operational failure — an uncaught
`OperationalRuleError` is caught in `concordat.cli.main` and converted to
exit code 2, printed to standard error, distinct from the `1` that
`ConcordatError` maps to.

### Rule package identifier validation

Rule package identifiers are validated against a canonical pattern —
lower-case ASCII words joined by single hyphens
(`^[a-z0-9]+(?:-[a-z0-9]+)*$`) — **before** any filesystem access.
`_rule_package_dir` then joins the validated identifier to the packages
root and confirms the resolved path stays under that root even though the
pattern alone already excludes traversal characters; the containment check
exists so that a future loosening of the pattern cannot silently reach
outside the root. The root itself comes from `_rule_packages_dir()`, a
cached lookup performed on first use rather than at import, so a missing or
unreadable rule tree surfaces when a rule runs instead of when the module
is imported.

### Conftest exit codes

Only Conftest exit codes `0` (no policy failures) and `1` (policy failures)
are treated as policy verdicts; both are expected to emit a JSON result
document on stdout. Any other exit code — a malformed policy, a bad flag, a
missing input file — means Conftest did not evaluate the policy at all, even
if it printed something on stdout that looks like JSON.
`_require_policy_exit_code` rejects those with an `OperationalRuleError`
rather than risk decoding an operational failure as though it were a clean
run.

### Packaging: installed package data and the source distribution

The build backend is setuptools (`[build-system]` in `pyproject.toml`:
`requires = ["setuptools>=61.0", "wheel"]`,
`build-backend = "setuptools.build_meta"`).

`[tool.setuptools] packages` lists every shipped package explicitly:
`concordat`, `concordat.auditor`, `concordat.persistence`,
`concordat.rules`, `concordat.canon`. The list is explicit rather than
`packages.find` because `concordat.canon` is an out-of-tree data package
that has to be named — that rules out `find`, so the in-tree subpackages
are listed alongside it rather than discovered automatically.

`[tool.setuptools.package-dir]` maps `"concordat.canon" =
"platform-standards/canon"`, and `[tool.setuptools.package-data]` ships
`"concordat.canon" = ["lint-rules/**/*"]`. So the canon lint-rule tree
lands under `concordat/canon/lint-rules` in the wheel, which is the point:
`concordat artefact rule run` has to work from an installed wheel, not only
a source checkout. The runner resolves the rule-package tree through
`importlib.resources`, with a source-checkout fallback — see
`_rule_packages_dir()` above.

`MANIFEST.in` controls the source distribution, and grafts three trees:
`platform-standards`, `scripts`, and `tests`. This preserves the sdist
contents the previous build backend shipped, per the file's own comment.

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
  leading/trailing hyphen, capped at 39 characters (GitHub's own owner
  length limit). This is a stricter, length-bounded sibling of
  `concordat.xdg`'s `_OWNER_PATTERN`, which has no length cap; the two are
  independent patterns maintained separately, not a shared constant.
- `_REPO_NAME_PATTERN` — alphanumerics, dot, underscore, and hyphen, 1–100
  characters, with at least one non-dot character (so `.` and `..` cannot
  be smuggled in as a "repository name" that later becomes a clone-directory
  component).

Both are checked again in `clone_and_audit` immediately before the values
are interpolated into a clone URL and a scratch-directory path, not only at
manifest-load time — belt and braces for any caller reaching `clone_and_audit`
directly rather than through `load_estate`.

### The append-only ledger and its idempotency rule

`docs/parabellum/ledger.jsonl` (by default) is an append-only JSON Lines
file: one JSON object per line, never rewritten or truncated
(`_append_record` opens the file in append mode and writes exactly one
record per call). Each record is durable — flushed to disk — before the
sweep moves on to the next repository, because auditing the whole estate
takes many minutes and clones over the network; an interrupted sweep resumes
from where the ledger left off rather than restarting.

**Idempotency rule:** a repository is skipped, rather than re-audited, when
the ledger already holds a record for that repository at the same
`commit_sha` (`_already_ledgered`). Excluded entries use a variant of this
rule keyed on `verdict == "excluded"` instead of a commit, since an
exclusion has no commit to compare against. `--force` bypasses the
commit-based skip (but not the exclusion skip, which unconditionally
prevents duplicate exclusion records for the same repository).

### The git boundary

All git operations funnel through the module-private `_git` helper, which
shells out to the `git` binary (`subprocess.run`, fixed argv, no shell) with
a 300-second timeout (`GIT_TIMEOUT`). Every call site supplies an
`operation` and `resource` for the resulting `OperationalRuleError` if the
command is missing, times out, or exits non-zero. Two call sites build on
`_git`:

- `resolve_head(owner, name)` runs `git ls-remote <url> HEAD` to obtain the
  default-branch head SHA **without cloning** — used to decide, cheaply,
  whether a repository has already been ledgered at its current head before
  paying for a clone.
- `clone_and_audit(owner, name)` performs a shallow, single-branch clone
  (`git clone --depth 1 --quiet`) into a temporary directory, resolves
  `HEAD` there with `git rev-parse HEAD`, and hands the checkout to
  `concordat.rules.run_rule` for the `rust-makefile-baseline` audit. The
  sweep is audit-only: nothing here ever writes to an estate repository.

## Property tests and the bounded reachability contract

### Hypothesis property tests

`tests/unit/test_properties.py` holds concordat's Hypothesis-based property
tests. Its module docstring states the discipline the whole file follows:
"where a property restates a regex, it is written from the specification
rather than the implementation's pattern, so the two can disagree" — a
property test that reimplements the code under test proves nothing, so
each property is derived from the documented rule instead. The file covers:

- **the owner-name grammar** (`TestOwnerNames`) — acceptance against
  `xdg.validate_owner` agrees with a grammar written independently of
  `_OWNER_PATTERN`, well-formed names round-trip unchanged, and no name
  containing a path separator is ever accepted;
- **credential filtering** (`TestCredentialFiltering`) — every value that
  survives `credentials._recognised_credentials` is a recognized key with a
  trimmed, non-blank string;
- **rule-package identifiers** (`TestRulePackageIdentifiers`) — acceptance
  by `runner._validated_rule_id` agrees with the canonical hyphenated-words
  grammar, and a rejected identifier never reaches the filesystem;
- **manifest repository names** (`TestManifestRepositoryNames`) — every
  name accepted by `sweep._validated_identifier` is a single, safe path
  component that cannot escape the directory it is joined to; and
- **ledger record selection** (`TestLedgerSelection`) — over a generated
  append-only history, the latest record for a repository is the last one
  appended.

`test_a_component_joined_to_a_root_stays_inside_it` joins a generated name
to a real directory rather than a `tmp_path` fixture: Hypothesis rejects
function-scoped fixtures, since they would be created once and then shared
across every generated example rather than being fresh per example.

### The bounded Rego reachability test

The rule package's `policy/rust_makefile_baseline_test.rego`, under the
`-- bounded reachability contract --` banner, enumerates `lint` prerequisite
chains of increasing depth over one envelope. QG-001 proves gate delegation
within one prerequisite hop, so this suite pins the boundary between
"provable" and "indeterminate" rather than sampling it: depth 0 (a direct
gate invocation) and depth 1 (one hop of delegation) are compliant, and
every deeper chain is indeterminate. `build` and `test` targets are kept
present in every case so FP-003 stays silent and QG-001 is the only
variable under test.

This policy suite is not wired into the Makefile. It is run directly with
Conftest:

```shell
cd platform-standards/canon/lint-rules/rust-makefile-baseline
conftest verify --policy policy --data fixtures/data.json
```

## Test seams and subprocess contracts

The suite substitutes real subprocesses and network access with two
distinct mechanisms, depending on whether the code under test shells out
directly or calls another concordat function that does.

### `cmd_mox`: the subprocess-mocking harness

`tests/conftest.py` defines a small, purpose-built `CmdMox` harness (not the
similarly-named third-party `cmdmox` library) and exposes it as the `cmd_mox`
pytest fixture. It monkeypatches `subprocess.run` globally for the duration
of a test (`CmdMox.replay`), so it intercepts *any* subprocess invocation —
`makeutil`, `conftest`, `git` — regardless of which module issued it.
Expectations are queued with a fluent builder:

```python
cmd_mox.mock("conftest").with_args("test", "--policy", ...).returns(
    exit_code=0, stdout="[]"
)
```

Each queued expectation is consumed in order (`collections.deque`); an
unexpected command, a command-name mismatch, or an argument mismatch raises
immediately, and any expectations left unconsumed at the end of a test raise
via `CmdMox.verify`.

### Module-level monkeypatch seams

Where concordat code calls another concordat function directly (rather than
shelling out), tests patch that function on the module attribute the caller
actually resolves at call time — which is not always the function's
*defining* module:

- **`concordat.estate_repository._probe_remote`** — `estate_repository.py`
  imports `_probe_remote` from `concordat.estate_git` and calls it as a bare
  name, so patching `concordat.estate_git._probe_remote` would leave
  `estate_repository`'s already-bound reference untouched. A comment above
  the import states explicitly that tests must patch
  `estate_repository._probe_remote` (along with `._build_client` and
  `._create_repository`) — the name as it appears in `estate_repository`'s
  own namespace.
- **`scripts.parabellum_sweep.resolve_head`** — `resolve_head` is defined
  directly in `parabellum_sweep.py`, so there is no import indirection to
  worry about: tests import the module (commonly aliased `sweep`) and
  monkeypatch `sweep.resolve_head` directly, replacing the network-touching
  `git ls-remote` call with a fixed SHA or a function that raises
  `OperationalRuleError`, without needing `cmd_mox` at all.

The rule of thumb: if the code shells out via `subprocess.run`, reach for
`cmd_mox`; if it calls a sibling concordat function, monkeypatch that
function on the module that does the calling.
