# whitaker-provisioning

Audits how a checkout provisions the Whitaker lint suite. The audit is
read-only. It evaluates a `policy-input/whitaker-provisioning` envelope built by
`concordat artefact rule run`; it never runs a workflow command or a script.

## The rule

The estate provisions Whitaker one way:

```yaml
- uses: leynos/shared-actions/.github/actions/install-whitaker@<certified revision>
```

That action carries the install rules. It installs an exact
`whitaker-installer` version, never pins the lint suite (the lints are a
rolling release), and always passes `--no-source-fallback`, so a missing
published lint library or Dylint tool archive fails the run instead of being
compiled. The action's own contract in shared-actions proves those rules. This
rule therefore does not re-check installer flags; it proves that the action is
the only route, and that every use of it names a certified revision (see
below).

## Certification

A revision of the action is certified by content, and the approval lives in
shared-actions, so concordat changes nothing when shared-actions gains commits.
A maintainer reviews the action at a commit and creates an annotated tag
`certified/install-whitaker/<whitaker-installer version>` on it. A pinned full
SHA is certified when **all** of these hold:

1. it is on the action repository's default branch (a commit that exists only
   in a fork is not);
2. it equals, or descends from, the commit of a certification tag;
3. its `install-whitaker` runtime digest equals that tag's.

The digest hashes each runtime file's mode, blob id and path. Runtime files
are every file in the directory except the `tests/` tree and Markdown files,
which the action does not execute, so a change confined to those does not move
the digest. Any other file counts, including one added later, so an unknown
file fails closed. Descent alone would not do: a later commit could drop
`--no-source-fallback`. A commit that changes the runtime content (a new
installer release) therefore stays noncompliant until the maintainer tags it,
and a commit that leaves it alone needs nothing. Any certified tag certifies,
not only the newest, so a repository on an older approved release stays
compliant while its tag exists; deleting a tag retires that release.

The checkout holds no shared-actions history, so the rule-run command asks the
GitHub API (tags, ancestry, the directory's tree) and records the answer for
each pinned SHA in the envelope's `install_certifications`; the policy reads
that fact. See "What the rule reports" below for how each answer is judged.

`scripts/whitaker_revisions.py certify <clone> <commit> --installer-version
<version>` checks a candidate (on `main`, holds the action, runtime files name
no `tests/` or Markdown path) and prints the `git tag -a` and `git push`
commands, which it never runs. `list <clone>` prints each floor and the
first-parent commits the floors certify.

### What the rule reports

A pin is judged on the recorded answer:

- certified: no finding;
- not certified (not on `main`, older than or unrelated to every tag, changed
  runtime content, or a SHA GitHub does not know): noncompliant;
- unknown (offline, rate limited, refused, a truncated tree) or absent from the
  envelope: indeterminate, never a pass.

A tag, a branch, a short SHA, a fork or a local copy is noncompliant without
asking anyone.

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
- An `install-whitaker` use that is not a certified revision: a tag, branch or
  short SHA, a fork of the action, a local copy of it, or a full SHA the
  certification above refuses. The message says why, and how a revision is
  certified.
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

- `certification_tag_prefix` — the prefix of the certification tags in the
  action repository, followed by a `whitaker-installer` version. Default:
  `certified/install-whitaker/`. A tag certifies the commit it points at and
  its descendants on the default branch that keep the action's runtime content
  (see Certification). Approving a floor includes checking that its runtime
  files reach no excluded content (tests or Markdown) by an indirect route such
  as a glob or a computed path: the `certify` scan only refuses literal
  `tests/` and `.md` mentions.
- `action_branch` — the action repository's default branch. Default: `main`.
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
  `uv run python fixtures/generate.py` from this directory. Each pin's
  certification is decided by the production decision over a synthetic action
  repository (one floor, and an unchanged, a changed, an older, an off-branch,
  an unknown and an unreadable commit), not written by hand.
