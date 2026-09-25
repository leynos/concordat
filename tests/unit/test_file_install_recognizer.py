"""Tests for recognizing a release binary placed on `PATH` by ``install``.

The recognizer is driven with commands it must accept and commands it must
refuse, since running it only over the repository's own workflows would pass
whether or not it discriminates.
"""

from __future__ import annotations

import pytest

from tests.unit.gate_provisioning_support import installed_tool_names


@pytest.mark.parametrize(
    "command",
    [
        ("install", "-D", "-m", "0755", "dl/makeutil-asset", "${bin_dir}/makeutil"),
        ("install", "dl/makeutil-asset", "/usr/local/bin/makeutil"),
        ("PATH=/x", "install", "-m", "0755", "dl/makeutil-asset", "bin/makeutil"),
    ],
)
def test_install_of_one_file_provisions_its_destination(
    command: tuple[str, ...],
) -> None:
    """Copying one file to a literal file name provisions that name."""
    assert installed_tool_names(command) == frozenset({"makeutil"})


@pytest.mark.parametrize(
    "command",
    [
        ("install", "-d", "${bin_dir}"),
        ("install", "--directory", "bin"),
        ("install", "-t", "bin", "dl/a", "dl/b"),
        ("install", "dl/makeutil-asset"),
        ("install", "dl/makeutil-asset", "${bin_dir}/${name}"),
        ("install", "-m", "0755", "dl/a", "dl/b", "bin/"),
        # With several sources the destination is a directory, not a name.
        ("install", "dl/a", "dl/b", "bin/tools"),
        ("echo", "install", "dl/makeutil-asset", "bin/makeutil"),
    ],
)
def test_other_install_shapes_provision_nothing(command: tuple[str, ...]) -> None:
    """Directory creation, several sources, or an expanded name are refused.

    ``echo install ...`` runs ``echo``, not ``install``, so it installs
    nothing either.
    """
    assert installed_tool_names(command) == frozenset()
