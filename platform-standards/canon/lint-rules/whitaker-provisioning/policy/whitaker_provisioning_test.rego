# Policy tests for the whitaker-provisioning rule package.
#
# Fixture envelopes are supplied via `conftest verify --data fixtures/data.json`
# and appear under `data.fixtures`. Every test runs under the parameters below,
# which mirror the manifest's defaults, and pins the exact finding profile a
# fixture must produce. Whether a pinned revision is certified is a fact the
# fixture envelopes record under `install_certifications`; the generator
# decides it with the production decision over a synthetic action repository.
package canon.lint_rules.whitaker_provisioning_test

import rego.v1

import data.canon.lint_rules.whitaker_provisioning as policy

parameters := {
	"certification_tag_prefix": "certified/install-whitaker/",
	"action_branch": "main",
	"producer_repositories": ["leynos/whitaker"],
	"action_repository": "leynos/shared-actions",
	"action_directory": ".github/actions/install-whitaker",
	"exemptions": [
		{
			"repository": "leynos/agent-helper-scripts",
			"path": "get-rust-tooling",
			"reason": "developer-environment script; CI never runs it",
		},
		{
			"repository": "leynos/example-pinned-suite",
			"path": ".github/workflows/ci.yml",
			"reason": "pins the suite until whitaker#311 is fixed",
		},
	],
}

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as parameters
}

profile(fixture) := {[f.rule_id, f.verdict, f.path] | some f in findings(fixture)}

messages(fixture) := {f.msg | some f in findings(fixture)}

# Every finding is QG-002, so a route and a pin are told apart by message.
is_route(fixture) if {
	some msg in messages(fixture)
	contains(msg, "replace this route with that step")
}

is_pin(fixture) if {
	some msg in messages(fixture)
	contains(msg, "is not a certified revision")
}

is_unchecked(fixture) if {
	some msg in messages(fixture)
	contains(msg, "could not be checked for certification")
}

# -- sufficient: the action at a certified revision is the whole of it -----------

# A cache of the installer, comments and echoes naming it, a `test -x` on its
# path, a lint run and an unrelated download are none of them a route.
test_the_action_at_a_certified_revision_is_compliant if {
	count(findings(data.fixtures.compliant)) == 0
}

test_a_repository_without_whitaker_is_compliant if {
	count(findings(data.fixtures.no_whitaker)) == 0
}

test_the_producer_is_exempt if {
	count(findings(data.fixtures.producer)) == 0
}

# The owning repository runs its action locally and its action's own body
# downloads and runs the installer; neither is a second route.
test_the_action_repository_may_run_and_define_the_action if {
	count(findings(data.fixtures.action_repository)) == 0
}

test_a_named_developer_script_is_exempt if {
	count(findings(data.fixtures.exempt_developer_script)) == 0
}

# -- narrow: every other route is refused ---------------------------------------

test_a_binstall_step_is_refused if {
	profile(data.fixtures.binstall_step) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_route(data.fixtures.binstall_step)
	not is_pin(data.fixtures.binstall_step)
	some msg in messages(data.fixtures.binstall_step)
	contains(msg, "installs a Whitaker tool with Cargo")
	contains(msg, "leynos/shared-actions/.github/actions/install-whitaker@<pin>")
}

# The `--git`/`--rev` form spans continued lines; they are one command.
test_a_git_revision_install_is_refused if {
	profile(data.fixtures.cargo_install_git) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_route(data.fixtures.cargo_install_git)
	not is_pin(data.fixtures.cargo_install_git)
}

test_a_direct_invocation_is_refused if {
	profile(data.fixtures.direct_invocation) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_route(data.fixtures.direct_invocation)
	not is_pin(data.fixtures.direct_invocation)
	some msg in messages(data.fixtures.direct_invocation)
	contains(msg, "runs whitaker-installer directly")
}

test_a_curl_download_is_refused if {
	profile(data.fixtures.curl_download) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_route(data.fixtures.curl_download)
	not is_pin(data.fixtures.curl_download)
	some msg in messages(data.fixtures.curl_download)
	contains(msg, "downloads a Whitaker tool")
}

