---
name: diff-review
description: Workflow awareness for claude-diff-review. Informs Claude that file edits are being tracked and will be reviewed by the user as consolidated diffs after the response. Use when editing files in a session where claude-diff-review is active.
---

# Claude Diff Review — Active

This session has **claude-diff-review** enabled. File edits are being automatically tracked.

## What this means for you

- **Edit freely** — all `Edit`, `Write`, and `MultiEdit` operations are auto-approved
- **Originals are preserved** — the user can restore any file to its pre-edit state
- **Review happens after your response** — the user will see consolidated VS Code diffs when you finish

## How to work effectively

- Make all related edits in a single response turn when possible — the user reviews them together
- You do not need to ask permission before editing files
- You do not need to show file contents before and after — the diff view handles that
- If you edit a file multiple times, only the original (pre-session) version is kept as the baseline

## User commands after your response

The user will use `claude-diff accept` or `claude-diff restore` to finalize changes. If they ask you to undo a specific file, they will run `claude-diff restore <path>` themselves — you do not need to reverse your edits manually.
