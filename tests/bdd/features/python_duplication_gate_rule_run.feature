Feature: Python duplication gate rule run
  Auditing a local checkout against the python-duplication-gate rule package,
  through the command an operator actually runs. The real policy evaluates the
  facts read from the real pyproject.toml and the real tree: these scenarios
  exercise the whole boundary from the checkout to the rendered table.

  Scenario: a checkout that does not use the duplication gate is audited clean
    Given a checkout with no nose table
    When I audit the checkout for its duplication gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: an uncapped scan that leaves test trees out is clean
    Given a checkout whose scan is uncapped and excludes its test tree
    When I audit the checkout for its duplication gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a capped scan is reported
    Given a checkout whose scan is capped at 30 families
    When I audit the checkout for its duplication gate
    Then the audit exit status is 1
    And the audit output reports "NS-001" "top is 30"

  Scenario: a scanned test tree is reported
    Given a checkout whose scan includes its test tree
    When I audit the checkout for its duplication gate
    Then the audit exit status is 1
    And the audit output reports "NS-002" "pkg/tests/test_mod.py"

  Scenario: a root that selects nothing is reported
    Given a checkout with a scan root that selects no Python file
    When I audit the checkout for its duplication gate
    Then the audit exit status is 1
    And the audit output reports "NS-003" "selects no Python file"

  Scenario: a settings table that cannot be read is reported as indeterminate
    Given a checkout whose scan roots are not a list
    When I audit the checkout for its duplication gate
    Then the audit exit status is 1
    And the audit output reports "NS-004" "roots must be a non-empty list"
