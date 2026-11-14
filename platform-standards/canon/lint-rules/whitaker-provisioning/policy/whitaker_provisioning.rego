# WP: Provision Whitaker only through shared-actions install-whitaker.
#
# The action alone carries the install rules (an exact installer version, the
# lint suite never pinned, --no-source-fallback always passed), and its own
# contract proves them. This policy therefore does not re-check flags. It
# proves two things: that the action is the only route by which a checkout
# provisions Whitaker, and that every use of it is pinned to a certified
# revision: one the audit's `install_certifications` records as descending from
# a certification tag with the runtime content unchanged.
#
# Every other route is refused wherever CI could take it: a workflow or
# composite-action `run:` body, another action's inputs, a Makefile, or a
# script. A route is recognized from the command that installs, downloads or
# runs one of the Whitaker tools; the policy does not interpret shell, so a
# command assembled at run time from variables is not recognized. The
# download clause reads a whole script, because a release-asset installer
# script names the asset in one line and fetches it in another.
package canon.lint_rules.whitaker_provisioning

import rego.v1

# -- parameters ---------------------------------------------------------------

default certification_tag_pattern := "certified/install-whitaker/<installer version>"

certification_tag_pattern := concat("", [data.parameters.certification_tag_prefix, "<installer version>"])

default producer_repositories := ["leynos/whitaker"]

producer_repositories := data.parameters.producer_repositories

default action_repository := "leynos/shared-actions"

action_repository := data.parameters.action_repository

default action_directory := ".github/actions/install-whitaker"

action_directory := data.parameters.action_directory

default exemptions := []

exemptions := data.parameters.exemptions

# -- envelope -----------------------------------------------------------------

finding(rule_id, verdict, path, msg) := {
	"rule_id": rule_id,
	"severity": "error",
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": msg,
}

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/whitaker-provisioning"
	is_array(input.workflows)
	is_array(input.actions)
	is_array(input.scripts)
}

repository_name := object.get(object.get(input, "repository", {}), "name", null)

is_producer if repository_name in producer_repositories

is_action_repository if repository_name == action_repository

# The action's own definition is the sanctioned route, so its files are not
# audited as a second one. Only in the repository that owns it: a directory
# of the same name elsewhere is a local copy, and is audited like any script.
in_action_directory(path) if {
	is_action_repository
	startswith(path, concat("", [action_directory, "/"]))
}

is_exempt(path) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.path == path
}

audited(path) if {
	not in_action_directory(path)
	not is_exempt(path)
}

# -- recognizers ----------------------------------------------------------------

# `cargo install`, `cargo +toolchain install`, `cargo binstall`,
# `cargo quickinstall` and the `cargo-binstall` binary, naming a tool.
install_pattern := `(cargo(\s+\+\S+)?\s+(install|binstall|quickinstall)|cargo-binstall)\b.*\b(whitaker-installer|cargo-dylint|dylint-link)\b`

# `whitaker-installer` in command position: at the start of a line or after
# a shell separator, subshell, brace group or keyword, behind optional
# `NAME=value` prefixes, with or without a directory or quotes. A bare `{` is
# not a separator: `${HOME}/.cargo/bin/whitaker-installer` inside an argument
# would otherwise read as a command.
invoke_pattern := `(^|[;&|(]|\{\s|\bthen|\bdo|\belse)\s*([A-Za-z_][A-Za-z0-9_]*=\S*\s+)*["']?[^\s"';&|()]*\bwhitaker-installer(\.exe)?["']?(\s|;|$)`

# A fetch by HTTP client, by `gh release download`, or by `gh api` against a
# releases endpoint, which is how a release-asset script reads an asset
# without ever naming a download URL.
download_pattern := `\b(curl|wget|Invoke-WebRequest|iwr|Invoke-RestMethod|urlretrieve|urlopen|requests\.get|httpx\.get)\b|\bgh\s+release\s+download\b|\bgh\s+api\b[^\n]*(\n[^\n]*)?/releases\b`

release_pattern := `\b(whitaker-installer|cargo-dylint|dylint-link)\b|\bleynos/whitaker\b`

