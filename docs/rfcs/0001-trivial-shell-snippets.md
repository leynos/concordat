# RFC 0001: Keep shell snippets trivial

- Status: Proposed; design only, not an implemented rule.
- Date: 2026-09-07.
- Rule package: `shell-snippet-baseline`.
- Proposed finding identifiers: SH-001 through SH-004.
- Delivery: [roadmap, Section 4.4](../roadmap.md).
- Repository baseline: `10f62125dd08088163419175d7b659e165263e24`.

## 1. Summary and decision

Concordat must reject a repository-owned shell snippet when its cyclomatic
complexity is **three or greater**, or when it contains **any loop**. These are
independent conditions: a single loop fails even when its complexity is two. The
largest permitted complexity is two; no configuration may raise that limit or
turn off loop detection.

Shell remains a thin command-launching layer with, at most, one trivial
conditional. Behaviour beyond that boundary belongs in checked-in, external
Python scripts. Those scripts must consume configuration through Cyclopts-backed
environment variables, satisfy the consuming repository's Python unit-test and
linting standards, and use the actual `cmd-mox` package for external-command
mocking. Moving the same algorithm into a shell file, an interpreter one-liner,
or an untested Python file does not satisfy the rule.

This request for comments (RFC) defines the policy, extraction boundaries,
implementation interfaces, and acceptance evidence. It does not add enforcement,
dependencies, generated policy artefacts, or estate-wide remediations in the
design change.

## 2. Context and existing contracts

The [scripting standards](../scripting-standards.md) already prescribe Python
3.13, `uv`, environment-first Cyclopts configuration, `pathlib`, and structured
external commands through Plumbum. This rule makes that existing direction
machine-checkable rather than introducing a second scripting convention.

At the baseline revision:

- `concordat/rules/envelope.py` constructs only the
  `policy-input/rust-makefile-baseline` envelope. The runner cannot acquire
  shell facts merely because another `rule.yaml` exists.
- `concordat/rules/runner.py` evaluates Conftest policies and returns existing
  `Finding` and `RuleRunResult` objects. Noncompliant and indeterminate results
  both exit with status 1; operational errors use status 2.
- `pyproject.toml` discovers `scripts/tests` and `tests`. The Makefile runs
  Ruff, spelling, blocking Skylos dead-code checks, `ty`, and pytest with xdist.
- `tests/conftest.py` defines its own `CmdMox` and `cmd_mox` fixture by patching
  `subprocess.run`. This is not the upstream `cmd-mox` package, which is absent
  from the project's declared dependencies. A similarly named fixture is not
  evidence that the requested mocking standard has been met.

The [assistant instructions](../../AGENTS.md), [developer
guide](../developers-guide.md), and current Makefile remain the quality
authorities. Recheck them during implementation: open work such as PR #110
proposes additional Python gates but is not part of this baseline. The estate's
planned PY-001 through PY-010 checks remain complementary; adding helper scripts
makes an otherwise non-Python repository subject to its applicable Python
standards.

## 3. Applicability and snippet boundaries

The policy applies to repository-owned automation regardless of the repository's
primary language. Discovery must not depend on `Cargo.toml`, a Python package,
or the presence of a top-level `scripts` directory.

The first complete release must cover these authoring surfaces:

| Surface | Unit of analysis |
| --- | --- |
| GitHub workflows, including reusable workflows | Each complete `jobs.*.steps[*].run` scalar in `.github/workflows/*.yml` and `*.yaml` |
| Local composite actions anywhere in the checkout | Each `runs.steps[*].run` in `action.yml` and `action.yaml` |
| GNU Makefiles and statically resolvable included fragments | Every complete target recipe, plus each `$(shell ...)` or `!=` shell body |
| Standalone shell | Each tracked shell file, including `.sh`, `.bash`, recognized shebangs, and statically referenced extensionless scripts |
| Package-manager scripts | Each `package.json` script whose effective executor is a supported shell |
| Container build instructions | Each shell-form instruction, including `RUN`, and statically identified shell invocations in exec form |

Table 1: Mandatory discovery surfaces and their authoring boundaries.

Within those surfaces, inspect literal nested `sh -c` and `bash -c` programs,
command substitutions, process substitutions, and shell-fed here-documents.
Follow repository-local shell invocations and `source`/`.` references when their
paths are statically resolvable. Keep caller and callee locations. A referenced
external shell file remains subject to the same rule; it is not remediation.

