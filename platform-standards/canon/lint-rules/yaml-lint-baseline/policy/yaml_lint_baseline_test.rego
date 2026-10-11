# Fixture tests for yaml-lint-baseline (YL-001 to YL-005).
package canon.lint_rules.yaml_lint_baseline_test

import rego.v1

import data.canon.lint_rules.yaml_lint_baseline as policy

profile(findings) := {
  [finding.rule_id, finding.verdict, finding.path, finding.line, finding.msg] | some finding in findings
}

# A decoding failure carries the parser's own message, which is not the policy's to pin.
kinds(findings) := {
  [finding.rule_id, finding.verdict, finding.path] | some finding in findings
}

# Labels must be a list.
test_actionlint_labels_not_list if {
  findings := policy.deny with input as data.fixtures.actionlint_labels_not_list
  profile(findings) == {
    ["YL-004", "noncompliant", ".github/actionlint.yaml", 0, ".github/actionlint.yaml must carry `self-hosted-runner.labels` as a list"],
  }
}

# An actionlint configuration that cannot be decoded is indeterminate.
test_actionlint_malformed if {
  findings := policy.deny with input as data.fixtures.actionlint_malformed
  kinds(findings) == {
    ["YL-004", "indeterminate", ".github/actionlint.yaml"],
  }
}

# The actionlint configuration must exist.
test_actionlint_missing if {
  findings := policy.deny with input as data.fixtures.actionlint_missing
  profile(findings) == {
    ["YL-004", "noncompliant", ".github/actionlint.yaml", 0, ".github/actionlint.yaml is missing; it must declare the estate's self-hosted runner labels"],
  }
}

# A configuration without the runner section declares no labels.
test_actionlint_no_section if {
  findings := policy.deny with input as data.fixtures.actionlint_no_section
  profile(findings) == {
    ["YL-004", "noncompliant", ".github/actionlint.yaml", 0, ".github/actionlint.yaml must list the runner label \"ubicloud-*\", so actionlint accepts the estate's self-hosted runners"],
  }
}

# The estate's runner label must be declared.
test_actionlint_other_labels if {
  findings := policy.deny with input as data.fixtures.actionlint_other_labels
  profile(findings) == {
    ["YL-004", "noncompliant", ".github/actionlint.yaml", 0, ".github/actionlint.yaml must list the runner label \"ubicloud-*\", so actionlint accepts the estate's self-hosted runners"],
  }
}

# The runner section must be a mapping.
test_actionlint_section_not_mapping if {
  findings := policy.deny with input as data.fixtures.actionlint_section_not_mapping
  profile(findings) == {
    ["YL-004", "noncompliant", ".github/actionlint.yaml", 0, ".github/actionlint.yaml must carry `self-hosted-runner.labels` as a list"],
  }
}

# actionlint also reads `actionlint.yml`.
test_actionlint_yml_spelling if {
  findings := policy.deny with input as data.fixtures.actionlint_yml_spelling
  count(findings) == 0
}

# yamllint reads `.yamllint` before `.yamllint.yml`, so it overrides the governed file.
test_alternate_dotless if {
  findings := policy.deny with input as data.fixtures.alternate_dotless
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint", 0, ".yamllint takes precedence over or duplicates .yamllint.yml; remove it and keep one local configuration"],
  }
}

# An alternate name does not stand in for the governed local file.
test_alternate_only if {
  findings := policy.deny with input as data.fixtures.alternate_only
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint", 0, ".yamllint takes precedence over or duplicates .yamllint.yml; remove it and keep one local configuration"],
    ["YL-003", "noncompliant", ".yamllint.yml", 0, ".yamllint.yml is missing; it must extend .yamllint-estate.yml"],
  }
}

# yamllint reads `.yamllint.yaml` before `.yamllint.yml`, so it overrides the governed file.
test_alternate_yaml if {
  findings := policy.deny with input as data.fixtures.alternate_yaml
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint.yaml", 0, ".yamllint.yaml takes precedence over or duplicates .yamllint.yml; remove it and keep one local configuration"],
  }
}

