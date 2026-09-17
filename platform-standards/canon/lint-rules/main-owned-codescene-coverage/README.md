# main-owned-codescene-coverage

Audits the GitHub Actions topology that keeps CodeScene coverage publication on
`main`. The audit is read-only. It evaluates a decoded-workflow envelope built
by `concordat artefact rule run`; it never executes workflow commands or sends
coverage data.

## CV-005

The rule requires all of the following:

- Every `generate-coverage` step in a pull-request workflow uses
  `with-ratchet: true`, and its effective Rust and Python baseline paths match
  a ratcheting generator in the main publisher.
- Pull-request workflows do not invoke a CodeScene action or a direct
  `cs-coverage check`/`upload` command, and do not receive `CS_ACCESS_TOKEN`.
- A workflow with a `push` trigger restricted to `main`, optionally alongside
  `workflow_dispatch`, generates ratcheted coverage and invokes the CodeScene
  action with `mode: upload`, or runs a direct `cs-coverage upload` command.

Malformed workflow YAML, unsupported job shapes, and reusable workflow jobs are
`indeterminate`. The rule fails closed because a local audit cannot inspect the
delegated workflow or infer arbitrary GitHub Actions expressions.

## Validation

From the repository root, run:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/main-owned-codescene-coverage/policy \
  --data platform-standards/canon/lint-rules/main-owned-codescene-coverage/fixtures/data.json

concordat artefact rule run main-owned-codescene-coverage --repo /path/to/checkout
```
