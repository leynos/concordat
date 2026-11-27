# gate-workspace-coverage: GC-001, and EN-001 for what could not be read.
#
# Four tools narrow to less than a workspace without saying so: `cargo clippy`,
# `cargo nextest run`, `cargo test` (with and without `--doc`) and Whitaker.
# In a workspace whose root is also a package, a bare command selects the root
# package alone; `workspace.default-members` and a `-p` list narrow it further.
# The gate reads green over the members it selected and says nothing of the
# rest, which is how Whitaker skipped every non-root member of the estate's
# workspaces for months.
#
# The Python envelope read the root workspace (its members and its default
# selection), every command the gate reaches (the Makefile's `lint` and `test`
# targets, and every workflow and composite-action `run` step, one reading per
# matrix entry), and the fenced prose examples that show those commands. This
# policy decides what the facts mean. For each tool the gate runs, the members
# its commands select together must be the whole workspace, less the crates the
# rule's parameters exempt by repository and name with a rationale and a review
# trigger. A tool the gate does not run is not required; the rule asks that a
# tool, once run, run over everything.
#
# GC-001 and the indeterminate findings that report evidence the reader could
# not establish carry the configured severity, which defaults to `warning` so
# the package ships as a survey. An envelope defect is always an error.
package canon.lint_rules.gate_workspace_coverage

import rego.v1

# `cargo nextest run` and `cargo test` (without `--doc`) are two runners of one
# thing, the unit and integration tests, so their commands count together under
# the one tool `tests`: a repository may run everything under nextest and keep one
# special target under `cargo test`, and every member is covered when the two
# select it between them. Nextest does not run doctests, so `cargo test --doc` is
# a tool of its own, `test-doc`.
tools := ["clippy", "tests", "test-doc", "whitaker"]

unit_test_runners := {"nextest", "test"}

# The invocation tools a judged tool is made of.
runners(tool) := unit_test_runners if tool == "tests"

runners(tool) := {tool} if tool != "tests"

# How a judged tool reads in a finding: the tests are `cargo test` commands.
label(tool) := "test" if tool == "tests"

label(tool) := tool if tool != "tests"

finding(id, severity, verdict, path, msg) := {
	"rule_id": id,
	"severity": severity,
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": msg,
}

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/gate-workspace-coverage"
	is_object(input.applicability)
	is_object(input.makefile)
	is_array(input.invocations)
	is_array(input.examples)
	is_array(input.unreadable)
}

# A repository is audited when its root has a Cargo.toml. Without one it is not
# a Rust repository, or its workspace lives below the root and is out of reach.
audited if {
	envelope_ok
	input.applicability.cargo_manifest == true
}

default severity := "warning"

severity := data.parameters.severity

default exemptions := []

exemptions := data.parameters.exemptions

repository_name := input.repository.name

# An exemption is a reviewed record: it names the repository and the crate, why
# the gate may leave the crate out, and what event sends it back for review. A
# blank rationale or review trigger asks nobody to revisit it, so it is not a
# record and exempts nothing. `tools` limits it to some tools; absent, it
# covers all of them.
is_exempt(crate, tool) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.crate == crate
	is_string(exemption.rationale)
	trim_space(exemption.rationale) != ""
	is_string(exemption.review_trigger)
	trim_space(exemption.review_trigger) != ""
	covers_tool(exemption, tool)
}

covers_tool(exemption, _) if not exemption.tools

covers_tool(exemption, tool) if tool in exemption.tools

# An exemption that names a runner (`nextest` or `test`) applies to the tests.
covers_tool(exemption, tool) if {
	some runner in runners(tool)
	runner in exemption.tools
}

# -- the workspace ------------------------------------------------------------------

workspace_known if {
	audited
	is_object(input.workspace)
}

members := {m | some m in input.workspace.members}

# One member, or none, cannot be narrowed.
multi_member if count(members) > 1

# The members a list of Cargo package selectors names. Cargo accepts glob
# patterns (`*`, `?`, `[..]`) in `-p` and `--exclude`, so a selector names every
# member it matches, not only the one it equals.
named(selectors) := {m |
	some m in members
	some selector in selectors
	glob.match(selector, [], m)
}

# What a command selects. Spelling the workspace out selects every member less
# the exclusions; a `-p` list selects what it names; a bare command selects the
# root package of a workspace that has one, `default-members` when the manifest
# sets them, and every member of a virtual workspace.
selected(invocation) := members - named(invocation.excludes) if invocation.workspace_flag == true

selected(invocation) := named(invocation.packages) if {
	invocation.workspace_flag != true
	count(invocation.packages) > 0
}

selected(invocation) := {m | some m in input.workspace.default_members} if {
	invocation.workspace_flag != true
	count(invocation.packages) == 0
	input.workspace.default_members != null
}

