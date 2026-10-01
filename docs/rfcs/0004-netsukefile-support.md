# RFC 0004: Netsukefiles as a first-class build orchestrator

## Preamble

- **RFC number:** 0004
- **Status:** Proposed
- **Created:** 2026-10-01
- **Audit domain:** File and Content Presence (FP-003), Quality-Gate Integrity
  (design document Section 3.1.1), and the Markdown and spelling checks of the
  Toolchain Baseline (design document Section 3.1.3)
- **Check identifiers:** BO-001, BO-002, BO-003 (proposed); amends FP-003,
  QG-001, QG-002, PD-002 to PD-004, PD-006 to PD-008, PD-010 and PD-013
- **Depends on:** the `.concordat` manifest (design document Section 2.2), the
  pinned `makeutil` facts, which this RFC leaves unchanged, and the Netsuke
  v0.1.0-beta4 manifest schema (`netsuke_version: "1.0.0"`)

## 1. Summary

Concordat judges a repository's build entry points through its root `Makefile`.
FP-003 requires the file and its canonical targets, QG-001 proves the Whitaker
gate binding over the Make closure from `lint`, and the Markdown and spelling
packages prove `mdtablefix`, `markdownlint-cli2` and `typos-config-builder`
over the closures from `check-fmt`, `fmt` and `spelling`. A repository that
orchestrates its build with Netsuke offers none of those facts, and today it
can satisfy the standards only by keeping a Makefile that its continuous
integration (CI) does not run.

This Request for Comments (RFC) proposes that concordat resolve one
*authoritative build orchestrator* per checkout, Make or Netsuke, and judge
that orchestrator's entry points with the same checks at the same strictness.
It has four parts:

- **Resolution.** A `.concordat` declaration, `build.orchestrator`, is
  authoritative; absent one, the orchestrator is inferred from which root
  manifest exists. BO-001 reports a checkout whose orchestrator cannot be
  resolved.
- **Netsuke facts.** A strict reader decodes the root `Netsukefile` as YAML 1.2
  without rendering Jinja and without running Netsuke, and records a fact block
  beside the existing `makeutil` report in each envelope.
- **Shared checks over per-orchestrator closures.** Each policy's
  Make-specific graph walk becomes an adapter, and the recipe predicates —
  command word, binding tail, required flags — are shared. FP-003, QG-001,
  PD-002 to PD-004, PD-007 and PD-008 keep their identifiers and meaning.
- **CI invocation.** Workflow recognizers read `netsuke build <target>` where
  they read `make <target>` today. BO-003 does not require CI to run any gate;
  it requires that whatever CI does run through `make` or `netsuke` comes from
  the authoritative orchestrator. BO-002 admits a transitional shim Makefile
  only when each shared target delegates to Netsuke.

The Make path is unchanged in behaviour: every existing fixture, and every
envelope recorded in the Parabellum ledger, must replay to the verdict it has
today.

### 1.1 Why the existing identifiers are kept

FP-003, QG-001 and the PD checks state requirements about a repository's build
entry points: that `lint` exists, that it cannot pass while the gate fails, and
that `check-fmt` checks what `fmt` writes. None of that is a property of Make.
Netsuke twins of each identifier would split one requirement into two catalogue
rows that could drift apart, and a repository that migrated would change
identifiers for an unchanged obligation. The identifiers therefore stay, and
their catalogue wording generalizes from "Makefile target" to "entry point of
the authoritative orchestrator".

The new `BO` (build orchestration) family covers only what is new: deciding
which orchestrator is authoritative, and keeping a second one honest. `BO-001`
to `BO-003` collide with nothing in the design document's catalogue or in RFCs
0001 to 0003. RFC 0001 records that issue #153 proposes an `MK` family for
Makefile shape; the full identifier list of issue #153 has not been checked for
this RFC, and Section 10 records that check as open.

### 1.2 Relationship to RFC 0001

catnap #90 installs Netsuke with `cargo install --locked netsuke-build` on a
pinned nightly, which is a TA-001 finding under RFC 0001: Netsuke v0.1.0-beta4
publishes prebuilt binaries for `cargo binstall`, and standalone binaries with
Secure Hash Algorithm 256 (SHA-256) checksum files (Netsuke users' guide,
"Install Netsuke"). The canonical workflow change in Section 3.9 therefore
installs Netsuke as a verified binary, and this RFC introduces no acquisition
rule of its own.

## 2. Motivation

### 2.1 Where concordat assumes Make

Table 1 inventories every place the worktree assumes Make, read at the
`origin/main` revision this RFC was written against. Three packages read no
Makefile and are already orchestrator-neutral: `rust-build-defaults` excludes
Makefile facts deliberately (`concordat/rules/envelope.py:136-142`), and
`main-owned-codescene-coverage` and `dependabot-update-shape` read only
workflows and Dependabot configuration.

#### Table 1: Make assumptions in concordat

| Location                                                                              | Assumption                                                                                                                                                            | Effect on a Make-free checkout                                                 |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `concordat/rules/makefile_facts.py:1-5`, `:365-384`                                   | The only build-graph fact source is `makeutil parse` over a Makefile                                                                                                  | No facts exist for another orchestrator                                        |
| `concordat/rules/envelope.py:83-98`, `:106`                                           | `rust-makefile-baseline` parses `checkout / "Makefile"` alone, and `root_makefile` is its applicability flag                                                          | `makefile` is `null`                                                           |
| `concordat/rules/markdown_envelope.py:462-467`, `:475`                                | The same for `markdown-formatting-baseline`                                                                                                                           | `makefile` is `null`                                                           |
| `concordat/rules/spelling_envelope.py:251-254`, `:261`                                | The same for `spelling-config-baseline`                                                                                                                               | `makefile` is `null`                                                           |
| `concordat/rules/whitaker_provisioning_envelope.py:62-63`, `:213-215`                 | QG-002 reads `Makefile`, `GNUmakefile`, `makefile` and `*.mk` as text for install routes                                                                              | A `Netsukefile` recipe that installs Whitaker is never read                    |
| `rust_makefile_baseline.rego:118-122`                                                 | FP-003 reports "root Makefile is missing"                                                                                                                             | One FP-003 finding                                                             |
| `rust_makefile_baseline.rego:81-84`, `:488-496`                                       | Every QG-001 clause requires `has_makefile`                                                                                                                           | QG-001 reports nothing at all                                                  |
| `rust_makefile_baseline.rego:168-171`, `:270-276`                                     | Edges are prerequisites and literal `$(MAKE) target`; the gate is the Make variable `$(WHITAKER)`                                                                     | The grammar does not exist in a Netsukefile                                    |
| `markdown_formatting_baseline.rego:98-107`, `:444-448`                                | FP-003 for `fmt` and `check-fmt`; PD-002 to PD-004 walk the Make closure                                                                                              | One FP-003 finding; PD-002 to PD-004 report nothing                            |
| `markdown_formatting_baseline.rego:818`                                               | PD-006 reads `make … markdownlint` in a `run:` step as linting from a shell                                                                                           | `netsuke build markdownlint` is not recognized                                 |
| `spelling_config_baseline.rego:499-506`                                               | PD-007 reports "root Makefile is missing"                                                                                                                             | One PD-007 finding                                                             |
| `spelling_config_baseline.rego:683-686`, `:696`, `:708`                               | PD-010 recognizes a legacy helper target only after `make`, and its remediation says "run make spelling"                                                              | A bypass through `netsuke build` is unrecognized, and the remediation is wrong |
| `spelling-config-baseline/rule.yaml:110`, `:116`; `spelling_config_baseline.rego:932` | PD-013's published block tells contributors to run `make spelling`, and duplicate guidance is matched only in that spelling                                           | A Netsuke repository must carry false guidance to comply                       |
| `platform-standards/canon/.github/workflows/ci.yml:14`, `:78-95`                      | The canonical reusable workflow runs `mbake validate Makefile` and `make` for `build`, `check-fmt`, `lint`, `typecheck` and `test`; CI-001 requires callers to use it | A Netsuke repository cannot call it                                            |
| `docs/concordat-design.md:1790`, `:1792`                                              | The catalogue states FP-003 and QG-001 in terms of the `Makefile`                                                                                                     | The doctrine itself names Make                                                 |
| `docs/concordat-design.md:1810`, `:1832-1852`                                         | QG-004, PY-001, PY-002, PY-004, PY-010, RT-001, RT-003, RT-005 and RT-011 are planned as "Makefile parse" checks                                                      | Each planned check would repeat the gap                                        |
| `docs/users-guide.md:255-258`, `:580`                                                 | The users' guide describes FP-003 as a root `Makefile` requirement                                                                                                    | User-facing guidance names Make                                                |

### 2.2 The worked case: catnap #90

leynos/catnap #90 (open, branch `adopt-netsuke-v2`) deletes catnap's 98-line
Makefile and adds a 114-line `Netsukefile` for Netsuke v0.1.0-beta4, with no
compatibility shim. Its accepted decision record,
`docs/adr-001-netsuke-build-orchestration.md`, chooses Netsuke "as the sole
workflow manifest, and against retaining Make or keeping a Make compatibility
shim". Table 2 records how the former targets map.

