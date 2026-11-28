# NC-011, NC-012, NC-013: a test spends a real compile only when the compile is
# the thing under test, and the repository has said so.
#
# The Python envelope has read every function in test scope that owns a compiler
# spawn, a trybuild or macrotest suite or a `cargo_metadata` query (the
# `builders`), and the entries of `.concordat/nested-builds.toml` (the
# `declarations`), each with whether its warm-up resolves. This policy turns an
# undeclared builder into NC-011, a build of the test infrastructure into
# NC-012, and a declared warm-up that is not there into NC-013. Anything the
# reader could not establish is indeterminate, never a pass.
#
# Every finding except an envelope defect carries the configured severity, so
# the package ships as a survey: a warning is reported and does not block.
package canon.lint_rules.nested_cargo_test_builds

import rego.v1

ENVELOPE_KIND := "policy-input/nested-cargo-test-builds"

# What a build may be the test of. The list is closed on purpose: free text
# would let a rationale argue its way past the rule.
allowed_subjects := {
	"compiler-diagnostics",
	"macro-expansion",
	"generated-code",
	"cargo-behaviour",
	"packaging",
	"build-script",
	"product-cli",
	"dependency-graph",
}

# A build whose subject is the machinery that supports the tests. Nothing the
# repository ships is under test, so no rationale makes the compile worth its
# cost: make the input synthetic instead.
prohibited_subjects := {
	"test-harness",
	"evidence-writer",
	"timeout-machinery",
	"preparation-stamp",
	"compile-counter",
}

finding(rule_id, severity, verdict, path, line, message) := {
	"rule_id": rule_id,
	"severity": severity,
	"verdict": verdict,
	"path": path,
	"line": line,
	"msg": message,
}

default severity := "warning"

severity := data.parameters.severity

default exemptions := []

exemptions := data.parameters.exemptions

repository_name := input.repository.name

envelope_ok if {
	input.schema_version == 1
	input.kind == ENVELOPE_KIND
	is_object(input.repository)
	is_array(input.builders)
	is_array(input.declarations)
	is_array(input.problems)
	input.declarations_state in {"absent", "read", "invalid"}
}

# An exemption is a reviewed record: it names the repository, the exact file,
# why the build is worth its cost, and what event sends it back for review. A
# blank rationale or review trigger asks nobody to revisit it, so it suppresses
# nothing. Exemptions reach NC-011 and NC-013 only.
is_exempt(path) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.path == path
	is_string(exemption.rationale)
	trim_space(exemption.rationale) != ""
	is_string(exemption.review_trigger)
	trim_space(exemption.review_trigger) != ""
}

# A declaration names a file, and optionally one function in it. Without a
# function it covers every builder in the file.
declares(declaration, builder) if {
	declaration.path == builder.path
	declaration.function == ""
}

declares(declaration, builder) if {
	declaration.path == builder.path
	declaration.function == builder.function
}

is_declared(builder) if {
	some declaration in input.declarations
	declares(declaration, builder)
}

is_builder_of(declaration) if {
	some builder in input.builders
	declares(declaration, builder)
}

# -- envelope defects -----------------------------------------------------------

deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/nested-cargo-test-builds envelope at schema version 1"
}

deny contains finding("EN-001", severity, "indeterminate", "", 0, msg) if {
	envelope_ok
	some problem in input.problems
	msg := sprintf("a nested-cargo fact could not be established: %s", [problem])
}

# A declaration whose subject is in neither list is a record the reader cannot
# judge, so it is indeterminate rather than a pass.
deny contains finding("EN-001", severity, "indeterminate", declaration.path, 0, msg) if {
	envelope_ok
	some declaration in input.declarations
	not declaration.subject in allowed_subjects
	not declaration.subject in prohibited_subjects
	msg := sprintf(
		"%s declares the subject %q, which is not one of the known subjects",
		[declaration.path, declaration.subject],
	)
}

# -- NC-011: a builder nobody has declared --------------------------------------

_describe(builder) := sprintf("%s (%s)", [builder.function, builder.form]) if builder.function != ""

_describe(builder) := builder.form if builder.function == ""

deny contains finding("NC-011", severity, "noncompliant", builder.path, builder.line, msg) if {
	envelope_ok
	some builder in input.builders
	builder.readable
	not is_declared(builder)
	not is_exempt(builder.path)
	msg := sprintf(
		"%s:%d: %s spends a real compile and no entry in .concordat/nested-builds.toml declares it. Name the file (and the function), say what the build is the test of and which warm-up pays for it; if the build is not the thing under test, replace it with a synthetic input",
		[builder.path, builder.line, _describe(builder)],
	)
}

