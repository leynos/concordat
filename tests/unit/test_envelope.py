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
        (tmp_path / "Cargo.toml").write_text("not = [valid", encoding="utf-8")
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
        from concordat.rules import rust_surfaces as rust_surfaces_module

        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "x"\n', encoding="utf-8"
        )
        monkeypatch.setattr(
            rust_surfaces_module.tomllib,
            "loads",
            lambda _text: ["not", "a", "table"],
        )
        with pytest.raises(
            OperationalRuleError, match="did not parse to a table"
        ) as exc_info:
            build_envelope(tmp_path)
        assert exc_info.value.operation == "parse-cargo-toml", exc_info.value.operation

    def test_declared_nested_surface_is_authoritative(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A declared nested workspace replaces root-manifest applicability."""
        rust_directory = tmp_path / "rust"
        rust_directory.mkdir(parents=True)
        (tmp_path / ".concordat").write_text(
            "language:\n  rust:\n    surfaces:\n      - path: rust/Cargo.toml\n",
            encoding="utf-8",
        )
        (rust_directory / "Cargo.toml").write_text(
            "[workspace]\nmembers = []\n",
            encoding="utf-8",
        )

        envelope = build_envelope(tmp_path)

        applicability = typ.cast("dict[str, object]", envelope["applicability"])
        cargo = typ.cast("dict[str, object]", envelope["cargo"])
        surfaces = typ.cast("list[dict[str, object]]", cargo["surfaces"])
        assert applicability["rust_surfaces_declared"] is True, applicability
        assert applicability["root_cargo_toml"] is False, applicability
        assert surfaces == [
            {
                "path": "rust/Cargo.toml",
                "role": "workspace",
                "parsed": {"workspace": {"members": []}},
            }
        ], surfaces

    def test_empty_declared_surface_list_disables_rust_applicability(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """An explicit empty list means that this checkout governs no Rust."""
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / ".concordat").write_text(
            "language:\n  rust:\n    surfaces: []\n",
            encoding="utf-8",
        )

        envelope = build_envelope(tmp_path)

        applicability = typ.cast("dict[str, object]", envelope["applicability"])
        cargo = typ.cast("dict[str, object]", envelope["cargo"])
        assert applicability["rust_surfaces_declared"] is True, applicability
        assert cargo["surfaces"] == [], cargo

    def test_absent_surface_declaration_preserves_root_fallback(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """An unrelated manifest leaves root Cargo discovery intact."""
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / ".concordat").write_text("enrolled: true\n", encoding="utf-8")
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "fixture"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        envelope = build_envelope(tmp_path)

        applicability = typ.cast("dict[str, object]", envelope["applicability"])
        cargo = typ.cast("dict[str, object]", envelope["cargo"])
        surfaces = typ.cast("list[dict[str, object]]", cargo["surfaces"])
        assert applicability["rust_surfaces_declared"] is False, applicability
        assert surfaces[0]["path"] == "Cargo.toml", surfaces
        assert surfaces[0]["role"] == "crate", surfaces

    def test_missing_declared_surface_raises_with_its_path(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A declaration cannot silently fall back when its Cargo file is absent."""
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / ".concordat").write_text(
            "language:\n  rust:\n    surfaces:\n      - path: rust/Cargo.toml\n",
            encoding="utf-8",
        )

        with pytest.raises(OperationalRuleError, match=r"rust/Cargo\.toml") as exc_info:
            build_envelope(tmp_path)

        assert exc_info.value.operation == "resolve-rust-surfaces"
        assert exc_info.value.resource == tmp_path / ".concordat"
