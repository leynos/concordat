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

# Literals the rule can prove falsy as a `&&` guard. Quoted strings have already
# lost their content by the time a guard is read, so an empty string cannot be
# told from a non-empty one and is not treated as provably falsy here.
FALSY_LITERALS := {"false", "null", "0"}

# References GitHub resolves to a non-empty string, so a `&&` guard that is one
# of them selects its right operand on every pull-request run.
TRUTHY_GUARDS := {
	"github.event.pull_request.number",
	"github.event_name",
	"github.head_ref",
	"github.ref",
	"github.repository",
	"github.run_attempt",
	"github.run_id",
	"github.run_number",
	"github.sha",
	"github.workflow",
}

# A quoted string may contain a brace, so the body pattern consumes quoted
# strings whole rather than stopping at the first `}`.
EXPRESSION_PATTERN := `\$\{\{((?:[^'}]*|'[^']*')*)\}\}`

# GitHub resolves a static index such as `github['run_id']` to the same value
# as the dotted form. Normalise it before quoted literals are removed, or the
# reference looks like the empty index `github[]`.
STATIC_INDEX_PATTERN := `\['([A-Za-z_][A-Za-z0-9_]*)'\]`

# Whether a `&&` branch is selectable can turn on a `github.event_name`
# comparison, as in `github.event_name == 'push' && github.head_ref`. The
# literal operand of such a comparison is kept, encoded as `@name@`, because
# every other literal is erased and the comparison would otherwise read
# `github.event_name == ""`. Only a lowercase identifier is encoded, so the
# token can never carry a dot, a quote or a `||` separator and cannot be
# mistaken for a reference or a fallback.
EVENT_NAME_RIGHT_PATTERN := `(github\.event_name\s*(?:==|!=)\s*)'([a-z_]+)'`

EVENT_NAME_LEFT_PATTERN := `'([a-z_]+)'(\s*(?:==|!=)\s*github\.event_name)`

# The guard itself, with the encoded event name, and the value it protects.
EVENT_GUARD_RIGHT_PATTERN := `^\(?\s*github\.event_name\s*(==|!=)\s*@([a-z_]+)@\s*\)?\s*&&\s*`

EVENT_GUARD_LEFT_PATTERN := `^\(?\s*@([a-z_]+)@\s*(==|!=)\s*github\.event_name\s*\)?\s*&&\s*`

# A bare event test: the same comparison with nothing after it. This is what a
# `cancel-in-progress` guard looks like when it is the whole expression.
EVENT_TEST_RIGHT_PATTERN := `^\(*\s*github\.event_name\s*(==|!=)\s*@([a-z_]+)@\s*\)*$`

EVENT_TEST_LEFT_PATTERN := `^\(*\s*@([a-z_]+)@\s*(==|!=)\s*github\.event_name\s*\)*$`

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

# The pull-request events the workflow declares. A group is judged against each
# of them, because a guard that holds for one event may select a constant for
# another that the same workflow also triggers on.
pr_events(workflow) := {event} if {
	on := workflow_on(workflow)
	is_string(on)
	api_event(on)
	event := on
}

pr_events(workflow) := events if {
	on := workflow_on(workflow)
	is_array(on)
	events := {event | some event in on; api_event(event)}
	count(events) > 0
}

