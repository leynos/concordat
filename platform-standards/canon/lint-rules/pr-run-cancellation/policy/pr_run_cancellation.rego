# RC-001, RC-002: pull-request workflows cancel superseded runs.
#
# A pull request that receives several pushes starts a run per push against
# the same ref. Without concurrency cancellation the earlier runs keep the
# runners busy on a commit nobody will merge, and the newest run queues behind
# them. RC-001 requires a cancelling concurrency block: at workflow level, or
# on every job that is not disabled with `if: false`. RC-002 requires every
# cancelling block to name the pull request in its group, so two pull requests
# do not cancel each other's runs.
#
# A workflow this policy cannot decode, whose trigger it cannot classify, or
# whose concurrency shape it cannot read is indeterminate rather than a pass.
# An exemption suppresses RC-001 and RC-002 only: evidence the reader could
# not establish stays reported.
#
# The ticket this package implements calls these clauses PR-001 and PR-002.
# The canon already uses the PR prefix for the priority checks, so the
# findings here are RC-001 and RC-002; the package README records the mapping.
package canon.lint_rules.pr_run_cancellation

import rego.v1

ENVELOPE_KIND := "policy-input/pr-run-cancellation"

API_EVENTS := {"pull_request", "pull_request_target"}

PR_NUMBER_MARKERS := {"github.event.pull_request.number", "github.head_ref"}

# A quoted string may contain a brace, so the body pattern consumes quoted
# strings whole rather than stopping at the first `}`.
EXPRESSION_PATTERN := `\$\{\{((?:[^'}]*|'[^']*')*)\}\}`

# GitHub resolves a static index such as `github['run_id']` to the same value
# as the dotted form. Normalise it before quoted literals are removed, or the
# reference looks like the empty index `github[]`.
STATIC_INDEX_PATTERN := `\['([A-Za-z_][A-Za-z0-9_]*)'\]`

# `fromJSON` is deliberately left out of every set below. It parses its
# argument, so a marker inside it may not survive: a branch name that is a JSON
# literal changes value, and `1` and `1e0` both parse to the number 1, which
# puts two pull requests in one group. Whether the parsed value still names the
# pull request cannot be read from the text, so an occurrence inside it is
# reported indeterminate rather than as a pass.

# `format(template, ...)` renders an argument only where the template names its
# replacement index, as in `format('ci-{0}', github.head_ref)`. A template that
# omits the index, such as `format('ci', github.head_ref)`, renders a constant
# for every pull request, so the marker does not carry the identity there.
TEMPLATE_FUNCTIONS := {"format"}

# `join(array, separator)` is position-sensitive in the same way. A marker in
# the array is joined into the result, but a marker in the separator renders
# only between elements: with one element it never appears, and with several it
# appears as punctuation rather than as the pull request's identity. Which
# applies cannot be settled from the text, so such an occurrence is reported
# indeterminate.
FIRST_ARGUMENT_FUNCTIONS := {"join"}

NON_IDENTITY_FUNCTIONS := {
	"contains",
	"startswith",
	"endswith",
	"hashfiles",
	"success",
	"failure",
	"always",
	"cancelled",
}

KNOWN_FUNCTIONS := TEMPLATE_FUNCTIONS | NON_IDENTITY_FUNCTIONS | FIRST_ARGUMENT_FUNCTIONS

