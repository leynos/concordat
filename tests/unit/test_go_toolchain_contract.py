"""Contract tests for the Go release selected by the suite workflows."""

from __future__ import annotations

import enum
import re
import typing as typ

import pytest

from tests.unit.gate_provisioning_support import GO_VERSION, REPOSITORY_ROOT

_TERRATEST_GO_MOD: typ.Final = (
    REPOSITORY_ROOT / "platform-standards/tofu/terratest/go.mod"
)
_GO_RELEASE: typ.Final = re.compile(
    r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)"
    r"(?:\.(?P<patch>0|[1-9]\d*))?"
)


class _GoReleaseContext(enum.Enum):
    """Identify the contract input that supplied a Go release string."""

    SELECTED_VERSION = "selected Go version"
    MODULE_DIRECTIVE = "go.mod `go` directive"


class _InvalidGoReleaseError(ValueError):
    """Report a release string that cannot be ordered as a Go version."""

    def __init__(self, value: str, *, context: _GoReleaseContext) -> None:
        """Build a diagnostic that names the invalid release's source."""
        self.value = value
        self.context = context
        super().__init__(f"{context.value} is not a Go release version: {value!r}")


class _GoDirectiveCountError(ValueError):
    """Report a module file without exactly one Go version directive."""

    def __init__(self, count: int) -> None:
        """Build a diagnostic with the number of directives that were found."""
        self.count = count
        super().__init__(
            f"go.mod must contain exactly one `go` directive (found {count})"
        )


class _MalformedGoDirectiveError(ValueError):
    """Report a Go directive whose fields do not contain one version."""

    def __init__(self, field_count: int) -> None:
        """Build a diagnostic with the number of fields in the directive."""
        self.field_count = field_count
        super().__init__(
            "the go.mod `go` directive must contain one version "
            f"(found {field_count} fields)"
        )


def _go_release(value: str, *, context: _GoReleaseContext) -> tuple[int, int, int]:
    """Parse a Go release into components for ordering."""
    match = _GO_RELEASE.fullmatch(value)
    if match is None:
        raise _InvalidGoReleaseError(value, context=context)
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or "0")


def _module_go_floor(module_contents: str) -> tuple[int, int, int]:
    """Read the sole Go minimum version declared by a module file."""
    directives: list[str] = []
    for line in module_contents.splitlines():
        fields = line.partition("//")[0].split()
        if fields and fields[0] == "go":
            if len(fields) != 2:
                raise _MalformedGoDirectiveError(field_count=len(fields))
            directives.append(fields[1])

    if len(directives) != 1:
        raise _GoDirectiveCountError(count=len(directives))
    return _go_release(directives[0], context=_GoReleaseContext.MODULE_DIRECTIVE)


def _meets_module_go_floor(selected_version: str, module_contents: str) -> bool:
    """Return whether the selected release satisfies the module minimum."""
    selected = _go_release(selected_version, context=_GoReleaseContext.SELECTED_VERSION)
    required = _module_go_floor(module_contents)
    return selected >= required


def test_selected_go_version_meets_the_nested_terratest_module_floor() -> None:
    """The shared setup-go pin must satisfy the tracked Terratest module."""
    module_contents = _TERRATEST_GO_MOD.read_text(encoding="utf-8")

    assert _meets_module_go_floor(GO_VERSION, module_contents), (
        f"shared Go {GO_VERSION} is below the minimum in "
        f"{_TERRATEST_GO_MOD.relative_to(REPOSITORY_ROOT)}"
    )


def test_previous_go_1_22_12_pin_is_below_the_nested_module_floor() -> None:
    """The previous hosted pin must fail the nested module's Go floor."""
    module_contents = _TERRATEST_GO_MOD.read_text(encoding="utf-8")

    assert not _meets_module_go_floor("1.22.12", module_contents), (
        "the previous hosted Go 1.22.12 pin must be rejected by the tracked "
        "Terratest module floor"
    )


@pytest.mark.parametrize(
    ("selected_version", "module_contents", "message"),
    [
        pytest.param(
            "1.26.9",
            "module example.test/tool\n",
            "exactly one `go` directive",
            id="missing-go-directive",
        ),
        pytest.param(
            "1.26.9",
            "module example.test/tool\ngo\n",
            "must contain one version",
            id="missing-directive-version",
        ),
        pytest.param(
            "1.26.9",
            "module example.test/tool\ngo 1.26.x\n",
            "not a Go release version",
            id="malformed-module-version",
        ),
        pytest.param(
            "1.26.x",
            "module example.test/tool\ngo 1.26.0\n",
            "not a Go release version",
            id="malformed-selected-version",
        ),
    ],
)
def test_go_floor_contract_rejects_malformed_directives_and_versions(
    selected_version: str,
    module_contents: str,
    message: str,
) -> None:
    """Malformed module directives and release strings must be rejected."""
    with pytest.raises(ValueError, match=re.escape(message)):
        _meets_module_go_floor(selected_version, module_contents)
