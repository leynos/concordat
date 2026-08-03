"""Unit tests for the rust-makefile-baseline fixture generator.

`fixtures/generate.py` produces the policy-input envelopes and the bundled
`data.json` that the Rego suite verifies against, so a silent change in what
it emits is a silent change in what the policy is tested on. Nothing here
runs `makeutil` or launches a subprocess: `subprocess.run` is replaced, so
the tests describe the generator's own decisions rather than the tool's.

The rule package directory contains a hyphen, so it is not an importable
package name; the module is loaded from its path instead.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    import types

_GENERATE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / "rust-makefile-baseline"
    / "fixtures"
    / "generate.py"
)


def _load_generator() -> types.ModuleType:
    """Import `fixtures/generate.py` by path and return the module."""
    spec = importlib.util.spec_from_file_location(
        "rust_makefile_baseline_generate", _GENERATE_PATH
    )
    if spec is None or spec.loader is None:
        message = f"could not load a module spec from {_GENERATE_PATH}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generate() -> types.ModuleType:
    """Return the loaded fixture-generator module."""
    return _load_generator()


class TestValidateReturncode:
    """Only a clean parse — or a recovered one for its own fixture — passes."""

    def test_success_is_accepted_for_any_fixture(
        self,
        generate: types.ModuleType,
    ) -> None:
        """Exit 0 is accepted regardless of which fixture was parsed."""
        generate._validate_returncode(0, pathlib.Path("anything.mk"), "")

    def test_recovery_is_accepted_only_for_the_recovered_fixture(
        self,
        generate: types.ModuleType,
    ) -> None:
        """Exit 1 is the recovered fixture's expected outcome."""
        generate._validate_returncode(1, pathlib.Path("recovered.mk"), "")

    @pytest.mark.parametrize(
        ("returncode", "stem"),
        [
            pytest.param(1, "soft-skip", id="recovery-on-another-fixture"),
            pytest.param(2, "recovered", id="hard-failure-on-recovered"),
            pytest.param(2, "soft-skip", id="hard-failure"),
        ],
    )
    def test_other_outcomes_are_refused(
        self,
        generate: types.ModuleType,
        returncode: int,
        stem: str,
    ) -> None:
        """Any other exit status aborts generation rather than being recorded.

        A recovered parse is a partial one: accepting it for a fixture that
        should parse cleanly would bake makeutil's guesswork into the policy
        inputs without anyone noticing.
        """
        path = pathlib.Path(f"{stem}.mk")

        with pytest.raises(generate.MakeutilFixtureError) as info:
            generate._validate_returncode(returncode, path, "boom")

        assert info.value.resource == path, info.value.resource
        assert info.value.tool == "makeutil", info.value.tool


class TestParseMakefile:
    """`parse_makefile` translates launch failures and decodes success."""

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(FileNotFoundError("makeutil"), id="absent-binary"),
            pytest.param(
                subprocess.TimeoutExpired(cmd="makeutil", timeout=30),
                id="timeout",
            ),
        ],
    )
    def test_launch_failures_become_a_domain_error(
        self,
        generate: types.ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        error: Exception,
    ) -> None:
        """A missing or hung makeutil names the fixture it could not parse."""
        source = tmp_path / "example.mk"
        source.write_text("all:\n\t@true\n", encoding="utf-8")

        def refuse(*_args: object, **_kwargs: object) -> typ.NoReturn:
            raise error

        monkeypatch.setattr(generate.subprocess, "run", refuse)

        with pytest.raises(generate.MakeutilFixtureError) as info:
            generate.parse_makefile(source)

        assert info.value.resource == source, info.value.resource
        assert info.value.tool == "makeutil", info.value.tool
        assert "could not run makeutil" in str(info.value), str(info.value)

    def test_a_successful_parse_returns_the_decoded_report(
        self,
        generate: types.ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """The report is makeutil's stdout, decoded, and nothing else."""
        source = tmp_path / "example.mk"
        source.write_text("all:\n\t@true\n", encoding="utf-8")
        report = {"rules": [{"targets": ["all"]}], "includes": []}
        calls: list[dict[str, object]] = []

        def fake_run(args: list[str], **kwargs: object) -> object:
            calls.append({"args": args, "cwd": kwargs.get("cwd")})
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps(report), stderr=""
            )

        monkeypatch.setattr(generate.subprocess, "run", fake_run)

        assert generate.parse_makefile(source) == report

        # The recorded `source.path` must stay machine-independent, so makeutil
        # is invoked on the bare filename from the fixture's own directory.
        assert calls[0]["args"] == ["makeutil", "parse", "example.mk"], calls
        assert calls[0]["cwd"] == tmp_path, calls


