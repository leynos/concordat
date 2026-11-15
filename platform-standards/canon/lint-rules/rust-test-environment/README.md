# rust-test-environment

Audits that a Rust repository's tests cannot change the process-wide
environment. The audit is read-only. It evaluates a
`policy-input/rust-test-environment` envelope built by
`concordat artefact rule run`.

## Why

A test that calls `std::env::set_var`, `std::env::remove_var` or
`std::env::set_current_dir` changes state that every thread in the process
shares. Rust tests in one binary run on parallel threads, so such a test needs
a lock or a `#[serial]` marker, and the runner then leaves cores idle while it
waits. Since Rust 2024 the calls are `unsafe` as well, because another thread
may read the environment as it changes. The estate's rule is that the
environment is an injected object, or the test runs a subprocess
(`Command::env`, `Command::current_dir`). The rule is for Rust only; Python
tests do not have the problem.

Clippy can find every such call, after macro expansion, so the rule does not
look for calls. It keeps Clippy's verdict standing: the lint must be switched
on for every crate, it must cover the wrapper crates that call the same
functions, and the only way to suppress it must be one that reports itself when
the site is migrated away.

## What counts as audited

A repository is audited when its root has a `Cargo.toml`. The unit is a
workspace member: the root package if it has a `[package]`, each directory that
`workspace.members` selects (globs relative to the root, minus
`workspace.exclude`) and that holds a `Cargo.toml`, and, when the root has a
`[workspace]` table, each crate inside the root that a member depends on by
`path` (directly, or through a `workspace = true` entry), which Cargo enrols
automatically unless excluded. A nested crate that is none of these (a fixture
crate, an example workspace) is another package: Clippy does not lint it as
part of this one, and the audit does not read it. Directories named `target`,
`fixtures`, `fixture`, `vendor`, `node_modules` and `.git` are not entered, and
a symbolic link is never followed.

## How the facts are read

The facts come from parsers, never a text search.

- Manifests, `clippy.toml` and `.cargo/config.toml` are decoded with `tomllib`.
- The Clippy configuration for a member is the one Clippy loads: the nearest
  `.clippy.toml` or `clippy.toml`, looking in the member's directory and then
  each parent up to the checkout (`.clippy.toml` first within a directory). It
  is the first file found, not a merge, so a member's own file shadows the
  root's. `disallowed-methods` entries are read as strings or as tables with a
  `path`.
- Dependencies come from `dependencies`, `dev-dependencies`,
  `build-dependencies` and the same tables under `[target.*]`. A renamed
  dependency is recorded under its `package` name, and `workspace = true`
  merges the features of the workspace's entry. A member's default features are
  followed through its own `[features]` table, so `default = ["extras"]` with
  `extras = ["figment/test"]` (or `"figment?/test"`) enables `test` on figment.
  Names are compared with `-` and `_` alike. Features that another member
  enables are not followed, although Cargo unifies them: the audit reads each
  member's own manifest.
- Rust files are read with tree-sitter-rust, so a suppression in a comment, a
  string or a doc example is not one. Every `.rs` file of a member is read,
  tests, benches and build scripts included, because that is where the sites
  are. Attributes written inside a macro body or invocation (`#[allow(..)]` in a
  `macro_rules!` transcriber), which tree-sitter leaves as tokens and Clippy
  honours after expansion, are read too.

A file that cannot be read, a `.rs` file whose syntax error spans text naming
`disallowed_methods`, `clippy::style`, `clippy::all` or `warnings`, or a
`CLIPPY_CONF_DIR` under `[env]` in `.cargo/config.toml` (which makes the
configuration in force unknowable) is reported as indeterminate (EN-001). A
syntax error elsewhere in a file is ignored, because syntax the parser predates
cannot hide a suppression of this lint.

## Checks

