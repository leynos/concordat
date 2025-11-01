# concordat Users' Guide

## Overview

The `concordat` command line interface (CLI) helps you enrol Git repositories
with Concordat. Enrolling creates a `.concordat` file at the repository root.
The file is a YAML 1.2 document with the key/value pair `enrolled: true`.
Downstream tooling relies on this marker to detect participating repositories.

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

- The CLI commits the new file to the current branch. If your Git configuration
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

## Troubleshooting

- The CLI refuses to operate on bare repositories. Create a working tree or
  clone the repository locally first.
- Ensure the repository has at least one existing commit. Enrolment commits
  require a parent revision.
- When pushing fails for an SSH repository, verify that the SSH agent knows the
  key and that the remote accepts your credentials.
