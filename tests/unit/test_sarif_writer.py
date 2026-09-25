"""Unit tests for persisting a SARIF log to disk."""

from __future__ import annotations

import json
import typing as typ

from concordat.auditor.models import Finding
from concordat.auditor.sarif import SarifBuilder

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_write_persists_the_built_document_as_utf8_json(tmp_path: Path) -> None:
    """The written file decodes as UTF-8 and holds the built SARIF document.

    The message carries non-ASCII text. `json.dumps` escapes it, so the file
    must be pure ASCII whatever the host locale, and must still round-trip the
    original message.
    """
    builder = SarifBuilder(tool_name="concordat-auditor")
    builder.add_findings(
        [Finding(rule_id="LC-001", message="Licence für café", level="error")],
        resource_fallback="LICENSE",
    )
    target = tmp_path / "nested" / "audit.sarif"

    written = builder.write(target)

    assert written == target, "write must return the path it wrote"
    raw = target.read_bytes()
    assert raw.isascii(), "the SARIF log must not depend on the host encoding"
    document = json.loads(raw.decode("utf-8"))
    assert document["version"] == "2.1.0", "the log must declare SARIF 2.1.0"
    results = document["runs"][0]["results"]
    assert [result["message"]["text"] for result in results] == ["Licence für café"], (
        "the finding message must round-trip unchanged"
    )