class TestSyntheticEnvelopes:
    """The two envelopes with no Makefile behind them."""

    def test_both_synthetic_cases_are_present(
        self,
        generate: types.ModuleType,
    ) -> None:
        """`no_makefile` and `not_rust` are generated without a fixture."""
        assert set(generate.synthetic_envelopes()) == {"no_makefile", "not_rust"}

    @pytest.mark.parametrize(
        ("key", "root_cargo_toml"),
        [
            pytest.param("no_makefile", True, id="rust-without-a-makefile"),
            pytest.param("not_rust", False, id="not-a-rust-repository"),
        ],
    )
    def test_applicability_distinguishes_the_two(
        self,
        generate: types.ModuleType,
        key: str,
        root_cargo_toml: bool,  # noqa: FBT001 - a parametrized case, not a flag
    ) -> None:
        """Both lack a Makefile; only one is a Rust repository.

        The pair exists to separate "the rule does not apply here" from "the
        rule applies and the Makefile is missing", so their Cargo
        applicability must differ while their Makefile applicability matches.
        """
        envelope = generate.synthetic_envelopes()[key]
        applicability = typ.cast("dict[str, object]", envelope["applicability"])

        assert applicability["root_cargo_toml"] is root_cargo_toml, envelope
        assert applicability["root_makefile"] is False, envelope
        assert envelope["makefile"] is None, envelope
        cargo = typ.cast("dict[str, object]", envelope["cargo"])
        expected_cargo = generate.CARGO_PARSED if root_cargo_toml else None
        assert cargo["parsed"] == expected_cargo, envelope


class TestMain:
    """`main` writes one envelope per input plus the bundled data document."""

    @pytest.fixture
    def generated(
        self,
        generate: types.ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> pathlib.Path:
        """Run `main` against a temporary tree and return the fixtures root."""
        makefiles = tmp_path / "makefiles"
        makefiles.mkdir()
        for stem in ("alpha", "soft-skip"):
            (makefiles / f"{stem}.mk").write_text("all:\n\t@true\n", encoding="utf-8")
        # A non-`.mk` file must be ignored rather than parsed.
        (makefiles / "README.md").write_text("not a fixture\n", encoding="utf-8")

        monkeypatch.setattr(generate, "FIXTURES_DIR", tmp_path)
        monkeypatch.setattr(generate, "MAKEFILES_DIR", makefiles)
        monkeypatch.setattr(generate, "ENVELOPES_DIR", tmp_path / "envelopes")
        monkeypatch.setattr(
            generate,
            "parse_makefile",
            lambda path: {"source": {"path": path.name}},
        )

        generate.main()
        return tmp_path

    def test_every_input_and_synthetic_case_is_written(
        self,
        generated: pathlib.Path,
    ) -> None:
        """One envelope per `.mk` input, plus the two synthetic ones."""
        written = {path.name for path in (generated / "envelopes").iterdir()}
        assert written == {
            "alpha.json",
            "soft_skip.json",
            "no_makefile.json",
            "not_rust.json",
        }, written

    def test_hyphens_become_underscores_in_the_key(
        self,
        generated: pathlib.Path,
    ) -> None:
        """Keys are Rego-addressable, so `soft-skip.mk` becomes `soft_skip`."""
        envelope = json.loads(
            (generated / "envelopes" / "soft_skip.json").read_text(encoding="utf-8")
        )
        makefile = typ.cast("dict[str, object]", envelope["makefile"])
        source = typ.cast("dict[str, object]", makefile["source"])
        assert source["path"] == "soft-skip.mk", envelope

    def test_the_bundle_carries_exactly_the_same_fixtures(
        self,
        generated: pathlib.Path,
    ) -> None:
        """`data.json` and the envelope files describe the same set.

        `conftest verify --data` reads the bundle, not the individual files,
        so a bundle that drifts from them would test something no envelope on
        disk represents.
        """
        bundle = json.loads((generated / "data.json").read_text(encoding="utf-8"))
        fixtures = typ.cast("dict[str, object]", bundle["fixtures"])

        assert set(fixtures) == {"alpha", "soft_skip", "no_makefile", "not_rust"}, (
            f"the bundle should hold every generated envelope, got {set(fixtures)}"
        )
        for key, envelope in fixtures.items():
            on_disk = json.loads(
                (generated / "envelopes" / f"{key}.json").read_text(encoding="utf-8")
            )
            assert on_disk == envelope, f"{key} differs between the bundle and the file"
