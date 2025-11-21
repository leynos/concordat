package terratest

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/hashicorp/hcl/v2/hclparse"
	"github.com/hashicorp/hcl/v2/hclsimple"
	"github.com/hashicorp/hcl/v2/hclsyntax"
)

func terraformOptions(t *testing.T, pathSegments ...string) *terraform.Options {
	t.Helper()

	absPath := resolveFixture(t, pathSegments...)
	return &terraform.Options{
		TerraformDir:    absPath,
		NoColor:         true,
		PlanFilePath:    filepath.Join(t.TempDir(), "plan.tfplan"),
		TerraformBinary: terraformBinary(),
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

func terraformBinary() string {
	if binary := strings.TrimSpace(os.Getenv("TERRAFORM_BINARY")); binary != "" {
		return binary
	}
	return "tofu"
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

// TestBackendBlockDeclared ensures the root stack opts into the S3 backend so
// remote state can be configured via a tfbackend file.
func TestBackendBlockDeclared(t *testing.T) {
	parser := hclparse.NewParser()
	file, diag := parser.ParseHCLFile(filepath.Join("..", "backend.tf"))
	if diag.HasErrors() {
		t.Fatalf("parse backend.tf: %s", diag.Error())
	}

	body, ok := file.Body.(*hclsyntax.Body)
	if !ok {
		t.Fatalf("backend.tf unexpected body type %T", file.Body)
	}

	found := false
	for _, block := range body.Blocks {
		if block.Type != "terraform" {
			continue
		}

		for _, nested := range block.Body.Blocks {
			if nested.Type == "backend" && len(nested.Labels) > 0 && nested.Labels[0] == "s3" {
				found = true
				break
			}
		}
	}

	if !found {
		t.Fatalf("expected terraform backend \"s3\" block in backend.tf")
	}
}

// TestScalewayBackendConfigAssertsNoInlineSecrets guards the committed
// tfbackend specimen against accidental credential leakage and regression of
// the documented defaults.
func TestScalewayBackendConfigAssertsNoInlineSecrets(t *testing.T) {
	type scalewayBackendConfig struct {
		Bucket                     string            `hcl:"bucket"`
		Key                        string            `hcl:"key"`
		Region                     string            `hcl:"region"`
		Endpoints                  map[string]string `hcl:"endpoints"`
		UsePathStyle               bool              `hcl:"use_path_style,optional"`
		SkipRegionValidation       bool              `hcl:"skip_region_validation,optional"`
		SkipRequestingAccountID    bool              `hcl:"skip_requesting_account_id,optional"`
		SkipCredentialsValidation  bool              `hcl:"skip_credentials_validation,optional"`
		UseLockfile                *bool             `hcl:"use_lockfile,optional"`
		AccessKey                  *string           `hcl:"access_key,optional"`
		SecretKey                  *string           `hcl:"secret_key,optional"`
		SessionToken               *string           `hcl:"session_token,optional"`
		DynamodbTable              *string           `hcl:"dynamodb_table,optional"`
		SkipGetEc2Platforms        *bool             `hcl:"skip_get_ec2_platforms,optional"`
		SkipMetadataApiCheck       *bool             `hcl:"skip_metadata_api_check,optional"`
		SkipOriginAccessValidation *bool             `hcl:"skip_origin_access_validation,optional"`
	}

	sourcePath := filepath.Join("..", "backend", "scaleway.tfbackend")
	data, err := os.ReadFile(sourcePath)
	if err != nil {
		t.Fatalf("read scaleway backend config: %v", err)
	}

	var config scalewayBackendConfig
	if err := hclsimple.Decode("scaleway.hcl", data, nil, &config); err != nil {
		t.Fatalf("decode scaleway backend config: %v", err)
	}

	if config.Bucket != "df12-tfstate" {
		t.Fatalf("unexpected bucket %q", config.Bucket)
	}
	if config.Key != "estates/test-case/main/terraform.tfstate" {
		t.Fatalf("unexpected key %q", config.Key)
	}
	if config.Region != "fr-par" {
		t.Fatalf("unexpected region %q", config.Region)
	}

	endpoint, ok := config.Endpoints["s3"]
	if !ok || endpoint != "https://s3.fr-par.scw.cloud" {
		t.Fatalf("unexpected endpoint map %#v", config.Endpoints)
	}

	assertBoolTrue(t, map[string]interface{}{"use_path_style": config.UsePathStyle}, "use_path_style", "use_path_style must be true for Scaleway")
	assertBoolTrue(t, map[string]interface{}{"skip_region_validation": config.SkipRegionValidation}, "skip_region_validation", "skip_region_validation must be true to avoid AWS region probes")
	assertBoolTrue(t, map[string]interface{}{"skip_requesting_account_id": config.SkipRequestingAccountID}, "skip_requesting_account_id", "skip_requesting_account_id must prevent AWS-specific API calls")
	assertBoolTrue(t, map[string]interface{}{"skip_credentials_validation": config.SkipCredentialsValidation}, "skip_credentials_validation", "skip_credentials_validation avoids credentials lookups")

	if config.UseLockfile != nil && *config.UseLockfile {
		t.Fatalf("use_lockfile should be omitted for Scaleway backends")
	}
	if config.AccessKey != nil || config.SecretKey != nil {
		t.Fatalf("backend config must not embed credentials")
	}
	if config.SessionToken != nil {
		t.Fatalf("backend config must not embed session_token")
	}
	if config.DynamodbTable != nil {
		t.Fatalf("backend config should not declare DynamoDB locking")
	}
	if config.SkipGetEc2Platforms != nil && !*config.SkipGetEc2Platforms {
		t.Fatalf("skip_get_ec2_platforms should be omitted or true")
	}
	if config.SkipMetadataApiCheck != nil && !*config.SkipMetadataApiCheck {
		t.Fatalf("skip_metadata_api_check should be omitted or true")
	}
	if config.SkipOriginAccessValidation != nil && !*config.SkipOriginAccessValidation {
		t.Fatalf("skip_origin_access_validation should be omitted or true")
	}
}