# A copy that differs from the canonical file is a fork.
test_base_drifted if {
  findings := policy.deny with input as data.fixtures.base_drifted
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint-estate.yml", 0, ".yamllint-estate.yml differs from the canonical estate yamllint base; copy it verbatim"],
  }
}

# The estate base file must be present.
test_base_missing if {
  findings := policy.deny with input as data.fixtures.base_missing
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint-estate.yml", 0, ".yamllint-estate.yml is missing; copy the canonical estate yamllint base verbatim"],
  }
}

# Installing shellcheck by hand forks the pinned version.
test_ci_hand_rolled_apt if {
  findings := policy.deny with input as data.fixtures.ci_hand_rolled_apt
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint\" provisions a lint tool by hand (step 2); use leynos/shared-actions/.github/actions/install-yaml-lint"],
  }
}

# Downloading actionlint by hand forks the pinned version.
test_ci_hand_rolled_download if {
  findings := policy.deny with input as data.fixtures.ci_hand_rolled_download
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint\" provisions a lint tool by hand (step 2); use leynos/shared-actions/.github/actions/install-yaml-lint"],
  }
}

# Installing yamllint by hand forks the pinned version.
test_ci_hand_rolled_uv if {
  findings := policy.deny with input as data.fixtures.ci_hand_rolled_uv
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint\" provisions a lint tool by hand (step 2); use leynos/shared-actions/.github/actions/install-yaml-lint"],
  }
}

# An installer after the lint step changes nothing for it.
test_ci_installer_after if {
  findings := policy.deny with input as data.fixtures.ci_installer_after
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint\" runs the lint gate (step 1) without an earlier step using leynos/shared-actions/.github/actions/install-yaml-lint"],
  }
}

# A step that never runs installs nothing.
test_ci_installer_disabled if {
  findings := policy.deny with input as data.fixtures.ci_installer_disabled
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint\" runs the lint gate (step 2) without an earlier step using leynos/shared-actions/.github/actions/install-yaml-lint"],
  }
}

# An installer in another job does not reach this job's PATH.
test_ci_installer_other_job if {
  findings := policy.deny with input as data.fixtures.ci_installer_other_job
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint\" runs the lint gate (step 1) without an earlier step using leynos/shared-actions/.github/actions/install-yaml-lint"],
  }
}

# A lint job that never runs is not CI enforcement.
test_ci_lint_disabled if {
  findings := policy.deny with input as data.fixtures.ci_lint_disabled
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows", 0, "no CI job runs `make lint`; the YAML lint gate is not enforced in CI"],
  }
}

# A workflow that cannot be decoded is indeterminate beside a clean one.
test_ci_malformed if {
  findings := policy.deny with input as data.fixtures.ci_malformed
  kinds(findings) == {
    ["YL-005", "indeterminate", ".github/workflows/broken.yml"],
  }
}

# A workflow that cannot be decoded leaves the lint lane unproven.
test_ci_malformed_only if {
  findings := policy.deny with input as data.fixtures.ci_malformed_only
  kinds(findings) == {
    ["YL-005", "indeterminate", ".github/workflows/broken.yml"],
  }
}

# A lint job with no installer step has no pinned tools.
test_ci_no_installer if {
  findings := policy.deny with input as data.fixtures.ci_no_installer
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint\" runs the lint gate (step 2) without an earlier step using leynos/shared-actions/.github/actions/install-yaml-lint"],
  }
}

# No job runs the lint gate.
test_ci_no_lint if {
  findings := policy.deny with input as data.fixtures.ci_no_lint
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows", 0, "no CI job runs `make lint`; the YAML lint gate is not enforced in CI"],
  }
}

# Other lint-named targets are not the gate.
test_ci_other_lint_targets if {
  findings := policy.deny with input as data.fixtures.ci_other_lint_targets
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows", 0, "no CI job runs `make lint`; the YAML lint gate is not enforced in CI"],
  }
}

