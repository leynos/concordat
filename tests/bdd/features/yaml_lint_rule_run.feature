Feature: YAML lint baseline rule run
  Auditing a local checkout against the yaml-lint-baseline rule package.

  Scenario: compliant repository
    Given a YAML lint checkout laid out from the "compliant" fixture scenario
    And makeutil reports the YAML lint "compliant" fixture facts
    And conftest reports no YAML lint failures
    When I run the YAML lint rule against the checkout
    Then the YAML lint rule exit status is 0
    And the table output reports the YAML lint rule as compliant

  Scenario: the lint gate runs yamllint without a Git file list
    Given a YAML lint checkout laid out from the "discarded_ls_files" fixture scenario
    And makeutil reports the YAML lint "discarded_ls_files" fixture facts
    And conftest reports the discarded file list failure
    When I run the YAML lint rule against the checkout
    Then the YAML lint rule exit status is 1
    And the output contains a YL-001 finding citing Makefile line 8

  Scenario: the estate yamllint base is missing
    Given a YAML lint checkout laid out from the "base_missing" fixture scenario
    And makeutil reports the YAML lint "base_missing" fixture facts
    And conftest reports the missing base failure
    When I run the YAML lint rule against the checkout
    Then the YAML lint rule exit status is 1
    And the output reports YL-003 as noncompliant for the base file

  Scenario: CI only mentions make lint
    Given a YAML lint checkout laid out from the "ci_decoy_echo" fixture scenario
    And makeutil reports the YAML lint "ci_decoy_echo" fixture facts
    And conftest reports the missing lint job failure
    When I run the YAML lint rule against the checkout
    Then the YAML lint rule exit status is 1
    And the output contains a YL-005 finding naming the workflows directory

  Scenario: a workflow cannot be decoded
    Given a YAML lint checkout laid out from the "ci_malformed" fixture scenario
    And makeutil reports the YAML lint "ci_malformed" fixture facts
    And conftest reports the undecodable workflow failure
    When I run the YAML lint rule against the checkout
    Then the YAML lint rule exit status is 1
    And the output reports YL-005 as indeterminate

  Scenario: checkout without YAML is compliant without a Makefile parse
    Given a YAML lint checkout laid out from the "not_applicable" fixture scenario
    And conftest reports no YAML lint failures
    When I run the YAML lint rule against the checkout
    Then the YAML lint rule exit status is 0
    And the table output reports the YAML lint rule as compliant