# Values that separate runs of the same pull request. A group built from one of
# them does not match a run that ought to be superseded. `github.sha` is the
# merge commit of a `pull_request` run, which changes with every push.
# `github.run_attempt` separates a re-run from the next push's first attempt,
# so an older run that was re-run is left holding a slot nobody will use.
RUN_VARYING_MARKERS := {
	"github.run_id",
	"github.run_number",
	"github.run_attempt",
	"github.sha",
	"github.event.pull_request.head.sha",
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

default repository_exemptions := []

repository_exemptions := data.parameters.repository_exemptions

default repository_name := null

repository_name := name if {
	is_object(input.repository)
	name := object.get(input.repository, "name", null)
}

repository_identity_usable if {
	is_string(repository_name)
	trim_space(repository_name) != ""
}

envelope_ok if {
	input.schema_version == 1
	input.kind == ENVELOPE_KIND
	is_object(input.repository)
	is_array(input.workflows)
}

# --------------------------------------------------------------------------
# Workflow shape
# --------------------------------------------------------------------------

workflow_path(workflow) := path if {
	is_object(workflow)
	path := object.get(workflow, "path", "")
	is_string(path)
	path != ""
}

workflow_path(workflow) := ".github/workflows" if not is_object(workflow)

workflow_path(workflow) := ".github/workflows" if {
	is_object(workflow)
	not is_string(object.get(workflow, "path", null))
}

workflow_path(workflow) := ".github/workflows" if {
	is_object(workflow)
	object.get(workflow, "path", "") == ""
}

workflow_parsed(workflow) := parsed if {
	is_object(workflow)
	parsed := object.get(workflow, "parsed", null)
	is_object(parsed)
}

# GitHub spells the trigger key `on`. A YAML 1.1 loader resolves that
# unquoted key to the boolean `True`, which JSON renders as the key "true",
# so a reader that knows only the string key finds no triggers at all and
# every trigger-derived clause below passes over an empty set. Read both.
workflow_trigger_key(workflow) := "on" if {
	parsed := workflow_parsed(workflow)
	"on" in object.keys(parsed)
}

workflow_trigger_key(workflow) := "true" if {
	parsed := workflow_parsed(workflow)
	not "on" in object.keys(parsed)
	"true" in object.keys(parsed)
}

workflow_on(workflow) := on if {
	parsed := workflow_parsed(workflow)
	key := workflow_trigger_key(workflow)
	on := parsed[key]
}

valid_trigger(workflow) if {
	on := workflow_on(workflow)
	is_string(on)
	trim_space(on) != ""
}

valid_trigger(workflow) if {
	on := workflow_on(workflow)
	is_array(on)
	count(on) > 0
	every event in on {
		is_string(event)
	}
}

# Only the event names matter to this rule, so a mapping is classifiable
# whenever its keys are. The per-event settings are the workflow's business
# and may take any shape GitHub accepts.
valid_trigger(workflow) if {
	on := workflow_on(workflow)
	is_object(on)
	count(on) > 0
	every event in object.keys(on) {
		is_string(event)
	}
}

has_pr_trigger(workflow) if {
	on := workflow_on(workflow)
	is_string(on)
	on in API_EVENTS
}

has_pr_trigger(workflow) if {
	on := workflow_on(workflow)
	is_array(on)
	some event in API_EVENTS
	event in on
}

has_pr_trigger(workflow) if {
	on := workflow_on(workflow)
	is_object(on)
	some event in API_EVENTS
	event in object.keys(on)
}

unsupported_workflow(workflow) if not is_object(workflow)

unsupported_workflow(workflow) if {
	is_object(workflow)
	object.get(workflow, "error", null) != null
}

unsupported_workflow(workflow) if {
	is_object(workflow)
	object.get(workflow, "error", null) == null
	not is_object(object.get(workflow, "parsed", null))
}

unsupported_workflow(workflow) if {
	workflow_parsed(workflow)
	not workflow_trigger_key(workflow)
}

unsupported_workflow(workflow) if {
	workflow_parsed(workflow)
	workflow_trigger_key(workflow)
	not valid_trigger(workflow)
}

# A workflow the policy can classify as pull-request-triggered, with a
# decodable document, is the only one whose jobs and concurrency it judges.
evaluable_workflow(workflow) if {
	not unsupported_workflow(workflow)
	has_pr_trigger(workflow)
}

workflow_jobs(workflow) := jobs if {
	parsed := workflow_parsed(workflow)
	jobs := object.get(parsed, "jobs", null)
	is_object(jobs)
}

valid_jobs(workflow) if {
	jobs := workflow_jobs(workflow)
	count(jobs) > 0
	every name in object.keys(jobs) {
		is_object(jobs[name])
	}
}

# --------------------------------------------------------------------------
# Concurrency shape and cancellation
# --------------------------------------------------------------------------

workflow_has_concurrency(workflow) if {
	parsed := workflow_parsed(workflow)
	"concurrency" in object.keys(parsed)
}

workflow_concurrency(workflow) := value if {
	parsed := workflow_parsed(workflow)
	"concurrency" in object.keys(parsed)
	value := parsed.concurrency
}

job_has_concurrency(job) if {
	is_object(job)
	"concurrency" in object.keys(job)
}

job_concurrency(job) := value if {
	job_has_concurrency(job)
	value := job.concurrency
}

valid_cancel_in_progress(value) if is_boolean(value)

valid_cancel_in_progress(value) if is_string(value)

cancel_in_progress_shape_ok(value) if {
	not "cancel-in-progress" in object.keys(value)
}

cancel_in_progress_shape_ok(value) if {
	"cancel-in-progress" in object.keys(value)
	valid_cancel_in_progress(value["cancel-in-progress"])
}

valid_concurrency(value) if {
	is_string(value)
	trim_space(value) != ""
}

valid_concurrency(value) if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	trim_space(group) != ""
	cancel_in_progress_shape_ok(value)
}

is_expression(value) if {
	is_string(value)
	trimmed := trim_space(value)
	startswith(trimmed, "${{")
	endswith(trimmed, "}}")
}

