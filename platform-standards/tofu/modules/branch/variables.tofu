variable "repository_node_id" {
  description = "GitHub GraphQL node identifier returned by the repository module."
  type        = string
  nullable    = false

  validation {
    condition     = trimspace(var.repository_node_id) != ""
    error_message = "Pass the repository node ID output by the repository module."
  }
}

variable "pattern" {
  description = "Branch name or glob pattern protected by this ruleset."
  type        = string
  default     = "main"

  validation {
    condition     = trimspace(var.pattern) != ""
    error_message = "Branch protection requires a non-empty pattern."
  }
}

variable "enforce_admins" {
  description = "Require admins to follow the same branch guardrails."
  type        = bool
  default     = true
}

variable "require_signed_commits" {
  description = "Reject unsigned commits to uphold provenance guarantees."
  type        = bool
  default     = true
}

variable "required_linear_history" {
  description = "Forbid merge commits on protected branches to keep history linear."
  type        = bool
  default     = true
}

variable "require_conversation_resolution" {
  description = "Force open review threads to resolve before merging."
  type        = bool
  default     = true
}

variable "allows_deletions" {
  description = "Permit branch deletion; default false so protected branches stay durable."
  type        = bool
  default     = false
}

variable "allows_force_pushes" {
  description = "Permit force pushes as an escape hatch; keep disabled unless explicitly justified."
  type        = bool
  default     = false
}

variable "status_checks" {
  description = "Required status checks; contexts must contain at least one entry when strict."
  type = object({
    strict   = optional(bool, true)
    contexts = optional(list(string), [])
  })
  default = {
    strict   = true
    contexts = ["concordat/auditor"]
  }

  validation {
    condition     = !try(var.status_checks.strict, true) || length(try(var.status_checks.contexts, [])) > 0
    error_message = "Provide at least one status check context when strict enforcement is enabled."
  }
}

variable "pull_request_reviews" {
  description = "Pull request review requirements for the branch protection rules."
  type = object({
    required_approvals         = optional(number, 2)
    dismiss_stale_reviews      = optional(bool, true)
    require_code_owner_reviews = optional(bool, true)
  })
  default = {}
}

variable "restrictions" {
  description = "Restrict who can push directly; supply GitHub team slugs and usernames. Currently informational only."
  type = object({
    teams = optional(list(string), [])
    users = optional(list(string), [])
  })
  default = {}
}
