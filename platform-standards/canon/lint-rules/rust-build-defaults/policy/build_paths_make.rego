# rust-build-defaults, Makefile half of BD-007 and BD-008: the builds that
# replace the default.
#
# BD-001 to BD-006 read what Cargo auto-discovers. A Makefile recipe can
# replace it: an assigned `RUSTFLAGS` replaces every `rustflags` source in
# `.cargo/config.toml`, and a coverage build cannot use the Cranelift default at
# all, because `-Cinstrument-coverage` is LLVM-only. Facts come from the pinned
# `makeutil parse` report, never from a textual search of the Makefile.
package canon.lint_rules.rust_build_defaults

import rego.v1

default gate_targets := ["lint", "test", "typecheck", "build"]

gate_targets := data.parameters.gate_targets

located_finding(rule_id, verdict, path, line, msg) := object.union(
	finding(rule_id, verdict, path, msg),
	{"line": line},
)

# -- the Makefile facts -----------------------------------------------------

makefile_report := object.get(input, "makefile", null)

makefile_refused := object.get(input, "makefile_error", null)

make_variables := makefile_report.variables if makefile_report != null

make_variables := [] if makefile_report == null

make_rules := makefile_report.rules if makefile_report != null

make_rules := [] if makefile_report == null

joined(text) := regex.replace(text, `\\\n[[:space:]]*`, " ")

assignments_of(name) := [variable |
	some variable in make_variables
	variable.name == name
]

# A variable with exactly one unconditional, non-`define` assignment has one
# value the policy can substitute; any other stays a reference, and what it
# could hold is read from every assignment it has (`possible_text`).
single_valued(name) if {
	assignments := assignments_of(name)
	count(assignments) == 1
	count(assignments[0].conditions) == 0
	assignments[0].define_block == false
}

variable_value[name] := joined(variable.raw_value) if {
	some variable in make_variables
	name := variable.name
	single_valued(name)
}

replacements := object.union(
	{sprintf("$(%s)", [name]): value | some name, value in variable_value},
	{sprintf("${%s}", [name]): value | some name, value in variable_value},
)

expand(text) := strings.replace_n(
	replacements,
	strings.replace_n(replacements, strings.replace_n(replacements, joined(text))),
)

references(text) := {match[1] |
	some match in regex.find_all_string_submatch_n(`\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]`, text, -1)
}

variable_edges[name] contains referenced if {
	some variable in make_variables
	name := variable.name
	some referenced in references(variable.raw_value)
}

variable_graph := {variable.name: object.get(variable_edges, variable.name, []) | some variable in make_variables}

# Everything *text* could expand to: itself, and every assignment of every
# variable it reaches. An over-approximation by design: a flag named under a
# condition, or by a `$(if ...)` branch, is counted as present.
possible_text(text) := concat(" ", array.concat([text], [joined(variable.raw_value) |
	some name in graph.reachable(variable_graph, references(text))
	some variable in assignments_of(name)
]))

# -- flags ------------------------------------------------------------------
#
# Cargo reads `-C x` and `-Cx` alike. Make joins a function's result straight
# onto the text before it (`$(THREADS)$(if ...)`), so `$` ends a token too, as
# do the braces of a shell expansion (`${RUSTFLAGS:+$RUSTFLAGS }-Zthreads=8`).

flag_delimiters := `[[:space:]"'(){},]+|\$`

flag_tokens(text) := {token |
	normalized := regex.replace(regex.replace(text, `-C[[:space:]]+`, "-C"), `-Z[[:space:]]+`, "-Z")
	some token in regex.split(flag_delimiters, normalized)
	token != ""
}

carries_flag(text, flag) if flag in flag_tokens(text)

# The backend the last `-Zcodegen-backend=` token selects, as rustc reads it.
rustflags_backend(text) := selected if {
	selections := [substring(token, count("-Zcodegen-backend="), -1) |
		some token in regex.split(flag_delimiters, regex.replace(text, `-Z[[:space:]]+`, "-Z"))
		startswith(token, "-Zcodegen-backend=")
	]
	count(selections) > 0
	selected := selections[count(selections) - 1]
}

