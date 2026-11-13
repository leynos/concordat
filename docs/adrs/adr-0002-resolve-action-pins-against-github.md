# ADR-002: Resolve action pins against GitHub

**Date:** 2026-09-28

**Status:** Accepted

## Context

PD-006 requires CI to lint Markdown through
`DavidAnson/markdownlint-cli2-action` pinned to a full commit SHA. The policy
checked the pin's shape only: forty lowercase hexadecimal characters. An
annotated tag object also has a forty-character SHA, and GitHub Actions accepts
it as a `uses:` ref, so a workflow pinned to one runs green while pinning a
mutable tag rather than the commit the baseline asks for.

The estate's canonical pin was such an object. `4580e161…` is the annotated tag
object of `v24.2.0`, which peels to the commit `21c1be1b…`. The rule package's
own fixtures carried it, and adoption pull requests copied it, so the defect
reached several repositories before anyone read the object type.

A checkout cannot tell the two apart. The SHA names an object in the action's
repository, not in the checkout under audit, and only that repository's object
store knows whether it is a commit or a tag.

## Decision

The rule-run command resolves every full-SHA pin of the action through the
GitHub REST API and records the answer as the envelope's `action_pins` fact:

- `git/commits/{sha}` answering means the pin names a commit;
- otherwise `git/tags/{sha}` answering means the pin names an annotated tag
  object, and the tag's target is recorded as the peeled commit;
- anything else — neither endpoint knows the SHA, the API refuses or rate
  limits, the network is unreachable, or the reply cannot be decoded — leaves
  the pin unresolved with its reason.

The policy judges the fact. A tag object is noncompliant, and the finding names
the commit it peels to so the fix is a copy. An unresolved pin, or a pin the
envelope does not mention, is indeterminate: offline, the rule cannot prove the
pin names a commit, so it does not pass.

Resolution happens at the command boundary, not in the envelope query.
`build_markdown_envelope` reads the checkout and leaves `action_pins` empty;
`with_action_pins` is the separate step that calls a resolver, and
`packages.resolving_pins` composes that step onto the envelope builder. The
domain (`action_pins.py`) knows nothing of HTTP; the adapter (`github_pins.py`)
reuses the auditor's `GithubClient` rather than a second client.
`concordat artefact rule run` constructs the adapter with a client factory that
reads the token on first use, so a checkout with no pin reads no credentials,
and an unreadable credentials file is an operational failure. The fixture
generator and the tests supply their own resolvers or point the command at a
local API double, so none of them reaches GitHub.

## Consequences

- `concordat artefact rule run markdown-formatting-baseline` makes up to two
  unauthenticated or token-authenticated GET requests per distinct pin. A
  checkout with no pinned action makes none and reads no credentials.
  `--github-api-url` selects another API root.
- The rule is no longer a pure function of the checkout for PD-006. An offline
  run reports PD-006 indeterminate on every pinned workflow, which is the
  fail-closed behaviour the package already applies to facts it cannot prove.
- Envelopes built before this change carry no `action_pins`, and the policy
  reports their pins indeterminate rather than trusting them.
- A caller that builds the envelope without the command step, such as
  `run_rule` with the default builder, gets every pin indeterminate rather than
  trusted.
- `concordat/rules/github_pins.py` is the only place that asks GitHub what an
  action pin names. Another rule package that needs the same fact composes
  `resolving_pins` or calls `with_action_pins`, rather than calling the API
  from its envelope builder.

## Addendum (2026-09-28): The API budget

The first estate sweeps showed the API budget is the operational limit.
Unauthenticated, GitHub allows 60 calls an hour, which a sweep spends within a
few repositories; every later pin was then indeterminate, with a message that
read like an unknown pin.

- The command authenticates with `GITHUB_TOKEN` or the concordat credentials,
  and otherwise with `gh auth token --hostname <host>`, where the host is
  derived from `--github-api-url` (`api.github.com` is `github.com`), so a
  token is never sent to a host it was not issued for.