Scan tracked content, not just executable permission bits or changed lines. Skip
dependency caches, version-control internals, and non-owned vendored content
using explicit inventory rules. Documentation fences and deliberately invalid
test fixtures are data unless an automation entry point executes them. A path
under `tests/` is not automatically exempt when a real workflow runs it.

The first grammar supports Portable Operating System Interface (POSIX)-style
`sh` and Bash. Resolve workflow shell selection from step, job, and workflow
defaults, then documented platform and container defaults.[^1] Do not parse
PowerShell, `cmd`, fish, zsh, custom shell wrappers, or unresolved runner
matrices as Bash. Inventory unsupported executable surfaces with an
indeterminate finding; never report them as inspected and clean. Remote actions
and workflows belong to their owning repositories and require separate audits of
pinned source, not downloads during local evaluation.

Arbitrary shell strings embedded in application source, dynamically generated
build systems, and other host languages require later adapters. Reports must
name the implemented adapters and unresolved surfaces: this release must not
claim to prove the absence of shell in every possible language or encoding.
Adding an adapter does not change the normative policy.

## 4. Cyclomatic complexity contract

Use a versioned, syntax-based profile named `concordat-shell-cc/v1`, not regular
expressions or line counts. For a non-empty analysis unit, compute `M = 1 + D`,
where `D` is the sum of the decisions below. Empty or comment-only units have `M
= 0`. This is a deliberately conservative policy profile, not a claim to
reconstruct every runtime control-flow edge of a shell interpreter.

| Construct | Contribution to `D` |
| --- | --- |
| `if` and each `elif` | One each; `else` contributes zero |
| Shell-list `&&` and `\|\|` | One per operator, including operators in an `if` condition |
| Boolean `&&` and `\|\|` inside `[[ ... ]]` or arithmetic expressions | One per operator |
| Arithmetic conditional `condition ? a : b` | One per conditional expression |
| `case` | One per non-default arm; a final standalone `*` default contributes zero |
| `for`, arithmetic `for`, `while`, `until`, and `select` | One each, and an independent loop violation |
| Conditional parameter expansions using `-`, `+`, `=`, or `?`, with optional `:` | One per expansion |

Table 2: Decision contributions in the Concordat shell complexity profile.

A `case` pattern list such as `a|b)` is one arm, not two. Case fall-through
operators `;&` and `;;&` are nontrivial control flow and fail Section 5
regardless of the score. Handle legacy `test`/`[` compound `-a` and `-o`
expressions only when their operand structure is unambiguous, adding one
decision per operator; otherwise report indeterminate rather than guessing that
an option-looking argument is a decision.

Do not count ordinary comparisons twice: `[ -f "$path" ]` adds no decision
beyond its enclosing `if`. Non-conditional expansions, sequencing, pipelines,
redirects, negation, `else`, `return`, `exit`, and shell options add no
decisions. Do not model implicit command failure, `errexit`, or `pipefail` as
extra decisions. Only executable syntax counts: quoted words such as `"for"` and
literal here-document data do not, but substitutions executed inside those
constructs do.

Count the whole authoring unit, including decisions inside subshells,
substitutions, function bodies, and statically decoded nested shell programs.
There is only one base contribution per unit: embedding a literal `bash -c`
program adds its decisions, not another base of one. Do not reset the budget per
line, function, subshell, or command. For Make, aggregate decisions over the
whole target recipe even when Make launches separate shells; retain the true
execution boundaries in facts. This intentionally prevents line splitting from
weakening the policy.

Traversal must find loops everywhere, including unreachable branches and
function bodies. The policy does not run constant folding to excuse a loop that
currently has no iterations. External shell files have their own whole-file
scores; semantic splitting of one algorithm across files or workflow steps is
still contrary to the Python requirement, although arbitrary equivalence between
such programs is not statically decidable by this sensor.

## 5. Trivial glue and prohibited workarounds

A passing snippet may contain fixed command invocations, assignments, quoting,
redirects, simple pipelines, shell setup, and one conditional within the budget.
For example, one `if`/`else`, one `command || exit 1`, or one `test && command`
may pass. A short snippet is not necessarily trivial: `a && b || c` has `M = 3`.
Passing this rule does not exempt error swallowing or other defects from the
existing quality-gate integrity rules.

