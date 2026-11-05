# Private BD-008 suite boundary; compose only with this package's Make facts.
# Direct installer-managed Whitaker must not inherit development codegen flags.
package canon.lint_rules.rust_build_defaults

import rego.v1

# Recognize the executed binary, never an argument or a target named whitaker.
# What may precede it: `VAR=val` assignments, and `env` carrying assignments
# and `-u NAME` / `--unset NAME` / `--unset=NAME` removals. An `env` option this
# does not know keeps the segment unrecognized, so it stays a gate build.
runs_isolated_whitaker(segment) if regex.match(
	`^[[:space:]]*[-@+]*[[:space:]]*(env[[:space:]]+((-u|--unset)[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]+|--unset=[A-Za-z_][A-Za-z0-9_]*[[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=("[^"]*"|'[^']*'|[^[:space:]]*)[[:space:]]+)*|([A-Za-z_][A-Za-z0-9_]*=("[^"]*"|'[^']*'|[^[:space:]]*)[[:space:]]+)*)([^[:space:]"'()]*/)?whitaker(\.exe)?([[:space:]]|$)`,
	segment,
)

# What the route's own prefix says about the environment. The prefix is
# captured by the same pattern that recognizes the route, and read token by
# token, so a quoted value (`NOTE=" CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm"`)
# or an argument after the binary is never mistaken for an assignment or a
# removal.
route_prefix(segment) := matches[0][1] if {
	matches := regex.find_all_string_submatch_n(
		`^[[:space:]]*[-@+]*[[:space:]]*(env[[:space:]]+((-u|--unset)[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]+|--unset=[A-Za-z_][A-Za-z0-9_]*[[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=("[^"]*"|'[^']*'|[^[:space:]]*)[[:space:]]+)*|([A-Za-z_][A-Za-z0-9_]*=("[^"]*"|'[^']*'|[^[:space:]]*)[[:space:]]+)*)([^[:space:]"'()]*/)?whitaker(\.exe)?([[:space:]]|$)`,
		segment, 1,
	)
	count(matches) == 1
}

# A backslash can escape a space or a quote, which moves a token boundary in
# ways this reader does not follow, so a prefix holding one is read as saying
# nothing rather than guessed at.
prefix_is_plain(segment) if not contains(route_prefix(segment), "\\")

prefix_tokens(segment) := regex.find_all_string_submatch_n(
	`(?:^|[[:space:]])(?:([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|'[^']*'|[^[:space:]]*)|(?:-u|--unset)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)|--unset=([A-Za-z_][A-Za-z0-9_]*))`,
	route_prefix(segment), -1,
) if prefix_is_plain(segment)

default prefix_tokens(_) := []

# The values the route's prefix assigns to *name*, in order.
route_assigned_values(segment, name) := [trim(token[2], `"'`) |
	some token in prefix_tokens(segment)
	token[1] == name
]

# The names an `env` prefix removes from the route's environment.
unset_names(segment) := {name |
	some token in prefix_tokens(segment)
	some name in {token[3], token[4]}
	name != ""
}

exempt_segment(segment) if runs_isolated_whitaker(segment)

# The recipe's working directory is tracked in order, as a depth relative to the
# checkout: a `cd ..` is one level up (-1), a `cd` into a subdirectory one level
# down (+1), and the parent of the checkout named absolutely (GNU Make's
# `$(dir $(CURDIR))` or `$(CURDIR)/..`, bare or quoted) sets the depth to -1
# whatever came before. Any other `cd` (an absolute path, `.`, `../sibling`) is
# not understood and resets the depth to 0, so it never reads as leaving the
# checkout. A route runs outside the checkout only at a negative depth: a `cd
# crates && cd ..` is back where it started, and Cargo still finds the
# checkout's configuration from there.
cd_segment(segment) if regex.match(`^[[:space:]]*[-@+]*[[:space:]]*cd([[:space:]]|$)`, segment)

