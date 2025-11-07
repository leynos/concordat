"""Thin GitHub REST client tailored for Auditor needs."""

from __future__ import annotations

import typing as typ

import requests

from .models import (
    BranchProtection,
    CollaboratorPermission,
    LabelState,
    RepositorySnapshot,
    RequiredPullRequestReviews,
    RequiredStatusChecks,
    TeamPermission,
)

DEFAULT_API_URL = "https://api.github.com"


class GithubError(RuntimeError):
    """Raised when the GitHub API returns a non-successful response."""


class GithubNotFoundError(GithubError):
    """Raised when the GitHub API returns a 404 for an optional resource."""


class GithubClient:
    """Minimal GitHub client using the REST API."""

    def __init__(
        self,
        *,
        token: str,
        api_url: str = DEFAULT_API_URL,
        timeout: int = 30,
    ) -> None:
        """Configure a GitHub session scoped to the provided token."""
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "concordat-auditor",
            }
        )

    def repository(self, owner: str, name: str) -> RepositorySnapshot:
        """Return repository metadata used by the repository checks."""
        data = self._get_json("GET", f"/repos/{owner}/{name}")
        return RepositorySnapshot(
            owner=data["owner"]["login"],
            name=data["name"],
            default_branch=data["default_branch"],
            allow_squash_merge=bool(data.get("allow_squash_merge", False)),
            allow_merge_commit=bool(data.get("allow_merge_commit", False)),
            allow_rebase_merge=bool(data.get("allow_rebase_merge", False)),
            allow_auto_merge=bool(data.get("allow_auto_merge", False)),
            delete_branch_on_merge=bool(data.get("delete_branch_on_merge", False)),
        )

    def branch_protection(
        self, owner: str, name: str, branch: str
    ) -> BranchProtection | None:
        """Fetch branch protection settings for the default branch."""
        try:
            data = self._get_json(
                "GET", f"/repos/{owner}/{name}/branches/{branch}/protection"
            )
        except GithubNotFoundError:
            return None
        status_checks = data.get("required_status_checks")
        reviews = data.get("required_pull_request_reviews")
        return BranchProtection(
            enforce_admins=bool(data.get("enforce_admins", {}).get("enabled", False)),
            require_signed_commits=data.get("required_signatures", {}).get(
                "enabled", None
            ),
            required_linear_history=bool(
                data.get("required_linear_history", {}).get("enabled", False)
            ),
            require_conversation_resolution=bool(
                data.get("required_conversation_resolution", {}).get("enabled", False)
            ),
            allows_deletions=bool(
                data.get("allow_deletions", {}).get("enabled", False)
            ),
            allows_force_pushes=bool(
                data.get("allow_force_pushes", {}).get("enabled", False)
            ),
            status_checks=self._parse_status_checks(status_checks),
            pull_request_reviews=self._parse_pull_request_reviews(reviews),
        )

    def teams(self, owner: str, name: str) -> tuple[TeamPermission, ...]:
        """List team permission assignments for the repository."""
        path = f"/repos/{owner}/{name}/teams"
        entries = self._paginate(path, params={"per_page": 100})
        return tuple(
            TeamPermission(slug=entry["slug"], permission=entry["permission"])
            for entry in entries
        )

    def outside_collaborators(
        self, owner: str, name: str
    ) -> tuple[CollaboratorPermission, ...]:
        """Return outside collaborators with their effective permissions."""
        path = f"/repos/{owner}/{name}/collaborators"
        entries = self._paginate(
            path, params={"per_page": 100, "affiliation": "outside"}
        )
        return tuple(
            CollaboratorPermission(
                login=entry["login"],
                permission=str(entry.get("permission", "")),
                permissions={
                    key: bool(value)
                    for key, value in entry.get("permissions", {}).items()
                    if isinstance(value, bool)
                },
            )
            for entry in entries
        )

    def labels(self, owner: str, name: str) -> tuple[LabelState, ...]:
        """Return all labels defined in the repository."""
        path = f"/repos/{owner}/{name}/labels"
        entries = self._paginate(path, params={"per_page": 100})
        return tuple(
            LabelState(
                name=entry["name"],
                color=str(entry.get("color", "")).lower(),
                description=str(entry.get("description", "")).strip(),
            )
            for entry in entries
        )

    # Internal helpers -------------------------------------------------

    def _get_json(self, method: str, path: str) -> dict[str, typ.Any]:
        response = self._request(method, path)
        return response.json()

    def _request(self, method: str, path: str) -> requests.Response:
        url = f"{self.api_url}{path}"
        response = self.session.request(method, url, timeout=self.timeout)
        if response.status_code == 404:
            message = f"{method} {path} returned 404."
            raise GithubNotFoundError(message)
        if response.status_code >= 400:
            detail = response.text[:400]
            message = f"{method} {path} failed: {response.status_code} {detail}"
            raise GithubError(message)
        return response

    def _paginate(
        self, path: str, *, params: dict[str, typ.Any] | None = None
    ) -> typ.Iterable[dict[str, typ.Any]]:
        url = f"{self.api_url}{path}"
        next_url: str | None = url
        next_params = params
        while next_url:
            response = self.session.get(
                next_url, params=next_params, timeout=self.timeout
            )
            if response.status_code >= 400:
                detail = response.text[:400]
                message = f"GET {next_url} failed: {response.status_code} {detail}"
                raise GithubError(message)
            yield from response.json()
            next_url = response.links.get("next", {}).get("url")
            next_params = None

    @staticmethod
    def _parse_status_checks(
        payload: dict[str, typ.Any] | None,
    ) -> RequiredStatusChecks | None:
        if not payload:
            return None
        contexts = payload.get("contexts") or []
        return RequiredStatusChecks(
            strict=bool(payload.get("strict", False)),
            contexts=tuple(str(ctx) for ctx in contexts),
        )

    @staticmethod
    def _parse_pull_request_reviews(
        payload: dict[str, typ.Any] | None,
    ) -> RequiredPullRequestReviews | None:
        if not payload:
            return None
        return RequiredPullRequestReviews(
            required_approvals=int(payload.get("required_approving_review_count", 0)),
            dismiss_stale_reviews=bool(payload.get("dismiss_stale_reviews", False)),
            require_code_owner_reviews=bool(
                payload.get("require_code_owner_reviews", False)
            ),
        )
