"""Shared fixtures for the unit suite."""

from __future__ import annotations

import dataclasses
import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pygit2
import pytest

import concordat.persistence.gitops as gitops
import concordat.persistence.models as persistence_models
from concordat import estate_execution, estate_repository
from concordat.estate import EstateRecord, RemoteProbe

if typ.TYPE_CHECKING:
    import unittest.mock as mock

    import pytest_mock


@pytest.fixture
def mock_remote_probe(
    mocker: pytest_mock.MockFixture,
) -> typ.Callable[..., mock.Mock]:
    """Return a factory for mocking estate remote probes."""

    def factory(
        *,
        reachable: bool,
        exists: bool,
        empty: bool,
        error: str | None = None,
    ) -> mock.Mock:
        probe = RemoteProbe(
            reachable=reachable,
            exists=exists,
            empty=empty,
            error=error,
        )
        return mocker.patch.object(
            estate_repository, "_probe_remote", return_value=probe
        )

    return factory


@pytest.fixture
def mock_bootstrap(mocker: pytest_mock.MockFixture) -> mock.Mock:
    """Mock template bootstrapping during init_estate tests."""
    return mocker.patch.object(estate_repository, "_bootstrap_template")


@pytest.fixture
def fake_github_client(mocker: pytest_mock.MockFixture) -> mock.Mock:
    """Patch `_build_client` with a GitHub client that has no repository yet.

    The organisation mock is reachable as
    ``fake_github_client.organization.return_value`` for tests that assert
    the repository was created through it.

    Returns
    -------
    mock.Mock
        The patched GitHub client mock.
    """
    client = mocker.Mock()
    client.repository.return_value = None
    client.organization.return_value = mocker.Mock()
    mocker.patch.object(estate_repository, "_build_client", return_value=client)
    return client


@pytest.fixture
def xdg_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    """Redirect every XDG base into the test's temporary directory.

    Shared so no test reads or writes the developer's real XDG directories,
    and so an active owner written by one test cannot be observed by another.

    Returns
    -------
    dict[str, str]
        The XDG environment mapping installed for the test.
    """
    mapping = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    for key, value in mapping.items():
        monkeypatch.setenv(key, value)
    return mapping


@dataclasses.dataclass(frozen=True)
class ConflictExpectation:
    """Expected behavior for conflict handling tests."""

    force: bool
    expect_error: bool
    expected_backend: str
    expected_manifest: dict[str, str]


class StubS3:
    """Stub S3 client used in persistence tests."""

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, str]:
        """Return enabled status for bucket versioning."""
        return {"Status": "Enabled"}

    def put_object(self, **kwargs: object) -> dict[str, str]:
        """Simulate writing an object."""
        return {}

    def delete_object(self, **kwargs: object) -> dict[str, str]:
        """Simulate deleting an object."""
        return {}


def _make_repo(root: Path) -> pygit2.Repository:
    """Create a seeded repository at root."""
    root = Path(root)
    repo = pygit2.init_repository(root, initial_head="main")
    readme = root / "README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text("seed\n", encoding="utf-8")
    index = repo.index
    index.add("README.md")
    index.write()
    tree_oid = index.write_tree()
    sig = pygit2.Signature("Test", "test@example.com")
    repo.create_commit("refs/heads/main", sig, sig, "seed", tree_oid, [])
    return repo


@pytest.fixture
def stub_s3() -> type[StubS3]:
    """Provide a stub S3 client class for persistence tests."""
    return StubS3


@pytest.fixture
def persist_repo_setup(
    tmp_path: Path,
) -> tuple[Path, pygit2.Repository, Path, EstateRecord]:
    """Create a working repo, bare remote, and estate record."""
    workdir = tmp_path / "workdir"
    repo = _make_repo(workdir)

    bare = tmp_path / "remote.git"
    pygit2.init_repository(str(bare), bare=True)
    upstream = repo.remotes.create("upstream", str(bare))
    upstream.push(["refs/heads/main:refs/heads/main"])

    record = EstateRecord(
        alias="core",
        repo_url=str(bare),
        github_owner="example",
    )
    return workdir, repo, bare, record


@pytest.fixture
def persist_prompts() -> typ.Iterator[str]:
    """Return standard prompt responses for persistence flows."""
    return iter([
        "df12",
        "fr-par",
        "https://s3.fr-par.scw.cloud",
        "estates/example/main",
        "terraform.tfstate",
    ])


