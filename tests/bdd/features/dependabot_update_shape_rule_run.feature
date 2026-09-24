Feature: Dependabot update-shape rule run
  Auditing a local checkout against the dependabot-update-shape rule package,
  through the command an operator actually runs. The real policy evaluates the
  real configuration and the local actions on disk: these scenarios exercise
  the whole boundary from the checkout to the rendered table.

  Scenario: a checkout without Dependabot is audited clean
    Given a checkout with no Dependabot configuration
    When I audit the checkout for its Dependabot update shape
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: the estate shape is audited clean
    Given a checkout whose Dependabot configuration has the estate shape
    When I audit the checkout for its Dependabot update shape
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a weekly entry with a wildcard group is reported
    Given a checkout whose cargo entry runs weekly with an ungrouped-majors wildcard
    When I audit the checkout for its Dependabot update shape
    Then the audit exit status is 1
    And the audit output reports "schedule.interval is weekly, not daily"
    And the audit output reports "is not the catch-all"

  Scenario: an uncovered composite action is reported
    Given a checkout whose composite action is not in the github-actions directories
    When I audit the checkout for its Dependabot update shape
    Then the audit exit status is 1
    And the audit output reports "do not cover /.github/actions/setup"

  Scenario: an unreadable configuration is indeterminate
    Given a checkout whose Dependabot configuration is not YAML
    When I audit the checkout for its Dependabot update shape
    Then the audit exit status is 1
    And the audit output reports "could not be read"
