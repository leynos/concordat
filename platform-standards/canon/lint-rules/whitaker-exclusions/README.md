# whitaker-exclusions

Audits every Whitaker exclusion a repository keeps in its `dylint.toml` files:
`excluded_crates`, `excluded_paths` and any other `excluded_*` key. The audit
is read-only. It evaluates a `policy-input/whitaker-exclusions` envelope built
by `concordat artefact rule run`.

## Why

An exclusion switches a lint off for part of a repository, and nothing rechecks
it. femtologging excluded `femtologging_rs`, its whole library, from
`no_std_fs_operations` on the strength of a Whitaker bug that was fixed long
since, and two test crates beside it
([femtologging#421](https://github.com/leynos/femtologging/issues/421)). A
whole-crate exclusion hides every finding in the crate, including ones that
have nothing to do with the reason it was added. The estate's own
configurations (netsuke's, Whitaker's) already state their reasons in comments;
this rule makes that a standard and makes a whole-crate exclusion of a governed
crate a decision someone must name.

## What counts as an exclusion

A key named `excluded_*` in any table of a `dylint.toml` outside the fixture,
vendored and build-output trees. `excluded_crates` is a whole-crate exclusion;
`excluded_paths` excludes a module and its descendants; any other `excluded_*`
key needs a reason too.

A **reason** is a non-empty comment block directly above the key or, when there
is none, directly above the table header. A blank line ends a block, and a
block of bare `#` lines says nothing. The audit checks that a reason exists,
not that it is good: a reviewer judges that, as with `missing_docs`. The text
scan finds keys at the start of a line, and ignores lines inside multi-line
strings, so a string that quotes a key and a comment lends neither to a real
key. A dotted key, an inline table, or a table header that repeats (`[[rule]]`
twice) is reported as not located (EN-001) instead of read as having no reason.

A crate name is resolved as rustc spells it, so hyphens are underscores and a
test target is named after its file. A name is a crate the repository
**governs** when it is a package, a library, a binary, a test, bench or example
target (declared with `[[test]]` and the like, or found as `tests/*.rs`,
`tests/*/main.rs`, `benches/`, `examples/` and `src/bin/`), or an alias of a
path dependency (femtologging's `_femtologging_rs`). It is a **fixture** when
it lives under a fixture, vendored or build-output tree (`fixtures`, `vendor`,
`third_party`, `patches`, `templates`, `examples`, `target`). A registry
dependency is not a crate of the repository. A path dependency is local whether
it is declared in the package, under a `[target.*]` table, or inherited from
`[workspace.dependencies]`. A package's `autotests`, `autobenches`,
`autoexamples` and `autobins` switches are honoured: with `autotests = false`
only the declared `[[test]]` targets count, not the files in `tests/`.

The audit never follows a symbolic link. A `dylint.toml`, a manifest, a target
folder or a file in one that is a link is reported as unreadable (EN-001), not
read as absent, so a link cannot make an exclusion look stale or a repository
look clean, and a link out of the checkout cannot put host filenames into a
verdict.

## Findings

| Id     | Meaning                                                                                                            |
| ------ | ------------------------------------------------------------------------------------------------------------------ |
| WX-001 | An exclusion has no reason.                                                                                        |
| WX-002 | A whole-crate exclusion names a governed crate that the `crate_exclusions` parameter does not name.                |
| WX-003 | A whole-crate exclusion names nothing in the repository, so it does nothing and is stale.                          |
| EN-001 | A `dylint.toml` or manifest could not be read, an exclusion is not a list of strings, or a key could not be found. |

WX-002 and WX-003 give one finding per exclusion key and list every crate they
concern: thirty test crates under one key are one decision to review. WX-003 is
not reported while a manifest is unreadable, because the name might be there.

Fixture crates that break their own lints on purpose (Whitaker's `fail_*`
examples) need no entry: they are not governed. Prefer `excluded_paths` to a
whole-crate entry, because it exempts one module and its descendants.

## Parameters

- `severity` (`warning` by default): the severity of every WX finding and of the
  indeterminate findings. An envelope defect is always `error`.
- `crate_exclusions`: whole-crate exclusions the repository may keep, each with
  `repository` (`owner/name`), `crate` (the name as Whitaker spells it), a
  `reason` and a `review_trigger`. The policy ignores an entry with a blank
  reason or trigger, because an exclusion nobody must revisit is not a record.

```yaml
crate_exclusions:
  - repository: leynos/femtologging
    crate: file_handler_tests
    reason: >-
      Integration tests that stage tempdir fixtures need ambient std::fs access.
    review_trigger: review when the handler tests take a capability handle
```

## Severity

The rule ships at `warning` severity so the estate can be surveyed before it
blocks. The `severity` parameter raises the findings to `error` without a code
change.

## Fixtures

`fixtures/generate.py` lays out each scenario as a synthetic checkout and
records the production envelope, so the Rego suite runs on the facts the
command would send. The scenarios cover the three repository shapes, with clean
variants, each defect, named exemptions and unreadable inputs. Regenerate with
`uv run python platform-standards/canon/lint-rules/whitaker-exclusions/fixtures/generate.py`
and run the suite with:

```shell
conftest verify \
  --policy platform-standards/canon/lint-rules/whitaker-exclusions/policy \
  --data platform-standards/canon/lint-rules/whitaker-exclusions/fixtures/data.json
```
