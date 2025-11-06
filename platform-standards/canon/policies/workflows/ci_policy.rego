package canon.workflows.ci

import rego.v1

workflow_name := "canon-ci"

deny contains msg if {
  input.name == workflow_name
  not input.on.workflow_call
  msg := "canon-ci workflow must expose workflow_call trigger"
}

deny contains msg if {
  input.name == workflow_name
  not input.on.workflow_dispatch
  msg := "canon-ci workflow must support workflow_dispatch for local testing"
}

deny contains msg if {
  input.name == workflow_name
  not input.jobs["lint-test"]
  msg := "canon-ci workflow must define the lint-test job"
}

deny contains msg if {
  input.name == workflow_name
  not job_uses_checkout
  msg := "lint-test job must check out the repository with actions/checkout@v4"
}

deny contains msg if {
  input.name == workflow_name
  not job_uses_setup_python
  msg := "lint-test job must set up Python using actions/setup-python@v5"
}

job_uses_checkout if {
  step := input.jobs["lint-test"].steps[_]
  step.uses == "actions/checkout@v4"
}

job_uses_setup_python if {
  step := input.jobs["lint-test"].steps[_]
  step.uses == "actions/setup-python@v5"
}
