"""Name the pinned `makeutil` revision in a failure that its drift can cause.

Fixture envelopes record what `makeutil parse` printed, diagnostic locations
included, and `makeutil --version` reads `0.1.0` for every revision, so a
checkout built against an older `makeutil` fails a regeneration comparison
with a diff nothing in the message explains. CI installs the release asset named
in `.github/workflows/ci.yml`, so that file is the single source the hint reads.
"""

from __future__ import annotations

import pathlib
import re
import typing as typ

_CI_WORKFLOW: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
)


class MakeutilPin(typ.NamedTuple):
    """The release asset CI installs: its version, file name and SHA-256."""

    version: str
    asset: str
    sha256: str


def _variable(text: str, name: str, workflow: pathlib.Path) -> str:
    """Return the quoted value of the `name:` env entry in *text*.

    Returns
    -------
    str
        The text between the quotes.

    Raises
    ------
    LookupError
        If *workflow* does not set `name`.
    """
    match = re.search(rf'^\s*{name}:\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        message = f"{workflow} carries no {name} pin"
        raise LookupError(message)
    return match.group(1)


def pinned_release(workflow: pathlib.Path = _CI_WORKFLOW) -> MakeutilPin:
    """Return the `makeutil` release asset CI installs, read from *workflow*.

    Returns
    -------
    MakeutilPin
        The version, asset name and SHA-256 the workflow pins. A missing
        variable raises `LookupError` from the reader.

    Examples
    --------
    >>> len(pinned_release().sha256)
    64
    """
    text = workflow.read_text(encoding="utf-8")
    return MakeutilPin(
        _variable(text, "MAKEUTIL_VERSION", workflow),
        _variable(text, "MAKEUTIL_ASSET", workflow),
        _variable(text, "MAKEUTIL_SHA256", workflow),
    )


def drift_hint(workflow: pathlib.Path = _CI_WORKFLOW) -> str:
    """Return the sentence to append to a fixture-regeneration failure."""
    pin = pinned_release(workflow)
    return (
        "if only a makeutil diagnostic location differs, the local makeutil is "
        f"not the pinned release v{pin.version} (`makeutil --version` reads "
        "0.1.0 for every revision, so it cannot tell): install the "
        f"{pin.asset} asset from the leynos/makeutil release and check its "
        f"SHA-256 is {pin.sha256}"
    )
