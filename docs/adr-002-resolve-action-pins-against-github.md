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

The Markdown envelope builder resolves every full-SHA pin of the action through
the GitHub REST API and records the answer as the envelope's `action_pins` fact:

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

Resolution is injected. `build_markdown_envelope` takes a resolver whose
default leaves every pin unresolved; the production package builder supplies
the GitHub resolver, built on first use with the token from
`credentials.github_token()`. The fixture generator and the behavioural tests
supply a table, so neither reaches the network.

## Consequences

- `concordat artefact rule run markdown-formatting-baseline` makes up to two
  unauthenticated or token-authenticated GET requests per distinct pin. A
  checkout with no pinned action makes none and reads no credentials.
- The rule is no longer a pure function of the checkout for PD-006. An offline
  run reports PD-006 indeterminate on every pinned workflow, which is the
  fail-closed behaviour the package already applies to facts it cannot prove.
- Envelopes built before this change carry no `action_pins`, and the policy
  reports their pins indeterminate rather than trusting them.
- `concordat/rules/action_pins.py` is the only place that asks what an action
  pin names. Another rule package that needs the same fact reuses its resolvers
  and `resolve_pins` rather than calling the API itself.
