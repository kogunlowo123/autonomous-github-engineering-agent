"""Application service: fetch issues, triage them and run the pipeline."""

from __future__ import annotations

import re

from ghagent.agents import TriageAgent
from ghagent.config import Settings
from ghagent.errors import ConfigurationError, GhagentError
from ghagent.github import GitHubClient
from ghagent.models import Issue, RunReport, Triage
from ghagent.pipeline import RunPipeline

_TARGET = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<number>\d+)$")


def parse_target(target: str) -> tuple[str, int]:
    """Split ``owner/repo#123`` into its parts."""
    match = _TARGET.match(target.strip())
    if not match:
        raise GhagentError(f"'{target}' is not in the form owner/repo#123")
    return match["repo"], int(match["number"])


class RunService:
    """Facade used by the CLI and library callers."""

    def __init__(
        self,
        settings: Settings,
        pipeline: RunPipeline,
        triage: TriageAgent,
        github: GitHubClient | None,
    ) -> None:
        self._settings = settings
        self._pipeline = pipeline
        self._triage = triage
        self._github = github

    def fetch_issue(self, target: str) -> Issue:
        """Fetch ``owner/repo#123`` from GitHub."""
        if self._github is None:
            raise ConfigurationError("no GitHub client is configured")
        repo, number = parse_target(target)
        return self._github.get_issue(repo, number)

    def triage(self, issue: Issue) -> Triage:
        """Triage ``issue`` without touching any repository."""
        return self._triage.evaluate(issue)

    def run(self, issue: Issue, *, source: str | None = None) -> RunReport:
        """Process ``issue``. Dry unless the settings say otherwise.

        Raises:
            ConfigurationError: If live mode is requested without a GitHub token.
        """
        settings = self._settings
        if not settings.dry_run and (settings.github_token is None or self._github is None):
            raise ConfigurationError("live mode needs GITHUB_TOKEN (or GHAGENT_GITHUB_TOKEN)")
        clone_source = source or settings.clone_url_template.format(repo=issue.repo)
        return self._pipeline.run(issue, source=clone_source, dry_run=settings.dry_run)
