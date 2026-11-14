"""Unit tests for the `policy-input/python-duplication-gate` envelope builder.

The Rego suite proves what the policy makes of each envelope; these tests prove
what the builder records: which files a scan selects once `exclude` is applied
relative to each root, which of them sit in a test tree, and which settings it
refuses to read.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.duplication_gate_envelope import (
    DEFAULT_TEST_DIRECTORY_NAMES,
    MAX_LISTED_TEST_FILES,
    build_duplication_gate_envelope,
)

if typ.TYPE_CHECKING:
    import types

CODE = "def run() -> None:\n    pass\n"
_PACKAGE_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / "python-duplication-gate"
)


def _pyproject(
    *, roots: str = '["pkg"]', top: str = "0", exclude: str = "[]", extra: str = ""
) -> str:
    """Return a `pyproject.toml` with a `[tool.nose]` table."""
    return (
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool.nose]\n'
        f"roots = {roots}\ntop = {top}\nexclude = {exclude}\n{extra}"
    )


def _lay_out(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write *files*, creating directories."""
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class TestApplicability:
    """The envelope says whether the repository uses the gate at all."""

    def test_no_pyproject(self, tmp_path: pathlib.Path) -> None:
        """A checkout without `pyproject.toml` has neither file nor table."""
        envelope = build_duplication_gate_envelope(tmp_path)
        assert envelope["applicability"] == {"pyproject": False, "nose_table": False}
        assert envelope["nose"] is None
        assert envelope["roots"] == []

    def test_no_nose_table(self, tmp_path: pathlib.Path) -> None:
        """A project file without `[tool.nose]` does not use the gate."""
        _lay_out(tmp_path, {"pyproject.toml": "[tool.ruff]\nline-length = 88\n"})
        envelope = build_duplication_gate_envelope(tmp_path)
        assert envelope["applicability"] == {"pyproject": True, "nose_table": False}

    def test_undecodable_pyproject_carries_its_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A file that is not TOML is recorded, not read as absent."""
        _lay_out(tmp_path, {"pyproject.toml": "[tool.nose\n"})
        envelope = build_duplication_gate_envelope(tmp_path)
        assert envelope["pyproject"] is not None
        assert "invalid TOML" in str(envelope["pyproject"]["error"])
        assert envelope["applicability"]["nose_table"] is False

    def test_a_pyproject_outside_the_checkout_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No policy input is read from outside the audited tree."""
        outside = tmp_path / "outside.toml"
        outside.write_text("[tool.nose]\n", encoding="utf-8")
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "pyproject.toml").symlink_to(outside)
        with pytest.raises(OperationalRuleError):
            build_duplication_gate_envelope(checkout)


