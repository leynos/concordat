"""Read the nextest setup scripts that run before a repository's timed tests.

A nextest setup script (`[scripts.setup.<name>]`) selected by a
`[[profile.<p>.scripts]]` rule runs before the tests the rule's `filter` covers,
so a build it starts is paid for outside any test's deadline. Whether a script
runs is a property of the rule, not of the script: the command lives in the
`[scripts.setup.<name>]` table and the `filter` in the profile rule that names
it. A rule naming a script nothing defines is a configuration nextest itself
refuses, so it prepares nothing.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import re
import tomllib
import typing as typ

from .dependency_family_coverage import mapping, sequence
from .fs_probe import probe_file
from .nested_cargo_gates import configured_strings

if typ.TYPE_CHECKING:
    import pathlib

NEXTEST_CONFIG: typ.Final = ".config/nextest.toml"
ALL_FILTER: typ.Final = "all()"
_MIN_REGEX: typ.Final = 2
# The selectors the reader evaluates, and the glue of a union between them
# (`|`, `+`, `or`, parentheses). Anything else in a filterset narrows or removes
# what a selector picks (`&`, `and`, `not`, `!`, `-`, `test(..)`, `kind(..)`).
_ATOM: typ.Final = re.compile(r"(?:binary|binary_id|package)\([^()]*\)|all\(\)")
_UNION_GLUE: typ.Final = re.compile(r"(?:[\s|+()]|(?<![\w-])or(?![\w-]))*")
# Nextest reads globs with `globset`, which also takes `{a,b}` alternation and
# backslash escapes; `fnmatch` does not, so a matcher using either is not
# evaluated rather than evaluated wrongly.
_UNSUPPORTED_GLOB: typ.Final = re.compile(r"[{}\\]")
DEFAULT_PROFILE: typ.Final = "default"


@dataclasses.dataclass(frozen=True, slots=True)
class SetupRule:
    """Store one nextest rule that runs a defined setup script.

    Attributes
    ----------
    filter:
        The rule's filterset expression; a missing one selects every test.
    """

    filter: str


def _defined_scripts(document: dict[str, object]) -> set[str]:
    """Return the names of the setup scripts a nextest configuration defines."""
    scripts = mapping(document.get("scripts")) or {}
    return set(mapping(scripts.get("setup")) or {})


def _profile_rules(document: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """Return every `[[profile.<name>.scripts]]` rule, with its profile's name."""
    rules: list[tuple[str, dict[str, object]]] = []
    for name, profile in (mapping(document.get("profile")) or {}).items():
        found = sequence((mapping(profile) or {}).get("scripts")) or []
        rules.extend(
            (name, rule) for item in found if (rule := mapping(item)) is not None
        )
    return rules


def _setup_names(rule: dict[str, object]) -> list[str]:
    """Return the script names a rule's `setup` key holds, one or a list."""
    names = rule.get("setup")
    return [names] if isinstance(names, str) else list(configured_strings(names))


def read_setup_rules(checkout: pathlib.Path, problems: list[str]) -> list[SetupRule]:
    """Return the nextest rules that run a setup script the file defines.

    Returns
    -------
    list[SetupRule]
        One entry per rule, empty when there is no readable configuration.
    """
    path = checkout / NEXTEST_CONFIG
    if not path.resolve().is_relative_to(checkout.resolve()):
        problems.append(
            f"{NEXTEST_CONFIG}: the path resolves outside the checkout, so it is "
            "not read"
        )
        return []
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"{NEXTEST_CONFIG}: {probe.read_error}")
        return []
    if not probe.present:
        return []
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        problems.append(f"cannot read {NEXTEST_CONFIG}: {error}")
        return []
    return _counted_rules(document, problems)


def _counted_rules(document: dict[str, object], problems: list[str]) -> list[SetupRule]:
    """Return the rules whose script runs for a plain `cargo nextest run`.

    A rule counts when it sits in the default profile, which every other profile
    inherits, has no `platform` restriction the reader cannot evaluate, names
    only scripts the file defines, and carries a filter the reader evaluates. A
    rule in another profile runs only when that profile is selected, and a
    restricted one only on its platform, so neither can prove a default run
    prepared; each is reported rather than counted or ignored.

    Returns
    -------
    list[SetupRule]
        The counted rules.
    """
    defined = _defined_scripts(document)
    counted: list[SetupRule] = []
    for profile, rule in _profile_rules(document):
        listed = _setup_names(rule)
        if not listed or not all(name in defined for name in listed):
            continue
        filterset = _filter_of(rule)
        reason = _uncounted_reason(profile, rule, filterset)
        if reason is None:
            counted.append(SetupRule(filterset))
        else:
            problems.append(
                f"{NEXTEST_CONFIG}: the setup rule `{filterset}` in profile "
                f"`{profile}` is not counted as preparing any test: {reason}"
            )
    return counted


