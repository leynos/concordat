# Policy tests for the whitaker-provisioning rule package.
#
# Fixture envelopes are supplied via `conftest verify --data fixtures/data.json`
# and appear under `data.fixtures`. Every test runs under the parameters below,
# which mirror the manifest's defaults with a stand-in listed revision, and
# pins the exact finding profile a fixture must produce.
package canon.lint_rules.whitaker_provisioning_test

import rego.v1

import data.canon.lint_rules.whitaker_provisioning as policy

listed := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

parameters := {
	"compliant_install_whitaker_refs": [listed],
	"producer_repositories": ["leynos/whitaker"],
	"action_repository": "leynos/shared-actions",
	"action_directory": ".github/actions/install-whitaker",
	"exemptions": [{
		"repository": "leynos/agent-helper-scripts",
		"path": "get-rust-tooling",
		"reason": "developer-environment script; CI never runs it",
	}],
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
	contains(msg, "is not a revision known to carry")
}

# -- sufficient: the action at a listed revision is the whole of it --------------

# A cache of the installer, comments and echoes naming it, a `test -x` on its
# path, a lint run and an unrelated download are none of them a route.
test_the_action_at_a_listed_revision_is_compliant if {
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

test_an_unlisted_revision_is_refused if {
	profile(data.fixtures.unlisted_pin) == {["QG-002", "noncompliant", ".github/workflows/ci.yml"]}
	is_pin(data.fixtures.unlisted_pin)
	not is_route(data.fixtures.unlisted_pin)
	some msg in messages(data.fixtures.unlisted_pin)
	contains(msg, "compliant_install_whitaker_refs")
	contains(msg, "git merge-base --is-ancestor")
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

test_an_empty_list_refuses_every_pin if {
	fs := policy.deny with input as data.fixtures.compliant
		with data.parameters as object.union(parameters, {"compliant_install_whitaker_refs": []})
	{f.rule_id | some f in fs} == {"QG-002"}
	every f in fs {
		contains(f.msg, "is not a revision known to carry")
	}
}

test_without_the_exemption_the_developer_script_is_a_route if {
	fs := policy.deny with input as data.fixtures.exempt_developer_script
		with data.parameters as object.union(parameters, {"exemptions": []})
	{[f.rule_id, f.path] | some f in fs} == {["QG-002", "get-rust-tooling"]}
}
