"""Run artifacts written for audit: report, diff and PR text."""

from __future__ import annotations

from pathlib import Path

from ghagent._version import __version__
from ghagent.agents.state import RunState
from ghagent.errors import GhagentError
from ghagent.models import RunReport, Status


def build_report(state: RunState) -> RunReport:
    """Summarise ``state`` as a serialisable :class:`RunReport`."""
    return RunReport(
        tool_version=__version__,
        repo=state.issue.repo,
        issue=state.issue.number,
        dry_run=state.dry_run,
        status=state.status or Status.NEEDS_HUMAN,
        reasons=state.reasons,
        triage=state.triage,
        plan=state.plan,
        changeset=state.changeset,
        baseline=state.baseline,
        tests=state.tests,
        attempts=state.attempt + 1 if state.plan is not None else 0,
        findings=state.findings,
        diff_stats=state.diff_stats,
        branch=state.branch,
        pr_url=state.pr_url,
        trace=state.trace,
    )


def run_directory(out_dir: Path, repo: str, number: int) -> Path:
    """Directory for one run. The repo name is validated by :class:`Issue`, so it is path-safe."""
    return out_dir / f"{repo.replace('/', '-')}-{number}"


def write_artifacts(state: RunState, out_dir: Path) -> RunReport:
    """Write ``report.json``, ``changes.diff`` and ``pr.md`` for ``state`` and return the report."""
    target = run_directory(out_dir, state.issue.repo, state.issue.number)
    report = build_report(state)
    try:
        target.mkdir(parents=True, exist_ok=True)
        if state.diff.strip():
            (target / "changes.diff").write_text(state.diff, encoding="utf-8", newline="")
            report.artifacts["diff"] = str(target / "changes.diff")
        if state.pr_body:
            (target / "pr.md").write_text(
                f"# {state.pr_title}\n\n{state.pr_body}", encoding="utf-8", newline=""
            )
            report.artifacts["pull_request"] = str(target / "pr.md")
        if state.workdir is not None:
            report.artifacts["workdir"] = str(state.workdir)
        report_path = target / "report.json"
        report.artifacts["report"] = str(report_path)
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        raise GhagentError(f"cannot write run artifacts to {target}: {exc}") from exc
    return report
