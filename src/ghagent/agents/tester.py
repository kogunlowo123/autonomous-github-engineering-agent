"""Tester agent: runs the project's tests before and after the change."""

from __future__ import annotations

from ghagent.agents.state import RunState
from ghagent.models import Status
from ghagent.runner import run_tests


class TesterAgent:
    """Workflow nodes for the baseline run and the post-change run."""

    __test__ = False  # not a pytest test class

    def __init__(self, command: list[str], *, timeout: int, require_green_baseline: bool) -> None:
        self._command = command
        self._timeout = timeout
        self._require_green = require_green_baseline

    def baseline(self, state: RunState) -> RunState:
        """Run the tests on the untouched repository so failures can be attributed correctly."""
        assert state.workdir is not None
        state.baseline = run_tests(self._command, state.workdir, timeout=self._timeout)
        result = state.baseline
        state.note = f"baseline {'passed' if result.passed else 'FAILED'} in {result.duration_s}s"
        if not result.passed and self._require_green:
            state.halt(
                Status.BASELINE_FAILING,
                "tests already fail before any change; fix the baseline first",
            )
        return state

    def run(self, state: RunState) -> RunState:
        """Run the tests against the modified working copy."""
        assert state.workdir is not None
        state.tests = run_tests(self._command, state.workdir, timeout=self._timeout)
        result = state.tests
        state.note = (
            f"tests {'passed' if result.passed else 'FAILED'} "
            f"(exit {result.exit_code}) in {result.duration_s}s"
        )
        return state
