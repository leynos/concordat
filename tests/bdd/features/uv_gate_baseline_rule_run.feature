Feature: uv gate baseline rule run
  Auditing a local checkout against the uv-gate-baseline rule package, through
  the command an operator actually runs. The real policy evaluates the real
  Makefile facts, the helper's digest, workflows and pyproject.toml: these
  scenarios exercise the whole boundary from the checkout to the rendered table.

  Scenario: a checkout that does not use uv is audited clean
    Given a checkout that does not use uv
    When I audit the checkout for its uv gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a checkout that reaches uv only through the canonical helper is clean
    Given a checkout that runs uv only through the canonical helper
    When I audit the checkout for its uv gate
    Then the audit exit status is 0
    And the audit table reports zero findings

  Scenario: a recipe that runs uv directly is reported
    Given a checkout whose lint recipe runs uv directly
    When I audit the checkout for its uv gate
    Then the audit exit status is 1
    And the audit output reports "UV-003" "reaches uv directly"

  Scenario: an edited helper and a cache override are reported
    Given a checkout with an edited helper and a cache override
    When I audit the checkout for its uv gate
    Then the audit exit status is 1
    And the audit output reports "UV-001" "not a canonical uv_gate.py"
    And the audit output reports "UV-002" "UV_CACHE_DIR"

  Scenario: an unpinned tool and a missing lock are reported
    Given a checkout with an unpinned tool and no lock
    When I audit the checkout for its uv gate
    Then the audit exit status is 1
    And the audit output reports "UV-004" "uv.lock does not"
    And the audit output reports "UV-006" "not pinned"
