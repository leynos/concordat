Feature: documentation presence rule run
  Auditing a local checkout against the documentation-presence rule package,
  through the command an operator actually runs. The real policy evaluates the
  real Makefile facts (parsed by the pinned makeutil), the real Python tree and
  the real pyproject.toml: these scenarios exercise the whole boundary from the
  checkout to the rendered table.

  Scenario: a checkout that owns no Python is audited clean
    Given a checkout that owns no Python
    When I audit the checkout for its documentation gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: an exact interrogate gate in lint over every Python directory is clean
    Given a checkout with an exact interrogate gate over every Python directory
    When I audit the checkout for its documentation gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a lint recipe with no interrogate run is reported
    Given a checkout whose lint recipe never runs interrogate
    When I audit the checkout for its documentation gate
    Then the audit exit status is 1
    And the audit output reports "DG-001" "reaches no interrogate run"

  Scenario: a gate that skips the tests and relaxes nested functions is reported
    Given a checkout whose gate skips the tests and ignores nested functions
    When I audit the checkout for its documentation gate
    Then the audit exit status is 1
    And the audit output reports "DG-001" "outside every interrogate run"
    And the audit output reports "DG-001" "--ignore-nested-functions"

  Scenario: a gate that trusts the rounded percentage is reported
    Given a checkout whose gate does not fail on a MISSED row
    When I audit the checkout for its documentation gate
    Then the audit exit status is 1
    And the audit output reports "DG-002" "verbose level 0"
    And the audit output reports "DG-002" "does not fail on a MISSED row"

  Scenario: a Rust workspace that denies missing_docs under a clippy gate is clean
    Given a Rust workspace whose members inherit a missing_docs deny
    When I audit the checkout for its documentation gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a Rust crate that does not deny missing_docs is reported
    Given a Rust crate with no missing_docs level
    When I audit the checkout for its documentation gate
    Then the audit exit status is 1
    And the audit output reports "DG-003" "does not deny `missing_docs`"

  Scenario: a Rust crate whose lint never runs clippy is reported
    Given a Rust crate that denies missing_docs but whose lint never runs clippy
    When I audit the checkout for its documentation gate
    Then the audit exit status is 1
    And the audit output reports "DG-004" "reaches no clippy run"

  Scenario: a clippy gate run through a recorded wrapper script is clean
    Given a Rust crate whose lint runs clippy only through a recorded wrapper script
    When I audit the checkout for its documentation gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a recorded wrapper script that no recipe runs is reported
    Given a Rust crate whose recorded wrapper script is never run by lint
    When I audit the checkout for its documentation gate
    Then the audit exit status is 1
    And the audit output reports "DG-004" "not run by any command"

  Scenario: a clippy gate wrapper without a record is reported
    Given a Rust crate whose lint runs a wrapper script with no recorded exception
    When I audit the checkout for its documentation gate
    Then the audit exit status is 1
    And the audit output reports "DG-004" "no recorded clippy-gate exception"
