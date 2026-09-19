# ADR 0001: Dry run by default, maintainer gate and human approval

- Status: Accepted
- Date: 2026-09-19

## Context

An agent that reads public issues and can push code is a target. Anyone can write an issue, and a model
can be persuaded by its contents. Controls that rely on the model refusing are not reliable.

## Decision

Enforce the boundary in code:

1. Work starts only when the issue carries a maintainer-applied label (`ghagent:approved` by default).
2. Runs are dry by default: they produce a report, diff and PR text and change nothing remote.
3. Live mode needs `--execute` and an approval decision. With no terminal and no `--yes`, approval is
   declined.
4. The agent can read issues and open pull requests. It has no code path to merge, close or delete.
5. It publishes only from prefixed branches, never force-pushes, and opens drafts by default.

## Consequences

- A hostile issue cannot start a run unless a maintainer labels it, and even then the output must pass
  tests, review and a person.
- Automation in CI must opt in explicitly with `--execute --yes`.
- Maintainers do one extra step (labelling). Removing the gate is possible but must be configured
  deliberately (`GHAGENT_REQUIRED_LABEL=`).

## Alternatives considered

- **Model-level refusal only.** Rejected: not deterministic and not testable.
- **Auto-merge on green tests.** Rejected: tests pass for wrong changes, and merging is the point where a
  person should decide.