# What the reader cannot see it can neither accept nor accuse. An exemption does
# not reach it.
deny contains finding("EN-001", severity, "indeterminate", builder.path, builder.line, msg) if {
	envelope_ok
	some builder in input.builders
	not builder.readable
	not is_declared(builder)
	msg := sprintf(
		"%s:%d: a spawn in %s starts a program or subcommand the reader cannot see, so whether it compiles and what it is the test of is unknown",
		[builder.path, builder.line, _describe(builder)],
	)
}

# A declaration with no builder behind it is not a record anybody can check.
deny contains finding("NC-011", severity, "noncompliant", declaration.path, 0, msg) if {
	envelope_ok
	some declaration in input.declarations
	not is_builder_of(declaration)
	not is_exempt(declaration.path)
	msg := sprintf(
		"%s: the declaration for %q matches no builder in the file, so it is stale: remove it or correct the path and function",
		[declaration.path, declaration.function],
	)
}

# -- NC-012: a build whose subject is the test infrastructure -------------------

deny contains finding("NC-012", severity, "noncompliant", declaration.path, 0, msg) if {
	envelope_ok
	some declaration in input.declarations
	declaration.subject in prohibited_subjects
	msg := sprintf(
		"%s declares a real build whose subject is %s. A test of the test infrastructure has no reason to spend a compile: feed the writer, reader, counter or timeout a synthetic input instead. This finding cannot be exempted",
		[declaration.path, declaration.subject],
	)
}

# A function that writes a scratch crate, builds it, and names the repository's
# harness code is a test of the harness whatever the repository declares. Both
# halves are required: either alone is only a reason to ask.
deny contains finding("NC-012", severity, "noncompliant", builder.path, builder.line, msg) if {
	envelope_ok
	some builder in input.builders
	builder.scratch_crate
	count(builder.harness_references) > 0
	msg := sprintf(
		"%s:%d: %s writes a scratch crate into a temporary directory, builds it, and uses the repository's own harness code (%s), so the build tests the harness and not the product. Feed the harness a synthetic input instead. This finding cannot be exempted",
		[builder.path, builder.line, _describe(builder), concat(", ", builder.harness_references)],
	)
}

# -- NC-013: a declared warm-up that is not there -------------------------------

deny contains finding("NC-013", severity, "noncompliant", declaration.path, 0, msg) if {
	envelope_ok
	some declaration in input.declarations
	declaration.coverage == "unresolved"
	not is_exempt(declaration.path)
	msg := sprintf(
		"%s declares %q as the warm-up for %q, but it does not resolve to anything that runs before the tests: a Makefile goal that `test` depends on or calls, a nextest setup script that selects the binary, or a test function that exists",
		[declaration.path, declaration.covered_by, declaration.function],
	)
}

deny contains finding("EN-001", severity, "indeterminate", declaration.path, 0, msg) if {
	envelope_ok
	some declaration in input.declarations
	declaration.coverage == "unreadable"
	msg := sprintf(
		"%s declares %q as the warm-up for %q, which the reader cannot check (an unknown form, or a Makefile it cannot follow)",
		[declaration.path, declaration.covered_by, declaration.function],
	)
}

# A fact the reader wrote in a shape this policy does not know is not a pass.
# `object.get` supplies a sentinel rather than reading the key directly, because
# a comparison on a missing key is undefined: the rule body would fail and the
# fact would escape unjudged.
deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	envelope_ok
	some builder in input.builders
	not _well_formed_builder(builder)
	msg := "a builder fact is missing a field its findings need (path, a numeric line, function, form, readable, scratch_crate, harness_references)"
}

deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	envelope_ok
	some declaration in input.declarations
	not _well_formed_declaration(declaration)
	msg := "a declaration fact is missing a field its findings need (path, function, subject, covered_by, coverage)"
}

_well_formed_builder(builder) if {
	is_string(object.get(builder, "path", null))
	is_number(object.get(builder, "line", null))
	is_string(object.get(builder, "function", null))
	object.get(builder, "form", "") in {"spawn", "trybuild", "macrotest", "metadata"}
	is_boolean(object.get(builder, "readable", null))
	is_boolean(object.get(builder, "scratch_crate", null))
	is_array(object.get(builder, "harness_references", null))
}

_well_formed_declaration(declaration) if {
	is_string(object.get(declaration, "path", null))
	is_string(object.get(declaration, "function", null))
	is_string(object.get(declaration, "subject", null))
	is_string(object.get(declaration, "covered_by", null))
	object.get(declaration, "coverage", "") in {"resolved", "unresolved", "unreadable"}
}
