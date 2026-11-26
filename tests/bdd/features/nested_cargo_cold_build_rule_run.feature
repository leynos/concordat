Feature: Nested cargo cold build
  Audits the nested builds a repository's tests start inside their timed
  bodies, through the operator's CLI and without running Cargo or touching the
  network. The rule ships at warning severity, so a build with no declared
  preparation is reported without stopping a gate.

  Scenario: A timed test that compiles with no declared preparation is reported
    Given a test that drives trybuild
    When I audit the nested cargo cold build
    Then the nested cargo cold build exit status is 0
    And the nested cargo cold build output contains "NC-010"
    And the nested cargo cold build output contains "noncompliant"

  Scenario: A nextest setup script that covers the test declares the preparation
    Given a test that drives trybuild
    And a nextest setup script that covers every test
    When I audit the nested cargo cold build
    Then the nested cargo cold build exit status is 0
    And the nested cargo cold build output contains "nested-cargo-cold-build: compliant"

  Scenario: A nested build into a temporary target directory is reported
    Given a test that builds into a temporary target directory
    When I audit the nested cargo cold build
    Then the nested cargo cold build exit status is 0
    And the nested cargo cold build output contains "NC-009"

  Scenario: A repository that starts no nested build is compliant
    Given a plain library with a plain test
    When I audit the nested cargo cold build
    Then the nested cargo cold build exit status is 0
    And the nested cargo cold build output contains "nested-cargo-cold-build: compliant"

  Scenario: A second test pass the Makefile does not prepare is reported
    Given a test that drives trybuild
    And a Makefile that prepares only the first of two test passes
    When I audit the nested cargo cold build
    Then the nested cargo cold build exit status is 0
    And the nested cargo cold build output contains "NC-010"
    And the nested cargo cold build output contains "Makefile:6"

  Scenario: Evidence the reader cannot read is indeterminate, never a pass
    Given a test source the reader cannot decode
    When I audit the nested cargo cold build
    Then the nested cargo cold build exit status is 0
    And the nested cargo cold build output contains "indeterminate"
    And the nested cargo cold build output contains "EN-001"
