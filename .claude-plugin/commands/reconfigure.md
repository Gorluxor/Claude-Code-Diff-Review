---
description: Interactively change review mode and scope via Claude
allowed-tools: Bash(claude-diff config:*), Bash(claude-diff status:*)
---

Help the user reconfigure claude-diff-review. First show the current config:

```
claude-diff config
```

Then ask the user up to three questions (one at a time using AskUserQuestion):

1. **Review mode** — which would you like?
   - `interactive` *(default)* — native VS Code side-by-side diff, accept/reject per hunk, re-engages Claude on rejection
   - `vscode` — opens `code --diff` for each file (view only, no in-editor controls)
   - `terminal` — coloured unified diff printed to the terminal
   - `summary` — file list with +/- counts only

2. **Interactive provider** — only ask this if they chose `interactive`:
   - `claude-code` *(default)* — Claude Code MCP openDiff, blocking one-file-at-a-time review
   - `copilot` — stage changes in git + VS Code Copilot "Review Changes" panel (non-blocking)

3. **Review scope** — when should diffs appear?
   - `session` *(default)* — all diffs together when Claude finishes its turn
   - `file` — each file's diff opens as soon as Claude moves on to the next file

After getting all answers, apply them:
```
claude-diff config review_mode <answer>
claude-diff config interactive_provider <answer>   # only if review_mode=interactive
claude-diff config review_scope <answer>
```

Then confirm the final config with `claude-diff config`.
