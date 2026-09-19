"""Unit tests for provider clients, HTTP handling and the service's issue fetching."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from ghagent.container import build_service
from ghagent.errors import ConfigurationError, GitHubError, ProviderError, TransientProviderError
from ghagent.providers import AnthropicChatClient, OpenAIChatClient
from ghagent.retry import call_with_retry
from tests.conftest import json_client, make_issue, make_settings

KEY = SecretStr("sk-test-key-000000000000000000")


class TestHttp:
    def test_retries_transient_then_succeeds(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503) if calls["n"] < 2 else httpx.Response(200, json={"ok": 1})

        assert json_client(handler).request("GET", "http://x") == {"ok": 1}
        assert calls["n"] == 2

    def test_error_mapping(self) -> None:
        with pytest.raises(TransientProviderError):
            json_client(lambda r: httpx.Response(429)).request("GET", "http://x")
        with pytest.raises(ProviderError, match="non-JSON"):
            json_client(lambda r: httpx.Response(200, text="<html>")).request("GET", "http://x")

        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        with pytest.raises(TransientProviderError, match="transport"):
            json_client(boom).request("GET", "http://x")

    def test_retry_returns_value(self) -> None:
        assert call_with_retry(lambda: 3, attempts=2, min_wait=0, max_wait=0) == 3


class TestChatClients:
    def test_openai(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == f"Bearer {KEY.get_secret_value()}"
            body = json.loads(request.content)
            assert body["temperature"] == 0 and body["messages"][0]["role"] == "system"
            return httpx.Response(200, json={"choices": [{"message": {"content": " hi "}}]})

        assert (
            OpenAIChatClient(json_client(handler), api_key=KEY, model="m").complete("s", "u")
            == "hi"
        )

    def test_anthropic(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-api-key"] == KEY.get_secret_value()
            assert request.url.path == "/v1/messages"
            return httpx.Response(200, json={"content": [{"type": "text", "text": "yo"}]})

        assert (
            AnthropicChatClient(json_client(handler), api_key=KEY, model="m").complete("s", "u")
            == "yo"
        )

    def test_bad_shapes(self) -> None:
        empty = json_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ProviderError):
            OpenAIChatClient(empty, api_key=KEY, model="m").complete("s", "u")
        with pytest.raises(ProviderError):
            AnthropicChatClient(empty, api_key=KEY, model="m").complete("s", "u")
        no_text = json_client(
            lambda r: httpx.Response(200, json={"content": [{"type": "tool_use"}]})
        )
        with pytest.raises(ProviderError, match="no text"):
            AnthropicChatClient(no_text, api_key=KEY, model="m").complete("s", "u")


class TestServiceIssueFetching:
    def _service(self, tmp_path: Path, payload: object, status: int = 200):  # type: ignore[no-untyped-def]
        client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload))
        )
        return build_service(make_settings(tmp_path), http_client=client)

    def test_fetch_and_triage(self, tmp_path: Path) -> None:
        payload = {
            "title": "average() returns the wrong value",
            "body": "It returns 3.0 instead of 4.0 for average([2, 4, 6]) in pkg/calc.py.",
            "labels": [{"name": "ghagent:approved"}],
            "user": {"login": "maintainer"},
        }
        service = self._service(tmp_path, payload)
        issue = service.fetch_issue("acme/demo#7")
        assert issue.number == 7 and issue.repo == "acme/demo"
        verdict = service.triage(issue)
        assert verdict.actionable and verdict.category.value == "bug"

    def test_fetch_errors(self, tmp_path: Path) -> None:
        with pytest.raises(GitHubError, match="pull request"):
            self._service(tmp_path, {"title": "t", "pull_request": {}}).fetch_issue("a/b#1")
        with pytest.raises(ProviderError):
            self._service(tmp_path, {"message": "Not Found"}, status=404).fetch_issue("a/b#1")
        with pytest.raises(Exception, match="owner/repo#123"):
            self._service(tmp_path, {}).fetch_issue("bad target")

    def test_issue_model_normalises_missing_body(self) -> None:
        assert make_issue(body=None).body == ""

    def test_service_without_github_client_cannot_fetch(self, tmp_path: Path) -> None:
        from ghagent.service import RunService

        base = build_service(make_settings(tmp_path))
        service = RunService(make_settings(tmp_path), base._pipeline, base._triage, None)
        with pytest.raises(ConfigurationError, match="no GitHub client"):
            service.fetch_issue("a/b#1")