# Shell and Make comments are prose, not commands, whether they fill a line or
# trail a command. A trailing comment starts at a `#` that follows whitespace
# outside quotes, so `url#fragment` and `"a # b"` keep their text and
# `echo done # cargo install whitaker-installer` loses only the comment. A lone
# quote, such as an apostrophe in prose, is passed over rather than allowed to
# hide the rest of the line. A continued line is one command, so its halves are
# joined before a clause reads it.
code_of(line) := trim_space(regex.find_all_string_submatch_n(
	`^((?:'[^']*'|"[^"]*"|[^\s'"#]#|[^'"#]|['"])*)`,
	line,
	1,
)[0][1])

command_lines(text) := [line |
	joined := replace(replace(text, "\\\r\n", " "), "\\\n", " ")
	some raw in split(joined, "\n")
	line := code_of(trim_space(raw))
	line != ""
]

installs_a_tool(text) if {
	some line in command_lines(text)
	regex.match(install_pattern, line)
}

invokes_the_installer(text) if {
	some line in command_lines(text)
	regex.match(invoke_pattern, line)
}

downloads_a_tool(text) if {
	body := concat("\n", command_lines(text))
	regex.match(download_pattern, body)
	regex.match(release_pattern, body)
}

route(text) := "installs a Whitaker tool with Cargo" if installs_a_tool(text)

else := "runs whitaker-installer directly" if invokes_the_installer(text)

else := "downloads a Whitaker tool" if downloads_a_tool(text)

# -- surfaces -------------------------------------------------------------------

decoded(document) := parsed if {
	is_object(document)
	parsed := object.get(document, "parsed", null)
	is_object(parsed)
}

workflow_steps contains [workflow.path, step] if {
	some workflow in input.workflows
	jobs := object.get(decoded(workflow), "jobs", {})
	is_object(jobs)
	some job in jobs
	is_object(job)
	steps := object.get(job, "steps", [])
	is_array(steps)
	some step in steps
	is_object(step)
}

action_steps contains [action.path, step] if {
	some action in input.actions
	runs := object.get(decoded(action), "runs", {})
	is_object(runs)
	steps := object.get(runs, "steps", [])
	is_array(steps)
	some step in steps
	is_object(step)
}

steps := workflow_steps | action_steps

refusal(path, how) := sprintf(
	"%s %s. Whitaker is provisioned only through uses: %s/%s@<pin>, which pins the installer, never pins the lint suite, and refuses a source build; replace this route with that step",
	[path, how, action_repository, action_directory],
)

# -- QG-002: a route other than the action ---------------------------------------

deny contains f if {
	envelope_ok
	not is_producer
	some [path, step] in steps
	audited(path)
	run := object.get(step, "run", null)
	is_string(run)
	how := route(run)
	f := finding("QG-002", "noncompliant", path, refusal(path, how))
}

# An action other than install-whitaker, handed a Whitaker tool by name, is a
# route too: `taiki-e/install-action` with `tool: cargo-dylint` is the shape.
deny contains f if {
	envelope_ok
	not is_producer
	some [path, step] in steps
	audited(path)
	uses := object.get(step, "uses", null)
	is_string(uses)
	not names_the_action(uses)
	hands_over_a_tool(object.get(step, "with", {}))
	how := sprintf("passes a Whitaker tool to %s", [uses])
	f := finding("QG-002", "noncompliant", path, refusal(path, how))
}

deny contains f if {
	envelope_ok
	not is_producer
	some script in input.scripts
	audited(script.path)
	is_string(script.text)
	how := route(script.text)
	f := finding("QG-002", "noncompliant", script.path, refusal(script.path, how))
}

# An input naming a tool, alone or in a list, optionally with `@version`. A
# path such as `~/.cargo/bin/whitaker-installer` in a cache step's `path` is
# not a tool name, and caching an installer the action installed is allowed.
hands_over_a_tool(inputs) if {
	is_object(inputs)
	some value in inputs
	is_string(value)
	some token in regex.split(`[,\s]+`, value)
	regex.match(`^(whitaker-installer|cargo-dylint|dylint-link)(@\S*)?$`, token)
}

# -- QG-002: the action, pinned to a certified revision ---------------------------

remote_prefix := concat("", [action_repository, "/", action_directory, "@"])

names_the_action(uses) if endswith(split(uses, "@")[0], "/install-whitaker")

names_the_action(uses) if endswith(uses, "/install-whitaker")

