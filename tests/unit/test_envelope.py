"""Unit tests for policy-envelope construction from a checkout.

`build_envelope` decides what the policy is asked about: whether the
checkout is a Rust repository, whether it has a Makefile, and what facts
accompany them. That is `concordat.rules.envelope`'s own boundary, so it is
tested apart from the makeutil parser it delegates to.
"""

from __future__ import annotations

import json
import pathlib
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.envelope import build_envelope
from tests.unit.rule_test_support import MINIMAL_REPORT, _write_checkout

if typ.TYPE_CHECKING:
    import os

    from tests.conftest import CmdMox


def _write_surface_path_declaration(checkout: pathlib.Path, path: str) -> pathlib.Path:
    """Write one test-only `.concordat` declaration for a supplied path."""
    manifest_path = checkout / ".concordat"
    manifest_path.write_text(
        f"language:\n  rust:\n    surfaces:\n      - path: {path}\n",
        encoding="utf-8",
    )
    return manifest_path


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

    def test_unreadable_concordat_raises_a_resolution_error(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A manifest read failure cannot impersonate an absent declaration."""
        tmp_path.mkdir(exist_ok=True)
        manifest_path = tmp_path / ".concordat"
        manifest_path.write_text("language: {}\n", encoding="utf-8")
        original_read_text = pathlib.Path.read_text

        def unreadable_manifest(
            path: pathlib.Path,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> str:
            """Raise the filesystem error only for the configured manifest."""
            if path == manifest_path:
                raise PermissionError
            return original_read_text(
                path,
                encoding=encoding,
                errors=errors,
                newline=newline,
            )

        monkeypatch.setattr(pathlib.Path, "read_text", unreadable_manifest)

        with pytest.raises(OperationalRuleError, match="cannot parse") as exc_info:
            build_envelope(tmp_path)

        error = exc_info.value
        assert error.operation == "resolve-rust-surfaces", error.operation
        assert error.resource == manifest_path, error.resource

    def test_unreadable_root_cargo_probe_raises_a_resolution_error(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A root-manifest probe failure cannot become no Rust applicability."""
        tmp_path.mkdir(exist_ok=True)
        cargo_path = tmp_path / "Cargo.toml"
        cargo_path.write_text(
            '[package]\nname = "fixture"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        original_stat = pathlib.Path.stat

        def unreadable_cargo(
            path: pathlib.Path, *, follow_symlinks: bool = True
        ) -> os.stat_result:
            """Raise the filesystem error only for the root Cargo probe."""
            if path == cargo_path:
                raise PermissionError
            return original_stat(path, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(pathlib.Path, "stat", unreadable_cargo)

        with pytest.raises(OperationalRuleError, match="cannot inspect") as exc_info:
            build_envelope(tmp_path)

        error = exc_info.value
        assert error.operation == "resolve-rust-surfaces", error.operation
        assert error.resource == cargo_path, error.resource

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
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "root"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        envelope = build_envelope(tmp_path)

        applicability = typ.cast("dict[str, object]", envelope["applicability"])
        cargo = typ.cast("dict[str, object]", envelope["cargo"])
        surfaces = typ.cast("list[dict[str, object]]", cargo["surfaces"])
        assert applicability["rust_surfaces_declared"] is True, applicability
        assert applicability["root_cargo_toml"] is True, applicability
        assert cargo["parsed"] is None, cargo
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
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "root"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        envelope = build_envelope(tmp_path)

        applicability = typ.cast("dict[str, object]", envelope["applicability"])
        cargo = typ.cast("dict[str, object]", envelope["cargo"])
        assert applicability["rust_surfaces_declared"] is True, applicability
        assert applicability["root_cargo_toml"] is True, applicability
        assert cargo["parsed"] is None, cargo
        assert cargo["surfaces"] == [], cargo

    def test_malformed_concordat_raises_a_resolution_error(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Malformed declarations cannot fall back to root Cargo discovery."""
        tmp_path.mkdir(exist_ok=True)
        manifest_path = tmp_path / ".concordat"
        manifest_path.write_text("language: [\n", encoding="utf-8")

        with pytest.raises(OperationalRuleError, match="cannot parse") as exc_info:
            build_envelope(tmp_path)

        error = exc_info.value
        assert error.operation == "resolve-rust-surfaces", error.operation
        assert error.resource == manifest_path, error.resource

    @pytest.mark.parametrize(
        "declaration",
        [
            "language:\n  rust:\n    surfaces: {}\n",
            "language:\n  rust:\n    surfaces:\n      - invalid\n",
        ],
        ids=["surfaces_mapping", "surface_scalar"],
    )
    def test_malformed_surface_declaration_raises_a_resolution_error(
        self,
        tmp_path: pathlib.Path,
        declaration: str,
    ) -> None:
        """Schema-invalid surface declarations are operationally visible."""
        tmp_path.mkdir(exist_ok=True)
        manifest_path = tmp_path / ".concordat"
        manifest_path.write_text(declaration, encoding="utf-8")

        with pytest.raises(OperationalRuleError) as exc_info:
            build_envelope(tmp_path)

        error = exc_info.value
        assert error.operation == "resolve-rust-surfaces", error.operation
        assert error.resource == manifest_path, error.resource

    @pytest.mark.parametrize(
        "path",
        ["/Cargo.toml", "../Cargo.toml", "rust/Package.toml"],
        ids=["absolute", "traversal", "wrong_basename"],
    )
    def test_unsafe_declared_surface_path_raises_a_resolution_error(
        self,
        tmp_path: pathlib.Path,
        path: str,
    ) -> None:
        """Unsafe manifest paths cannot reach the checkout filesystem."""
        tmp_path.mkdir(exist_ok=True)
        manifest_path = _write_surface_path_declaration(tmp_path, path)

        with pytest.raises(
            OperationalRuleError, match="repository-relative"
        ) as exc_info:
            build_envelope(tmp_path)

        error = exc_info.value
        assert error.operation == "resolve-rust-surfaces", error.operation
        assert error.resource == manifest_path, error.resource

    @pytest.mark.parametrize("escaped", [r"\0", r"\r", r"\n", r"\e"])
    def test_control_character_in_surface_path_raises_a_resolution_error(
        self,
        tmp_path: pathlib.Path,
        escaped: str,
    ) -> None:
        """Control characters are rejected before filesystem path handling."""
        tmp_path.mkdir(exist_ok=True)
        manifest_path = _write_surface_path_declaration(
            tmp_path,
            f'"Cargo{escaped}.toml"',
        )

        with pytest.raises(
            OperationalRuleError, match="control characters"
        ) as exc_info:
            build_envelope(tmp_path)

        error = exc_info.value
        assert error.operation == "resolve-rust-surfaces", error.operation
        assert error.resource == manifest_path, error.resource

    def test_invalid_declared_role_raises_a_resolution_error(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Role overrides stay within the published surface vocabulary."""
        tmp_path.mkdir(exist_ok=True)
        manifest_path = tmp_path / ".concordat"
        manifest_path.write_text(
            "language:\n  rust:\n    surfaces:\n"
            "      - path: Cargo.toml\n        role: integration\n",
            encoding="utf-8",
        )

        with pytest.raises(OperationalRuleError, match=r"workspace.*crate") as exc_info:
            build_envelope(tmp_path)

        error = exc_info.value
        assert error.operation == "resolve-rust-surfaces", error.operation
        assert error.resource == manifest_path, error.resource

    def test_symlinked_declared_surface_cannot_escape_checkout(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A safe-looking declaration cannot resolve through an external link."""
        tmp_path.mkdir(exist_ok=True)
        manifest_path = tmp_path / ".concordat"
        outside_directory = tmp_path.parent / "outside-cargo"
        outside_directory.mkdir(exist_ok=True)
        (outside_directory / "Cargo.toml").write_text(
            '[package]\nname = "outside"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        linked_directory = tmp_path / "rust"
        linked_directory.symlink_to(outside_directory, target_is_directory=True)
        manifest_path.write_text(
            "language:\n  rust:\n    surfaces:\n      - path: rust/Cargo.toml\n",
            encoding="utf-8",
        )

        with pytest.raises(
            OperationalRuleError, match="escapes the checkout"
        ) as exc_info:
            build_envelope(tmp_path)

        error = exc_info.value
        assert error.operation == "resolve-rust-surfaces", error.operation
        assert error.resource == manifest_path, error.resource

    def test_declared_surface_paths_are_normalized_before_emission(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Equivalent safe paths have one envelope spelling and identity."""
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / ".concordat").write_text(
            "language:\n  rust:\n    surfaces:\n      - path: ./Cargo.toml\n",
            encoding="utf-8",
        )
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "fixture"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        envelope = build_envelope(tmp_path)

        cargo = typ.cast("dict[str, object]", envelope["cargo"])
        surfaces = typ.cast("list[dict[str, object]]", cargo["surfaces"])
        assert surfaces[0]["path"] == "Cargo.toml", surfaces

    def test_equivalent_declared_surface_paths_are_duplicates(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Lexical aliases cannot create multiple entries for one manifest."""
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / ".concordat").write_text(
            "language:\n  rust:\n    surfaces:\n"
            "      - path: Cargo.toml\n      - path: ./Cargo.toml\n",
            encoding="utf-8",
        )
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "fixture"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        with pytest.raises(OperationalRuleError, match=r"repeats 'Cargo\.toml'"):
            build_envelope(tmp_path)

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

        assert exc_info.value.operation == "resolve-rust-surfaces", exc_info.value
        assert exc_info.value.resource == tmp_path / ".concordat", exc_info.value
