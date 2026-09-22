Feature: Main-owned CodeScene coverage rule run
  Auditing a local checkout against the main-owned-codescene-coverage rule
  package, through the command an operator actually runs. The real policy
  evaluates the real workflow documents: these scenarios exercise the whole
  boundary from the checkout on disk to the rendered table.

  Scenario: a compliant checkout is audited clean
    Given a checkout whose coverage wiring satisfies CV-005
    When I audit the checkout for CodeScene coverage
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a pull-request lane that contacts CodeScene is reported
    Given a checkout whose pull-request lane invokes CodeScene
    When I audit the checkout for CodeScene coverage
    Then the audit exit status is 1
    And the audit output reports "pull-request workflow invokes CodeScene" for "ci.yml"

  Scenario: an unguarded publisher is reported
    Given a checkout whose publisher upload step has no ref guard
    When I audit the checkout for CodeScene coverage
    Then the audit exit status is 1
    And the audit output reports "not guarded on github.ref" for "coverage-main.yml"

  Scenario: a workflow directory outside the checkout is refused
    Given a checkout whose workflow directory links outside it
    When I audit the checkout for CodeScene coverage
    Then the audit exit status is 2
    And the audit stderr explains that the directory resolves outside the checkout
