Feature: Rust lint tables rule run
  Auditing a local checkout against the rust-lint-tables rule package, through
  the command an operator actually runs. The real policy evaluates the facts read
  from real manifests and the canon's baseline data: these scenarios exercise
  the whole boundary from the checkout to the rendered table, for each of the
  three shapes a Rust repository takes.

  Scenario: a checkout with no Cargo manifest is not audited
    Given a checkout with no Cargo manifest
    When I audit the checkout for Rust lint tables
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a workspace designed as one is clean
    Given a virtual workspace whose members all inherit the baseline
    When I audit the checkout for Rust lint tables
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a workspace member that does not inherit is reported
    Given a virtual workspace with a member that sets no lint tables
    When I audit the checkout for Rust lint tables
    Then the audit output reports "LT-001" "package b is in workspace Cargo.toml"
    And the audit output reports "LT-001" "workspace = true"

  Scenario: a workspace grown from one crate whose root does not opt in is reported
    Given a root package whose workspace defines lint tables it does not inherit
    When I audit the checkout for Rust lint tables
    Then the audit output reports "LT-001" "root package"

  Scenario: a Python extension with a thin lint table is reported
    Given a Python project whose rust_extension crate sets four lints
    When I audit the checkout for Rust lint tables
    Then the audit output reports "LT-002" "clippy::missing_assert_message"
    And the audit output reports "LT-003" "unsafe_code"

  Scenario: a Python extension with the full baseline is clean
    Given a Python project whose rust_extension crate carries the full baseline
    When I audit the checkout for Rust lint tables
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a manifest that cannot be read is indeterminate
    Given a virtual workspace with a member whose manifest is not TOML
    When I audit the checkout for Rust lint tables
    Then the audit output reports "EN-001" "crates/b/Cargo.toml"
