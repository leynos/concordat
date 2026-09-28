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
  GET requests per distinct pin, and answers a pin repeated within the run from
  the resolver's own record. A checkout with no pinned action makes none and
  reads no credentials. `--github-api-url` selects another API root.
- The API budget is the operational limit. Unauthenticated, GitHub allows 60
  calls an hour, which an estate sweep spends within a few repositories, so the
  command authenticates with `GITHUB_TOKEN`, the concordat credentials, or
  `gh auth token`, in that order. A lookup refused because the limit is spent
  (429, or 403 with `X-RateLimit-Remaining: 0`) is recorded with
  `rate_limited: true`, and PD-006 reports it as indeterminate with the remedy
  instead of as an unknown pin.
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
