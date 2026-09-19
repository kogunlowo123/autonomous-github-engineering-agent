"""Domain models shared by every agent."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_REPO_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


class IssueCategory(str, Enum):
    """Coarse issue classes."""

    BUG = "bug"
    FEATURE = "feature"
    DOCS = "docs"
    CHORE = "chore"
    QUESTION = "question"
    SECURITY = "security"


class Issue(BaseModel):
    """A GitHub issue. Its text is untrusted input written by an arbitrary user."""

    repo: str = Field(pattern=_REPO_PATTERN)
    number: int = Field(gt=0)
    title: str = Field(max_length=300)
    body: str = Field(default="", max_length=20_000)
    labels: list[str] = Field(default_factory=list)
    author: str = ""
    url: str = ""

    @field_validator("body", mode="before")
    @classmethod
    def _none_body(cls, value: object) -> object:
        return "" if value is None else value


class Triage(BaseModel):
    """Triage agent verdict."""

    category: IssueCategory
    priority: Literal["p1", "p2", "p3"]
    actionable: bool
    reasons: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    """Planner agent output."""

    summary: str
    files_to_read: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "medium"
    source: Literal["model", "heuristic"] = "heuristic"


class Edit(BaseModel):
    """A single file edit proposed by the coder."""

    path: str = Field(min_length=1, max_length=500)
    action: Literal["replace", "create"]
    search: str | None = None
    replace: str | None = None
    content: str | None = None


class ChangeSet(BaseModel):
    """The coder's proposal."""

    summary: str = Field(min_length=1, max_length=500)
    edits: list[Edit] = Field(min_length=1, max_length=50)


class TestResult(BaseModel):
    """Outcome of one test run."""

    __test__ = False  # not a pytest test class

    command: list[str]
    exit_code: int | None
    passed: bool
    timed_out: bool = False
    output: str = ""
    duration_s: float = 0.0


class Finding(BaseModel):
    """A security or policy finding on the proposed diff."""

    rule: str
    severity: Literal["block", "warn"]
    message: str
    path: str = ""
    line: int = 0


class DiffStats(BaseModel):
    """Size of the proposed change."""

    files: int = 0
    additions: int = 0
    deletions: int = 0


class Status(str, Enum):
    """Terminal outcome of a run."""

    PR_READY = "pr_ready"
    PR_OPENED = "pr_opened"
    AWAITING_APPROVAL = "awaiting_approval"
    NEEDS_HUMAN = "needs_human"
    BASELINE_FAILING = "baseline_failing"
    NO_CHANGE = "no_change"
    EDIT_FAILED = "edit_failed"
    TESTS_FAILED = "tests_failed"
    BLOCKED_SECURITY = "blocked_security"


class TraceEvent(BaseModel):
    """One executed workflow node."""

    node: str
    detail: str = ""
    duration_ms: float = 0.0


class RunReport(BaseModel):
    """Everything a run decided and did, written to disk for audit."""

    schema_version: str = "1.0"
    tool_version: str
    repo: str
    issue: int
    dry_run: bool
    status: Status
    reasons: list[str] = Field(default_factory=list)
    triage: Triage | None = None
    plan: Plan | None = None
    changeset: ChangeSet | None = None
    baseline: TestResult | None = None
    tests: TestResult | None = None
    attempts: int = 0
    findings: list[Finding] = Field(default_factory=list)
    diff_stats: DiffStats = Field(default_factory=DiffStats)
    branch: str = ""
    pr_url: str = ""
    artifacts: dict[str, str] = Field(default_factory=dict)
    trace: list[TraceEvent] = Field(default_factory=list)
