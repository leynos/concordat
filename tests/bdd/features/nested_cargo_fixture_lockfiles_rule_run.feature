Feature: Nested cargo fixture lockfiles
  Audits whether a nested-build fixture resolves the same dependency graph as
  the workspace, through the operator's CLI and without running Cargo or
  touching the network.

  Scenario Outline: A fixture's lockfile is audited against the workspace lock
    Given a workspace whose lock resolves "serde"
    And a fixture crate with a "<lock>" lockfile
    When I audit the nested cargo fixture lockfiles
    Then the nested cargo exit status is <status>
    And the nested cargo output contains "<finding>"

    Examples:
      | lock     | status | finding       |
      | matching | 0      | compliant     |
      | drifting | 1      | NC-001        |
      | missing  | 1      | NC-002        |
      | broken   | 1      | indeterminate |