cd_up(segment) if regex.match(
	`^[[:space:]]*[-@+]*[[:space:]]*cd[[:space:]]+(\.\./?|"\.\./?"|'\.\./?')[[:space:]]*$`,
	segment,
)

cd_parent(segment) if regex.match(
	`^[[:space:]]*[-@+]*[[:space:]]*cd[[:space:]]+("?\$\(dir[[:space:]]+\$\(CURDIR\)\)/?"?|"?\$\(CURDIR\)/\.\./?"?)[[:space:]]*$`,
	segment,
)

cd_down(segment) if regex.match(
	`^[[:space:]]*[-@+]*[[:space:]]*cd[[:space:]]+"?[A-Za-z0-9_][^[:space:]"']*"?[[:space:]]*$`,
	segment,
)

# A `cd` that names where it goes instead of moving relative to where it was.
cd_resets(segment) if cd_parent(segment)

cd_resets(segment) if {
	cd_segment(segment)
	not cd_up(segment)
	not cd_down(segment)
}

cd_reset_depth(segment) := -1 if cd_parent(segment)

cd_reset_depth(segment) := 0 if {
	cd_resets(segment)
	not cd_parent(segment)
}

cd_move(segment) := -1 if cd_up(segment)

cd_move(segment) := 1 if cd_down(segment)

# The depth at `index`: the last reset before it, then every relative move
# after that reset.
cwd_depth(segments, index) := depth if {
	resets := {position | some position, earlier in segments; position < index; cd_resets(earlier)}
	count(resets) > 0
	anchor := max(resets)
	moves := [step | some position, earlier in segments; position > anchor; position < index; step := cd_move(earlier)]
	depth := cd_reset_depth(segments[anchor]) + sum(moves)
} else := depth if {
	moves := [step | some position, earlier in segments; position < index; step := cd_move(earlier)]
	depth := sum(moves)
}

# The route runs outside the checkout when the recipe line has left it before the
# segment runs. Cargo discovers `.cargo/config.toml` from the working directory
# upwards, so the checkout's configuration, and any backend profile it selects,
# is never read; `--manifest-path` still reads the manifest.
route_leaves_checkout(recipe, segment) if {
	segments := recipe_segments(recipe)
	some index, later in segments
	later == segment
	cwd_depth(segments, index) < 0
}

isolated_suite_sites contains [rule, recipe, segment] if {
	some target in gate_targets
	target in known_targets
	some rule in make_rules
	some reached in rule.targets
	reached in closure(target)
	some recipe in rule.recipes
	some segment in recipe_segments(recipe)
	runs_isolated_whitaker(segment)
}

# Parent target assignments reach prerequisites; unrelated targets do not.
# Multiple possible inherited values remain ambiguous rather than choosing an
# arbitrary graph path that GNU Make might evaluate in a different order.
inherited_suite_values(rule, name) := {expand(assignment.value) |
	some parent in make_rules
	assignment := target_assignment(parent)
	assignment.name == name
	some target in parent.targets
	some reached in rule.targets
	target != reached
	reached in closure(target)
}

suite_values(rule, segment, name) := {values[count(values) - 1]} if {
	values := route_assigned_values(segment, name)
	count(values) > 0
} else := {""} if {
	# `env -u NAME` removes the variable, which for RUSTFLAGS leaves the
	# configuration's sources unreplaced and carrying nothing of the caller's.
	name in unset_names(segment)
} else := {value} if {
	value := target_value(rule, name)
} else := values if {
	values := inherited_suite_values(rule, name)
	count(values) > 0
} else := {value} if {
	value := variable_value[name]
} else := {possible_text(sprintf("$(%s)", [name]))} if {
	count(assignments_of(name)) > 0
} else := set()

suite_development_flag(value) if {
	some token in flag_tokens(replace(value, "\u001f", " "))
	startswith(token, "-Zthreads=")
}

suite_development_flag(value) if {
	some token in flag_tokens(replace(value, "\u001f", " "))
	endswith(token, "-fuse-ld=mold")
}