# ortho-config #492's script: it fetches the asset with `gh api` against a
# releases endpoint and never writes a download URL.
test_a_release_asset_script_is_refused if {
	profile(data.fixtures.release_asset_script) == {["QG-002", "noncompliant", "scripts/install_whitaker_binary.sh"]}
	is_route(data.fixtures.release_asset_script)
	not is_pin(data.fixtures.release_asset_script)
	some msg in messages(data.fixtures.release_asset_script)
	contains(msg, "downloads a Whitaker tool")
}

test_a_makefile_install_is_refused if {
	profile(data.fixtures.makefile_install) == {["QG-002", "noncompliant", "Makefile"]}
	is_route(data.fixtures.makefile_install)
	not is_pin(data.fixtures.makefile_install)
}

test_another_action_handed_a_tool_is_refused if {
	profile(data.fixtures.install_action_tool) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_route(data.fixtures.install_action_tool)
	not is_pin(data.fixtures.install_action_tool)
}

test_a_composite_action_route_is_refused if {
	profile(data.fixtures.composite_action_route) == {["QG-002", "noncompliant", ".github/actions/setup/action.yml"]}
	is_route(data.fixtures.composite_action_route)
	not is_pin(data.fixtures.composite_action_route)
}

# An exemption names its repository: the same script elsewhere is a route.
test_an_exemption_does_not_travel if {
	profile(data.fixtures.exempt_script_elsewhere) == {["QG-002", "noncompliant", "get-rust-tooling"]}
	is_route(data.fixtures.exempt_script_elsewhere)
	not is_pin(data.fixtures.exempt_script_elsewhere)
}

# After the floor, but the action's runtime content has changed: a maintainer
# certifies new content by tagging it, and until then it is refused.
test_a_revision_with_changed_runtime_content_is_refused if {
	profile(data.fixtures.unlisted_pin) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_pin(data.fixtures.unlisted_pin)
	not is_route(data.fixtures.unlisted_pin)
	some msg in messages(data.fixtures.unlisted_pin)
	contains(msg, "changes the action's runtime content")
	contains(msg, "certified/install-whitaker/<installer version>")
}

test_a_revision_older_than_every_floor_is_refused if {
	profile(data.fixtures.older_pin) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_pin(data.fixtures.older_pin)
	some msg in messages(data.fixtures.older_pin)
	contains(msg, "older than, or unrelated to, every certification tag")
}

# A fork-only commit is not on the action repository's default branch.
test_a_revision_off_the_default_branch_is_refused if {
	profile(data.fixtures.off_branch_pin) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_pin(data.fixtures.off_branch_pin)
	some msg in messages(data.fixtures.off_branch_pin)
	contains(msg, "is not on the default branch")
}

test_a_revision_no_repository_knows_is_refused if {
	profile(data.fixtures.unknown_pin) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_pin(data.fixtures.unknown_pin)
	some msg in messages(data.fixtures.unknown_pin)
	contains(msg, "names no commit")
}

# A tag, a branch or a short SHA names no revision to certify.
test_a_floating_ref_is_refused if {
	profile(data.fixtures.floating_ref) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_pin(data.fixtures.floating_ref)
	not is_route(data.fixtures.floating_ref)
}

# An API that could not answer says nothing about the revision: indeterminate,
# never noncompliant.
test_a_revision_whose_certification_could_not_be_read_is_indeterminate if {
	profile(data.fixtures.unreadable_pin) == {["QG-002", "indeterminate", ".github/workflows/ci.yml"]}
	is_unchecked(data.fixtures.unreadable_pin)
	not is_pin(data.fixtures.unreadable_pin)
	some msg in messages(data.fixtures.unreadable_pin)
	contains(msg, "rate limited")
}

# An envelope built without the command step carries no answer for its pin,
# which is not a pass.
test_a_revision_nobody_checked_is_indeterminate if {
	profile(data.fixtures.unresolved_pin) == {["QG-002", "indeterminate", ".github/workflows/ci.yml"]}
	is_unchecked(data.fixtures.unresolved_pin)
	some msg in messages(data.fixtures.unresolved_pin)
	contains(msg, "the audit did not resolve it")
}

