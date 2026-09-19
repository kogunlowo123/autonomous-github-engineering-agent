"""Unit tests for git operations, the test runner, the GitHub client, config and infrastructure."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from ghagent.config import Settings
from ghagent.container import build_service
from ghagent.errors import ConfigurationError, GitError, GitHubError, GraphError, ProviderError
from ghagent.github import GitHubClient
from ghagent.gitops import GitRepo, _auth_env
from ghagent.graph import END, WorkflowGraph
from ghagent.logging_setup import JsonFormatter, configure_logging, get_logger
from ghagent.runner import run_tests, scrubbed_env
from ghagent.service import parse_target
from tests.conftest import git, json_client, make_bare_remote, make_repo, make_settings

TOKEN = SecretStr("ghp_" + "t" * 36)


class TestGitRepo:
    def test_clone_branch_diff_commit(self, tmp_path: Path) -> None:
        source = make_repo(tmp_path / "src")
        repo = GitRepo.clone(str(source), tmp_path / "clone")
        assert repo.current_branch() == "main" and repo.tracked_files()[0] == "README.md"
        repo.create_branch("ghagent/issue-1")
        (repo.path / "README.md").write_text("# Changed\n", encoding="utf-8")
        (repo.path / "new.txt").write_text("hello\n", encoding="utf-8")
        diff = repo.diff()
        assert "README.md" in diff and "new.txt" in diff and "+hello" in diff
        sha = repo.commit_all("change things")
        assert sha == repo.head_sha() and git(repo.path, "log", "-1", "--format=%an") == "ghagent\n"
        assert git(source, "branch", "--list", "ghagent/*") == ""

    def test_reset_hard_removes_changes_and_untracked_files(self, tmp_path: Path) -> None:
        repo = GitRepo.clone(str(make_repo(tmp_path / "src")), tmp_path / "clone")
        (repo.path / "README.md").write_text("dirty", encoding="utf-8")
        (repo.path / "junk.txt").write_text("x", encoding="utf-8")
        repo.reset_hard()
        assert (repo.path / "README.md").read_text(encoding="utf-8") == "# Demo\n"
        assert not (repo.path / "junk.txt").exists()

    def test_push_to_bare_remote_without_force(self, tmp_path: Path) -> None:
        bare = make_bare_remote(tmp_path)
        repo = GitRepo.clone(str(bare), tmp_path / "clone")
        repo.create_branch("ghagent/issue-2")
        (repo.path / "f.txt").write_text("x", encoding="utf-8")
        repo.commit_all("add f")
        repo.push("origin", "ghagent/issue-2")
        assert "ghagent/issue-2" in git(bare, "branch", "--list")
        repo.git("reset", "--hard", "HEAD~1")
        (repo.path / "h.txt").write_text("z", encoding="utf-8")
        repo.commit_all("different history")
        with pytest.raises(GitError, match="push failed"):
            repo.push("origin", "ghagent/issue-2")

    def test_errors_are_wrapped_and_redacted(self, tmp_path: Path) -> None:
        with pytest.raises(GitError, match="clone failed"):
            GitRepo.clone(str(tmp_path / "does-not-exist"), tmp_path / "c")
        repo = GitRepo.clone(str(make_repo(tmp_path / "src")), tmp_path / "clone")
        with pytest.raises(GitError) as info:
            repo.git("checkout", "branch-with-token-" + "sk-" + "a" * 30)
        assert "a" * 30 not in str(info.value)

    def test_credentials_travel_in_environment_not_argv_or_config(self, tmp_path: Path) -> None:
        env = _auth_env(TOKEN)
        assert (
            env["GIT_CONFIG_KEY_0"] == "http.extraheader" and "ttt" not in env["GIT_CONFIG_VALUE_0"]
        )
        assert _auth_env(None) == {}
        repo = GitRepo.clone(str(make_repo(tmp_path / "src")), tmp_path / "clone", token=TOKEN)
        assert "t" * 36 not in (repo.path / ".git" / "config").read_text(encoding="utf-8")


class TestRunner:
    def test_pass_fail_and_output_tail(self, tmp_path: Path) -> None:
        ok = run_tests([sys.executable, "-c", "print('fine')"], tmp_path, timeout=30)
        assert ok.passed and ok.exit_code == 0 and "fine" in ok.output
        bad = run_tests(
            [sys.executable, "-c", "import sys; print('boom'*5000); sys.exit(2)"],
            tmp_path,
            timeout=30,
        )
        assert not bad.passed and bad.exit_code == 2 and len(bad.output) <= 6000

    def test_timeout_and_missing_command(self, tmp_path: Path) -> None:
        slow = run_tests([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=1)
        assert slow.timed_out and not slow.passed and slow.exit_code is None
        missing = run_tests(["definitely-not-a-command-xyz"], tmp_path, timeout=5)
        assert not missing.passed and "could not start" in missing.output

    def test_environment_is_scrubbed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "should-not-leak")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
        env = scrubbed_env()
        assert "GITHUB_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env and env["CI"] == "1"
        probe = "import os,sys; sys.exit(1 if any(k in os.environ for k in ('GITHUB_TOKEN','ANTHROPIC_API_KEY')) else 0)"
        assert run_tests([sys.executable, "-c", probe], tmp_path, timeout=30).passed

    def test_output_is_redacted(self, tmp_path: Path) -> None:
        secret = "sk-" + "r" * 30
        result = run_tests([sys.executable, "-c", f"print('key {secret}')"], tmp_path, timeout=30)
        assert secret not in result.output and "[REDACTED]" in result.output


class TestGitHubClient:
    def _client(self, handler: object) -> GitHubClient:
        return GitHubClient(json_client(handler), token=TOKEN)  # type: ignore[arg-type]

    def test_get_issue_maps_fields(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == f"Bearer {TOKEN.get_secret_value()}"
            assert request.headers["x-github-api-version"] == "2022-11-28"
            return httpx.Response(
                200,
                json={
                    "title": "T",
                    "body": None,
                    "labels": [{"name": "ghagent:approved"}, "stray"],
                    "user": {"login": "octo"},
                    "html_url": "https://github.com/a/b/issues/3",
                },
            )

        issue = self._client(handler).get_issue("a/b", 3)
        assert (issue.title, issue.body, issue.labels, issue.author) == (
            "T",
            "",
            ["ghagent:approved"],
            "octo",
        )

    def test_rejects_pull_requests_and_bad_shapes(self) -> None:
        pr = self._client(lambda r: httpx.Response(200, json={"title": "t", "pull_request": {}}))
        with pytest.raises(GitHubError, match="pull request"):
            pr.get_issue("a/b", 1)
        with pytest.raises(GitHubError, match="unexpected"):
            self._client(lambda r: httpx.Response(200, json=[])).get_issue("a/b", 1)

    def test_find_and_create_pull_request(self) -> None:
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.query.decode()))
            if request.method == "GET":
                return httpx.Response(200, json=[{"html_url": "https://x/pull/1"}])
            body = json.loads(request.content)
            assert body["draft"] is True and body["head"] == "me:ghagent/issue-1"
            return httpx.Response(201, json={"html_url": "https://x/pull/2"})

        client = self._client(handler)
        assert client.find_open_pull_request("a/b", "me:ghagent/issue-1") == "https://x/pull/1"
        url = client.create_pull_request(
            "a/b", title="t", body="b", head="me:ghagent/issue-1", base="main", draft=True
        )
        assert url == "https://x/pull/2" and "head=me:ghagent/issue-1" in seen[0][1]

    def test_missing_url_and_listing_errors(self) -> None:
        with pytest.raises(GitHubError, match="no html_url"):
            self._client(lambda r: httpx.Response(201, json={})).create_pull_request(
                "a/b", title="t", body="b", head="h", base="m", draft=True
            )
        with pytest.raises(GitHubError, match="unexpected"):
            self._client(lambda r: httpx.Response(200, json={})).find_open_pull_request("a/b", "h")
        empty = self._client(lambda r: httpx.Response(200, json=[]))
        assert empty.find_open_pull_request("a/b", "h") is None

    def test_no_token_sends_no_authorization(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "authorization" not in request.headers
            return httpx.Response(200, json=[])

        GitHubClient(json_client(handler), token=None).find_open_pull_request("a/b", "h")


class TestConfigAndContainer:
    def test_conservative_defaults(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)
        assert (
            settings.dry_run and settings.draft_pr and settings.required_label == "ghagent:approved"
        )
        assert settings.branch_prefix == "ghagent/" and ".github/" in settings.deny_paths

    def test_secrets_masked_and_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = make_settings(tmp_path, github_token="ghp_" + "s" * 36)
        assert "s" * 36 not in repr(settings) + settings.model_dump_json()
        monkeypatch.setenv("GITHUB_TOKEN", "abc")
        token = Settings(_env_file=None).github_token
        assert token is not None and token.get_secret_value() == "abc"

    def test_env_list_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHAGENT_TEST_COMMAND", '["make", "test"]')
        monkeypatch.setenv("GHAGENT_ALLOWED_AUTHORS", '["alice"]')
        settings = Settings(_env_file=None)
        assert settings.test_command == ["make", "test"] and settings.allowed_authors == ["alice"]

    @pytest.mark.parametrize(
        "override",
        [
            {"branch_prefix": "ghagent"},
            {"branch_prefix": ""},
            {"test_command": []},
            {"retry_min_wait": 5.0, "retry_max_wait": 1.0},
        ],
    )
    def test_invalid_settings(self, tmp_path: Path, override: dict[str, object]) -> None:
        with pytest.raises(ValueError, match=r"branch_prefix|test_command|retry_max_wait"):
            make_settings(tmp_path, **override)

    @pytest.mark.parametrize(
        ("provider", "name"), [("openai", "OPENAI"), ("anthropic", "ANTHROPIC")]
    )
    def test_missing_provider_key_fails_fast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, name: str
    ) -> None:
        for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(ConfigurationError, match=name):
            build_service(make_settings(tmp_path, llm_provider=provider))

    def test_parse_target(self) -> None:
        assert parse_target(" acme/demo#12 ") == ("acme/demo", 12)
        for bad in ("acme/demo", "acme#1", "a/b#x", "a/b/c#1"):
            with pytest.raises(Exception, match="owner/repo#123"):
                parse_target(bad)


class TestGraphAndLogging:
    def _graph(self) -> WorkflowGraph[list[str]]:
        graph: WorkflowGraph[list[str]] = WorkflowGraph()
        for name in "abc":
            graph.add_node(name, lambda s, n=name: [*s, n])
        graph.set_entry("a")
        return graph

    def test_routing_and_budget(self) -> None:
        graph = self._graph()
        graph.add_conditional_edges("a", lambda s: "b")
        graph.add_edge("b", END)
        assert graph.run([]) == ["a", "b"]
        loop = self._graph()
        loop.add_edge("a", "a")
        with pytest.raises(GraphError, match="exceeded"):
            loop.run([], max_steps=3)

    def test_validation(self) -> None:
        with pytest.raises(GraphError, match="entry"):
            WorkflowGraph[list[str]]().validate()
        graph = self._graph()
        graph.add_edge("a", "ghost")
        with pytest.raises(GraphError, match="unknown node"):
            graph.validate()
        with pytest.raises(GraphError):
            self._graph().add_node("a", lambda s: s)
        with pytest.raises(GraphError, match="no outgoing"):
            self._graph().run([])

    def test_http_error_mapping(self) -> None:
        with pytest.raises(ProviderError) as info:
            json_client(lambda r: httpx.Response(400, text="key sk-" + "z" * 30)).request(
                "GET", "http://x"
            )
        assert "z" * 30 not in str(info.value)

    def test_logging_redacts(self, capsys: pytest.CaptureFixture[str]) -> None:
        record = logging.LogRecord(
            "ghagent.t", logging.INFO, __file__, 1, "tok sk-" + "q" * 30, (), None
        )
        assert json.loads(JsonFormatter().format(record))["message"] == "tok [REDACTED]"
        configure_logging("INFO", json_output=False)
        configure_logging("INFO", json_output=False)
        assert len(logging.getLogger("ghagent").handlers) == 1
        get_logger("unit").info("password = hunter2hunter2")
        assert "hunter2" not in capsys.readouterr().err
        configure_logging("CRITICAL")
