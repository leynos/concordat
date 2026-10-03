# rust-build-defaults, workflow half of BD-007 and BD-009.
#
# A workflow step replaces the default the same ways a Makefile recipe does:
# an assigned `RUSTFLAGS` replaces every `rustflags` source, and a coverage or
# dev-profile release build cannot use the Cranelift default. The toolchain
# action is the step that most often assigns it: shared-actions `setup-rust`
# exports its `rustflags` input, `-D warnings` by default, to every later step.
# Facts come from each workflow decoded as YAML, never from a textual search.
package canon.lint_rules.rust_build_defaults

import rego.v1

default gate_cargo_subcommands := ["build", "check", "clippy", "doc", "nextest", "test"]

gate_cargo_subcommands := data.parameters.gate_cargo_subcommands

default setup_rust_rustflags := "-D warnings"

setup_rust_rustflags := data.parameters.setup_rust_rustflags

# -- the workflow facts -----------------------------------------------------

workflow_files := object.get(input, "workflows", [])

decoded_workflows := [workflow |
	some workflow in workflow_files
	workflow.error == null
	is_object(workflow.parsed)
]

as_text(value) := value if is_string(value)

as_text(value) := sprintf("%v", [value]) if not is_string(value)

env_of(holder) := env if {
	env := object.get(holder, "env", {})
	is_object(env)
} else := {}

job_steps(job) := steps if {
	steps := object.get(job, "steps", [])
	is_array(steps)
} else := []

workflow_jobs contains [workflow, job_id, job] if {
	some workflow in decoded_workflows
	jobs := object.get(workflow.parsed, "jobs", {})
	is_object(jobs)
	some job_id, job in jobs
	is_object(job)
}

step_label(step, index) := step.name if {
	is_string(step.name)
} else := sprintf("step %d", [index + 1])

uses_shared_action(step, name) if {
	is_string(step.uses)
	regex.match(sprintf(`^leynos/shared-actions/\.github/actions/%s@`, [name]), step.uses)
}

setup_rust_step(step) if uses_shared_action(step, "setup-rust")

setup_rust_step(step) if {
	is_string(step.uses)
	startswith(step.uses, "actions-rust-lang/setup-rust-toolchain@")
}

setup_input(step, name, fallback) := as_text(object.get(object.get(step, "with", {}), name, fallback))

# -- run steps as command segments ------------------------------------------

run_segments(step) := [segment |
	is_string(step.run)
	some line in split(regex.replace(step.run, `\\\n[[:space:]]*`, " "), "\n")
	some segment in command_segments(line)
	not comment_or_print(segment)
]

# Each cargo invocation in a segment as [toolchain override, subcommand].
cargo_invocations(segment) := [[match[3], match[4]] |
	some match in regex.find_all_string_submatch_n(
		`(^|[[:space:]/])cargo([[:space:]]+\+([^[:space:]]+))?[[:space:]]+([a-z][a-z0-9-]*)`,
		segment, -1,
	)
]

cross_builds(segment) if regex.match(`(^|[[:space:]/])cross([[:space:]]+\+[^[:space:]]+)?[[:space:]]+build([[:space:]]|$)`, segment)

step_segments contains [workflow, job_id, job, index, step, segment] if {
	some [workflow, job_id, job] in workflow_jobs
	some index, step in job_steps(job)
	is_object(step)
	some segment in run_segments(step)
}

# -- the environment a step's command sees ----------------------------------

step_env_value(workflow, job, step, segment, name) := value if {
	values := assigned_values(segment, name)
	count(values) > 0
	value := values[count(values) - 1]
} else := as_text(env_of(step)[name]) if {
	env_of(step)[name] != null
} else := as_text(env_of(job)[name]) if {
	env_of(job)[name] != null
} else := as_text(env_of(workflow.parsed)[name]) if {
	env_of(workflow.parsed)[name] != null
}

# The toolchain action exports its `rustflags` input unless it is empty or
# `RUSTFLAGS` is already set, and a later step inherits the export.
setup_rust_export(job, index) := value if {
	setups := [step |
		some position, step in job_steps(job)
		position < index
		setup_rust_step(step)
	]
	count(setups) > 0
	value := setup_input(setups[count(setups) - 1], "rustflags", setup_rust_rustflags)
	value != ""
}