# A reusable workflow may run the gate, so absence is not proven.
test_ci_reusable_only if {
  findings := policy.deny with input as data.fixtures.ci_reusable_only
  profile(findings) == {
    ["YL-005", "indeterminate", ".github/workflows", 0, "no workflow provably runs `make lint`; a job calls a reusable workflow that was not inspected"],
  }
}

# A third-party lint action bypasses the Make gate.
test_ci_third_party_action if {
  findings := policy.deny with input as data.fixtures.ci_third_party_action
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint\" lints through raven-actions/actionlint (step 2); run the Make gate with leynos/shared-actions/.github/actions/install-yaml-lint instead"],
  }
}

# Each lint job needs its own installer.
test_ci_two_jobs if {
  findings := policy.deny with input as data.fixtures.ci_two_jobs
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint-again\" runs the lint gate (step 1) without an earlier step using leynos/shared-actions/.github/actions/install-yaml-lint"],
  }
}

# The installer must be pinned to a full commit SHA.
test_ci_unpinned_installer if {
  findings := policy.deny with input as data.fixtures.ci_unpinned_installer
  profile(findings) == {
    ["YL-005", "noncompliant", ".github/workflows/ci.yml", 0, "job \"lint\" pins leynos/shared-actions/.github/actions/install-yaml-lint to \"v1\"; pin it to a full commit SHA"],
  }
}

# The reference wiring raises no finding.
test_compliant if {
  findings := policy.deny with input as data.fixtures.compliant
  count(findings) == 0
}

# A conditional rule on the lint path cannot be proven.
test_conditional if {
  findings := policy.deny with input as data.fixtures.conditional
  profile(findings) == {
    ["YL-001", "indeterminate", "Makefile", 0, "the \"lint\" target reaches a conditional Make rule; its recipes cannot be proven"],
    ["YL-002", "indeterminate", "Makefile", 0, "the \"lint\" target reaches a conditional Make rule; its recipes cannot be proven"],
  }
}

# `-d` replaces the estate configuration inline.
test_config_data if {
  findings := policy.deny with input as data.fixtures.config_data
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 8, "\"lint\"-path recipe runs yamllint with -d, which overrides the estate configuration or hides warnings"],
  }
}

# A literal `$(MAKE) target` chain is a proven edge of the lint closure.
test_delegated if {
  findings := policy.deny with input as data.fixtures.delegated
  count(findings) == 0
}

# Printing a command is not running it.
test_echo_decoy if {
  findings := policy.deny with input as data.fixtures.echo_decoy
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 0, "no recipe reachable from \"lint\" runs yamllint"],
    ["YL-002", "noncompliant", "Makefile", 0, "no recipe reachable from \"lint\" runs actionlint"],
  }
}

# Further runner labels beside the estate glob are the repository's own business.
test_extra_labels if {
  findings := policy.deny with input as data.fixtures.extra_labels
  count(findings) == 0
}

# A recipe that hides the tool behind an undefined variable is unproven.
test_hidden_tool if {
  findings := policy.deny with input as data.fixtures.hidden_tool
  profile(findings) == {
    ["YL-001", "indeterminate", "Makefile", 0, "no recipe reachable from \"lint\" provably runs yamllint; unresolved Make variables: LINT_YAML"],
    ["YL-002", "indeterminate", "Makefile", 0, "no recipe reachable from \"lint\" provably runs actionlint; unresolved Make variables: LINT_YAML"],
  }
}

# The tools named literally in the lint recipe are as good as variables.
test_literal_tools if {
  findings := policy.deny with input as data.fixtures.literal_tools
  count(findings) == 0
}

# Extending yamllint's `default` skips the estate base.
test_local_extends_default if {
  findings := policy.deny with input as data.fixtures.local_extends_default
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint.yml", 0, ".yamllint.yml must set `extends: .yamllint-estate.yml`"],
  }
}

