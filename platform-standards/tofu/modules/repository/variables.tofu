variable "name" {
  description = "Unique slug for the repository within the GitHub organization."
  type        = string
  nullable    = false

  validation {
    condition     = trimspace(var.name) != ""
    error_message = "Provide a non-empty repository name."
  }
}

variable "description" {
  description = "Human readable summary to surface in the GitHub UI."
  type        = string
  default     = ""
}

variable "visibility" {
  description = "Repository visibility; internal is available on GitHub Enterprise only."
  type        = string
  default     = "private"

  validation {
    condition     = contains(["private", "internal", "public"], var.visibility)
    error_message = "Visibility must be one of private, internal, or public."
  }
}

variable "homepage_url" {
  description = "Optional homepage URL shown in the repository header."
  type        = string
  default     = ""
}

variable "topics" {
  description = "Repository topics used by the Auditor to categorise services."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for topic in var.topics : trimspace(topic) != ""])
    error_message = "Topics must not contain empty values."
  }
}

variable "has_issues" {
  description = "Enable GitHub Issues for the repository when true."
  type        = bool
  default     = true
}

variable "has_projects" {
  description = "Enable legacy Projects (Projects v1); most teams disable this in favour of Projects v2."
  type        = bool
  default     = false
}

variable "has_discussions" {
  description = "Expose Discussions to support asynchronous Q&A when enabled."
  type        = bool
  default     = false
}

variable "delete_branch_on_merge" {
  description = "Enforce deletion of PR branches after a successful merge."
  type        = bool
  default     = true
}

variable "merge_strategies" {
  description = <<-EOT
    Merge strategy toggles; at least one of merge_commit, rebase_merge, or
    squash_merge must be enabled to preserve a usable release path. auto_merge
    should only be enabled for repositories that rely on bot-driven updates.
  EOT
  type = object({
    allow_merge_commit = optional(bool)
    allow_rebase_merge = optional(bool)
    allow_squash_merge = optional(bool)
    allow_auto_merge   = optional(bool)
  })
  default = {}

  validation {
    condition = alltrue([
      for value in [
        try(var.merge_strategies.allow_merge_commit, null),
        try(var.merge_strategies.allow_rebase_merge, null),
        try(var.merge_strategies.allow_squash_merge, null)
      ] : value == null
      ]) || anytrue([
      try(var.merge_strategies.allow_merge_commit == true, false),
      try(var.merge_strategies.allow_rebase_merge == true, false),
      try(var.merge_strategies.allow_squash_merge == true, false)
    ])
    error_message = "Enable at least one of allow_merge_commit, allow_rebase_merge, or allow_squash_merge."
  }
}

variable "auto_init" {
  description = "Initialise the repository with a default README.md when creating new repos."
  type        = bool
  default     = false
}

variable "default_branch" {
  description = "Optional default branch name; omit to rely on the GitHub default."
  type        = string
  default     = ""
}

variable "is_template" {
  description = "Mark the repository as a template so other teams can scaffold from it."
  type        = bool
  default     = false
}

variable "vulnerability_alerts" {
  description = "Enable Dependabot vulnerability alerts; required for Concordat monitoring."
  type        = bool
  default     = true
}
