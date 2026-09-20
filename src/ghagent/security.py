"""Safety policy: secret redaction, injection screening, path rules and diff scanning.

The agent acts on text written by strangers (issues) and produced by a model (patches). This module
holds the deterministic checks that sit between those and the repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ghagent.models import DiffStats, Finding

REDACTION = "[REDACTED]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END "
        r"(?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"]?"
        r"(?!\[REDACTED\])[^\s'\",;]{6,}"
    ),
)
_PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")

_INJECTION: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any)\b"
        r"[^.\n]{0,40}\b(instructions?|rules?|prompts?|guidelines?)\b",
        re.IGNORECASE,
    ),
    "role_reassignment": re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    "prompt_exfiltration": re.compile(
        r"\b(reveal|print|show|repeat|leak|output|send)\b[^.\n]{0,40}\b(system\s+prompt|api\s+key|"
        r"secrets?|tokens?|credentials?|environment\s+variables?)\b",
        re.IGNORECASE,
    ),
    "role_tags": re.compile(r"<\s*/?\s*(system|assistant|developer)\s*>", re.IGNORECASE),
    "agent_directive": re.compile(
        r"\b(ai|assistant|agent|bot)\b[^.\n]{0,30}\b(must|should|needs? to)\b[^.\n]{0,60}"
        r"\b(disable|skip|bypass|delete|exfiltrate|push\s+to\s+main|merge)\b",
        re.IGNORECASE,
    ),
}
_MENTION = re.compile(r"(?<![\w`])@(?=\w)")
_HTML_TAG = re.compile(r"<[^>\n]{1,200}>")
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks")


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace credential-shaped substrings and return the text with the substitution count."""
    total = 0
    for pattern in _SECRET_PATTERNS:
        text, count = pattern.subn(_replacement, text)
        total += count
    return text, total


def _replacement(match: re.Match[str]) -> str:
    key = re.match(r"(?i)(password|passwd|secret|api[_-]?key|token)\s*[:=]", match.group(0))
    return f"{key.group(0)} {REDACTION}" if key else REDACTION


def redact(text: str) -> str:
    """Return ``text`` with credential-shaped substrings replaced."""
    return redact_secrets(text)[0]


def scan_injection(text: str) -> list[str]:
    """Return the names of every injection heuristic that matches ``text``."""
    return [name for name, pattern in _INJECTION.items() if pattern.search(text)]


def neutralize_markdown(text: str) -> str:
    """Make model- or user-derived text safe to place in a PR body.

    Mentions are broken (so nobody is pinged), HTML tags are removed and secrets are redacted.
    """
    text = _HTML_TAG.sub("", text)
    text = _MENTION.sub("@​", text)
    return redact(text)


@dataclass(frozen=True)
class PathPolicy:
    """Decides which repository paths the agent may modify."""

    deny: tuple[str, ...] = field(default_factory=tuple)

    def check(self, path: str) -> str | None:
        """Return a reason if ``path`` must not be edited, otherwise ``None``."""
        normalized = path.replace("\\", "/")
        if not normalized or "\x00" in normalized:
            return "empty or invalid path"
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
            return "absolute paths are not allowed"
        parts = PurePosixPath(normalized).parts
        if ".." in parts:
            return "path traversal is not allowed"
        name = parts[-1]
        if name.lower().endswith(_SENSITIVE_SUFFIXES):
            return "key and certificate files are protected"
        for entry in self.deny:
            if entry.endswith("/"):
                directory = entry[:-1]
                if directory in parts[:-1] or normalized.startswith(entry):
                    return f"path is under protected directory {entry}"
            elif name == entry or name.startswith(entry + ".") or normalized == entry:
                return f"{entry} is protected"
        return None


# diff scanning

