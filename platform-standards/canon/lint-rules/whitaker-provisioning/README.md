# whitaker-provisioning

Audits how a checkout provisions the Whitaker lint suite. The audit is
read-only. It evaluates a `policy-input/whitaker-provisioning` envelope built by
`concordat artefact rule run`; it never runs a workflow command or a script.

## The rule

The estate provisions Whitaker one way:

```yaml
- uses: leynos/shared-actions/.github/actions/install-whitaker@<listed revision>
```

That action carries the install rules. It installs an exact
`whitaker-installer` version, never pins the lint suite (the lints are a
rolling release), and always passes `--no-source-fallback`, so a missing
published lint library or Dylint tool archive fails the run instead of being
compiled. The action's own contract in shared-actions proves those rules. This
rule therefore does not re-check installer flags; it proves that the action is
the only route, and that every use of it names a revision known to carry them.

## Checks

Every finding is **QG-002** (error) in the design document's catalogue; the
message says which of the following it is.

- A route other than the action. Refused wherever CI could
  take it: a workflow or composite-action `run:` body, a Makefile (`Makefile`,
  `GNUmakefile`, `*.mk`), or a script under `.github/`, `bin/`, `ci/`,
  `scripts/` or `tools/`, or at the root with a shebang. A route is:
  - an install of `whitaker-installer`, `cargo-dylint` or `dylint-link` by
    `cargo install` (including `--git`/`--rev` and `cargo +toolchain install`),
    `cargo binstall`, `cargo quickinstall` or `cargo-binstall`;
  - `whitaker-installer` run directly, in command position;
  - a download: a fetch by an HTTP client (`curl`, `wget`, `Invoke-WebRequest`,
    Python's `urlopen`/`urlretrieve`, `requests.get`, `httpx.get`),
    `gh release download`, or `gh api` against a releases endpoint, in a script
    or step that also names a Whitaker tool or `leynos/whitaker`. The clause
    reads the whole script, because a release-asset installer names the asset
    in one line and fetches it in another; ortho-config #492's script, which
    fetches with `gh api` and never writes a download URL, is a fixture;
  - another action handed a Whitaker tool as an input, such as
    `taiki-e/install-action` or shared-actions `install-tool` with
    `tool: cargo-dylint`.
- An `install-whitaker` use pinned to a revision not in
  `compliant_install_whitaker_refs`, a fork of the action, or a local copy of
  it. The message says how a revision joins the list.
- An indeterminate finding for a surface the policy cannot read: a
  workflow or action that is not valid YAML, a script larger than 1 MiB, or a
  script that is a symlink leaving the checkout. A directory the audit cannot
  list is an operational error rather than a skipped subtree.
- **EN-001** (error, indeterminate): the envelope is not a schema-1
  `policy-input/whitaker-provisioning` document.

## What the rule declines to judge

The policy recognizes commands; it does not interpret shell. A command
assembled at run time from variables, or a tool installed by a reusable
workflow in another repository, is not recognized. Shell and Make comments are
prose, whether they fill a line or follow a command after whitespace outside
quotes, and a continued line is read as one command. A script is read when it
sits under `.github/`, `bin/`, `ci/`, `scripts/` or `tools/` with a common
interpreter suffix (`.sh`, `.py`, `.js`, `.rb`, `.pl` and the like) or a
shebang.

These are not routes, and fixtures prove each stays compliant: caching
`~/.cargo/bin/whitaker-installer` in an `actions/cache` `path`, an `echo` or a
`test -x` naming it, running `whitaker --all`, and a script that downloads an
unrelated tool. Test code (a `test`, `tests`, `__tests__` or `fixtures`
directory, `test_*` and `*_test` files) is not automation and is not read. A
file that is not UTF-8, or that has a NUL byte near its start, is a binary
rather than a script and is not read.

## Parameters

- `compliant_install_whitaker_refs` — the shared-actions revisions a pin may
  name. It is derived, never edited by hand: **every first-parent commit on
  shared-actions main that is an approved root, or descends from one and leaves
  the `install-whitaker` directory's Git tree id equal to that root's**. A tree
  id hashes the directory's contents, so the action a consumer runs is the
  approved one whatever else changed in shared-actions. The derivation cannot
  run at audit time, because the audited checkout does not hold shared-actions'
  history; `scripts/whitaker_revisions.py sync --clone <shared-actions clone>`
  regenerates the list, and `check` fails when it has drifted. A commit that
  changes the directory is refused until a reviewer adds it to
  `install_whitaker_roots`.
- `install_whitaker_roots` — the reviewed shared-actions commits the list is
  derived from. The first is the merge of shared-actions #522, the change that
  made the action carry the install rules.
- `producer_repositories` — repositories that build and publish Whitaker and
  are not audited. Default: `leynos/whitaker`.
- `action_repository` and `action_directory` — where the action lives. The
  owning repository may run the action from its own checkout (`./` or `$/`),
  and the action's own files are the sanctioned route, not a second one.
- `exemptions` — named scripts that are not audited, each with its
  `repository`, `path` and `reason`. Developer-environment scripts that CI
  never runs belong here, and otherwise only a temporary entry whose reason
  names the change that removes it. An exemption names its repository, so the
  same path elsewhere is still audited. Default:
  - `leynos/weaver`, `.github/workflows/ci.yml`: Weaver pins the Whitaker lint
    suite to source revision `2bc0c3f` because leynos/whitaker#311
    (`no_expect_outside_tests` flags `cfg(test)` companion-module helpers since
    suite `4e8a8ab`) regresses its lint, and `install-whitaker` refuses a suite
    pin by design. It is removed by the change that fixes #311 and moves
    Weaver's step onto `install-whitaker`. Only that workflow is exempt; a
    second workflow in Weaver that installs Whitaker by hand is still a route.
  - `leynos/agent-helper-scripts`, `get-rust-tooling`: a developer-environment
    bootstrap run by hand, never by CI. It pins `whitaker-installer` 0.2.9 and
    passes `--no-source-fallback`.
  - `leynos/shared-actions`, `.github/workflows/test-install-tool.yml`:
    install-tool self-test; Dylint entries removed by follow-up. Its macOS leg
    asks `install-tool` for `cargo-dylint` to prove the off-Linux refusal. The
    follow-up, after shared-actions #488 merges, removes the Dylint entries
    from install-tool's manifest, moves that proof onto a synthetic Linux-only
    entry, and deletes this exemption.

Exemptions and producers match the repository's GitHub slug, read from the
checkout's `origin` remote. A checkout without a GitHub origin matches none.

## Layout

- `rule.yaml` — package manifest: sensor, input kind, parameters, defaults.
- `policy/` — the Rego policy and its tests.
- `fixtures/repos/` — one miniature checkout per behaviour.
- `fixtures/envelopes/` and `fixtures/data.json` — the envelopes the
  production builder records for each checkout, regenerated with
  `uv run python fixtures/generate.py` from this directory.
