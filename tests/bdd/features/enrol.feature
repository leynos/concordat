Feature: Enrolling repositories with concordat
  The concordat CLI enrols repositories by writing a concordat document.

  Scenario: Enrolling a repository creates the concordat document
    Given a git repository
    When I run concordat enrol for that repository
    Then the repository contains the concordat document
    And the concordat document declares enrolled true

  Scenario: Disenrolling a repository clears the enrolled flag
    Given a git repository
    And the repository is enrolled with concordat
    When I run concordat disenrol for that repository
    Then the repository contains the concordat document
    And the concordat document declares enrolled false

  Scenario: Rejecting repositories outside the estate owner
    Given a git repository
    And the repository remote targets owner "other-owner"
    When I attempt to enrol that repository
    Then concordat reports the owner mismatch
