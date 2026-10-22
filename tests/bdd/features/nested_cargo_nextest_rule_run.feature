Feature: Nested cargo nextest configuration
  Audits whether tests that spawn a compiler run in a serialized, bounded
  nextest group, through the operator's CLI and without running Cargo or
  nextest.

  Scenario Outline: A compiler-spawning test is audited against the nextest config
    Given a crate whose test runs "<command>"
    And a nextest configuration that "<config>"
    When I audit the nested cargo nextest configuration
    Then the nested nextest exit status is <status>
    And the nested nextest output contains "<finding>"

    Examples:
      | command     | config   | status | finding   |
      | cargo build | bounds   | 0      | compliant |
      | cargo build | is absent | 1     | NC-004    |
      | cargo build | skips it | 1      | NC-005    |
      | cargo check | bounds   | 0      | compliant |
      | cargo metadata | is absent | 0   | compliant |