# The evaluated body of every `${{ ... }}` expression in a string. Text outside
# an expression is not evaluated and does not contribute. A quoted string is a
# literal, so its text is not a reference and cannot name the pull request; it
# survives only as the `{N}` placeholders a `format` template renders, wrapped
# in quotes so a literal template can be told from a computed one.
expression_bodies(text) := [body |
	some match in regex.find_all_string_submatch_n(EXPRESSION_PATTERN, text, -1)
	normalised := regex.replace(match[1], STATIC_INDEX_PATTERN, ".$1")
	body := concat("", [segment |
		some index, part in split(normalised, "'")
		segment := segment_view(index, part)
	])
]

segment_view(index, part) := part if index % 2 == 0

segment_view(index, part) := sprintf("\"%s\"", [literal_placeholders(part)]) if index % 2 == 1

# Only the replacement placeholders survive, so literal text cannot look like an
# evaluated reference. `{{` and `}}` are escaped braces, so they must go before
# the remaining `{N}` sequences are read.
literal_placeholders(part) := concat("", [match[0] |
	some match in regex.find_all_string_submatch_n(
		`\{[0-9]+(?::[^}]*)?\}`,
		regex.replace(regex.replace(part, `\{\{`, ""), `\}\}`, ""),
		-1,
	)
])

# A `||` inside a call argument is that argument's own fallback, not a branch
# of the whole expression, so only an operator outside every parenthesis
# separates branches. Counting parentheses is enough here because the body has
# already lost its quoted literals, and `{0}` placeholders carry none.
parenthesis_delta(text) := count(split(text, "(")) - count(split(text, ")"))

depth_before(tokens, index) := sum([delta |
	some position in numbers.range(0, count(tokens) - 1)
	position < index
	delta := parenthesis_delta(tokens[position])
])

# `split(text, "")` yields the characters, which lets the rule locate the
# parenthesis and comma positions a call's argument list is built from.
characters(text) := split(text, "")

paren_step(char) := 1 if char == "("

paren_step(char) := 0 - 1 if char == ")"

paren_step(char) := 0 if not char in {"(", ")"}

# The parenthesis depth before character `index`.
depth_at(text, 0) := 0

depth_at(text, index) := sum([delta |
	some position in numbers.range(0, index - 1)
	delta := paren_step(characters(text)[position])
]) if index > 0

branch_starts(tokens) := sort(array.concat([0], [index |
	some index in numbers.range(0, count(tokens) - 1)
	index > 0
	depth_before(tokens, index) == 0
]))

next_start(starts, index, fallback) := starts[index + 1] if index + 1 < count(starts)

next_start(starts, index, fallback) := fallback if index + 1 >= count(starts)

# GitHub evaluates a `||` chain from the left and takes the first truthy
# branch, so every branch is a candidate value for the group.
expression_branches(body) := [trim_space(branch) |
	tokens := split(body, "||")
	starts := branch_starts(tokens)
	some index, start in starts
	end := next_start(starts, index, count(tokens))
	branch := concat("||", array.slice(tokens, start, end))
]

# The value GitHub selects when it is truthy is the text before the first
# top-level `||`. A `||` inside a call argument belongs to that argument, and
# the arguments after it are still part of the value, so cutting there would
# drop a later argument that does carry the pull request.
top_level_separators(text) := [position |
	chars := characters(text)
	some position in numbers.range(0, count(chars) - 1)
	position + 1 < count(chars)
	chars[position] == "|"
	chars[position + 1] == "|"
	depth_at(text, position) == 0
]

selected_head(branch) := trim_space(branch) if count(top_level_separators(branch)) == 0

selected_head(branch) := trim_space(substring(branch, 0, stop)) if {
	separators := top_level_separators(branch)
	count(separators) > 0
	stop := sort(separators)[0]
}

# Every call whose argument list is still open at the end of the text. A call
# is open when the depth after its `(` never returns to the level it opened
# from, so a call that already closed before the marker is not part of the
# expression the marker sits in. A grouping parenthesis has no name and
# contributes no link, because it passes the value through untouched.
call_is_open(text, position) if {
	chars := characters(text)
	every index in numbers.range(position + 1, count(chars)) {
		depth_at(text, index) > depth_at(text, position)
	}
}

open_call_positions(text) := [position |
	chars := characters(text)
	some position in numbers.range(0, count(chars) - 1)
	chars[position] == "("
	call_is_open(text, position)
]

call_name_before(text, position) := name if {
	prefix := trim_right(substring(text, 0, position), " \t\r\n")
	matches := regex.find_all_string_submatch_n(`([A-Za-z_][A-Za-z0-9_.]*)$`, prefix, -1)
	count(matches) > 0
	name := lower(matches[count(matches) - 1][1])
}