selected(invocation) := {input.workspace.root_package} if {
	invocation.workspace_flag != true
	count(invocation.packages) == 0
	input.workspace.default_members == null
	input.workspace.kind == "root-package"
}

selected(invocation) := members if {
	invocation.workspace_flag != true
	count(invocation.packages) == 0
	input.workspace.default_members == null
	input.workspace.kind in {"virtual", "single"}
}

# -- invocations ----------------------------------------------------------------------

invocations_of(tool) := [i | some i in input.invocations; i.tool in runners(tool)]

# A command the reader cannot place cannot be counted as covering or as failing
# to cover, so a tool with one is reported as unknown and not judged.
unplaced(tool) := [i |
	some i in invocations_of(tool)
	not placed(i)
]

placed(i) if {
	i.resolved == true
	i.changes_directory == false
}

# A walk that skipped a target may have skipped the command that covers the
# rest, so no member can be called uncovered while it is incomplete.
walk_complete if input.makefile.is_resolved == true

tool_known(tool) if {
	count(unplaced(tool)) == 0
	walk_complete
	nothing_unreadable
}

# A file or line the reader could not read might hold the command that covers a
# member, or one that narrows a tool, so no tool is called uncovered while any
# input is unreadable. EN-001 reports each one.
nothing_unreadable if count(input.unreadable) == 0

covered(tool) := {m |
	some i in invocations_of(tool)
	some m in selected(i)
}

uncovered(tool) := {m |
	some m in members
	not m in covered(tool)
	not is_exempt(m, tool)
}

narrowing(tool) := [i |
	some i in invocations_of(tool)
	count(members - selected(i)) > 0
]

# An example shows the same narrowing when it leaves out some of the members the
# gate leaves out; one that merely selects a different subset does not.
narrowing_examples(tool) := [e |
	some e in input.examples
	e.tool in runners(tool)
	e.resolved == true
	e.changes_directory == false
	count((members - selected(e)) & uncovered(tool)) > 0
]

list_sources(items) := concat("; ", [sprintf("%s: %s", [i.source, i.command]) | some i in items])

examples_note(tool) := "" if count(narrowing_examples(tool)) == 0

examples_note(tool) := sprintf(
	"; documentation shows the same narrowing, update it with the gate: %s",
	[concat(", ", [e.source | some e in narrowing_examples(tool)])],
) if count(narrowing_examples(tool)) > 0

first_source(tool) := narrowing(tool)[0].source

# -- envelope and unreadable inputs ---------------------------------------------------

deny contains finding("EN-001", "error", "indeterminate", "", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/gate-workspace-coverage envelope at schema version 1"
}

deny contains finding("EN-001", severity, "indeterminate", "", msg) if {
	audited
	some entry in input.unreadable
	msg := sprintf("a gate command or file could not be read, so the audit cannot say which workspace members it covers: %s", [entry])
}

deny contains finding("EN-001", severity, "indeterminate", "Cargo.toml", msg) if {
	audited
	not is_object(input.workspace)
	msg := "the root Cargo.toml could not be read as a workspace, so no gate command can be judged against it"
}

deny contains finding("EN-001", severity, "indeterminate", i.source, msg) if {
	workspace_known
	multi_member
	some tool in tools
	some i in unplaced(tool)
	msg := sprintf("%s: the audit cannot place this %s command (%s), so it does not count as covering or missing any member: %s", [i.source, i.tool, reason(i), i.command])
}

reason(i) := i.why if i.why != ""

reason(i) := "it runs in another directory or against another manifest" if {
	i.why == ""
	i.changes_directory == true
}

# Reported on its own: a gate that is only `$(MAKE) -C nested lint` has no known
# command at all, yet the Makefile it delegates to may narrow every tool.
deny contains finding("EN-001", severity, "indeterminate", "Makefile", msg) if {
	workspace_known
	multi_member
	not walk_complete
	msg := "the Makefile walk from lint and test skipped a target it could not resolve (a `make -C`, or a variable that names a target), so the audit cannot say which commands the gate runs"
}

# -- GC-001: a gate command that leaves workspace members out --------------------------

deny contains finding("GC-001", severity, "noncompliant", first_source(tool), msg) if {
	workspace_known
	multi_member
	some tool in tools
	count(invocations_of(tool)) > 0
	tool_known(tool)
	count(uncovered(tool)) > 0
	msg := sprintf(
		"the %s commands the gate runs leave %d of %d workspace member(s) out: %s. Select the whole workspace (Cargo's --workspace or --all; for Whitaker, `--workspace` on either side of the `--`), or exempt a crate by name with a rationale and a review trigger in the rule's parameters. Narrowing commands: %s%s",
		[label(tool), count(uncovered(tool)), count(members), concat(", ", sort(uncovered(tool))), list_sources(narrowing(tool)), examples_note(tool)],
	)
}