# A helper rather than `not rustflags_backend(x) == "cranelift"`: OPA hoists
# the call out of that negation, so an undefined selection would fail it.
names_cranelift(text) if rustflags_backend(text) == "cranelift"

# -- recipes as command segments --------------------------------------------

comment_or_print(segment) if regex.match(`^[[:space:]]*[-@+]*[[:space:]]*(#|echo([[:space:]]|$)|printf([[:space:]]|$))`, segment)

recipe_segments(recipe) := [segment |
	some segment in regex.split(`&&|\|\||;|\|`, expand(recipe.text))
	not comment_or_print(segment)
]

# `RUSTFLAGS=` anywhere on the line is an assignment: an environment prefix,
# an `export`, or an `env` argument all replace the configuration's sources.
assigned_values(segment, name) := [trim(match[2], `"'`) |
	some match in regex.find_all_string_submatch_n(
		sprintf(`(^|[[:space:]])%s=("[^"]*"|'[^']*'|[^[:space:]]*)`, [name]),
		segment, -1,
	)
]

runs_llvm_cov(segment) if regex.match(
	`(^|[[:space:]/])(cargo|\$\([A-Za-z_][A-Za-z0-9_]*\))([[:space:]]+\+[^[:space:]]+)?[[:space:]]+llvm-cov([[:space:]]|$)`,
	segment,
)

builds_release_profile(segment) if regex.match(`(^|[[:space:]])(--release|-r)([[:space:]]|$)`, segment)

profile_argument(segment) := matches[0][2] if {
	matches := regex.find_all_string_submatch_n(`(^|[[:space:]])--profile(?:=|[[:space:]]+)([A-Za-z0-9_-]+)`, segment, 1)
	count(matches) == 1
}

# -- target-specific assignments --------------------------------------------
#
# `makeutil` reports `test: export RUSTFLAGS := ...` as a rule whose
# prerequisites are the assignment's words, so the assignment is read back from
# them. No prerequisite of a real rule contains an assignment operator.

target_assignment(rule) := {"name": match[2], "value": match[4]} if {
	matches := regex.find_all_string_submatch_n(
		`^(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*(::=|:=|\?=|\+=|!=|=)[[:space:]]*(.*)$`,
		concat(" ", rule.prerequisites), 1,
	)
	count(matches) == 1
	match := matches[0]
}

assignment_rule(rule) if target_assignment(rule)

target_assignments(target, name) := [assignment.value |
	some rule in make_rules
	target in rule.targets
	assignment := target_assignment(rule)
	assignment.name == name
]

# -- the static closure from a gate target ----------------------------------

known_targets := {target |
	some rule in make_rules
	some target in rule.targets
}

static_make_targets(recipe) := {match[2] |
	some match in regex.find_all_string_submatch_n(`\$[({]MAKE[)}]((?:[[:space:]]+-[A-Za-z]+)*)[[:space:]]+([A-Za-z0-9_.-]+)`, recipe.text, -1)
}

target_edges[target] contains next if {
	some rule in make_rules
	not assignment_rule(rule)
	some target in rule.targets
	some next in rule.prerequisites
}

target_edges[target] contains next if {
	some rule in make_rules
	some target in rule.targets
	some recipe in rule.recipes
	some next in static_make_targets(recipe)
}

target_graph := {target: object.get(target_edges, target, []) | some target in known_targets}

closure(root) := {root} | graph.reachable(target_graph, {root})

dynamic_make(root) if {
	some rule in make_rules
	some target in rule.targets
	target in closure(root)
	some recipe in rule.recipes
	regex.match(`\$[({]MAKE[)}][[:space:]]+(\$[({]|-C)`, recipe.text)
}

makefile_unprovable_reason := "the Makefile includes other files, so its variables cannot be proven" if {
	count(makefile_report.includes) > 0
} else := "the Makefile parse was recovered from syntax errors, so its facts may be incomplete" if {
	makefile_report.parse.status != "complete"
}

# -- BD-007, Makefile half: every coverage recipe selects LLVM --------------