The following constructs exceed the glue boundary even with `M < 3`:

- Shell function definitions, programmable traps, case fall-through, and custom
  background-job coordination. Keep reusable behaviour and lifecycle management
  in Python rather than shell subprograms.
- `eval`, runtime-generated executable shell, and dynamically supplied shell
  command bodies. Literal nested shell is analysed recursively, not hidden
  behind its outer invocation. Unresolved shell-file paths produce an
  indeterminate result rather than an assumed violation or pass.
- Inline replacement programs, including `python -c`, Python fed on standard
  input or through a here-document, `shell: python`, and equivalent custom
  algorithm bodies passed to other interpreters. A Python here-document is not
  an external, tested Python script. Recognized inline programs fail; unknown
  interpreter boundaries remain explicit analysis gaps.
- Direct GitHub expression interpolation into executable `run` text, including
  inside shell quotes. Put values in `env` and consume them as data. Masking an
  expression with a harmless placeholder must not turn a dynamic program into a
  compliant static one. GitHub's security guidance also recommends separating
  expression values from script source.[^2]

Existing commands with fixed arguments may perform substantial work internally;
this rule regulates repository-authored glue, not the implementation of `git`,
`uv`, or another tool. It is a maintainability policy, not a sandbox or a proof
that an adversary cannot disguise an interpreter behind an arbitrary executable.

## 6. External Python contract

### 6.1. Source, inputs, and command execution

Place substantive logic in an importable, checked-in Python module, normally
under `scripts/`, with a small guarded entry point. Resolve its location using
the caller's effective working directory; action-relative scripts must resolve
against the local action directory, not an assumed checkout root. Missing,
generated, or out-of-checkout replacement scripts cannot pass the contract.

All configurable automation inputs must have typed Cyclopts bindings using
`cyclopts.config.Env` or explicit `Parameter(env_var=...)` declarations.[^3] Do
not reconstruct argument lists in Bash, parse booleans or lists manually, or
read configurable inputs through scattered `os.getenv` calls. Optional CLI flags
for non-secret local use remain valid with the documented precedence CLI over
environment over default; automation callers pass configuration through
environment variables. Secret inputs remain environment-only or secret-store
backed and must not appear in arguments or diagnostic dumps.

A launcher may look like this, assuming the consuming project declares and locks
the script's runtime dependencies:

```yaml
- name: Publish artefacts
  shell: bash
  env:
    INPUT_VERSION: ${{ inputs.version }}
    INPUT_DRY_RUN: ${{ inputs.dry_run }}
  run: uv run --locked python scripts/publish.py
```

The corresponding entry point uses, for example,
`App(config=cyclopts.config.Env("INPUT_", command=False))`, with typed `version:
str` and `dry_run: bool` parameters. Test the actual Cyclopts application
boundary, not just direct calls to its decorated function. Zero-input scripts do
not need invented parameters; scripts with configurable inputs cannot satisfy
this rule with an unused Cyclopts import.

Retain the consuming repository's Python version, metadata, dependency-locking,
and documentation requirements. In Concordat, that includes Python 3.13 and the
script metadata described in the scripting standards. Project-mode execution
uses the project lock; direct `uv` script-mode execution must use its
appropriate script lock. Declare test-only dependencies such as `cmd-mox` in the
development environment rather than adding them to production solely for this
policy.

Use Plumbum's structured command interface as required by Concordat's scripting
standards; other consumers retain their approved structured Python command
runner. Never move the original program unchanged into `shell=True`, `eval`, or
`bash -c` inside Python. Preserve explicit working directories, argument
boundaries, exit status handling, output semantics, and timeouts.

### 6.2. Unit tests and quality gates

The Python implementation and its tests must participate in the repository's
normal test, formatting, lint, type-checking, and applicable coverage gates.
Presence of `test_publish.py` alone is not sufficient. Inspect effective
discovery and exclusion settings, aggregate target wiring, and workflow
conditions; reject scripts or tests excluded from the normal gates and gates
whose failures are ignored. Reuse PY and QG facts where available without
waiting for every planned estate rule to ship.

