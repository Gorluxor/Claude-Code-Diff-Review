# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## Project Overview

**claude-diff-review** is a Claude Code hooks-based tool that auto-approves file edits, captures originals in a shadow directory, and opens consolidated VS Code diffs when Claude finishes its response. It is distributed as a Claude Code plugin installable via the marketplace.

## Running Tests

```bash
python3 tests/test_e2e.py
```

The e2e test simulates the full hook lifecycle (SessionStart → PreToolUse → edit → PostToolUse → Stop) against a temporary directory. No external dependencies required.

To enforce tests locally before every push:

```bash
git config core.hooksPath .githooks
```

CI also runs tests on every push and blocks the release job if they fail (`.github/workflows/release.yml`).

## Architecture

```
bin/claude-diff       # CLI: status, accept, diff, config, install, uninstall, pause, resume
hooks/
  session_start.py   # SessionStart: init state, clean sessions older than 24h
  pre_tool_use.py    # PreToolUse: capture originals; file-transition progressive preview
  post_tool_use.py   # PostToolUse: track per-file edit counts
  stop.py            # Stop: slim dispatcher → delegates to lib/review
lib/
  state.py           # Shared: session dirs, shadow ops, state read/write, hook I/O, round mgmt
  diff.py            # Display: ANSI colors, format_path, diff stats, terminal/VS Code diff
  review.py          # Review: IDE review, terminal per-hunk, copilot, re-engagement messages
  ide.py             # MCP client: SSE/WebSocket openDiff RPC to VS Code
tests/
  test_e2e.py        # End-to-end lifecycle test (23 tests)
install.sh           # One-command installer
```

State is stored per-session under `~/.claude-diff-review/sessions/<CLAUDE_SESSION_ID>/`.

## Key Conventions

- **Python 3.8+**, no third-party dependencies
- File naming: `snake_case.py` for Python, `kebab-case` for shell/CLI
- Hooks must always exit 0 on non-critical errors — never block Claude unexpectedly
- Hook scripts receive JSON on stdin (Claude Code format); output JSON to stdout for PreToolUse decisions
- All stderr output from hooks is visible to the user in Claude Code's verbose mode (`Ctrl+O`)
- Shadow copies are taken only on the first edit to a file; subsequent edits accumulate in the real file

## Configuration

User config lives at `~/.claude-diff-review/config.json`:

| Key | Values | Default |
|-----|--------|---------|
| `review_mode` | `interactive` \| `vscode` \| `terminal` \| `summary` | `interactive` |
| `review_scope` | `session` \| `file` | `session` |
| `vscode_wait` | `true` \| `false` | `true` |
| `shadow_update` | `session` \| `round` | `round` |
| `auto_cleanup` | `true` \| `false` | `true` |

## Plugin Installation

```bash
# Test locally without installing
claude --plugin-dir ./Claude-Code-Diff-Review

# Add repo as a marketplace and install via /plugins browser
claude plugin marketplace add https://github.com/Gorluxor/Claude-Code-Diff-Review

# Or standalone (no plugin system needed)
git clone https://github.com/Gorluxor/Claude-Code-Diff-Review.git
cd Claude-Code-Diff-Review
bash install.sh
```

## Plugin Structure

```
.claude-plugin/plugin.json   # Plugin manifest (name, version, author)
skills/diff-review/SKILL.md  # Skill: informs Claude that diff tracking is active
hooks/hooks.json             # Hook declarations (SessionStart, Pre/PostToolUse, Stop)
hooks/*.py                   # Hook implementation scripts
bin/claude-diff              # CLI executable (added to PATH when plugin is enabled)
lib/state.py                 # Shared state management
```
