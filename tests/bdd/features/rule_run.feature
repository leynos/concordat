Feature: Rust Makefile baseline rule run
  Auditing a local checkout against the rust-makefile-baseline rule package.

  Scenario: compliant repository
    Given a checkout with a root Cargo.toml
    And makeutil reports the "compliant" fixture facts
    And conftest reports no failures
    When I run the rule against the checkout
    Then the exit status is 0
    And the table output reports zero findings

  # `WHITAKER ?= whitaker` is the sanctioned estate pattern: a local override
  # is permitted because CI installs the real binary. It is not a finding.
  Scenario: overridable gate variable is compliant
    Given a checkout with a root Cargo.toml
    And makeutil reports the "overridable_gate" fixture facts
    And conftest reports no failures
    When I run the rule against the checkout
    Then the exit status is 0
    And the table output reports zero findings

  Scenario: soft-skipped lint gate
    Given a checkout with a root Cargo.toml
    And makeutil reports the "soft_skip" fixture facts
    And conftest reports the soft-skip failure
    When I run the rule against the checkout
    Then the exit status is 1
    And the output contains a QG-001 finding citing Makefile line 12

  Scenario: include renders the gate unprovable
    Given a checkout with a root Cargo.toml
    And makeutil reports the "with_include" fixture facts
    And conftest reports the include indeterminate failure
    When I run the rule against the checkout
    Then the exit status is 1
    And the output reports QG-001 as indeterminate

  Scenario: missing canonical target
    Given a checkout with a root Cargo.toml
    And makeutil reports the "missing_target" fixture facts
    And conftest reports the missing-target failures
    When I run the rule against the checkout
    Then the exit status is 1
    And the output contains an FP-003 finding naming the "lint" target

  Scenario: makeutil is not installed
    Given a checkout with a root Cargo.toml and a root Makefile
    And no makeutil executable is available
    When I run the rule against the checkout
    Then the exit status is 2
    And stderr explains that makeutil is required
