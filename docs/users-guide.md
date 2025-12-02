# concordat Users' Guide

## Overview

The `concordat` command line interface (CLI) helps maintainers enrol Git
repositories with Concordat. Enrolling creates a `.concordat` file at the
repository root. The file is a YAML 1.2 document with the key/value pair
`enrolled: true`. Downstream tooling relies on this marker to detect
participating repositories, and Concordat's continuous integration (CI)
workflows read the same flag before applying changes.

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

- Ensure an estate with the correct `github_owner` is active before enrolling:
  run `concordat estate init --github-owner <owner>` once and
  `concordat estate use <alias>` to activate it. The CLI refuses repositories
  whose GitHub slug does not start with the recorded owner and fails fast when
  it cannot determine the slug from the repository or `origin` remote.

- When the repository already contains a `.concordat` file with
  `enrolled: true`, the CLI prints `already enrolled` and makes no changes.

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

- When rehearsing or running tests without access to the platform-standards
  repository, set `CONCORDAT_SKIP_PLATFORM_PR=1` to disable the IaC pull
  request step while keeping the `github_owner` guard active for the GitHub
  owner.

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

- Invoking `concordat ls` without namespaces defaults to the active estate's
  recorded `github_owner`, which keeps ad-hoc inventory dumps aligned with the
  estate configuration.

- Provide a personal access token with `--token` or the `GITHUB_TOKEN`
  environment variable when listing private repositories:

  ```shell
  uv run concordat ls --token "$GITHUB_TOKEN" my-org
  ```

## Managing estates

Concordat tracks platform-standards repositories, referred to as *estates*, in
`$XDG_CONFIG_HOME/concordat/config.yaml` (`~/.config/concordat/config.yaml` on
most systems). Each estate entry records an alias, the managed `github_owner`,
the Git URL for the platform-standards repository, the OpenTofu inventory path,
and the default branch. The CLI uses the **active estate** to determine where
enrolment PRs should be opened.

- Bootstrap a new estate from the bundled template:

  ```shell
  uv run concordat estate init core git@github.com:example/platform-standards.git \
    --github-owner example \
    --github-token "$GITHUB_TOKEN"
  ```

  - The CLI copies the `platform-standards` directory into a temporary Git repo,
    commits the initial contents, and pushes to the provided remote.
  - `--github-owner` is required when the remote URL is not hosted on GitHub.
    When omitted, the CLI infers the owner from the repository slug and stores
    it so `concordat enrol` can enforce the namespace guard.
  - When the target repository does not exist, Concordat prompts before using
    the GitHub API (via `github3.py`) to create it. Pass `--yes` to skip the
    prompt in scripted environments.
  - Initialization aborts if the repository already contains commits.

- List the configured estates and their remotes:

  ```shell
  uv run concordat estate ls
  ```

- Show the repositories that an estate currently manages. Without an argument,
  the CLI uses the active estate:

  ```shell
  uv run concordat estate show
  uv run concordat estate show sandbox
  ```

- Switch the active estate:

  ```shell
  uv run concordat estate use sandbox
  ```

## Previewing and applying estate changes

Use the `plan` and `apply` commands to run OpenTofu against the active estate
without leaving the CLI. Both commands require `GITHUB_TOKEN` and the estate's
`github_owner` to be recorded.

- Preview changes with `concordat plan`. Additional OpenTofu arguments can be
  appended directly to the command (for example, `-detailed-exitcode`).

  ```shell
  uv run concordat plan -- -detailed-exitcode
  ```

  The CLI refreshes the cached estate under
  `$XDG_CACHE_HOME/concordat/estates/<alias>`, clones it into a temporary
  directory, writes `terraform.tfvars` with the recorded owner, runs
  `tofu init -input=false`, and then `tofu plan`. Paths are echoed, so the
  workspace can be inspected; pass `--keep-workdir` to skip the cleanup step.

- Reconcile the estate with `concordat apply`. The command requires an explicit
  `--auto-approve` to match OpenTofu's automation guard.

  ```shell
  uv run concordat apply --auto-approve
  ```

`concordat apply` uses the same workspace preparation as `plan`, adds
`-auto-approve` for OpenTofu, and returns the exit code from the underlying
`tofu` invocation, so pipelines can gate on it. Pass `--keep-workdir` when you
also want to retain the apply workspace for inspection.

### Persisting estate state in object storage

Use `concordat estate persist` to move OpenTofu state into a shared,
version-controlled backend for the active estate. The command:

- prompts for bucket, region, endpoint, key prefix, and key suffix, seeding
  defaults from any existing `backend/persistence.yaml`
- verifies the Scaleway bucket has versioning enabled and performs a zero-byte
  put/delete to confirm the supplied credentials can write to the prefix
- writes `backend/<alias>.tfbackend` (no credentials) plus
  `backend/persistence.yaml` (`schema_version: 1`) describing the backend
- pushes a branch named `estate/persist-<timestamp>` and opens a pull request
  when `GITHUB_TOKEN` resolves the estate remote to a GitHub repository

Re-running the command refuses to replace existing backend files unless
`--force` is supplied; use `--force` when rotating buckets or prefixes. Secrets
such as `AWS_SECRET_ACCESS_KEY` are validated in memory only and are never
written to disk.

Non-interactive use for automation:

- Provide backend values via flags (`--bucket`, `--region`, `--endpoint`,
  `--key-prefix`, `--key-suffix`) or the environment variables
  `CONCORDAT_PERSIST_BUCKET`, `CONCORDAT_PERSIST_REGION`,
  `CONCORDAT_PERSIST_ENDPOINT`, `CONCORDAT_PERSIST_KEY_PREFIX`, and
  `CONCORDAT_PERSIST_KEY_SUFFIX`.
