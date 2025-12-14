# concordat

**Keep your GitHub repositories in line, without the hassle.**

Concordat is a command-line interface (CLI) that helps you enforce consistent
standards across your GitHub repositories. You define the rules once in a
central *estate*, and concordat ensures every enrolled repository follows them
using OpenTofu under the hood.

No more manually clicking through repository settings. No more drift between
projects. Just enrol your repos, run `plan`, and let concordat handle the rest.

## Features

- **Estate management** – Organize platform-standards repositories into named
  estates, each tracking a GitHub owner and an OpenTofu inventory.
- **Repository enrolment** – Add repositories to an estate with a single
  command; concordat creates the `.concordat` marker and opens the necessary
  pull requests.
- **OpenTofu workflows** – Preview changes with `concordat plan` and reconcile
  them with `concordat apply`, all without leaving the CLI.
- **Remote state persistence** – Store OpenTofu state in S3-compatible backends
  (AWS, Scaleway, DigitalOcean Spaces) for team collaboration.
- **Auditor workflow** – A scheduled GitHub Action audits enrolled repositories
  and surfaces compliance issues in the Code Scanning dashboard.

## Installation

Concordat uses [uv](https://docs.astral.sh/uv/) for dependency management.
Clone the repository and sync the development environment:

```shell
git clone git@github.com:your-org/concordat.git
cd concordat
uv sync --group dev
```

Verify the installation:

```shell
uv run concordat --help
```

## Quick start

### 1. Initialize an estate

An estate is a platform-standards repository that holds your OpenTofu
configuration and inventory. Bootstrap one with:

```shell
uv run concordat estate init core git@github.com:example/platform-standards.git \
  --github-token "$GITHUB_TOKEN"
# concordat infers github_owner from the repo and prompts you to confirm it.
```

### 2. Enrol a repository

Point concordat at a local checkout or an SSH URL:

```shell
uv run concordat enrol path/to/my-repo
# or
uv run concordat enrol git@github.com:example/my-repo.git
```

Concordat commits the `.concordat` marker and opens a pull request to add the
repository to your estate's inventory.

### 3. Preview and apply standards

See what concordat would change:

```shell
uv run concordat plan
```

When you are happy, apply the configuration:

```shell
uv run concordat apply --auto-approve
```

That's it—your repository now conforms to your platform standards.

## What gets enforced

Concordat's OpenTofu modules enforce a curated set of repository standards.
Here's what you get out of the box:

### Repository settings

- **Squash-only merging** – Merge commits and rebase merges are disabled,
  keeping your commit history clean and linear.
- **Delete branch on merge** – Feature branches are tidied up automatically.
- **Vulnerability alerts** – Dependabot alerts are enabled by default.

### Branch protection

- **Required reviews** – Pull requests need at least two approvals before
  merging.
- **Signed commits** – Commits on protected branches must be cryptographically
  signed.
- **Linear history** – Enforces a straight commit history without merge commits.
- **Conversation resolution** – All review threads must be resolved before
  merging.
- **Status checks** – Configurable continuous integration (CI) checks must pass
  before merging.

### Team management

- **Privacy controls** – Teams are set to closed or secret visibility.
- **Repository permissions** – Fine-grained permission mapping (pull, triage,
  push, maintain, admin) for each team.

## Project structure

```plaintext
concordat/              CLI package (Python, built with cyclopts)
├── cli.py              Main entry point
├── estate.py           Estate configuration and management
├── enrol.py            Repository enrolment logic
├── persistence/        Remote state persistence
└── auditor/            Compliance auditor (GitHub Action)

platform-standards/     OpenTofu estate template
└── tofu/
    ├── main.tofu       Orchestration and inventory loading
    ├── modules/        Reusable modules
    │   ├── repository/ Repository settings (RS-002)
    │   ├── branch/     Branch protection rules
    │   └── team/       Team and permission management
    ├── policies/       OPA/Rego policy definitions
    └── inventory/      YAML inventory of managed repositories

docs/                   Detailed documentation
```

## Documentation

For the full story, head to the docs:

- [Users' guide](docs/users-guide.md) – Comprehensive CLI reference and
  workflows
- [Design document](docs/concordat-design.md) – Architecture and design
  decisions
- [OpenTofu coding standards](docs/opentofu-coding-standards.md) – House rules
  for writing OpenTofu code

## Development

Concordat follows the conventions in [AGENTS.md](AGENTS.md). The most useful
Makefile targets:

| Target           | Description                              |
| ---------------- | ---------------------------------------- |
| `make fmt`       | Format Python and Markdown sources       |
| `make lint`      | Run ruff linter                          |
| `make typecheck` | Run pyright type checker                 |
| `make test`      | Run the test suite                       |
| `make check-fmt` | Verify formatting without changing files |

Run all quality gates before committing:

```shell
make fmt && make lint && make typecheck && make test
```

## Licence

Concordat is released under the [MIT Licence](LICENSE).
