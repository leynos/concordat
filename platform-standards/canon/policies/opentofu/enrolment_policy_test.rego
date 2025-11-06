package canon.opentofu

import rego.v1

test_accepts_true_flag if {
  result := data.canon.opentofu.enrolment.deny with input as {"enrolled": true}
  count(result) == 0
}

test_missing_mapping_rejected if {
  result := data.canon.opentofu.enrolment.deny with input as []
  result[_] == "`.concordat` must be a YAML mapping"
}

test_missing_key_rejected if {
  result := data.canon.opentofu.enrolment.deny with input as {}
  result[_] == "`.concordat` must define enrolled: true to opt into OpenTofu"
}

test_false_flag_rejected if {
  result := data.canon.opentofu.enrolment.deny with input as {"enrolled": false}
  result[_] == "`.concordat` must define enrolled: true to opt into OpenTofu"
}
