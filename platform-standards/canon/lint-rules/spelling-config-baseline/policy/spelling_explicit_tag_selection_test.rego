# Distinguish explicit-pin provenance from the newest-block fallback.
package canon.lint_rules.spelling_config_baseline_test

import rego.v1

import data.canon.lint_rules.spelling_config_baseline as policy

selection_blocks := {
  "v0.1.3": "## Spelling\n\nThe earlier published block.",
  "v0.2.0": "## Spelling\n\nThe selected published block.",
  "v0.3.0": "## Spelling\n\nThe newer published block.",
}

explicit_middle_pin(body) := json.patch(spelling_with_pin("refs/tags/v0.2.0"), [{
  "op": "replace",
  "path": "/agents_md/text",
  "value": concat("\n", [policy.agents_start_marker, body, policy.agents_end_marker]),
}])

# A newer block makes an empty-pin fallback observably wrong.
test_explicit_middle_pin_selects_its_published_block if {
  findings := policy.deny with input as explicit_middle_pin(selection_blocks["v0.2.0"]) with data.parameters.agents_md_blocks as selection_blocks
  count(findings) == 0
}

test_explicit_middle_pin_names_its_block_when_text_differs if {
  every wrong in ["v0.1.3", "v0.3.0"] {
    findings := policy.deny with input as explicit_middle_pin(selection_blocks[wrong]) with data.parameters.agents_md_blocks as selection_blocks
    profile(findings) == {
      ["PD-013", "noncompliant", 0, "AGENTS.md's spelling block differs from typos-config-builder v0.2.0's docs/agents-md-spelling.md; copy it verbatim"],
    }
  }
}
