"""Test runner: executes the project's test command with a scrubbed environment and a timeout.

This is process hygiene, not a sandbox. Tests run with the agent's privileges, so run the agent
itself inside a disposable container or CI runner when the repository is not fully trusted.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ghagent.models import TestResult
from ghagent.security import redact

_ENV_ALLOWLIST = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
    "VIRTUAL_ENV",
)
_MAX_OUTPUT = 6000


def scrubbed_env() -> dict[str, str]:
    """A minimal environment that carries no tokens or API keys."""
    env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    env.update({"CI": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def run_tests(command: list[str], cwd: Path, *, timeout: int) -> TestResult:
    """Run ``command`` in ``cwd`` and summarise the result.

    Output is redacted and truncated to its tail, which is where failures are reported.
    """
    start = time.perf_counter()
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            cwd=cwd,
            env=scrubbed_env(),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout if isinstance(exc.stdout, str) else ""
        return TestResult(
            command=command,
            exit_code=None,
            passed=False,
            timed_out=True,
            output=redact(partial)[-_MAX_OUTPUT:],
            duration_s=round(time.perf_counter() - start, 3),
        )
    except OSError as exc:
        return TestResult(
            command=command,
            exit_code=None,
            passed=False,
            output=f"could not start test command: {exc}",
            duration_s=round(time.perf_counter() - start, 3),
        )
    combined = redact((completed.stdout or "") + (completed.stderr or ""))
    return TestResult(
        command=command,
        exit_code=completed.returncode,
        passed=completed.returncode == 0,
        output=combined[-_MAX_OUTPUT:],
        duration_s=round(time.perf_counter() - start, 3),
    )
