Feature: Markdown formatting baseline rule run
  Auditing a local checkout against the markdown-formatting-baseline rule package.

  Scenario: compliant repository
    Given a Markdown checkout laid out from the "compliant" fixture scenario
    And makeutil reports the Markdown "compliant" fixture facts
    And conftest reports no Markdown failures
    When I run the Markdown rule against the checkout
    Then the Markdown rule exit status is 0
    And the table output reports the Markdown rule as compliant

  Scenario: check-fmt does not run mdtablefix and fmt uses the wrapper
    Given a Markdown checkout laid out from the "mdformat_wrapper" fixture scenario
    And makeutil reports the Markdown "mdformat_wrapper" fixture facts
    And conftest reports the mdformat wrapper failures
    When I run the Markdown rule against the checkout
    Then the Markdown rule exit status is 1
    And the output contains a PD-003 finding citing Makefile line 8
    And the output contains a PD-002 finding naming "check-fmt"

  Scenario: markdownlint configuration cannot be decoded
    Given a Markdown checkout laid out from the "config_malformed" fixture scenario
    And makeutil reports the Markdown "config_malformed" fixture facts
    And conftest reports the malformed configuration failure
    When I run the Markdown rule against the checkout
    Then the Markdown rule exit status is 1
    And the output reports PD-005 as indeterminate

  Scenario: CI lints Markdown from a shell step
    Given a Markdown checkout laid out from the "workflow_shell_lint" fixture scenario
    And makeutil reports the Markdown "workflow_shell_lint" fixture facts
    And conftest reports the shell lint failure
    When I run the Markdown rule against the checkout
    Then the Markdown rule exit status is 1
    And the output contains a PD-006 finding naming the CI workflow

  Scenario: checkout without Markdown is compliant without a Makefile parse
    Given a Markdown checkout laid out from the "no_markdown" fixture scenario
    And conftest reports no Markdown failures
    When I run the Markdown rule against the checkout
    Then the Markdown rule exit status is 0
    And the table output reports the Markdown rule as compliant
