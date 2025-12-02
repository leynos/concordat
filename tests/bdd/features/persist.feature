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
    And the persistence permissions probe writes and deletes a single object to bucket "df12-tfstate" with key "estates/example/main/terraform.tfstate.concordat-tfstate-check"

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

  Scenario: Persisting without a GitHub token skips PR creation
    Given an isolated concordat config directory
    And an estate repository with alias "core"
    And pull requests are stubbed
    And bucket versioning status is "Enabled"
    And persistence prompts "df12-tfstate, fr-par, https://s3.fr-par.scw.cloud, estates/example/main, terraform.tfstate"
    And GITHUB_TOKEN is unset
    When I run concordat estate persist
    Then the command succeeds
    And no pull request was attempted

  Scenario: Versioning check failure surfaces an error
    Given an isolated concordat config directory
    And an estate repository with alias "core"
    And pull requests are stubbed
  And bucket versioning check fails
  And persistence prompts "df12-tfstate, fr-par, https://s3.fr-par.scw.cloud, estates/example/main, terraform.tfstate"
  And GITHUB_TOKEN is set to "test-token"
  When I run concordat estate persist
  Then the command fails with error containing "Versioning check failed"

  Scenario: Permission probe failure surfaces an error
    Given an isolated concordat config directory
    And an estate repository with alias "core"
    And pull requests are stubbed
    And bucket write permission check fails
    And persistence prompts "df12-tfstate, fr-par, https://s3.fr-par.scw.cloud, estates/example/main, terraform.tfstate"
    And GITHUB_TOKEN is set to "test-token"
    When I run concordat estate persist
    Then the command fails with error containing "Bucket permissions check failed"

  Scenario: Persisting to an explicitly named estate alias
    Given an isolated concordat config directory
    And an estate repository with alias "demo-estate"
    And pull requests are stubbed
    And bucket versioning status is "Enabled"
    And persistence prompts "df12-tfstate, fr-par, https://s3.fr-par.scw.cloud, estates/example/main, terraform.tfstate"
    And GITHUB_TOKEN is set to "test-token"
  When I run "concordat estate persist --alias demo-estate"
  Then the command succeeds
  And backend file "backend/demo-estate.tfbackend" contains "df12-tfstate"
  And persistence manifest records bucket "df12-tfstate"

  Scenario: Persisting non-interactively with flags
    Given an isolated concordat config directory
    And an estate repository with alias "core"
    And pull requests are stubbed
    And bucket versioning status is "Enabled"
    And GITHUB_TOKEN is set to "test-token"
    When I run "concordat estate persist --bucket df12-tfstate --region fr-par --endpoint https://s3.fr-par.scw.cloud --key-prefix estates/example/main --key-suffix terraform.tfstate --no-input"
    Then the command succeeds
    And backend file "backend/core.tfbackend" contains "df12-tfstate"

  Scenario: Persisting with an unknown estate alias fails
    Given an isolated concordat config directory
    And no estate repository is registered with alias "missing-estate"
    When I run "concordat estate persist --alias missing-estate"
    Then the command fails with error containing "is not configured"
