"""Publisher agent: builds the PR text, commits, and (only if approved) pushes and opens a PR."""

from __future__ import annotations

import re

from ghagent.agents.state import Approver, RunState
from ghagent.config import Settings
from ghagent.errors import ConfigurationError, PolicyError
from ghagent.github import GitHubClient
from ghagent.models import IssueCategory, Status
from ghagent.security import neutralize_markdown

_PREFIX = {
    IssueCategory.BUG: "fix",
    IssueCategory.FEATURE: "feat",
    IssueCategory.DOCS: "docs",
    IssueCategory.CHORE: "chore",
}


def _one_line(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", neutralize_markdown(text)).strip()[:limit]


def build_pr(state: RunState, footer: str) -> tuple[str, str]:
    """Return the PR title and Markdown body for ``state``."""
    assert state.changeset is not None and state.triage is not None
    prefix = _PREFIX.get(state.triage.category, "chore")
    title = f"{prefix}: {_one_line(state.changeset.summary, 90)} (#{state.issue.number})"

    lines = [
        f"Refs #{state.issue.number}",
        "",
        "## Summary",
        "",
        neutralize_markdown(state.changeset.summary),
        "",
        "## Changes",
        "",
    ]
    lines += [f"- `{edit.path}` ({edit.action})" for edit in state.changeset.edits]
    stats = state.diff_stats
    lines += [
        "",
        f"{stats.files} files changed, {stats.additions} additions, {stats.deletions} deletions.",
        "",
        "## Verification",
        "",
    ]
    if state.baseline is not None:
        lines.append(
            f"- Baseline: {'passed' if state.baseline.passed else 'failed'} before the change."
        )
    if state.tests is not None:
        lines.append(
            f"- Tests after the change: {'passed' if state.tests.passed else 'FAILED'} "
            f"(`{' '.join(state.tests.command)}`)."
        )
    warnings = [f for f in state.findings if f.severity == "warn"]
    lines += ["", "## Review notes", ""]
    if warnings:
        lines += [
            f"- {f.rule}: {f.message}" + (f" in `{f.path}`" if f.path else "") for f in warnings
        ]
    else:
        lines.append("- No warnings from the automated diff review.")
    lines += ["", "---", neutralize_markdown(footer), ""]
    return title, "\n".join(lines)


class PublisherAgent:
    """Workflow node that finishes a successful run."""

    def __init__(
        self,
        settings: Settings,
        github: GitHubClient | None,
        approver: Approver,
    ) -> None:
        self._settings = settings
        self._github = github
        self._approver = approver

    def run(self, state: RunState) -> RunState:
        assert state.repo is not None and state.changeset is not None
        state.pr_title, state.pr_body = build_pr(state, self._settings.pr_footer)
        if (
            not state.branch.startswith(self._settings.branch_prefix)
            or state.branch == state.base_branch
        ):
            raise PolicyError(f"refusing to publish from branch '{state.branch}'")

        state.repo.commit_all(f"{state.pr_title}\n\nRefs #{state.issue.number}")

        if state.dry_run:
            state.status = Status.PR_READY
            state.reasons.append("dry run: committed locally only, nothing was pushed")
            state.note = "dry run, PR text prepared"
            return state

        summary = (
            f"{state.pr_title}: {state.diff_stats.files} files, "
            f"+{state.diff_stats.additions}/-{state.diff_stats.deletions}, "
            f"branch {state.branch} -> {state.base_branch}"
        )
        if not self._approver.approve(summary):
            state.status = Status.AWAITING_APPROVAL
            state.reasons.append("a human did not approve the push; the branch exists only locally")
            state.note = "approval declined"
            return state

        if self._github is None:
            raise ConfigurationError("a GitHub client is required to open a pull request")
        settings = self._settings
        state.repo.push(settings.push_url or "origin", state.branch, token=settings.github_token)
        head = (
            f"{settings.pr_head_owner}:{state.branch}" if settings.pr_head_owner else state.branch
        )
        state.pr_url = self._github.create_pull_request(
            state.issue.repo,
            title=state.pr_title,
            body=state.pr_body,
            head=head,
            base=state.base_branch,
            draft=settings.draft_pr,
        )
        state.status = Status.PR_OPENED
        state.reasons.append("pull request opened" + (" as a draft" if settings.draft_pr else ""))
        state.note = state.pr_url
        return state
