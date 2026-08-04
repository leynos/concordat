"""Unit tests for policy-envelope construction from a checkout.

`build_envelope` decides what the policy is asked about: whether the
checkout is a Rust repository, whether it has a Makefile, and what facts
accompany them. That is `concordat.rules.envelope`'s own boundary, so it is
tested apart from the makeutil parser it delegates to.
"""

from __future__ import annotations

import json
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.envelope import build_envelope
from tests.unit.rule_test_support import MINIMAL_REPORT, _write_checkout

if typ.TYPE_CHECKING:
    import pathlib

    from tests.conftest import CmdMox


class TestBuildEnvelope:
    """Envelope construction from a checkout directory."""

    def test_empty_checkout_yields_inapplicable_envelope(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Empty checkout yields inapplicable envelope."""
        _write_checkout(tmp_path, cargo=False, makefile=False)
        envelope = build_envelope(tmp_path)
        assert envelope["schema_version"] == 1, envelope
        applicability = typ.cast("dict[str, object]", envelope["applicability"])
        assert applicability["root_cargo_toml"] is False, applicability
        assert applicability["root_makefile"] is False, applicability
        assert envelope["makefile"] is None, envelope["makefile"]

    def test_full_checkout_yields_facts(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Full checkout yields facts."""
        _write_checkout(tmp_path, cargo=True, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        cmd_mox.replay()
        envelope = build_envelope(tmp_path)
        cmd_mox.verify()
        applicability = typ.cast("dict[str, object]", envelope["applicability"])
        assert applicability["root_cargo_toml"] is True, applicability
        assert applicability["root_makefile"] is True, applicability
        cargo = typ.cast("dict[str, object]", envelope["cargo"])
        parsed = typ.cast("dict[str, object]", cargo["parsed"])
        assert parsed["package"] == {"name": "fixture", "version": "0.1.0"}, parsed
        makefile = typ.cast("dict[str, object]", envelope["makefile"])
        assert makefile["schema_version"] == 1, makefile

    def test_invalid_cargo_toml_raises(self, tmp_path: pathlib.Path) -> None:
        """Invalid cargo toml raises with structured context."""
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "Cargo.toml").write_text("not = [valid")
        with pytest.raises(OperationalRuleError, match=r"Cargo\.toml") as exc_info:
            build_envelope(tmp_path)
        error = exc_info.value
        assert error.operation == "parse-cargo-toml", error.operation
        assert error.tool is None, error.tool
        assert error.resource == tmp_path / "Cargo.toml", error.resource

    def test_non_table_cargo_structure_raises(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cargo TOML that parses to a non-table cannot fill the envelope."""
        from concordat.rules import envelope as envelope_module

        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
        monkeypatch.setattr(
            envelope_module.tomllib,
            "loads",
            lambda _text: ["not", "a", "table"],
        )
        with pytest.raises(
            OperationalRuleError, match="did not parse to a table"
        ) as exc_info:
            build_envelope(tmp_path)
        assert exc_info.value.operation == "parse-cargo-toml", exc_info.value.operation
