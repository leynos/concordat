package terratest

import (
	"path/filepath"
	"testing"

	"github.com/gruntwork-io/terratest/modules/terraform"
)

func terraformOptions(t *testing.T, pathSegments ...string) *terraform.Options {
	t.Helper()

	absPath := resolveFixture(t, pathSegments...)
	return &terraform.Options{
		TerraformDir: absPath,
		NoColor:      true,
		PlanFilePath: filepath.Join(t.TempDir(), "plan.tfplan"),
	}
}

func resolveFixture(t *testing.T, pathSegments ...string) string {
	t.Helper()

	target := filepath.Join(pathSegments...)
	absPath, err := filepath.Abs(target)
	if err != nil {
		t.Fatalf("resolve fixture %s: %v", target, err)
	}
	return absPath
}

// assertBoolTrue fails the test if the given attribute is not a true boolean.
func assertBoolTrue(t *testing.T, attributes map[string]interface{}, key, message string) {
	t.Helper()
	value, ok := attributes[key].(bool)
	if !ok || !value {
		t.Fatalf("%s, got %#v", message, attributes[key])
	}
}

// assertBoolFalse fails the test if the given attribute is not a false boolean.
func assertBoolFalse(t *testing.T, attributes map[string]interface{}, key, message string) {
	t.Helper()
	value, ok := attributes[key].(bool)
	if !ok || value {
		t.Fatalf("%s, got %#v", message, attributes[key])
	}
}

// TestRepositoryModuleDefaults validates the default merge strategy logic using terraform
// plan output so we avoid hitting the GitHub API. The fixture config parallels CI usage.
func TestRepositoryModuleDefaults(t *testing.T) {
	options := terraformOptions(t, "..", "modules", "repository", "tests", "fixture")

	planStruct := terraform.InitAndPlanAndShowWithStruct(t, options)
	repoAddress := "module.repository.github_repository.this"
	plannedRepo, exists := planStruct.ResourcePlannedValuesMap[repoAddress]
	if !exists {
		t.Fatalf("expected repository resource %s to be planned", repoAddress)
	}

	assertBoolTrue(t, plannedRepo.AttributeValues, "allow_squash_merge", "expected squash merge to remain enabled")
	assertBoolFalse(t, plannedRepo.AttributeValues, "allow_merge_commit", "merge commits must stay disabled")
	assertBoolFalse(t, plannedRepo.AttributeValues, "allow_rebase_merge", "rebase merges must stay disabled")
	assertBoolTrue(t, plannedRepo.AttributeValues, "delete_branch_on_merge", "delete_branch_on_merge should default to true")
}

// TestRepositoryModuleRejectsMissingMergePaths ensures the validation guard blocks
// configurations that disable every merge mode.
func TestRepositoryModuleRejectsMissingMergePaths(t *testing.T) {
	options := terraformOptions(t, "..", "modules", "repository", "tests", "fixture_disable_merges")

	if _, err := terraform.InitAndPlanE(t, options); err == nil {
		t.Fatalf("expected plan to fail when all merge strategies are disabled")
	}
}

// TestRepositoryModuleRejectsDisallowedMergeModes ensures the guardrails block
// attempts to re-enable merge commits or rebase merges.
func TestRepositoryModuleRejectsDisallowedMergeModes(t *testing.T) {
	options := terraformOptions(t, "..", "modules", "repository", "tests", "fixture_enable_disallowed_merge")

	if _, err := terraform.InitAndPlanE(t, options); err == nil {
		t.Fatalf("expected plan to fail when merge commits or rebase merges are enabled")
	}
}

// TestBranchModuleRequiresStatusChecks ensures strict status checks carry contexts and
// conversation resolution is force-enabled.
func TestBranchModuleRequiresStatusChecks(t *testing.T) {
	options := terraformOptions(t, "..", "modules", "branch", "tests", "fixture")

	planStruct := terraform.InitAndPlanAndShowWithStruct(t, options)
	protectionAddress := "module.branch.github_branch_protection.this"
	plannedProtection, exists := planStruct.ResourcePlannedValuesMap[protectionAddress]
	if !exists {
		t.Fatalf("expected branch protection resource %s to be planned", protectionAddress)
	}

	assertBoolTrue(t, plannedProtection.AttributeValues, "require_conversation_resolution", "conversation resolution guardrail should be true")

	statusChecks, ok := plannedProtection.AttributeValues["required_status_checks"].([]interface{})
	if !ok || len(statusChecks) == 0 {
		t.Fatalf("expected required status checks to be populated, got %#v", plannedProtection.AttributeValues["required_status_checks"])
	}
}

// TestTeamModulePermissionMap verifies the module honours explicit repository permissions
// and deduplicates maintainers when declared more than once.
func TestTeamModulePermissionMap(t *testing.T) {
	options := terraformOptions(t, "..", "modules", "team", "tests", "fixture")

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