suite_development_flag(value) if {
	some token in flag_tokens(replace(value, "\u001f", " "))
	token == "-Zcodegen-backend=cranelift"
}

suite_value_unknown(value) if contains(value, "$")

# These explicit overrides can displace a safe RUSTFLAGS assignment. Inspect
# them too; a missing override is different from a missing isolation boundary.
suite_contamination(rule, segment) if {
	some name in ["RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS"]
	some value in suite_values(rule, segment, name)
	suite_development_flag(value)
}

suite_contamination(rule, segment) if {
	"cranelift" in suite_values(rule, segment, "CARGO_PROFILE_DEV_CODEGEN_BACKEND")
}

suite_contamination(rule, segment) if {
	"cranelift" in suite_values(rule, segment, "CARGO_PROFILE_TEST_CODEGEN_BACKEND")
}

suite_isolation_unproved(rule, _, segment) if {
	count(suite_values(rule, segment, "RUSTFLAGS")) != 1
}

# RUSTFLAGS does not displace a separate profile or package backend. The facts
# do not prove how the suite's internal Cargo call selects/overrides these, so
# preserve an explicit measurement gap instead of assuming an LLVM route.
separate_manifest_backend if {
	some backend in manifest_backends
	backend.backend != "llvm"
}

suite_isolation_unproved(_, _, _) if manifest_backend_error != ""

suite_isolation_unproved(_, _, _) if separate_manifest_backend

# A route that leaves the checkout never discovers its Cargo configuration, so
# a backend selected there does not reach the suite.
suite_isolation_unproved(rule, recipe, segment) if {
	some backend in backends
	backend.scope in {"profile", "package-override"}
	backend.backend == "cranelift"
	not route_leaves_checkout(recipe, segment)
	not profile_backend_overridden(rule, segment, backend)
}

# An explicit `CARGO_PROFILE_<P>_CODEGEN_BACKEND=llvm` on the route displaces the
# configuration's Cranelift for that profile, which is proof of isolation. Only
# a profile table can be displaced this way: a package override has no such
# variable, so it stays unproved.
profile_backend_overridden(rule, segment, backend) if {
	backend.scope == "profile"
	name := sprintf("CARGO_PROFILE_%s_CODEGEN_BACKEND", [upper(replace(backend.profile, "-", "_"))])
	# Only the route's own prefix proves the override reaches Whitaker: a Make
	# variable that is not exported never enters its environment.
	route_assigned_values(segment, name) == ["llvm"]
	suite_values(rule, segment, name) == {"llvm"}
}

suite_isolation_unproved(rule, _, segment) if {
	some name in ["RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_PROFILE_DEV_CODEGEN_BACKEND", "CARGO_PROFILE_TEST_CODEGEN_BACKEND"]
	values := suite_values(rule, segment, name)
	count(values) > 1
}

suite_isolation_unproved(rule, _, segment) if {
	some name in ["RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_PROFILE_DEV_CODEGEN_BACKEND", "CARGO_PROFILE_TEST_CODEGEN_BACKEND"]
	some value in suite_values(rule, segment, name)
	suite_value_unknown(value)
}

deny contains f if {
	rustflags_clause_applies
	some [rule, recipe, segment] in isolated_suite_sites
	suite_contamination(rule, segment)
	f := located_finding("BD-008", "noncompliant", "Makefile", recipe.location.start_line,
		"the direct Whitaker route carries development frontend, Cranelift or mold flags; isolate its installer-managed toolchain")
}

deny contains f if {
	rustflags_clause_applies
	some [rule, recipe, segment] in isolated_suite_sites
	not suite_contamination(rule, segment)
	suite_isolation_unproved(rule, recipe, segment)
	f := located_finding("BD-008", "indeterminate", "Makefile", recipe.location.start_line,
		"the direct Whitaker route lacks complete statically known flag/profile isolation; its selected backends or unresolved overrides cannot be proven isolated")
}
