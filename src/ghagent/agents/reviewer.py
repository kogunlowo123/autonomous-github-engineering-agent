"""Reviewer agent: security and policy review of the diff before it can leave the machine."""

from __future__ import annotations

from ghagent.agents.state import RunState
from ghagent.models import Finding, Status
from ghagent.security import scan_diff


class ReviewerAgent:
    """Blocks empty, oversized or dangerous changes."""

    def __init__(self, *, max_files: int, max_diff_lines: int) -> None:
        self._max_files = max_files
        self._max_lines = max_diff_lines

    def run(self, state: RunState) -> RunState:
        assert state.repo is not None
        state.diff = state.repo.diff()
        if not state.diff.strip():
            state.halt(Status.NO_CHANGE, "the model's edits produced no difference")
            return state

        findings, stats = scan_diff(state.diff)
        if stats.files > self._max_files:
            findings.append(
                Finding(
                    rule="too-many-files",
                    severity="block",
                    message=f"change touches {stats.files} files, the limit is {self._max_files}",
                )
            )
        size = stats.additions + stats.deletions
        if size > self._max_lines:
            findings.append(
                Finding(
                    rule="diff-too-large",
                    severity="block",
                    message=f"change is {size} lines, the limit is {self._max_lines}",
                )
            )
        state.findings = findings
        state.diff_stats = stats
        blocks = [f for f in findings if f.severity == "block"]
        state.note = f"{len(blocks)} blocking, {len(findings) - len(blocks)} warnings"
        if blocks:
            state.halt(
                Status.BLOCKED_SECURITY,
                "blocked: " + "; ".join(f"{f.rule} ({f.path or 'diff'})" for f in blocks),
            )
        return state
