Feature: Nested cargo offline tests
  Audits the cargo a repository's own tests start, through the operator's CLI
  and without running Cargo or touching the network. The rule ships at warning
  severity, so a spawn that can reach the network is reported without stopping
  a gate.

  Scenario Outline: A test's own cargo run is audited for network reach
    Given a test that runs cargo with "<evidence>" offline evidence
    When I audit the nested cargo offline tests
    Then the nested cargo offline exit status is <status>
    And the nested cargo offline output contains "<text>"

    Examples:
      | evidence | status | text           |
      | frozen   | 0      | compliant      |
      | none     | 0      | NC-007         |

  Scenario: A test entry point with no prefetch is noncompliant
    Given a test that runs cargo with "frozen" offline evidence
    And a Makefile whose test target fetches nothing first
    When I audit the nested cargo offline tests
    Then the nested cargo offline exit status is 0
    And the nested cargo offline output contains "NC-008"

  Scenario: A test entry point with a success-gated prefetch is compliant
    Given a test that runs cargo with "frozen" offline evidence
    And a Makefile whose test target fetches first
    When I audit the nested cargo offline tests
    Then the nested cargo offline exit status is 0
    And the nested cargo offline output contains "compliant"

  Scenario: Evidence the reader cannot read is indeterminate, never a pass
    Given a test source the reader cannot decode
    When I audit the nested cargo offline tests
    Then the nested cargo offline exit status is 0
    And the nested cargo offline output contains "indeterminate"
    And the nested cargo offline output contains "EN-001"
