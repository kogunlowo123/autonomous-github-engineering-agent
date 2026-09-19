"""Workflow wiring.

::

    triage -> prepare -> baseline -> plan -> code -> test -> review -> publish -> finish
                                              ^  \\        \\
                                              |   +-> retry +-> (give up) -> finish
                                              +-------------+

Any node may end the run early by setting a terminal status; every path passes through ``finish``,
which writes the audit artifacts.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ghagent.agents import (
    CoderAgent,
    PlannerAgent,
    PublisherAgent,
    ReviewerAgent,
    RunState,
    TesterAgent,
    TriageAgent,
)
from ghagent.artifacts import write_artifacts
from ghagent.config import Settings
from ghagent.errors import PolicyError
from ghagent.github import GitHubClient
from ghagent.gitops import GitRepo
from ghagent.graph import END, WorkflowGraph
from ghagent.logging_setup import get_logger
from ghagent.models import Issue, RunReport, Status, TraceEvent

_log = get_logger("pipeline")
_FEEDBACK_TAIL = 3000


def _make_writable(func: Callable[[str], object], target: str, *_: object) -> None:
    """rmtree error hook: git marks object files read-only, which blocks deletion on Windows."""
    os.chmod(target, stat.S_IWRITE)
    func(target)


def _remove_tree(path: Path, allowed_root: Path) -> None:
    """Delete ``path`` only if it is strictly inside ``allowed_root``."""
    resolved = path.resolve()
    root = allowed_root.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise PolicyError(f"refusing to delete {path}: outside the work directory")
    if sys.version_info >= (3, 12):
        shutil.rmtree(resolved, onexc=_make_writable)
    else:
        shutil.rmtree(resolved, onerror=_make_writable)


class RunPipeline:
    """Runs one issue through the agents and returns the audited :class:`RunReport`."""

    def __init__(
        self,
        settings: Settings,
        *,
        triage: TriageAgent,
        planner: PlannerAgent,
        coder: CoderAgent,
        tester: TesterAgent,
        reviewer: ReviewerAgent,
        publisher: PublisherAgent,
        github: GitHubClient | None,
    ) -> None:
        self._settings = settings
        self._github = github

        graph: WorkflowGraph[RunState] = WorkflowGraph()
        nodes: dict[str, Callable[[RunState], RunState]] = {
            "triage": triage.run,
            "prepare": self._prepare,
            "baseline": tester.baseline,
            "plan": planner.run,
            "code": coder.run,
            "test": tester.run,
            "retry": self._retry,
            "review": reviewer.run,
            "publish": publisher.run,
            "finish": self._finish,
        }
        for name, func in nodes.items():
            graph.add_node(name, self._traced(name, func))
        graph.set_entry("triage")

        def go(nxt: str) -> Callable[[RunState], str]:
            return lambda s: "finish" if s.halted else nxt

        graph.add_conditional_edges("triage", go("prepare"))
        graph.add_conditional_edges("prepare", go("baseline"))
        graph.add_conditional_edges("baseline", go("plan"))
        graph.add_edge("plan", "code")
        graph.add_conditional_edges(
            "code", lambda s: "finish" if s.halted else ("retry" if s.edit_failed else "test")
        )
        graph.add_conditional_edges(
            "test", lambda s: "review" if s.tests is not None and s.tests.passed else "retry"
        )
        graph.add_conditional_edges("retry", go("code"))
        graph.add_conditional_edges("review", go("publish"))
        graph.add_edge("publish", "finish")
        graph.add_edge("finish", END)
        self._graph = graph

    # -- helpers ------------------------------------------------------------------------------

    def _traced(
        self, name: str, func: Callable[[RunState], RunState]
    ) -> Callable[[RunState], RunState]:
        def wrapper(state: RunState) -> RunState:
            start = time.perf_counter()
            state.note = ""
            result = func(state)
            elapsed = round((time.perf_counter() - start) * 1000.0, 3)
            result.trace.append(TraceEvent(node=name, detail=result.note, duration_ms=elapsed))
            return result

        return wrapper

    def _workdir(self, issue: Issue) -> Path:
        return self._settings.work_dir / f"{issue.repo.replace('/', '-')}-{issue.number}"

    # -- nodes --------------------------------------------------------------------------------

    def _prepare(self, state: RunState) -> RunState:
        settings = self._settings
        workdir = self._workdir(state.issue)
        if workdir.exists():
            _remove_tree(workdir, settings.work_dir)
        is_remote = state.source.startswith(("http://", "https://"))
        repo = GitRepo.clone(
            state.source,
            workdir,
            token=settings.github_token if is_remote else None,
            identity=(settings.committer_name, settings.committer_email),
            timeout=settings.git_timeout_seconds,
        )
        state.repo, state.workdir = repo, workdir
        state.base_branch = repo.current_branch()
        state.branch = f"{settings.branch_prefix}issue-{state.issue.number}"

        if not state.dry_run and self._github is not None:
            owner = settings.pr_head_owner or state.issue.repo.split("/")[0]
            existing = self._github.find_open_pull_request(
                state.issue.repo, f"{owner}:{state.branch}"
            )
            if existing:
                state.halt(
                    Status.NEEDS_HUMAN,
                    f"a pull request from {state.branch} is already open: {existing}",
                )
                return state
        repo.create_branch(state.branch)
        state.note = f"cloned {state.base_branch}, working on {state.branch}"
        return state

    def _retry(self, state: RunState) -> RunState:
        assert state.repo is not None
        if state.attempt + 1 >= self._settings.max_attempts:
            status = Status.EDIT_FAILED if state.edit_failed else Status.TESTS_FAILED
            state.halt(status, f"gave up after {state.attempt + 1} attempt(s)")
            return state
        if not state.edit_failed and state.tests is not None:
            state.feedback = (
                "The tests failed after your change. Fix the cause without weakening any test. "
                "Output tail:\n" + state.tests.output[-_FEEDBACK_TAIL:]
            )
        state.attempt += 1
        state.repo.reset_hard()
        state.tests = None
        state.changeset = None
        state.note = f"retrying (attempt {state.attempt + 1})"
        return state

    def _finish(self, state: RunState) -> RunState:
        if state.status is None:
            state.halt(Status.NEEDS_HUMAN, "the run ended without a decision")
        state.note = state.status.value if state.status else ""
        return state

    # -- entry point --------------------------------------------------------------------------

    def run(self, issue: Issue, *, source: str, dry_run: bool) -> RunReport:
        """Process ``issue`` against the repository at ``source``."""
        state = self._graph.run(RunState(issue=issue, dry_run=dry_run, source=source))
        report = write_artifacts(state, self._settings.out_dir)
        _log.info(
            "run completed",
            extra={"repo": issue.repo, "issue": issue.number, "status": report.status.value},
        )
        return report
