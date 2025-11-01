Feature: Listing GitHub repositories
  Scenario: Listing repositories across namespaces
    Given GitHub namespaces "alpha, bravo"
    And the GitHub API returns repositories "git@github.com:alpha/app.git, git@github.com:bravo/service.git"
    When I run concordat ls for those namespaces
    Then the CLI prints the repository SSH URLs
