"""GitHub REST client for the three calls the agent needs."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr, ValidationError

from ghagent.errors import GitHubError
from ghagent.models import Issue
from ghagent.providers.http import JsonClient


class GitHubClient:
    """Reads issues and opens pull requests. It cannot merge, close or delete anything."""

    def __init__(
        self,
        client: JsonClient,
        *,
        token: SecretStr | None,
        api_url: str = "https://api.github.com",
    ) -> None:
        self._client = client
        self._token = token
        self._api = api_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        return headers

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        return self._client.request(
            method, f"{self._api}{path}", json=body, headers=self._headers()
        )

    def get_issue(self, repo: str, number: int) -> Issue:
        """Fetch an issue. Pull requests are rejected because they are not issues to work on."""
        data = self._call("GET", f"/repos/{repo}/issues/{number}")
        if not isinstance(data, dict):
            raise GitHubError("unexpected issue response")
        if "pull_request" in data:
            raise GitHubError(f"#{number} is a pull request, not an issue")
        try:
            return Issue(
                repo=repo,
                number=number,
                title=str(data.get("title", ""))[:300],
                body=str(data.get("body") or "")[:20_000],
                labels=[
                    str(item.get("name", ""))
                    for item in data.get("labels", [])
                    if isinstance(item, dict)
                ],
                author=str((data.get("user") or {}).get("login", "")),
                url=str(data.get("html_url", "")),
            )
        except ValidationError as exc:
            raise GitHubError(f"issue response failed validation: {exc}") from exc

    def find_open_pull_request(self, repo: str, head: str) -> str | None:
        """Return the URL of an open PR from ``head`` (``owner:branch``) or ``None``."""
        data = self._call("GET", f"/repos/{repo}/pulls?state=open&head={head}")
        if not isinstance(data, list):
            raise GitHubError("unexpected pull request listing")
        return str(data[0].get("html_url", "")) if data else None

    def create_pull_request(
        self, repo: str, *, title: str, body: str, head: str, base: str, draft: bool
    ) -> str:
        """Open a pull request and return its URL."""
        data = self._call(
            "POST",
            f"/repos/{repo}/pulls",
            {"title": title, "body": body, "head": head, "base": base, "draft": draft},
        )
        url = data.get("html_url") if isinstance(data, dict) else None
        if not url:
            raise GitHubError("pull request response had no html_url")
        return str(url)
