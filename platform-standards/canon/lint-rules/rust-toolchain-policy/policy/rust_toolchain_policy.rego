# RT-022 to RT-027: the Rust toolchain is pinned exactly, kept fresh, movable
# by Dependabot, and its MSRV is declared and exercised.
#
# The Python envelope has already read the pin, the Cargo packages, the
# Dependabot `rust-toolchain` entries and the toolchains the workflows name,
# and supplies "today" as `evaluated_on` so the nightly-age clause is a pure
# comparison. A repository with no governed Rust surface is outside the rule.
# A document the envelope could not read is indeterminate, never clean.
package canon.lint_rules.rust_toolchain_policy

import rego.v1

finding(rule_id, severity, verdict, path, message) := {
  "rule_id": rule_id,
  "severity": severity,
  "verdict": verdict,
  "path": path,
  "line": 0,
  "msg": message,
}

pin_path := "rust-toolchain.toml"

dependabot_path := ".github/dependabot.yml"

workflows_path := ".github/workflows"

nanoseconds_per_day := 86400000000000

envelope_ok if {
  input.schema_version == 1
  input.kind == "policy-input/rust-toolchain-policy"
  is_boolean(input.applicable)
}

applies if {
  envelope_ok
  input.applicable == true
}

pin := input.toolchain

has_pin if {
  is_object(pin)
  object.get(pin, "parse_error", null) == null
  is_string(object.get(pin, "channel", null))
}

channel := pin.channel if has_pin

channel_kind := pin.channel_kind if has_pin

deny contains finding("RT-022", "error", "indeterminate", pin_path, msg) if {
  not envelope_ok
  msg := "policy input is not a policy-input/rust-toolchain-policy envelope at schema version 1"
}

# -- RT-022: the pin is exact ---------------------------------------------------

deny contains finding("RT-022", "error", "indeterminate", pin_path, msg) if {
  applies
  is_object(pin)
  reason := object.get(pin, "parse_error", null)
  reason != null
  msg := sprintf("rust-toolchain.toml could not be read: %s", [reason])
}

deny contains finding("RT-022", "error", "noncompliant", pin_path, msg) if {
  applies
  pin == null
  msg := "no rust-toolchain.toml: the toolchain is whatever the machine has, so the build and the Dependabot bump have nothing to agree on"
}

deny contains finding("RT-022", "error", "noncompliant", pin_path, msg) if {
  applies
  is_object(pin)
  object.get(pin, "parse_error", null) == null
  not is_string(object.get(pin, "channel", null))
  msg := "rust-toolchain.toml names no channel"
}

exact_kinds := {"version", "nightly-dated"}

deny contains finding("RT-022", "error", "noncompliant", pin_path, msg) if {
  applies
  has_pin
  not channel_kind in exact_kinds
  msg := sprintf("channel %q is not pinned exactly: name a release (1.93.1) or a dated nightly (nightly-2026-03-26), because rustup resolves a floating channel at install time and Dependabot cannot move it", [channel])
}

# -- Exceptions -----------------------------------------------------------------

# An exception is current only while it names the channel the repository is
# pinned to, so every move of the pin has to re-justify it. A repository whose
# nightly is coupled to compiler internals cannot build on a stable release, so
# the exception also stands in for an MSRV (RT-025): there is no stable
# toolchain to declare.
exempt if {
  has_pin
  some document in input.exceptions
  some section in document.sections
  channel in section.channels_named
}

# -- RT-023: a nightly is fresh -------------------------------------------------

window_days := data.parameters.window_days

pin_age_ns := time.parse_ns("2006-01-02", evaluated_on) - time.parse_ns("2006-01-02", trim_prefix(channel, "nightly-")) if {
  has_pin
  channel_kind == "nightly-dated"
}

evaluated_on := input.evaluated_on

deny contains finding("RT-023", "error", "noncompliant", pin_path, msg) if {
  applies
  not exempt
  pin_age_ns > window_days * nanoseconds_per_day
  msg := sprintf("%s is %d days old, beyond the %d-day window", [channel, pin_age_ns / nanoseconds_per_day, window_days])
}

# -- RT-024: Dependabot can move the pin ----------------------------------------

dependabot := input.dependabot

deny contains finding("RT-024", "error", "indeterminate", dependabot_path, msg) if {
  applies
  has_pin
  not exempt
  reason := object.get(dependabot, "error", null)
  reason != null
  msg := sprintf("Dependabot configuration could not be read: %s", [reason])
}

moves_pin if {
  some entry in dependabot.entries
  "/" in entry.directories
  entry.interval in data.parameters.dependabot_intervals
}

deny contains finding("RT-024", "error", "noncompliant", dependabot_path, msg) if {
  applies
  has_pin
  not exempt
  object.get(dependabot, "error", null) == null
  not moves_pin
  msg := sprintf("no rust-toolchain update entry on / with an interval of %s: nothing moves the %s pin", [concat(" or ", data.parameters.dependabot_intervals), channel])
}

# -- RT-025: libraries declare an MSRV ------------------------------------------

declares_msrv(crate) if crate.rust_version != null

declares_msrv(crate) if {
  crate.inherits_workspace
  is_string(input.workspace_rust_version)
}

deny contains finding("RT-025", "warning", "noncompliant", crate.path, msg) if {
  applies
  not exempt
  some crate in input.crates
  crate.is_publishable
  crate.has_lib
  not declares_msrv(crate)
  msg := sprintf("publishable library crate %v declares no rust-version, so its users cannot tell which compiler it supports", [object.get(crate, "name", crate.path)])
}

# -- RT-026: a declared MSRV is exercised ---------------------------------------

minor(version) := concat(".", array.slice(split(version, "."), 0, 2))

declared_msrvs contains version if {
  some crate in input.crates
  version := crate.rust_version
  version != null
}

declared_msrvs contains input.workspace_rust_version if is_string(input.workspace_rust_version)

exercised(version) if minor(version) in input.ci.numeric_minors

exercised(version) if input.pin_minor == minor(version)

exercised(_) if input.ci.msrv_job

exercised(_) if input.ci.reads_manifest_msrv

deny contains finding("RT-026", "error", "indeterminate", workflows_path, msg) if {
  applies
  count(declared_msrvs) > 0
  some error in input.ci.errors
  msg := sprintf("workflow could not be read, so the MSRV cannot be matched to CI: %s", [error])
}

deny contains finding("RT-026", "error", "noncompliant", "Cargo.toml", msg) if {
  applies
  some version in declared_msrvs
  not exercised(version)
  count(input.ci.errors) == 0
  msg := sprintf("rust-version %s is declared but no workflow installs that toolchain, runs cargo-msrv or has an msrv job", [version])
}

# -- RT-027: CI does not compete with the pin -----------------------------------

deny contains finding("RT-027", "error", "indeterminate", workflows_path, msg) if {
  applies
  has_pin
  some error in input.ci.errors
  msg := sprintf("workflow could not be read, so CI cannot be compared with the pin: %s", [error])
}

deny contains finding("RT-027", "error", "noncompliant", workflows_path, msg) if {
  applies
  has_pin
  some nightly in input.ci.dated_nightlies
  nightly != channel
  msg := sprintf("a workflow names %s but rust-toolchain.toml pins %s: CI and the file must not disagree about the toolchain", [nightly, channel])
}
