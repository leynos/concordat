"""Unit tests for the estate repository-provisioning decision layer.

This covers `estate_repository` itself: owner resolution, the remote-state
dispatch behind `_prepare_repository`, the ordering contract of
`_ensure_repository_exists`, and the re-export seams the rest of the suite
patches. The git and GitHub implementations it delegates to are tested in
`test_estate_git` and `test_estate_github`.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from concordat import estate, estate_repository
from concordat.estate import GitHubOwnerConfirmationAbortedError
from concordat.estate_repository import _resolve_and_confirm_owner

if typ.TYPE_CHECKING:
    import github3
    import pytest_mock


@dataclasses.dataclass
class OwnerResolutionScenario:
    """Test scenario for resolving github_owner."""

    slug: str | None
    github_owner: str | None
    confirm_response: str
    expected_result: str | None
    confirmer_called: bool
    should_raise: type[Exception] | None


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            OwnerResolutionScenario(
                slug="example/platform-standards",
                github_owner="sandbox",
                confirm_response="yes",
                expected_result="sandbox",
                confirmer_called=False,
                should_raise=None,
            ),
            id="explicit-owner-bypasses-confirmation",
        ),
        pytest.param(
            OwnerResolutionScenario(
                slug="example/platform-standards",
                github_owner=None,
                confirm_response="yes",
                expected_result="example",
                confirmer_called=True,
                should_raise=None,
            ),
            id="inferred-owner-confirmed",
        ),
        pytest.param(
            OwnerResolutionScenario(
                slug="example/platform-standards",
                github_owner=None,
                confirm_response="no",
                expected_result=None,
                confirmer_called=True,
                should_raise=GitHubOwnerConfirmationAbortedError,
            ),
            id="inferred-owner-declined",
        ),
    ],
)
def test_resolve_and_confirm_owner_behavior(
    scenario: OwnerResolutionScenario,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Validate owner resolution with explicit and inferred values.

    Scenarios:
    - Explicit `github_owner` bypasses the confirmer.
    - Inferred owners prompt for confirmation and return the slug owner when
      accepted.
    - Declining the inferred owner raises GitHubOwnerConfirmationAbortedError.
    """
    confirm_bool = scenario.confirm_response == "yes"
    confirmer = mocker.Mock(return_value=confirm_bool)

    if scenario.should_raise is not None:
        with pytest.raises(scenario.should_raise):
            _resolve_and_confirm_owner(scenario.slug, scenario.github_owner, confirmer)
    else:
        resolved = _resolve_and_confirm_owner(
            scenario.slug,
            scenario.github_owner,
            confirmer,
        )
        assert resolved == scenario.expected_result

    if scenario.confirmer_called:
        confirmer.assert_called_once()
    else:
        confirmer.assert_not_called()


