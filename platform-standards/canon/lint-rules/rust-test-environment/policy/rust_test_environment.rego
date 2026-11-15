# rust-test-environment: TE-001 to TE-004.
#
# Input is a policy-input/rust-test-environment envelope. The Python envelope
# reads Cargo manifests and Clippy configuration with a TOML parser and the Rust
# sources with tree-sitter; this policy decides what those facts mean.
#
# The estate's rule is that tests never change the process-wide environment: it
# is an injected object, or the test runs a subprocess. Clippy finds the calls
# (std::env::set_var, remove_var, set_current_dir, and the wrapper crates that
# call them) after macro expansion, so this policy never looks for a call. It
# holds a repository to the three things that keep Clippy's verdict standing:
# every workspace member's effective Clippy configuration disallows the calls
# (TE-001) and the entry points of the wrapper crates the member uses (TE-002);
# the lint is suppressed only by a site-level #[expect] with a reason (TE-003);
# and no configuration file lowers it (TE-004). EN-001 is the fail-closed
# verdict for a file the audit could not read. No clause is waivable, so the
# policy reads no exception data.
package canon.lint_rules.rust_test_environment

import rego.v1

finding(id, verdict, path, line, msg) := {
	"rule_id": id,
	"severity": "error",
	"verdict": verdict,
	"path": path,
	"line": line,
	"msg": msg,
}

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/rust-test-environment"
	is_object(input.applicability)
	is_array(input.members)
	is_array(input.suppressions)
	is_array(input.config_suppressions)
	is_array(input.unreadable)
	is_array(input.required_paths)
}

# A repository is audited when its root has a Cargo.toml. Without one it is not
# a Rust repository and no clause applies.
audited if {
	envelope_ok
	input.applicability.cargo_manifest == true
}

# -- envelope and unreadable files -----------------------------------------------

deny contains finding("EN-001", "indeterminate", "", 0, msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/rust-test-environment envelope at schema version 1"
}

deny contains finding("EN-001", "indeterminate", "", 0, msg) if {
	audited
	some entry in input.unreadable
	msg := sprintf("the audit could not read a file, so the lint's configuration or suppressions are unknown: %s", [entry])
}

# -- TE-001: the guard ----------------------------------------------------------------

# Members that share one configuration file are reported together, once per
# file, so a workspace of twenty crates is one finding and not twenty.

members_without_config := sort([m.path |
	some m in input.members
	m.clippy.path == null
])

deny contains finding("TE-001", "noncompliant", members_without_config[0], 0, msg) if {
	audited
	count(members_without_config) > 0
	msg := sprintf(
		"%d workspace member(s) (%s) have no clippy.toml or .clippy.toml at or above them, so nothing disallows %s",
		[count(members_without_config), concat(", ", members_without_config), concat(", ", input.required_paths)],
	)
}

readable_configs := {m.clippy |
	some m in input.members
	m.clippy.path != null
	m.clippy.readable == true
}

members_using(config_path) := sort([m.path |
	some m in input.members
	m.clippy.path == config_path
])

deny contains finding("TE-001", "noncompliant", config.path, 0, msg) if {
	audited
	some config in readable_configs
	missing := {p | some p in input.required_paths; not p in config.disallowed}
	count(missing) > 0
	msg := sprintf(
		"%s lints %d workspace member(s) (%s) and its disallowed-methods does not list %s; add them so a test cannot change the process-wide environment",
		[config.path, count(members_using(config.path)), concat(", ", members_using(config.path)), concat(", ", sort(missing))],
	)
}

# -- TE-002: wrapper crates ------------------------------------------------------------

# One entry per configuration file and wrapper crate, with the members that
# depend on the crate and the paths the crate needs disallowed.
wrapper_needs := {need |
	some m in input.members
	m.clippy.path != null
	m.clippy.readable == true
	some w in m.wrappers
	need := {"config": m.clippy, "crate": w.crate, "paths": w.paths}
}

deny contains finding("TE-002", "noncompliant", need.config.path, 0, msg) if {
	audited
	some need in wrapper_needs
	missing := {p | some p in need.paths; not p in need.config.disallowed}
	count(missing) > 0
	users := sort([m.path |
		some m in input.members
		m.clippy.path == need.config.path
		some w in m.wrappers
		w.crate == need.crate
	])
	msg := sprintf(
		"%d workspace member(s) (%s) depend on %s, whose entry points change the process-wide environment; %s does not disallow %s",
		[count(users), concat(", ", users), need.crate, need.config.path, concat(", ", sort(missing))],
	)
}

# -- TE-003: suppression only by a site-level expect with a reason -------------------

deny contains finding("TE-003", "noncompliant", s.path, s.line, msg) if {
	audited
	some s in input.suppressions
	s.kind == "allow"
	msg := sprintf("%s is allowed; the only sanctioned suppression is #[expect(clippy::disallowed_methods, reason = \"...\")] on the function or statement that needs it, so the backlog reports itself when a site is migrated", [s.lint])
}

deny contains finding("TE-003", "noncompliant", s.path, s.line, msg) if {
	audited
	some s in input.suppressions
	s.kind == "expect-no-reason"
	msg := "#[expect(clippy::disallowed_methods)] has no reason, or a blank one; say why this site may change the process-wide environment"
}

deny contains finding("TE-003", "noncompliant", s.path, s.line, msg) if {
	audited
	some s in input.suppressions
	s.kind == "expect-scope"
	msg := sprintf("an expectation of %s covers more than one site (a crate or module attribute, or an attribute on a module, trait or type); put it on the function or statement that needs it", [s.lint])
}

# -- TE-004: no configuration-level suppression ----------------------------------------

deny contains finding("TE-004", "noncompliant", c.path, 0, msg) if {
	audited
	some c in input.config_suppressions
	msg := sprintf("%s lowers the lint outside source, which hides every site; remove it and use #[expect] on the site", [c.detail])
}
