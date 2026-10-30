# DF-001 to DF-009: dependency relationships the estate has recorded are
# protected by the repository's configuration and evidence.
#
# The Python envelope has already read every governed manifest and lock, decided
# for each declaration which Dependabot entry covers it and which group takes its
# published name first for each update class (document order matters; a Rego
# object keeps none), resolved locked identities, and read the repository's
# documented bounds and derived inputs. This policy only compares those facts.
# It opens nothing, runs no repository code and contacts no registry. A
# relationship applies where the repository declares every member or contracts
# to it in `.concordat`. Missing evidence is indeterminate, never compliant.
package canon.lint_rules.dependency_family_contracts

import rego.v1

finding(rule_id, verdict, path, message) := {
	"rule_id": rule_id,
	"severity": "error",
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": message,
}

envelope_ok if {
	input.schema_version == 2
	input.kind == "policy-input/dependency-family-contracts"
	is_array(input.relationships)
	is_array(input.declarations)
	is_array(input.family_contracts)
	is_array(input.monitoring)
	is_array(input.protected_bounds)
	is_array(input.derived_inputs)
}

python_ecosystems := {"pip", "uv"}

# `pip` and `uv` are two updaters for the same Python dependency surface, so a
# catalog member recorded once matches whichever native lock the repository
# actually uses.
ecosystem_matches(declared, wanted) if {
	declared == wanted
}

ecosystem_matches(declared, wanted) if {
	declared in python_ecosystems
	wanted in python_ecosystems
}

declarations_of(pkg, ecosystem) := [d |
	some d in input.declarations
	d["package"] == pkg
	ecosystem_matches(d.ecosystem, ecosystem)
]

member_declarations(rel) := [d |
	some member in rel.members
	some d in declarations_of(member["package"], member.ecosystem)
]

# An exception may name one manifest; it then exempts that manifest's
# declarations only, so a deliberate older baseline in one workspace does not
# hide a split in another.
exempted(rel, d) if {
	some exception in input.exceptions
	exception.relationship == rel.id
	exception.manifest == null
}

exempted(rel, d) if {
	some exception in input.exceptions
	exception.relationship == rel.id
	exception.manifest == d.manifest
}

checked_declarations(rel) := [d |
	some d in member_declarations(rel)
	not exempted(rel, d)
]

all_exempt(rel) if {
	count(member_declarations(rel)) > 0
	count(checked_declarations(rel)) == 0
}

contracted(rel) if {
	some identifier in input.family_contracts
	identifier == rel.id
}

relationship_exists(identifier) if {
	some rel in input.relationships
	rel.id == identifier
}

missing_members(rel) := [member |
	some member in rel.members
	count(declarations_of(member["package"], member.ecosystem)) == 0
]

applicable contains rel if {
	envelope_ok
	some rel in input.relationships
	count(checked_declarations(rel)) > 0
	every member in rel.members {
		count(declarations_of(member["package"], member.ecosystem)) > 0
	}
	not all_exempt(rel)
}

applicable contains rel if {
	envelope_ok
	some rel in input.relationships
	contracted(rel)
	not all_exempt(rel)
}

first_path(rel) := path if {
	decls := checked_declarations(rel)
	path := sort([d.manifest | some d in decls])[0]
}

uncovered(rel) := [d |
	some d in checked_declarations(rel)
	d.coverage.reason != null
]

unknown_breaking(rel) := [d |
	some d in checked_declarations(rel)
	d.coverage.breaking_update_type == null
]

# The update class a declaration's current line can be broken by. Python and
# Cargo classify a pre-1.0 bump differently, so each declaration carries its
# own answer.
breaking(d) := d.coverage.breaking_update_type

breaking_classes(rel) := {update_type |
	some d in checked_declarations(rel)
	update_type := breaking(d)
	update_type != null
}