- **TE-001** (error): every audited member's effective Clippy configuration
  lists each of `required_paths` in `disallowed-methods`: `std::env::set_var`,
  `std::env::remove_var` and `std::env::set_current_dir`. A member with no
  configuration at all is reported, as is one whose own file shadows a complete
  one above it with an incomplete one. A configuration that could not be
  decoded is EN-001, not TE-001.
- **TE-002** (error): for each wrapper crate a member depends on, the member's
  configuration also lists that crate's entry points. The default table covers
  `temp_env` (`with_var`, `with_var_unset`, `with_vars`, `with_vars_unset`, and
  `async_with_vars` when the `async_closure` feature is enabled), `env_lock`
  (`lock_env`, `lock_current_dir`) and figment's `Jail` (`expect_with` and
  `try_with`, required only when the `test` feature that provides `Jail` is
  enabled). Clippy warns when a configured path does not resolve, which is why
  a path is required only when the crate and feature that define it are
  present. A repository's own helper that wraps `std::env::set_var` needs no
  entry: the call inside it is already disallowed.
- **TE-003** (error): the lint is suppressed only by
  `#[expect(clippy::disallowed_methods, reason = "...")]` on a function, `let`,
  statement, `const`, `static` or `impl` block (the one named adapter behind an
  injection seam, such as a type that reads the process environment for the
  seam's trait). The audit reports, with the file and line:
  - an `allow` of the lint, of a group that contains it (`clippy::style`,
    `clippy::all`), or of `warnings`, at any level, also inside `cfg_attr` and
    inside a macro body;
  - an `expect` with no `reason`, or a blank one, or one that is not a string
    (ordinary, raw and multi-line string literals all count);
  - an `expect` that is a crate or module inner attribute, an outer attribute on
    a `mod`, `trait` or type, or an `expect` of a group, since one attribute
    there silences every site added later.

  The audit does not look for the calls themselves. A site that is later
  migrated leaves its `expect` unfulfilled, which rustc reports under
  `unfulfilled_lint_expectations`, so the backlog removes itself. A function
  that wraps the call has the `expect`, and its callers do not need one.
- **TE-004** (error): no configuration lowers the lint outside source. A
  `[lints.clippy]` or `[workspace.lints.clippy]` entry that sets
  `disallowed_methods`, `style` or `all` to `allow`, or a `[lints.rust]` or
  `[workspace.lints.rust]` entry that does so for `warnings` (string or table
  form), is reported, as is an `-A` or `--allow` flag for them in
  `build.rustflags`, `target.*.rustflags`, or `[env]` `RUSTFLAGS` or
  `CLIPPY_ARGS` of `.cargo/config.toml` (or the legacy `.cargo/config`). Flags
  in Make recipes and workflow steps are not read.
- **EN-001** (error, indeterminate): a file could not be read or decoded, a
  syntax error hides the lint, or `CLIPPY_CONF_DIR` is set. The rule reports
  what it cannot judge instead of guessing.

## Parameters

- `required_paths`: the paths TE-001 requires. Dropping
  `std::env::set_current_dir` is a change to this default only.
- `silencing_lints`: the lint, the groups that contain it, and `warnings`.
- `wrapper_paths`: the TE-002 table, keyed by crate name in underscore form,
  with `paths`, `feature_paths` (extra paths when the member enables a feature)
  and `required_feature` (the requirement applies only with that feature).

## Waivers

None. Sites that cannot be migrated yet (production start-up, a binary that
runs after dropping privilege) carry an `expect` with a reason; that is the
waiver, and it is visible at the site.

## Limits

The audit does not check that CI runs Clippy over test targets
(`--all-targets`); a gate that skips them would not see the calls. It does not
read `-A` flags in Make recipes. It does not model `lints.workspace = true`
inheritance: a lints table anywhere in the checkout is read as applying.

## Fixtures

`fixtures/generate.py` lays out each scenario as a synthetic checkout and
records the production envelope, so `fixtures/data.json` is what the command
would send to Conftest. Regenerate it after changing the builder:

```bash
uv run python \
    platform-standards/canon/lint-rules/rust-test-environment/fixtures/generate.py
```