#### Table 2: catnap's former Make targets in the Netsukefile

| Former Make target | Netsuke entry point                                    | Shape                                                                                                             |
| ------------------ | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| `all`              | `all`, the manifest default (`netsuke` with no target) | Dependency-only action; `deps: [check-fmt, lint, test, spelling]`, `dependency_order: serial`                     |
| `lint`             | `lint`                                                 | Dependency-only action over `rust-lint` and `github-actions-lint`, serial; replaces `$(MAKE) github-actions-lint` |
| (part of `lint`)   | `rust-lint`                                            | Command list: `cargo doc`, `cargo clippy`, then `PATH=… RUSTFLAGS=… whitaker --all -- …` as a literal command     |
| `check-fmt`        | `check-fmt`                                            | Command list: `cargo fmt --all -- --check`, then `mdtablefix --check` with the select and rule flags literally    |
| `fmt`              | `fmt`                                                  | Command list: `cargo +nightly fmt`, `mdtablefix --in-place …`, `markdownlint-cli2 --fix`                          |
| `test`             | `test`                                                 | Scalar command whose runner is chosen by `{% if command_available("cargo-nextest", …) %}` at manifest load        |
| `spelling`         | `spelling`                                             | Scalar command running `typos-config-builder gate` through `uv tool run --from …@v0.1.1`                          |
| `build`, `release` | `build`, `release`                                     | Scalar commands; the pattern rule `target/%/catnap` is gone                                                       |
| `help`             | `netsuke help targets`                                 | Built in; reads each entry's `description`                                                                        |

CI changes to match: `make check-fmt` becomes `netsuke build check-fmt`,
`make spelling` becomes `netsuke build spelling`, and
`/usr/bin/make ACTIONLINT=… lint` becomes `ACTIONLINT=… netsuke build lint`.
Both the pull-request and the main-branch coverage jobs gain a Netsuke cache, a
source install, and `ninja-build`.

Read against the policy text in Table 1, the current packages would report
catnap #90's head as follows. These verdicts are predicted from the policies,
not yet observed by a run, and Section 5 makes observing them the first gate.

- `rust-makefile-baseline`: one FP-003 finding, "root Makefile is missing",
  and nothing for QG-001.
- `markdown-formatting-baseline`: one FP-003 finding, and nothing for PD-002
  to PD-004.
- `spelling-config-baseline`: one PD-007 finding, "root Makefile is missing".

The shape of that result is the central hazard. One presence finding stands in
for the whole gate proof, so a change that merely taught FP-003 to accept a
`Netsukefile` would report catnap compliant without QG-001 ever having been
evaluated. A vacuous pass is worse than the current refusal, and invariant I2
in Section 3.1 exists to prevent it.

catnap #90 also shows what the gap costs an adopter. Its
`tests/netsuke_actions.rs` and `tests/netsuke_policy.rs` re-implement, in the
repository, the proofs concordat makes centrally for a Makefile: the exact
command words of each action, that a failing tool fails its action and stops
later commands, and the literal CI step text. Every future adopter would repeat
that work.

### 2.3 Why a shim Makefile does not settle it

A shim whose `lint` recipe is `netsuke build lint` satisfies FP-003 and fails
QG-001, because no recipe reachable from `lint` invokes `$(WHITAKER)`. To pass,
the shim must copy the real recipes, at which point concordat audits a file CI
does not run, and the copy drifts from the manifest CI does run. A required
shim therefore either fails or misleads. catnap's decision record rejects one
for a related reason: it "would preserve a second entrypoint and require
maintaining compatibility behaviour". Section 3.7 admits a shim as a
transitional convenience, and only in a form that cannot carry gates of its own.

### 2.4 The Netsuke semantics that matter

The design depends on these properties of Netsuke v0.1.0-beta4, each taken from
the Netsuke users' guide (`docs/users-guide.md` for that release):

- **Entry points.** `actions` are implicitly phony; `targets` are file or
  logical nodes; `defaults` are literal names, and Jinja is not rendered there.
  An action or target with a non-empty `deps` list may omit its recipe to form
  a dependency-only aggregate ("Author a manifest"; "Rules and recipes").