pr_events(workflow) := events if {
	on := workflow_on(workflow)
	is_object(on)
	events := {event | some event in object.keys(on); api_event(event)}
	count(events) > 0
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
# in quotes so a literal template can be told from a computed one. A literal
# with no placeholders keeps a marker of its own so it stays distinct from the
# empty literal, which is falsy. The one exception is the event name a
# `github.event_name` comparison guards on, kept as `@name@` so that guard can
# be evaluated.
expression_bodies(text) := [body |
	some match in regex.find_all_string_submatch_n(EXPRESSION_PATTERN, text, -1)
	normalised := regex.replace(match[1], STATIC_INDEX_PATTERN, ".$1")
	scoped := regex.replace(
		regex.replace(normalised, EVENT_NAME_RIGHT_PATTERN, "$1@$2@"),
		EVENT_NAME_LEFT_PATTERN,
		"@$1@$2",
	)
	body := concat("", [segment |
		some index, part in split(scoped, "'")
		segment := segment_view(index, part)
	])
]

segment_view(index, part) := part if index % 2 == 0

segment_view(index, part) := sprintf("\"%s\"", [literal_placeholders(part)]) if {
	index % 2 == 1
	literal_placeholders(part) != ""
}

# A span with no placeholders still has to stay distinct from the empty
# literal: `'ci' || github.head_ref` renders `ci` for every pull request, while
# `'' || github.head_ref` always reaches the fallback. Erasing both to `""`
# would make the rule read a constant branch as falsy and accept a group that
# shares one value across every pull request.
ERASED_LITERAL := "*"

segment_view(index, part) := sprintf("\"%s\"", [ERASED_LITERAL]) if {
	index % 2 == 1
	literal_placeholders(part) == ""
	part != ""
}

segment_view(index, part) := `""` if {
	index % 2 == 1
	part == ""
}

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

# The named calls still open at `offset`, ignoring grouping parentheses, which
# have no name and pass the value through untouched.
enclosing_call_positions(head, offset) := named if {
	positions := open_call_positions(substring(head, 0, offset))
	named := [candidate |
		some candidate in positions
		call_name_before(head, candidate)
	]
}

# The `)` that closes the call opened at `position`: the first one whose depth
# returns to the level the `(` opened from, so a nested call's `)` is skipped.
call_close_positions(text, position) := [index |
	chars := characters(text)
	some index in numbers.range(position, count(chars) - 1)
	chars[index] == ")"
	depth_at(text, index) == depth_at(text, position) + 1
]

call_close(text, position) := end if {
	ends := call_close_positions(text, position)
	count(ends) > 0
	end := sort(ends)[0]
}

# An operator applied to any call that carries the occurrence replaces that
# call's result, so the group no longer contains the pull request:
# `format('{0}-{1}', github.head_ref, 'ci') == 'release'` renders a boolean.
# The checks next to the marker cannot see it, because the marker's own
# argument ends at a comma before the operator. Every enclosing call is
# examined, so an operator on an inner call is not hidden by an outer call that
# merely renders it, as in
# `format('{0}', format('{0}-{1}', github.head_ref, 'ci') == 'release')`.
enclosing_call_defeats(head, offset) if {
	some position in enclosing_call_positions(head, offset)
	call_result_is_replaced(head, position)
}

call_result_is_replaced(head, position) if {
	name := call_name_before(head, position)
	trimmed := trim_right(substring(head, 0, position), " \t\r\n")
	start := count(trimmed) - count(name)
	end := call_close(head, position)
	operator_defeats(
		substring(head, 0, start),
		substring(head, end + 1, count(characters(head)) - end - 1),
	)
}

operator_defeats(before_call, after_call) if precedes_negation(before_call)

operator_defeats(before_call, after_call) if precedes_comparison(before_call)

operator_defeats(before_call, after_call) if follows_comparison(after_call)

operator_defeats(before_call, after_call) if follows_conjunction(after_call)

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

# A template is literal only when the whole argument is one quoted span. The
# body reader quotes each literal it keeps, so a compound such as
# `'{0}' && '{1}'` becomes `"{0}" && "{1}"`: a leading quote alone would accept
# it, and GitHub selects the second literal, not the template the rule read.
LITERAL_ARGUMENT_PATTERN := `^"([^"]|"")*"$`

literal_argument(text) if regex.match(LITERAL_ARGUMENT_PATTERN, text)

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
	head := lower(text)
	some marker in PR_NUMBER_MARKERS
	parts := split(head, marker)
	some index, following in parts
	index > 0
	marker_occurrence_is_identity(
		head,
		occurrence_prefix(parts, marker, index),
		following,
	)
}

marker_occurrence_is_identity(head, before, after) if {
	marker_occurrence_context_holds(head, before, after)
	argument_conjunction_holds(before)
	links := call_links(before)
	chain_carries_value(links)
}

marker_occurrence_is_unclassifiable(head, before, after) if {
	marker_occurrence_context_holds(head, before, after)
	argument_conjunction_unsettled(before)
}

marker_occurrence_is_unclassifiable(head, before, after) if {
	marker_occurrence_context_holds(head, before, after)
	links := call_links(before)
	chain_unsettled(links)
}

# The conditions an occurrence needs before GitHub can evaluate it as part of
# the group's value: a complete property reference that is not compared,
# negated or a `&&` left operand, and no enclosing call that replaces its
# result. Selection among `||` branches is judged separately, per branch.
occurrence_survives_value(head, before, after) if {
	not continues_property(after)
	not extends_property(before)
	not follows_comparison(after)
	not precedes_comparison(before)
	not precedes_negation(before)
	not follows_conjunction(after)
	not enclosing_call_defeats(head, count(before))
}

marker_occurrence_context_holds(head, before, after) if {
	occurrence_survives_value(head, before, after)
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
	not separator_branch_provably_falsy(text, after, depth)
}

