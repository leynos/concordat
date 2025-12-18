"""Utilities for enrolling Git repositories with concordat."""

from __future__ import annotations

import dataclasses
import typing as typ
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pygit2
from pygit2 import RemoteCallbacks, Repository, Signature
from ruamel.yaml import YAML

from .errors import ConcordatError
from .gitutils import build_remote_callbacks
from .platform_standards import (
    PlatformStandardsConfig,
    PlatformStandardsResult,
    ensure_repository_pr,
    ensure_repository_removal_pr,
    parse_github_slug,
)

CONCORDAT_FILENAME = ".concordat"
CONCORDAT_DOCUMENT = {"enrolled": True}
COMMIT_MESSAGE = "chore: enrol repository with concordat"
DISENROL_COMMIT_MESSAGE = "chore: disenrol repository with concordat"
ERROR_NO_REPOSITORIES = "At least one repository must be provided."
ERROR_UNBORN_HEAD = "Enrolment requires a repository with at least one commit."
ERROR_UNKNOWN_BRANCH = "Cannot determine current branch."
ERROR_MISSING_ORIGIN = "Repository missing 'origin' remote."


def _no_repositories_error() -> ConcordatError:
    return ConcordatError(ERROR_NO_REPOSITORIES)


def _remote_clone_failed_error(specification: str, error: Exception) -> ConcordatError:
    detail = f"Failed to clone {specification!r}: {error}"
    return ConcordatError(detail)


def _remote_clone_bare_error(specification: str) -> ConcordatError:
    detail = f"Remote clone for {specification!r} is bare."
    return ConcordatError(detail)


def _repository_bare_error(specification: str) -> ConcordatError:
    detail = f"Repository {specification!r} is bare."
    return ConcordatError(detail)


def _repository_not_found_error(specification: str) -> ConcordatError:
    detail = f"Repository {specification!r} not found."
    return ConcordatError(detail)


def _open_repository_error(specification: str, error: Exception) -> ConcordatError:
    detail = f"Cannot open repository {specification!r}: {error}"
    return ConcordatError(detail)


def _unknown_branch_error() -> ConcordatError:
    return ConcordatError(ERROR_UNKNOWN_BRANCH)


def _missing_origin_error() -> ConcordatError:
    return ConcordatError(ERROR_MISSING_ORIGIN)


def _push_failed_error(error: Exception) -> ConcordatError:
    detail = f"Failed to push changes: {error}"
    return ConcordatError(detail)


def _read_error(path: Path, error: Exception) -> ConcordatError:
    detail = f"Cannot read {path}: {error}"
    return ConcordatError(detail)


def _unborn_head_error() -> ConcordatError:
    return ConcordatError(ERROR_UNBORN_HEAD)


def _missing_document_error(specification: str) -> ConcordatError:
    detail = f"Repository {specification!r} does not contain a concordat document."
    return ConcordatError(detail)


def _invalid_document_error(specification: str) -> ConcordatError:
    detail = f"Repository {specification!r} has an invalid concordat document."
    return ConcordatError(detail)


def _owner_slug_missing_error(specification: str) -> ConcordatError:
    detail = (
        f"Unable to determine the GitHub slug for {specification!r}. "
        "Set the repository's origin remote to a GitHub SSH/HTTPS URL or "
        "pass the SSH URL directly so concordat can enforce github_owner."
    )
    return ConcordatError(detail)


def _owner_mismatch_error(
    specification: str,
    slug: str,
    github_owner: str,
) -> ConcordatError:
    detail = (
        f"Repository {slug!r} does not belong to github_owner {github_owner!r}. "
        "Use `concordat estate use` to switch estates or enrol a repository "
        "under the configured owner."
    )
    return ConcordatError(detail)


def _render_platform_pr_result(result: PlatformStandardsResult) -> str:
    """Render the platform inventory PR outcome.

    The active estate inventory (and therefore `concordat estate show`) reflects
    the estate repository default branch. When concordat opens/updates a feature
    branch PR in platform-standards, the repository will not appear in the
    estate inventory until the PR is merged.
    """
    if result.created:
        message = "platform PR opened"
        if result.pr_url:
            message = f"{message}: {result.pr_url}"
        return f"{message} (merge to update estate inventory)"
    return f"platform PR skipped: {result.message}"