# Commas at the argument-list depth of the call opened at `position`. A comma
# inside a nested call is deeper, so it does not separate this call's arguments.
argument_separators(text, position) := [index |
	chars := characters(text)
	some index in numbers.range(0, count(chars) - 1)
	index > position
	chars[index] == ","
	depth_at(text, index) == depth_at(text, position) + 1
]

call_argument_index(text, position) := count(argument_separators(text, position))

call_first_argument(text, position) := trim_space(substring(text, position + 1, end - position - 1)) if {
	ends := argument_separators(text, position)
	count(ends) > 0
	end := sort(ends)[0]
}

call_first_argument(text, position) := trim_space(substring(text, position + 1, count(characters(text)) - position - 1)) if {
	count(argument_separators(text, position)) == 0
}

# Each enclosing call with the argument position the marker occupies, which is
# what `format` and `join` need to know whether the value survives.
call_links(text) := [link |
	some position in open_call_positions(text)
	link := {
		"name": call_name_before(text, position),
		"argument_index": call_argument_index(text, position),
		"first_argument": call_first_argument(text, position),
	}
]

literal_argument(text) if startswith(text, "\"")

# The replacement indices a literal template renders. `{{` and `}}` are escaped
# braces, so they are removed before the remaining `{N}` sequences are read.
format_placeholders(template) := {number |
	some match in regex.find_all_string_submatch_n(
		`\{([0-9]+)(?::[^}]*)?\}`,
		regex.replace(regex.replace(template, `\{\{`, ""), `\}\}`, ""),
		-1,
	)
	number := to_number(match[1])
}

# The conditions a `format` argument needs before its placeholders can decide
# anything: the call renders, its template is readable, and the marker is a
# replacement argument rather than the template itself.
format_argument_settled(link) if {
	link.name in TEMPLATE_FUNCTIONS
	literal_argument(link.first_argument)
	link.argument_index > 0
}

format_link_carries(link) if {
	format_argument_settled(link)
	(link.argument_index - 1) in format_placeholders(link.first_argument)
}

format_link_discards(link) if {
	format_argument_settled(link)
	not (link.argument_index - 1) in format_placeholders(link.first_argument)
}

link_carries_value(link) if format_link_carries(link)

link_carries_value(link) if {
	link.name in FIRST_ARGUMENT_FUNCTIONS
	link.argument_index == 0
}

link_discards_value(link) if link.name in NON_IDENTITY_FUNCTIONS

link_discards_value(link) if format_link_discards(link)

link_unsettled(link) if not link.name in KNOWN_FUNCTIONS

link_unsettled(link) if {
	link.name in FIRST_ARGUMENT_FUNCTIONS
	link.argument_index != 0
}

link_unsettled(link) if {
	link.name in TEMPLATE_FUNCTIONS
	not literal_argument(link.first_argument)
}

chain_carries_value(links) if every link in links { link_carries_value(link) }

chain_discards_value(links) if {
	some link in links
	link_discards_value(link)
}

# A known call that returns a boolean or a constant settles the occurrence
# wherever it sits in the chain, so an unknown sibling cannot unsettle it.
chain_unsettled(links) if {
	not chain_discards_value(links)
	some link in links
	link_unsettled(link)
}

# A marker names the pull request when at least one occurrence is evaluated as
# its value. An occurrence used as a comparison operand, as in
# `github.event.pull_request.number > 0` or
# `0 < github.event.pull_request.number`, renders as a boolean instead, so that
# occurrence carries no identity; another occurrence in the same expression
# still can, as in `format('{0}-{1}', github.head_ref, github.head_ref == 'x')`.
# Parentheses around the operand are transparent, so `(github.head_ref) == 'x'`
# is a comparison too. Negation and conjunction replace the value the same way:
# `!github.head_ref` renders a boolean, and `github.head_ref && 'ci'` renders
# the right operand, so in both the marker is not the group's value. The marker
# must be a complete reference: `github.head_ref_name` is a different property,
# and GitHub resolves an unknown property to an empty string. An occurrence
# handed to a function that returns something else, such as
# `contains(github.head_ref, 'release')`, carries no identity either, and an
# outer call that discards the value defeats an inner one that carries it, as
# in `contains(format('{0}', github.head_ref), 'release')`. `format` carries
# the occurrence only where its literal template renders that argument's
# replacement index, and `join` only from its array argument. An occurrence
# handed to a call the rule does not recognise cannot be classified.
#
# The text before an occurrence is the whole prefix, not just the text since
# the previous occurrence: in `contains(github.head_ref, github.head_ref)` the
# second occurrence is just as much an argument of the call as the first. Only
# the value the group takes counts, so an occurrence behind a `||` in its own
# argument is not the selected value: `format('{0}', 'ci' || github.head_ref)`
# renders `ci` for every pull request. An occurrence in a later argument is
# unaffected by a fallback that sits in an earlier one.
occurrence_prefix(parts, marker, index) := concat(marker, array.slice(parts, 0, index))

