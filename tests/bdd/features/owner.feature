Feature: Active GitHub owner management
  The headline configuration file records which GitHub owner namespaces
  all configuration, credentials, cache, and state.

  Scenario: setting and showing the active owner
    When I run "concordat owner use leynos"
    Then the owner command exits with 0
    When I run "concordat owner show"
    Then the owner command exits with 0
    And the owner output is "leynos"

  Scenario: showing the owner before one is configured
    When I run "concordat owner show"
    Then the owner command exits with 1
    And the owner output mentions "concordat owner use"

  Scenario: rejecting an invalid owner name
    When I run "concordat owner use not/valid"
    Then the owner command exits with 1
    And the owner output mentions "invalid GitHub owner"
