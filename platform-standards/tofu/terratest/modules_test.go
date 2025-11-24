package terratest

import (
	"io"
	"io/fs"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go/aws"
	"github.com/aws/aws-sdk-go/aws/credentials"
	"github.com/aws/aws-sdk-go/aws/session"
	"github.com/aws/aws-sdk-go/service/s3"
	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/hashicorp/hcl/v2"
	"github.com/hashicorp/hcl/v2/hclparse"
	"github.com/hashicorp/hcl/v2/hclsimple"
	"github.com/hashicorp/hcl/v2/hclsyntax"
	"github.com/johannesboyne/gofakes3"
	"github.com/johannesboyne/gofakes3/backend/s3mem"
)

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

// copyContext holds the source and destination directories for a stack copy operation.
type copyContext struct {
	src string
	dst string
}

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

	found := hasS3BackendBlock(body)
	if !found {
		t.Fatalf("expected terraform backend \"s3\" block in backend.tf")
	}
}

// TestBackendTerraformRequirementsDeclared ensures backend.tf locks the OpenTofu
// and GitHub provider versions expected by CI.
func TestBackendTerraformRequirementsDeclared(t *testing.T) {
	parser := hclparse.NewParser()
	file, diag := parser.ParseHCLFile(filepath.Join("..", "backend.tf"))
	if diag.HasErrors() {
		t.Fatalf("parse backend.tf: %s", diag.Error())
	}

	body, ok := file.Body.(*hclsyntax.Body)
	if !ok {
		t.Fatalf("backend.tf unexpected body type %T", file.Body)
	}

	terraformBlock := findTerraformBlock(t, body)
	validateRequiredVersion(t, terraformBlock)
	requiredProviders := findRequiredProvidersBlock(t, terraformBlock)
	validateGitHubProvider(t, requiredProviders)
}

func hasS3BackendBlock(body *hclsyntax.Body) bool {
	for _, block := range body.Blocks {
		if block.Type != "terraform" {
			continue
		}
		if containsS3Backend(block) {
			return true
		}
	}
	return false
}

func containsS3Backend(terraformBlock *hclsyntax.Block) bool {
	for _, nested := range terraformBlock.Body.Blocks {
		if nested.Type != "backend" {
			continue
		}
		if len(nested.Labels) == 0 {
			continue
		}
		if isS3BackendBlock(nested) {
			return true
		}
	}
	return false
}

func isS3BackendBlock(block *hclsyntax.Block) bool {
	if block.Type != "backend" {
		return false
	}
	if len(block.Labels) == 0 {
		return false
	}
	return block.Labels[0] == "s3"
}

func findTerraformBlock(t *testing.T, body *hclsyntax.Body) *hclsyntax.Block {
	t.Helper()

	for _, blk := range body.Blocks {
		if blk.Type == "terraform" {
			return blk
		}
	}
	t.Fatalf("expected terraform block in backend.tf")
	return nil
}

func validateRequiredVersion(t *testing.T, terraformBlock *hclsyntax.Block) {
	t.Helper()

	const expectedRequiredVersion = ">= 1.12.0, < 2.0.0"
	requiredVersionAttr, ok := terraformBlock.Body.Attributes["required_version"]
	if !ok {
		t.Fatalf("expected terraform.required_version to be declared in backend.tf")
	}

	requiredVersionVal, diags := requiredVersionAttr.Expr.Value(&hcl.EvalContext{})
	if diags.HasErrors() {
		t.Fatalf("evaluate terraform.required_version: %s", diags.Error())
	}
	if requiredVersionVal.AsString() != expectedRequiredVersion {
		t.Fatalf("expected terraform.required_version %q, got %q", expectedRequiredVersion, requiredVersionVal.AsString())
	}
}

func findRequiredProvidersBlock(t *testing.T, terraformBlock *hclsyntax.Block) *hclsyntax.Block {
	t.Helper()

	for _, blk := range terraformBlock.Body.Blocks {
		if blk.Type == "required_providers" {
			return blk
		}
	}
	t.Fatalf("expected terraform.required_providers block in backend.tf")
	return nil
}

