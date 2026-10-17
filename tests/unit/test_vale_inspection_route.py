"""Contracts for optional, reproducible Vale prose inspection."""

from __future__ import annotations

import configparser
import dataclasses
import os
import pathlib
import re
import shutil
import subprocess
import typing as typ

import pytest

REPOSITORY_ROOT: typ.Final = pathlib.Path(__file__).resolve().parents[2]
MAKEFILE: typ.Final = REPOSITORY_ROOT / "Makefile"
VALE_CONFIG: typ.Final = REPOSITORY_ROOT / ".vale.ini"
MAKE: typ.Final = pathlib.Path(shutil.which("make") or "/usr/bin/make")
VALE_PACKAGE_PATH: typ.Final = ".vale/packages/concordat-0.3.1.zip"
VALE_RELEASES: typ.Final = "https://github.com/vale-cli/vale/releases/download"
VALE_ASSET: typ.Final = "vale_3.22.0_Linux_64-bit.tar.gz"
VALE_SHA256: typ.Final = (
    "52f5cd0314a1b7384cac6aa102a68193977312f6ba9c9f3ae001b5deec8e3a10"
)
VALE_MEMBER_SIZE: typ.Final = "45954304"
VALE_HELPER_REF: typ.Final = "031bad66e27daf137f5c4acd8f0c2d8201c3c726"
VALE_OVERRIDES: typ.Final = ("VALE=vale", "UV=uv", "VALE_PYTHON=3.13")
EXPECTED_HELPER_CALL: typ.Final = (
    "uv\trun --isolated --python 3.13 --with "
    f"git+https://github.com/leynos/concordat-vale.git@{VALE_HELPER_REF} "
    "python scripts/update_acronym_allowlist.py"
)
EXPECTED_INSTALL_CALL: typ.Final = (
    "uv\trun --isolated --python 3.13 scripts/install_release_binary.py install vale"
)
EXPECTED_STYLE_ARCHIVE_CALL: typ.Final = (
    "uv\trun --isolated --no-project --python 3.13 --with "
    "cyclopts==4.25.2 python -m scripts.vale_style_archive"
)
EXPECTED_STYLE_CHECK_CALL: typ.Final = f"{EXPECTED_STYLE_ARCHIVE_CALL} check"
EXPECTED_STYLE_INSTALL_CALL: typ.Final = f"{EXPECTED_STYLE_ARCHIVE_CALL} install"
EXPECTED_ROUTE_PREFIX: typ.Final = (EXPECTED_STYLE_CHECK_CALL, "vale\tsync")


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class _ValeSandbox:
    """Own one Make test's working directory and fake-tool boundary."""

    cwd: pathlib.Path
    bin_directory: pathlib.Path
    log_path: pathlib.Path


def _configuration() -> configparser.ConfigParser:
    """Parse Vale's global options and selectors as ordinary INI sections."""
    configuration = configparser.ConfigParser(interpolation=None, strict=True)
    vale_text = VALE_CONFIG.read_text(encoding="utf-8")
    configuration.read_string(f"[vale-global]\n{vale_text}")
    return configuration


def _write_executable(path: pathlib.Path, contents: str) -> None:
    """Create an executable command stub for the Make boundary tests."""
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _tool_directory(
    tmp_path: pathlib.Path,
    *,
    vale: bool = True,
    system: str = "Linux",
    machine: str = "x86_64",
) -> _ValeSandbox:
    """Create logged fake tools without contacting Vale or uv services."""
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    log_path = tmp_path / "vale-tools.log"
    uv = """#!/bin/sh
printf 'uv\\t%s\\n' "$*" >> "$VALE_TEST_LOG"
printf 'pins\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \\
  "$VALE_VERSION" "$VALE_ASSET" "$VALE_SHA256" "$VALE_RELEASES" \\
  "$VALE_ARCHIVE_FORMAT" "$VALE_ARCHIVE_MEMBER" \\
  "$VALE_ARCHIVE_MEMBER_SIZE" "$RUNNER_TEMP|$GITHUB_PATH" >> "$VALE_TEST_LOG"
case "$VALE_TEST_FAIL:$*" in
  style-check:*python\\ -m\\ scripts.vale_style_archive\\ check*) exit 23 ;;
  helper:*update_acronym_allowlist.py*) exit 17 ;;
  installer:*install_release_binary.py*) exit 19 ;;
esac
"""
    _write_executable(bin_directory / "uv", uv)
    uname = f"""#!/bin/sh
case "$1" in
  -s) printf '%s\\n' '{system}' ;;
  -m) printf '%s\\n' '{machine}' ;;
  *) exit 2 ;;
esac
"""
    _write_executable(bin_directory / "uname", uname)
    if vale:
        vale_stub = """#!/bin/sh
printf 'vale\\t%s\\n' "$*" >> "$VALE_TEST_LOG"
case "$VALE_TEST_FAIL:$*" in
  sync:sync) exit 11 ;;
  inspection:--no-global\\ .) exit 13 ;;
esac
"""
        _write_executable(bin_directory / "vale", vale_stub)
    return _ValeSandbox(cwd=tmp_path, bin_directory=bin_directory, log_path=log_path)


