Feature: Persisting estate state remotely

  Scenario: Persisting state when bucket versioning is enabled
    Given an isolated concordat config directory
    And an estate repository with alias "core"
    And pull requests are stubbed
    And bucket versioning status is "Enabled"
    And persistence prompts "df12-tfstate, fr-par, https://s3.fr-par.scw.cloud, estates/example/main, terraform.tfstate"
    And GITHUB_TOKEN is set to "test-token"
    When I run concordat estate persist
    Then the command succeeds
    And backend file "backend/core.tfbackend" contains "bucket                      = \"df12-tfstate\""
    And persistence manifest records bucket "df12-tfstate"
    And credentials are not written to the backend files

  Scenario: Persisting fails when bucket versioning is disabled
    Given an isolated concordat config directory
    And an estate repository with alias "core"
    And pull requests are stubbed
    And bucket versioning status is "Suspended"
    And persistence prompts "df12-tfstate, fr-par, https://s3.fr-par.scw.cloud, estates/example/main, terraform.tfstate"
    And GITHUB_TOKEN is set to "test-token"
    When I run concordat estate persist
    Then the command fails with error containing "must enable versioning"
    And backend file "backend/core.tfbackend" is absent
    And persistence manifest is absent

  Scenario: Replacing an existing backend requires --force
    Given an isolated concordat config directory
    And an estate repository with alias "core"
    And pull requests are stubbed
    And bucket versioning status is "Enabled"
    And persistence prompts "first-bucket, fr-par, https://s3.fr-par.scw.cloud, estates/example/main, terraform.tfstate"
    And GITHUB_TOKEN is set to "test-token"
    When I run concordat estate persist
    Then the command succeeds
    And backend file "backend/core.tfbackend" contains "first-bucket"
    And persistence manifest records bucket "first-bucket"
    And the persistence change is merged into main
    And persistence prompts "second-bucket, fr-par, https://s3.fr-par.scw.cloud, estates/example/main, terraform.tfstate"
    When I run concordat estate persist
    Then the command fails with error containing "--force"
    And persistence prompts "second-bucket, fr-par, https://s3.fr-par.scw.cloud, estates/example/main, terraform.tfstate"
    When I run concordat estate persist with options "--force"
    Then backend file "backend/core.tfbackend" contains "second-bucket"
    And persistence manifest records bucket "second-bucket"
