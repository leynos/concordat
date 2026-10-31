# Private BD-008 suite boundary; compose only with this package's Make facts.
# Direct installer-managed Whitaker must not inherit development codegen flags.
package canon.lint_rules.rust_build_defaults

import rego.v1

# Recognize the executed binary, never an argument or a target named whitaker.
runs_isolated_whitaker(segment) if regex.match(
	`^[[:space:]]*[-@+]*[[:space:]]*(env[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*=("[^"]*"|'[^']*'|[^[:space:]]*)[[:space:]]+)*([^[:space:]"'()]*/)?whitaker(\.exe)?([[:space:]]|$)`,
	segment,
)

exempt_segment(segment) if runs_isolated_whitaker(segment)

# A `cd` to the parent of the checkout, in the spellings a recipe uses: `..`,
# GNU Make's `$(dir $(CURDIR))` and `$(CURDIR)/..`, bare or quoted. A `cd` into
# a subdirectory does not count: Cargo still finds the checkout's configuration
# from there.
leaves_checkout(segment) if regex.match(
	`^[[:space:]]*[-@+]*[[:space:]]*cd[[:space:]]+(\.\./?|"\.\./?"|'\.\./?'|"?\$\(dir[[:space:]]+\$\(CURDIR\)\)/?"?|"?\$\(CURDIR\)/\.\./?"?)[[:space:]]*$`,
	segment,
)

# The route runs outside the checkout when a segment before it in the same
# recipe line leaves the checkout. Cargo discovers `.cargo/config.toml` from the
# working directory upwards, so the checkout's configuration, and any backend
# profile it selects, is never read; `--manifest-path` still reads the manifest.
route_leaves_checkout(recipe, segment) if {
	segments := recipe_segments(recipe)
	some index, later in segments
	later == segment
	some earlier_index, earlier in segments
	earlier_index < index
	leaves_checkout(earlier)
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
	values := assigned_values(segment, name)
	count(values) > 0
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
separate_config_backend if {
	some backend in backends
	backend.scope in {"profile", "package-override"}
	backend.backend == "cranelift"
}

separate_manifest_backend if {
	some backend in manifest_backends
	backend.backend != "llvm"
}

suite_isolation_unproved(_, _, _) if manifest_backend_error != ""

suite_isolation_unproved(_, _, _) if separate_manifest_backend

# A route that leaves the checkout never discovers its Cargo configuration, so
# a backend selected there does not reach the suite.
suite_isolation_unproved(_, recipe, segment) if {
	separate_config_backend
	not route_leaves_checkout(recipe, segment)
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