# A `||` whose own left branch is provably falsy does not cut the marker off:
# GitHub moves on to the next branch, so `false || github.head_ref` renders the
# marker while `'ci' || github.head_ref` renders `ci` for every pull request.
separator_branch_provably_falsy(text, after, depth) if {
	start := separator_branch_start(text, after, depth)
	branch := substring(text, start, after - start)
	branch_provably_falsy(branch)
}

separator_branch_start(text, position, depth) := start if {
	starts := separator_branch_starts(text, position, depth)
	count(starts) > 0
	start := max(starts)
}

separator_branch_start(text, position, depth) := 0 if {
	count(separator_branch_starts(text, position, depth)) == 0
}

# Where the branch immediately left of a separator begins: after the previous
# separator, comma or opening parenthesis at that separator's own depth, so a
# nested call's argument list is not read as part of the branch.
separator_branch_starts(text, position, depth) := starts if {
	chars := characters(text)
	boundaries := {start |
		some index in numbers.range(0, position - 1)
		chars[index] in {"(", ","}
		depth_at(text, index + 1) == depth
		start := index + 1
	}
	separators := {start |
		some index in numbers.range(0, position - 1)
		index + 1 < position
		chars[index] == "|"
		chars[index + 1] == "|"
		depth_at(text, index) == depth
		start := index + 2
	}
	starts := boundaries | separators
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

# A `&&` chain inside a call argument is not reached by the branch-level
# conjunction checks, because those split only at the top level. The guards of
# every argument the occurrence sits in decide whether the marker is the value
# GitHub takes: `format('{0}', false && github.head_ref)` renders `false`, and
# so does the outer argument of
# `format('{0}', false && format('{0}', github.head_ref))`. Top-level guards are
# left to the branch rules, which judge them against each declared event.
argument_guards(before) := guards if {
	chars := characters(before)
	depth := depth_at(before, count(chars))
	depth > 0
	guards := [guard |
		some level in numbers.range(1, depth)
		some guard in argument_guards_at(before, level)
	]
	count(guards) > 0
}

argument_guards_at(before, depth) := guards if {
	segments := conjunction_segments(argument_prefix_at(before, depth))
	count(segments) > 1
	guards := array.slice(segments, 0, count(segments) - 1)
}

argument_has_guards(before) if argument_guards(before)

argument_conjunction_holds(before) if not argument_has_guards(before)

argument_conjunction_holds(before) if {
	guards := argument_guards(before)
	every guard in guards {
		guard_proven_truthy(guard)
	}
}

argument_conjunction_unsettled(before) if {
	some guard in argument_guards(before)
	not guard_proven_truthy(guard)
	not lower(guard) in FALSY_LITERALS
}

# The text of one argument the occurrence sits in, from that argument's opening
# boundary to the occurrence, so that a `&&` in a nested call or in another
# argument is not read as a guard on this one.
argument_prefix_at(before, depth) := substring(before, start, count(before) - start) if {
	start := argument_start_at(before, depth)
}

argument_start_at(before, depth) := start if {
	boundaries := argument_boundaries_at(before, depth)
	count(boundaries) > 0
	start := max(boundaries)
}

argument_start_at(before, depth) := 0 if count(argument_boundaries_at(before, depth)) == 0

argument_boundaries_at(before, depth) := boundaries if {
	chars := characters(before)
	boundaries := {start |
		some index in numbers.range(0, count(chars) - 1)
		chars[index] in {"(", ","}
		depth_at(before, index + 1) == depth
		start := index + 1
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

# A run-varying reference only makes the group vary when GitHub evaluates it as
# part of the group's value. The argument context is the one the marker check
# uses, so a value the text never renders does not count:
# `format('ci-{0}', github.head_ref, github.run_id)` builds a stable group, while
# the same value rendered by `{1}` does make it vary. A call the rule cannot
# classify is counted, so an unreadable argument stays conservative.
mentions_run_varying_value(text) if {
	head := lower(text)
	some marker in RUN_VARYING_MARKERS
	parts := split(head, marker)
	some index, following in parts
	index > 0
	run_varying_occurrence_counts(
		head,
		occurrence_prefix(parts, marker, index),
		following,
	)
}

# The value also has to be the one GitHub evaluates: a `||` earlier in the same
# argument can turn the occurrence into an unreached fallback, and a `&&` guard
# in an enclosing argument decides whether the value is rendered at all.
# `format('{0}-{1}', github.head_ref, false && github.run_id)` renders
# `ci-false` for every pull request, so the run id never reaches the group.
run_varying_occurrence_counts(head, before, after) if {
	occurrence_survives_value(head, before, after)
	run_value_reachable(before)
	not argument_conjunction_provably_falsy(before)
	links := call_links(before)
	not chain_discards_value(links)
}

argument_conjunction_provably_falsy(before) if {
	some guard in argument_guards(before)
	branch_provably_falsy(guard)
}

# A `||` whose own left branch always renders hides everything after it, so a
# run value there never reaches the group. An earlier branch the rule cannot
# prove truthy may be falsy, which leaves the run value reachable and keeps the
# conservative reading. A separator in another argument does not reach this one.
run_value_reachable(before) if {
	chars := characters(before)
	separators := [position |
		some position in numbers.range(0, count(chars) - 1)
		position + 1 < count(chars)
		chars[position] == "|"
		chars[position + 1] == "|"
	]
	every position in separators {
		not separator_branch_always_truthy(before, position)
	}
}

separator_branch_always_truthy(text, after) if {
	depth := depth_at(text, after)
	not comma_between_at_depth(text, after, depth)
	not depth_drops_below(text, after, depth)
	start := separator_branch_start(text, after, depth)
	branch := substring(text, start, after - start)
	branch_always_truthy(branch)
}

# A branch whose value GitHub always renders, so a `||` after it never falls
# through: a bare marker, a non-empty literal, or a `&&` chain proven truthy.
branch_always_truthy(branch) if branch_bare_marker(branch)

branch_always_truthy(branch) if branch_constant_literal(branch)

branch_always_truthy(branch) if branch_provably_truthy(branch)

# A trimmed literal-false expression is the one expression that provably does
# not cancel. Anything else written as an expression may evaluate true on the
# run it matters for, so it is treated as cancelling.
literal_false_expression(value) if {
	is_expression(value)
	trimmed := trim_space(value)
	inner := trim_space(trim_suffix(trim_prefix(trimmed, "${{"), "}}"))
	lower(inner) == "false"
}

cancels_in_progress_at(value, _) if value == true

cancels_in_progress_at(value, _) if {
	is_string(value)
	lower(trim_space(value)) == "true"
}

cancels_in_progress_at(value, event) if {
	is_expression(value)
	not literal_false_expression(value)
	expression_cancels_at(value, event)
}

# A `cancel-in-progress` value that is nothing but `github.event_name` tests
# cancels only for the events those tests select: `${{ github.event_name ==
# 'pull_request' }}` evaluates false on a `pull_request_target` run and never
# cancels it. Any other expression may evaluate true on the run that matters,
# so it stays conservatively cancelling.
expression_cancels_at(value, event) if {
	some body in expression_bodies(value)
	not body_is_event_test(body)
}

expression_cancels_at(value, event) if {
	bodies := expression_bodies(value)
	count(bodies) > 0
	every body in bodies {
		body_is_event_test(body)
	}
	every body in bodies {
		body_cancels_at(body, event)
	}
}

body_is_event_test(body) if {
	branches := expression_branches(body)
	count(branches) > 0
	every branch in branches {
		event_test(branch)
	}
}

body_cancels_at(body, event) if {
	some branch in expression_branches(body)
	test := event_test(branch)
	event_test_holds_at(test, event)
}

# The event name a comparison guards on survives as `@name@`, so a bare test —
# one with no `&&` tail — can be read straight off the evaluated body.
event_test(branch) := {"event": event, "operator": operator} if {
	trimmed := trim_space(branch)
	matches := regex.find_all_string_submatch_n(EVENT_TEST_RIGHT_PATTERN, trimmed, 1)
	count(matches) > 0
	operator := matches[0][1]
	event := matches[0][2]
}

event_test(branch) := {"event": event, "operator": operator} if {
	trimmed := trim_space(branch)
	matches := regex.find_all_string_submatch_n(EVENT_TEST_LEFT_PATTERN, trimmed, 1)
	count(matches) > 0
	event := matches[0][1]
	operator := matches[0][2]
}

event_test_holds_at(test, event) if {
	test.operator == "=="
	test.event == event
}

event_test_holds_at(test, event) if {
	test.operator == "!="
	test.event != event
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

# A `||` branch the declared event can reach is a candidate for the value
# GitHub selects. One whose own event test excludes the event renders a
# constant instead, and one whose value is provably falsy is skipped, so
# neither carries anything into the group.
branch_selected_at(branches, index, event) if {
	not event_guard_excludes_at(branches[index], event)
	not branch_provably_falsy(branches[index])
	every earlier in array.slice(branches, 0, index) {
		branch_may_be_falsy_at(earlier, event)
	}
}

# An earlier branch whose guard the rule cannot decide may or may not supply a
# value first, so this branch may or may not render. A varying value there
# could reach the group, which the text alone cannot settle.
branch_conditionally_selected_at(branches, index, event) if {
	branch_selected_at(branches, index, event)
	some earlier in array.slice(branches, 0, index)
	not branch_definitely_falsy_at(earlier, event)
}

branch_definitely_falsy_at(branch, event) if event_guard_excludes_at(branch, event)

branch_definitely_falsy_at(branch, _) if branch_provably_falsy(branch)

# What a branch contributes for one event: a branch the event cannot select is
# stable because it renders nothing, a conditionally selected branch is stable
# only when its own text carries nothing that varies between runs, and a
# definitely selected branch is judged on its text.
branch_stability_at(branches, index, event) := "stable" if {
	not branch_selected_at(branches, index, event)
}

branch_stability_at(branches, index, event) := outcome if {
	branch_selected_at(branches, index, event)
	not branch_conditionally_selected_at(branches, index, event)
	outcome := branch_stability(branches[index])
}

branch_stability_at(branches, index, event) := "stable" if {
	branch_conditionally_selected_at(branches, index, event)
	branch_is_stable(branches[index])
}

branch_stability_at(branches, index, event) := "unclassifiable" if {
	branch_conditionally_selected_at(branches, index, event)
	not branch_is_stable(branches[index])
}

# Each expression is judged on its branches for the event at hand:
# `github.event_name == 'push' && github.run_id` renders a constant on a
# `pull_request` run, and a value that changes every time on a run the test
# selects.
expression_stability_at(body, event) := "unclassifiable" if {
	branches := expression_branches(body)
	some index in numbers.range(0, count(branches) - 1)
	branch_stability_at(branches, index, event) == "unclassifiable"
}

expression_stability_at(body, event) := "stable" if {
	branches := expression_branches(body)
	every index in numbers.range(0, count(branches) - 1) {
		branch_stability_at(branches, index, event) == "stable"
	}
}

# An unclassifiable branch outranks an unstable one, as it does in
# `group_stability_at`. Two run-varying branches can be unstable and
# unclassifiable at once: in `github.run_id || github.sha` the first is always
# selected, and the second is selected only when the first is falsy, so whether
# it renders cannot be read from the text. Without the guard the two clauses
# would both produce a value and Rego would fail the whole evaluation.
expression_stability_at(body, event) := "unstable" if {
	branches := expression_branches(body)
	every index in numbers.range(0, count(branches) - 1) {
		branch_stability_at(branches, index, event) != "unclassifiable"
	}
	some index in numbers.range(0, count(branches) - 1)
	branch_stability_at(branches, index, event) == "unstable"
}

# A group whose expressions all keep their value between pushes to one pull
# request is stable: the next push's run joins it and cancels the superseded
# one. A group built from a value that changes instead never matches the
# previous push's run, so it does not cancel in any useful sense, whatever
# `cancel-in-progress` says. A plain string is a constant group: it never
# varies, so RC-002 judges whether it names the pull request.
group_stability_at(value, event) := "stable" if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	every body in expression_bodies(group) {
		expression_stability_at(body, event) == "stable"
	}
}

group_stability_at(value, event) := "unclassifiable" if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	some body in expression_bodies(group)
	expression_stability_at(body, event) == "unclassifiable"
}

group_stability_at(value, event) := "unstable" if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	every body in expression_bodies(group) {
		expression_stability_at(body, event) != "unclassifiable"
	}
	some body in expression_bodies(group)
	expression_stability_at(body, event) == "unstable"
}

# GitHub applies workflow-level and job-level concurrency as separate
# controls, so a job is covered for an event when either level cancels that
# event's superseded run. Coverage is still counted per event: a workflow
# block that cancels only `pull_request` says nothing about a
# `pull_request_target` run of the same workflow.
concurrency_cancels_at(value, event) if {
	is_object(value)
	"cancel-in-progress" in object.keys(value)
	cancels_in_progress_at(value["cancel-in-progress"], event)
	group_stability_at(value, event) == "stable"
}

workflow_cancels_at(workflow, event) if {
	workflow_has_concurrency(workflow)
	concurrency_cancels_at(workflow_concurrency(workflow), event)
}

job_cancels_at(job, event) if {
	job_has_concurrency(job)
	concurrency_cancels_at(job_concurrency(job), event)
}

# The events on which a block cancels. A block whose event test excludes an
# event leaves that event's runs alone, so its group identity is judged only on
# the events it does cancel; a block that never cancels is left to RC-001.
cancelling_events(value, events) := {event |
	some event in events
	concurrency_cancels_at(value, event)
}

workflow_cancels_somewhere(workflow) if {
	count(cancelling_events(workflow_concurrency(workflow), pr_events(workflow))) > 0
}

job_cancels_somewhere(job, events) if {
	count(cancelling_events(job_concurrency(job), events)) > 0
}

# A `github.event_name` guard decides whether the `&&` branch it protects can
# be selected. The guard is judged against each pull-request event the workflow
# declares, so `== 'pull_request'` selects the branch for a `pull_request` run
# and excludes it for a `pull_request_target` run of the same workflow.
event_guard(branch) := {"event": event, "operator": operator, "value": value} if {
	trimmed := trim_space(branch)
	matches := regex.find_all_string_submatch_n(EVENT_GUARD_RIGHT_PATTERN, trimmed, 1)
	count(matches) > 0
	operator := matches[0][1]
	event := matches[0][2]
	value := trim_space(regex.replace(trimmed, EVENT_GUARD_RIGHT_PATTERN, ""))
}

event_guard(branch) := {"event": event, "operator": operator, "value": value} if {
	trimmed := trim_space(branch)
	matches := regex.find_all_string_submatch_n(EVENT_GUARD_LEFT_PATTERN, trimmed, 1)
	count(matches) > 0
	event := matches[0][1]
	operator := matches[0][2]
	value := trim_space(regex.replace(trimmed, EVENT_GUARD_LEFT_PATTERN, ""))
}

api_event(event) if event in API_EVENTS

event_guard_holds_at(branch, event) if {
	guard := event_guard(branch)
	guard.operator == "=="
	guard.event == event
}

event_guard_holds_at(branch, event) if {
	guard := event_guard(branch)
	guard.operator == "!="
	guard.event != event
}

event_guard_excludes_at(branch, event) if {
	event_guard(branch)
	not event_guard_holds_at(branch, event)
}

# GitHub takes the first truthy branch of a `||` chain, so a later branch is
# selectable only when every earlier one can be falsy. A bare marker is never
# empty on a pull request, and a non-empty literal is truthy, so neither lets
# the chain move on. A guard that fails for this event is always falsy, and a
# value the rule cannot prove non-empty may be falsy.
branch_may_be_falsy_at(branch, event) if event_guard_excludes_at(branch, event)

branch_may_be_falsy_at(branch, event) if {
	event_guard_holds_at(branch, event)
	value := event_guard(branch).value
	not branch_always_truthy(value)
	not mentions_pull_request(value)
}

branch_may_be_falsy_at(branch, _) if {
	not event_guard(branch)
	not branch_always_truthy(branch)
}

branch_bare_marker(branch) if {
	some marker in PR_NUMBER_MARKERS
	lower(trim_space(branch)) == marker
}

# A `&&` chain whose guards all hold takes its final segment, so a chain ending
# in a reference GitHub always resolves to a non-empty string is truthy as a
# whole. In `github.ref && github.head_ref || 'ci'` the marker is always
# selected, so the `ci` fallback is never reached and the group is compliant.
branch_provably_truthy(branch) if {
	conjunction := branch_conjunction(branch)
	every guard in conjunction.guards {
		guard_proven_truthy(guard)
	}
	branch_bare_marker(conjunction.value)
}

# An empty literal is falsy, so the chain moves past it; every other literal
# renders the same text for every pull request.
branch_constant_literal(branch) if {
	trimmed := trim_space(branch)
	literal_argument(trimmed)
	trimmed != `""`
}

# A branch excluded by its own guard supplies a falsy value, so the chain moves
# past it and its text is never the group's value. Every other branch the chain
# can reach is a candidate for the value GitHub selects, which is the same
# question stability asks of a branch.
branch_value_reachable_at(branches, index, event) if {
	branch_selected_at(branches, index, event)
}

branch_value_reachable(branches, event) if {
	some index in numbers.range(0, count(branches) - 1)
	branch_value_reachable_at(branches, index, event)
}

# The text that decides a branch's value names the pull request only when the
# guard selecting it holds for this event; an excluded branch renders a boolean
# instead.
branch_names_pull_request_at(branch, event) if {
	guard := event_guard(branch)
	event_guard_holds_at(branch, event)
	not branch_conjunction_falsy(guard.value)
	mentions_pull_request(guard.value)
}

branch_names_pull_request_at(branch, _) if {
	not event_guard(branch)
	not branch_conjunction_falsy(branch)
	mentions_pull_request(branch)
}

# A `&&` chain yields its last segment only when every earlier segment is
# truthy, so the chain is kept as the guards that must hold and the value they
# select. Splitting every top-level `&&` matters: in
# `github.ref && false && github.head_ref` the literal `false` stops the chain,
# and a rule that read only the first guard would call the marker selected.
branch_conjunction(branch) := {"guards": guards, "value": value} if {
	segments := conjunction_segments(branch)
	count(segments) > 1
	guards := array.slice(segments, 0, count(segments) - 1)
	value := segments[count(segments) - 1]
}

conjunction_segments(text) := [trimmed |
	bounds := array.concat(
		[0],
		array.concat(top_level_conjunctions(text), [count(characters(text))]),
	)
	some index in numbers.range(0, count(bounds) - 2)
	trimmed := trim_space(
		substring(text, segment_start(bounds, index), segment_length(bounds, index)),
	)
]

segment_start(bounds, index) := 0 if index == 0

segment_start(bounds, index) := bounds[index] + 2 if index > 0

segment_length(bounds, index) := bounds[index + 1] - segment_start(bounds, index)

# A literal the rule can prove falsy anywhere in the chain makes the whole
# conjunction falsy, so the marker after it never becomes the group's value.
branch_conjunction_falsy(branch) if {
	conjunction := branch_conjunction(branch)
	some guard in conjunction.guards
	lower(guard) in FALSY_LITERALS
}

# A value GitHub is known to treat as falsy, so a `||` carrying it moves on to
# the next branch instead of rendering it.
branch_provably_falsy(branch) if {
	lower(trim_space(branch)) in FALSY_LITERALS
}

branch_provably_falsy(branch) if trim_space(branch) == `""`

branch_provably_falsy(branch) if branch_conjunction_falsy(branch)

# A chain with a guard the rule cannot prove truthy may leave the value after
# it unselected. When that value names the pull request, whether the group
# carries it cannot be read from the text, so it is reported indeterminate
# rather than as a pass or a defect.
branch_guard_unsettled_at(branch, _) if {
	not event_guard(branch)
	conjunction := branch_conjunction(branch)
	mentions_pull_request(conjunction.value)
	not branch_conjunction_falsy(branch)
	not branch_conjunction_holds(branch)
}

# The value an event guard protects is a conjunction in its own right: in
# `github.event_name == 'pull_request' && github.ref == 'refs/heads/main' &&
# github.head_ref` the marker is selected only when the second guard is also
# truthy, which the rule cannot prove from the text.
branch_guard_unsettled_at(branch, event) if {
	guard := event_guard(branch)
	event_guard_holds_at(branch, event)
	conjunction := branch_conjunction(guard.value)
	mentions_pull_request(conjunction.value)
	not branch_conjunction_falsy(guard.value)
	not branch_conjunction_holds(guard.value)
}

guard_proven_truthy(guard) if lower(guard) in TRUTHY_GUARDS

guard_proven_truthy(guard) if lower(guard) == "true"

branch_conjunction_holds(branch) if {
	conjunction := branch_conjunction(branch)
	every guard in conjunction.guards {
		guard_proven_truthy(guard)
	}
}

top_level_conjunctions(text) := [position |
	chars := characters(text)
	some position in numbers.range(0, count(chars) - 1)
	position + 1 < count(chars)
	chars[position] == "&"
	chars[position + 1] == "&"
	depth_at(text, position) == 0
]

# A branch is settled when its value cannot be the one GitHub selects for this
# event, or when it names the pull request and carries no guard the rule cannot
# evaluate. Every branch that can supply the value must be settled, and at
# least one must exist: an expression whose every branch is falsy on a pull
# request is a constant group.
branch_settled_at(branches, index, event) if {
	not branch_value_reachable_at(branches, index, event)
}

branch_settled_at(branches, index, event) if {
	branch := branches[index]
	branch_value_reachable_at(branches, index, event)
	branch_names_pull_request_at(branch, event)
	not branch_guard_unsettled_at(branch, event)
}

branch_list_settled_at(branches, event) if {
	branch_value_reachable(branches, event)
	unsettled := [index |
		some index in numbers.range(0, count(branches) - 1)
		not branch_settled_at(branches, index, event)
	]
	count(unsettled) == 0
}

# The marker only names the pull request when GitHub evaluates it, and only in
# the branch GitHub selects. A group that spells the marker as literal text,
# such as `ci-github.head_ref`, is a constant, so every pull request shares it
# and cancels the others. Match the marker only inside the unquoted part of a
# `${{ ... }}` expression; inside quotes it is a string literal, as in
# `${{ 'github.head_ref' }}`. A marker used as a comparison operand is also
# excluded, because the comparison renders as a boolean rather than as the pull
# request's identity. Finally, every branch a pull request can select must name
# the pull request, so a marker that appears only in a constant-first fallback,
# as in `${{ 'ci' || github.head_ref }}` or in a fallback nested inside a call
# argument, does not name anything.
# Every event is judged on its own, because the group text may name the pull
# request through one body for one event and a different body for another.
group_names_pull_request(value, events) if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	bodies := expression_bodies(group)
	every event in events {
		some body in bodies
		branch_list_settled_at(expression_branches(body), event)
	}
}

