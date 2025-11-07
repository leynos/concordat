# concordat Users' Guide

## Overview

The `concordat` command line interface (CLI) helps maintainers enrol Git
repositories with Concordat. Enrolling creates a `.concordat` file at the
repository root. The file is a YAML 1.2 document with the key/value pair
`enrolled: true`. Downstream tooling relies on this marker to detect
participating repositories.

## Installing the CLI

1. Create or update the virtual environment:

   ```shell
   uv sync --group dev
   ```

2. Invoke the CLI with `uv run` to ensure the correct environment is used.

## Enrolling repositories

- Enrol one or more repositories by passing their paths:

  ```shell
  uv run concordat enrol path/to/repo-one path/to/repo-two
  ```

- When the repository already contains a `.concordat` file with `enrolled:
  true`, the CLI prints `already enrolled` and makes no changes.

- The CLI commits the new file to the current branch. If the Git configuration
  does not define `user.name` and `user.email`, supply details explicitly:

  ```shell
  uv run concordat enrol path/to/repo --author-name "Jess Example" \
    --author-email "jess@example.com"
  ```

- Pass `--push` to push the commit to the repository's `origin` remote after
  creation.

- Remote repositories reachable over Secure Shell (SSH) can be enrolled
  directly. Provide the SSH URL and ensure an SSH agent exposes the required
  key:

  ```shell
  uv run concordat enrol git@github.com:example/project.git
  ```

  The CLI clones the repository, creates the enrolment commit, and pushes it
  back to the remote.

## Disenrolling repositories

- Mark repositories as no longer enrolled by setting the `.concordat` flag to
  `false`:

  ```shell
  uv run concordat disenrol path/to/repo-one path/to/repo-two
  ```

- The CLI commits the change to the current branch and accepts the same
  `--push`, `--author-name`, and `--author-email` options as the enrol command.

## Listing repositories

- List every repository within one or more GitHub namespaces:

  ```shell
  uv run concordat ls leynos df12
  ```

  Each line is an SSH URL that can be passed directly to `concordat enrol`.

- Provide a personal access token with `--token` or the `GITHUB_TOKEN`
  environment variable when listing private repositories:

  ```shell
  uv run concordat ls --token "$GITHUB_TOKEN" my-org
  ```

## Running the squash-only merge plan

The `platform-standards/tofu` directory contains a runnable OpenTofu stack that
enforces the squash-only merge strategy (RS-002). The stack consumes
`platform-standards/tofu/inventory/repositories.yaml`, which now includes the
non-production `test-case/squash-only-standard` record so operators can
rehearse changes without touching production.

1. Set a placeholder GitHub token so the provider schema loads without reaching
   the API:

   ```shell
   export GITHUB_TOKEN=placeholder
   ```

2. Initialise the stack and preview the actions with the default `test-case`
   owner:

   ```shell
   cd platform-standards/tofu
   tofu init
   tofu plan -var github_owner=test-case -detailed-exitcode
   ```

   Exit code `2` indicates that OpenTofu would make changes (expected for the
   sample repository), while exit code `0` confirms convergence.

3. Override `github_owner` and extend `inventory/repositories.yaml` when you are
   ready to target additional organizations. The `github_owner` guard blocks
   accidental cross-org drift by asserting that every slug shares the
   configured owner.

### Validating the test-case standard end to end

Use the commands below whenever you modify the squash-only merge test case or
need to demonstrate the guardrails to stakeholders:

- Keep formatting and linting in sync:

  ```shell
  tofu fmt -recursive -check
  tflint --chdir=platform-standards/tofu
  ```

- Preview the drift that would enrol the sample repository:

  ```shell
  GITHUB_TOKEN=placeholder tofu -chdir=platform-standards/tofu \
    plan -var github_owner=test-case -detailed-exitcode
  ```

  Exit code `2` means changes are pending; exit code `0` shows convergence.
  Share the plan output when reviewers want to inspect the settings OpenTofu
  will apply.

- Capture a plan file and run an ephemeral apply in a throwaway workspace:

  ```shell
  GITHUB_TOKEN=placeholder tofu -chdir=platform-standards/tofu plan \
    -var github_owner=test-case -out=plan.tfplan
  tofu -chdir=platform-standards/tofu workspace new demo-squash || true
  GITHUB_TOKEN=placeholder tofu -chdir=platform-standards/tofu apply plan.tfplan
  ```

  Always delete the workspace or discard the generated state file afterward.

- Exercise the module’s native unit tests (plan and apply):

  ```shell
  GITHUB_TOKEN=placeholder tofu -chdir=platform-standards/tofu/modules/repository \
    test
  ```

- Drive the Terratest suite for happy and unhappy paths:

  ```shell
  GOCACHE=$PWD/platform-standards/tofu/terratest/.gocache \
    go -C platform-standards/tofu/terratest test ./...
  ```

- Validate the OPA policy expectations:

  ```shell
  conftest test --policy platform-standards/tofu/policies \
    platform-standards/tofu/policies/examples/*.json
  ```

Running the full sequence above mirrors the automation that CI performs, making
it clear that the test-case standard enforces RS-002 through static checks,
unit-style tests, Terratest coverage, and policy validation before any real
repository settings change.

## Auditor workflow

- Scheduled audits run via `.github/workflows/auditor.yml` every day at 05:00
  UTC. Results land in GitHub's Code Scanning dashboard because the workflow
  uploads the generated SARIF file using `github/codeql-action/upload-sarif`.
- Trigger the workflow manually with **Run workflow** if you want to inspect a
  specific revision. Provide `snapshot_path` (for example,
  `tests/fixtures/auditor/snapshot.json`) to replay a recorded API response and
  set `upload_sarif` to `false` when you only need a local artefact.
- Run the same workflow locally with `act`:

  ```shell
  CONCORDAT_RUN_ACT_TESTS=1 pytest tests/workflows/test_auditor_workflow.py -k auditor
  ```

  The test reads `tests/fixtures/workflows/auditor-workflow-dispatch.json`,
  downloads workflow artefacts under a temporary directory, and asserts that
  the SARIF log structure is valid.

## Troubleshooting

- The CLI refuses to operate on bare repositories. Create a working tree or
  clone the repository locally first.
- Ensure the repository has at least one existing commit. Enrolment commits
  require a parent revision.
- When pushing fails for an SSH repository, verify that the SSH agent knows the
  key and that the remote accepts the configured credentials.
