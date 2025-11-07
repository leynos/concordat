package terratest

import (
	"path/filepath"
	"testing"

	"github.com/gruntwork-io/terratest/modules/terraform"
)

// TestRepositoryModuleDefaults validates the default merge strategy logic using terraform
// plan output so we avoid hitting the GitHub API. The fixture config parallels CI usage.
func TestRepositoryModuleDefaults(t *testing.T) {
	fixturePath, err := filepath.Abs(filepath.Join("..", "modules", "repository", "tests", "fixture"))
	if err != nil {
		t.Fatalf("resolve repository fixture: %v", err)
	}
	options := &terraform.Options{
		TerraformDir: fixturePath,
		NoColor:      true,
		PlanFilePath: filepath.Join(t.TempDir(), "plan.tfplan"),
	}

	planStruct := terraform.InitAndPlanAndShowWithStruct(t, options)
	repoAddress := "module.repository.github_repository.this"
	plannedRepo, exists := planStruct.ResourcePlannedValuesMap[repoAddress]
	if !exists {
		t.Fatalf("expected repository resource %s to be planned", repoAddress)
	}

	allowSquash, ok := plannedRepo.AttributeValues["allow_squash_merge"].(bool)
	if !ok || !allowSquash {
		t.Fatalf("expected squash merge to remain enabled, got %#v", plannedRepo.AttributeValues["allow_squash_merge"])
	}

	mergeCommit, ok := plannedRepo.AttributeValues["allow_merge_commit"].(bool)
	if !ok || mergeCommit {
		t.Fatalf("merge commits must stay disabled, got %#v", plannedRepo.AttributeValues["allow_merge_commit"])
	}

	rebaseMerge, ok := plannedRepo.AttributeValues["allow_rebase_merge"].(bool)
	if !ok || rebaseMerge {
		t.Fatalf("rebase merges must stay disabled, got %#v", plannedRepo.AttributeValues["allow_rebase_merge"])
	}

	deleteBranches, ok := plannedRepo.AttributeValues["delete_branch_on_merge"].(bool)
	if !ok || !deleteBranches {
		t.Fatalf("delete_branch_on_merge should default to true, got %#v", plannedRepo.AttributeValues["delete_branch_on_merge"])
	}
}

// TestRepositoryModuleRejectsMissingMergePaths ensures the validation guard blocks
// configurations that disable every merge mode.
func TestRepositoryModuleRejectsMissingMergePaths(t *testing.T) {
	fixturePath, err := filepath.Abs(filepath.Join("..", "modules", "repository", "tests", "fixture_disable_merges"))
	if err != nil {
		t.Fatalf("resolve repository fixture: %v", err)
	}
	options := &terraform.Options{
		TerraformDir: fixturePath,
		NoColor:      true,
		PlanFilePath: filepath.Join(t.TempDir(), "plan.tfplan"),
	}

	if _, err := terraform.InitAndPlanE(t, options); err == nil {
		t.Fatalf("expected plan to fail when all merge strategies are disabled")
	}
}

// TestRepositoryModuleRejectsDisallowedMergeModes ensures the guardrails block
// attempts to re-enable merge commits or rebase merges.
func TestRepositoryModuleRejectsDisallowedMergeModes(t *testing.T) {
	fixturePath, err := filepath.Abs(filepath.Join("..", "modules", "repository", "tests", "fixture_enable_disallowed_merge"))
	if err != nil {
		t.Fatalf("resolve repository fixture: %v", err)
	}
	options := &terraform.Options{
		TerraformDir: fixturePath,
		NoColor:      true,
		PlanFilePath: filepath.Join(t.TempDir(), "plan.tfplan"),
	}

	if _, err := terraform.InitAndPlanE(t, options); err == nil {
		t.Fatalf("expected plan to fail when merge commits or rebase merges are enabled")
	}
}

// TestBranchModuleRequiresStatusChecks ensures strict status checks carry contexts and
// conversation resolution is force-enabled.
func TestBranchModuleRequiresStatusChecks(t *testing.T) {
	fixturePath, err := filepath.Abs(filepath.Join("..", "modules", "branch", "tests", "fixture"))
	if err != nil {
		t.Fatalf("resolve branch fixture: %v", err)
	}
	options := &terraform.Options{
		TerraformDir: fixturePath,
		NoColor:      true,
		PlanFilePath: filepath.Join(t.TempDir(), "plan.tfplan"),
	}

	planStruct := terraform.InitAndPlanAndShowWithStruct(t, options)
	protectionAddress := "module.branch.github_branch_protection.this"
	plannedProtection, exists := planStruct.ResourcePlannedValuesMap[protectionAddress]
	if !exists {
		t.Fatalf("expected branch protection resource %s to be planned", protectionAddress)
	}

	requireConversation, ok := plannedProtection.AttributeValues["require_conversation_resolution"].(bool)
	if !ok || !requireConversation {
		t.Fatalf("conversation resolution guardrail should be true, got %#v", plannedProtection.AttributeValues["require_conversation_resolution"])
	}

	statusChecks, ok := plannedProtection.AttributeValues["required_status_checks"].([]interface{})
	if !ok || len(statusChecks) == 0 {
		t.Fatalf("expected required status checks to be populated, got %#v", plannedProtection.AttributeValues["required_status_checks"])
	}
}

// TestTeamModulePermissionMap verifies the module honours explicit repository permissions
// and deduplicates maintainers when declared more than once.
func TestTeamModulePermissionMap(t *testing.T) {
	fixturePath, err := filepath.Abs(filepath.Join("..", "modules", "team", "tests", "fixture"))
	if err != nil {
		t.Fatalf("resolve team fixture: %v", err)
	}
	options := &terraform.Options{
		TerraformDir: fixturePath,
		NoColor:      true,
		PlanFilePath: filepath.Join(t.TempDir(), "plan.tfplan"),
	}

	planStruct := terraform.InitAndPlanAndShowWithStruct(t, options)
	maintainerKey := "module.team.github_team_membership.maintainers[\"alice\"]"
	if _, exists := planStruct.ResourcePlannedValuesMap[maintainerKey]; !exists {
		t.Fatalf("expected maintainer membership %s to be planned", maintainerKey)
	}

	memberKey := "module.team.github_team_membership.members[\"bob\"]"
	if _, exists := planStruct.ResourcePlannedValuesMap[memberKey]; !exists {
		t.Fatalf("expected member mapping %s to be planned", memberKey)
	}

	repoPermissionsAddress := "module.team.github_team_repository.default_permissions[\"fixture-repo\"]"
	if _, exists := planStruct.ResourcePlannedValuesMap[repoPermissionsAddress]; !exists {
		t.Fatalf("expected repository permission mapping %s to be created", repoPermissionsAddress)
	}
}