- Pass `--no-input` to fail fast instead of prompting when any required value
  is missing. Defaults from an existing `backend/persistence.yaml` are still
  honoured in non-interactive mode.

### Configuring remote-state credentials

Remote-state backends rely on environment variables; the CLI simply checks that
they exist before shelling out to OpenTofu. Export the pair that matches the
selected provider:

| Provider                | Required variables                           | Optional variables                                                       | Notes                                                                      |
| ----------------------- | -------------------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| AWS S3 / Spaces         | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | `AWS_SESSION_TOKEN` (when using temporary credentials such as STS)       | Values are passed straight to OpenTofu's S3 backend.                       |
| Scaleway Object Storage | `SCW_ACCESS_KEY`, `SCW_SECRET_KEY`           | `AWS_SESSION_TOKEN` (only when scaleway issues temporary AWS-style keys) | Concordat maps these onto the AWS variable names before invoking OpenTofu. |

The stack declares an explicit `s3` backend in
`platform-standards/tofu/backend.tf` and ships a Scaleway starter config at
`platform-standards/tofu/backend/scaleway.tfbackend`. Initialise estates with
OpenTofu 1.12 or newer using:

```bash
GITHUB_TOKEN=placeholder \
  tofu -chdir=platform-standards/tofu \
  init -backend-config backend/scaleway.tfbackend
```

Example shell snippet:

```bash
export AWS_ACCESS_KEY_ID=AKIA... # or SCW_ACCESS_KEY for Scaleway
export AWS_SECRET_ACCESS_KEY=xxxx # or SCW_SECRET_KEY
# export AWS_SESSION_TOKEN=...    # optional for temporary sessions
```

When multiple estates exist, run `concordat estate persist` for each remote
stack using the appropriate credentials. The roadmap and design doc (§2.8)
describe lock troubleshooting steps and disaster-recovery procedures that build
on this environment setup.

### Estate configuration file

Concordat stores estate metadata in `$XDG_CONFIG_HOME/concordat/config.yaml`
(`~/.config/concordat/config.yaml` when the environment variable is unset). The
file is regular YAML 1.2 with an `estate` section:

```yaml
estate:
  active_estate: core
  estates:
    core:
      github_owner: example
      repo_url: git@github.com:example/platform-standards.git
      branch: main
      inventory_path: tofu/inventory/repositories.yaml
    sandbox:
      github_owner: example
      repo_url: git@github.com:example/sandbox-standards.git
      branch: main
      inventory_path: tofu/inventory/repositories.yaml
```

- `active_estate` is optional; the first `estate init` call populates it
  automatically.
- `github_owner` identifies the GitHub organization or user managed by the
  estate. `concordat enrol` and `concordat ls` rely on the stored owner to
  guard against cross-organization drift.
- `branch` and `inventory_path` default to `main` and
  `tofu/inventory/repositories.yaml`. Override them when the remote uses
  another branch name or inventory layout.
- Manual edits are allowed, but prefer the CLI to ensure validation is applied.

### Interaction with enrolment

The `concordat enrol` command automatically targets the active estate and
refuses to run unless that estate records `github_owner`. The
`--platform-standards-url` flag still overrides the repository where the
OpenTofu pull request is opened, but the namespace guard always uses the active
estate owner.

- Run `concordat estate use <alias>` before invoking `concordat enrol` when
  switching estates (for example, when working on a fork).
- Ensure repositories expose an `origin` remote pointing at GitHub (or pass the
  SSH URL directly) so the CLI can resolve the slug and enforce the owner guard.
- If the estate inventory misses a repository, run `concordat estate show` to
  confirm the inventory contents before debugging the enrolment.

## Running the squash-only merge plan

The `platform-standards/tofu` directory contains a runnable OpenTofu stack that
enforces the squash-only merge strategy (RS-002). The stack consumes
`platform-standards/tofu/inventory/repositories.yaml`, which now includes the
non-production `test-case/squash-only-standard` record, so operators can
rehearse changes without touching production.

1. Set a placeholder GitHub token so the provider schema loads without reaching
   the API:

   ```shell
   export GITHUB_TOKEN=placeholder
   ```

2. Initialize the stack and preview the actions with the default `test-case`
   owner:

   ```shell
   cd platform-standards/tofu
   tofu init
   tofu plan -var github_owner=test-case -detailed-exitcode
   ```

   Exit code `2` indicates that OpenTofu would make changes (expected for the
   sample repository), while exit code `0` confirms convergence.

3. Override `github_owner` and extend `inventory/repositories.yaml` when ready
   to target additional organizations. The `github_owner` guard blocks
   accidental cross-org drift by asserting that every slug shares the
   configured GitHub owner.

### Validating the test-case standard end to end

Use the commands below when modifying the squash-only merge test case or
demonstrating the guardrails to stakeholders:

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

- Validate the Open Policy Agent (OPA) policy expectations:

  ```shell
  conftest test --policy platform-standards/tofu/policies \
    platform-standards/tofu/policies/examples/*.json
  ```

Running the full sequence above mirrors the automation that CI performs,
demonstrating that the test-case standard enforces RS-002 through static
checks, unit-style tests, Terratest coverage, and policy validation before any
real repository settings change.

## Auditor workflow

- Scheduled audits run via `.github/workflows/auditor.yml` every day at 05:00
  UTC. Results land in GitHub's Code Scanning dashboard because the workflow
  uploads the generated Static Analysis Results Interchange Format (SARIF) file
  using the GitHub `github/codeql-action/upload-sarif` action.

- Trigger the workflow manually with **Run workflow** to inspect a specific
  revision. Provide `snapshot_path` (for example,
  `tests/fixtures/auditor/snapshot.json`) to replay a recorded API response and
  set `upload_sarif` to `false` when only a local artefact is required.

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
