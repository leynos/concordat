Feature: Rust test environment rule run
  Auditing a local checkout against the rust-test-environment rule package,
  through the command an operator actually runs. The real policy evaluates the
  facts read from the real manifests, Clippy configuration and Rust sources:
  these scenarios exercise the whole boundary from the checkout to the
  rendered table.

  Scenario: a checkout with no Cargo manifest is not audited
    Given a checkout with no Cargo manifest
    When I audit the checkout for its test environment
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a guarded crate with a site-level expectation is clean
    Given a crate whose Clippy configuration disallows the environment calls
    When I audit the checkout for its test environment
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a crate with no Clippy configuration is reported
    Given a crate with no Clippy configuration
    When I audit the checkout for its test environment
    Then the audit exit status is 1
    And the audit output reports "TE-001" "std::env::set_current_dir"

  Scenario: a wrapper crate whose entry points are not disallowed is reported
    Given a crate that depends on temp-env without disallowing its functions
    When I audit the checkout for its test environment
    Then the audit exit status is 1
    And the audit output reports "TE-002" "temp_env::with_var"

  Scenario: an allow attribute is reported with its file
    Given a crate that allows the lint in a test file
    When I audit the checkout for its test environment
    Then the audit exit status is 1
    And the audit output reports "TE-003" "tests/it.rs"

  Scenario: a lints table that allows the lint is reported
    Given a crate whose Cargo manifest allows the lint
    When I audit the checkout for its test environment
    Then the audit exit status is 1
    And the audit output reports "TE-004" "[lints.clippy]"

  Scenario: a Clippy configuration that cannot be read is reported as indeterminate
    Given a crate whose Clippy configuration is not valid TOML
    When I audit the checkout for its test environment
    Then the audit exit status is 1
    And the audit output reports "EN-001" "clippy.toml"