def _run_make(
    sandbox: _ValeSandbox,
    target: str,
    *,
    fail: str = "",
    overrides: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run one optional Make route against the fake tool boundary."""
    environment = os.environ.copy()
    environment["PATH"] = f"{sandbox.bin_directory}{os.pathsep}{environment['PATH']}"
    environment["VALE_TEST_LOG"] = str(sandbox.log_path)
    environment["VALE_TEST_FAIL"] = fail
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - Fixed Make target and arguments.
        [
            str(MAKE),
            "--no-print-directory",
            "-f",
            str(MAKEFILE),
            target,
            *overrides,
        ],
        cwd=sandbox.cwd,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )


def _calls(log_path: pathlib.Path) -> list[str]:
    """Return the logged tool calls, omitting the installer environment row."""
    if not log_path.exists():
        return []
    return [
        row
        for row in log_path.read_text(encoding="utf-8").splitlines()
        if row.startswith(("vale\t", "uv\t"))
    ]


def test_released_configuration_uses_local_package_and_vocabulary() -> None:
    """Use only the cached style package and its built-in vocabulary."""
    configuration = _configuration()
    global_options = configuration["vale-global"]

    assert global_options["packages"] == VALE_PACKAGE_PATH
    assert global_options["stylespath"] == ".vale/styles"
    assert global_options["minalertlevel"] == "warning"
    assert global_options["vocab"] == "concordat"
    assert "vocabpath" not in global_options
    assert configuration.sections() == [
        "vale-global",
        "docs/**/*.{md,markdown,mdx}",
        "AGENTS.md",
        "*.rs",
        "README.md",
    ]
    assert configuration["docs/**/*.{md,markdown,mdx}"]["blockignores"] == (
        r"(?m)^\[\^\d+\]:[^\n]*(?:\n[ \t]+[^\n]*)*"
    )
    assert configuration["*.rs"]["concordat.rustnorun"] == "NO"
    assert configuration["*.rs"]["concordat.acronyms"] == "NO"
    assert configuration["README.md"]["concordat.pronouns"] == "NO"


def test_vale_defaults_to_the_ignored_local_binary() -> None:
    """The optional installer and default executable share the ignored tree."""
    makefile = MAKEFILE.read_text(encoding="utf-8")
    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert re.search(
        r"^VALE \?= \$\(CURDIR\)/\.vale/tools/vale-bin/vale$",
        makefile,
        flags=re.MULTILINE,
    )
    assert re.search(r"^\.vale$", gitignore, flags=re.MULTILINE)


def test_vale_runs_sync_then_pinned_helper_then_inspection(
    tmp_path: pathlib.Path,
) -> None:
    """The local inspection route is ordered and pins its helper runtime."""
    sandbox = _tool_directory(tmp_path)
    result = _run_make(sandbox, "vale", overrides=VALE_OVERRIDES)

    assert result.returncode == 0, result.stderr
    assert _calls(sandbox.log_path) == [
        *EXPECTED_ROUTE_PREFIX,
        EXPECTED_HELPER_CALL,
        "vale\t--no-global .",
    ]


def test_vale_style_archive_check_failure_stops_before_sync(
    tmp_path: pathlib.Path,
) -> None:
    """A failed cached style check prevents Vale from syncing."""
    sandbox = _tool_directory(tmp_path)
    result = _run_make(sandbox, "vale", fail="style-check", overrides=VALE_OVERRIDES)

    assert result.returncode != 0
    assert _calls(sandbox.log_path) == [EXPECTED_STYLE_CHECK_CALL]


def test_vale_supports_executable_paths_with_spaces(
    tmp_path: pathlib.Path,
) -> None:
    """Quote the injectable Vale path for both executable invocations."""
    sandbox = _tool_directory(tmp_path, vale=False)
    vale_directory = sandbox.cwd / "vale binary"
    vale_directory.mkdir()
    spaced_vale = vale_directory / "vale"
    _write_executable(
        spaced_vale,
        '#!/bin/sh\nprintf \'vale\\t%s\\n\' "$*" >> "$VALE_TEST_LOG"\n',
    )
    result = _run_make(
        sandbox,
        "vale",
        overrides=(f"VALE={spaced_vale}", "UV=uv", "VALE_PYTHON=3.13"),
    )

    assert result.returncode == 0, result.stderr
    assert _calls(sandbox.log_path) == [
        *EXPECTED_ROUTE_PREFIX,
        EXPECTED_HELPER_CALL,
        "vale\t--no-global .",
    ]


@pytest.mark.parametrize(
    ("failure", "expected_calls"),
    [
        pytest.param("sync", [*EXPECTED_ROUTE_PREFIX], id="sync-failure"),
        pytest.param(
            "helper",
            [*EXPECTED_ROUTE_PREFIX, EXPECTED_HELPER_CALL],
            id="helper-failure",
        ),
        pytest.param(
            "inspection",
            [
                *EXPECTED_ROUTE_PREFIX,
                EXPECTED_HELPER_CALL,
                "vale\t--no-global .",
            ],
            id="inspection-failure",
        ),
    ],
)
def test_vale_route_propagates_each_step_failure(
    tmp_path: pathlib.Path,
    failure: str,
    expected_calls: list[str],
) -> None:
    """A failed sync, helper or inspection stops the remaining route."""
    sandbox = _tool_directory(tmp_path)
    result = _run_make(
        sandbox,
        "vale",
        fail=failure,
        overrides=VALE_OVERRIDES,
    )

    assert result.returncode != 0
    assert _calls(sandbox.log_path) == expected_calls


def test_missing_vale_preflight_gives_the_install_command(
    tmp_path: pathlib.Path,
) -> None:
    """The preflight names the supported installer when its binary is absent."""
    sandbox = _tool_directory(tmp_path, vale=False)
    missing_binary = sandbox.cwd / "missing-vale"
    result = _run_make(
        sandbox,
        "vale",
        overrides=(f"VALE={missing_binary}",),
    )

    assert result.returncode != 0
    assert "make install-vale" in result.stderr
    assert _calls(sandbox.log_path) == []


def test_install_targets_use_the_pinned_binary_and_local_styles(
    tmp_path: pathlib.Path,
) -> None:
    """Binary install adds styles; external Vale can install styles alone."""
    sandbox = _tool_directory(tmp_path)
    result = _run_make(
        sandbox,
        "install-vale",
        overrides=("UV=uv", "VALE_PYTHON=3.13"),
    )

    assert result.returncode == 0, result.stderr
    calls = _calls(sandbox.log_path)
    assert calls == [EXPECTED_INSTALL_CALL, EXPECTED_STYLE_INSTALL_CALL]
    assert (sandbox.cwd / ".vale/tools").is_dir()
    assert (sandbox.cwd / ".vale/tools/github_path").is_file()
    rows = sandbox.log_path.read_text(encoding="utf-8").splitlines()
    assert rows[1] == (
        "pins\t3.22.0\t"
        f"{VALE_ASSET}\t{VALE_SHA256}\t{VALE_RELEASES}\t"
        f"tar.gz\tvale\t{VALE_MEMBER_SIZE}\t"
        f"{sandbox.cwd / '.vale/tools'}|{sandbox.cwd / '.vale/tools/github_path'}"
    )
    standalone = _run_make(
        sandbox,
        "install-vale-styles",
        overrides=("VALE=/external/vale", "UV=uv"),
    )
    assert standalone.returncode == 0, standalone.stderr
    assert _calls(sandbox.log_path) == [*calls, EXPECTED_STYLE_INSTALL_CALL]


def test_install_rejects_other_platforms_before_invoking_uv(
    tmp_path: pathlib.Path,
) -> None:
    """The archive pin is not presented as a portable installer."""
    sandbox = _tool_directory(
        tmp_path,
        system="Darwin",
        machine="x86_64",
    )
    result = _run_make(
        sandbox,
        "install-vale",
        overrides=("UV=uv",),
    )

    assert result.returncode != 0
    assert "Linux x86_64 only" in result.stderr
    assert _calls(sandbox.log_path) == []


def test_install_failure_propagates_without_fallback(tmp_path: pathlib.Path) -> None:
    """A failed binary installer stops the optional provisioning target."""
    sandbox = _tool_directory(tmp_path)
    result = _run_make(
        sandbox,
        "install-vale",
        fail="installer",
        overrides=("UV=uv",),
    )

    assert result.returncode != 0
    assert _calls(sandbox.log_path) == [EXPECTED_INSTALL_CALL]


def test_standard_make_targets_do_not_reach_vale(tmp_path: pathlib.Path) -> None:
    """Vale remains an optional inspection outside routine build and CI gates."""
    sandbox = _tool_directory(tmp_path)
    environment = os.environ.copy()
    environment["PATH"] = f"{sandbox.bin_directory}{os.pathsep}{environment['PATH']}"
    environment["VALE_TEST_LOG"] = str(sandbox.log_path)
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - Fixed Make targets and arguments.
        [
            str(MAKE),
            "--no-print-directory",
            "--dry-run",
            "-f",
            str(MAKEFILE),
            "all",
            "lint",
            "typecheck",
            "markdownlint",
            "fmt",
            "check-fmt",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "update_acronym_allowlist.py" not in result.stdout
    assert "vale sync" not in result.stdout
    assert "--no-global ." not in result.stdout
