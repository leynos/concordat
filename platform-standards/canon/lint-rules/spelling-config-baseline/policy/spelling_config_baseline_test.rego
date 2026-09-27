# Fixture tests for spelling-config-baseline (PD-007 to PD-012).
package canon.lint_rules.spelling_config_baseline_test

import rego.v1

import data.canon.lint_rules.spelling_config_baseline as policy

profile(findings) := {
  [finding.rule_id, finding.verdict, finding.line, finding.msg] | some finding in findings
}

# A release above the floor is accepted.
test_above_floor if {
  findings := policy.deny with input as data.fixtures.above_floor
  count(findings) == 0
}

# A pin newer than every published text compares with the newest.
test_agents_above_newest_text if {
  findings := policy.deny with input as data.fixtures.agents_above_newest_text
  count(findings) == 0
}

# A reworded block drifts from the published text.
test_agents_drifted if {
  findings := policy.deny with input as data.fixtures.agents_drifted
  profile(findings) == {
    ["PD-013", "noncompliant", 0, "AGENTS.md's spelling block differs from typos-config-builder v0.1.3's docs/agents-md-spelling.md; copy it verbatim"],
  }
}

# A second `make spelling` instruction outside the block duplicates it.
test_agents_duplicate_guidance if {
  findings := policy.deny with input as data.fixtures.agents_duplicate_guidance
  profile(findings) == {
    ["PD-013", "noncompliant", 6, "AGENTS.md gives spelling guidance outside the canonical block; the block replaces it"],
  }
}

# Markers in the wrong order bound no block.
test_agents_end_before_start if {
  findings := policy.deny with input as data.fixtures.agents_end_before_start
  profile(findings) == {
    ["PD-013", "noncompliant", 0, "AGENTS.md does not carry exactly one spelling block between <!-- typos-config-builder:agents-md:start --> and <!-- typos-config-builder:agents-md:end -->"],
  }
}

test_agents_missing if {
  findings := policy.deny with input as data.fixtures.agents_missing
  profile(findings) == {
    ["PD-013", "noncompliant", 0, "AGENTS.md is missing; it must carry typos-config-builder's spelling block between its markers"],
  }
}

# The text alone, without markers, is not the block.
test_agents_no_markers if {
  findings := policy.deny with input as data.fixtures.agents_no_markers
  profile(findings) == {
    ["PD-013", "noncompliant", 0, "AGENTS.md does not carry exactly one spelling block between <!-- typos-config-builder:agents-md:start --> and <!-- typos-config-builder:agents-md:end -->"],
  }
}

# `typos.local.toml` guidance outside the block is a repository's own, not a duplicate.
test_agents_overlay_mention if {
  findings := policy.deny with input as data.fixtures.agents_overlay_mention
  count(findings) == 0
}

# Whitespace is normalized, so a rewrapped block still matches.
test_agents_reflowed if {
  findings := policy.deny with input as data.fixtures.agents_reflowed
  count(findings) == 0
}

# Two blocks leave the canonical one ambiguous.
test_agents_two_blocks if {
  findings := policy.deny with input as data.fixtures.agents_two_blocks
  profile(findings) == {
    ["PD-013", "noncompliant", 0, "AGENTS.md does not carry exactly one spelling block between <!-- typos-config-builder:agents-md:start --> and <!-- typos-config-builder:agents-md:end -->"],
  }
}

test_below_floor if {
  findings := policy.deny with input as data.fixtures.below_floor
  profile(findings) == {
    ["PD-007", "noncompliant", 3, "\"spelling\"-path recipe pins typos-config-builder v0.1.1, below the floor v0.1.2"],
  }
}

# A branch moves, so it is not an immutable release.
test_branch_pin if {
  findings := policy.deny with input as data.fixtures.branch_pin
  profile(findings) == {
    ["PD-007", "noncompliant", 3, "\"spelling\"-path recipe pins typos-config-builder to \"main\", which is not a release tag; pin one at or above v0.1.2"],
  }
}

test_ci_direct_typos if {
  findings := policy.deny with input as data.fixtures.ci_direct_typos
  profile(findings) == {
    ["PD-010", "noncompliant", 0, "job \"spelling\" runs typos directly (Run typos); run make spelling, whose gate owns Typos and never drift-checks"],
  }
}

test_ci_drift_builder if {
  findings := policy.deny with input as data.fixtures.ci_drift_builder
  profile(findings) == {
    ["PD-010", "noncompliant", 0, "job \"spelling\" drift-checks typos.toml with typos-config-builder --check (Check typos.toml drift); run make spelling, whose gate owns Typos and never drift-checks"],
  }
}

