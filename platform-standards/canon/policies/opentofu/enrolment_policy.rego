package canon.opentofu.enrolment

import rego.v1

deny contains "`.concordat` must be a YAML mapping" if {
  not is_object(input)
}

manifest := input if {
  is_object(input)
}

enrolled_value := object.get(manifest, "enrolled", null)

deny contains "`.concordat` must define enrolled: true to opt into OpenTofu" if {
  manifest
  not is_boolean(enrolled_value)
}

deny contains "`.concordat` must define enrolled: true to opt into OpenTofu" if {
  manifest
  is_boolean(enrolled_value)
  enrolled_value != true
}
