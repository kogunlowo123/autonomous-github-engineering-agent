"""Exception hierarchy for the ghagent package."""

from __future__ import annotations


class GhagentError(Exception):
    """Base class for all errors raised deliberately by ghagent."""


class ConfigurationError(GhagentError):
    """Raised when settings are missing, inconsistent or invalid."""


class PolicyError(GhagentError):
    """Raised when an action violates a safety policy."""


class GitError(GhagentError):
    """Raised when a git command fails."""


class GitHubError(GhagentError):
    """Raised when the GitHub API returns an unexpected result."""


class EditError(GhagentError):
    """Raised when proposed edits cannot be applied safely."""


class ProviderError(GhagentError):
    """Raised when an upstream provider returns a non-retryable failure."""


class TransientProviderError(ProviderError):
    """Raised for retryable upstream failures such as rate limits or 5xx responses."""


class GraphError(GhagentError):
    """Raised when the workflow graph is mis-wired or exceeds its step budget."""