# Only the allowed keys may appear in the local file.
test_local_extra_keys if {
  findings := policy.deny with input as data.fixtures.local_extra_keys
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint.yml", 0, ".yamllint.yml sets \"ignore-from-file\"; the baseline allows only extends, ignore"],
    ["YL-003", "noncompliant", ".yamllint.yml", 0, ".yamllint.yml sets \"yaml-files\"; the baseline allows only extends, ignore"],
  }
}

# A local `ignore` block is the one extension the baseline allows.
test_local_ignore if {
  findings := policy.deny with input as data.fixtures.local_ignore
  count(findings) == 0
}

# `ignore` may also be a list of patterns.
test_local_ignore_list if {
  findings := policy.deny with input as data.fixtures.local_ignore_list
  count(findings) == 0
}

# `ignore` must be patterns, not a scalar.
test_local_ignore_number if {
  findings := policy.deny with input as data.fixtures.local_ignore_number
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint.yml", 0, ".yamllint.yml must give `ignore` as a block of patterns or a list"],
  }
}

# A local file that cannot be decoded is indeterminate, not absent.
test_local_malformed if {
  findings := policy.deny with input as data.fixtures.local_malformed
  kinds(findings) == {
    ["YL-003", "indeterminate", ".yamllint.yml"],
  }
}

# The repository's own configuration must exist and extend the base.
test_local_missing if {
  findings := policy.deny with input as data.fixtures.local_missing
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint.yml", 0, ".yamllint.yml is missing; it must extend .yamllint-estate.yml"],
  }
}

# A local file without `extends` skips the estate base.
test_local_no_extends if {
  findings := policy.deny with input as data.fixtures.local_no_extends
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint.yml", 0, ".yamllint.yml must set `extends: .yamllint-estate.yml`"],
  }
}

# A local file that is not a mapping cannot extend anything.
test_local_non_mapping if {
  findings := policy.deny with input as data.fixtures.local_non_mapping
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint.yml", 0, ".yamllint.yml must be a YAML mapping that extends .yamllint-estate.yml"],
  }
}

# A local `rules` block forks the estate policy.
test_local_sets_rules if {
  findings := policy.deny with input as data.fixtures.local_sets_rules
  profile(findings) == {
    ["YL-003", "noncompliant", ".yamllint.yml", 0, ".yamllint.yml sets \"rules\"; the baseline allows only extends, ignore"],
  }
}

# Make options and further targets around `lint` still run the lint gate.
test_make_flags if {
  findings := policy.deny with input as data.fixtures.make_flags
  count(findings) == 0
}

# The lint closure never runs actionlint.
test_no_actionlint if {
  findings := policy.deny with input as data.fixtures.no_actionlint
  profile(findings) == {
    ["YL-002", "noncompliant", "Makefile", 0, "no recipe reachable from \"lint\" runs actionlint"],
  }
}

# The tools exist but no `lint` target gathers them.
test_no_lint_target if {
  findings := policy.deny with input as data.fixtures.no_lint_target
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 0, "required Make target \"lint\" is absent"],
    ["YL-002", "noncompliant", "Makefile", 0, "required Make target \"lint\" is absent"],
  }
}

# Linting `.` reaches vendored and ignored trees.
test_no_ls_files if {
  findings := policy.deny with input as data.fixtures.no_ls_files
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 8, "\"lint\"-path recipe runs yamllint without a `git ls-files` list covering *.yml and *.yaml; select tracked YAML only"],
  }
}

# Without a Makefile neither tool can run in the gate.
test_no_makefile if {
  findings := policy.deny with input as data.fixtures.no_makefile
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 0, "root Makefile is missing"],
    ["YL-002", "noncompliant", "Makefile", 0, "root Makefile is missing"],
  }
}

# Without `--strict`, warnings do not fail the gate.
test_no_strict if {
  findings := policy.deny with input as data.fixtures.no_strict
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 8, "\"lint\"-path recipe runs yamllint without --strict; warnings would not fail the target"],
  }
}