def _build_status_parts(
    base_message: str,
    *,
    committed: bool = False,
    pushed: bool = False,
    platform_pr: PlatformStandardsResult | None = None,
) -> list[str]:
    """Build list of status message parts from base message and optional flags.

    Args:
        base_message: The initial status message fragment.
        committed: Whether to append "committed" to the parts.
        pushed: Whether to append "pushed" to the parts.
        platform_pr: Optional platform PR result to append.

    Returns:
        List of status message fragments ready for joining.

    """
    parts = [base_message]
    if committed:
        parts.append("committed")
    if pushed:
        parts.append("pushed")
    if platform_pr:
        parts.append(_render_platform_pr_result(platform_pr))
    return parts


def _format_outcome(repository: str, status_parts: list[str]) -> str:
    """Format repository and status parts into a final outcome message.

    Args:
        repository: The repository specification.
        status_parts: List of status message fragments.

    Returns:
        Formatted message: "{repository}: {joined parts}"

    """
    status = ", ".join(status_parts)
    return f"{repository}: {status}"


@dataclasses.dataclass(frozen=True)
class EnrollmentOutcome:
    """Captured outcome for a processed repository."""

    repository: str
    location: Path
    created: bool
    committed: bool
    pushed: bool
    platform_pr: PlatformStandardsResult | None = None

    def render(self) -> str:
        """Return a concise human readable summary."""
        base_message = "already enrolled" if not self.created else "created .concordat"
        status_parts = _build_status_parts(
            base_message,
            committed=self.committed if self.created else False,
            pushed=self.pushed if self.created else False,
            platform_pr=self.platform_pr,
        )
        return _format_outcome(self.repository, status_parts)


@dataclasses.dataclass(frozen=True)
class DisenrollmentOutcome:
    """Captured outcome for a processed repository during disenrolment."""

    repository: str
    location: Path
    updated: bool
    missing_document: bool
    committed: bool
    pushed: bool
    platform_pr: PlatformStandardsResult | None = None

    def render(self) -> str:
        """Return a concise human readable summary."""
        if self.missing_document:
            base_message = "missing .concordat"
            status_parts = _build_status_parts(
                base_message,
                platform_pr=self.platform_pr,
            )
        elif not self.updated:
            base_message = "already disenrolled"
            status_parts = _build_status_parts(
                base_message,
                platform_pr=self.platform_pr,
            )
        else:
            base_message = "updated .concordat"
            status_parts = _build_status_parts(
                base_message,
                committed=self.committed,
                pushed=self.pushed,
                platform_pr=self.platform_pr,
            )
        return _format_outcome(self.repository, status_parts)


_yaml = YAML(typ="safe")
_yaml.version = (1, 2)
_yaml.default_flow_style = False


def enrol_repositories(
    repositories: typ.Sequence[str],
    *,
    push_remote: bool = False,
    author_name: str | None = None,
    author_email: str | None = None,
    platform_standards: PlatformStandardsConfig | None = None,
    github_owner: str | None = None,
    force: bool = False,
) -> list[EnrollmentOutcome]:
    """Enrol each repository and return the captured outcomes."""
    if not repositories:
        raise _no_repositories_error()

    outcomes: list[EnrollmentOutcome] = []
    for specification in repositories:
        outcome = _enrol_repository(
            specification,
            push_remote=push_remote,
            author_name=author_name,
            author_email=author_email,
            platform_standards=platform_standards,
            github_owner=github_owner,
            force=force,
        )
        outcomes.append(outcome)
    return outcomes


