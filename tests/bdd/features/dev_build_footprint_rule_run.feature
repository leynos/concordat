Feature: Rust development build footprint
  Audits repository defaults through the operator's CLI without running Cargo,
  rustc, rustup, a build, or a network request.

  Scenario Outline: Default dev build storage is audited
    Given a development checkout with "<profile>" settings on "<channel>"
    And its Cargo metadata control is "<metadata>"
    When I audit the development build footprint
    Then the footprint exit status is <status>
    And the footprint output contains "<finding>"

    Examples:
      | profile | channel            | metadata | status | finding       |
      | small   | nightly-2026-05-28 | legacy | 0      | compliant     |
      | default | nightly-2026-05-28 | legacy | 1      | DF-001        |
      | small   | nightly-2026-05-28 | absent    | 1      | DF-003        |
      | small   | stable            | absent    | 0      | compliant     |
      | small   | nightly-2025-06-25 | legacy | 1      | indeterminate |
      | small   | nightly-2026-10-20 | modern   | 0      | compliant     |
      | small   | nightly-2026-07-20 | modern   | 1      | indeterminate |
