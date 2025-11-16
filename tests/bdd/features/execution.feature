Feature: Running estate execution commands

  Scenario: Plan cleans up its workspace
    Given an isolated concordat config directory
    And an isolated concordat cache directory
    And a fake estate repository is registered
    And a fake tofu binary logs invocations
    When I run concordat plan
    Then the command exits with code 0
    And fake tofu commands were "version -json | init -input=false | plan"
    And the execution workspace has been removed

  Scenario: Keeping the workspace preserves the directory
    Given an isolated concordat config directory
    And an isolated concordat cache directory
    And a fake estate repository is registered
    And a fake tofu binary logs invocations
    When I run concordat plan with options "--keep-workdir"
    Then the command exits with code 0
    And the execution workspace remains on disk

  Scenario: Apply requires --auto-approve
    Given an isolated concordat config directory
    And an isolated concordat cache directory
    And a fake estate repository is registered
    And a fake tofu binary logs invocations
    When I run concordat apply
    Then the command fails with message "auto-approve"

  Scenario: Apply forwards --auto-approve
    Given an isolated concordat config directory
    And an isolated concordat cache directory
    And a fake estate repository is registered
    And a fake tofu binary logs invocations
    When I run concordat apply with options "--auto-approve"
    Then the command exits with code 0
    And fake tofu commands were "version -json | init -input=false | apply -auto-approve"

  Scenario: Plan requires GITHUB_TOKEN
    Given an isolated concordat config directory
    And an isolated concordat cache directory
    And a fake estate repository is registered
    And GITHUB_TOKEN is unset
    And a fake tofu binary logs invocations
    When I run concordat plan
    Then the command fails with message "GITHUB_TOKEN"

  Scenario: Plan requires an active estate
    Given an isolated concordat config directory
    And an isolated concordat cache directory
    And GITHUB_TOKEN is set to "placeholder-token"
    And a fake tofu binary logs invocations
    When I run concordat plan
    Then the command fails with message "No active estate configured"
