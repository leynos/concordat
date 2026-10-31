Feature: Nested cargo CI wiring
  Audits whether a workflow leaves nested cargo builds a warm, shared cache,
  through the operator's CLI and without running a workflow.

  Scenario Outline: A workflow's target directory and wrapper are audited
    Given a workflow that "<wiring>"
    When I audit the nested cargo CI wiring
    Then the nested wiring exit status is <status>
    And the nested wiring output contains "<finding>"

    Examples:
      | wiring                          | status | finding   |
      | keeps the defaults              | 0      | compliant |
      | builds into the runner temp dir | 1      | NC-006    |
      | opts out of the wrapper silently | 1     | NC-006    |
      | opts out and scopes the wrapper | 0      | compliant |
