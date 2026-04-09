---
name: diff-review
description: Workflow awareness for claude-diff-review. Informs Claude that file edits are being tracked and reviewed interactively. Use when editing files in a session where claude-diff-review is active.
---

# Claude Diff Review — Active

This session has **claude-diff-review** enabled. File edits are being automatically tracked.

## What this means for you

- **Edit freely** — all `Edit`, `Write`, and `MultiEdit` operations are auto-approved
- **Originals are preserved** — the first version of each file is kept as a shadow copy
- **Interactive review happens after your response** — the user reviews diffs with per-hunk accept/reject when you finish
- **Multi-round editing** — if the user rejects changes, you will be re-engaged with details of what was rejected/modified

## How to work effectively

- Make all related edits in a single response turn when possible — the user reviews them together
- You do not need to ask permission before editing files
- You do not need to show file contents before and after — the diff view handles that
- If you edit a file multiple times, only the original (pre-session) version is kept as the baseline
- When re-engaged after rejection: read the rejection details, acknowledge the user's edits, and ask if they want further changes

## What happens after your response

1. The Stop hook fires and opens interactive review (VS Code diff editor or terminal per-hunk review)
2. The user accepts, rejects, or modifies changes per file/hunk
3. If rejections or modifications occurred, you are re-engaged with a summary
4. If all changes are accepted, the session ends normally
5. The user runs `claude-diff accept` to finalize and clean up shadow copies