# A group whose only marker references feed a call the rule does not classify,
# or sit behind a comparison or guard the rule cannot evaluate, may or may not
# carry the pull request, so it is reported as indeterminate rather than as a
# pass or a defect.
group_identity_unknown(value, events) if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	not group_names_pull_request(value, events)
	some body in expression_bodies(group)
	branches := expression_branches(body)
	some index in numbers.range(0, count(branches) - 1)
	some event in events
	branch_value_reachable_at(branches, index, event)
	branch_guard_unsettled_at(branches[index], event)
}

group_identity_unknown(value, events) if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	not group_names_pull_request(value, events)
	some body in expression_bodies(group)
	branches := expression_branches(body)
	some index in numbers.range(0, count(branches) - 1)
	some event in events
	branch_value_reachable_at(branches, index, event)
	branch_has_unclassifiable_marker(branches[index])
}

# An unknown transformation in any branch the pull request can select may or may
# not reach the group, so the identity cannot be read from the text.
branch_has_unclassifiable_marker(branch) if {
	head := lower(branch)
	some marker in PR_NUMBER_MARKERS
	parts := split(head, marker)
	some index, following in parts
	index > 0
	marker_occurrence_is_unclassifiable(
		head,
		occurrence_prefix(parts, marker, index),
		following,
	)
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
	some event in pr_events(workflow)
	group_stability_at(workflow_concurrency(workflow), event) == "unclassifiable"
}

