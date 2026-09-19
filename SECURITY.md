# Security Policy

## Supported versions

Security fixes are released for the latest minor version on the `main` branch.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a vulnerability

Do not open a public issue for security reports. Use GitHub's private vulnerability reporting (the
**Report a vulnerability** button on this repository's **Security** tab) and include a description and
impact, the affected version or commit, and a minimal reproduction (issue text, repository layout,
configuration). You can expect an acknowledgement within 3 business days and a triage decision within
10 business days.

## Trust boundary

| Input | Trust |
| ----- | ----- |
| Issue title and body | Untrusted. Anyone may author them. |
| Repository contents and test output | Untrusted as instructions; trusted as code to execute only if you trust the repository |
| Model output | Untrusted. It is parsed, validated, applied under policy and reviewed before use |
| Maintainer labels | Trusted. The approval label is the gate that starts work |
| Configuration and environment | Trusted |

## Security controls

| Threat | Control | Location |
| ------ | ------- | -------- |
| Issue text steers the agent | Approval label gate, author allowlist, injection screening before any model call | `agents/triage.py`, `security.py` |
| Injection through delimiters | Closing tags for prompt sections are neutralised in untrusted text; prompts declare that text data | `agents/planner.py`, `agents/coder.py` |
| Model edits protected files | Deny list for `.git/`, `.github/`, env and key files; traversal and symlink checks | `security.PathPolicy`, `workspace.py` |
| Partial or corrupt edits | Edits validated and computed in memory first; all-or-nothing writes | `workspace.apply_edits` |
| Credentials added to the repo | Secret patterns and private-key headers block publication | `security.scan_diff` |
| Remote code execution added | `curl | sh` style patterns block publication; `eval`, `exec`, `os.system`, `shell=True`, `pickle`, disabled TLS are flagged in the PR | `security.scan_diff` |
| Tests weakened to pass | Removed assertions and disabled tests are flagged; a green baseline is required first | `security.scan_diff`, `agents/tester.py` |
| Runaway changes | Caps on files, changed lines, attempts and file size | `agents/reviewer.py`, `config.py` |
| Unapproved publication | Live mode needs `--execute` and an approval step; non-interactive use without `--yes` declines | `agents/publisher.py`, `cli.py` |
| Publishing to the wrong branch | Only `ghagent/` branches, never the base branch, never a force push, no merge code exists | `agents/publisher.py`, `gitops.py` |
| Token exposure | Passed through `GIT_CONFIG_*` environment variables; `SecretStr`; stripped from the test environment; redacted from logs, errors and test output | `gitops.py`, `runner.py`, `logging_setup.py` |
| Argument injection into git | No shell; arguments are lists; ref names are constructed from validated values | `gitops.py` |
| Markup or mentions in PR text | HTML removed, `@` mentions broken, secrets redacted | `security.neutralize_markdown` |
| Unsafe deletion of work directories | Deletion only strictly inside the configured work directory | `pipeline._remove_tree` |
| Vulnerable dependencies | `pip-audit`, Dependabot, CodeQL | `.github/` |

## Deployment guidance

- **Run in a disposable environment.** The test runner scrubs environment variables and enforces a
  timeout, but it is not a sandbox. Repository tests run with the agent's file, network and process
  privileges. Use a throwaway container or CI runner with no ambient credentials, and restrict its
  network access where possible.
- **Use a least-privilege token.** A fine-grained token limited to the target repositories with
  issue read and contents and pull request write is sufficient. The agent needs no admin, workflow or
  merge permissions.
- **Protect your default branches.** Require reviews and status checks so an agent-authored PR cannot
  merge without a person.
- **Restrict who can apply the approval label.** Whoever can label an issue can start a run.

## Known limitations

- Injection screening, secret detection and dangerous-pattern rules are heuristic.
- A repository's own tests can do anything the agent's user can do.
- The model may propose a change that passes tests and still is wrong. Human review is required.
- Reports and artifacts contain repository content; store them accordingly.
