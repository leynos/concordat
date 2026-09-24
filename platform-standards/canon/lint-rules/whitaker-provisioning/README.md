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
  script that is a symlink leaving the checkout.
- **EN-001** (error, indeterminate): the envelope is not a schema-1
  `policy-input/whitaker-provisioning` document.

## What the rule declines to judge

The policy recognizes commands; it does not interpret shell. A command
assembled at run time from variables, or a tool installed by a reusable
workflow in another repository, is not recognized. Shell and Make comments are
prose, and a continued line is read as one command.

These are not routes, and fixtures prove each stays compliant: caching
`~/.cargo/bin/whitaker-installer` in an `actions/cache` `path`, an `echo` or a
`test -x` naming it, running `whitaker --all`, and a script that downloads an
unrelated tool. Test code (a `test`, `tests`, `__tests__` or `fixtures`
directory, `test_*` and `*_test` files) is not automation and is not read. A
file that is not UTF-8, or that has a NUL byte near its start, is a binary
rather than a script and is not read.

## Parameters

- `compliant_install_whitaker_refs` — the shared-actions revisions a pin may
  name. The first entry is the merge of shared-actions #522, the change that
  made the action carry the install rules. **A revision joins the list only once
  `git merge-base --is-ancestor <first entry> <revision>` succeeds in a
  shared-actions clone**, so every entry descends from it. The check cannot be
  made at audit time: the audited checkout does not hold shared-actions'
  history.
- `producer_repositories` — repositories that build and publish Whitaker and
  are not audited. Default: `leynos/whitaker`.
- `action_repository` and `action_directory` — where the action lives. The
  owning repository may run the action from its own checkout (`./` or `$/`),
  and the action's own files are the sanctioned route, not a second one.
- `exemptions` — named scripts that are not audited, each with its
  `repository`, `path` and `reason`. Only developer-environment scripts that CI
  never runs belong here. An exemption names its repository, so the same path
  elsewhere is still audited. Default:
  - `leynos/agent-helper-scripts`, `get-rust-tooling`: a developer-environment
    bootstrap run by hand, never by CI. It pins `whitaker-installer` 0.2.9 and
    passes `--no-source-fallback`.

Exemptions and producers match the repository's GitHub slug, read from the
checkout's `origin` remote. A checkout without a GitHub origin matches none.

## Layout

- `rule.yaml` — package manifest: sensor, input kind, parameters, defaults.
- `policy/` — the Rego policy and its tests.
- `fixtures/repos/` — one miniature checkout per behaviour.
- `fixtures/envelopes/` and `fixtures/data.json` — the envelopes the
  production builder records for each checkout, regenerated with
  `uv run python fixtures/generate.py` from this directory.