unclassifiable_job_groups(workflow) := {name |
	some name in object.keys(workflow_jobs(workflow))
	job := workflow_jobs(workflow)[name]
	some event in pr_events(workflow)
	group_stability_at(job_concurrency(job), event) == "unclassifiable"
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

# A job is covered when the workflow block or the job's own block cancels a
# given event's superseded run, because GitHub applies both levels and either
# one stops the work. The two levels are not interchangeable across events: a
# workflow block that cancels only `pull_request` leaves the job uncovered on
# a `pull_request_target` run unless the job block cancels that event too.
uncovered_jobs(workflow) := {name |
	some name in object.keys(workflow_jobs(workflow))
	job := workflow_jobs(workflow)[name]
	not job_disabled(job)
	some event in pr_events(workflow)
	not workflow_cancels_at(workflow, event)
	not job_cancels_at(job, event)
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
	msg := "the workflow's concurrency group has a `||` fallback beside a value that changes between runs, so whether it cancels a superseded run cannot be classified"
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
	msg := sprintf("job(s) %s have a concurrency group with a `||` fallback beside a value that changes between runs, so whether it cancels a superseded run cannot be classified", [concat(", ", sort(names))])
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	msg := "the workflow's concurrency group passes a pull-request marker to a call this rule cannot classify, so whether the group names the pull request cannot be classified"
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	workflow_has_concurrency(workflow)
	events := cancelling_events(workflow_concurrency(workflow), pr_events(workflow))
	group_identity_unknown(workflow_concurrency(workflow), events)
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	jobs := workflow_jobs(workflow)
	unknown := {name |
		some name in object.keys(jobs)
		job := jobs[name]
		events := cancelling_events(job_concurrency(job), pr_events(workflow))
		group_identity_unknown(job_concurrency(job), events)
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
		"workflow %s runs on pull requests but neither it nor job(s) %s cancel superseded runs for every pull-request event it declares. Add a cancelling concurrency block at workflow level, or to every job that is not disabled with `if: false`",
		[workflow_path(workflow), concat(", ", sort(names))],
	)
}

deny contains finding("RC-002", severity, "noncompliant", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	rc_audited(workflow)
	workflow_cancels_somewhere(workflow)
	events := cancelling_events(workflow_concurrency(workflow), pr_events(workflow))
	not group_names_pull_request(workflow_concurrency(workflow), events)
	not group_identity_unknown(workflow_concurrency(workflow), events)
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
	job_cancels_somewhere(job, pr_events(workflow))
	job_events := cancelling_events(job_concurrency(job), pr_events(workflow))
	not group_names_pull_request(job_concurrency(job), job_events)
	not group_identity_unknown(job_concurrency(job), job_events)
	msg := sprintf(
		"job %s of %s cancels superseded runs, but its concurrency group does not name the pull request. Include github.event.pull_request.number or github.head_ref so runs for different pull requests do not cancel one another",
		[name, workflow_path(workflow)],
	)
}