mentions_pull_request(text) if {
	some marker in PR_NUMBER_MARKERS
	parts := split(lower(text), marker)
	some index, following in parts
	index > 0
	marker_occurrence_is_identity(occurrence_prefix(parts, marker, index), following)
}

marker_occurrence_is_identity(before, after) if {
	marker_occurrence_context_holds(before, after)
	links := call_links(before)
	chain_carries_value(links)
}

marker_occurrence_is_unclassifiable(before, after) if {
	marker_occurrence_context_holds(before, after)
	links := call_links(before)
	chain_unsettled(links)
}

marker_occurrence_context_holds(before, after) if {
	not continues_property(after)
	not extends_property(before)
	not follows_comparison(after)
	not precedes_comparison(before)
	not precedes_negation(before)
	not follows_conjunction(after)
	marker_is_selected(before)
}

# A `||` selects only the text after it, and only within its own argument: a
# comma at the separator's depth starts a later argument, and a closing
# parenthesis that returns the depth below it leaves the enclosing call, so
# neither is reached by that fallback.
comma_between_at_depth(text, after, depth) if {
	chars := characters(text)
	commas := [index |
		some index in numbers.range(0, count(chars) - 1)
		index > after
		chars[index] == ","
		depth_at(text, index) == depth
	]
	count(commas) > 0
}

depth_drops_below(text, after, depth) if {
	chars := characters(text)
	some index in numbers.range(0, count(chars))
	index > after + 1
	depth_at(text, index) < depth
}

separator_reaches_marker(text, after, depth) if {
	not comma_between_at_depth(text, after, depth)
	not depth_drops_below(text, after, depth)
}

marker_is_selected(text) if {
	chars := characters(text)
	separators := [position |
		some position in numbers.range(0, count(chars) - 1)
		position + 1 < count(chars)
		chars[position] == "|"
		chars[position + 1] == "|"
	]
	every position in separators {
		not separator_reaches_marker(text, position, depth_at(text, position))
	}
}

continues_property(text) if regex.match(`^[A-Za-z0-9_.]`, text)

extends_property(text) if regex.match(`[A-Za-z0-9_.]$`, text)

follows_comparison(text) if {
	trimmed := trim_left(text, " \t\r\n)")
	some operator in {"==", "!=", ">=", "<=", ">", "<"}
	startswith(trimmed, operator)
}

precedes_comparison(text) if {
	trimmed := trim_right(text, " \t\r\n(")
	some operator in {"==", "!=", ">=", "<=", ">", "<"}
	endswith(trimmed, operator)
}

# `!` renders a boolean, so a negated occurrence is not the group's value.
precedes_negation(text) if {
	trimmed := trim_right(text, " \t\r\n(")
	endswith(trimmed, "!")
}

# `&&` yields its right operand, so an occurrence that is its left operand is
# not the group's value.
follows_conjunction(text) if {
	trimmed := trim_left(text, " \t\r\n)")
	startswith(trimmed, "&&")
}

mentions_run_varying_value(text) if {
	some marker in RUN_VARYING_MARKERS
	contains(lower(text), marker)
}

# A trimmed literal-false expression is the one expression that provably does
# not cancel. Anything else written as an expression may evaluate true on the
# run it matters for, so it is treated as cancelling.
literal_false_expression(value) if {
	is_expression(value)
	trimmed := trim_space(value)
	inner := trim_space(trim_suffix(trim_prefix(trimmed, "${{"), "}}"))
	lower(inner) == "false"
}

cancels_in_progress(value) if value == true

cancels_in_progress(value) if {
	is_string(value)
	lower(trim_space(value)) == "true"
}

cancels_in_progress(value) if {
	is_expression(value)
	not literal_false_expression(value)
}

# A branch is stable when it carries no value that changes between pushes to
# the same pull request.
branch_is_stable(branch) if {
	not mentions_run_varying_value(branch)
}

# A branch that keeps a nested fallback carries a value whose selection the
# text alone cannot settle: the varying value may sit in the surrounding
# argument list, or only in a fallback a pull request never selects.
branch_is_unclassifiable(branch) if {
	contains(branch, "||")
	mentions_run_varying_value(branch)
}

branch_stability(branch) := "stable" if branch_is_stable(branch)

branch_stability(branch) := "unclassifiable" if branch_is_unclassifiable(branch)

branch_stability(branch) := "unstable" if {
	not branch_is_stable(branch)
	not branch_is_unclassifiable(branch)
}