# Each relationship is checked in the one update class its members classify
# alike. A family whose members classify updates differently cannot be judged
# by one group's `update-types`, so it is indeterminate rather than guessed.
activation(rel, update_type) if {
	breaking_classes(rel) == {update_type}
}

taken(rel, update_type) := [m |
	some d in checked_declarations(rel)
	m := d.coverage.by_update_type[update_type]
	m != null
]

group_keys(rel, update_type) := {[m.entry, m.group] |
	some m in taken(rel, update_type)
}

catch_all_selected(rel, update_type) if {
	some m in taken(rel, update_type)
	m.catch_all
}

unclaimed(rel, update_type) if {
	count(taken(rel, update_type)) < count(checked_declarations(rel))
}

shared_narrow_group(rel, update_type) if {
	not unclaimed(rel, update_type)
	count(group_keys(rel, update_type)) == 1
	not catch_all_selected(rel, update_type)
}

# Every member of the relationship joins one defined multi-ecosystem group
# whose patterns take it. Dependabot then opens a single pull request for the
# group, so one CI run sees the whole relationship; that coordinates the update
# but does not choose compatible versions, which is why it protects a tuple
# without an upper bound and why it only counts when every member is in it.
shared_multi_group(rel) if {
	multi_groups := {d.coverage.multi_group | some d in member_declarations(rel)}
	count(multi_groups) == 1
	every d in member_declarations(rel) {
		d.coverage.multi_group != null
	}
}

# A narrow group names every member for the breaking class, but another group
# takes at least one of them first, so the family still splits.
common_narrow(rel, update_type) := {key |
	some m in taken(rel, update_type)
	key := [m.entry, m.group]
	some d in checked_declarations(rel)
	group_key_exists(some_narrow(d, update_type), key)
	every other in checked_declarations(rel) {
		group_key_exists(some_narrow(other, update_type), key)
	}
}

some_narrow(d, update_type) := [key |
	some m in d.coverage.matching_narrow_by_update_type[update_type]
	key := [m.entry, m.group]
]

group_key_exists(keys, wanted) if {
	some key in keys
	key == wanted
}

group_family(rel) if {
	rel.kind in {"family", "type-identity"}
}

group_names(rel, update_type) := sort({m.group |
	some m in taken(rel, update_type)
}) if {
	count(taken(rel, update_type)) > 0
}

group_names(rel, update_type) := ["no group"] if {
	count(taken(rel, update_type)) == 0
}

# A relationship that records no migration must still render its finding. Read
# the field with a default: a direct `rel.migrations` leaves the whole rule
# body undefined for the catalog entries that omit it.
migration_note(rel) := note if {
	refs := object.get(rel, "migrations", [])
	is_array(refs)
	count(refs) > 0
	note := sprintf("; migration evidence: %s", [concat(", ", refs)])
}

migration_note(rel) := "" if {
	refs := object.get(rel, "migrations", [])
	not is_array(refs)
}

migration_note(rel) := "" if {
	refs := object.get(rel, "migrations", [])
	is_array(refs)
	count(refs) == 0
}

member_list(rel) := concat(", ", sort([member["package"] |
	some member in rel.members
]))

# ---------------------------------------------------------------------------
# DF-006: facts that cannot be judged.
# ---------------------------------------------------------------------------

deny contains finding("DF-006", "indeterminate", ".", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/dependency-family-contracts envelope at schema version 2"
}

deny contains finding("DF-006", "indeterminate", ".", msg) if {
	envelope_ok
	some problem in input.problems
	msg := sprintf("dependency facts could not be established: %s", [problem])
}

deny contains finding("DF-006", "indeterminate", ".", msg) if {
	envelope_ok
	some identifier in input.family_contracts
	not relationship_exists(identifier)
	msg := sprintf(
		"dependencies.family_contracts names %s, which the relationship data does not define",
		[identifier],
	)
}