- `GithubClient` raises `GithubRateLimitError`, a `GithubForbiddenError`, for a
  429 or a 403 with `X-RateLimit-Remaining: 0`. The pin is recorded with
  `rate_limited: true`, and PD-006 reports it as indeterminate with its remedy:
  wait for the limit to reset, or run with a token that has quota left.
- `GithubPinResolver` keeps each answer for the run, so a pin repeated within
  one `rule run` costs one lookup. Each run is one repository; a pin shared
  across repositories costs one lookup per repository.

## Addendum (2026-09-29): The on-disk cache

The per-run memo does not help a sweep: each repository is a separate process,
so a pin shared across repositories still costs a lookup in each. What a SHA
names in an action repository never changes, so `concordat/rules/pin_cache.py`
keeps definite answers on disk.

- **What is cached.** A commit, or an annotated tag object with its peeled
  commit (which may be absent). An unresolved pin is never cached: its reason
  is a spent rate limit, an outage or a refusal a new token would lift, and
  keeping it would freeze the reason past its cause.
- **Key.** The API root, then `owner/repository` and `sha`, lower-cased, one
  file per key. Whether a SHA names a commit depends on the host asked, so each
  normalized API root (lower-cased, trailing slash removed) has its own
  directory below the cache directory, named by the first sixteen hexadecimal
  digits of its SHA-256; a github.com answer is never served for a GitHub
  Enterprise Server, and the reverse. A repository that is not a plain
  `owner/name` pair of `[A-Za-z0-9][A-Za-z0-9_.-]*` segments, or a pin that is
  not forty lowercase hexadecimal digits, is not a key: the repository comes
  from a workflow the audit does not control and becomes a path.
- **Format.** One JSON object per file, version 1:

  ```json
  {"version": 1, "api_root": "https://api.github.com",
   "repository": "davidanson/markdownlint-cli2-action",
   "sha": "21c1be1b93ad9ed58fa840aacc3f279cde2a72ff",
   "object_type": "commit", "commit": "21c1be1b93ad9ed58fa840aacc3f279cde2a72ff"}
  ```

  `object_type` is `commit` or `tag`. For a commit, `commit` equals `sha`; for
  a tag it is the peeled commit or `null`. `api_root`, `repository` and `sha`
  repeat the key, and an entry that disagrees with the path it sits at is
  corrupt. A change to this shape bumps `version`, and an entry of any other
  version is read as corrupt.
- **Atomic writes.** An entry is written to a temporary file in its own
  directory and renamed into place, so a run killed mid-write leaves the old
  entry or none, and concurrent runs cannot interleave.
- **Reads change nothing.** `PinCache.get` is a query. It returns a
  `CacheRead` whose outcome is one of `hit`, `miss`, `corrupt`, `unreadable` or
  `bypassed` (an unsafe key), so the caller can tell the cases apart. Deleting
  is the separate command `PinCache.discard`.
- **Corrupt entries.** An entry that is not valid JSON or not valid UTF-8, has
  another version, names another key, or describes an answer no resolver would
  give, reads as `corrupt`. `cached_resolver` discards it and asks GitHub, so a
  lookup that then fails cannot leave the bad entry behind. A cache that cannot
  be read or written costs a warning, never the audit.
- **Observability.** Each read's outcome is logged at debug level as one of the
  five fixed categories, never with entry content. Concordat has no metrics
  facility, and this change does not add one for a local cache.
- **Location and switches.** `--pin-cache-dir`, then `CONCORDAT_PIN_CACHE_DIR`,
  then `$XDG_CACHE_HOME/concordat/action-pins`, then
  `~/.cache/concordat/action-pins`. `--no-pin-cache` neither reads nor writes.
- **Layering.** `cached_resolver` wraps the resolver the command builds, so the
  per-run memo in `GithubPinResolver` remains the inner layer and the domain
  module `action_pins.py` still knows nothing of storage.