# The shortcut that ignores fallbacks is only sound when the selected head is
# the marker itself: `github.head_ref` is non-empty for every pull request, so
# the fallback never renders. A head that merely contains the marker, such as
# `github.event_name == 'push' && github.head_ref`, can be falsy for a pull
# request, and then the fallback is what the group carries.
head_is_marker_reference(text) if lower(trim_space(text)) in PR_NUMBER_MARKERS

# A single expression has no fallback to weigh, so the branch decides alone.
expression_stability(body) := outcome if {
	branches := expression_branches(body)
	count(branches) == 1
	outcome := branch_stability(branches[0])
}

# In the documented `github.head_ref || github.run_id` shape the pull-request
# branch is selected, and the run-id fallback only applies to pushes, which
# this rule does not audit.
expression_stability(body) := outcome if {
	branches := expression_branches(body)
	count(branches) > 1
	head := selected_head(branches[0])
	head_is_marker_reference(head)
	outcome := branch_stability(branches[0])
}

# A head that carries the pull request inside a larger expression does not
# prove the fallbacks go unused, so every branch is examined. When one of them
# varies between runs the group cannot be settled from the text; it is reported
# as unclassifiable rather than called a defect or a pass.
expression_stability(body) := "stable" if {
	branches := expression_branches(body)
	count(branches) > 1
	head := selected_head(branches[0])
	not head_is_marker_reference(head)
	mentions_pull_request(head)
	every branch in branches {
		branch_is_stable(branch)
	}
}

expression_stability(body) := "unclassifiable" if {
	branches := expression_branches(body)
	count(branches) > 1
	head := selected_head(branches[0])
	not head_is_marker_reference(head)
	mentions_pull_request(head)
	some branch in branches
	not branch_is_stable(branch)
}

# With no pull request in the head, any branch may be selected, so every one
# must be stable.
expression_stability(body) := "stable" if {
	branches := expression_branches(body)
	count(branches) > 1
	head := selected_head(branches[0])
	not head_is_marker_reference(head)
	not mentions_pull_request(head)
	every branch in branches {
		branch_is_stable(branch)
	}
}

expression_stability(body) := "unclassifiable" if {
	branches := expression_branches(body)
	count(branches) > 1
	head := selected_head(branches[0])
	not head_is_marker_reference(head)
	not mentions_pull_request(head)
	some branch in branches
	branch_is_unclassifiable(branch)
}

expression_stability(body) := "unstable" if {
	branches := expression_branches(body)
	count(branches) > 1
	head := selected_head(branches[0])
	not head_is_marker_reference(head)
	not mentions_pull_request(head)
	some branch in branches
	not branch_is_stable(branch)
	every branch in branches {
		not branch_is_unclassifiable(branch)
	}
}

# A group whose expressions all keep their value between pushes to one pull
# request is stable: the next push's run joins it and cancels the superseded
# one. A group built from a value that changes instead never matches the
# previous push's run, so it does not cancel in any useful sense, whatever
# `cancel-in-progress` says. A plain string is a constant group: it never
# varies, so RC-002 judges whether it names the pull request.
group_stability(value) := "stable" if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	every body in expression_bodies(group) {
		expression_stability(body) == "stable"
	}
}

group_stability(value) := "unclassifiable" if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	some body in expression_bodies(group)
	expression_stability(body) == "unclassifiable"
}

group_stability(value) := "unstable" if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	every body in expression_bodies(group) {
		expression_stability(body) != "unclassifiable"
	}
	some body in expression_bodies(group)
	expression_stability(body) == "unstable"
}

concurrency_cancels(value) if {
	is_object(value)
	"cancel-in-progress" in object.keys(value)
	cancels_in_progress(value["cancel-in-progress"])
	group_stability(value) == "stable"
}

workflow_cancels(workflow) if {
	workflow_has_concurrency(workflow)
	concurrency_cancels(workflow_concurrency(workflow))
}

job_cancels(job) if {
	job_has_concurrency(job)
	concurrency_cancels(job_concurrency(job))
}

# The marker only names the pull request when GitHub evaluates it, and only in
# the branch GitHub selects. A group that spells the marker as literal text,
# such as `ci-github.head_ref`, is a constant, so every pull request shares it
# and cancels the others. Match the marker only inside the unquoted part of a
# `${{ ... }}` expression; inside quotes it is a string literal, as in
# `${{ 'github.head_ref' }}`. A marker used as a comparison operand is also
# excluded, because the comparison renders as a boolean rather than as the pull
# request's identity. Finally, the selected head is the one a pull request
# carries, so a marker that appears only in a constant-first fallback, as in
# `${{ 'ci' || github.head_ref }}` or in a fallback nested inside a call
# argument, does not name anything.
group_names_pull_request(value) if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	some body in expression_bodies(group)
	branches := expression_branches(body)
	mentions_pull_request(selected_head(branches[0]))
}

