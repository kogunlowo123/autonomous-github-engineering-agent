# ADR 0003: Exact-match edits applied all-or-nothing

- Status: Accepted
- Date: 2026-09-19

## Context

Models can express a change as a unified diff, a whole-file rewrite, or targeted edits. Diffs often
have misaligned hunks; whole-file rewrites are expensive and hide small changes in noise.

## Decision

The model returns JSON: `replace` edits with an exact `search` string that must occur once in the file,
and `create` edits with full content for new files. `apply_edits` validates every edit against path
policy and limits in memory and writes only if all are valid.

## Consequences

- Application is deterministic, and rejections are specific ("found 2 matches"), which makes the retry
  feedback effective.
- A failed attempt never leaves a half-applied change.
- Edits are small and reviewable by construction, which fits the diff size limits.
- Wide refactors, moves and deletions are not expressible in v0.1. Deletion is deliberately absent so a
  model cannot "fix" a failing test by removing it.
