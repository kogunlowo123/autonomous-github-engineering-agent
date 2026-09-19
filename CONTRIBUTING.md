# Contributing

Thanks for helping improve this project. This guide covers the workflow and the quality bar.

## Development setup

Requirements: Python 3.10+ and `git` 2.31 or newer.

```bash
git clone <repository-url>
cd autonomous-github-engineering-agent
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Checks

Every change must pass the gates CI enforces:

```bash
make lint        # ruff check + ruff format --check
make typecheck   # mypy --strict
make cov         # pytest with a coverage gate of 80%
```

`make format` applies safe autofixes and formatting. The integration tests run nested test suites and
real git operations, so a full run takes about a minute.

## Workflow

1. Open an issue for anything larger than a small fix so the design can be discussed first.
2. Branch from `main`: `feature/<short-name>` or `fix/<short-name>`.
3. Keep commits focused, with imperative subjects.
4. Add or update tests. Bug fixes need a regression test that fails without the fix.
5. Update `CHANGELOG.md` under **Unreleased** and any affected documentation.
6. Open a pull request describing the problem, the approach and how you verified it.

## Code standards

- Python 3.10+, fully type-annotated, `mypy --strict` clean.
- Docstrings explain behaviour, not restate names.
- Errors raised deliberately derive from `GhagentError`.
- Never log or persist secrets. Route user-visible error text through `security.redact`.
- Collaborators are injected through constructors; wiring lives in `container.py`.
- Tests run offline. Use real temporary git repositories and `httpx.MockTransport`
  (see `tests/conftest.py`), not network calls.

## Changes to safety controls

The controls in [SECURITY.md](SECURITY.md) are the point of this project. A pull request that weakens one
(a broader path allowlist, a relaxed limit, publishing without approval, reading another untrusted
input channel) needs an explicit rationale and a test showing the remaining protection. Additions
should come with a test that demonstrates the attack they prevent.

## Adding a diff rule

Add a pattern to `_BLOCK_RULES` or `_WARN_RULES` in `security.py` with a clear message. Add one test
that triggers it and one that shows a similar safe line does not.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not file public issues for vulnerabilities.

## License

By contributing you agree that your contributions are licensed under the MIT License.