deny contains finding("DF-006", "indeterminate", ".", msg) if {
	some rel in applicable
	locked := missing_members(rel)
	count(locked) > 0
	msg := sprintf(
		"%s is contracted but no declaration was found for %s; declare the manifest surface in .concordat or remove the contract",
		[rel.id, concat(", ", sort([member["package"] | some member in locked]))],
	)
}

deny contains finding("DF-006", "indeterminate", first_path(rel), msg) if {
	some rel in applicable
	count(uncovered(rel)) > 0
	reasons := {d.coverage.reason | some d in uncovered(rel)}
	msg := sprintf("%s cannot be judged: %s", [rel.id, concat("; ", sort(reasons))])
}

deny contains finding("DF-006", "indeterminate", first_path(rel), msg) if {
	some rel in applicable
	broken := unknown_breaking(rel)
	count(broken) > 0
	paths := {d.manifest | some d in broken}
	msg := sprintf(
		"%s cannot be judged: the update class that can break the current line is unknown for %s",
		[rel.id, concat(", ", sort(paths))],
	)
}

deny contains finding("DF-006", "indeterminate", first_path(rel), msg) if {
	some rel in applicable
	group_family(rel)
	count(breaking_classes(rel)) > 1
	msg := sprintf(
		"%s: its members classify a pre-1.0 bump differently, so no single `update-types` group can protect them; split the contract by ecosystem or record a reviewed exception",
		[rel.id],
	)
}

# ---------------------------------------------------------------------------
# DF-001 / DF-003: release-family coordination per update class.
# ---------------------------------------------------------------------------

deny contains finding("DF-001", "noncompliant", first_path(rel), msg) if {
	some rel in applicable
	group_family(rel)
	count(uncovered(rel)) == 0
	count(unknown_breaking(rel)) == 0
	not shared_multi_group(rel)
	some update_type in {"major", "minor", "patch"}
	activation(rel, update_type)
	not shared_narrow_group(rel, update_type)
	count(common_narrow(rel, update_type)) == 0
	msg := sprintf(
		"%s: the %s update class of %s is not taken together by one narrow Dependabot group ahead of the catch-all (taken by: %s); add a group for the family, or record a reviewed exception%s",
		[
			rel.id,
			update_type,
			member_list(rel),
			concat(", ", group_names(rel, update_type)),
			migration_note(rel),
		],
	)
}

deny contains finding("DF-003", "noncompliant", first_path(rel), msg) if {
	some rel in applicable
	group_family(rel)
	count(uncovered(rel)) == 0
	count(unknown_breaking(rel)) == 0
	not shared_multi_group(rel)
	some update_type in {"major", "minor", "patch"}
	activation(rel, update_type)
	not shared_narrow_group(rel, update_type)
	count(common_narrow(rel, update_type)) > 0
	msg := sprintf(
		"%s: group %s matches every member for the %s update class, but another group takes one of them first (taken by: %s); move it ahead of the shadowing group%s",
		[
			rel.id,
			concat(", ", sort({key[1] | some key in common_narrow(rel, update_type)})),
			update_type,
			concat(", ", group_names(rel, update_type)),
			migration_note(rel),
		],
	)
}

# ---------------------------------------------------------------------------
# DF-007: resolved type and trait identity.
# ---------------------------------------------------------------------------

identity_edges(rel, name) := {identity |
	some d in checked_declarations(rel)
	some identity in d.resolved.member_edges[name]
}

direct_identities(rel, name) := {identity |
	some member in rel.members
	member["package"] == name
	some d in declarations_of(member["package"], member.ecosystem)
	d.resolved.package_id != null
	identity := d.resolved.package_id
}

linkable(rel) if {
	some member in rel.members
	count(identity_edges(rel, member["package"])) > 0
}

deny contains finding("DF-006", "indeterminate", first_path(rel), msg) if {
	some rel in applicable
	rel.kind == "type-identity"
	count(uncovered(rel)) == 0
	count(unknown_breaking(rel)) == 0
	not linkable(rel)
	msg := sprintf(
		"%s needs the resolved package identities, but the lock records no edge between %s",
		[rel.id, member_list(rel)],
	)
}

