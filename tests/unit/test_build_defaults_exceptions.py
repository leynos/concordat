"""Unit tests for the recorded build-defaults exceptions (BD-010).

The declaration is structured data in `.concordat`; the reader checks it
against the files it names, and the policy (not the reader) decides whether the
exception is substantiated. These tests pin the facts the reader reports.
"""

from __future__ import annotations

import textwrap
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.build_defaults_exceptions import (
    ROUTE_ABSENT,
    ROUTE_OK,
    ROUTE_UNREADABLE,
    inspect_standard_exceptions,
)

if typ.TYPE_CHECKING:
    import pathlib

ROUTE = "tools/dev-fast/config.toml"
ADR = "docs/adr-012.md"
FAST_ROUTE = textwrap.dedent(
    """\
    [target.'cfg(target_os = "linux")']
    rustflags = ["-Zthreads=8", "-Clink-arg=-fuse-ld=mold"]
    """
)


def _declare(checkout: pathlib.Path, body: str) -> None:
    """Write a `.concordat` declaring *body* under `language.rust`."""
    (checkout / ".concordat").write_text(
        "language:\n  rust:\n" + textwrap.indent(textwrap.dedent(body), "    "),
        encoding="utf-8",
    )


def _entry(**overrides: str) -> str:
    """Return a declaration with one exception entry."""
    fields = {
        "route": ROUTE,
        "adr": ADR,
        "rationale": "Wheels build without mold.",
        "review_trigger": "Revisit when the wheel images carry mold.",
    }
    fields.update(overrides)
    lines = "\n".join(f"      {key}: {value}" for key, value in fields.items())
    return f"build_defaults:\n  exceptions:\n    - clauses: [BD-002, BD-004]\n{lines}\n"


def _files(checkout: pathlib.Path, route: str | None = FAST_ROUTE) -> None:
    """Create the route configuration and the decision record."""
    (checkout / "tools" / "dev-fast").mkdir(parents=True)
    if route is not None:
        (checkout / ROUTE).write_text(route, encoding="utf-8")
    (checkout / "docs").mkdir()
    (checkout / ADR).write_text("# ADR\n", encoding="utf-8")


def test_a_checkout_without_a_declaration_has_no_exceptions(
    tmp_path: pathlib.Path,
) -> None:
    """No `.concordat`, or none under `language.rust`, yields no exceptions."""
    assert inspect_standard_exceptions(tmp_path) == []
    (tmp_path / ".concordat").write_text("language:\n  rust: {}\n", encoding="utf-8")
    assert inspect_standard_exceptions(tmp_path) == []


def test_a_substantiated_exception_reports_its_facts(tmp_path: pathlib.Path) -> None:
    """The route's flags and the record's presence are reported as facts."""
    _files(tmp_path)
    _declare(tmp_path, _entry())
    [exception] = inspect_standard_exceptions(tmp_path)
    assert exception["clauses"] == ["BD-002", "BD-004"]
    assert exception["route_state"] == ROUTE_OK
    assert exception["route_threads"] is True
    assert exception["route_linker"] is True
    assert exception["adr_present"] is True


@pytest.mark.parametrize(
    ("route", "threads", "linker"),
    [
        (
            '[target.\'cfg(target_os = "linux")\']\nrustflags = ["-Zthreads=8"]\n',
            True,
            False,
        ),
        (
            (
                "[target.'cfg(target_os = \"linux\")']\n"
                'rustflags = ["-Clink-arg=-fuse-ld=mold"]\n'
            ),
            False,
            True,
        ),
        (
            (
                "[target.'cfg(target_os = \"windows\")']\n"
                'rustflags = ["-Zthreads=8", "-Clink-arg=-fuse-ld=mold"]\n'
            ),
            True,
            False,
        ),
        ("[unstable]\ncodegen-backend = true\n", False, False),
    ],
    ids=["no-linker", "no-threads", "linker-off-linux", "no-rustflags"],
)
def test_a_route_missing_a_fast_flag_is_reported(
    tmp_path: pathlib.Path, route: str, *, threads: bool, linker: bool
) -> None:
    """A route that omits the frontend or a Linux linker reports it."""
    _files(tmp_path, route)
    _declare(tmp_path, _entry())
    [exception] = inspect_standard_exceptions(tmp_path)
    assert (exception["route_threads"], exception["route_linker"]) == (threads, linker)