_BLOCK_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "pipe-to-shell",
        re.compile(r"(?i)\b(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(ba|z)?sh\b"),
        "downloads and executes remote code",
    ),
)
_WARN_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("eval", re.compile(r"\beval\s*\("), "uses eval()"),
    ("exec", re.compile(r"(?<![.\w])exec\s*\("), "uses exec()"),
    ("os-system", re.compile(r"\bos\.system\s*\("), "runs a shell command with os.system"),
    ("shell-true", re.compile(r"shell\s*=\s*True"), "enables shell=True"),
    ("pickle", re.compile(r"\bpickle\.loads?\s*\("), "deserialises with pickle"),
    ("yaml-load", re.compile(r"\byaml\.load\s*\((?!.*Safe)"), "uses yaml.load without SafeLoader"),
    ("tls-verify", re.compile(r"verify\s*=\s*False"), "disables TLS verification"),
    ("chmod-777", re.compile(r"chmod\s+(-R\s+)?0?777|0o777"), "grants world-writable permissions"),
    (
        "test-disabled",
        re.compile(r"@pytest\.mark\.(skip|xfail)|\bpytest\.(skip|xfail)\s*\(|@unittest\.skip"),
        "disables a test",
    ),
)
_BUILD_FILES = re.compile(
    r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile|poetry\.lock|"
    r"package(-lock)?\.json|yarn\.lock|go\.(mod|sum)|Cargo\.(toml|lock)|Dockerfile[^/]*|"
    r"docker-compose[^/]*\.ya?ml|Makefile|\.pre-commit-config\.yaml)$"
)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _is_test_path(path: str) -> bool:
    lowered = path.lower()
    return bool(
        re.search(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.spec\.", lowered)
    )


def scan_diff(diff: str) -> tuple[list[Finding], DiffStats]:
    """Scan a unified diff and return findings plus size statistics."""
    findings: list[Finding] = []
    stats = DiffStats()
    path = ""
    line_no = 0
    added_asserts = removed_asserts = 0
    in_hunk = False
    flagged_build: set[str] = set()

    def close_file() -> None:
        nonlocal added_asserts, removed_asserts
        if path and _is_test_path(path) and removed_asserts > added_asserts:
            findings.append(
                Finding(
                    rule="assertions-removed",
                    severity="warn",
                    message=f"removes more assertions ({removed_asserts}) than it adds ({added_asserts})",
                    path=path,
                )
            )
        added_asserts = removed_asserts = 0

    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            close_file()
            path, line_no, in_hunk = "", 0, False
            stats.files += 1
        elif raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            findings.append(
                Finding(
                    rule="binary-change",
                    severity="block",
                    message="binary files are not allowed",
                    path=path,
                )
            )
        elif not in_hunk and raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                findings.append(
                    Finding(
                        rule="file-deleted", severity="warn", message="a file is deleted", path=path
                    )
                )
            else:
                path = target[2:] if target.startswith("b/") else target
                if _BUILD_FILES.search(path) and path not in flagged_build:
                    flagged_build.add(path)
                    findings.append(
                        Finding(
                            rule="build-config",
                            severity="warn",
                            message="dependency or build configuration changed",
                            path=path,
                        )
                    )
        elif not in_hunk and raw.startswith("--- "):
            if raw[4:].strip() != "/dev/null" and not path:
                path = raw[4:].strip().removeprefix("a/")
        elif match := _HUNK.match(raw):
            line_no = int(match.group(1)) - 1
            in_hunk = True
        elif raw.startswith("+"):
            line_no += 1
            stats.additions += 1
            text = raw[1:]
            if "assert" in text:
                added_asserts += 1
            _scan_added_line(text, path, line_no, findings)
        elif raw.startswith("-"):
            stats.deletions += 1
            if "assert" in raw:
                removed_asserts += 1
        elif raw.startswith(" "):
            line_no += 1
    close_file()
    return findings, stats


def _scan_added_line(text: str, path: str, line_no: int, findings: list[Finding]) -> None:
    secret = any(p.search(text) for p in _SECRET_PATTERNS) or _PRIVATE_KEY_HEADER.search(text)
    if secret:
        findings.append(
            Finding(
                rule="secret",
                severity="block",
                message="adds what looks like a credential",
                path=path,
                line=line_no,
            )
        )
    for rule, pattern, message in _BLOCK_RULES:
        if pattern.search(text):
            findings.append(
                Finding(rule=rule, severity="block", message=message, path=path, line=line_no)
            )
    for rule, pattern, message in _WARN_RULES:
        if pattern.search(text):
            findings.append(
                Finding(rule=rule, severity="warn", message=message, path=path, line=line_no)
            )