func validateGitHubProvider(t *testing.T, requiredProviders *hclsyntax.Block) {
	t.Helper()

	githubProviderAttr, ok := requiredProviders.Body.Attributes["github"]
	if !ok {
		t.Fatalf("expected terraform.required_providers.github to be declared in backend.tf")
	}

	githubProviderVal, diags := githubProviderAttr.Expr.Value(&hcl.EvalContext{})
	if diags.HasErrors() {
		t.Fatalf("evaluate terraform.required_providers.github: %s", diags.Error())
	}
	if !githubProviderVal.Type().IsObjectType() {
		t.Fatalf("expected terraform.required_providers.github to be an object, got %s", githubProviderVal.Type().FriendlyName())
	}

	attrs := githubProviderVal.AsValueMap()
	versionVal, ok := attrs["version"]
	if !ok {
		t.Fatalf("expected terraform.required_providers.github to declare a version constraint")
	}

	const expectedGitHubProviderVersion = "~> 6.3"
	if versionVal.AsString() != expectedGitHubProviderVersion {
		t.Fatalf("expected terraform.required_providers.github.version %q, got %q", expectedGitHubProviderVersion, versionVal.AsString())
	}
}

// TestScalewayBackendConfigAssertsNoInlineSecrets guards the committed
// tfbackend specimen against accidental credential leakage and regression of
// the documented defaults.
func TestScalewayBackendConfigAssertsNoInlineSecrets(t *testing.T) {
	config := loadScalewayBackendConfig(t)

	validateScalewayRequiredFields(t, config)
	validateScalewayRequiredBooleans(t, config)
	validateScalewayForbiddenCredentials(t, config)
	validateScalewayOptionalSkipFlags(t, config)
}

// TestBackendInitAgainstFakeS3 exercises backend init using the Scaleway
// template against a local S3-compatible server to guard backend wiring.
func TestBackendInitAgainstFakeS3(t *testing.T) {
	config := loadScalewayBackendConfig(t)
	fakeS3, bucket := startFakeS3(t)
	defer fakeS3.Close()

	config.Bucket = bucket
	config.Key = "behavioural/test/terraform.tfstate"
	config.Region = "us-east-1"
	config.Endpoints = map[string]string{"s3": fakeS3.URL}

	workspace := copyStackToTemp(t, "..")
	opts := &terraform.Options{
		TerraformDir:    workspace,
		NoColor:         true,
		TerraformBinary: terraformBinary(),
		BackendConfig: map[string]interface{}{
			"bucket":                      config.Bucket,
			"key":                         config.Key,
			"region":                      config.Region,
			"endpoints":                   config.Endpoints,
			"use_path_style":              config.UsePathStyle,
			"skip_region_validation":      config.SkipRegionValidation,
			"skip_requesting_account_id":  config.SkipRequestingAccountID,
			"skip_credentials_validation": config.SkipCredentialsValidation,
		},
		EnvVars: map[string]string{
			"AWS_ACCESS_KEY_ID":     "test",
			"AWS_SECRET_ACCESS_KEY": "test",
			"AWS_REGION":            config.Region,
		},
	}

	if _, err := terraform.InitE(t, opts); err != nil {
		t.Fatalf("tofu init with fake S3 backend: %v", err)
	}
}

func validateScalewayRequiredFields(t *testing.T, cfg scalewayBackendConfig) {
	t.Helper()

	if cfg.Bucket != "df12-tfstate" {
		t.Fatalf("unexpected bucket %q", cfg.Bucket)
	}
	if cfg.Key != "estates/test-case/main/terraform.tfstate" {
		t.Fatalf("unexpected key %q", cfg.Key)
	}
	if cfg.Region != "fr-par" {
		t.Fatalf("unexpected region %q", cfg.Region)
	}

	endpoint, exists := cfg.Endpoints["s3"]
	if !exists || endpoint != "https://s3.fr-par.scw.cloud" {
		t.Fatalf("unexpected endpoint map %#v", cfg.Endpoints)
	}
}

func validateScalewayRequiredBooleans(t *testing.T, cfg scalewayBackendConfig) {
	t.Helper()

	assertBoolTrue(t, map[string]interface{}{"use_path_style": cfg.UsePathStyle}, "use_path_style", "use_path_style must be true for Scaleway")
	assertBoolTrue(t, map[string]interface{}{"skip_region_validation": cfg.SkipRegionValidation}, "skip_region_validation", "skip_region_validation must be true to avoid AWS region probes")
	assertBoolTrue(t, map[string]interface{}{"skip_requesting_account_id": cfg.SkipRequestingAccountID}, "skip_requesting_account_id", "skip_requesting_account_id must prevent AWS-specific API calls")
	assertBoolTrue(t, map[string]interface{}{"skip_credentials_validation": cfg.SkipCredentialsValidation}, "skip_credentials_validation", "skip_credentials_validation avoids credentials lookups")
}

