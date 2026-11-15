Feature: Rust prover tooling
  Audits how a repository that uses a prover obtains it, through the
  operator's CLI and without running a workflow.

  Scenario Outline: A repository's prover wiring is audited
    Given a repository that "<wiring>"
    When I audit the prover tooling
    Then the prover tooling exit status is <status>
    And the prover tooling output contains "<finding>"

    Examples:
      | wiring                                | status | finding   |
      | installs through the shared action    | 0      | compliant |
      | installs Verus in a workflow step     | 1      | PT-002    |
      | uses the shared action by tag         | 1      | PT-003    |
      | keeps a local installer script        | 1      | PT-004    |
      | holds Kani proofs that nothing runs   | 1      | PT-006    |
      | runs its Kani proofs from a make target | 0    | compliant |
      | uses no prover                        | 0      | compliant |
