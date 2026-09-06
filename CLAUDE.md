# Claude Repository Instructions

Claude must follow the shared Claude-Codex coordination protocol below for every repository task.

## Shared Coordination Protocol

1. Before acting, read `git status --short --branch` and the latest entry in `COORDINATION_LOG.md`.
2. Announce the next command, test, or file change in the visible chat before execution.
3. Append a chronological entry to `COORDINATION_LOG.md`; never rewrite or delete an earlier entry.
4. Preserve the user's full prompt, but replace credentials, tokens, private keys, and unnecessary personal information with `[REDACTED]`.
5. Record the active agent, intended action, checkpoint owner, files examined or changed, exact commands and tests, exit codes, pass/fail/skipped status, important output or errors, commit information, and blocker or handoff.
6. Record failed attempts and skipped tests with their reasons. Large outputs may be summarized, but exact commands, exit codes, and relevant error text must remain visible.
7. Do not overwrite another agent's uncommitted work. If the working tree contains unexpected changes, stop that checkpoint and hand off or ask the user before touching overlapping files.
8. One agent owns a checkpoint through implementation and verification. Commit only after `git diff --check`, repository status, relevant tests, and a staged credential-pattern scan have been recorded.
9. Make one commit per coherent checkpoint, then identify the next owner or state that the checkpoint is unassigned.
10. Because a Git commit cannot contain its own final hash, the closing entry records the base commit and marks the result as pending; the next chronological entry records the resulting hash. Git history remains the authoritative receipt.

## Required Log Entry Format

```markdown
## YYYY-MM-DDTHH:MM:SS+TZ - Agent - Entry type

- User prompt (redacted): ...
- Intended action: ...
- Checkpoint owner: ...
- Previous checkpoint commit: ...
- Files examined: ...
- Files changed: ...
- Commands and tests:
  - `exact command` -> PASS|FAIL|SKIPPED (exit code N); relevant result
- Result: PASS|FAIL|SKIPPED
- Important output/error: ...
- Base commit: ...
- Checkpoint commit: ...
- Blocker/handoff: ...
```

Use `N/A` rather than omitting a field. Simulations must be labeled `SIMULATION` and must not imply that Claude or Codex actually ran a command.