deny contains finding("DF-007", "noncompliant", first_path(rel), msg) if {
	some rel in applicable
	rel.kind == "type-identity"
	count(uncovered(rel)) == 0
	some member in rel.members
	identities := identity_edges(rel, member["package"])
	count(identities) > 1
	msg := sprintf(
		"%s: %s is resolved at more than one identity (%s), so the exchanged type has no single identity; align the declarations or record a reviewed exception%s",
		[
			rel.id,
			member["package"],
			concat(", ", sort(identities)),
			migration_note(rel),
		],
	)
}

deny contains finding("DF-007", "noncompliant", first_path(rel), msg) if {
	some rel in applicable
	rel.kind == "type-identity"
	count(uncovered(rel)) == 0
	some member in rel.members
	identities := identity_edges(rel, member["package"])
	count(identities) > 0
	some direct in direct_identities(rel, member["package"])
	not identities[direct]
	msg := sprintf(
		"%s: the declaration of %s resolves to %s, but the lock links it as %s; align the requirement with the version the other members were built against%s",
		[
			rel.id,
			member["package"],
			direct,
			concat(", ", sort(identities)),
			migration_note(rel),
		],
	)
}

# ---------------------------------------------------------------------------
# DF-002: independently versioned tuples. A group cannot select mutually
# compatible latest versions, so a member needs an upper bound or an `ignore`
# rule; alternatively every member joins one multi-ecosystem group, so the
# update arrives as one pull request. An exact pin is the thing a version
# update replaces.
# ---------------------------------------------------------------------------

protecting_ignore(d) if {
	some rule in d.coverage.ignore_rules
	rule.protects_breaking
}

member_protected(name, ecosystem) if {
	decls := declarations_of(name, ecosystem)
	count(decls) > 0
	every d in decls {
		d.upper_bounded
	}
}

member_protected(name, ecosystem) if {
	decls := declarations_of(name, ecosystem)
	count(decls) > 0
	every d in decls {
		protecting_ignore(d)
	}
}

# An ignore rule that holds every declaration back leaves the member with no
# update at all, so the tuple cannot drift apart. This is the fact the registry
# records across entries: a plain entry may ignore a member the group entry does
# not take, and the rule that did it need not belong to the handling entry.
member_protected(name, ecosystem) if {
	decls := declarations_of(name, ecosystem)
	count(decls) > 0
	every d in decls {
		d.coverage.ignored
	}
}

protected_tuple(rel) if {
	some member in rel.members
	member_protected(member["package"], member.ecosystem)
}

protected_tuple(rel) if {
	shared_multi_group(rel)
}

deny contains finding("DF-002", "noncompliant", first_path(rel), msg) if {
	some rel in applicable
	rel.kind == "tuple"
	count(uncovered(rel)) == 0
	not protected_tuple(rel)
	msg := sprintf(
		"%s: %s are independently versioned and no member has an upper bound, is held back by ignore rules, or carries a breaking-class ignore rule across all its declarations; add a documented bound, join the members to one multi-ecosystem group, or record a reviewed exception%s",
		[rel.id, member_list(rel), migration_note(rel)],
	)
}

# ---------------------------------------------------------------------------
# DF-005: a documented compatibility bound must not be relaxed silently.
# ---------------------------------------------------------------------------

bound_path(bound) := bound.manifest if {
	bound.manifest != null
}

bound_path(bound) := "." if {
	bound.manifest == null
}

deny contains finding("DF-005", "noncompliant", bound_path(bound), msg) if {
	some bound in input.protected_bounds
	bound.status in {"relaxed", "open"}
	msg := sprintf(
		"%s: the documented compatibility bound for %s (%s) is no longer held by the declaration (%s); restore the bound or record the migration evidence and update the bound",
		[bound.relationship, bound.package, bound.bound, bound.current],
	)
}

