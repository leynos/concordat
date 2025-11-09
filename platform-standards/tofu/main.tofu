locals {
  inventory_path     = "${path.module}/inventory/repositories.yaml"
  raw_inventory      = yamldecode(file(local.inventory_path))
  repository_entries = try(local.raw_inventory.repositories, [])
  schema_version     = try(local.raw_inventory.schema_version, 0)
  default_repo_config = {
    description            = ""
    visibility             = "private"
    homepage_url           = ""
    topics                 = []
    has_issues             = true
    has_projects           = false
    has_discussions        = false
    delete_branch_on_merge = true
    merge_strategies       = {}
    auto_init              = false
    default_branch         = ""
    is_template            = false
    vulnerability_alerts   = true
  }

  repository_records = {
    for entry in local.repository_entries :
    entry.name => merge(
      local.default_repo_config,
      try(entry.settings, {}),
      {
        slug       = entry.name
        slug_parts = split("/", entry.name)
      }
    )
  }

  repository_targets = {
    for slug, settings in local.repository_records :
    slug => merge(
      settings,
      {
        owner     = settings.slug_parts[0]
        repo_name = settings.slug_parts[length(settings.slug_parts) - 1]
      }
    )
  }
}

check "inventory_schema" {
  assert {
    condition     = local.schema_version == 1
    error_message = "platform-standards/tofu/inventory/repositories.yaml schema_version must be 1."
  }
}

check "inventory_slug_format" {
  assert {
    condition = alltrue([
      for slug, settings in local.repository_records :
      length(settings.slug_parts) == 2 && alltrue([for part in settings.slug_parts : trimspace(part) != ""])
    ])
    error_message = "Inventory entries must use owner/name slugs."
  }
}

check "inventory_owner_alignment" {
  assert {
    condition = length(local.repository_targets) == 0 || alltrue([
      for _, settings in local.repository_targets :
      settings.owner == var.github_owner
    ])
    error_message = "Inventory entries must match the configured github_owner."
  }
}

module "repository" {
  for_each = local.repository_targets
  source   = "./modules/repository"

  name                   = each.value.repo_name
  description            = each.value.description
  visibility             = each.value.visibility
  homepage_url           = each.value.homepage_url
  topics                 = each.value.topics
  has_issues             = each.value.has_issues
  has_projects           = each.value.has_projects
  has_discussions        = each.value.has_discussions
  delete_branch_on_merge = each.value.delete_branch_on_merge
  merge_strategies       = each.value.merge_strategies
  auto_init              = each.value.auto_init
  default_branch         = each.value.default_branch
  is_template            = each.value.is_template
  vulnerability_alerts   = each.value.vulnerability_alerts
}
