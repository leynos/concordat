Feature: Whitaker exclusions rule run
  Auditing a local checkout against the whitaker-exclusions rule package,
  through the command an operator actually runs. The real policy evaluates the
  facts read from real manifests and dylint.toml files: these scenarios exercise
  the whole boundary from the checkout to the rendered table, for each of the
  three shapes a Rust repository takes.

  Scenario: a checkout with no dylint configuration is not audited
    Given a checkout with no dylint configuration
    When I audit the checkout for Whitaker exclusions
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a path exclusion with a reason is clean
    Given a workspace whose dylint.toml excludes a path with a reason
    When I audit the checkout for Whitaker exclusions
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a path exclusion without a reason is reported
    Given a workspace whose dylint.toml excludes a path without a reason
    When I audit the checkout for Whitaker exclusions
    Then the audit output reports "WX-001" "gives no reason"

  Scenario: a whole governed crate excluded from a grown workspace is reported
    Given a root package whose dylint.toml excludes the whole root crate
    When I audit the checkout for Whitaker exclusions
    Then the audit output reports "WX-002" "crate_exclusions"

  Scenario: femtologging's extension excludes three crates under one comment
    Given a Python project whose rust_extension excludes three whole crates
    When I audit the checkout for Whitaker exclusions
    Then the audit output reports "WX-002" "_femtologging_rs, file_handler_tests, heavy"

  Scenario: an exclusion that names nothing is stale
    Given a workspace whose dylint.toml excludes a crate that does not exist
    When I audit the checkout for Whitaker exclusions
    Then the audit output reports "WX-003" "ghost"

  Scenario: a dylint.toml that is not TOML is indeterminate
    Given a workspace whose dylint.toml is not TOML
    When I audit the checkout for Whitaker exclusions
    Then the audit output reports "EN-001" "dylint.toml"