For Concordat, acceptance requires `make test`, `make lint`, `make check-fmt`,
and `make typecheck` to include the new modules. This includes Ruff, spelling,
Skylos, `ty`, and pytest/xdist at the inspected baseline. Do not introduce a
weaker scripts-only lint configuration or freeze the requirement to today's tool
list. Apply the consuming repository's existing coverage thresholds; this RFC
does not invent a replacement percentage.

Unit tests must exercise successful and failing branches, missing and malformed
environment inputs, defaults, declared list splitting, paths containing spaces,
Unicode, empty values, command failures, output handling, and absence of side
effects on import. Environment tests may use pytest's `monkeypatch`; Cyclopts
provides examples of this testing approach.[^4]

Mock external commands with upstream `cmd-mox`, asserting command names,
arguments, relevant environment and working directory, outputs, return codes,
and unused or unexpected calls.[^5] Do not substitute the existing Concordat
lookalike fixture or hand-written `subprocess.run` stubs. Pure computation need
not acquire a meaningless command mock. Resolve commands after the mocking
context is active so cached absolute paths cannot bypass its shims.

Concordat's adoption must add a compatible locked development dependency and
remove fixture shadowing. Either migrate existing fixture consumers, or rename
the legacy harness and explicitly bind new tests to the real package. Tests must
prove the chosen fixture comes from cmd-mox and intercepts a real child-process
boundary, rather than asserting only a matching fixture name.

Static checks establish wiring and recognizable contracts; they cannot prove
that an assertion meaningfully tests an algorithm. The trusted repository
continuous integration (CI) must actually run those tests and gates at the
reviewed revision. The local Auditor must not run untrusted repository tests,
import scripts, or invoke `pytest --collect-only` to manufacture that evidence.

## 7. Extraction, parsing, and trust boundaries

Implement typed discovery and fact extraction in Python, with a pinned
Tree-sitter runtime and Bash grammar behind a small parser adapter. Keep policy
classification in Conftest/Rego. Pin compatible grammar/runtime versions and
record them with the complexity-profile version; grammar changes require fixture
review rather than silently changing scores.

Dependency installation belongs to environment preparation, never to the local
audit. Record and verify the installed parser versions before collecting facts.

Use source-aware YAML parsing to retain scalar style, indentation, anchors,
alias provenance, and source ranges. Decode folded and quoted scalars according
to YAML semantics, then map parser offsets back to the original file. Handle
CRLF and UTF-8 byte-to-column conversion deliberately. When a precise subrange
cannot be represented, locate the containing scalar and explain the reduced
precision instead of inventing a line or column.

Reuse the existing `makeutil` boundary for Make syntax. Never invoke `make`,
`make -n`, a shell, or repository helper code to expand recipes: expansion
itself can execute commands. Resolve only supported static values and includes;
handle recipe prefixes, continuations, `.ONESHELL`, `$$`, and shell selection
with source maps. Extend makeutil upstream when its facts are insufficient
rather than adding another ad-hoc Make parser to Concordat. Unknown expansion or
a recovered Make parse is indeterminate.

Tree-sitter can recover erroneous input. Reject both `ERROR` and `MISSING` nodes
as incomplete analysis, even when a partial tree has a low score.[^6] An unknown
executable grammar construct, unsupported dialect, undecodable input, unresolved
include, or exhausted resource limit must never yield a clean score. Retain any
independently proven violations alongside the analysis-gap finding.

The collector is read-only and offline. It must not fetch remote source, expand
environment variables using the auditor's environment, follow symlinks outside
the checkout, or execute templates and command substitutions. Bound file size,
node count, nesting, include recursion, and total work. Use visited-source keys
to detect cycles. Record bounded diagnostics and hashes, not complete shell
bodies or secret-bearing environment values. Hostile fixtures must demonstrate
that no sentinel file, network request, or child process results from parsing.

## 8. Rule package and runner integration

Ship the package under
`platform-standards/canon/lint-rules/shell-snippet-baseline/`, containing
`rule.yaml`, a README, Rego policy, policy tests, and fixture data. Register it
in `platform-standards/canon/manifest.yaml`, including integrity metadata, and
verify both source-checkout and installed-wheel discovery. Extend `rule
validate` when that command lands; do not present it as an existing capability.

Keep `sensor.type: conftest` and the existing namespace convention
`canon.lint_rules.shell_snippet_baseline`. This local source rule needs no
GitHub API sensor or actuator. The initial package declares no mutations.