cranelift_routes contains "profile" if {
	some backend in dev_backends
	backend.backend == "cranelift"
}

cranelift_routes contains "rustflags" if {
	some backend in dev_backends_from_flags
	backend.backend == "cranelift"
}

cranelift_test_profile if {
	some backend in backends
	backend.scope == "profile"
	backend.profile == "test"
	backend.backend == "cranelift"
}

coverage_clause_applies if {
	applicable
	config_readable
	count(cranelift_routes) > 0
}

manifest_profile_backend(name) := input.cargo.parsed.profile[name]["codegen-backend"]

profile_selects_llvm(name) if {
	some backend in backends
	backend.scope == "profile"
	backend.profile == name
	backend.backend == "llvm"
}

profile_selects_llvm(name) if manifest_profile_backend(name) == "llvm"

release_profile_is_llvm if {
	not profile_selects_cranelift("release")
}

profile_selects_cranelift(name) if {
	some backend in backends
	backend.scope == "profile"
	backend.profile == name
	backend.backend == "cranelift"
}

# The rule's own target-specific assignment of a variable, the last one when
# there are several, as Make applies them in order.
target_value(rule, name) := expand(values[count(values) - 1]) if {
	values := [value |
		some target in rule.targets
		some value in target_assignments(target, name)
	]
	count(values) > 0
}

exported_value(name) := variable_value[name] if {
	some variable in assignments_of(name)
	variable.exported == true
}

# The value a recipe's command sees: its own prefix first, then the rule's
# target-specific assignment, then a Makefile-wide exported one.
make_env_value(rule, segment, name) := value if {
	values := assigned_values(segment, name)
	count(values) > 0
	value := values[count(values) - 1]
} else := value if {
	value := target_value(rule, name)
} else := value if {
	value := exported_value(name)
}

make_rustflags(rule, segment) := value if {
	value := make_env_value(rule, segment, "RUSTFLAGS")
} else := possible_text("$(RUSTFLAGS)") if {
	count(assignments_of("RUSTFLAGS")) > 0
}

make_profile_env_llvm(rule, segment) if {
	make_env_value(rule, segment, "CARGO_PROFILE_DEV_CODEGEN_BACKEND") == "llvm"
	not cranelift_test_profile
}

make_profile_env_llvm(rule, segment) if {
	make_env_value(rule, segment, "CARGO_PROFILE_DEV_CODEGEN_BACKEND") == "llvm"
	make_env_value(rule, segment, "CARGO_PROFILE_TEST_CODEGEN_BACKEND") == "llvm"
}

make_route_satisfied(rule, segment, "profile") if make_profile_env_llvm(rule, segment)

make_route_satisfied(rule, segment, route) if {
	route in cranelift_routes
	rustflags_backend(make_rustflags(rule, segment)) == "llvm"
}

make_route_satisfied(rule, segment, "profile") if profile_selects_llvm(profile_argument(segment))

make_route_satisfied(rule, segment, "profile") if {
	builds_release_profile(segment)
	release_profile_is_llvm
}

# An assigned `RUSTFLAGS` replaces the source that selected Cranelift, unless
# it names Cranelift again.
make_route_satisfied(rule, segment, "rustflags") if {
	flags := make_rustflags(rule, segment)
	not names_cranelift(flags)
}

make_selects_llvm(rule, segment) if {
	every route in cranelift_routes {
		make_route_satisfied(rule, segment, route)
	}
}

# A reference the policy could not substitute might hold the selection.
make_selection_unproven(segment) if {
	some name in references(segment)
	text := possible_text(sprintf("$(%s)", [name]))
	regex.match(`codegen-backend|CODEGEN_BACKEND|RUSTFLAGS|--profile`, text)
}

coverage_verdict(segment) := "indeterminate" if {
	make_selection_unproven(segment)
} else := "noncompliant"

coverage_segments contains [rule, recipe, segment] if {
	some rule in make_rules
	some recipe in rule.recipes
	some segment in recipe_segments(recipe)
	runs_llvm_cov(segment)
}

