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
    """Return the check to run when a fixture comparison fails.

    The pin is offered as something to verify, not as the diagnosis: a changed
    fixture or generator also moves a diagnostic location. A workflow that
    cannot be read or carries no pin yields a sentence saying so, because the
    hint is built while another assertion is already failing and must not
    replace that failure with its own exception.

    Parameters
    ----------
    workflow
        The workflow file that pins the release; defaults to the repository's
        `ci.yml`.

    Returns
    -------
    str
        One sentence naming the pinned release, asset and SHA-256 to verify
        the local `makeutil` against, or why the pin could not be read.

    Examples
    --------
    >>> "SHA-256" in drift_hint()
    True
    """
    try:
        pin = pinned_release(workflow)
    except (OSError, UnicodeDecodeError, LookupError) as error:
        return f"the pinned makeutil release could not be read ({error})"
    return (
        "before blaming the fixtures, check the local makeutil against the "
        f"pinned release v{pin.version}: `makeutil --version` does not "
        "distinguish revisions, so compare the SHA-256 of the "
        f"{pin.asset} asset from the leynos/makeutil release with {pin.sha256}"
    )


def assert_matches_recorded(
    recorded: object,
    regenerated: object,
    label: str,
    workflow: pathlib.Path = _CI_WORKFLOW,
) -> None:
    """Assert that *recorded* equals *regenerated*, or fail with the pin check.

    Parameters
    ----------
    recorded
        The checked-in value.
    regenerated
        What regeneration produced.
    label
        What is being compared, named first in the failure message.
    workflow
        The workflow file that pins the release.

    Raises
    ------
    AssertionError
        If the two differ, carrying `label` and the result of `drift_hint`.

    Examples
    --------
    >>> assert_matches_recorded({"a": 1}, {"a": 1}, "envelope")
    """
    if recorded != regenerated:
        message = f"{label} differs from regeneration: {drift_hint(workflow)}"
        raise AssertionError(message)