def test_a_split_flag_spelling_counts(tmp_path: pathlib.Path) -> None:
    """`-C link-arg=...` and `-Z threads=8` are the same flags as the joined forms."""
    _files(
        tmp_path,
        "[build]\nrustflags = ['-Z', 'threads=8']\n"
        "[target.'cfg(target_os = \"linux\")']\n"
        "rustflags = ['-Z', 'threads=8', '-C', 'link-arg=-fuse-ld=mold']\n",
    )
    _declare(tmp_path, _entry())
    [exception] = inspect_standard_exceptions(tmp_path)
    assert (exception["route_threads"], exception["route_linker"]) == (True, True)


def test_a_missing_route_or_record_is_reported(tmp_path: pathlib.Path) -> None:
    """An absent route file and an absent decision record are facts, not errors."""
    (tmp_path / "docs").mkdir()
    _declare(tmp_path, _entry())
    [exception] = inspect_standard_exceptions(tmp_path)
    assert exception["route_state"] == ROUTE_ABSENT
    assert exception["adr_present"] is False


def test_an_unparsable_route_is_reported(tmp_path: pathlib.Path) -> None:
    """A route that is not TOML is unreadable, which the policy treats as unproved."""
    _files(tmp_path, "this is = not [toml\n")
    _declare(tmp_path, _entry())
    [exception] = inspect_standard_exceptions(tmp_path)
    assert exception["route_state"] == ROUTE_UNREADABLE


@pytest.mark.parametrize(
    "override",
    [{"route": "../outside.toml"}, {"route": "/etc/passwd"}, {"adr": "../adr.md"}],
    ids=["route-escapes", "route-absolute", "adr-escapes"],
)
def test_a_path_that_escapes_the_checkout_is_not_followed(
    tmp_path: pathlib.Path, override: dict[str, str]
) -> None:
    """A declared path outside the checkout reads as absent."""
    _files(tmp_path)
    _declare(tmp_path, _entry(**override))
    [exception] = inspect_standard_exceptions(tmp_path)
    if "route" in override:
        assert exception["route_state"] == ROUTE_ABSENT
    else:
        assert exception["adr_present"] is False


@pytest.mark.parametrize(
    "body",
    [
        "build_defaults: []\n",
        "build_defaults:\n  exceptions: nope\n",
        "build_defaults:\n  exceptions:\n    - just a string\n",
        (
            "build_defaults:\n  exceptions:\n    - clauses: BD-002\n      route: a\n"
            "      adr: b\n      rationale: c\n      review_trigger: d\n"
        ),
        "build_defaults:\n  exceptions:\n    - clauses: [BD-002]\n      route: a\n",
    ],
    ids=[
        "not-a-mapping",
        "not-a-list",
        "entry-not-a-mapping",
        "clauses-not-a-list",
        "fields-missing",
    ],
)
def test_a_malformed_declaration_is_an_operational_failure(
    tmp_path: pathlib.Path, body: str
) -> None:
    """The rule never reads a malformed declaration as a waiver."""
    _declare(tmp_path, body)
    with pytest.raises(OperationalRuleError):
        inspect_standard_exceptions(tmp_path)


def test_the_flags_come_from_the_rule_parameters(tmp_path: pathlib.Path) -> None:
    """The route is held to the flags the standard names, not to literals."""
    _files(tmp_path, '[build]\nrustflags = ["-Zthreads=4"]\n')
    _declare(tmp_path, _entry())
    [exception] = inspect_standard_exceptions(tmp_path, {"threads_flag": "-Zthreads=4"})
    assert exception["route_threads"] is True


def test_the_envelope_carries_exceptions_only_when_declared(
    tmp_path: pathlib.Path,
) -> None:
    """Undeclared exceptions leave the envelope's bytes as they were."""
    from concordat.rules.envelope import build_build_defaults_envelope

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "fixture"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    assert "standard_exceptions" not in build_build_defaults_envelope(tmp_path)
    _files(tmp_path)
    _declare(tmp_path, _entry())
    envelope = build_build_defaults_envelope(tmp_path)
    assert envelope["standard_exceptions"][0]["route_state"] == ROUTE_OK