# The effective `RUSTFLAGS` as [value, where it came from].
step_rustflags(workflow, job, index, step, segment) := [value, "the step's RUSTFLAGS"] if {
	value := step_env_value(workflow, job, step, segment, "RUSTFLAGS")
} else := [value, "the RUSTFLAGS the toolchain action exports"] if {
	value := setup_rust_export(job, index)
}

unknowable(value) if contains(value, "${{")

# -- BD-007, workflow half: coverage and dev-profile release builds ---------

runs_workflow_llvm_cov(segment) if {
	some [_, subcommand] in cargo_invocations(segment)
	subcommand == "llvm-cov"
}

workflow_triggers(workflow) := object.get(workflow.parsed, "on", null)

release_triggered(workflow) if workflow_triggers(workflow) == "release"

release_triggered(workflow) if {
	triggers := workflow_triggers(workflow)
	is_array(triggers)
	"release" in triggers
}

release_triggered(workflow) if {
	triggers := workflow_triggers(workflow)
	is_object(triggers)
	"release" in object.keys(triggers)
}

release_triggered(workflow) if {
	push := workflow_triggers(workflow).push
	is_object(push)
	object.get(push, "tags", null) != null
}

builds_dev_profile(segment) if {
	not builds_release_profile(segment)
	not profile_argument(segment)
}

dev_release_build(segment) if {
	some [_, subcommand] in cargo_invocations(segment)
	subcommand in {"build", "zigbuild"}
	builds_dev_profile(segment)
}

dev_release_build(segment) if {
	cross_builds(segment)
	builds_dev_profile(segment)
}

backend_paths contains [workflow, job_id, job, index, step, segment, "runs cargo llvm-cov"] if {
	some [workflow, job_id, job, index, step, segment] in step_segments
	runs_workflow_llvm_cov(segment)
}

backend_paths contains [workflow, job_id, job, index, step, segment, "builds a release through the development profile"] if {
	some [workflow, job_id, job, index, step, segment] in step_segments
	release_triggered(workflow)
	dev_release_build(segment)
}

workflow_profile_llvm(workflow, job, step, segment) if {
	step_env_value(workflow, job, step, segment, "CARGO_PROFILE_DEV_CODEGEN_BACKEND") == "llvm"
	not cranelift_test_profile
}

workflow_profile_llvm(workflow, job, step, segment) if {
	step_env_value(workflow, job, step, segment, "CARGO_PROFILE_DEV_CODEGEN_BACKEND") == "llvm"
	step_env_value(workflow, job, step, segment, "CARGO_PROFILE_TEST_CODEGEN_BACKEND") == "llvm"
}

workflow_route_satisfied(workflow, job, index, step, segment, "profile") if workflow_profile_llvm(workflow, job, step, segment)

workflow_route_satisfied(workflow, job, index, step, segment, route) if {
	route in cranelift_routes
	[flags, _] := step_rustflags(workflow, job, index, step, segment)
	rustflags_backend(flags) == "llvm"
}

workflow_route_satisfied(workflow, job, index, step, segment, "profile") if profile_selects_llvm(profile_argument(segment))

workflow_route_satisfied(workflow, job, index, step, segment, "profile") if {
	builds_release_profile(segment)
	release_profile_is_llvm
}

workflow_route_satisfied(workflow, job, index, step, segment, "rustflags") if {
	[flags, _] := step_rustflags(workflow, job, index, step, segment)
	not names_cranelift(flags)
}

workflow_selects_llvm(workflow, job, index, step, segment) if {
	every route in cranelift_routes {
		workflow_route_satisfied(workflow, job, index, step, segment, route)
	}
}

workflow_selection_unknowable(workflow, job, index, step, segment) if {
	some name in {"CARGO_PROFILE_DEV_CODEGEN_BACKEND", "CARGO_PROFILE_TEST_CODEGEN_BACKEND"}
	unknowable(step_env_value(workflow, job, step, segment, name))
}

workflow_selection_unknowable(workflow, job, index, step, segment) if {
	[flags, _] := step_rustflags(workflow, job, index, step, segment)
	unknowable(flags)
}

backend_verdict(workflow, job, index, step, segment) := "indeterminate" if {
	workflow_selection_unknowable(workflow, job, index, step, segment)
} else := "noncompliant"

