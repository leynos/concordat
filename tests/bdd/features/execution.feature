Feature: Running estate execution commands

  Background:
    Given an isolated concordat config directory
    And an isolated concordat cache directory
    And a fake tofu binary logs invocations

  Scenario: Plan cleans up its workspace
    Given a fake estate repository is registered
    When I run concordat plan
    Then the command exits with code 0
    And fake tofu commands were "version -json | init -input=false | plan"
    And the execution workspace has been removed

  Scenario: Keeping the workspace preserves the directory
    Given a fake estate repository is registered
    When I run concordat plan with options "--keep-workdir"
    Then the command exits with code 0
    And the execution workspace remains on disk

  Scenario: Apply requires --auto-approve
    Given a fake estate repository is registered
    When I run concordat apply
    Then the command fails with message "auto-approve"

  Scenario: Apply forwards --auto-approve
    Given a fake estate repository is registered
    When I run concordat apply with options "--auto-approve"
    Then the command exits with code 0
    And fake tofu commands were "version -json | init -input=false | apply -auto-approve"

  Scenario: Apply preserves the workspace when requested
    Given a fake estate repository is registered
    When I run concordat apply with options "--auto-approve --keep-workdir"
    Then the command exits with code 0
    And the execution workspace remains on disk

  Scenario: Plan requires GITHUB_TOKEN
    Given a fake estate repository is registered
    And GITHUB_TOKEN is unset
    When I run concordat plan
    Then the command fails with message "GITHUB_TOKEN"

  Scenario: Plan requires an active estate
    Given GITHUB_TOKEN is set to "placeholder-token"
    When I run concordat plan
    Then the command fails with message "No active estate configured"

  Scenario: Plan uses the remote backend when persistence is enabled
    Given a fake estate repository is registered
    And the estate repository has remote state configured
    And remote backend credentials are set
    When I run concordat plan
    Then the command exits with code 0
    And fake tofu commands were "version -json | init -input=false -backend-config=backend/core.tfbackend | plan"
    And backend details are logged
    And no backend secrets are logged

  Scenario: Plan uses the remote backend with SPACES credentials
    Given a fake estate repository is registered
    And the estate repository has remote state configured
    And remote backend credentials are set via SPACES
    When I run concordat plan
    Then the command exits with code 0
    And fake tofu commands were "version -json | init -input=false -backend-config=backend/core.tfbackend | plan"
    And backend details are logged
    And no backend secrets are logged

  Scenario: Plan uses the remote backend with AWS credentials
    Given a fake estate repository is registered
    And the estate repository has remote state configured
    And remote backend credentials are set via AWS
    And aws-style backend secrets are present in the environment
    When I run concordat plan
    Then the command exits with code 0
    And fake tofu commands were "version -json | init -input=false -backend-config=backend/core.tfbackend | plan"
    And backend details are logged
    And no backend secrets are logged

  Scenario: Plan refuses to run without remote backend credentials
    Given a fake estate repository is registered
    And the estate repository has remote state configured
    And remote backend credentials are missing
    When I run concordat plan
    Then the command fails with message "AWS_ACCESS_KEY_ID"