Add a trusted, explicit package-to-envelope-builder registry in
`concordat/rules/`, selecting the builder before collecting facts. Preserve the
existing Rust envelope and default behaviour unchanged. Do not dynamically
import repository-supplied Python from `rule.yaml` and do not route this rule
through the Rust builder or require makeutil for a workflow-only checkout.

The new envelope uses `schema_version: 1` and `kind:
policy-input/shell-snippet-baseline`. Its validated schema includes:

- Repository revision, package/profile/tool versions, enabled adapters, and a
  complete inventory with analysed, excluded-with-reason, and unresolved counts.
- Per-snippet identity, host kind, effective shell and directory, source ranges,
  content hash, parse status, decision sites and kinds, score when complete,
  loop sites, prohibited constructs, and nested-source provenance.
- Python entry-point facts: resolved script path, typed environment bindings,
  configured input names, referenced tests, effective gate inclusion, and
  explicit unknowns. Never carry input values or claim test execution evidence
  that the collector did not acquire.

Use Python's abstract syntax tree for recognizable Cyclopts declarations and
repository-local wrappers, without importing them. Resolve aliases and simple
re-exports with bounded traversal. Dynamic application factories or ambiguous
test/gate wiring are indeterminate, not automatically noncompliant. Explicit
rule-owned mappings may disambiguate paths, bindings, and tests, but must
validate against source and cannot assert that tests passed or disable findings.
Introduce any mapping schema through the rule-package parameter contract, not an
unrelated unvalidated manifest file.

Add location and evidence fields to findings compatibly, retaining the existing
required fields and CLI exit codes. Keep JSON ordering and fingerprints stable;
Static Analysis Results Interchange Format (SARIF) integration must preserve the
same finding IDs and original source ranges.

## 9. Findings and enforcement

| Identifier | Condition | Remediation |
| --- | --- | --- |
| SH-001 | `M >= 3` or any loop | Extract the behaviour into tested external Python; report the score and every loop independently |
| SH-002 | A proven nontrivial or inline-code workaround from Section 5 | Remove executable glue and use an external Python entry point with environment inputs |
| SH-003 | A proven missing Python input, test, mocking, or quality-gate contract | Add the actual Cyclopts bindings, upstream cmd-mox tests, and normal gate inclusion |
| SH-004 | Analysis cannot establish the applicable contract | Resolve the named parser, shell, path, or evidence gap; never treat uncertainty as compliance |

Table 3: Proposed findings within the single shell-snippet-baseline package.

SH-001 through SH-003 are noncompliant. SH-004 is indeterminate. Use stable
reason codes such as `complexity`, `loop`, `inline-program`,
`missing-env-binding`, `tests-excluded`, and `unsupported-shell`, without
including source text in fingerprints. A snippet with both a loop and a high
score retains both reasons.

A complete clean evaluation exits 0. Any policy violation or analysis gap exits
1; failure to initialize tooling, read required inputs, or execute Conftest
exits 2 through `OperationalRuleError`. Both non-zero outcomes block
enforcement, but remain distinguishable in reports. Preserve the runner's
existing verdict aggregation and retain all findings when uncertainty and
noncompliance coexist.

Audit-only rollout changes whether a check blocks merging, not its computed
verdict. Enforcement promotes the unchanged findings to error-level merge gates.
Do not add baseline-file suppression, inline ignores, or a configurable
complexity ceiling. Existing estate exemptions, where authorized, must retain
the original finding, owner, justification, scope, and expiry; they do not make
the program compliant or alter its measured complexity.

## 10. Acceptance and adversarial tests

Write unit, behavioural, and policy tests before implementation, following the
repository's test-first standard. The following examples form the minimum
boundary corpus; each must assert score, loop facts, verdict, and source
location as applicable.

| Fixture | Required result |
| --- | --- |
| Empty text, comments, or a quoted string containing `if` and `for` | No false decisions or loops |
| One fixed command or a plain pipeline | `M = 1`, no SH-001 |
| One simple `if`/`else` or `test && command` | `M = 2`, no SH-001 |
| Two sequential `if` statements, nested `if`, or `if` plus `elif` | `M = 3`, SH-001 |
| `a && b \|\| c` or `if a && b; then c; fi` | `M = 3`, SH-001 |
| Two non-default case arms plus a default | `M = 3`, SH-001 |
| Each loop form, including arithmetic `for` and `select` | SH-001 even with only one decision |
| A loop hidden in a function, substitution, or literal `bash -c` | Loop violation at its original location |
| Literal here-document containing shell keywords | No false loop; executable substitutions still count |
| `python -c`, Python here-document, or `shell: python` | SH-002, not successful migration |
| Valid launcher with real Cyclopts bindings and normally gated tests | No SH-003 |
| Unused Cyclopts import, argv-only inputs, excluded tests, or fake cmd_mox | SH-003 when proven, never a presence-only pass |
| Recovered parse, unsupported shell, or dynamic source path | SH-004, never exit 0 |