deny contains f if {
	coverage_clause_applies
	some [workflow, job_id, job, index, step, segment, what] in backend_paths
	not workflow_selects_llvm(workflow, job, index, step, segment)
	f := finding(
		"BD-007", backend_verdict(workflow, job, index, step, segment), workflow.path,
		sprintf(
			"job %q (%s) %s without selecting LLVM, but the development profile's default backend is Cranelift",
			[job_id, step_label(step, index), what],
		),
	)
}

# -- an undecodable workflow cannot be judged --------------------------------
#
# The decoded set silently drops a file whose YAML did not load, so without
# this a broken workflow would read as one with nothing to report. The finding
# names the file and a fixed category, never the parser's own message, which
# can quote workflow content.

undecoded_category(error) := "not UTF-8 text" if {
	startswith(error, "not UTF-8 text")
} else := "invalid YAML" if {
	startswith(error, "invalid YAML")
} else := "not a mapping" if {
	error == "workflow document is not a mapping"
} else := "unreadable"

deny contains f if {
	applicable
	some workflow in workflow_files
	workflow.error != null
	category := undecoded_category(workflow.error)
	f := finding(
		"BD-009", "indeterminate", workflow.path,
		sprintf("the workflow could not be decoded (%s), so BD-007 and BD-009 cannot judge its cargo steps", [category]),
	)
}

# -- BD-009: a direct cargo gate step keeps the fast flags -------------------

gate_cargo_segment(segment) := toolchain if {
	some [toolchain, subcommand] in cargo_invocations(segment)
	subcommand in gate_cargo_subcommands
	not exempt_segment(segment)
	not dev_release_build_exempt(segment)
}

dev_release_build_exempt(segment) if {
	some [_, subcommand] in cargo_invocations(segment)
	subcommand in {"build", "zigbuild"}
	not builds_dev_profile(segment)
}

runner_labels(job) := [job["runs-on"]] if is_string(job["runs-on"])

runner_labels(job) := job["runs-on"] if is_array(job["runs-on"])

runner_labels(job) := [labels] if {
	is_object(job["runs-on"])
	labels := object.get(job["runs-on"], "labels", [])
	is_string(labels)
}

runner_labels(job) := labels if {
	is_object(job["runs-on"])
	labels := object.get(job["runs-on"], "labels", [])
	is_array(labels)
}

# Only a literal Linux label proves the linker flag belongs; an expression
# (`${{ matrix.os }}`) proves nothing, and the linker is then not demanded.
runs_on_linux(job) if {
	some label in runner_labels(job)
	is_string(label)
	regex.match(`^(ubuntu|ubicloud)|(^|-)linux($|-)`, lower(label))
}

# A toolchain other than nightly rejects `-Zthreads`, so it is not demanded
# of a step that overrides the pin with one.
stable_override(job, index, toolchain) if {
	toolchain != ""
	not startswith(toolchain, "nightly")
}

stable_override(job, index, toolchain) if {
	toolchain == ""
	setups := [step |
		some position, step in job_steps(job)
		position < index
		setup_rust_step(step)
	]
	count(setups) > 0
	requested := setup_input(setups[count(setups) - 1], "toolchain", "")
	requested != ""
	not startswith(requested, "nightly")
}

workflow_required_flags(job, index, toolchain) := {flag |
	some flag in required_fast_flags
	flag == threads_flag
	not stable_override(job, index, toolchain)
} | {flag |
	some flag in required_fast_flags
	flag == linker_flag
	runs_on_linux(job)
}

deny contains f if {
	applicable
	some [workflow, job_id, job, index, step, segment] in step_segments
	gate_cargo_segment(segment)
	[flags, source] := step_rustflags(workflow, job, index, step, segment)
	unknowable(flags)
	f := finding(
		"BD-009", "indeterminate", workflow.path,
		sprintf("job %q (%s) runs cargo with %s set from an expression, so its flags cannot be read", [job_id, step_label(step, index), source]),
	)
}

deny contains f if {
	applicable
	some [workflow, job_id, job, index, step, segment] in step_segments
	toolchain := gate_cargo_segment(segment)
	[flags, source] := step_rustflags(workflow, job, index, step, segment)
	not unknowable(flags)
	some flag in workflow_required_flags(job, index, toolchain)
	not carries_flag(flags, flag)
	f := finding(
		"BD-009", "noncompliant", workflow.path,
		sprintf(
			"job %q (%s) runs cargo with %s (%q), which lacks %q and replaces every rustflags source in the Cargo configuration",
			[job_id, step_label(step, index), source, flags, flag],
		),
	)
}