func validateScalewayForbiddenCredentials(t *testing.T, cfg scalewayBackendConfig) {
	t.Helper()

	if cfg.UseLockfile != nil && *cfg.UseLockfile {
		t.Fatalf("use_lockfile should be omitted for Scaleway backends")
	}
	if cfg.AccessKey != nil || cfg.SecretKey != nil {
		t.Fatalf("backend config must not embed credentials")
	}
	if cfg.SessionToken != nil {
		t.Fatalf("backend config must not embed session_token")
	}
	if cfg.DynamodbTable != nil {
		t.Fatalf("backend config should not declare DynamoDB locking")
	}
}

func validateScalewayOptionalSkipFlags(t *testing.T, cfg scalewayBackendConfig) {
	t.Helper()

	if cfg.SkipGetEc2Platforms != nil && !*cfg.SkipGetEc2Platforms {
		t.Fatalf("skip_get_ec2_platforms should be omitted or true")
	}
	if cfg.SkipMetadataApiCheck != nil && !*cfg.SkipMetadataApiCheck {
		t.Fatalf("skip_metadata_api_check should be omitted or true")
	}
	if cfg.SkipOriginAccessValidation != nil && !*cfg.SkipOriginAccessValidation {
		t.Fatalf("skip_origin_access_validation should be omitted or true")
	}
}

func loadScalewayBackendConfig(t *testing.T) scalewayBackendConfig {
	t.Helper()

	sourcePath := filepath.Join("..", "backend", "scaleway.tfbackend")
	data, err := os.ReadFile(sourcePath)
	if err != nil {
		t.Fatalf("read scaleway backend config: %v", err)
	}

	var config scalewayBackendConfig
	if err := hclsimple.Decode("scaleway.hcl", data, nil, &config); err != nil {
		t.Fatalf("decode scaleway backend config: %v", err)
	}
	return config
}

func startFakeS3(t *testing.T) (*httptest.Server, string) {
	t.Helper()

	memBackend := s3mem.New()
	fake := gofakes3.New(memBackend)
	server := httptest.NewServer(fake.Server())

	awsConfig := &aws.Config{
		Region:           aws.String("us-east-1"),
		Endpoint:         aws.String(server.URL),
		S3ForcePathStyle: aws.Bool(true),
		Credentials:      credentials.NewStaticCredentials("test", "test", ""),
	}

	sess := session.Must(session.NewSession(awsConfig))
	client := s3.New(sess)
	bucket := strings.ReplaceAll("fake-s3-"+time.Now().UTC().Format("150405.000000000"), ".", "-")

	if _, err := client.CreateBucket(&s3.CreateBucketInput{Bucket: aws.String(bucket)}); err != nil {
		t.Fatalf("create bucket on fake S3: %v", err)
	}

	return server, bucket
}

func copyStackToTemp(t *testing.T, src string) string {
	t.Helper()

	dst := t.TempDir()
	ctx := copyContext{src: src, dst: dst}
	err := filepath.WalkDir(src, func(path string, d fs.DirEntry, err error) error {
		return copyStackEntry(ctx, path, d, err)
	})
	if err != nil {
		t.Fatalf("copy stack to temp: %v", err)
	}

	return dst
}

func copyStackEntry(ctx copyContext, path string, d fs.DirEntry, err error) error {
	if err != nil {
		return err
	}

	rel, err := filepath.Rel(ctx.src, path)
	if err != nil {
		return err
	}

	if rel == "." {
		return nil
	}

	// Skip terraform artifacts and VCS metadata
	if shouldSkipPath(rel) {
		if d.IsDir() {
			return filepath.SkipDir
		}
		return nil
	}

	target := filepath.Join(ctx.dst, rel)
	if d.IsDir() {
		return os.MkdirAll(target, 0o755)
	}

	return copyFile(path, target)
}

// shouldSkipPath returns true if the relative path represents a terraform
// artefact or VCS metadata that should be excluded when copying a stack.
func shouldSkipPath(rel string) bool {
	if rel == ".terraform" {
		return true
	}
	if strings.HasPrefix(rel, ".git") {
		return true
	}
	if rel == ".terraform.lock.hcl" {
		return true
	}
	return false
}
func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}

	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()

	if _, err := io.Copy(out, in); err != nil {
		return err
	}
	return out.Sync()
}