def disenrol_repositories(
    repositories: typ.Sequence[str],
    *,
    push_remote: bool = False,
    author_name: str | None = None,
    author_email: str | None = None,
    platform_standards: PlatformStandardsConfig | None = None,
    github_owner: str | None = None,
    allow_missing_document: bool = False,
) -> list[DisenrollmentOutcome]:
    """Disenrol each repository and return the captured outcomes."""
    if not repositories:
        raise _no_repositories_error()

    outcomes: list[DisenrollmentOutcome] = []
    for specification in repositories:
        outcome = _disenrol_repository(
            specification,
            push_remote=push_remote,
            author_name=author_name,
            author_email=author_email,
            platform_standards=platform_standards,
            github_owner=github_owner,
            allow_missing_document=allow_missing_document,
        )
        outcomes.append(outcome)
    return outcomes


def _disenrol_repository(
    specification: str,
    *,
    push_remote: bool,
    author_name: str | None,
    author_email: str | None,
    platform_standards: PlatformStandardsConfig | None,
    github_owner: str | None,
    allow_missing_document: bool,
) -> DisenrollmentOutcome:
    with _repository_context(specification) as context:
        repo_slug = _slug_with_owner_guard(
            _repository_slug(context.repository, specification),
            github_owner,
            specification,
        )
        concordat_path = context.location / CONCORDAT_FILENAME
        missing_document = False
        updated = False
        if concordat_path.exists():
            updated = _set_enrolled_value(
                context.location,
                value=False,
                specification=specification,
            )
        else:
            if not allow_missing_document:
                raise _missing_document_error(specification)
            missing_document = True

        if not updated:
            return DisenrollmentOutcome(
                repository=specification,
                location=context.location,
                updated=False,
                missing_document=missing_document,
                committed=False,
                pushed=False,
                platform_pr=_platform_pr_removal_result(repo_slug, platform_standards),
            )

        _stage_document(context.repository)
        commit_oid = _commit_disenrol_document(
            context.repository,
            author_name=author_name,
            author_email=author_email,
        )

        should_push = context.is_remote or push_remote
        pushed = False
        if should_push:
            _push_document(context.repository, context.callbacks)
            pushed = True

        return DisenrollmentOutcome(
            repository=specification,
            location=context.location,
            updated=True,
            missing_document=False,
            committed=commit_oid is not None,
            pushed=pushed,
            platform_pr=_platform_pr_removal_result(repo_slug, platform_standards),
        )


def _enrol_repository(
    specification: str,
    *,
    push_remote: bool,
    author_name: str | None,
    author_email: str | None,
    platform_standards: PlatformStandardsConfig | None,
    github_owner: str | None,
    force: bool,
) -> EnrollmentOutcome:
    with _repository_context(specification) as context:
        repo_slug = _slug_with_owner_guard(
            _repository_slug(context.repository, specification),
            github_owner,
            specification,
        )
        created = _ensure_concordat_document(context.location)
        if not created:
            platform_result = None
            if force:
                platform_result = _platform_pr_result(repo_slug, platform_standards)
            return EnrollmentOutcome(
                repository=specification,
                location=context.location,
                created=False,
                committed=False,
                pushed=False,
                platform_pr=platform_result,
            )

        _stage_document(context.repository)
        commit_oid = _commit_document(
            context.repository,
            author_name=author_name,
            author_email=author_email,
        )

        should_push = context.is_remote or push_remote
        pushed = False
        if should_push:
            _push_document(context.repository, context.callbacks)
            pushed = True

        platform_result = _platform_pr_result(
            repo_slug,
            platform_standards,
        )

        return EnrollmentOutcome(
            repository=specification,
            location=context.location,
            created=True,
            committed=commit_oid is not None,
            pushed=pushed,
            platform_pr=platform_result,
        )


def _slug_with_owner_guard(
    slug: str | None,
    github_owner: str | None,
    specification: str,
) -> str | None:
    """Isolate github_owner enforcement so enrolment stays linear."""
    if github_owner is None:
        return slug
    return _require_allowed_owner(slug, github_owner, specification)