def _uncounted_reason(
    profile: str, rule: dict[str, object], filterset: str
) -> str | None:
    """Return why a rule cannot prove a default run prepared, or None if it can."""
    if profile != DEFAULT_PROFILE:
        return "it runs only when that profile is selected"
    if "platform" in rule:
        return "it is restricted to a platform the reader does not evaluate"
    if not _is_union_of_selectors(filterset):
        return (
            "its filter uses negation, difference, intersection, a brace or escape "
            "in a glob, or a predicate the reader does not evaluate (it reads "
            "`all()` and unions of `binary(..)`, `binary_id(..)` and `package(..)`)"
        )
    return None


def _filter_of(rule: dict[str, object]) -> str:
    """Return a rule's `filter`, which selects every test when it is absent."""
    selection = rule.get("filter")
    return selection if isinstance(selection, str) else ALL_FILTER


def _matches(matcher: str, name: str) -> bool:
    """Report whether a nextest matcher selects *name*.

    A matcher is `=text` (equal), `~text` (contains), `/pattern/` (a regular
    expression), `#glob` or, with no prefix, a glob, which is the default for
    `binary`, `binary_id` and `package`. A form the reader does not understand
    selects nothing, so it can only fail to credit a preparation, never invent
    one.

    Returns
    -------
    bool
        True when *name* satisfies the matcher.
    """
    text = matcher.strip()
    if text.startswith("="):
        return name == text[1:]
    if text.startswith("~"):
        return text[1:] in name
    if text.startswith("#"):
        return fnmatch.fnmatchcase(name, text[1:])
    if len(text) > _MIN_REGEX and text.startswith("/") and text.endswith("/"):
        try:
            return re.search(text[1:-1], name) is not None
        except re.error:
            return False
    return fnmatch.fnmatchcase(name, text)


def covers(rules: list[SetupRule], binary: str | None, package: str | None) -> bool:
    """Report whether a setup rule selects every test, or the binary or package.

    A `binary(..)` or `binary_id(..)` selector is compared with the binary's
    name and a `package(..)` selector with the package's, so `package(=ui)` does
    not cover a binary called `ui` in package `app`.

    Only `all()` and a union of `binary(..)`, `binary_id(..)` and `package(..)`
    selectors are evaluated. A filter that negates (`not binary(ui)`), subtracts
    (`all() - binary(ui)`) or intersects (`binary(ui) & test(=one)`) names a
    binary without selecting all of it, so a name found in one proves nothing.
    Such a rule never covers, and the reader reports it as a problem.

    Returns
    -------
    bool
        True when nextest runs a setup script before the named binary or crate.
    """
    return any(
        _selects(_ATOM.findall(rule.filter), binary, package)
        for rule in rules
        if _is_union_of_selectors(rule.filter)
    )


def _selects(atoms: list[str], binary: str | None, package: str | None) -> bool:
    """Report whether a union of selectors includes `all()` or the binary or package."""
    return ALL_FILTER in atoms or any(
        _selects_one(atom, binary, package) for atom in atoms
    )


def _selects_one(atom: str, binary: str | None, package: str | None) -> bool:
    """Report whether one selector picks the binary (or the package, for `package`).

    A `binary_id` names the package and the binary joined by `::`
    (`app::ui` for `app/tests/ui.rs`; the package alone is its library's unit
    tests), so it is compared with that form only.

    Returns
    -------
    bool
        True when the selector's matcher selects the named binary or package.
    """
    kind, _, rest = atom.partition("(")
    matcher = rest.removesuffix(")")
    if kind == "package":
        return package is not None and _matches(matcher, package)
    if binary is None:
        return False
    if kind == "binary_id":
        return package is not None and _matches(matcher, f"{package}::{binary}")
    return _matches(matcher, binary)


def _is_union_of_selectors(filterset: str) -> bool:
    """Report whether *filterset* is only `all()` and selectors joined by union."""
    if any(_is_unsupported_glob(atom) for atom in _ATOM.findall(filterset)):
        return False
    return _UNION_GLUE.fullmatch(_ATOM.sub("", filterset)) is not None


def _is_unsupported_glob(atom: str) -> bool:
    """Report whether a selector's matcher is a glob the reader cannot evaluate.

    Only a glob (`#glob`, or text with no prefix) is read with `globset`, which
    also takes braces; an equality, contains or regular-expression matcher may
    hold braces of its own (`/^app[0-9]{2}$/`). A backslash is declined in every
    kind.

    Returns
    -------
    bool
        True when the matcher uses a backslash, or is a glob that uses braces.
    """
    matcher = atom.partition("(")[2].removesuffix(")").strip()
    # A backslash starts an escape nextest decodes (`\u{70}`) or a regex form
    # Python lacks (`\z`), whatever the matcher's kind, so it is never evaluated.
    if "\\" in matcher:
        return True
    is_regex = len(matcher) > _MIN_REGEX and matcher[0] == matcher[-1] == "/"
    if matcher[:1] in {"=", "~"} or is_regex:
        return False
    return _UNSUPPORTED_GLOB.search(matcher) is not None