@pytest.fixture
def persist_monkeypatch_base(
    monkeypatch: pytest.MonkeyPatch,
    persist_repo_setup: tuple[Path, pygit2.Repository, Path, EstateRecord],
    xdg_env: dict[str, str],
) -> None:
    """Apply shared isolated-environment setup for persistence tests.

    `xdg_env` is depended on for its ordering, not its value. `persist_estate`
    reads the active owner and the owner's credentials file, so these tests
    need those paths pinned to this test's own `tmp_path`: the autouse
    `_isolated_xdg_bases` in `tests/conftest.py` already keeps every test out
    of the real home, but it points at a separate factory-made directory, so
    a test cannot write a credentials file where the code under test will
    look for it.
    """
    del xdg_env
    workdir, _, _, _ = persist_repo_setup
    monkeypatch.setattr(
        estate_execution,
        "ensure_estate_cache",
        lambda _: workdir,
    )
    monkeypatch.setattr(
        gitops,
        "_branch_name",
        lambda *args, **kwargs: "estate/persist-test",
    )


@dataclasses.dataclass(frozen=True)
class PersistTestContext:
    """Shared context for persist_estate integration-style tests."""

    workdir: Path
    repo: pygit2.Repository
    bare: Path
    record: EstateRecord
    prompts: typ.Iterator[str]
    stub_s3: type[StubS3]


@pytest.fixture
def persist_test_context(
    persist_repo_setup: tuple[Path, pygit2.Repository, Path, EstateRecord],
    persist_prompts: typ.Iterator[str],
    persist_monkeypatch_base: None,
    stub_s3: type[StubS3],
) -> PersistTestContext:
    """Bundle common fixtures for persist_estate tests.

    XDG isolation arrives through `persist_monkeypatch_base`, which owns the
    shared environment setup these tests need.

    Returns
    -------
    PersistTestContext
        The grouped fixtures used by persist-estate tests.
    """
    workdir, repo, bare, record = persist_repo_setup
    return PersistTestContext(
        workdir=workdir,
        repo=repo,
        bare=bare,
        record=record,
        prompts=persist_prompts,
        stub_s3=stub_s3,
    )


@pytest.fixture
def conflict_test_setup(tmp_path: Path) -> tuple[Path, Path]:
    """Set up paths with existing content for conflict testing."""
    backend_path = tmp_path / "backend.tfbackend"
    manifest_path = tmp_path / "backend" / "persistence.yaml"
    backend_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    backend_path.write_text("old-backend", encoding="utf-8")
    with manifest_path.open("w", encoding="utf-8") as handle:
        persistence_models._yaml.dump({"bucket": "old"}, handle)
    return backend_path, manifest_path


@pytest.fixture(
    params=[
        ConflictExpectation(
            force=False,
            expect_error=True,
            expected_backend="old-backend",
            expected_manifest={"bucket": "old"},
        ),
        ConflictExpectation(
            force=True,
            expect_error=False,
            expected_backend="new-backend",
            expected_manifest={"bucket": "new"},
        ),
    ],
    ids=["raises_without_force", "overwrites_with_force"],
)
def expectation(request: pytest.FixtureRequest) -> ConflictExpectation:
    """Provide conflict scenarios for write handling."""
    return request.param


def _make_record(repo_path: Path, alias: str = "core") -> EstateRecord:
    """Create an EstateRecord pointing at the provided repository path."""
    return EstateRecord(
        alias=alias,
        repo_url=str(repo_path),
        github_owner="example",
    )


@pytest.fixture
def fake_tofu(monkeypatch: pytest.MonkeyPatch) -> list[typ.Any]:
    """Provide a reusable FakeTofu stub and capture created instances."""
    created: list[typ.Any] = []

    class _FakeTofu:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.cwd = cwd
            self.env = env
            self.calls: list[list[str]] = []
            created.append(self)

        def _record(self, verb: str, extra_args: list[str]) -> SimpleNamespace:
            args = [verb, *extra_args]
            self.calls.append(args)
            return SimpleNamespace(stdout="", stderr="", returncode=0, errored=False)

        def init(
            self,
            *,
            backend_conf: str | None = None,
            disable_backends: bool = False,
            extra_args: list[str] | None = None,
        ) -> bool:
            args = list(extra_args or [])
            if backend_conf:
                args.append(f"-backend-config={backend_conf}")
            self._record(
                "init",
                args
                + (
                    [f"--disable-backends={disable_backends}"]
                    if disable_backends
                    else []
                ),
            )
            return True

        def plan(
            self, *, extra_args: list[str] | None = None
        ) -> tuple[SimpleNamespace, SimpleNamespace]:
            self._record("plan", list(extra_args or []))
            plan_log = SimpleNamespace(stdout="", stderr="", errored=False)
            plan = SimpleNamespace(errored=False)
            return plan_log, plan

        def apply(
            self,
            plan_file: str | None = None,
            *,
            extra_args: list[str] | None = None,
        ) -> SimpleNamespace:
            args = list(extra_args or [])
            if plan_file:
                args.append(plan_file)
            result = self._record("apply", args)
            result.plan_file = plan_file
            return result

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            # Fallback path when public methods are unavailable.
            return self._record(args[0], args[1:])

    monkeypatch.setattr("concordat.estate_execution.Tofu", _FakeTofu)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", _FakeTofu)
    return created
