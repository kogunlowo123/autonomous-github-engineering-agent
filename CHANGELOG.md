# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Container `HEALTHCHECK` that runs `ghagent policy`.

### Fixed

- `Settings` accepts field names as keyword arguments, so `Settings(github_token=...)` is no longer silently ignored.

## [0.1.0] - 2026-09-19

### Added

- Issue-to-pull-request pipeline with triage, planner, coder, tester, security reviewer and publisher
  agents, orchestrated as a conditional state machine with a retry loop.
- Dry-run default: every run writes `report.json`, `changes.diff` and `pr.md`. Live mode requires
  `--execute` and human approval and opens draft pull requests only.
- Maintainer approval label gate, optional author allowlist and prompt-injection screening for issue
  text.
- Isolated clone per run, green-baseline requirement, tests before and after the change, and feedback
  from failures into the next attempt.
- All-or-nothing exact-match edit application with protected paths, traversal and symlink checks, and
  size limits.
- Diff security review: secrets and pipe-to-shell block; dangerous calls, weakened tests and build file
  changes warn; file and line limits.
- Safe git wrapper with credentials passed by environment, GitHub REST client limited to reading issues
  and opening pull requests, scrubbed-environment test runner.
- OpenAI and Anthropic model support for planning and coding, with a heuristic planner fallback.
- `ghagent` CLI (`run`, `triage`, `policy`) with documented exit codes.
- Dockerfile, Makefile and GitHub Actions workflows for lint, format, types, tests, dependency audit,
  CodeQL and build validation.
