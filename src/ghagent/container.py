"""Composition root: builds a :class:`RunService` from :class:`Settings`."""

from __future__ import annotations

import httpx

from ghagent.agents import (
    Approver,
    CoderAgent,
    DenyApprover,
    PlannerAgent,
    PublisherAgent,
    ReviewerAgent,
    TesterAgent,
    TriageAgent,
)
from ghagent.config import Settings
from ghagent.errors import ConfigurationError
from ghagent.github import GitHubClient
from ghagent.pipeline import RunPipeline
from ghagent.providers import AnthropicChatClient, JsonClient, LLMClient, OpenAIChatClient
from ghagent.security import PathPolicy
from ghagent.service import RunService


def _build_llm(settings: Settings, client: JsonClient) -> LLMClient | None:
    if settings.llm_provider == "openai":
        if settings.openai_api_key is None:
            raise ConfigurationError("GHAGENT_OPENAI_API_KEY must be set when llm_provider=openai")
        return OpenAIChatClient(
            client,
            api_key=settings.openai_api_key,
            model=settings.openai_chat_model,
            base_url=settings.openai_base_url,
        )
    if settings.llm_provider == "anthropic":
        if settings.anthropic_api_key is None:
            raise ConfigurationError(
                "GHAGENT_ANTHROPIC_API_KEY must be set when llm_provider=anthropic"
            )
        return AnthropicChatClient(
            client,
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            base_url=settings.anthropic_base_url,
        )
    return None


def build_service(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    approver: Approver | None = None,
    llm: LLMClient | None = None,
) -> RunService:
    """Assemble the dependency graph.

    Args:
        settings: Validated configuration.
        http_client: Optional shared client, mainly for tests using a mock transport.
        approver: Human approval gate; defaults to one that refuses everything.
        llm: Model client override (tests); otherwise built from the settings.

    Raises:
        ConfigurationError: If the selected provider is missing its API key.
    """
    json_client = JsonClient(
        http_client or httpx.Client(timeout=settings.http_timeout_seconds),
        attempts=settings.retry_attempts,
        min_wait=settings.retry_min_wait,
        max_wait=settings.retry_max_wait,
    )
    model = llm if llm is not None else _build_llm(settings, json_client)
    github = GitHubClient(json_client, token=settings.github_token, api_url=settings.github_api_url)
    policy = PathPolicy(tuple(settings.deny_paths))

    triage = TriageAgent(
        required_label=settings.required_label, allowed_authors=settings.allowed_authors
    )
    pipeline = RunPipeline(
        settings,
        triage=triage,
        planner=PlannerAgent(model, policy, max_file_bytes=settings.max_file_bytes),
        coder=CoderAgent(
            model,
            policy,
            max_files=settings.max_files,
            max_file_bytes=settings.max_file_bytes,
            max_context_chars=settings.max_context_chars,
        ),
        tester=TesterAgent(
            settings.test_command,
            timeout=settings.test_timeout_seconds,
            require_green_baseline=settings.require_green_baseline,
        ),
        reviewer=ReviewerAgent(
            max_files=settings.max_files, max_diff_lines=settings.max_diff_lines
        ),
        publisher=PublisherAgent(settings, github, approver or DenyApprover()),
        github=github,
    )
    return RunService(settings, pipeline, triage, github)
