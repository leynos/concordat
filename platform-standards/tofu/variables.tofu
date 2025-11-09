variable "github_owner" {
  description = "GitHub organization or user that owns the managed repositories."
  type        = string
  default     = "test-case"
  nullable    = false

  validation {
    condition     = length(trimspace(var.github_owner)) > 0
    error_message = "Set github_owner to the organization slug you intend to manage."
  }
}

variable "github_token" {
  description = <<-EOT
    Token with repository administration permissions for the GitHub organization.
    Leave blank to fall back to the GITHUB_TOKEN environment variable.
  EOT
  type        = string
  default     = ""
  sensitive   = true
}
