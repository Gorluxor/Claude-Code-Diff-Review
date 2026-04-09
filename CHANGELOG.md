# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.1.3] - 2026-04-09

### Added

- **Interactive review mode** (`review_mode: "interactive"`) — replaces passive diff viewing with a GitHub Copilot-style per-hunk accept/reject workflow:
  - Stop hook rewrites each changed file in-place with Git conflict markers (`<<<<<<< original` / `=======` / `>>>>>>> claude`)
  - VS Code opens every changed file; inline **Accept Incoming** / **Accept Current** buttons appear per hunk
  - Terminal waits for Enter; resolves remaining (unresolved) markers by restoring the original side
  - If any hunks were rejected, the Stop hook outputs `{"decision": "block", "reason": "..."}` to re-engage Claude with a structured summary of exactly what was rejected and why
- **`claude-diff finalize`** — standalone conflict resolver for when VS Code is closed before pressing Enter, or when you want to finalize without looping back to Claude; prints accepted/rejected counts per file
- `interactive` is now the **default `review_mode`** for new installs

### Changed

- `install.sh` default config changed from `review_mode: "vscode"` to `review_mode: "interactive"`
- `bin/claude-diff` `load_config()` default changed to `"interactive"`
- `claude-diff config` legend now documents the `interactive` mode

### Internal

- `lib/state.py`: added `import difflib`; added `"conflict_counts": {}` to session state defaults; new `write_conflict_markers(shadow, real) -> int` and `resolve_conflict_markers(real) -> dict` functions
- `hooks/stop.py`: added `import json`; imports `write_conflict_markers`, `resolve_conflict_markers`; new `run_interactive_review()` and `_build_rejection_message()` functions; `main()` dispatches to interactive branch before the existing vscode/terminal/summary flow

---

## [0.1.2] - 2026-04-09

### Added

- **Claude Code plugin system compliance** — repo now follows the official plugin spec:
  - `.claude-plugin/plugin.json` — plugin manifest (name, version, description, author, homepage, repository, license)
  - `hooks/hooks.json` — hook declarations in the standard plugin format; replaces manual `settings.json` injection for plugin-based installs
  - `skills/diff-review/SKILL.md` — skill that informs Claude it is operating in a tracked session, so it edits freely without unnecessary permission checks
- **`CLAUDE.md`** — root-level guidance for Claude Code when working in this repository (architecture, conventions, test command, plugin install instructions)
- README Installation section now leads with `claude plugin marketplace add` and `--plugin-dir` for local testing, with standalone `install.sh` as a fallback

### Changed

- README Installation section restructured: plugin marketplace install is now the primary method

### Tests

- Added test section 11: `review_scope=file` per-file progressive preview
  - Verifies `previewed_files` is initialized in state on SessionStart
  - Verifies completed file is marked previewed when Claude moves to a new file
  - Verifies the file currently being edited is not marked previewed prematurely

---

## [0.1.1] - 2026-04-09

### Added

- **`review_scope` config option** — controls when diffs are shown:
  - `session` (default) — all diffs open together at the end of Claude's turn (existing behavior)
  - `file` — progressive mode: as Claude moves from one file to the next, the completed file's diff opens in VS Code immediately, so you can review file A while Claude is still working on files B and C
- `claude-diff config review_scope <session|file>` to switch modes at any time
- Config legend printed at the bottom of `claude-diff config` output
- Footer note in Stop output indicating how many files were already previewed progressively
- MIT `LICENSE` file

### Changed

- `install.sh` default config now includes `review_scope: "session"`
- README completely overhauled: shields.io badges, feature table, detailed config reference, collapsible manual install section, architecture table, `review_scope` docs
- `claude-diff config` output now shows valid values for each key

### Internal

- `lib/state.py`: added `"previewed_files": []` to session state defaults
- `hooks/session_start.py`: resets `previewed_files` on session init
- `hooks/pre_tool_use.py`: per-file progressive preview logic — opens VS Code diffs for completed files when Claude starts editing a new file; new `_open_vscode_diff_bg` helper
- `hooks/stop.py`: reads `review_scope` from config; skips already-previewed files from VS Code open calls while still including them in the summary

---

## [0.1.0] - 2026-04-09

Initial release. Full working implementation of the shadow-capture + consolidated diff review flow.

### Added

- **PreToolUse hook** (`Edit|Write|MultiEdit` matcher) — captures the original file to a session-scoped shadow directory before each edit; returns `"allow"` so Claude's edit proceeds unblocked
- **PostToolUse hook** — tracks per-file edit counts in session state
- **Stop hook** — when Claude finishes its response, opens `code --diff <shadow> <real>` for every edited file and prints a styled summary with `+`/`-` line counts and edit counts
- **SessionStart hook** — initializes fresh session state, cleans sessions older than 24 hours
- **`claude-diff` CLI** with subcommands:
  - `status` — show tracked files and diff stats for the current session
  - `accept` — accept all changes and clean up shadow copies
  - `restore [path]` — restore one file or all files to their pre-edit originals
  - `diff [path] [--mode]` — re-open VS Code diffs or print terminal diffs on demand
  - `config [key] [value]` — view or set configuration
  - `cleanup` — remove all session data
  - `install [--settings]` — inject hooks into `~/.claude/settings.json`
  - `uninstall [--settings]` — remove hooks from settings
- **`review_mode`** config option: `vscode` (default), `terminal`, `summary`
- **`auto_cleanup`** config option: remove shadow copies after `accept` (default: `true`)
- **`CLAUDE_DIFF_MODE`** env var to override `review_mode` per-invocation
- **Binary file detection** — binary files are tracked but skipped from diffing
- **New file handling** — files created by Claude get an empty shadow so diffs show them as fully added
- **Session isolation** — each Claude Code session gets its own state directory keyed by `CLAUDE_SESSION_ID`; concurrent sessions never interfere
- **Auto session cleanup** — sessions older than 24 hours are removed on the next SessionStart
- **`install.sh`** one-command installer: checks prerequisites (Python 3, `code` CLI), copies files, links CLI into `~/.local/bin/`, writes default config, injects hooks
- **Settings merge safety** — `install` adds hooks alongside existing ones; never overwrites user's existing hooks, deny rules, or permission settings
- End-to-end test (`tests/test_e2e.py`) simulating the full hook lifecycle against a temporary project

### Architecture decisions

- Edits land in the real file (not a temp copy) so Claude can read back its own changes mid-task
- Shadow copy is taken only on the first edit to a file; subsequent edits to the same file accumulate in the real file and are compared against the single original snapshot
- Stop hook chosen over per-edit diffs to consolidate Claude's typical 3–5 edits per file into one clean review unit
