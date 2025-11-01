Feature: Enrolling repositories with concordat
  The concordat CLI enrols repositories by writing a concordat document.

  Scenario: Enrolling a repository creates the concordat document
    Given a git repository
    When I run concordat enrol for that repository
    Then the repository contains the concordat document
    And the concordat document declares enrolled true