deny contains finding("DF-006", "indeterminate", bound_path(bound), msg) if {
	some bound in input.protected_bounds
	bound.status == "unknown"
	msg := sprintf(
		"%s: the documented compatibility bound for %s (%s) cannot be compared with the declaration; record a parseable bound or an exception",
		[bound.relationship, bound.package, bound.bound],
	)
}

# ---------------------------------------------------------------------------
# DF-004 / DF-009: derived inputs and reviewed action pins.
# ---------------------------------------------------------------------------

selector_location(selector) := selector.location if {
	selector.location != null
}

selector_location(selector) := selector.path if {
	selector.location == null
}

bad_selector(selector) if {
	selector.status != "resolved"
}

mismatched(selector) if {
	selector.matches == false
	selector.override == null
}

unreviewed_override(selector) if {
	selector.override != null
	selector.override_rationale == null
}

unreviewed_override(selector) if {
	selector.override != null
	selector.override_review_trigger == null
}

deny contains finding("DF-004", "noncompliant", path, msg) if {
	some derived in input.derived_inputs
	derived.canonical.status == "resolved"
	some selector in derived.selectors
	mismatched(selector)
	path := selector_location(selector)
	msg := sprintf(
		"%s: %s no longer matches the canonical value %s (read %s); update the occurrence, or record an override with a rationale and review trigger",
		[
			derived.id,
			selector.kind,
			derived.canonical.value,
			selector.value,
		],
	)
}

deny contains finding("DF-006", "indeterminate", path, msg) if {
	some derived in input.derived_inputs
	derived.canonical.status != "resolved"
	path := derived.canonical.location
	msg := sprintf(
		"%s: the canonical value cannot be established (%s); declare it or record an exception",
		[derived.id, derived.canonical.reason],
	)
}

deny contains finding("DF-006", "indeterminate", path, msg) if {
	some derived in input.derived_inputs
	some selector in derived.selectors
	bad_selector(selector)
	path := selector_location(selector)
	msg := sprintf(
		"%s: %s cannot be judged (%s); make the value literal or record an override",
		[derived.id, selector.kind, selector.reason],
	)
}

deny contains finding("DF-006", "indeterminate", path, msg) if {
	some derived in input.derived_inputs
	some selector in derived.selectors
	unreviewed_override(selector)
	path := selector_location(selector)
	msg := sprintf(
		"%s: the override on %s needs both a rationale and a review trigger",
		[derived.id, selector.kind],
	)
}

deny contains finding("DF-009", "noncompliant", path, msg) if {
	some derived in input.derived_inputs
	derived.canonical.kind == "action"
	derived.canonical.status == "resolved"
	not reviewed_sha(derived)
	path := derived.canonical.location
	msg := sprintf(
		"%s: action pin %s is not in the reviewed SHAs; record the review that approved this pin",
		[derived.id, derived.canonical.value],
	)
}

reviewed_sha(derived) if {
	some reviewed in derived.reviewed
	reviewed.sha == derived.canonical.value
}

# ---------------------------------------------------------------------------
# DF-008: native ecosystem monitoring and duplicate pip/uv coverage.
# ---------------------------------------------------------------------------

config_readable if {
	input.config != null
	input.config.error == null
}

deny contains finding("DF-008", "noncompliant", fact.manifest, msg) if {
	config_readable
	some fact in input.monitoring
	fact.duplicate
	msg := sprintf(
		"%s is covered by both pip and uv entries; keep one native entry so the same requirements are not updated twice",
		[fact.manifest],
	)
}

deny contains finding("DF-008", "noncompliant", fact.manifest, msg) if {
	config_readable
	some fact in input.monitoring
	not fact.native in fact.covered
	msg := sprintf(
		"%s is not monitored by its native %s ecosystem (covered by: %s); add a native %s entry",
		[
			fact.manifest,
			fact.native,
			concat(", ", sort(fact.covered)),
			fact.native,
		],
	)
}