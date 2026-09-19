"""Shared fixtures: real git repositories, a scripted model and a mock GitHub API."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from ghagent.config import Settings
from ghagent.models import Issue
from ghagent.providers.http import JsonClient

BUGGY_FILES: dict[str, str] = {
    "pkg/__init__.py": "",
    "pkg/calc.py": (
        "def total(values):\n"
        "    return sum(values)\n"
        "\n"
        "\n"
        "def average(values):\n"
        "    return sum(values) / (len(values) + 1)\n"
    ),
    "tests/test_calc.py": (
        "from pkg.calc import total\n\n\ndef test_total():\n    assert total([1, 2, 3]) == 6\n"
    ),
    "README.md": "# Demo\n",
}

BUG_SEARCH = "sum(values) / (len(values) + 1)"
BUG_REPLACE = "sum(values) / len(values)"
NEW_TEST = (
    "from pkg.calc import average\n\n\ndef test_average():\n    assert average([2, 4, 6]) == 4\n"
)


def git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` and return stdout."""
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def branches(repo: Path) -> list[str]:
    """Names of the local branches in ``repo``."""
    out = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return out.split()


def make_repo(path: Path, files: dict[str, str] | None = None) -> Path:
    """Create a git repository on branch ``main`` containing ``files``."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    for relative, content in (files or BUGGY_FILES).items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "initial")
    return path


def make_bare_remote(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """A bare repository seeded from a fresh working repository, used as ``origin``."""
    seed = make_repo(tmp_path / "seed", files)
    bare = tmp_path / "remote.git"
    subprocess.run(  # noqa: S603
        ["git", "clone", "-q", "--bare", str(seed), str(bare)],  # noqa: S607
        check=True,
        capture_output=True,
    )
    return bare


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Settings that ignore the developer's environment and write only under ``tmp_path``."""
    base: dict[str, object] = {
        "work_dir": tmp_path / "work",
        "out_dir": tmp_path / "runs",
        "test_command": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        "test_timeout_seconds": 120,
        "retry_min_wait": 0.0,
        "retry_max_wait": 0.0,
        "log_level": "CRITICAL",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def make_issue(**overrides: Any) -> Issue:
    """The sample bug report, approved by a maintainer."""
    data: dict[str, Any] = {
        "repo": "acme/demo",
        "number": 7,
        "title": "average() returns the wrong value",
        "body": (
            "Calling average([2, 4, 6]) in pkg/calc.py returns 3.0 instead of 4.0. "
            "It looks like it divides by len(values) + 1."
        ),
        "labels": ["ghagent:approved"],
        "author": "maintainer",
    }
    data.update(overrides)
    return Issue.model_validate(data)


def good_reply() -> str:
    """A correct fix plus a regression test."""
    return json.dumps(
        {
            "summary": "Fix average() to divide by the number of values",
            "edits": [
                {
                    "path": "pkg/calc.py",
                    "action": "replace",
                    "search": BUG_SEARCH,
                    "replace": BUG_REPLACE,
                },
                {"path": "tests/test_average.py", "action": "create", "content": NEW_TEST},
            ],
        }
    )


def reply(edits: list[dict[str, str]], summary: str = "Change") -> str:
    """Build a model reply from raw edits."""
    return json.dumps({"summary": summary, "edits": edits})


class ScriptedLLM:
    """Model double. Planner calls get ``planner_reply``; coder calls consume ``replies``."""

    def __init__(self, *replies: str, planner_reply: str | None = None) -> None:
        self.replies = list(replies)
        self.planner_reply = planner_reply
        self.coder_prompts: list[str] = []
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if "You plan a minimal code change" in system:
            return self.planner_reply if self.planner_reply is not None else "not json"
        self.coder_prompts.append(user)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class FakeGitHub:
    """Mock GitHub REST API that records every request."""

    def __init__(self, existing_pr: str | None = None) -> None:
        self.existing_pr = existing_pr
        self.requests: list[tuple[str, str, dict[str, Any] | None, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append(
            (request.method, request.url.path, body, request.headers.get("authorization"))
        )
        if request.method == "GET" and request.url.path.endswith("/pulls"):
            listing = [{"html_url": self.existing_pr}] if self.existing_pr else []
            return httpx.Response(200, json=listing)
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/acme/demo/pull/99"})
        return httpx.Response(404, json={"message": "not found"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def posts(self) -> list[dict[str, Any]]:
        return [b for m, _, b, _ in self.requests if m == "POST" and b is not None]


def json_client(
    handler: Callable[[httpx.Request], httpx.Response], attempts: int = 2
) -> JsonClient:
    """A JsonClient backed by an in-process mock transport."""
    return JsonClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        attempts=attempts,
        min_wait=0.0,
        max_wait=0.0,
    )


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "source")