# A group whose only marker references feed a call the rule does not classify
# may or may not carry the pull request, so it is reported as indeterminate
# rather than as a pass or a defect.
group_identity_unknown(value) if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	not group_names_pull_request(value)
	some body in expression_bodies(group)
	branches := expression_branches(body)
	head := selected_head(branches[0])
	some marker in PR_NUMBER_MARKERS
	parts := split(lower(head), marker)
	some index, following in parts
	index > 0
	marker_occurrence_is_unclassifiable(occurrence_prefix(parts, marker, index), following)
}

# A missing `if` key is undefined rather than null, and an undefined
# comparison fails, so this tests only the literal false.
job_disabled(job) if {
	is_object(job)
	job["if"] == false
}

job_disabled(job) if {
	is_object(job)
	condition := object.get(job, "if", null)
	is_string(condition)
	lower(trim_space(condition)) == "false"
}

job_disabled(job) if {
	is_object(job)
	literal_false_expression(object.get(job, "if", null))
}

invalid_workflow_concurrency(workflow) if {
	workflow_has_concurrency(workflow)
	not valid_concurrency(workflow_concurrency(workflow))
}

invalid_job_concurrency(job) if {
	job_has_concurrency(job)
	not valid_concurrency(job_concurrency(job))
}

# RC-001 and RC-002 are only stated when every concurrency block in the
# workflow can be classified. Reporting noncompliance from a shape the policy
# could not read would turn an unknown into a defect.
concurrency_shapes_readable(workflow) if {
	valid_jobs(workflow)
	not invalid_workflow_concurrency(workflow)
	jobs := workflow_jobs(workflow)
	every name in object.keys(jobs) {
		not invalid_job_concurrency(jobs[name])
	}
}

workflow_group_unclassifiable(workflow) if {
	workflow_has_concurrency(workflow)
	group_stability(workflow_concurrency(workflow)) == "unclassifiable"
}

unclassifiable_job_groups(workflow) := {name |
	some name in object.keys(workflow_jobs(workflow))
	group_stability(job_concurrency(workflow_jobs(workflow)[name])) == "unclassifiable"
} if {
	concurrency_shapes_readable(workflow)
}

# A group whose value cannot be read leaves the workflow's substantive
# judgement waiting, exactly as an unreadable concurrency shape does.
concurrency_unclassifiable(workflow) if workflow_group_unclassifiable(workflow)

concurrency_unclassifiable(workflow) if {
	count(unclassifiable_job_groups(workflow)) > 0
}

concurrency_evaluable(workflow) if {
	concurrency_shapes_readable(workflow)
	not concurrency_unclassifiable(workflow)
}

uncovered_jobs(workflow) := {name |
	some name in object.keys(workflow_jobs(workflow))
	job := workflow_jobs(workflow)[name]
	not job_disabled(job)
	not workflow_cancels(workflow)
	not job_cancels(job)
} if {
	concurrency_evaluable(workflow)
}

# --------------------------------------------------------------------------
# Exemptions
# --------------------------------------------------------------------------

# An exemption is a reviewed record: it names the repository, the exact
# workflow, why it may leave superseded runs running, and what event sends it
# back for review. A blank field asks nobody to revisit it, so it is not a
# record and suppresses nothing.
is_exempt(path) if {
	repository_identity_usable
	some exemption in exemptions
	is_object(exemption)
	repository := object.get(exemption, "repository", null)
	is_string(repository)
	trim_space(repository) != ""
	repository == repository_name
	exempted_path := object.get(exemption, "path", null)
	is_string(exempted_path)
	exempted_path == path
	rationale := object.get(exemption, "rationale", null)
	is_string(rationale)
	trim_space(rationale) != ""
	review_trigger := object.get(exemption, "review_trigger", null)
	is_string(review_trigger)
	trim_space(review_trigger) != ""
}

is_repository_exempt if {
	repository_identity_usable
	some exemption in repository_exemptions
	is_object(exemption)
	repository := object.get(exemption, "repository", null)
	is_string(repository)
	trim_space(repository) != ""
	repository == repository_name
	rationale := object.get(exemption, "rationale", null)
	is_string(rationale)
	trim_space(rationale) != ""
	review_trigger := object.get(exemption, "review_trigger", null)
	is_string(review_trigger)
	trim_space(review_trigger) != ""
}

rc_audited(workflow) if {
	not is_repository_exempt
	not is_exempt(workflow_path(workflow))
}

# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

