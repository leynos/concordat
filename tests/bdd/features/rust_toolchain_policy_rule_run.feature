Feature: Rust toolchain policy rule run
  Auditing a local checkout against the rust-toolchain-policy rule package,
  through the command an operator actually runs. The real policy evaluates the
  real pin, manifest, Dependabot configuration and workflows: these scenarios
  exercise the whole boundary from the checkout to the rendered table.

  Scenario: a checkout without Rust is outside the rule
    Given a checkout with no Rust code
    When I audit the checkout for its toolchain policy
    Then the toolchain audit exit status is 0
    And the toolchain audit table reports zero findings

  Scenario: a fresh pin that Dependabot moves is audited clean
    Given a checkout pinned to a fresh nightly that Dependabot moves
    When I audit the checkout for its toolchain policy
    Then the toolchain audit exit status is 0
    And the toolchain audit table reports zero findings

  Scenario: an old nightly that nothing moves is reported
    Given a checkout pinned to an old nightly with no Dependabot entry
    When I audit the checkout for its toolchain policy
    Then the toolchain audit exit status is 1
    And the toolchain audit output reports "RT-023" and "days old"
    And the toolchain audit output reports "RT-024" and "nothing moves"

  Scenario: a floating channel is reported
    Given a checkout pinned to the floating stable channel
    When I audit the checkout for its toolchain policy
    Then the toolchain audit exit status is 1
    And the toolchain audit output reports "RT-022" and "is not pinned exactly"

  Scenario: an MSRV that CI never installs is reported
    Given a checkout declaring an MSRV that no workflow exercises
    When I audit the checkout for its toolchain policy
    Then the toolchain audit exit status is 1
    And the toolchain audit output reports "RT-026" and "rust-version 1.85 is declared"

  Scenario: a recorded exception exempts the age and movability clauses
    Given a checkout with an old nightly and a toolchain policy exception naming it
    When I audit the checkout for its toolchain policy
    Then the toolchain audit exit status is 0
    And the toolchain audit table reports zero findings
