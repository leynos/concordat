"""A local HTTP double of the GitHub REST API for command-level tests.

The rule-run command reaches GitHub through `requests`, so the honest seam for
an end-to-end test is the HTTP boundary itself: a real server on a loopback
port that answers from a route table and records every path it was asked
for. Nothing in the code under test is patched; the command is pointed at the
server with `--github-api-url`.
"""

from __future__ import annotations

import dataclasses
import http.server
import json
import threading
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@dataclasses.dataclass(frozen=True, slots=True)
class Reply:
    """One canned answer: an HTTP status and an optional JSON body."""

    status: int
    body: object = None


@dataclasses.dataclass(slots=True)
class FakeGithubApi:
    """Routes keyed by request path, and the paths requested so far."""

    routes: dict[str, Reply] = dataclasses.field(default_factory=dict)
    requested: list[str] = dataclasses.field(default_factory=list)
    url: str = ""

    def answer(self, path: str) -> Reply:
        """Record *path* and return its route, or a 404 for an unknown one."""
        self.requested.append(path)
        return self.routes.get(path, Reply(404, {"message": "Not Found"}))


def _handler(api: FakeGithubApi) -> type[http.server.BaseHTTPRequestHandler]:
    """Return a request handler class that answers from *api*."""

    class Handler(http.server.BaseHTTPRequestHandler):
        """Serve GETs from the fake's route table."""

        def do_GET(self) -> None:
            """Write the routed status and JSON body."""
            reply = api.answer(self.path)
            payload = b"" if reply.body is None else json.dumps(reply.body).encode()
            self.send_response(reply.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the base signature
            """Keep the test output quiet."""

    return Handler


def serve(api: FakeGithubApi) -> cabc.Iterator[FakeGithubApi]:
    """Serve *api* on a loopback port until the generator is closed.

    Yields
    ------
    FakeGithubApi
        *api*, with `url` set to the server's root.
    """
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(api))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    api.url = f"http://{host}:{port}"
    try:
        yield api
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def git_object_routes(
    repository: str,
    commits: cabc.Iterable[str] = (),
    tags: cabc.Mapping[str, str] | None = None,
) -> dict[str, Reply]:
    """Return routes under which *commits* are commits and *tags* are tag objects.

    *tags* maps each annotated tag object's SHA to the commit it points at.

    Returns
    -------
    dict[str, Reply]
        Routes for the commit and tag endpoints, keyed by request path.
    """
    routes = {
        f"/repos/{repository}/git/commits/{sha}": Reply(200, {"sha": sha})
        for sha in commits
    }
    for sha, target in (tags or {}).items():
        body = {"sha": sha, "object": {"type": "commit", "sha": target}}
        routes[f"/repos/{repository}/git/tags/{sha}"] = Reply(200, body)
    return routes
