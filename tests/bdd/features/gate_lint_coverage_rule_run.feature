Feature: Gate lint coverage rule run
  Auditing a local checkout against the gate-lint-coverage rule package, through
  the command an operator actually runs. The real policy evaluates the facts
  read from real manifests, Makefiles and workflows: these scenarios exercise the
  whole boundary from the checkout to the rendered table, for each of the three
  shapes a Rust repository takes.

  Scenario: a checkout with no Cargo manifest is not audited
    Given a checkout with no Cargo manifest
    When I audit the checkout for gate lint coverage
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a workspace whose gate checks everything is clean
    Given a workspace whose lint target checks every target and feature and builds the docs
    When I audit the checkout for gate lint coverage
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: Clippy without all targets is reported
    Given a workspace whose clippy leaves out the test targets
    When I audit the checkout for gate lint coverage
    Then the audit output reports "LC-001" "no clippy command checks all targets"

  Scenario: a missing feature lane is reported
    Given a workspace whose clippy checks only one of two features
    When I audit the checkout for gate lint coverage
    Then the audit output reports "LC-002" "feature a/slow"

  Scenario: a command that does not deny warnings is reported
    Given a workspace whose clippy does not deny warnings
    When I audit the checkout for gate lint coverage
    Then the audit output reports "LC-003" "does not deny warnings"

  Scenario: a gate with no cargo doc is reported for a root package that grew members
    Given a root package with a member whose gate never runs cargo doc
    When I audit the checkout for gate lint coverage
    Then the audit output reports "LC-004" "reaches no `cargo doc`"

  Scenario: a Python extension gated through manifest-path is clean
    Given a Python project whose rust_extension crate is linted and documented by manifest path
    When I audit the checkout for gate lint coverage
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a Python extension whose lanes come from a script is indeterminate
    Given a Python project whose lint lanes are built by a script
    When I audit the checkout for gate lint coverage
    Then the audit output reports "EN-001" "scripts/lint_rust_lanes.py"
    And the audit output reports "LC-004" "rust_extension/Cargo.toml"
