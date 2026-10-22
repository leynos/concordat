Feature: Nested cargo fixture manifests
  Audits whether Dependabot reaches the standalone fixture manifests that
  declare registry dependencies, through the operator's CLI and without
  running Cargo or touching the network.

  Scenario Outline: A fixture manifest is audited against the Dependabot config
    Given a workspace with a fixture crate that declares "<dependency>"
    And a Dependabot configuration "<coverage>" the fixture directory
    When I audit the nested cargo fixture manifests
    Then the nested manifest exit status is <status>
    And the nested manifest output contains "<finding>"

    Examples:
      | dependency | coverage | status | finding   |
      | serde      | lists    | 0      | compliant |
      | serde      | omits    | 1      | NC-003    |
      | a path     | omits    | 0      | compliant |
