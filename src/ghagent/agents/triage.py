"""Triage agent: decides whether an issue is safe and suitable for automated work."""

from __future__ import annotations

import re

from ghagent.agents.state import RunState
from ghagent.models import Issue, IssueCategory, Status, Triage
from ghagent.security import scan_injection

_SECURITY = re.compile(
    r"\b(vulnerab\w*|cve-\d|exploit\w*|rce|xss|csrf|sql\s*injection|credential\s+leak\w*|"
    r"security\s+(issue|bug|flaw)|privilege\s+escalation|auth(entication)?\s+bypass)\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(
    r"^\s*(how|why|what|where|can|is|does|do)\b.*\?\s*$", re.IGNORECASE | re.DOTALL
)
_DOCS = re.compile(r"\b(typo|documentation|docs?|readme|docstring|comment)\b", re.IGNORECASE)
_BUG = re.compile(
    r"\b(bug|error|crash\w*|fails?|failing|exception|traceback|regression|broken|wrong|incorrect)\b",
    re.IGNORECASE,
)
_FEATURE = re.compile(
    r"\b(add|support|implement|feature|enhancement|allow|option)\b", re.IGNORECASE
)
_P1 = re.compile(r"\b(crash\w*|data\s+loss|outage|regression|corrupt\w*)\b", re.IGNORECASE)
_MIN_BODY_CHARS = 20


def classify(issue: Issue) -> tuple[IssueCategory, str]:
    """Return the category and priority for ``issue`` using surface patterns."""
    text = f"{issue.title}\n{issue.body}"
    if _SECURITY.search(text):
        return IssueCategory.SECURITY, "p1"
    if _QUESTION.match(issue.title) and len(issue.body) < 400:
        return IssueCategory.QUESTION, "p3"
    if _BUG.search(text):
        return IssueCategory.BUG, "p1" if _P1.search(text) else "p2"
    if _DOCS.search(issue.title):
        return IssueCategory.DOCS, "p3"
    if _FEATURE.search(text):
        return IssueCategory.FEATURE, "p2"
    return IssueCategory.CHORE, "p3"


class TriageAgent:
    """Applies trigger policy, screens for injection and classifies the issue."""

    def __init__(self, *, required_label: str | None, allowed_authors: list[str]) -> None:
        self._required_label = required_label
        self._allowed = {a.lower() for a in allowed_authors}

    def evaluate(self, issue: Issue) -> Triage:
        """Return the triage verdict for ``issue``."""
        category, priority = classify(issue)
        reasons: list[str] = []

        if self._required_label and self._required_label not in issue.labels:
            reasons.append(f"issue is missing the approval label '{self._required_label}'")
        if self._allowed and issue.author.lower() not in self._allowed:
            reasons.append(f"author '{issue.author}' is not in the allowed list")
        findings = scan_injection(f"{issue.title}\n{issue.body}")
        if findings:
            reasons.append(f"issue text matches prompt-injection patterns: {', '.join(findings)}")
        if category is IssueCategory.SECURITY:
            reasons.append("security-sensitive issues are routed to a human")
        if category is IssueCategory.QUESTION:
            reasons.append("issue reads as a question, not a change request")
        if len(issue.body.strip()) < _MIN_BODY_CHARS:
            reasons.append("issue body is too short to act on")

        return Triage(category=category, priority=priority, actionable=not reasons, reasons=reasons)

    def run(self, state: RunState) -> RunState:
        state.triage = self.evaluate(state.issue)
        if state.triage.actionable:
            state.note = f"{state.triage.category.value} {state.triage.priority}, actionable"
        else:
            state.halt(Status.NEEDS_HUMAN, "; ".join(state.triage.reasons))
        return state