test_ci_drift_git_diff if {
  findings := policy.deny with input as data.fixtures.ci_drift_git_diff
  profile(findings) == {
    ["PD-010", "noncompliant", 0, "job \"spelling\" drift-checks typos.toml with git diff (Check typos.toml is current); run make spelling, whose gate owns Typos and never drift-checks"],
  }
}

test_ci_legacy_script if {
  findings := policy.deny with input as data.fixtures.ci_legacy_script
  profile(findings) == {
    ["PD-010", "noncompliant", 0, "job \"spelling\" runs vendored spelling machinery (step 1); run make spelling, whose gate owns Typos and never drift-checks"],
  }
}

test_ci_legacy_target if {
  findings := policy.deny with input as data.fixtures.ci_legacy_target
  profile(findings) == {
    ["PD-010", "noncompliant", 0, "job \"spelling\" runs a legacy spelling helper target (step 1); run make spelling, whose gate owns Typos and never drift-checks"],
  }
}

# A workflow that cannot be decoded is indeterminate, never passed.
test_ci_malformed if {
  findings := policy.deny with input as data.fixtures.ci_malformed
  profile(findings) == {
    ["PD-010", "indeterminate", 0, ".github/workflows/broken.yml could not be decoded: invalid YAML: while parsing a flow node\nexpected the node content, but found '<stream end>'\n  in \"<unicode string>\", line 2, column 1:\n    \n    ^ (line: 2)"],
  }
}

test_ci_typos_action if {
  findings := policy.deny with input as data.fixtures.ci_typos_action
  profile(findings) == {
    ["PD-010", "noncompliant", 0, "job \"spelling\" runs the crate-ci/typos action (Typos); run make spelling instead"],
  }
}

# The canonical gate: uvx, a release at the floor, a binding status, and every file in place.
test_compliant if {
  findings := policy.deny with input as data.fixtures.compliant
  count(findings) == 0
}

# The default command renders typos.toml but neither runs Typos nor checks phrases.
test_default_command if {
  findings := policy.deny with input as data.fixtures.default_command
  profile(findings) == {
    ["PD-007", "noncompliant", 0, "no recipe reachable from \"spelling\" runs typos-config-builder gate"],
    ["PD-007", "noncompliant", 3, "\"spelling\"-path recipe runs typos-config-builder without gate; gate regenerates typos.toml, runs Typos, and checks phrases in one step"],
  }
}

# The gate may run from a prerequisite of the spelling target.
test_delegated if {
  findings := policy.deny with input as data.fixtures.delegated
  count(findings) == 0
}

# The builder runs its own pinned Typos; a second, direct run is the legacy shape.
test_direct_typos if {
  findings := policy.deny with input as data.fixtures.direct_typos
  profile(findings) == {
    ["PD-007", "noncompliant", 4, "\"spelling\"-path recipe runs typos directly; gate runs the builder's pinned Typos"],
  }
}

# The 0.1.x design regenerates typos.toml on every run and never drift-checks it.
test_drift_check if {
  findings := policy.deny with input as data.fixtures.drift_check
  profile(findings) == {
    ["PD-007", "noncompliant", 0, "no recipe reachable from \"spelling\" runs typos-config-builder gate"],
    ["PD-007", "noncompliant", 3, "\"spelling\"-path recipe drift-checks typos.toml with --check; gate regenerates it from the live dictionary instead"],
  }
}

# A line that only prints `typos` is a mention, not a run.
test_echo_mention if {
  findings := policy.deny with input as data.fixtures.echo_mention
  count(findings) == 0
}

test_gitignore_missing if {
  findings := policy.deny with input as data.fixtures.gitignore_missing
  profile(findings) == {
    ["PD-011", "noncompliant", 0, ".gitignore is missing; it must list .typos-oxendict-base.json and .typos-oxendict-base.toml"],
  }
}

test_gitignore_none_listed if {
  findings := policy.deny with input as data.fixtures.gitignore_none_listed
  profile(findings) == {
    ["PD-011", "noncompliant", 0, ".gitignore does not list .typos-oxendict-base.json, the builder's untracked cache"],
    ["PD-011", "noncompliant", 0, ".gitignore does not list .typos-oxendict-base.toml, the builder's untracked cache"],
  }
}

test_gitignore_partial if {
  findings := policy.deny with input as data.fixtures.gitignore_partial
  profile(findings) == {
    ["PD-011", "noncompliant", 0, ".gitignore does not list .typos-oxendict-base.toml, the builder's untracked cache"],
  }
}

# A root-anchored `.gitignore` line names the same file.
test_gitignore_rooted if {
  findings := policy.deny with input as data.fixtures.gitignore_rooted
  count(findings) == 0
}