def _execute_platform_pr_operation(
    repo_slug: str | None,
    platform_standards: PlatformStandardsConfig | None,
    operation: typ.Callable[[str, PlatformStandardsConfig], PlatformStandardsResult],
) -> PlatformStandardsResult | None:
    """Execute a platform PR operation with common validation and error handling.

    Args:
        repo_slug: GitHub repository slug (owner/repo) or None.
        platform_standards: Platform configuration or None.
        operation: The platform operation to execute (e.g., ensure_repository_pr).

    Returns:
        PlatformStandardsResult if operation was attempted, None if config missing.

    """
    if platform_standards is None:
        return None
    if not repo_slug:
        return PlatformStandardsResult(
            created=False,
            branch=None,
            pr_url=None,
            message="unable to determine GitHub slug",
        )
    try:
        return operation(repo_slug, platform_standards)
    except ConcordatError as error:
        return PlatformStandardsResult(
            created=False,
            branch=None,
            pr_url=None,
            message=str(error),
        )


def _platform_pr_result(
    repo_slug: str | None,
    platform_standards: PlatformStandardsConfig | None,
) -> PlatformStandardsResult | None:
    """Encapsulate platform PR creation to keep enrolment orchestration clear."""
    return _execute_platform_pr_operation(
        repo_slug,
        platform_standards,
        lambda slug, config: ensure_repository_pr(slug, config=config),
    )


def _platform_pr_removal_result(
    repo_slug: str | None,
    platform_standards: PlatformStandardsConfig | None,
) -> PlatformStandardsResult | None:
    """Encapsulate platform PR removal to keep disenrolment orchestration clear."""
    return _execute_platform_pr_operation(
        repo_slug,
        platform_standards,
        lambda slug, config: ensure_repository_removal_pr(slug, config=config),
    )


@dataclasses.dataclass
class _RepositoryContext:
    repository: Repository
    location: Path
    is_remote: bool
    callbacks: RemoteCallbacks | None


@contextmanager
def _repository_context(
    specification: str,
) -> typ.Iterator[_RepositoryContext]:
    if _looks_like_remote(specification):
        callbacks = build_remote_callbacks(specification)
        with TemporaryDirectory(prefix="concordat-clone-") as temp_root:
            target = Path(temp_root, "repo")
            try:
                repository = pygit2.clone_repository(
                    url=specification,
                    path=str(target),
                    callbacks=callbacks,
                )
            except pygit2.GitError as error:
                raise _remote_clone_failed_error(specification, error) from error

            workdir = repository.workdir
            if workdir is None:
                raise _remote_clone_bare_error(specification)

            yield _RepositoryContext(
                repository=repository,
                location=Path(workdir),
                is_remote=True,
                callbacks=callbacks,
            )
        return

    repository = _open_local_repository(specification)
    workdir = repository.workdir
    if workdir is None:
        raise _repository_bare_error(specification)

    yield _RepositoryContext(
        repository=repository,
        location=Path(workdir),
        is_remote=False,
        callbacks=None,
    )


def _open_local_repository(specification: str) -> Repository:
    path = Path(specification).expanduser()
    try:
        resolved = pygit2.discover_repository(str(path))
    except KeyError as error:
        raise _repository_not_found_error(specification) from error

    if resolved is None:
        raise _repository_not_found_error(specification)

    try:
        return pygit2.Repository(resolved)
    except pygit2.GitError as error:
        raise _open_repository_error(specification, error) from error


def _stage_document(repository: Repository) -> None:
    index = repository.index
    index.add(CONCORDAT_FILENAME)
    index.write()


def _commit_document(
    repository: Repository,
    *,
    author_name: str | None,
    author_email: str | None,
) -> pygit2.Oid | None:
    return _commit_with_message(
        repository,
        author_name=author_name,
        author_email=author_email,
        message=COMMIT_MESSAGE,
    )


def _commit_disenrol_document(
    repository: Repository,
    *,
    author_name: str | None,
    author_email: str | None,
) -> pygit2.Oid | None:
    return _commit_with_message(
        repository,
        author_name=author_name,
        author_email=author_email,
        message=DISENROL_COMMIT_MESSAGE,
    )