Table 4: Minimum executable acceptance corpus.

Additional fixtures must cover every mandatory host, both YAML extensions,
folding and quoting, aliases, effective shell precedence, containers, mixed
runner matrices, working directories, local actions, Make continuations and
includes, `$$`, multiple recipe lines, CRLF, Unicode, source cycles, symlink
escape, malformed YAML, nested substitutions, and resource limits. Include all
parameter expansion variants and ambiguous `test` forms from the profile.

Add Hypothesis properties for whitespace/comment invariance, correct treatment
of quoted data, monotonicity when adding a decision, and unconditional rejection
when inserting a loop. Test two independent decisions across recipe lines and
nested programs to catch accidental budget resets. Mutation checks must kill
changes from `>= 3` to `> 3`, removal of each loop detector, omission of
short-circuit operators, and clean results from parser recovery.

Policy tests cover missing or malformed facts, correct namespace selection,
empty inventories, mixed proven violations and uncertainty, and stable output.
Behavioural tests exercise the actual CLI exit statuses and installed-wheel
package lookup. Scanner tests prove that audited commands never execute;
separate Python migration tests use upstream cmd-mox to exercise their intended
external-command boundaries safely. Record all ordinary repository quality-gate
results for implementation changes.

## 11. Migration, sequencing, and alternatives

The [roadmap, Section 4.4](../roadmap.md) separates parser/profile work, host
adapters, Python contracts, package integration, and adoption. Build on the
existing Section 1.2 rule runner and coordinate with Sections 4.2 and 4.3; the
local audit does not require live-API actuators or completion of unrelated
governance work.

First audit Concordat and representative Python, Rust-with-helper-scripts,
composite-action, and container/package-script repositories. Publish findings
and unresolved-source counts at pinned revisions. Migrate Concordat's own
violations with characterization tests and the real cmd-mox fixture, then enable
a mandatory check only after the required adapters and documented edge cases
pass. Do not silently weaken doctrine to improve pilot figures.

Each migration preserves command arguments, working directory, environment,
outputs, error handling, and timeouts; puts behaviour in Python; adds actual
input-boundary and command-contract tests; includes both script and tests in
normal gates; and reduces the caller to a fixed launcher. Keep these changes in
reviewed repository-specific pull requests. Do not mechanically translate shell
control flow as part of remediation.

Rejected alternatives are regex counting, ShellCheck alone, a line-count limit,
per-function budgets that hide aggregate decisions, moving logic into `.sh`, and
inline Python. None enforces the complete requested boundary. A bespoke shell
interpreter or generic program-equivalence engine adds unnecessary scope; a
versioned conservative syntax profile with explicit indeterminate outcomes is
the chosen trade-off.

## References

[^1]: [GitHub Actions workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
    particularly effective shell selection and `defaults.run`.
[^2]: [GitHub Actions secure use reference](https://docs.github.com/en/actions/reference/security/secure-use),
    guidance on script injection and intermediate environment variables.
[^3]: [Cyclopts environment configuration](https://cyclopts.readthedocs.io/en/latest/config_file.html)
    and [parameter documentation](https://cyclopts.readthedocs.io/en/latest/parameters.html).
[^4]: [Cyclopts unit-testing cookbook](https://cyclopts.readthedocs.io/en/latest/cookbook/unit_testing.html).
[^5]: [cmd-mox README](https://github.com/leynos/cmd-mox/blob/main/README.md)
    and [usage guide](https://github.com/leynos/cmd-mox/blob/main/docs/usage-guide.md).
[^6]: [Tree-sitter query syntax](https://tree-sitter.github.io/tree-sitter/using-parsers/queries/1-syntax.html),
    including distinct `ERROR` and `MISSING` recovery nodes.
