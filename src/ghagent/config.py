"""Typed runtime configuration loaded from ``GHAGENT_*`` environment variables and ``.env``.

Defaults are conservative: runs are dry, PRs are drafts, the agent only acts on issues a
maintainer has labelled, and every limit is finite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Secrets are :class:`SecretStr` and never appear in ``repr``."""

    model_config = SettingsConfigDict(
        env_prefix="GHAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Mode
    dry_run: bool = True
    draft_pr: bool = True

    # GitHub
    github_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GHAGENT_GITHUB_TOKEN", "GITHUB_TOKEN")
    )
    github_api_url: str = "https://api.github.com"
    clone_url_template: str = "https://github.com/{repo}.git"
    push_url: str | None = None
    pr_head_owner: str | None = None
    committer_name: str = "ghagent"
    committer_email: str = "ghagent@users.noreply.github.com"

    # Trigger policy
    required_label: str | None = "ghagent:approved"
    allowed_authors: list[str] = Field(default_factory=list)

    # Model provider (needed by the coder; the planner falls back to heuristics without one)
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GHAGENT_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-4o"
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GHAGENT_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-sonnet-5"
    anthropic_max_tokens: int = Field(default=8000, gt=0)

    # Change limits
    max_files: int = Field(default=5, ge=1)
    max_diff_lines: int = Field(default=400, ge=1)
    max_file_bytes: int = Field(default=200_000, ge=1000)
    max_context_chars: int = Field(default=60_000, ge=1000)
    max_attempts: int = Field(default=2, ge=1, le=5)
    deny_paths: list[str] = Field(
        default_factory=lambda: [".git/", ".github/", ".env", "id_rsa", "id_ed25519"]
    )
    branch_prefix: str = "ghagent/"

    # Tests
    test_command: list[str] = Field(default_factory=lambda: ["python", "-m", "pytest", "-q"])
    test_timeout_seconds: int = Field(default=600, ge=1)
    require_green_baseline: bool = True

    # Output
    work_dir: Path = Path(".ghagent/work")
    out_dir: Path = Path(".ghagent/runs")
    pr_footer: str = "Opened automatically by ghagent. Review the diff carefully before merging."

    # Networking
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    retry_attempts: int = Field(default=3, ge=1)
    retry_min_wait: float = Field(default=0.5, ge=0)
    retry_max_wait: float = Field(default=8.0, ge=0)
    git_timeout_seconds: int = Field(default=300, ge=1)

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "WARNING"
    log_json: bool = True

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.retry_max_wait < self.retry_min_wait:
            raise ValueError("retry_max_wait must be >= retry_min_wait")
        if not self.branch_prefix or not self.branch_prefix.endswith("/"):
            raise ValueError("branch_prefix must be non-empty and end with '/'")
        if not self.test_command:
            raise ValueError("test_command must not be empty")
        return self