def _commit_with_message(
    repository: Repository,
    *,
    author_name: str | None,
    author_email: str | None,
    message: str,
) -> pygit2.Oid | None:
    if repository.head_is_unborn:
        raise _unborn_head_error()

    index = repository.index
    tree_oid = index.write_tree()
    parents = [repository.head.target]

    signature = _signature(repository, author_name, author_email)
    return repository.create_commit(
        "HEAD",
        signature,
        signature,
        message,
        tree_oid,
        parents,
    )


def _push_document(repository: Repository, callbacks: RemoteCallbacks | None) -> None:
    branch = repository.head.shorthand
    if not branch:
        raise _unknown_branch_error()

    try:
        remote = repository.remotes["origin"]
    except KeyError as error:
        raise _missing_origin_error() from error

    refspec = f"refs/heads/{branch}:refs/heads/{branch}"
    try:
        remote.push([refspec], callbacks=callbacks)
    except pygit2.GitError as error:
        raise _push_failed_error(error) from error


def _write_document(destination: Path, document: dict[str, object]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        _yaml.dump(document, handle)


def _ensure_concordat_document(location: Path) -> bool:
    destination = location / CONCORDAT_FILENAME
    if destination.exists():
        existing = _load_yaml(destination)
        if isinstance(existing, dict):
            mapping = typ.cast("dict[str, object]", existing)
            enrolled_value = mapping.get("enrolled")
            if isinstance(enrolled_value, bool) and enrolled_value is True:
                return False

    _write_document(destination, dict(CONCORDAT_DOCUMENT))
    return True


def _set_enrolled_value(location: Path, *, value: bool, specification: str) -> bool:
    destination = location / CONCORDAT_FILENAME
    if not destination.exists():
        raise _missing_document_error(specification)

    existing = _load_yaml(destination)
    if not isinstance(existing, dict):
        raise _invalid_document_error(specification)

    mapping = typ.cast("dict[str, object]", existing)
    current = mapping.get("enrolled")
    if not isinstance(current, bool):
        raise _invalid_document_error(specification)

    if current is value:
        return False

    updated = dict(mapping)
    updated["enrolled"] = value
    _write_document(destination, updated)
    return True


def _repository_slug(repository: Repository, specification: str) -> str | None:
    try:
        origin = repository.remotes["origin"]
    except KeyError:
        origin = None
    if origin is not None and origin.url:
        slug = parse_github_slug(origin.url)
        if slug:
            return slug
    return parse_github_slug(specification)


def _require_allowed_owner(
    slug: str | None,
    github_owner: str,
    specification: str,
) -> str:
    if not slug:
        raise _owner_slug_missing_error(specification)
    _guard_slug_format(slug, specification)
    expected_owner = github_owner.strip()
    if not expected_owner:
        detail = (
            f"github_owner is empty or whitespace for {specification!r}. "
            "Update the estate configuration with a non-empty owner before "
            "enrolling repositories."
        )
        raise ConcordatError(detail)
    repo_owner, _, _ = slug.partition("/")
    if repo_owner.lower() != expected_owner.lower():
        raise _owner_mismatch_error(specification, slug, expected_owner)
    return slug


def _guard_slug_format(slug: str, specification: str) -> None:
    if "/" in slug:
        return
    detail = (
        f"Malformed GitHub slug {slug!r} for {specification!r}. "
        "Expected format: 'owner/repo'."
    )
    raise ConcordatError(detail)


def _load_yaml(path: Path) -> object:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return _yaml.load(handle) or {}
    except OSError as error:
        raise _read_error(path, error) from error


def _signature(
    repository: Repository,
    author_name: str | None,
    author_email: str | None,
) -> Signature:
    if author_name and author_email:
        return Signature(author_name, author_email)
    try:
        return repository.default_signature
    except KeyError:
        # Fall back to a deterministic identity so commits still succeed in CI.
        return Signature("concordat", "concordat@local")


def _looks_like_remote(specification: str) -> bool:
    return specification.startswith("git@") or specification.startswith("ssh://")
