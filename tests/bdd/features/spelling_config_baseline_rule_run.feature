Feature: Spelling configuration baseline rule run
  Auditing a local checkout against the spelling-config-baseline rule package,
  through the command an operator actually runs. The real policy evaluates the
  real Makefile facts, workflows, overlay and .gitignore: these scenarios
  exercise the whole boundary from the checkout to the rendered table.

  Scenario: a checkout without a spelling setup is audited clean
    Given a checkout with no spelling setup
    When I audit the checkout for its spelling gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: the pinned gate is audited clean
    Given a checkout whose spelling target runs the pinned gate
    When I audit the checkout for its spelling gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: concordat's own spelling gate is audited clean
    When I audit concordat's own checkout for its spelling gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a release below the floor is reported
    Given a checkout whose spelling target pins v0.1.1
    When I audit the checkout for its spelling gate
    Then the audit exit status is 1
    And the audit output reports "PD-007" "below the floor v0.1.2"

  Scenario: the legacy drift-check setup is reported
    Given a checkout with the legacy drift-check setup
    When I audit the checkout for its spelling gate
    Then the audit exit status is 1
    And the audit output reports "PD-007" "drift-checks typos.toml"
    And the audit output reports "PD-008" "TYPOS_VERSION"
    And the audit output reports "PD-009" "scripts/typos_rollout_check.py"
