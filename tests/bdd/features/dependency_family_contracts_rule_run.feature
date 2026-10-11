Feature: Dependency-family contracts rule run
  Auditing a local checkout against the dependency-family-contracts rule
  package, through the command an operator actually runs. The real policy
  evaluates the facts read from a real manifest, lock and Dependabot
  configuration: these scenarios exercise the whole boundary from the checkout
  to the rendered table.

  Scenario: a checkout declaring no related packages is audited clean
    Given a checkout that declares no related packages
    When I audit the checkout for its dependency families
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a PyO3 family taken together by one group is audited clean
    Given a checkout whose PyO3 packages share a narrow Dependabot group
    When I audit the checkout for its dependency families
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a PyO3 family left to the catch-all is reported
    Given a checkout whose PyO3 packages fall to the catch-all group
    When I audit the checkout for its dependency families
    Then the audit exit status is 1
    And the audit output reports "DF-001"
    And the audit output reports "are not taken together by one Dependabot group"

  Scenario: a recorded exception silences the relationship it names
    Given a checkout whose PyO3 packages fall to the catch-all group with a reviewed exception
    When I audit the checkout for its dependency families
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a checkout with no Dependabot configuration is indeterminate
    Given a checkout that declares PyO3 but has no Dependabot configuration
    When I audit the checkout for its dependency families
    Then the audit exit status is 1
    And the audit output reports "DF-006"
    And the audit output reports "no decodable Dependabot configuration"

  Scenario: exactly pinned Pylint and Astroid sharing a group are audited clean
    Given a checkout pinning Pylint and Astroid exactly with a shared Dependabot group
    When I audit the checkout for its dependency families
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: exactly pinned Pylint and Astroid left to the catch-all are reported
    Given a checkout pinning Pylint and Astroid exactly under the catch-all group
    When I audit the checkout for its dependency families
    Then the audit exit status is 1
    And the audit output reports "DF-001"
    And the audit output reports "pylint-astroid"

  Scenario: a nested crate that no declaration governs is indeterminate
    Given a checkout whose PyO3 crate sits in an undeclared subdirectory
    When I audit the checkout for its dependency families
    Then the audit exit status is 1
    And the audit output reports "DF-006"
    And the audit output reports "outside the governed Rust surfaces"