# The owning repository runs the action from its own checkout.
runs_from_own_checkout(uses) if {
	is_action_repository
	uses in {concat("", ["./", action_directory]), concat("", ["$/", action_directory])}
}

# A full commit SHA of the action repository's action. Whether that revision is
# certified is not decidable from the checkout, which holds no history of the
# action repository: the rule-run command asks, and records the answer under
# `install_certifications` for the revision.
revision_of(uses) := ref if {
	startswith(uses, remote_prefix)
	ref := substring(uses, count(remote_prefix), -1)
	regex.match(`^[0-9a-f]{40}$`, ref)
}

default certification_status(_) := ""

certification_status(sha) := status if {
	cert := object.get(object.get(input, "install_certifications", {}), sha, null)
	is_object(cert)
	status := object.get(cert, "status", "")
}

default certification_reason(_) := ""

certification_reason(sha) := reason if {
	cert := object.get(object.get(input, "install_certifications", {}), sha, null)
	is_object(cert)
	reason := object.get(cert, "reason", "")
}

not_a_revision_refusal(path, uses) := sprintf(
	"%s uses %s, which is not a certified revision of the Whitaker install rules. Pin %s to a full commit SHA of a certified revision (a revision on shared-actions `main` that descends from a `%s` tag with the install-whitaker runtime content unchanged since it)",
	[path, uses, remote_prefix, certification_tag_pattern],
)

uncertified_refusal(path, uses, reason) := sprintf(
	"%s uses %s, which is not a certified revision of the Whitaker install rules: %s. A maintainer certifies new install-whitaker content by tagging a reviewed commit `%s`; until then pin a certified revision",
	[path, uses, reason, certification_tag_pattern],
)

unresolved_refusal(path, uses, reason) := sprintf(
	"%s uses %s, which could not be checked for certification (%s), so whether it carries the Whitaker install rules is unknown",
	[path, uses, reason],
)

# A fork, a local copy, a tag, a branch or a short SHA is no revision to certify.
deny contains f if {
	envelope_ok
	not is_producer
	some [path, step] in steps
	uses := object.get(step, "uses", null)
	is_string(uses)
	names_the_action(uses)
	not runs_from_own_checkout(uses)
	not revision_of(uses)
	f := finding("QG-002", "noncompliant", path, not_a_revision_refusal(path, uses))
}

deny contains f if {
	envelope_ok
	not is_producer
	some [path, step] in steps
	uses := object.get(step, "uses", null)
	is_string(uses)
	names_the_action(uses)
	sha := revision_of(uses)
	certification_status(sha) == "uncertified"
	f := finding("QG-002", "noncompliant", path, uncertified_refusal(path, uses, certification_reason(sha)))
}

# An answer that is missing or not one of the three the command records means
# the audit did not establish certification, which is not a pass.
deny contains f if {
	envelope_ok
	not is_producer
	some [path, step] in steps
	uses := object.get(step, "uses", null)
	is_string(uses)
	names_the_action(uses)
	sha := revision_of(uses)
	not certification_status(sha) in {"certified", "uncertified"}
	reason := unresolved_reason(sha)
	f := finding("QG-002", "indeterminate", path, unresolved_refusal(path, uses, reason))
}

unresolved_reason(sha) := certification_reason(sha) if certification_status(sha) == "unknown"

unresolved_reason(sha) := "the audit did not resolve it" if certification_status(sha) != "unknown"

# -- QG-002: surfaces the policy cannot read --------------------------------------

unreadable contains [document.path, document.error] if {
	some document in array.concat(input.workflows, input.actions)
	is_object(document)
	object.get(document, "error", null) != null
}

unreadable contains [script.path, script.error] if {
	some script in input.scripts
	is_object(script)
	object.get(script, "error", null) != null
}

deny contains f if {
	envelope_ok
	not is_producer
	some [path, reason] in unreadable
	audited(path)
	msg := sprintf("%s could not be read (%s), so whether it provisions Whitaker is unknown", [path, reason])
	f := finding("QG-002", "indeterminate", path, msg)
}

# -- EN-001: an envelope this policy was not written for ---------------------------

deny contains f if {
	not envelope_ok
	f := finding("EN-001", "indeterminate", ".", "the policy input is not a schema-1 policy-input/whitaker-provisioning envelope")
}