# `--no-warnings` hides what `--strict` exists to fail on.
test_no_warnings if {
  findings := policy.deny with input as data.fixtures.no_warnings
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 8, "\"lint\"-path recipe runs yamllint with --no-warnings, which overrides the estate configuration or hides warnings"],
  }
}

# The lint closure never runs yamllint.
test_no_yamllint if {
  findings := policy.deny with input as data.fixtures.no_yamllint
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 0, "no recipe reachable from \"lint\" runs yamllint"],
  }
}

# A checkout with no YAML is out of scope.
test_not_applicable if {
  findings := policy.deny with input as data.fixtures.not_applicable
  count(findings) == 0
}

# Targets that nothing in the lint closure reaches are not the gate.
test_not_in_lint if {
  findings := policy.deny with input as data.fixtures.not_in_lint
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 0, "no recipe reachable from \"lint\" runs yamllint"],
    ["YL-002", "noncompliant", "Makefile", 0, "no recipe reachable from \"lint\" runs actionlint"],
  }
}

# A list that misses `*.yaml` leaves those files unlinted.
test_one_pathspec if {
  findings := policy.deny with input as data.fixtures.one_pathspec
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 8, "\"lint\"-path recipe runs yamllint without a `git ls-files` list covering *.yml and *.yaml; select tracked YAML only"],
  }
}

# Other targets whose names end in lint are not mistaken for the lint gate.
test_other_lint_targets_beside_lint if {
  findings := policy.deny with input as data.fixtures.other_lint_targets_beside_lint
  count(findings) == 0
}

# An empty `-shellcheck=` switches off the script analysis.
test_shellcheck_off if {
  findings := policy.deny with input as data.fixtures.shellcheck_off
  profile(findings) == {
    ["YL-002", "noncompliant", "Makefile", 11, "\"lint\"-path recipe runs actionlint with -shellcheck=, which switches off the shellcheck integration"],
  }
}

# A `-` prefix or `|| true` leaves the tool's status unable to fail the target.
test_soft_skip if {
  findings := policy.deny with input as data.fixtures.soft_skip
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 8, "\"lint\"-path recipe soft-skips yamllint; its exit status cannot fail the target"],
    ["YL-002", "noncompliant", "Makefile", 11, "\"lint\"-path recipe soft-skips actionlint; its exit status cannot fail the target"],
  }
}

# A flag behind an undefined Make variable is unproven.
test_unresolved if {
  findings := policy.deny with input as data.fixtures.unresolved
  profile(findings) == {
    ["YL-001", "indeterminate", "Makefile", 8, "\"lint\"-path recipe runs yamllint with unresolved Make variables: YAMLLINT_FLAGS; its flags cannot be proven"],
  }
}

# A `--version` probe is not provisioning.
test_version_probe if {
  findings := policy.deny with input as data.fixtures.version_probe
  count(findings) == 0
}

# An included Makefile cannot be read, so the closure is unproven.
test_with_include if {
  findings := policy.deny with input as data.fixtures.with_include
  profile(findings) == {
    ["YL-001", "indeterminate", "Makefile", 0, "Makefile includes other files; the recipes cannot be proven"],
    ["YL-002", "indeterminate", "Makefile", 0, "Makefile includes other files; the recipes cannot be proven"],
  }
}

# xargs options with a numeric argument are read through, and `-s` is `--strict`.
test_xargs_options if {
  findings := policy.deny with input as data.fixtures.xargs_options
  count(findings) == 0
}

# YAML without workflows owes the yamllint checks only.
test_yaml_only if {
  findings := policy.deny with input as data.fixtures.yaml_only
  count(findings) == 0
}

# YAML without workflows and without a Makefile still owes the gate.
test_yaml_only_no_makefile if {
  findings := policy.deny with input as data.fixtures.yaml_only_no_makefile
  profile(findings) == {
    ["YL-001", "noncompliant", "Makefile", 0, "root Makefile is missing"],
    ["YL-002", "noncompliant", "Makefile", 0, "root Makefile is missing"],
  }
}
