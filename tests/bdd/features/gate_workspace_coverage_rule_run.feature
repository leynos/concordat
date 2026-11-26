Feature: Gate workspace coverage rule run
  Auditing a local checkout against the gate-workspace-coverage rule package,
  through the command an operator actually runs. The real policy evaluates the
  facts read from the real manifests, Makefile, workflows and prose: these
  scenarios exercise the whole boundary from the checkout to the rendered
  table.

  Scenario: a checkout with no Cargo manifest is not audited
    Given a checkout with no Cargo manifest
    When I audit the checkout for gate workspace coverage
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a gate that selects the whole workspace is clean
    Given a workspace whose gate runs every tool with --workspace
    When I audit the checkout for gate workspace coverage
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a bare cargo clippy in a root package workspace is reported
    Given a workspace whose lint target runs a bare cargo clippy
    When I audit the checkout for gate workspace coverage
    Then the audit output reports "GC-001" "clippy"
    And the audit output reports "GC-001" "Makefile:4 (lint)"

  Scenario: Whitaker's own --all does not select the workspace
    Given a workspace whose lint target runs whitaker with only its own --all
    When I audit the checkout for gate workspace coverage
    Then the audit output reports "GC-001" "whitaker"

  Scenario: a narrowing example in the documentation is named
    Given a workspace whose lint target runs a bare cargo clippy
    And an AGENTS.md that shows the bare command
    When I audit the checkout for gate workspace coverage
    Then the audit output reports "GC-001" "AGENTS.md:2"

  Scenario: a CI matrix entry is read as its own command
    Given a workspace whose CI matrix runs cargo test without selecting members
    When I audit the checkout for gate workspace coverage
    Then the audit output reports "GC-001" "--features sqlite"

  Scenario: a wrapper script is indeterminate, never guessed
    Given a workspace whose lint target runs a wrapper script for whitaker
    When I audit the checkout for gate workspace coverage
    Then the audit output reports "EN-001" "wrapper script"