deny contains f if {
	coverage_clause_applies
	makefile_refused != null
	f := finding("BD-007", "indeterminate", "Makefile", sprintf("makeutil could not parse the Makefile, so its coverage recipes cannot be read: %s", [makefile_refused]))
}

deny contains f if {
	coverage_clause_applies
	makefile_report != null
	reason := makefile_unprovable_reason
	count(coverage_segments) > 0
	f := finding("BD-007", "indeterminate", "Makefile", reason)
}

deny contains f if {
	coverage_clause_applies
	makefile_report != null
	not makefile_unprovable_reason
	some [rule, recipe, segment] in coverage_segments
	not make_selects_llvm(rule, segment)
	f := located_finding(
		"BD-007", coverage_verdict(segment), "Makefile", recipe.location.start_line,
		sprintf(
			"the %s recipe runs cargo llvm-cov without selecting LLVM, but the development profile's default backend is Cranelift, which cannot instrument coverage",
			[concat(", ", rule.targets)],
		),
	)
}

# -- BD-008: a gate recipe that assigns RUSTFLAGS restates the fast flags ---

exempt_segment(segment) if runs_llvm_cov(segment)

exempt_segment(segment) if {
	regex.match(`(^|[[:space:]])build([[:space:]]|$)`, segment)
	builds_release_profile(segment)
}

gate_recipe_sites contains [target, rule, recipe, segment] if {
	some target in gate_targets
	target in known_targets
	some rule in make_rules
	some reached in rule.targets
	reached in closure(target)
	some recipe in rule.recipes
	some segment in recipe_segments(recipe)
	not exempt_segment(segment)
}

# Each RUSTFLAGS assignment a gate build takes, as [where, line, value].
gate_rustflags contains [sprintf("the %s recipe", [concat(", ", rule.targets)]), recipe.location.start_line, value] if {
	some [_, rule, recipe, segment] in gate_recipe_sites
	some value in assigned_values(segment, "RUSTFLAGS")
}

gate_rustflags contains [sprintf("the %s target-specific assignment", [target]), rule.location.start_line, value] if {
	some target in gate_targets
	some rule in make_rules
	some reached in rule.targets
	reached in closure(target)
	assignment := target_assignment(rule)
	assignment.name == "RUSTFLAGS"
	value := assignment.value
}

# Make exports a variable that came from the environment, and CI's toolchain
# setup puts `RUSTFLAGS` there, so a Makefile-wide assignment reaches every
# gate recipe whether or not it is marked `export`. Its assignments are read
# together, as an append (`+=`) combines them.
gate_rustflags contains ["the Makefile-wide assignment", line, "$(RUSTFLAGS)"] if {
	lines := [variable.location.start_line | some variable in assignments_of("RUSTFLAGS")]
	count(lines) > 0
	line := min(lines)
}

rustflags_clause_applies if {
	applicable
	makefile_report != null
	not makefile_unprovable_reason
}

deny contains f if {
	applicable
	makefile_refused != null
	f := finding("BD-008", "indeterminate", "Makefile", sprintf("makeutil could not parse the Makefile, so its RUSTFLAGS assignments cannot be read: %s", [makefile_refused]))
}

deny contains f if {
	applicable
	makefile_report != null
	reason := makefile_unprovable_reason
	f := finding("BD-008", "indeterminate", "Makefile", reason)
}

deny contains f if {
	rustflags_clause_applies
	some target in gate_targets
	dynamic_make(target)
	f := finding("BD-008", "indeterminate", "Makefile", sprintf("the %q target reaches a dynamic recursive Make invocation, so its recipes cannot be proven", [target]))
}

required_fast_flags contains threads_flag if threads_clause_applies

required_fast_flags contains linker_flag if linker_clause_applies

deny contains f if {
	rustflags_clause_applies
	some [where, line, value] in gate_rustflags
	some flag in required_fast_flags
	not carries_flag(possible_text(value), flag)
	f := located_finding(
		"BD-008", "noncompliant", "Makefile", line,
		sprintf(
			"%s sets RUSTFLAGS without %q; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it",
			[where, flag],
		),
	)
}