deny contains finding("EN-001", "error", "indeterminate", ".github/workflows", 0, msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/pr-run-cancellation envelope at schema version 1"
}

deny contains finding("EN-001", severity, "indeterminate", ".github/workflows", 0, msg) if {
	msg := "a workflow entry is not an object"
	envelope_ok
	some workflow in input.workflows
	not is_object(workflow)
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	is_object(workflow)
	error := object.get(workflow, "error", null)
	error != null
	msg := sprintf("the workflow could not be read: %v", [error])
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	msg := "the workflow document is not a mapping"
	envelope_ok
	some workflow in input.workflows
	is_object(workflow)
	object.get(workflow, "error", null) == null
	not is_object(object.get(workflow, "parsed", null))
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	msg := "the workflow declares no triggers"
	envelope_ok
	some workflow in input.workflows
	workflow_parsed(workflow)
	not workflow_trigger_key(workflow)
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	msg := "the workflow's triggers have a shape this rule cannot classify"
	envelope_ok
	some workflow in input.workflows
	workflow_parsed(workflow)
	workflow_trigger_key(workflow)
	not valid_trigger(workflow)
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	msg := "the workflow does not declare a non-empty jobs mapping of job objects, so its pull-request jobs cannot be evaluated"
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	not valid_jobs(workflow)
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	msg := "the workflow's concurrency block is not a non-empty string or an object with a string group and a boolean or string cancel-in-progress"
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	invalid_workflow_concurrency(workflow)
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	jobs := workflow_jobs(workflow)
	invalid := {name |
		some name in object.keys(jobs)
		invalid_job_concurrency(jobs[name])
	}
	count(invalid) > 0
	msg := sprintf("job(s) %s have a concurrency block that is not a non-empty string or an object with a string group and a boolean or string cancel-in-progress", [concat(", ", sort(invalid))])
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	msg := "the workflow's concurrency group nests a `||` fallback inside a call argument beside a value that changes between runs, so whether it cancels a superseded run cannot be classified"
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_shapes_readable(workflow)
	workflow_group_unclassifiable(workflow)
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_shapes_readable(workflow)
	names := unclassifiable_job_groups(workflow)
	count(names) > 0
	msg := sprintf("job(s) %s have a concurrency group that nests a `||` fallback inside a call argument beside a value that changes between runs, so whether it cancels a superseded run cannot be classified", [concat(", ", sort(names))])
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	msg := "the workflow's concurrency group passes a pull-request marker to a call this rule cannot classify, so whether the group names the pull request cannot be classified"
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	workflow_has_concurrency(workflow)
	group_identity_unknown(workflow_concurrency(workflow))
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	jobs := workflow_jobs(workflow)
	unknown := {name |
		some name in object.keys(jobs)
		group_identity_unknown(job_concurrency(jobs[name]))
	}
	count(unknown) > 0
	msg := sprintf("job(s) %s have a concurrency group that passes a pull-request marker to a call this rule cannot classify, so whether the group names the pull request cannot be classified", [concat(", ", sort(unknown))])
}

deny contains finding("RC-001", severity, "noncompliant", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	rc_audited(workflow)
	names := uncovered_jobs(workflow)
	count(names) > 0
	msg := sprintf(
		"workflow %s runs on pull requests but neither it nor job(s) %s cancel superseded runs. Add a cancelling concurrency block at workflow level, or to every job that is not disabled with `if: false`",
		[workflow_path(workflow), concat(", ", sort(names))],
	)
}

deny contains finding("RC-002", severity, "noncompliant", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	rc_audited(workflow)
	workflow_cancels(workflow)
	not group_names_pull_request(workflow_concurrency(workflow))
	not group_identity_unknown(workflow_concurrency(workflow))
	msg := sprintf(
		"workflow %s cancels superseded runs, but its concurrency group does not name the pull request. Include github.event.pull_request.number or github.head_ref so runs for different pull requests do not cancel one another",
		[workflow_path(workflow)],
	)
}

# GitHub applies workflow-level and job-level concurrency as separate controls,
# so a workflow group that names the pull request does not stop a job group
# from cancelling another pull request's run. Every cancelling job block is
# therefore checked on its own.
deny contains finding("RC-002", severity, "noncompliant", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	rc_audited(workflow)
	jobs := workflow_jobs(workflow)
	some name in object.keys(jobs)
	job := jobs[name]
	job_cancels(job)
	not group_names_pull_request(job_concurrency(job))
	not group_identity_unknown(job_concurrency(job))
	msg := sprintf(
		"job %s of %s cancels superseded runs, but its concurrency group does not name the pull request. Include github.event.pull_request.number or github.head_ref so runs for different pull requests do not cancel one another",
		[name, workflow_path(workflow)],
	)
}