test_legacy_targets if {
  findings := policy.deny with input as data.fixtures.legacy_targets
  profile(findings) == {
    ["PD-008", "noncompliant", 5, "Makefile defines the legacy \"spelling-helper-test\" helper target; gate replaces it"],
    ["PD-008", "noncompliant", 8, "Makefile defines the legacy \"spelling-phrase-check\" helper target; gate replaces it"],
  }
}

test_legacy_variables if {
  findings := policy.deny with input as data.fixtures.legacy_variables
  profile(findings) == {
    ["PD-008", "noncompliant", 1, "Makefile assigns TYPOS_VERSION; the builder pins its own Typos and dependencies"],
    ["PD-008", "noncompliant", 2, "Makefile assigns PATHSPEC_VERSION; the builder pins its own Typos and dependencies"],
    ["PD-008", "noncompliant", 3, "Makefile assigns TYPOS_CONFIG_BUILDER_COMMIT; the builder pins its own Typos and dependencies"],
  }
}

test_no_gate if {
  findings := policy.deny with input as data.fixtures.no_gate
  profile(findings) == {
    ["PD-007", "noncompliant", 0, "no recipe reachable from \"spelling\" runs typos-config-builder gate"],
  }
}

test_no_makefile if {
  findings := policy.deny with input as data.fixtures.no_makefile
  profile(findings) == {
    ["PD-007", "noncompliant", 0, "root Makefile is missing; a \"spelling\" target must run typos-config-builder gate"],
  }
}

test_no_spelling_target if {
  findings := policy.deny with input as data.fixtures.no_spelling_target
  profile(findings) == {
    ["PD-007", "noncompliant", 0, "the \"spelling\" target is absent; it must run typos-config-builder gate"],
  }
}

# A repository with no spelling setup is out of scope.
test_not_applicable if {
  findings := policy.deny with input as data.fixtures.not_applicable
  count(findings) == 0
}

test_other_repository if {
  findings := policy.deny with input as data.fixtures.other_repository
  profile(findings) == {
    ["PD-007", "noncompliant", 3, "\"spelling\"-path recipe pins git+https://github.com/example/typos-config-builder.git@v0.1.2, not the github.com/leynos/typos-config-builder repository"],
  }
}

# An overlay that cannot be decoded is indeterminate, never passed.
test_overlay_malformed if {
  findings := policy.deny with input as data.fixtures.overlay_malformed
  profile(findings) == {
    ["PD-012", "indeterminate", 0, "typos.local.toml could not be decoded: invalid TOML: Invalid value (at end of document)"],
  }
}

test_overlay_missing if {
  findings := policy.deny with input as data.fixtures.overlay_missing
  profile(findings) == {
    ["PD-012", "noncompliant", 0, "typos.local.toml is missing; the gate needs a schema 1 overlay, even an empty one"],
  }
}

test_overlay_no_schema if {
  findings := policy.deny with input as data.fixtures.overlay_no_schema
  profile(findings) == {
    ["PD-012", "noncompliant", 0, "typos.local.toml declares schema none; the builder reads schema 1"],
  }
}

test_overlay_schema_2 if {
  findings := policy.deny with input as data.fixtures.overlay_schema_2
  profile(findings) == {
    ["PD-012", "noncompliant", 0, "typos.local.toml declares schema 2; the builder reads schema 1"],
  }
}

# A commit is immutable but not a release, and cannot be compared with the floor.
test_sha_pin if {
  findings := policy.deny with input as data.fixtures.sha_pin
  profile(findings) == {
    ["PD-007", "noncompliant", 3, "\"spelling\"-path recipe pins typos-config-builder to commit d6da92f02240a79a945c835f69bdd08a888da1d0; pin a release tag at or above v0.1.2"],
  }
}

# `|| true` masks the gate's status.
test_soft_skip if {
  findings := policy.deny with input as data.fixtures.soft_skip
  profile(findings) == {
    ["PD-007", "noncompliant", 3, "\"spelling\"-path recipe soft-skips typos-config-builder gate; its exit status cannot fail the target"],
  }
}

test_unpinned if {
  findings := policy.deny with input as data.fixtures.unpinned
  profile(findings) == {
    ["PD-007", "noncompliant", 3, "\"spelling\"-path recipe runs typos-config-builder without pinning a release; run it with uvx --from the tagged repository"],
  }
}

# A builder hidden behind an unresolved variable cannot be proven.
test_unresolved if {
  findings := policy.deny with input as data.fixtures.unresolved
  profile(findings) == {
    ["PD-007", "indeterminate", 0, "no recipe reachable from \"spelling\" provably runs typos-config-builder gate; unresolved Make variables: BUILDER"],
  }
}

