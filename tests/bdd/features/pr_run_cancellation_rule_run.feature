Feature: Pull-request run cancellation rule run
  Auditing a local checkout against the pr-run-cancellation rule package,
  through the command an operator actually runs. The real policy evaluates the
  real workflow documents: these scenarios exercise the whole boundary from
  the checkout on disk to the rendered table.

  Scenario: a checkout whose pull-request workflows cancel superseded runs
    Given a checkout whose workflows cancel superseded pull-request runs
    When I audit the checkout for pull-request run cancellation
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a checkout that cancels on each job
    Given a checkout whose jobs each cancel superseded runs
    When I audit the checkout for pull-request run cancellation
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a pull-request workflow without concurrency is advisory
    Given a checkout whose pull-request workflow has no concurrency
    When I audit the checkout for pull-request run cancellation
    Then the audit exit status is 0
    And the audit output reports "RC-001" for "ci.yml"
    And the audit output reports severity "warning"

  Scenario: a group that spans pull requests is advisory
    Given a checkout whose concurrency group spans pull requests
    When I audit the checkout for pull-request run cancellation
    Then the audit exit status is 0
    And the audit output reports "RC-002" for "ci.yml"

  Scenario: a malformed workflow is indeterminate
    Given a checkout whose workflow is not valid YAML
    When I audit the checkout for pull-request run cancellation
    Then the audit exit status is 0
    And the audit output reports "EN-001" for "ci.yml"
    And the audit output reports verdict "indeterminate"

  Scenario: a workflow directory outside the checkout is refused
    Given a checkout whose workflow directory links outside it
    When I audit the checkout for pull-request run cancellation
    Then the audit exit status is 2
    And the audit stderr explains that the directory resolves outside the checkout