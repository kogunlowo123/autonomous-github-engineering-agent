"""Command-line interface: ``ghagent run | triage | policy``.

Exit codes: 0 when a pull request is ready or opened, 1 when the run ended for any other reason
(needs a human, blocked, failing tests, declined), 2 for usage or runtime errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from ghagent.agents import Approver, AutoApprover
from ghagent.config import Settings
from ghagent.container import build_service
from ghagent.errors import GhagentError
from ghagent.logging_setup import configure_logging
from ghagent.models import Issue, Status
from ghagent.security import PathPolicy, redact
from ghagent.service import parse_target

_SUCCESS = {Status.PR_READY, Status.PR_OPENED}


class PromptApprover:
    """Asks the person at the terminal. Declines automatically when there is no terminal."""

    def approve(self, summary: str) -> bool:
        if not sys.stdin.isatty():
            print("No interactive terminal; declining. Use --yes to approve non-interactively.")
            return False
        print(f"About to push and open a pull request:\n  {summary}")
        return input("Proceed? [y/N] ").strip().lower() in {"y", "yes"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghagent", description="Autonomous GitHub engineering agent"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_issue_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("target", nargs="?", help="owner/repo#123 (fetched from GitHub)")
        p.add_argument("--issue-file", type=Path, help="read the issue from a JSON file instead")

    run = sub.add_parser("run", help="work on an issue (dry run unless --execute)")
    add_issue_args(run)
    run.add_argument("--source", help="clone URL or local path (default: GitHub clone URL)")
    run.add_argument("--out", type=Path, help="directory for run artifacts")
    run.add_argument("--execute", action="store_true", help="push and open a PR (needs a token)")
    run.add_argument("--yes", action="store_true", help="approve the push without prompting")

    triage = sub.add_parser("triage", help="classify an issue without touching any repository")
    add_issue_args(triage)

    sub.add_parser("policy", help="print the active safety policy")
    return parser


def _load_issue(args: argparse.Namespace, fetch: Callable[[str], Issue]) -> Issue:
    if args.issue_file:
        try:
            data = json.loads(args.issue_file.read_text(encoding="utf-8"))
            if args.target:
                repo, number = parse_target(args.target)
                data.update({"repo": repo, "number": number})
            return Issue.model_validate(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise GhagentError(f"cannot read issue file: {redact(str(exc))}") from exc
    if not args.target:
        raise GhagentError("provide owner/repo#123 or --issue-file")
    return fetch(args.target)


def _print_policy(settings: Settings) -> None:
    policy = PathPolicy(tuple(settings.deny_paths))
    rows = {
        "mode": "dry run" if settings.dry_run else "live",
        "draft pull requests": settings.draft_pr,
        "required issue label": settings.required_label or "(none)",
        "allowed authors": settings.allowed_authors or "(anyone)",
        "protected paths": [*policy.deny, "*.pem", "*.key", "*.p12", "*.pfx", "*.jks"],
        "max files": settings.max_files,
        "max changed lines": settings.max_diff_lines,
        "max attempts": settings.max_attempts,
        "branch prefix": settings.branch_prefix,
        "test command": settings.test_command,
        "require green baseline": settings.require_green_baseline,
        "merges": "never",
        "pushes to default branch": "never",
    }
    for key, value in rows.items():
        print(f"{key}: {value}")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = _parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        settings = Settings()
        configure_logging(settings.log_level, json_output=settings.log_json)
        if args.command == "policy":
            _print_policy(settings)
            return 0

        if args.command == "run":
            updates: dict[str, object] = {}
            if args.execute:
                updates["dry_run"] = False
            if args.out:
                updates["out_dir"] = args.out
            settings = settings.model_copy(update=updates)
            approver: Approver | None = None
            if args.execute:
                approver = AutoApprover() if args.yes else PromptApprover()
            service = build_service(settings, approver=approver)
            issue = _load_issue(args, service.fetch_issue)
            report = service.run(issue, source=args.source)
            print(f"Issue: {report.repo}#{report.issue}")
            print(f"Status: {report.status.value}" + (" (dry run)" if report.dry_run else ""))
            for reason in report.reasons:
                print(f"- {reason}")
            if report.branch:
                print(f"Branch: {report.branch}")
            if report.pr_url:
                print(f"Pull request: {report.pr_url}")
            for name, path in report.artifacts.items():
                print(f"{name}: {path}")
            return 0 if report.status in _SUCCESS else 1

        service = build_service(settings)
        issue = _load_issue(args, service.fetch_issue)
        verdict = service.triage(issue)
        print(f"Category: {verdict.category.value}  Priority: {verdict.priority}")
        print("Actionable: " + ("yes" if verdict.actionable else "no"))
        for reason in verdict.reasons:
            print(f"- {reason}")
        return 0 if verdict.actionable else 1
    except (GhagentError, ValidationError) as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
