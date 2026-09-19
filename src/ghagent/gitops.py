"""Thin, safe wrapper around the ``git`` command line.

Commands run without a shell, with prompts disabled and a timeout. Credentials are passed through
``GIT_CONFIG_*`` environment variables rather than argv or the remote URL, so they never appear in
process listings, ``.git/config`` or error messages.
"""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

from pydantic import SecretStr

from ghagent.errors import GitError
from ghagent.security import redact


def _auth_env(token: SecretStr | None) -> dict[str, str]:
    if token is None:
        return {}
    raw = base64.b64encode(f"x-access-token:{token.get_secret_value()}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {raw}",
    }


class GitRepo:
    """A local git working copy."""

    def __init__(
        self,
        path: Path,
        *,
        identity: tuple[str, str] = ("ghagent", "ghagent@users.noreply.github.com"),
        timeout: int = 300,
    ) -> None:
        self.path = path
        self._identity = identity
        self._timeout = timeout

    # -- plumbing -----------------------------------------------------------------------------

    @staticmethod
    def _run(
        args: list[str],
        *,
        cwd: Path | None,
        timeout: int,
        token: SecretStr | None = None,
        identity: tuple[str, str] | None = None,
    ) -> str:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", **_auth_env(token)}
        prefix: list[str] = []
        if identity is not None:
            prefix = ["-c", f"user.name={identity[0]}", "-c", f"user.email={identity[1]}"]
        try:
            result = subprocess.run(  # noqa: S603
                ["git", *prefix, *args],  # noqa: S607
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(redact(f"git {args[0]} failed: {exc}")) from exc
        if result.returncode != 0:
            detail = redact((result.stderr or result.stdout).strip())[:500]
            raise GitError(f"git {args[0]} failed ({result.returncode}): {detail}")
        return result.stdout

    def git(self, *args: str, token: SecretStr | None = None, identity: bool = False) -> str:
        """Run ``git <args>`` in this repository and return stdout."""
        return self._run(
            list(args),
            cwd=self.path,
            timeout=self._timeout,
            token=token,
            identity=self._identity if identity else None,
        )

    @classmethod
    def clone(
        cls,
        source: str,
        dest: Path,
        *,
        token: SecretStr | None = None,
        identity: tuple[str, str] = ("ghagent", "ghagent@users.noreply.github.com"),
        timeout: int = 300,
    ) -> GitRepo:
        """Clone ``source`` (URL or local path) into ``dest``, without hardlinks."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        cls._run(
            ["clone", "--quiet", "--no-hardlinks", "--", source, str(dest)],
            cwd=None,
            timeout=timeout,
            token=token,
        )
        return cls(dest, identity=identity, timeout=timeout)

    # -- queries ------------------------------------------------------------------------------

    def current_branch(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def head_sha(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def tracked_files(self) -> list[str]:
        """Paths tracked by git, which excludes ignored and untracked files."""
        out = self.git("ls-files", "-z")
        return [p for p in out.split("\0") if p]

    def diff(self) -> str:
        """Unified diff of the working tree (including new files) against HEAD."""
        self.git("add", "-A", "--intent-to-add")
        return self.git("diff", "HEAD", "--no-color", "--no-ext-diff")

    # -- mutations ----------------------------------------------------------------------------

    def create_branch(self, name: str) -> None:
        self.git("checkout", "--quiet", "-b", name)

    def reset_hard(self) -> None:
        """Discard all uncommitted changes, including untracked files."""
        self.git("reset", "--quiet", "--hard", "HEAD")
        self.git("clean", "--quiet", "-fd")

    def commit_all(self, message: str) -> str:
        """Stage everything, commit and return the new commit sha."""
        self.git("add", "-A")
        self.git("commit", "--quiet", "--no-verify", "-m", message, identity=True)
        return self.head_sha()

    def push(self, remote: str, branch: str, *, token: SecretStr | None = None) -> None:
        """Push ``branch`` without force. ``remote`` may be a remote name or URL."""
        self.git("push", "--quiet", remote, f"{branch}:refs/heads/{branch}", token=token)
