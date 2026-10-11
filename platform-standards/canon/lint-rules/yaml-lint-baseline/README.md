# yaml-lint-baseline

Audits a checkout's YAML and GitHub Actions workflow linting. The sensor is a
Conftest/Rego policy evaluated over a `policy-input/yaml-lint-baseline`
envelope built by `concordat artefact rule run`. Makefile facts come from the
pinned `makeutil parse` command, the yamllint and actionlint configurations and
each GitHub Actions workflow from a YAML decode, and the estate yamllint base
file as its SHA-256. The policy never reparses any of those formats itself.

The reference wiring is a root Makefile whose `lint` target reaches both tools,

```make
YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint

lint: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)
```

with the estate base file copied to `.yamllint-estate.yml`, a thin
`.yamllint.yml` that extends it, `.github/actionlint.yaml` declaring the estate
runner labels, and a CI job that installs the pinned tools before it runs
`make lint`:

```yaml
- uses: leynos/shared-actions/.github/actions/install-yaml-lint@<SHA>
- run: make lint
```

The canonical files are under `platform-standards/canon/lint/yaml/`. yamllint
resolves `extends:` against the current directory, so it must run from the
repository root, as the Makefile's recipes do.

## Checks

- **YL-001** (error): a recipe reachable from `lint` runs `yamllint --strict`
  (or `-s`) over a `git ls-files` list that names every pathspec in
  `yaml_pathspecs` (`*.yml` and `*.yaml` by default), and its exit status
  reaches Make. `xargs` with short options is read through, because yamllint
  has no Git mode. `-d`/`--config-data` and `--no-warnings` are refused,
  because they replace the estate configuration or hide what `--strict` fails
  on. A flag behind a Make variable the policy cannot resolve is indeterminate.
- **YL-002** (error): a recipe reachable from `lint` runs actionlint with a
  binding exit status and without an empty `-shellcheck` value (`-shellcheck=`,
  `-shellcheck ""`), which would switch off the script analysis that makes
  actionlint worth running; a recipe that only prints `-version` is refused.
- **YL-003** (error): `.yamllint-estate.yml` has a digest in `base_sha256s`,
  `.yamllint.yml` extends it and sets only the keys in `local_allowed_keys`
  (`extends` and `ignore` by default), and neither `.yamllint` nor
  `.yamllint.yaml` exists, because yamllint reads those before `.yamllint.yml`
  and they would silently replace it. The user-global
  `$XDG_CONFIG_HOME/yamllint/config` is not a repository file and is not
  audited. A local `ignore` replaces the base's rather than adding to it.
- **YL-004** (error): `.github/actionlint.yaml` (or `.yml`) exists and lists
  every label in `runner_labels` (`ubicloud-*` by default) under
  `self-hosted-runner.labels`. Applies when the checkout has workflows.
- **YL-005** (error): every enabled CI step that runs `make lint` (in command
  position, not quoted text or a comment, not a dry run, and without
  `continue-on-error` or a trailing `|| true`) has an earlier enabled step in
  the same job using `installer_action`, pinned to a full commit SHA; some job
  runs `make lint` at all; no enabled `run:` step matches a
  `hand_rolled_patterns` entry (a `uv tool install yamllint`, an actionlint
  download, an `apt install shellcheck`); and no step uses a `lint_actions`
  entry. A job that calls a reusable workflow leaves the absence of a lint job
  indeterminate, because the policy cannot see inside it.

A checkout with no YAML file is out of scope. Every check fails closed: a
Makefile with includes, conditional rules on the lint path, dynamic recursive
Make or a recovered parse, and a configuration or workflow that cannot be
decoded, each produce an `indeterminate` finding rather than a pass.

The checks that read the Makefile prove the shape of the lint path, not that
the tools run to completion on the repository's YAML; that is what the lint
lane itself proves.

## Parameters

The defaults in `rule.yaml` are the estate standard. `base_sha256s` lists the
accepted digests of `.yamllint-estate.yml`, so a revision of the base can be
introduced while consumers catch up. `fixtures/generate.py` records one
envelope per scenario and bundles them with these defaults into
`fixtures/data.json`, which `conftest verify` runs `policy/` against.

## Regenerating the fixtures

```shell
uv run python \
  platform-standards/canon/lint-rules/yaml-lint-baseline/fixtures/generate.py
```

The pinned `makeutil` must be on `PATH`.
