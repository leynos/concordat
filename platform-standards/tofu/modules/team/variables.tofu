variable "name" {
  description = "Display name for the GitHub team."
  type        = string
  nullable    = false

  validation {
    condition     = trimspace(var.name) != ""
    error_message = "Team name cannot be empty."
  }
}

variable "description" {
  description = "Optional summary to help owners understand the team's charter."
  type        = string
  default     = ""
}

variable "privacy" {
  description = "Team visibility; closed teams appear to members while secret teams stay hidden."
  type        = string
  default     = "closed"

  validation {
    condition     = contains(["closed", "secret"], var.privacy)
    error_message = "Privacy must be closed or secret; visible is deprecated in GitHub Enterprise."
  }
}

variable "maintainers" {
  description = "GitHub usernames granted maintainer permissions for the team."
  type        = list(string)
  default     = []
}

variable "members" {
  description = "Regular team members; combine with repository permissions to provision access."
  type        = list(string)
  default     = []
}

variable "parent_team_id" {
  description = "Optional parent team node ID used to nest teams hierarchically."
  type        = string
  default     = ""
}

variable "repository_permissions" {
  description = "Map of repository names to permission levels that the team should hold by default."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for permission in values(var.repository_permissions) :
      contains(["pull", "triage", "push", "maintain", "admin"], permission)
    ])
    error_message = "Repository permissions must be one of pull, triage, push, maintain, or admin."
  }
}
