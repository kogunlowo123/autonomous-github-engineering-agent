"""Mutable state threaded through the workflow, and the human approval gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ghagent.gitops import GitRepo
from ghagent.models import (
    ChangeSet,
    DiffStats,
    Finding,
    Issue,
    Plan,
    Status,
    TestResult,
    TraceEvent,
    Triage,
)


@dataclass
class RunState:
    """Everything one run knows and has done so far."""

    issue: Issue
    dry_run: bool
    source: str
    triage: Triage | None = None
    plan: Plan | None = None
    changeset: ChangeSet | None = None
    baseline: TestResult | None = None
    tests: TestResult | None = None
    attempt: int = 0
    feedback: str = ""
    edit_failed: bool = False
    findings: list[Finding] = field(default_factory=list)
    diff: str = ""
    diff_stats: DiffStats = field(default_factory=DiffStats)
    repo: GitRepo | None = None
    workdir: Path | None = None
    branch: str = ""
    base_branch: str = ""
    pr_title: str = ""
    pr_body: str = ""
    pr_url: str = ""
    status: Status | None = None
    reasons: list[str] = field(default_factory=list)
    trace: list[TraceEvent] = field(default_factory=list)
    note: str = ""

    @property
    def halted(self) -> bool:
        """True once a node has decided the run is over."""
        return self.status is not None

    def halt(self, status: Status, reason: str) -> None:
        """End the run with ``status`` and a human-readable reason."""
        self.status = status
        self.reasons.append(reason)
        self.note = reason


@runtime_checkable
class Approver(Protocol):
    """Human-in-the-loop gate consulted before anything leaves the machine."""

    def approve(self, summary: str) -> bool:
        """Return True to allow the push and pull request."""


class DenyApprover:
    """Refuses everything. Used when no approval mechanism is available."""

    def approve(self, summary: str) -> bool:
        return False


class AutoApprover:
    """Approves everything. Only for explicit ``--yes`` use in trusted automation."""

    def approve(self, summary: str) -> bool:
        return True
