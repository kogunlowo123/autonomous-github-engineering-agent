# ADR 0002: Real git operations with credentials in the environment

- Status: Accepted
- Date: 2026-09-19

## Context

The agent clones, branches, diffs, commits and pushes. Options include a git library, shelling out to
`git`, or the GitHub contents API. Credentials must not leak into process listings, config files or
logs.

## Decision

Shell out to the `git` executable without a shell, using argument lists, with prompts disabled and
timeouts. Authenticate with an `http.extraheader` supplied through `GIT_CONFIG_COUNT/KEY/VALUE`
environment variables for the single command that needs it. Never embed tokens in remote URLs. The diff
that gets reviewed is the one git itself produces from the working tree (with intent-to-add so new files
appear).

## Consequences

- The behaviour matches what a developer would see, including real clone, commit and push semantics.
  Integration tests use real repositories and a bare remote instead of mocking git.
- Tokens do not appear in argv, `.git/config` or error messages (errors are redacted as well); a test
  asserts the config file contains no token after an authenticated clone.
- `git` must be installed. `GIT_CONFIG_*` requires git 2.31 or newer.
- Work happens in a fresh clone, so the user's checkout is never touched.

## Alternatives considered

- **GitPython or dulwich.** Adds a dependency and subtle behaviour differences without reducing risk.
- **GitHub contents API for commits.** Avoids a local clone but cannot run tests, which the workflow
  requires.