# The verdict is read from the envelope, not assumed: the same checkout with
# its answer removed, or turned to a refusal, changes the finding.
test_the_recorded_answer_decides if {
	bare := object.remove(data.fixtures.compliant, ["install_certifications"])
	removed := object.union(bare, {"install_certifications": {}})
	{[f.rule_id, f.verdict] | some f in findings(removed)} == {["QG-002", "indeterminate"]}
	refused := object.union(bare, {"install_certifications": {"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": {
		"status": "uncertified",
		"floor": null,
		"reason": "stand-in",
	}}})
	{[f.rule_id, f.verdict] | some f in findings(refused)} == {["QG-002", "noncompliant"]}
}

# A status the command does not record is no answer.
test_an_unrecognised_status_is_indeterminate if {
	bare := object.remove(data.fixtures.compliant, ["install_certifications"])
	odd := object.union(bare, {"install_certifications": {"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": {
		"status": "probably",
		"floor": null,
		"reason": null,
	}}})
	{[f.rule_id, f.verdict] | some f in findings(odd)} == {["QG-002", "indeterminate"]}
}

test_a_fork_of_the_action_is_refused if {
	profile(data.fixtures.fork_action) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_pin(data.fixtures.fork_action)
	not is_route(data.fixtures.fork_action)
}

# A consumer's own `install-whitaker` directory is a copy, refused as a use
# and audited as a route.
test_a_local_copy_of_the_action_is_refused if {
	profile(data.fixtures.local_copy) == {
		["QG-002", "noncompliant", ".github/workflows/ci.yml"],
		["QG-002", "noncompliant", ".github/actions/install-whitaker/action.yml"],
	}
	is_route(data.fixtures.local_copy)
	is_pin(data.fixtures.local_copy)
}

test_an_unreadable_workflow_is_indeterminate if {
	profile(data.fixtures.unreadable_workflow) == {["QG-002", "indeterminate", ".github/workflows/ci.yml"]}
}

# A composite action that is not valid YAML stays in the envelope with its
# reason, as a workflow does, rather than dropping out of the audit.
test_an_unreadable_action_is_indeterminate if {
	profile(data.fixtures.unreadable_action) == {["QG-002", "indeterminate", ".github/actions/setup/action.yml"]}
}

test_an_unknown_envelope_is_indeterminate if {
	profile(data.fixtures.unknown_schema) == {["EN-001", "indeterminate", "."]}
}

# -- the parameters are what decide ---------------------------------------------

test_the_tag_prefix_is_named_in_a_refusal if {
	fs := policy.deny with input as data.fixtures.unlisted_pin
		with data.parameters as object.union(parameters, {"certification_tag_prefix": "approved/iw/"})
	some f in fs
	contains(f.msg, "approved/iw/<installer version>")
}

test_without_the_exemption_the_developer_script_is_a_route if {
	fs := policy.deny with input as data.fixtures.exempt_developer_script
		with data.parameters as object.union(parameters, {"exemptions": []})
	{[f.rule_id, f.path] | some f in fs} == {["QG-002", "get-rust-tooling"]}
}

# A pinned-suite install is exempt in one named workflow, and only there:
# a second workflow in the same repository that installs Whitaker by hand is a
# route, so the exemption is by path and not by repository.
test_a_workflow_exemption_covers_only_its_workflow if {
	profile(data.fixtures.exempt_pinned_suite) == {["QG-002", "noncompliant", ".github/workflows/release.yml"]}
	is_route(data.fixtures.exempt_pinned_suite)
}

# The same workflow in another repository is a route: the exemption names its repository.
test_a_workflow_exemption_does_not_travel if {
	profile(data.fixtures.exempt_pinned_suite_elsewhere) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_route(data.fixtures.exempt_pinned_suite_elsewhere)
}

test_without_the_exemption_the_workflow_is_a_route if {
	fs := policy.deny with input as data.fixtures.exempt_pinned_suite
		with data.parameters as object.union(parameters, {"exemptions": []})
	{[f.rule_id, f.path] | some f in fs} == {
		["QG-002", ".github/workflows/ci.yml"],
		["QG-002", ".github/workflows/release.yml"],
	}
}