class TestRepositoryPlanHelpers:
    """State distinctions behind `_prepare_repository`'s remote-state dispatch.

    The public `init_estate` flow already covers each outcome; these pin the
    contracts the dispatcher relies on — chiefly which paths may build a
    GitHub client, and the slug guards that precede any authentication.
    """

    REPO_URL = "git@github.com:example/core.git"

    def test_missing_remote_plan_never_builds_a_client(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """A confirmed-absent remote is planned without contacting GitHub."""
        build_client = mocker.patch.object(estate_repository, "_build_client")

        plan = estate_repository._plan_missing_repository("example/core")

        assert plan.needs_creation is True, plan
        assert (plan.owner, plan.name) == ("example", "core"), plan
        assert plan.client is None, "a missing remote needs no client, got a client"
        build_client.assert_not_called()

    def test_missing_remote_plan_requires_a_slug(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """Without a slug there is nothing to create, and no client is built."""
        build_client = mocker.patch.object(estate_repository, "_build_client")

        with pytest.raises(estate.UnsupportedRepositoryCreationError):
            estate_repository._plan_missing_repository(None)

        build_client.assert_not_called()

    def test_unreachable_remote_plan_preserves_the_built_client(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """The client built to query GitHub is carried into the plan."""
        fake_client = mocker.Mock()
        fake_client.repository.return_value = None
        build_client = mocker.patch.object(
            estate_repository, "_build_client", return_value=fake_client
        )

        plan = estate_repository._plan_unreachable_repository(
            self.REPO_URL,
            "example/core",
            "token",
            None,
        )

        build_client.assert_called_once_with("token", None)
        assert plan.needs_creation is True, plan
        assert (plan.owner, plan.name) == ("example", "core"), plan
        assert plan.client is fake_client, (
            "the plan should reuse the client built for the GitHub lookup"
        )

    def test_unreachable_remote_plan_requires_a_slug(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """An unreachable remote without a slug fails before authenticating."""
        build_client = mocker.patch.object(estate_repository, "_build_client")

        with pytest.raises(estate.RepositoryUnreachableError):
            estate_repository._plan_unreachable_repository(
                self.REPO_URL, None, "token", None
            )

        build_client.assert_not_called()


class TestEnsureRepositoryExists:
    """Step ordering of the provisioning flow.

    The order is load-bearing: identity is required before a client is built,
    the operator is prompted before the owner/name pair is validated, and
    nothing is created until the prompt is accepted.
    """

    @staticmethod
    def _plan(
        *,
        slug: str | None = "example/core",
        owner: str | None = "example",
        name: str | None = "core",
        client: github3.GitHub | None = None,
    ) -> estate_repository.RepositoryPlan:
        return estate_repository.RepositoryPlan(
            needs_creation=True,
            slug=slug,
            owner=owner,
            name=name,
            client=client,
        )

    @staticmethod
    def _ensure(
        plan: estate_repository.RepositoryPlan,
        confirmer: typ.Callable[[str], bool],
    ) -> None:
        estate_repository._ensure_repository_exists(
            plan,
            "token",
            None,
            confirmer,
        )

    def test_missing_slug_does_not_build_a_client(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """Without a slug the flow fails before authenticating or prompting."""
        build_client = mocker.patch.object(estate_repository, "_build_client")
        create = mocker.patch.object(estate_repository, "_create_repository")
        confirmer = mocker.Mock(return_value=True)

        with pytest.raises(estate.RepositorySlugUnknownError):
            self._ensure(self._plan(slug=None), confirmer)

        build_client.assert_not_called()
        confirmer.assert_not_called()
        create.assert_not_called()

    def test_declined_confirmation_creates_nothing(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """A declined prompt aborts before any repository is created."""
        create = mocker.patch.object(estate_repository, "_create_repository")
        confirmer = mocker.Mock(return_value=False)

        with pytest.raises(estate.EstateCreationAbortedError):
            self._ensure(self._plan(client=mocker.Mock()), confirmer)

        confirmer.assert_called_once_with(
            "Create GitHub repository example/core? [y/N]: "
        )
        create.assert_not_called()

    def test_prepared_client_is_reused_for_creation(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """A plan carrying a client reuses it instead of authenticating again."""
        build_client = mocker.patch.object(estate_repository, "_build_client")
        create = mocker.patch.object(estate_repository, "_create_repository")
        prepared = mocker.Mock()

        self._ensure(self._plan(client=prepared), lambda _prompt: True)

        build_client.assert_not_called()
        create.assert_called_once_with(prepared, "example", "core")

    @pytest.mark.parametrize(
        ("owner", "name"),
        [
            pytest.param(None, "core", id="missing-owner"),
            pytest.param("example", None, id="missing-name"),
        ],
    )
    def test_incomplete_identity_is_rejected_after_confirmation(
        self,
        mocker: pytest_mock.MockFixture,
        owner: str | None,
        name: str | None,
    ) -> None:
        """The owner/name pair is validated only once the prompt is accepted."""
        create = mocker.patch.object(estate_repository, "_create_repository")
        confirmer = mocker.Mock(return_value=True)

        with pytest.raises(estate.RepositoryIdentityError):
            self._ensure(
                self._plan(owner=owner, name=name, client=mocker.Mock()),
                confirmer,
            )

        confirmer.assert_called_once()
        create.assert_not_called()


class TestEstateRepositoryReexport:
    """Repository-lifecycle types stay importable from the façade."""

    def test_remote_probe_is_the_same_class(self) -> None:
        """``estate.RemoteProbe`` is the class defined in ``estate_git``.

        The façade re-exports through ``estate_repository``, which imports the
        class directly, so all three names must be one object.
        """
        from concordat import estate_git

        assert estate.RemoteProbe is estate_repository.RemoteProbe, (
            "estate.RemoteProbe should be estate_repository.RemoteProbe"
        )
        assert estate_repository.RemoteProbe is estate_git.RemoteProbe, (
            "estate_repository.RemoteProbe should be estate_git.RemoteProbe"
        )

    def test_moved_helpers_are_imported_aliases(self) -> None:
        """The patch seams alias the implementations, so monkeypatches bite.

        Tests patch ``estate_repository`` attributes; those must be the very
        objects ``estate_git``/``estate_github`` define, not wrappers.
        """
        from concordat import estate_git, estate_github

        for name, module in (
            ("_probe_remote", estate_git),
            ("_bootstrap_template", estate_git),
            ("_collect_inventory", estate_git),
            ("default_template_root", estate_git),
            ("TemplateBootstrap", estate_git),
            ("_build_client", estate_github),
            ("_create_repository", estate_github),
        ):
            assert getattr(estate_repository, name) is getattr(module, name), (
                f"estate_repository.{name} should be {module.__name__}.{name}"
            )