class TestSettings:
    """The settings are read from the table, and what cannot be is recorded."""

    def test_top_is_recorded(self, tmp_path: pathlib.Path) -> None:
        """A set `top` is carried as its integer."""
        _lay_out(tmp_path, {"pyproject.toml": _pyproject(top="30"), "pkg/a.py": CODE})
        nose = build_duplication_gate_envelope(tmp_path)["nose"]
        assert nose is not None
        assert (nose["top_present"], nose["top"], nose["problems"]) == (True, 30, [])

    def test_unset_top_is_recorded_as_absent(self, tmp_path: pathlib.Path) -> None:
        """An unset `top` is distinct from `top = 0`."""
        text = _pyproject().replace("top = 0\n", "")
        _lay_out(tmp_path, {"pyproject.toml": text, "pkg/a.py": CODE})
        nose = build_duplication_gate_envelope(tmp_path)["nose"]
        assert nose is not None
        assert (nose["top_present"], nose["top"]) == (False, None)

    @pytest.mark.parametrize("top", ['"0"', "true", "0.0"])
    def test_a_top_that_is_not_an_integer_is_a_problem(
        self, tmp_path: pathlib.Path, top: str
    ) -> None:
        """A boolean or string is not the integer Cargo-style settings expect."""
        _lay_out(tmp_path, {"pyproject.toml": _pyproject(top=top)})
        nose = build_duplication_gate_envelope(tmp_path)["nose"]
        assert nose is not None
        assert nose["problems"]
        assert build_duplication_gate_envelope(tmp_path)["roots"] == []

    @pytest.mark.parametrize(
        "roots",
        ['"pkg"', "[]", "[1]", '[""]', '["/abs"]', '["../up"]', '["a/../../up"]'],
    )
    def test_unusable_roots_are_problems(
        self, tmp_path: pathlib.Path, roots: str
    ) -> None:
        """Roots that cannot name a path inside the checkout are not scanned."""
        _lay_out(tmp_path, {"pyproject.toml": _pyproject(roots=roots)})
        envelope = build_duplication_gate_envelope(tmp_path)
        assert envelope["nose"] is not None
        assert envelope["nose"]["problems"]
        assert envelope["roots"] == []

    @pytest.mark.parametrize("exclude", ['"tests/**"', "[1]", '["tests\\\\"]'])
    def test_unusable_excludes_are_problems(
        self, tmp_path: pathlib.Path, exclude: str
    ) -> None:
        """An exclude that is not a list of valid patterns is not guessed at."""
        _lay_out(tmp_path, {"pyproject.toml": _pyproject(exclude=exclude)})
        nose = build_duplication_gate_envelope(tmp_path)["nose"]
        assert nose is not None
        assert nose["problems"]

    def test_nose_that_is_not_a_table_is_a_problem(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`tool.nose = 3` is the gate's table being wrong, not absent."""
        _lay_out(tmp_path, {"pyproject.toml": "[tool]\nnose = 3\n"})
        envelope = build_duplication_gate_envelope(tmp_path)
        assert envelope["applicability"]["nose_table"] is True
        assert envelope["nose"] is not None
        assert envelope["nose"]["problems"] == ["[tool.nose] must be a table"]


class TestSelection:
    """The scan selects what the detector would, and test trees are found."""

    def _roots(
        self, checkout: pathlib.Path, files: dict[str, str], **table: str
    ) -> list[dict[str, object]]:
        """Return the envelope's root facts for a layout."""
        _lay_out(checkout, {"pyproject.toml": _pyproject(**table), **files})
        return [
            dict(root) for root in build_duplication_gate_envelope(checkout)["roots"]
        ]

    def test_exclude_is_matched_relative_to_the_root(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`tests/**` excludes `pkg/tests`, but `pkg/tests/**` excludes nothing."""
        files = {"pkg/a.py": CODE, "pkg/tests/t.py": CODE, "pkg/sub/tests/t.py": CODE}
        relative = self._roots(tmp_path, files, exclude='["tests/**"]')
        assert (relative[0]["selected_count"], relative[0]["test_file_count"]) == (2, 1)
        repository = self._roots(tmp_path, files, exclude='["pkg/tests/**"]')
        assert (repository[0]["selected_count"], repository[0]["test_file_count"]) == (
            3,
            2,
        )

    def test_a_directory_pattern_matches_at_any_depth(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An unanchored trailing-slash pattern is git's, not a literal prefix."""
        files = {"pkg/a.py": CODE, "pkg/sub/tests/t.py": CODE}
        roots = self._roots(tmp_path, files, exclude='["tests/"]')
        assert (roots[0]["selected_count"], roots[0]["test_file_count"]) == (1, 0)

    def test_negation_brings_a_file_back(self, tmp_path: pathlib.Path) -> None:
        """A `!` pattern re-includes, and the re-included test file is reported."""
        files = {"pkg/tests/t.py": CODE, "pkg/tests/keep.py": CODE}
        roots = self._roots(tmp_path, files, exclude='["tests/**", "!tests/keep.py"]')
        assert roots[0]["test_files"] == ["pkg/tests/keep.py"]

    def test_only_python_files_are_selected(self, tmp_path: pathlib.Path) -> None:
        """Prose and data in a test tree select nothing, so nothing is reported."""
        files = {"pkg/tests/data.json": "{}\n", "pkg/tests/notes.md": "x\n"}
        roots = self._roots(tmp_path, files)
        assert (roots[0]["selected_count"], roots[0]["test_file_count"]) == (0, 0)

    @pytest.mark.parametrize(
        ("root", "files"),
        [
            ("tests", {"tests/t.py": CODE}),
            ("pkg/tests", {"pkg/tests/t.py": CODE}),
            ("tests/helpers", {"tests/helpers/h.py": CODE}),
            ("pkg", {"pkg/unittests/t.py": CODE}),
            ("pkg", {"pkg/testing/t.py": CODE}),
            ("pkg", {"pkg/test/t.py": CODE}),
        ],
    )
    def test_each_default_name_marks_a_test_tree(
        self, tmp_path: pathlib.Path, root: str, files: dict[str, str]
    ) -> None:
        """The root may be, lie inside, or hold the test tree."""
        roots = self._roots(tmp_path, files, roots=json.dumps([root]))
        assert roots[0]["test_file_count"] == 1

    def test_behaviour_is_not_a_test_tree_by_default(
        self, tmp_path: pathlib.Path
    ) -> None:
        """BDD step trees stay scanned until the estate decides otherwise."""
        assert "behaviour" not in DEFAULT_TEST_DIRECTORY_NAMES
        roots = self._roots(tmp_path, {"pkg/behaviour/steps.py": CODE})
        assert roots[0]["test_file_count"] == 0

    def test_the_manifest_parameter_replaces_the_names(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`test_directory_names` decides which directories hold tests."""
        _lay_out(
            tmp_path,
            {
                "pyproject.toml": _pyproject(),
                "pkg/behaviour/steps.py": CODE,
                "pkg/tests/t.py": CODE,
            },
        )
        envelope = build_duplication_gate_envelope(
            tmp_path, {"test_directory_names": ["behaviour"]}
        )
        assert envelope["test_directory_names"] == ["behaviour"]
        assert envelope["roots"][0]["test_files"] == ["pkg/behaviour/steps.py"]

    def test_hidden_and_vendored_trees_are_not_walked(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A virtual environment's own tests are not the repository's."""
        files = {"pkg/.venv/tests/t.py": CODE, "pkg/node_modules/tests/t.py": CODE}
        roots = self._roots(tmp_path, {**files, "pkg/a.py": CODE})
        assert (roots[0]["selected_count"], roots[0]["test_file_count"]) == (1, 0)

    def test_a_file_root_is_selected_unless_excluded_by_name(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A single-file root selects that file; an exclude can name it."""
        files = {"tool.py": CODE, "data.json": "{}\n"}
        roots = self._roots(tmp_path, files, roots='["tool.py", "data.json"]')
        assert [r["selected_count"] for r in roots] == [1, 0]
        roots = self._roots(tmp_path, files, roots='["tool.py"]', exclude='["tool.py"]')
        assert roots[0]["selected_count"] == 0

    def test_an_absent_root_does_not_exist(self, tmp_path: pathlib.Path) -> None:
        """A missing root is recorded as absent, not as empty."""
        roots = self._roots(tmp_path, {}, roots='["missing"]')
        assert (roots[0]["exists"], roots[0]["selected_count"]) == (False, 0)

    def test_a_symlinked_python_file_is_not_selected(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Links are never followed, so the walk stays inside the tree."""
        _lay_out(tmp_path, {"pyproject.toml": _pyproject(), "real.py": CODE})
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "link.py").symlink_to(tmp_path / "real.py")
        assert (
            build_duplication_gate_envelope(tmp_path)["roots"][0]["selected_count"] == 0
        )

    def test_a_root_resolving_outside_the_checkout_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A symlinked root cannot carry the scan out of the audited tree."""
        outside = tmp_path / "outside"
        _lay_out(outside, {"a.py": CODE})
        checkout = tmp_path / "checkout"
        _lay_out(checkout, {"pyproject.toml": _pyproject()})
        (checkout / "pkg").symlink_to(outside, target_is_directory=True)
        with pytest.raises(OperationalRuleError):
            build_duplication_gate_envelope(checkout)

    def test_listed_test_files_are_capped_but_counted(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The evidence is bounded; the count is not."""
        total = MAX_LISTED_TEST_FILES + 5
        files = {f"pkg/tests/t{index:03d}.py": CODE for index in range(total)}
        roots = self._roots(tmp_path, files)
        assert roots[0]["test_file_count"] == total
        assert (
            len(typ.cast("list[str]", roots[0]["test_files"])) == MAX_LISTED_TEST_FILES
        )


def _load_generator() -> types.ModuleType:
    """Import `fixtures/generate.py` by path, as its directory is not a package."""
    path = _PACKAGE_DIR / "fixtures" / "generate.py"
    spec = importlib.util.spec_from_file_location("duplication_gate_generate", path)
    if spec is None or spec.loader is None:
        message = f"could not load a module spec from {path}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestFixtureGenerator:
    """The checked-in fixtures are what the production builder produces."""

    def test_the_bundle_is_what_regeneration_would_write(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A stale `data.json` would test the policy on an envelope nobody builds."""
        generator = _load_generator()
        parameters = generator.manifest_parameters()
        expected = {
            "fixtures": {
                key: generator.build_fixture_envelope(files, parameters)
                for key, files in generator.SCENARIOS.items()
            },
            "parameters": parameters,
        }
        recorded = json.loads(
            (_PACKAGE_DIR / "fixtures" / "data.json").read_text(encoding="utf-8")
        )
        assert recorded == expected

    def test_the_manifest_defaults_name_the_estate_test_directories(self) -> None:
        """The rule's default names are the ruled four, in the ruled order."""
        parameters = _load_generator().manifest_parameters()
        assert parameters["test_directory_names"] == list(DEFAULT_TEST_DIRECTORY_NAMES)
