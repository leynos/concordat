Feature: Managing estates with concordat

  Scenario: Listing configured estates
    Given an empty concordat config directory
    And sample estates are configured
    When I run concordat estate ls
    Then the CLI prints
      """
      core	git@github.com:example/core.git
      sandbox	git@github.com:example/sandbox.git
      """

  Scenario: Initialising a missing estate registers it
    Given an empty concordat config directory
    And betamax cassette "estate-init-create" is active
    And the estate remote probe reports a missing repository
    When I run concordat estate init core git@github.com:example/platform-estate.git with confirmation
    Then the command succeeds
    And estate "core" is recorded in the config
