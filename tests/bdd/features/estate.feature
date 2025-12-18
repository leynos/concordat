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

  Scenario: Estate initialisation prompts to confirm inferred owner
    Given an empty concordat config directory
    And the estate remote probe reports an empty existing repository
    And the operator responds "y" to prompts
    When I run concordat estate init core git@github.com:example/platform-estate.git interactively
    Then the command succeeds
    And the CLI prompts
      """
      Inferred github_owner 'example' from estate repo 'example/platform-estate'. Use this? [y/N]:
      """
    And estate "core" is recorded in the config

  Scenario: Estate initialisation aborts when inferred owner is declined
    Given an empty concordat config directory
    And the estate remote probe reports an empty existing repository
    And the operator responds "n" to prompts
    When I run concordat estate init core git@github.com:example/platform-estate.git interactively
    Then the command fails with "GitHub owner confirmation declined"
    And estate "core" is not recorded in the config

  Scenario: Interactive estate initialisation prompts before creating the repo
    Given an empty concordat config directory
    And betamax cassette "estate-init-create" is active
    And the estate remote probe reports a missing repository
    And the operator responds "y" to prompts
    When I run concordat estate init core git@github.com:example/platform-estate.git with token
    Then the command succeeds
    And the CLI prompts
      """
      Inferred github_owner 'example' from estate repo 'example/platform-estate'. Use this? [y/N]:
      Create GitHub repository example/platform-estate? [y/N]:
      """
    And estate "core" is recorded in the config

  Scenario: Estate initialisation sanitises the inventory
    Given an empty concordat config directory
    And a local estate remote
    When I run concordat estate init local-core using that remote for owner "sandbox"
    Then the estate inventory contains no sample repositories