# cuprum's shape: uv tool run through variables, a `?=` version, a continuation line, and a conditional environment prefix.
test_uv_tool_run if {
  findings := policy.deny with input as data.fixtures.uv_tool_run
  count(findings) == 0
}

test_vendored if {
  findings := policy.deny with input as data.fixtures.vendored
  profile(findings) == {
    ["PD-009", "noncompliant", 0, "scripts/generate_typos_config.py is vendored spelling machinery; typos-config-builder owns generation and phrase checks"],
    ["PD-009", "noncompliant", 0, "scripts/tests/test_typos_rollout_check.py is vendored spelling machinery; typos-config-builder owns generation and phrase checks"],
    ["PD-009", "noncompliant", 0, "scripts/typos_rollout_check.py is vendored spelling machinery; typos-config-builder owns generation and phrase checks"],
  }
}

# Vendored machinery alone makes the repository a subject.
test_vendored_only if {
  findings := policy.deny with input as data.fixtures.vendored_only
  profile(findings) == {
    ["PD-007", "noncompliant", 0, "root Makefile is missing; a \"spelling\" target must run typos-config-builder gate"],
    ["PD-009", "noncompliant", 0, "scripts/typos_rollout.py is vendored spelling machinery; typos-config-builder owns generation and phrase checks"],
    ["PD-011", "noncompliant", 0, ".gitignore is missing; it must list .typos-oxendict-base.json and .typos-oxendict-base.toml"],
    ["PD-012", "noncompliant", 0, "typos.local.toml is missing; the gate needs a schema 1 overlay, even an empty one"],
    ["PD-013", "noncompliant", 0, "AGENTS.md is missing; it must carry typos-config-builder's spelling block between its markers"],
  }
}

# An included Makefile may define anything, so the closure is not proven.
test_with_include if {
  findings := policy.deny with input as data.fixtures.with_include
  profile(findings) == {
    ["PD-007", "indeterminate", 0, "Makefile includes other files; the spelling recipes cannot be proven"],
  }
}

# The floor is a parameter: raising it past the pinned release fails the gate.
test_raised_floor_rejects_the_pinned_release if {
  findings := policy.deny with input as data.fixtures.compliant with data.parameters.builder_floor as "v0.2.0"
  profile(findings) == {
    ["PD-007", "noncompliant", 3, "\"spelling\"-path recipe pins typos-config-builder v0.1.2, below the floor v0.2.0"],
  }
}

# Another package's envelope is refused rather than read.
test_wrong_kind_is_indeterminate if {
  findings := policy.deny with input as object.union(data.fixtures.compliant, {"kind": "policy-input/other"})
  profile(findings) == {
    ["EN-001", "indeterminate", 0, "policy input is not a policy-input/spelling-config-baseline envelope at schema version 1"],
  }
}

# Each pin compares with the newest published text at or below it.
test_a_pin_compares_with_the_newest_text_at_or_below_it if {
  blocks := {"v0.1.3": data.parameters.agents_md_blocks["v0.1.3"], "v0.2.0": "## Spelling\n\nA later text."}
  findings := policy.deny with input as data.fixtures.above_floor with data.parameters.agents_md_blocks as blocks
  profile(findings) == {
    ["PD-013", "noncompliant", 0, "AGENTS.md's spelling block differs from typos-config-builder v0.2.0's docs/agents-md-spelling.md; copy it verbatim"],
  }
}

# A pin older than every published text compares with the earliest.
test_a_pin_older_than_every_text_compares_with_the_earliest if {
  blocks := {"v0.1.3": data.parameters.agents_md_blocks["v0.1.3"], "v0.2.0": "## Spelling\n\nA later text."}
  findings := policy.deny with input as data.fixtures.compliant with data.parameters.agents_md_blocks as blocks
  count(findings) == 0
}

# A gate whose pin cannot be proven compares with the newest published text.
test_an_unproven_pin_compares_with_the_newest_text if {
  blocks := {"v0.1.3": data.parameters.agents_md_blocks["v0.1.3"], "v0.2.0": "## Spelling\n\nA later text."}
  findings := policy.deny with input as data.fixtures.unpinned with data.parameters.agents_md_blocks as blocks
  some finding in findings
  finding.rule_id == "PD-013"
  finding.msg == "AGENTS.md's spelling block differs from typos-config-builder v0.2.0's docs/agents-md-spelling.md; copy it verbatim"
}

# Without any published text the block cannot be compared, so it is not passed.
test_no_configured_text_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.compliant with data.parameters.agents_md_blocks as {}
  profile(findings) == {
    ["PD-013", "indeterminate", 0, "no canonical AGENTS.md spelling block is configured; the block cannot be compared"],
  }
}