- **Edges.** `sources`, `deps` and `order_only_deps` name inputs; `rule` names
  a reusable recipe. `dependency_order: serial` runs direct `deps` in order and
  stops at the first failure ("Targets, inputs, and dependencies"; "Run direct
  dependencies serially").
- **Recipes.** A recipe is one `command` string, an ordered `command` list, a
  `script`, or a `rule`. A list stops at the first non-zero entry. On Unix each
  entry runs in its own brace group, the groups are joined with `&&`, and a
  later entry inherits the working directory, environment and shell variables
  an earlier entry leaves behind; a successful direct `exec` ends the chain.
  Scripts run under `/bin/sh -e` ("Rules and recipes"; "Review the safety
  boundary").
- **Windows.** On Windows every legacy recipe runs under Windows PowerShell
  unless `NETSUKE_WINDOWS_SHELL=bash` selects the Bash route, and PowerShell
  does not perform POSIX `${VAR:-default}` expansion ("Windows legacy recipe
  contract").
- **Manifest time.** Jinja, `foreach` and `when` are evaluated when the
  manifest loads, and the template library includes host-observing helpers
  (`shell`, `fetch`, `which`, `command_available`, `env`, `glob`). The guide
  calls a `Netsukefile` "executable build configuration, not passive data"
  ("Understand the build model"; "Select optional tools").
- **Strictness.** The manifest is YAML 1.2; unknown fields and duplicate
  mapping keys are errors ("Author a manifest").
- **Invocation.** The command shape is `netsuke [OPTIONS] build [TARGETS]...`,
  with global options before the subcommand. `netsuke` alone builds the
  defaults; "a bare target such as `netsuke hello` is not accepted". `-f`, `-C`,
  `--config`, `NETSUKE_FILE` and `NETSUKE_CONFIG` select another manifest or
  configuration, and a project `.netsuke.toml` takes part in configuration
  ("Use the command-line interface"; "Configure Netsuke").
- **Query mode.** `netsuke help targets` loads, expands and validates the
  manifest with a side-effect-free Jinja surface, skips recipe bodies, and
  marks entries whose `when` it cannot resolve as conditional ("Generate and
  inspect artefacts").

## 3. Design

### 3.1 Invariants

Every clause below is subordinate to five invariants. A clause that would break
one is wrong, whatever else it achieves.

- **I1 — The audited graph is the graph CI runs.** Concordat proves gates in
  the manifest that CI invokes, never in a second file maintained beside it.
- **I2 — Netsuke is never judged less strictly than Make.** Each construct
  that makes a Make proof indeterminate or noncompliant has a Netsuke
  counterpart that does the same. No check passes because its facts are absent.
- **I3 — Sensors stay hermetic.** Fact extraction reads files. It never runs
  the manifest, renders Jinja, consults the host's `PATH`, or reaches the
  network, matching the `conftest` sensor contract that RFC 0002 Section 3.3
  cites from design document Section 2.1.2.
- **I4 — No Make regression.** For a checkout whose authoritative orchestrator
  is Make, every existing fixture and every recorded envelope yields exactly
  its current finding set.
- **I5 — Fail closed.** A construct the reader or a policy cannot prove is
  `indeterminate`, never a pass.

### 3.2 Resolving the authoritative orchestrator

A new optional `.concordat` field, `build.orchestrator`, takes `make` or
`netsuke` and is authoritative when present, following the precedent that a
declared `language.rust.surfaces` list is authoritative (design document
Section 2.2.1). Absent a declaration, the orchestrator is inferred from the
regular files `Makefile` and `Netsukefile` at the checkout root, through the
same containment guard the envelope builders already apply to the Makefile.

#### Table 3: Orchestrator resolution

| `build.orchestrator` | Root `Makefile` | Root `Netsukefile` | Project `.netsuke.toml` | Authoritative | Finding                                                                               |
| -------------------- | --------------- | ------------------ | ----------------------- | ------------- | ------------------------------------------------------------------------------------- |
| absent               | present         | absent             | not consulted           | Make          | None from resolution                                                                  |
| absent               | absent          | present            | absent or proven inert  | Netsuke       | None from resolution                                                                  |
| absent               | absent          | present            | redirects or unprovable | none          | BO-001, `indeterminate`: the configuration may select another manifest or defaults    |
| absent               | present         | present            | not consulted           | Make          | BO-001, `indeterminate`: both manifests exist and neither is declared                 |
| absent               | absent          | absent             | not consulted           | none          | The packages' existing presence findings (FP-003, PD-007), now naming either manifest |
| `make`               | any             | any                | not consulted           | Make          | None from resolution; a `Netsukefile` is not audited, and BO-003 governs CI           |
| `netsuke`            | any             | present            | absent or proven inert  | Netsuke       | None from resolution; a `Makefile` is a shim judged by BO-002                         |
| `netsuke`            | any             | present            | redirects or unprovable | none          | BO-001, `indeterminate`: the configuration may select another manifest or defaults    |
| `netsuke`            | any             | absent             | not consulted           | none          | BO-001, `noncompliant`: the declared manifest is missing                              |
| any other value      | any             | any                | not consulted           | none          | BO-001, `indeterminate`: the declaration cannot be read                               |

The fourth row keeps Make authoritative so that adding a `Netsukefile` to a
Make repository changes nothing about its gates, which is what I4 asks; BO-001
asks the repository to say which file CI should be held to. A project
`.netsuke.toml` can select another manifest path and configure default targets,
so it is consulted on every row where Netsuke would otherwise be authoritative.
It is "proven inert" when the reader shows it sets neither, through its own
keys and its `extends` chain. A configuration that redirects, or that the
reader cannot prove inert, leaves no authoritative orchestrator, because
neither the root `Netsukefile` nor an unaudited selected manifest can be held
to be what CI runs. Where Make is authoritative, Netsuke configuration cannot
change what Make runs, so it is not consulted. The configuration keys involved
are taken from Netsuke's sample configuration in step 1 rather than assumed
here.

BO-001 ships in a new package, `build-orchestration-baseline`, so a resolution
problem is reported once rather than once per package. The other packages read
the same resolution from their envelopes and, where it yields no authoritative
orchestrator, report their existing presence finding with a message that points
at BO-001.

### 3.3 Netsuke facts

`makefile_facts.py` states the existing doctrine: concordat never parses GNU
Make syntax itself, and `makeutil parse` is the sole source of Makefile facts.
The Netsuke equivalent is narrower, because the manifest is already structured
data of the kind concordat decodes for workflows. The reader therefore decodes
YAML structure, and it never interprets Jinja. Rendering Jinja would mean
running Netsuke's template library, whose host helpers make the answer depend
on the auditing host, which I3 forbids.

The reader:

- decodes the root `Netsukefile` as YAML 1.2 and refuses a document with a
  duplicate mapping key, an unknown field, or a `netsuke_version` it does not
  model, because Netsuke refuses each of those and a reader that accepted them
  would describe a manifest Netsuke never builds;
- records every rule, action and target with its source line, the literal
  names it defines, its `deps`, `sources`, `order_only_deps`, `rule`,
  `dependency_order`, any `when` or `foreach`, and its recipe;
- keeps each recipe string verbatim, marks whether it contains Jinja, and
  substitutes only a bare `{{ name }}` whose global `vars` entry is a literal
  string free of Jinja, which mirrors the policies' single-assignment rule for
  Make variables;
- records the literal `defaults` list; and
- reports a refusal as a parse status the policies read as `indeterminate`, in
  the way they read a recovered `makeutil` parse.

The fact block's proposed shape, shown for the start of catnap #90's
`rust-lint` action with its third list entry omitted:

```json
{
  "schema_version": 1,
  "source": {"path": "Netsukefile"},
  "parse": {"status": "complete", "reason": null},
  "netsuke_version": "1.0.0",
  "vars": {},
  "rules": [],
  "entries": [
    {
      "section": "actions",
      "names": ["rust-lint"],
      "names_literal": true,
      "line": 51,
      "conditional": false,
      "deps": [],
      "sources": [],
      "order_only_deps": [],
      "dependency_order": "parallel",
      "rule": null,
      "recipe": {
        "form": "command-list",
        "entries": [
          {"text": "RUSTDOCFLAGS=\"-D warnings\" cargo doc --no-deps", "line": 54, "jinja": "none"},
          {"text": "cargo clippy --all-targets --all-features -- -D warnings", "line": 55, "jinja": "none"}
        ]
      }
    }
  ],
  "defaults": ["all"]
}
```

Table 4 states how each Make construct the policies already handle maps onto
Netsuke, and how the reader and policies treat it.

#### Table 4: Make constructs and their Netsuke counterparts

| Concern           | Make (`makeutil` facts)                               | Netsuke (reader facts)                                                    | Treatment                                                        |
| ----------------- | ----------------------------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Entry point       | A rule's target                                       | An action or target `name`                                                | Literal names only; any templated name makes absence unprovable  |
| Static edge       | A prerequisite; a literal `$(MAKE) target` chain      | A `deps`, `sources` or `order_only_deps` string naming an entry           | Other strings are file inputs, not edges                         |
| Recipe reference  | No counterpart                                        | A literal `rule` naming a reusable rule                                   | The rule's recipe is the entry's recipe; it is not an edge       |
| Dynamic edge      | `$(MAKE) $(VAR)`, `$(MAKE) -C`                        | A templated dependency or `rule`                                          | `indeterminate` on the path                                      |
| Conditional       | A rule with `conditions`                              | An entry with `when` or `foreach`                                         | `indeterminate` on the path                                      |
| Ambiguous entry   | Several rules, or a double-colon rule, for one target | One name defined by more than one entry, as complementary `when` pairs do | `indeterminate`                                                  |
| Include           | An `include` directive                                | None in the manifest; a `.netsuke.toml` manifest redirect                 | `indeterminate` (BO-001 for the redirect)                        |
| Unreadable facts  | A recovered parse                                     | A refused document                                                        | `indeterminate`                                                  |
| Variable          | One unconditional, non-`define` assignment            | A bare `{{ name }}` over a literal global `vars` string                   | Substituted; any other Jinja is unresolved                       |
| Recipe unit       | One recipe line in its own shell                      | One scalar `command`, or one list entry sharing a shell with its list     | Section 3.5                                                      |
| Error suppression | The `-` prefix                                        | No prefix exists; suppression is shell text such as `\|\| true`           | Make's `-`, `@` and `+` prefixes are not read in Netsuke recipes |
| Script            | No counterpart                                        | `script:`                                                                 | `indeterminate` in the first slice                               |

### 3.4 Entry points and required targets

FP-003 keeps its parameters. `required_targets` names entry points, which for
Netsuke are action or target names invoked as `netsuke build <name>`. A
dependency-only aggregate is a defined entry point: catnap's `lint` defines no
command and is the correct `lint`. "Unconditionally" carries over as "with no
`when` or `foreach`". Jinja inside the recipe body does not make the entry
conditional: catnap's `test` is defined unconditionally even though its runner
is chosen at manifest load, and FP-003 asks only that `test` exists. PD-008
carries over in the same terms: its legacy helper targets are entry names, and
its legacy pins are `vars` keys of the same names.

That same `test` action is where a later rule will need a decision. QG-004 asks
for the canonical `TEST_CMD` nextest fallback, and in Netsuke the equivalent is
necessarily Jinja, either `command_available` in the recipe or a complementary
`when` pair. Neither is readable under I3. QG-004 is planned, not shipped, so
Section 10 records the question rather than this RFC answering it.

### 3.5 Closure and recipe checks

Each of `rust-makefile-baseline`, `markdown-formatting-baseline` and
`spelling-config-baseline` currently mixes two concerns: walking the Make graph
from a root, and judging a recipe's text. The proposal separates them inside
each package's Rego:

- an **adapter** per orchestrator answers `entry_defined(root)`,
  `closure_status(root)`, which is provable or carries the reason it is not, and
  `path_recipes(root)`, the set of recipe records reachable from the root,
  each carrying its text, line, shell context and position within the context;
- the **checks** consume only those three answers, plus the shared
  shell-reading predicates the packages already contain: the command-word test,
  the environment-assignment prefix, the binding tail, and the soft-skip
  readings.

The Make adapter is the existing code moved without change, which I4 makes
testable: the refactor is correct only if every current fixture verdict is
byte-identical. The Netsuke adapter is new and applies these rules.

- **The gate reference.** QG-001's `gate_variable` names a Make variable. A
  new parameter, `gate_executable` (default `whitaker`), names the executable,
  which the design document already admits: "a reachable recipe referencing the
  gate variable or executable" (Section 2.2.1). In a Netsuke recipe the gate is
  proven where the command word is `whitaker`, a path ending `/whitaker`, or a
  substituted `{{ name }}` that resolves to either. A shell default such as
  `${WHITAKER:-whitaker}` is `indeterminate` until the doctrine decision in
  Section 10 is taken.
- **Prefixes.** Make's recipe prefixes `-`, `@` and `+` do not exist in
  Netsuke. In a Netsuke recipe, `-whitaker` and `@whitaker` name other
  programs, so the Netsuke adapter never strips them and never credits them.
  POSIX environment assignments before the command word are read exactly as
  they are for Make, which is what lets catnap's
  `PATH="…" RUSTFLAGS="-D warnings" whitaker --all -- …` count.
- **Binding.** Within one scalar command or one list entry, the existing tail
  rule applies unchanged: arguments and `&&`-chained commands may follow the
  gate, and `;`, `|`, a bare `&` or `|| true` mask it. Between list entries,
  binding holds by construction, because Netsuke joins entries with `&&` and
  stops at the first failure; catnap's `public_action_propagates_tool_failure`
  test observes this for every action.
- **Shared shell context.** List entries share one shell, so earlier entries
  can change where a later one runs. A directly preceding entry that is exactly
  `cd <dir>` qualifies a gate for the surface at `<dir>`, as
  `cd <dir> && $(WHITAKER)` does in Make, and disqualifies the root surface.
  Any other preceding entry in which the command word of any command segment
  changes shell state (`cd` elsewhere, `pushd`, `popd`, `export`, `unset`,
  `set`, `trap`, `source`, `.`, `eval`, `alias`, `shopt`, `umask`), or that is
  a bare assignment, makes the gate's context `indeterminate`.
- **Termination.** Entries run in the current shell, not a subshell, so an
  earlier entry can end the whole recipe before the gate is reached. A
  preceding entry that is a direct `exec` or a bare `exit`, in either case with
  any arguments, ends the chain: the gate never runs, and is reported as not
  reached. `exit 0` is the sharper case, because the recipe then succeeds. A
  preceding entry in which `exit`, `exec`, `return`, `kill` or `logout` is the
  command word of any segment, as in `test -f x || exit 0`, may end the chain
  on a condition the policy cannot evaluate, and makes the gate
  `indeterminate`. The words count only in command position, never inside
  quoted text, under the readings the policies already use for `which`. The
  same reading applies to a segment before the gate within one entry, as in
  `exit 0; whitaker --all`. The termination rule takes precedence over the
  shared command-word predicate: a gate it marks as not reached or
  `indeterminate` is never credited, whatever the command-word predicate says.
  For Netsuke, this holds from step 1 without condition. For Make, the shared
  predicate credits `exit 0; $(WHITAKER) --all` today, which is a pre-existing
  false negative. Correcting it is a change to the Make path, so it ships with
  step 1 only if gate G2 shows it alters no existing fixture or recorded
  verdict. If G2 shows a changed verdict, the Make correction becomes a
  separate change with its own review, and the Netsuke adapter applies the rule
  regardless.
- **Scripts and template control.** A `script:` recipe on the path is
  `indeterminate`, because `/bin/sh -e` and multi-line control flow are a
  different reading from the line-at-a-time grammar the predicates implement. A
  recipe with Jinja beyond a substituted variable is treated as an unresolved
  variable is today: `indeterminate` where the required tool could hide behind
  it.
- **Working directory.** Recipes run from the manifest's directory, so the
  root `Netsukefile` qualifies the root surface. An invocation that moves it,
  through `-C`, is handled by Section 3.6.

The shell readings are POSIX readings. They are what Netsuke runs on Unix and
on the explicit Windows Bash route, and not what it runs under Windows
PowerShell, where `RUSTFLAGS="-D warnings" whitaker` is not an environment
prefix at all. Section 3.6 therefore makes a Windows invocation on the
PowerShell route `indeterminate` rather than letting a POSIX proof stand for it.

### 3.6 CI invocation

Every rule that recognizes a Make invocation in a `run:` step learns the
Netsuke spelling, with one recognizer shared between packages. A Netsuke
invocation is a command word `netsuke`, or a path ending `/netsuke`, followed
by global options, then `build`, then zero or more targets, optionally after
`--`. With no targets it builds the manifest's literal `defaults`. Three
spellings are deliberately not invocations of the audited manifest:

- `netsuke <name>` without `build`, which Netsuke rejects, so the step fails
  loudly rather than bypassing anything;
- an invocation that may load another manifest or configuration, which is
  `indeterminate` exactly as `make -C` is (design document Section 2.2.1): one
  carrying `-f`/`--file`, `-C`/`--directory` or `--config`; one whose
  environment may set `NETSUKE_FILE` or `NETSUKE_CONFIG`; or one that may run
  outside the checkout root, where Netsuke would look for a different
  `Netsukefile`; and
- a bare `netsuke` where a `--default-target` option or project configuration
  could change the defaults, which is `indeterminate`.

The environment is read where the shell sets it, not only where the workflow
YAML does. GitHub Actions' merged `env:` at workflow, job and step scope is one
source. Within the same `run:` body, an assignment prefix on the invocation
(`NETSUKE_FILE=ci/Netsukefile netsuke build lint`) and an earlier `export` or
assignment of either variable are two more. A line in an earlier step of the
same job that writes either name to `$GITHUB_ENV` is a fourth. The working
directory is resolved as GitHub Actions resolves it: the step's
`working-directory:`, else the job's `defaults.run.working-directory`, else the
workflow's, and then any `cd`, `pushd` or `popd` before the invocation in the
same body. An effective directory other than the checkout root, or one given by
an expression the envelope cannot resolve, makes the invocation
`indeterminate`. An unrelated assignment prefix such as catnap's
`ACTIONLINT="…" netsuke build lint` changes nothing. Which further `NETSUKE_`
variables select a manifest, a directory or default targets is settled with the
configuration keys in step 1 (Section 10). The same reading applies to Make
invocations: `-f`, `--file`, `--makefile`, `-C`, `--directory` and a
`MAKEFILES` assignment select a graph other than the root `Makefile`.

The recognizer changes two existing clauses, corrects the remediation text of
three more, and adds one rule:

- **PD-006** treats `netsuke build … markdownlint` in a `run:` step as linting
  Markdown from a shell, as it treats `make … markdownlint`
  (`markdown_formatting_baseline.rego:818`).
- **PD-010** recognizes a legacy helper target reached through `netsuke build`
  (`spelling_config_baseline.rego:683-686`), and its remediation names the
  authoritative orchestrator's spelling, `netsuke build spelling` or
  `make spelling`.
- **PD-007, FP-003 and QG-001 remediation text** names the authoritative
  orchestrator throughout.
- **BO-003** reports every workflow step that invokes the orchestrator that is
  not authoritative, whatever target it names. `make lint` in a repository
  whose authoritative orchestrator is Netsuke is a finding, and so is
  `make ci`: a target with no Netsuke counterpart can carry the gates just as
  well, and CI would still run a graph concordat did not audit, which breaks
  I1. The only exemption is a Make invocation in which every named target is
  one BO-002 finds to be a conforming shim, and which passes no command-line
  variable assignment and no `-e`/`--environment-overrides`; either could
  replace the shim's shell or its recipe text, so such an invocation is
  `indeterminate`. A bare `make` names the Makefile's default goal, and is
  `indeterminate` where the facts cannot establish which target that is. The
  rule is symmetric: under Make authority, a `netsuke build` step in CI is a
  finding. An invocation of the authoritative orchestrator that may load
  another graph is `indeterminate`, as set out above. A step on a Windows
  runner that invokes Netsuke without `NETSUKE_WINDOWS_SHELL: bash` in its
  environment is `indeterminate`, for the reason Section 3.5 gives. A job that
  calls the canonical reusable workflow through `jobs.<id>.uses` runs whichever
  orchestrator its `build-orchestrator` input names, and the recognizer cannot
  see the commands inside the callee. BO-003 therefore reads the input itself:
  a literal value, or the default `make` when the input is omitted, that
  differs from the resolved authority is a finding, and a value given by an
  expression the envelope cannot resolve is `indeterminate`.

BO-003 is narrow in one direction and broad in the other. It does not require
CI to run any gate. It does require that whatever CI runs through an
orchestrator comes from the audited manifest, because restricting it to the
governed target names would let an unmatched target carry the real gates. A
second orchestrator invoked from a script that CI runs, rather than from a
`run:` body, is outside the recognizer's reach, as a script-driven Make
invocation is today; Section 10 records the gap.

### 3.7 A transitional shim (BO-002)

Where Netsuke is authoritative and a root `Makefile` also exists, the Makefile
is still parsed by `makeutil`, for BO-002 alone. Each Makefile target that
shares its name with a Netsuke entry point, or with a required target, must be
a shim: exactly one binding recipe line `netsuke build <same name>`, whose
command word is the literal `netsuke`, and no prerequisites other than other
conforming shim targets. A Make variable is not accepted in place of the
literal, even one with a single unconditional value of `netsuke`. The
environment (under `make -e`) or a command-line assignment can override such a
variable, so `NETSUKE=true make -e lint` would run `true build lint` and
succeed without running Netsuke. Section 3.6 makes the invocation-time
overrides that remain, such as `SHELL=…`, `indeterminate` under BO-003. Any
other recipe is BO-002 `noncompliant`, because a Makefile with gates of its own
is the second source of truth Section 2.3 rejects. BO-002 does not audit a Make
target with no Netsuke counterpart, because a developer convenience such as
`make install-tools` gates nothing. BO-003 closes the route such a target would
otherwise open: CI cannot invoke it without a finding, since it cannot be a
conforming shim when no same-named entry exists. The shim is permitted, never
required, and catnap #90 needs none.

### 3.8 Spelling guidance and Whitaker provisioning

PD-013 compares `AGENTS.md` with the block typos-config-builder publishes for
the pinned release, and that block says "`make spelling` runs the pinned
`typos-config-builder gate`" (`spelling-config-baseline/rule.yaml:110`). A
Netsuke repository can comply only by carrying an instruction that is false for
it. The proposal keys `agents_md_blocks` by release tag and orchestrator. Until
typos-config-builder publishes Netsuke text, PD-013 on a Netsuke repository is
`indeterminate` and names the missing text, rather than comparing with Make
guidance. The duplicate-guidance pattern (`spelling_config_baseline.rego:932`)
learns `netsuke build spelling`. Publishing the text is a change in
leynos/typos-config-builder, outside this repository.

QG-002 reads Makefiles as text for Whitaker install routes. The builder adds
files named `Netsukefile`, at any depth, to the same text surfaces
(`whitaker_provisioning_envelope.py:62-63`). The policy needs no change,
because it already reads text for install and download commands. Without this,
adopting Netsuke would open a provisioning route QG-002 never sees.

### 3.9 Envelopes, replay, canonical CI and documentation

**Envelopes.** Each Make-reading envelope gains an additive `build` object
carrying the resolution (`declared`, `detected`, `authoritative`, `reason`) and
the Netsuke fact block, plus an `applicability.root_netsukefile` flag. The
`makefile` field is unchanged, and `schema_version` stays 1. A policy that
receives an envelope without `build` treats the checkout as Make-authoritative,
so recorded evidence replays to its recorded verdict. This follows the
precedent of `cargo.surfaces`, which was added within schema 1 with a fallback
"so replaying recorded evidence cannot turn an audited Rust checkout into an
unmeasured clean result" (`rust_makefile_baseline.rego:34-36`; design document
Section 2.2.1).

Keeping version 1 follows the rule the design document states for this
envelope, not an exception to it. The design document's rule that a
`schema_version` is "Bumped when new fields are introduced so older CLIs can
refuse unsupported layouts" is stated for one artefact: the remote-state
persistence manifest, `persistence.yaml` (design document Section 2.8.1,
"Backend specification"). For the policy-input envelope, the design document
records the opposite decision: additive fields are carried "so the envelope
stays at `schema_version: 1`" (Section 2.2.1). The two consumers of an envelope
cannot be harmed by an additive field. The policy of the same package version
reads it. An older policy reads fixed paths and never sees `build`, so it
reports a Netsuke checkout as missing its Makefile, which is the current
fail-closed verdict, not a pass. A layout change that removed or retyped an
existing field would still require a bump.

**Package identifiers.** The package identifiers stay, because the Parabellum
ledger records them (`docs/parabellum/ledger.jsonl`). Each affected package
takes a minor version bump. Whether `rust-makefile-baseline` takes a display
name that no longer says Makefile is left to Section 10.

**Canonical CI.** The reusable workflow gains a `build-orchestrator` input
defaulting to `make`, so every current caller is unaffected. Under `netsuke` it
installs Netsuke as a digest-verified standalone binary rather than from
source, installs Ninja 1.10 or newer (required for serial dependency lists),
replaces `mbake validate Makefile` with `netsuke help targets`, which loads and
validates the manifest's structure without running a recipe, and runs
`netsuke build <target>` for each canonical step.

**Documentation.** The design document's catalogue rows for FP-003, QG-001 and
PD-002 to PD-008, the users' guide's FP-003 description, the four package
READMEs, and the `.concordat` schema table (adding `build.orchestrator`) are
updated in the step that changes each rule's behaviour, as the users' guide and
design-record rules in `AGENTS.md` require. Each step in Section 5 lists its
own documentation, and only PD-013's text waits on step 5's external dependency.

## 4. Hypotheses and the evidence that decides them

The proposal rests on eight assumptions. Each is stated as a hypothesis with
the observation that would refute it and the decision the result changes.
Verdicts use `untested`, `falsified`, `not-falsified` and `inconclusive`, kept
distinct from the rules' `compliant`, `noncompliant` and `indeterminate`.

### 4.1 The hypotheses

#### Table 5: Hypotheses

| ID  | Hypothesis                                                                                                                                                        | Refuted by                                                                                                                                                                                                     | Decision it informs                                           | Status                                        |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- | --------------------------------------------- |
| H1  | Table 1 is the complete set of Make assumptions: only those packages change verdict when a Makefile is removed                                                    | Any other package's verdict on catnap #90 differing between its base (Makefile) and its head (Netsukefile)                                                                                                     | Scope of steps 1 to 3                                         | `not-falsified` by reading; `untested` by run |
| H2  | Today the four packages report catnap #90's head as Section 2.2 predicts, with QG-001 and PD-002 to PD-004 silent                                                 | A run whose finding set differs from the prediction                                                                                                                                                            | Whether the vacuity hazard is real or already guarded         | `untested`                                    |
| H3  | A strict YAML reader that leaves Jinja unrendered recovers the entry graph Netsuke builds                                                                         | Any entry, edge, default or conditional flag that differs from `netsuke help targets --json` on catnap #90 and on the tested examples in Netsuke's guide; any document Netsuke refuses that the reader accepts | Reader in concordat, or an upstream fact export (Section 11)  | `untested`                                    |
| H4  | Splitting each policy into a Make adapter and shared checks changes no Make verdict                                                                               | Any changed verdict on the existing fixture suites or on a replay of the ledger's recorded envelopes                                                                                                           | Whether the adapter design is safe to ship                    | `untested`                                    |
| H5  | Two orchestrations of the same recipes earn the same findings: catnap's base Makefile and head Netsukefile yield one finding set, modulo paths, lines and wording | A verdict that differs for a reason Section 3 does not document as a semantic difference                                                                                                                       | Whether the Netsuke adapter is as strict as the Make one (I2) | `untested`                                    |
| H6  | Every construct that defeats a Make proof has a Netsuke counterpart that defeats the Netsuke proof                                                                | A mutation in Section 7 that leaves a Netsuke must-raise fixture passing                                                                                                                                       | Whether I2 holds                                              | `untested`                                    |
| H7  | The Netsuke invocation grammar can be recognized with the precision of the Make one                                                                               | A fixture in Table 8 misclassified in either direction                                                                                                                                                         | Scope of BO-003 and the PD-006 and PD-010 changes             | `untested`                                    |
| H8  | Netsuke adoption in the estate extends beyond catnap                                                                                                              | An estate sweep finding catnap the only repository with, or proposing, a root `Netsukefile`                                                                                                                    | Whether steps 4 and 5 are worth their cost                    | `untested`                                    |

Two limits bound every result. catnap #90 is one adopter, so a pass on it shows
the design is sufficient for that manifest, not that it generalizes; the
fixture suites in Section 6 carry the generalization. Netsuke v0.1.0-beta4 is
an early-adopter release whose guide warns that "command names, flags,
diagnostic schemas, and some manifest details may change before 1.0", so the
reader models one `netsuke_version` and refuses the rest.

H5 has a sharp prediction worth stating before any run. catnap's base Makefile
and head Netsukefile both pin typos-config-builder at `v0.1.1`, below the
`v0.1.3` floor (`spelling-config-baseline` README, PD-007). H5 therefore
predicts the same PD-007 floor finding from both. A Netsuke adapter that passed
the head while the Make adapter reported the base would be more permissive on
an otherwise identical recipe, which is the I2 failure the hypothesis tests.

## 5. Delivery steps and decision gates

The steps are proposals for the parent to approve. Each delivers a usable slice
rather than a layer: the first makes one package judge a Make-free checkout end
to end, and later steps extend the same loop. Steps 4 and 5 are conditional on
the evidence named in their gates.

### 5.1 Step 1: judge catnap's Rust gates without a Makefile

- **Outcome.** `rust-makefile-baseline` and the new
  `build-orchestration-baseline` resolve the orchestrator, read Netsuke facts,
  and judge FP-003 and QG-001 on a Netsuke checkout.
- **Question.** Can a hermetic reader and an adapter prove the lint gate in a
  Netsukefile at Make's strictness (H3, H4, H6)?
- **In scope.** Resolution and BO-001 (Section 3.2); the Netsuke reader
  (Section 3.3); the adapter split of `rust-makefile-baseline` only; the
  Netsuke adapter rules for QG-001 (Section 3.5); the additive envelope field
  (Section 3.9); the Netsuke clause of QG-002 (Section 3.8); and the
  documentation for each of these: the design document's catalogue rows for
  FP-003, QG-001, QG-002 and BO-001, the `.concordat` schema table's
  `build.orchestrator` field, the users' guide's FP-003 and QG-001 description,
  and the `rust-makefile-baseline` and `whitaker-provisioning` READMEs.
- **Excluded.** The Markdown and spelling packages, CI recognizers, BO-002,
  BO-003, PD-013, `script:` recipes, Windows proofs, and `${VAR:-default}` gate
  forms.
- **Prerequisites.** None beyond the current packages; `netsuke` v0.1.0-beta4
  is needed to generate fixtures, never at audit time.
- **Evidence.** Gate G0, taken before any change, then gates G1 to G3.
- **Acceptance.** G1 to G3 pass.
- **Falsification.** G1 finds a mismatch the reader cannot fix without
  rendering Jinja, or G2 finds a changed Make verdict that no correct refactor
  avoids.
- **Decision enabled.** Whether to extend the adapter to the Markdown and
  spelling packages unchanged.

### 5.2 Step 2: extend to the Markdown and spelling recipe checks

- **Outcome.** PD-002 to PD-004, PD-007 and PD-008 judge Netsuke recipes
  through the same adapter; FP-003 in `markdown-formatting-baseline` follows.
- **Question.** Does the step 1 adapter carry over without new grammar (H5)?
- **In scope.** The adapter split of the two packages, and their Netsuke
  adapters, reusing step 1's reader; the catalogue rows for PD-002 to PD-004,
  PD-007 and PD-008, the users' guide's Markdown and spelling sections, and
  both packages' READMEs.
- **Excluded.** PD-006, PD-010 and PD-013, which are workflow and guidance
  checks and belong to steps 3 and 5.
- **Prerequisites.** Step 1.
- **Evidence.** Gate G4.
- **Falsification.** A PD check that needs Netsuke-specific grammar the step 1
  adapter cannot express, which would argue for a different adapter boundary.
- **Decision enabled.** Whether catnap #90 can be declared satisfiable
  without a Makefile for the shipped packages, modulo PD-013.

### 5.3 Step 3: recognize Netsuke in CI and in the canonical workflow

- **Outcome.** The shared recognizer, the PD-006 and PD-010 changes, BO-003,
  and the canonical workflow's `build-orchestrator` input.
- **Question.** Can CI invocation be read with Make's precision (H7), and can
  I1 be enforced without requiring CI to run anything new?
- **In scope.** Section 3.6 and the canonical-workflow paragraph of
  Section 3.9, with the catalogue rows for PD-006, PD-010 and BO-003, the
  affected README sections, and the users' guide's description of the canonical
  workflow's `build-orchestrator` input.
- **Excluded.** BO-002's shim conformance, which BO-003 consults but does not
  define; until step 4 lands, a Make invocation in a Netsuke repository is a
  BO-003 finding whatever the Makefile holds.
- **Prerequisites.** Step 1 for resolution.
- **Evidence.** Gate G5.
- **Decision enabled.** Whether a shim is needed at all, informed by how many
  repositories BO-003 reports.

### 5.4 Step 4: admit a conforming shim

- **Outcome.** BO-002 (Section 3.7) and its use in BO-003, with BO-002's
  catalogue row and the users' guide's migration notes on shims.
- **Question.** Does any repository want a transitional shim?
- **Prerequisites.** Steps 1 and 3, and gate G6.
- **Recommendation if not warranted.** Defer. catnap #90 rejects a shim, and
  BO-003 already keeps a stray Makefile from carrying CI gates.

### 5.5 Step 5: orchestrator-neutral spelling guidance and documentation

- **Outcome.** PD-013 keyed by orchestrator, the Netsuke duplicate-guidance
  pattern, and PD-013's own catalogue row and README text. Every other
  documentation change ships with the step that changes the rule it describes,
  so no shipped behaviour waits on this externally blocked step.
- **Prerequisites.** Step 2, and typos-config-builder publishing a Netsuke
  block, which is outside concordat. Until then PD-013 stays `indeterminate`
  for Netsuke repositories.

### 5.6 Deferred

- The planned "Makefile parse" checks (QG-004, PY-001, PY-002, PY-004,
  PY-010, RT-001, RT-003, RT-005 and RT-011) should be written against the
  adapter from inception, so that each ships orchestrator-neutral. This RFC
  records that obligation and does not schedule those checks.
- Proofs for `script:` recipes, the Windows PowerShell route, and Jinja
  control flow in recipes.
- The OpenTofu `tf-plan` targets that `infrastructure.opentofu` enables
  (design document Section 2.2, Table 2).

### 5.7 Decision gates

Thresholds are proposals until the parent approves them, and are fixed before
any run.

#### Table 6: Decision gates

| Gate | Evidence                                                                                                                                              | Proceed when                                                                                                                                                     | Revise when                                                                                                                              | Stop when                                                                                       |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| G0   | Run the four packages on catnap #90's base and head with the current code                                                                             | The findings match H2's prediction                                                                                                                               | A package is already silent on the head where H2 predicts a finding: record it, then proceed                                             | Never; G0 is a baseline, and it is taken before any change                                      |
| G1   | Reader output against `netsuke help targets --json` on catnap #90's head and on each tested example in Netsuke's guide                                | Zero differences in entries, edges, defaults and conditional flags; every document Netsuke refuses is refused                                                    | One bounded revision of the reader closes every difference                                                                               | A difference needs Jinja rendering to close; then take the upstream export (Section 11)         |
| G2   | Existing `rust-makefile-baseline` fixtures under `conftest verify`, and a replay of the ledger's recorded envelopes for that package                  | Zero changed verdicts                                                                                                                                            | —                                                                                                                                        | Any changed verdict that a correct refactor cannot remove                                       |
| G3   | `rust-makefile-baseline` and `build-orchestration-baseline` on catnap #90's head and base, plus the step's mutations of the head's `rust-lint` action | The head yields no FP-003 or BO-001 finding, its QG-001 verdict is compliant and equals the base's, and every Section 7 mutation that names QG-001 discriminates | QG-001 is `indeterminate` on the head for a reader limitation, or the base is unexpectedly noncompliant: fix or record once, then re-run | QG-001 is `indeterminate` on the head for a documented Netsuke semantic: escalate to the parent |
| G4   | The Markdown and spelling packages on catnap #90's base and head                                                                                      | Identical finding sets modulo paths, lines and wording, including the shared PD-007 floor finding                                                                | A difference traced to a reader or adapter defect                                                                                        | A difference that is a genuine semantic gap the adapter cannot close                            |
| G5   | Table 8's fixtures, and the canonical workflow exercised with `build-orchestrator: netsuke` on catnap #90                                             | Every pair discriminates; the canonical workflow passes on catnap                                                                                                | A misclassified spelling of the invocation grammar                                                                                       | —                                                                                               |
| G6   | A sweep for estate repositories with a root `Netsukefile`, or with one proposed, that also keep or want a Makefile                                    | At least one repository requests a shim                                                                                                                          | —                                                                                                                                        | None request one: defer step 4                                                                  |

The G3 stop clause matters most. If QG-001 cannot be proven on catnap's head
because of a Netsuke semantic, such as the shared shell context, the
proposition "a Netsuke repository can satisfy concordat without a Makefile" is
only partly supported. The decision to accept that, to change the adopter's
manifest shape, or to extend the grammar belongs to the parent, not to the
implementer.

## 6. Fixtures

Each pair differs in exactly the fact its rule claims to decide.

### 6.1 Resolution fixtures

#### Table 7: BO-001 and BO-002 fixtures

| Must raise                                                                                                                                 | Must not raise                                                                                   | Difference under test                                                       |
| ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| `both-undeclared`: a root `Makefile` and `Netsukefile`, no declaration                                                                     | `both-declared-netsuke`: the same files with `build.orchestrator: netsuke` and a conforming shim | Whether the authority is declared                                           |
| `declared-netsuke-missing`: `build.orchestrator: netsuke` and no `Netsukefile`                                                             | `declared-netsuke-present`: the same declaration with the file                                   | Presence of the declared manifest                                           |
| `netsuke-config-redirects`: a project `.netsuke.toml` selecting another manifest path                                                      | `netsuke-config-budgets-only`: a `.netsuke.toml` that sets only resource budgets                 | Whether the configuration can move the audited manifest                     |
| `shim-carries-gate`: under Netsuke authority, a Makefile `lint` recipe that runs `$(WHITAKER)`                                             | `shim-delegates`: the same target whose sole recipe is `netsuke build lint`                      | Whether the Makefile carries a gate of its own                              |
| `declared-netsuke-config-redirects`: `build.orchestrator: netsuke`, a root `Netsukefile`, and a `.netsuke.toml` selecting another manifest | `declared-netsuke-present`                                                                       | A redirect leaves no authority even when Netsuke is declared                |
| `shim-variable-executable`: a Makefile `lint` recipe `$(NETSUKE) build lint` with `NETSUKE := netsuke`                                     | `shim-delegates`                                                                                 | Only the literal executable is a shim, because a variable can be overridden |
| `shim-renames`: a Makefile `lint` recipe running `netsuke build rust-lint`                                                                 | `shim-delegates`: as above                                                                       | Same-name delegation, not delegation to any entry                           |
| —                                                                                                                                          | `make-only`: a Make repository with no `Netsukefile` and no declaration                          | Resolution adds no finding to the current estate (I4)                       |

### 6.2 Netsuke adapter fixtures

Each must-raise fixture is a one-entry mutation of a manifest modelled on
catnap #90's head, and each must-not-raise fixture is that manifest unchanged
or minimally changed.

#### Table 8: Netsuke adapter and recognizer fixtures

| Must raise                                                                                                                | Must not raise                                                                                     | Difference under test                                                          |
| ------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `gate-or-true`: the `whitaker` entry ending `\|\| true`                                                                   | `catnap-head`: the entry as catnap has it                                                          | Binding tail within an entry                                                   |
| `gate-after-semicolon-masked`: `whitaker --all; true` in one entry                                                        | `gate-own-entry`: `whitaker --all` and `true` as two list entries                                  | Entry boundaries bind; a `;` inside an entry masks                             |
| `gate-pipeline-same-entry`: the entry `whitaker --all \| tee lint.log`                                                    | `gate-before-pipeline-entry`: the gate entry followed by a separate entry `cargo metadata \| head` | A pipe masks only within its own entry                                         |
| `lint-one-hop-only`: `lint` depends on `checks`, and `checks` on an action that never runs the gate                       | `lint-two-hops`: the same chain where the second hop runs the gate                                 | The closure is transitive, not one hop                                         |
| `gate-at-prefixed`: the entry `@whitaker --all`                                                                           | `catnap-head`                                                                                      | Make prefixes are not read in Netsuke                                          |
| `gate-under-when`: `rust-lint` carrying a `when` expression                                                               | `catnap-head`                                                                                      | Conditional ancestry, `indeterminate`                                          |
| `lint-defined-twice`: two `lint` actions under complementary `when` expressions                                           | `catnap-head`                                                                                      | Ambiguous entry, `indeterminate`                                               |
| `gate-after-exec`: an entry `exec true` before the `whitaker` entry                                                       | `gate-after-echo`: an entry `echo starting` before it                                              | `exec` ends the chain                                                          |
| `gate-after-exit`: an entry `exit 0` before the `whitaker` entry                                                          | `gate-after-echo-exit`: an entry `echo "exit 0"` before it                                         | `exit` ends the shared shell only in command position                          |
| `gate-after-conditional-exit`: an entry `test -f skip-lint \|\| exit 0` before the gate                                   | `gate-after-echo`                                                                                  | A conditional exit makes the gate `indeterminate`                              |
| `gate-after-exit-same-entry`: the entry `exit 0; whitaker --all`                                                          | `catnap-head`                                                                                      | Termination before the gate within one entry                                   |
| `gate-after-cd-elsewhere`: an entry `cd docs` before the gate, with a root surface only                                   | `gate-after-cd-surface`: an entry `cd rust` before the gate, with `rust/Cargo.toml` declared       | Shared shell context qualifies a surface and disqualifies the root             |
| `gate-in-script`: the gate inside a `script:` recipe                                                                      | `catnap-head`                                                                                      | Scripts are `indeterminate` in the first slice                                 |
| `lint-dep-templated`: `deps: ["{{ lint_parts }}"]`                                                                        | `catnap-head`                                                                                      | Dynamic edge, `indeterminate`                                                  |
| `gate-var-unresolved`: `{{ tool }}` where `vars.tool` itself contains Jinja                                               | `gate-var-literal`: `{{ tool }}` where `vars.tool` is `whitaker`                                   | Single-literal substitution only                                               |
| `duplicate-key`: a manifest repeating `actions:`                                                                          | `catnap-head`                                                                                      | The reader refuses what Netsuke refuses                                        |
| `ci-make-under-netsuke`: a workflow running `make lint` with Netsuke authoritative and no conforming shim                 | `ci-netsuke-build`: the same step as `netsuke build lint`                                          | BO-003: CI runs the audited graph                                              |
| `ci-make-unmatched-target`: Netsuke authoritative, a Makefile `ci` target running the gates, and `make ci` in a workflow  | `make-unmatched-target-local`: the same Makefile target, which no workflow invokes                 | BO-003 covers every target of the second orchestrator, not only governed names |
| `ci-netsuke-other-file`: `netsuke -f ci.yml build lint`                                                                   | `ci-netsuke-build`                                                                                 | A selected manifest is not the audited one, `indeterminate`                    |
| `ci-netsuke-inline-file`: `NETSUKE_FILE=ci/Netsukefile netsuke build lint`                                                | `ci-netsuke-inline-unrelated`: catnap's `ACTIONLINT="…" netsuke build lint`                        | Which variable the assignment prefix sets                                      |
| `ci-netsuke-exported-file`: `export NETSUKE_CONFIG=ci.toml` on an earlier line of the same `run:` body                    | `ci-netsuke-build`                                                                                 | Shell-level environment, not only the workflow's `env:`                        |
| `ci-netsuke-github-env-file`: an earlier step in the job appends `NETSUKE_FILE=…` to `$GITHUB_ENV`                        | `ci-netsuke-build`                                                                                 | Environment written by an earlier step                                         |
| `ci-netsuke-defaults-run-dir`: a job with `defaults.run.working-directory: tools` and a step `netsuke build lint`         | `ci-netsuke-build`                                                                                 | The inherited working directory, not only the step's own key                   |
| `ci-reusable-input-mismatch`: a Make-authoritative caller passing `build-orchestrator: netsuke` to the canonical workflow | `ci-reusable-input-matches`: the same caller passing `make`, or omitting the input                 | BO-003 reads the reusable workflow's input against the resolved authority      |
| `ci-make-shim-override`: `make SHELL=true lint` against a conforming shim under Netsuke authority                         | `ci-make-shim`: `make lint` against the same shim                                                  | Invocation-time overrides void the shim exemption, `indeterminate`             |
| `ci-netsuke-cd`: `cd tools && netsuke build lint`                                                                         | `ci-netsuke-build`                                                                                 | A moved working directory loads another `Netsukefile`                          |
| `ci-netsuke-windows-powershell`: `netsuke build lint` on `windows-latest` without `NETSUKE_WINDOWS_SHELL`                 | `ci-netsuke-windows-bash`: the same step with `NETSUKE_WINDOWS_SHELL: bash`                        | POSIX proofs do not cover PowerShell, `indeterminate`                          |
| `ci-netsuke-markdownlint`: a `run:` step `netsuke build markdownlint` beside no action step                               | `ci-netsuke-echo`: a step that echoes `netsuke build markdownlint`                                 | PD-006 reads the command word, not the text                                    |
| —                                                                                                                         | `ci-netsuke-bare-target`: `netsuke lint`                                                           | Not an invocation; Netsuke rejects it, so nothing is bypassed                  |

## 7. Contract mutations

Every mutation is applied in both directions: a weakening must stop a
must-raise fixture raising, and a widening must make a must-not-raise fixture
raise.

- **Vacuity mutation.** Accept a `Netsukefile` for FP-003 and remove QG-001's
  Netsuke clauses. `gate-or-true` must stop raising, which proves that those
  clauses, not FP-003, carry the gate proof.
- **Prefix mutation.** Share Make's `[-@+]*` prefix grammar with the Netsuke
  adapter. The rule must stop raising on `gate-at-prefixed`.
- **Context mutation.** Treat each list entry as its own shell, as Make treats
  each line. The rule must stop raising on `gate-after-cd-elsewhere` and on
  `gate-after-exec`. This is the mutation a `makeutil`-shaped export would
  apply silently, and Section 11 rejects that export on its evidence.
- **Join mutation.** Join list entries into one string with `&&` and read it
  as one recipe line. The rule must now raise on `gate-before-pipeline-entry`,
  whose pipe sits in a later entry and cannot mask the gate, proving that
  entries are kept as separate records. Netsuke's own wrapper exists for the
  same reason: each entry is evaluated inside its own brace group so that its
  text cannot change the chain's structure.
- **Termination mutation.** Recognize only `exec` as ending the chain. The
  rule must stop raising on `gate-after-exit`, `gate-after-conditional-exit` and
  `gate-after-exit-same-entry`. Widening it to match `exit` anywhere in the
  text must make it raise on `gate-after-echo-exit`, so the command-position
  reading is load-bearing in both directions.
- **Environment-scope mutation.** Read only the workflow's merged `env:`. The
  recognizer must stop reporting `ci-netsuke-inline-file`,
  `ci-netsuke-exported-file` and `ci-netsuke-github-env-file`. Widening it to
  any assignment prefix must make it report `ci-netsuke-inline-unrelated`,
  which is the step catnap #90 actually runs.
- **Governed-name mutation.** Restrict BO-003 to the governed entry-point
  names. The rule must stop raising on `ci-make-unmatched-target`, which is the
  route a repository would use to run unaudited gates under a neutral name.
- **Condition mutation.** Ignore `when` and `foreach`. The rule must stop
  raising on `gate-under-when` and `lint-defined-twice`.
- **Hop mutation.** Follow direct `deps` only, one hop from the root. The rule
  must now raise on `lint-two-hops`, which is the shape the design document
  records for wildside's two-hop `$(MAKE)` chain on the Make side (Section
  2.2.1).
- **Reader-strictness mutation.** Accept duplicate keys with last-wins
  semantics. The reader must stop refusing `duplicate-key`.
- **Resolution mutation.** Let a present `Netsukefile` win over a present
  `Makefile` when nothing is declared. `make-only` is unchanged, but a Make
  repository that gains a `Netsukefile` changes authority silently, so
  `both-undeclared` must stop raising BO-001.
- **Invocation mutation.** Accept `netsuke <name>` as an invocation. The
  recognizer must now credit `ci-netsuke-bare-target`.
- **Shim mutation.** Accept any `netsuke build` recipe as a shim. BO-002 must
  stop raising on `shim-renames`.
- **Shim-executable mutation.** Accept a Make variable whose single value is
  `netsuke` as the shim's command word. BO-002 must stop raising on
  `shim-variable-executable`, the shape `make -e` can turn into
  `true build lint`.
- **Override mutation.** Exempt every Make invocation of conforming shims,
  whatever it passes. BO-003 must stop reporting `ci-make-shim-override`.
- **Inherited-directory mutation.** Read only a step's own
  `working-directory:`. The recognizer must stop reporting
  `ci-netsuke-defaults-run-dir`.
- **Reusable-input mutation.** Read only `run:` bodies. BO-003 must stop
  reporting `ci-reusable-input-mismatch`, which no `run:` step reveals.
- **Redirect mutation.** Resolve a declared Netsuke checkout to Netsuke
  whatever its `.netsuke.toml` holds. BO-001 must stop raising on
  `declared-netsuke-config-redirects`.

## 8. Properties

Four predicates range over inputs no fixture table can cover, so each carries a
Hypothesis property test written from this document, in the developers' guide's
discipline for `tests/unit/test_properties.py`.

- **Closure equivalence across orchestrators (H5).** Over generated acyclic
  entry graphs whose every edge carries a kind drawn from `deps`, `sources` and
  `order_only_deps`, the same graph expressed as a `makeutil`-shaped report,
  with every edge as a prerequisite, and as Netsuke reader facts, with each
  edge in the field its kind names, yields the same reachable set from every
  root. Each kind is placed only on entries whose schema admits it, and the
  generator is required to produce, for each kind, graphs where some entry is
  reachable through that kind alone. An adapter that omits any one traversal
  therefore fails the property. A `makeutil` side constructed directly as a
  report means the property needs no `makeutil` binary.
- **Recipe references.** Over the same graphs, with recipes attached to
  generated reusable rules and entries selecting them through a literal `rule`,
  the recipe set reachable from every root equals the set obtained by inlining
  each rule's recipe into the entries that name it. An adapter that treated
  `rule` as an edge to another entry, or ignored it, fails this.
- **Command-list binding.** Over generated lists of context-neutral entries
  in which exactly one entry invokes the gate with a binding tail, the verdict
  is compliant and invariant under permuting the other entries; appending
  `|| true` to the gate entry always yields noncompliant; and inserting a
  context-changing or terminating entry, `exit` among them, anywhere before the
  gate never yields compliant.
- **Resolution totality.** Over every combination of declaration (absent,
  `make`, `netsuke`, invalid), root `Makefile` (present, absent), root
  `Netsukefile` (present, absent) and project `.netsuke.toml` (absent, budgets
  only, redirecting, unreadable), resolution yields exactly one row of Table 3.
  This is the combinatorial suite step 1 owns, and it is small enough to
  enumerate rather than sample.

## 9. Migration and compatibility

**The Make estate.** Nothing changes for a repository with only a Makefile.
Resolution yields Make, the Make adapter is the existing code, the envelope
keeps its `makefile` field, and BO-001 to BO-003 raise nothing. G2 makes that
claim falsifiable before step 1 ships, and the replay rule in Section 3.9 keeps
the ledger's recorded verdicts reproducible. The one behavioural change a Make
repository can see is BO-001 `indeterminate` on adding a root `Netsukefile`
without a declaration, which is the intended signal.

**Moving to Netsuke.** A repository migrating from Make to Netsuke:

1. adds the `Netsukefile`, keeping the Makefile, with
   `build.orchestrator: make` until the manifest is complete;
2. switches the declaration to `netsuke` and either deletes the Makefile or
   reduces each shared target to a `netsuke build` shim (BO-002, once step 4
   ships);
3. switches CI steps to `netsuke build <target>` (BO-003), on Windows with
   `NETSUKE_WINDOWS_SHELL: bash`;
4. installs Netsuke as a verified binary rather than from source (RFC 0001
   TA-001 and TA-002), and Ninja 1.10 or newer; and
5. carries typos-config-builder's Netsuke guidance block once it exists
   (PD-013).

catnap #90 already satisfies steps 2 and 3 without a shim and would need step
4's install change and, independently of Netsuke, its builder pin moved to the
floor.

**Reverting.** Declaring `make` restores the Make audit at once, because the
Make path is untouched.

## 10. Open questions

1. **The `${WHITAKER:-whitaker}` form.** Make's `WHITAKER ?= whitaker` is a
   sanctioned estate pattern because a local override is permitted and CI
   installs the real binary (`rust_makefile_baseline.rego:567-570`, doctrine
   decision of 2026-07-19). The shell default is its closest Netsuke
   counterpart but is resolved at run time rather than make time. Whether the
   doctrine extends to it is a decision for the parent, and until it is taken
   the form is `indeterminate`.
2. **The test-runner fallback.** QG-004 and RT-011 will need a readable
   Netsuke form of the nextest fallback. Both available forms are Jinja that a
   hermetic reader cannot evaluate. Recognizing one fixed `command_available`
   pattern as the canonical idiom, or asking Netsuke for a declarative form,
   are the two candidates.
3. **The `.netsuke.toml` keys.** Which configuration keys select a manifest
   and default targets, and how `extends` composes them, must be read from
   Netsuke's sample configuration in step 1.
4. **Issue #153 identifiers.** The proposed `BO` family must be checked
   against issue #153's full identifier list before the catalogue changes.
5. **The `rust-makefile-baseline` name.** Its identifier stays for the
   ledger's sake. Whether its display name and README should stop saying
   Makefile is a presentation decision.
6. **An upstream fact export.** If G1 fails, or once Netsuke reaches 1.0,
   a `netsuke` query that emits entries, edges and unrendered recipe text as
   versioned JSON, run hermetically, would replace the concordat-side reader as
   `makeutil` replaces a Make parser. Netsuke is an estate project, so the
   request can be made, but this RFC does not depend on it.
7. **The G1 oracle's coverage.** Netsuke's guide documents the `help targets`
   JSON envelope but not whether its catalogue carries each entry's
   dependencies. If it does not, G1 compares entries, defaults and conditional
   flags through it, and compares edges against `netsuke graph` output produced
   in a fixture-generation sandbox, never at audit time.
8. **Orchestrators invoked from scripts.** BO-003 reads `run:` bodies. A
   script under `scripts/` that CI runs and that invokes `make` or `netsuke` is
   outside its reach, as a script-driven Make invocation is outside every
   current rule's. QG-002 already reads such scripts as text for install
   routes, so the same surfaces could carry an orchestrator recognizer, at the
   cost of deciding which scripts CI actually runs.

## 11. Alternatives rejected

**Require a shim Makefile.** This needs no concordat change at all. Rejected
because a delegating shim fails QG-001 and a shim that passes it copies the
recipes into a file CI does not run (Section 2.3). The requirement would force
every adopter to choose between failing and misleading, and catnap's own
decision record rejects it.

**Accept a `Netsukefile` for FP-003 and leave the rest.** The smallest change
that turns catnap #90 compliant. Rejected because every QG-001 and PD-002 to
PD-004 clause requires Make facts, so the gate proofs would fall silent and the
repository would pass with nothing proven (Section 2.2). This is the defect
invariant I2 and the vacuity mutation exist to prevent.

**Generate a Makefile from the Netsukefile and audit that.** This reuses
`makeutil` and the policies unchanged. Rejected because the generated file is
an artefact nobody runs, so I1 fails by construction, and because the
translation cannot be faithful: Make runs each recipe line in its own shell,
while Netsuke runs a command list in one shell with state carried across
entries, so a `cd` in one entry changes where the next runs. A generated
Makefile would credit the root surface for a gate that runs in a subdirectory.

**Ask Netsuke to emit `makeutil`-shaped facts.** The same unchanged policies,
with Netsuke owning the translation. Rejected for the same semantic reason: the
`makeutil` schema has no shared shell context, no serial dependency order, and
an ignore-errors flag Netsuke lacks. The context mutation in Section 7 is
exactly what this export would apply without anyone choosing it. A
Netsuke-native export is different and remains open (Section 10).

**Parse the Ninja that `netsuke generate` writes.** Ninja is Netsuke's real
output, and reading it would avoid modelling the manifest schema. Rejected
because generation evaluates the whole template library, including `shell`,
`fetch`, `which` and `env`, so the audit would run manifest-controlled code on
the auditing host and its answer would depend on that host's `PATH`, as catnap's
`test` action shows. It would also require Netsuke, a pinned nightly or
binary, and Ninja on every auditor, and the recipe text arrives wrapped in
`eval` payloads with doubled dollars that the predicates would have to unwrap.
This breaks I3.

**Use `netsuke help targets --json` as the only fact source.** Query mode is
hermetic by design, and it is the fidelity oracle G1 uses. Rejected as the sole
source because it skips recipe bodies, and every gate check is a check of
recipe text.

**Audit both manifests whenever both exist.** This needs no resolution rule.
Rejected because the manifest CI does not run produces findings nobody can act
on, and passes nobody should trust; BO-001 and BO-003 make the authority
explicit instead.

**Issue Netsuke twins of each identifier.** Separate `QG-001N`-style rules
would leave the Make rules untouched. Rejected for the reasons in Section 1.1:
one obligation would gain two identifiers, a migration would change a
repository's identifiers for no change in what it must do, and the two rows
could drift.
