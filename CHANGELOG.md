# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.8.2] - 2026-04-12

### Fixed

- backfill missing config keys on every session start

### Changed

- docs: announce v0.8.1 stable, add known issues and log quickstart

---


## [0.8.1] - 2026-04-12

### Fixed

- write vscode_wait to config in first-run setup wizard

### Changed

- docs: update README for current feature set

---


## [0.8.0] - 2026-04-12

### Added

- merge remote changes + add shadow_update config setting
- change shadow_update default to 'round'
- add shadow_update config setting (session vs round)
- vscode mode now blocks per-file and detects accept/reject/modified

---


## [0.7.0] - 2026-04-12

### Added

- vscode mode now blocks per-file and detects accept/reject/modified

---


## [0.6.2] - 2026-04-12

### Fixed

- clear accepted decision when a file is edited again after review

---


## [0.6.1] - 2026-04-12

### Fixed

- reopen diff when FILE_SAVED arrives too fast (stray Ctrl+S guard)

---


## [0.6.0] - 2026-04-12

### Added

- add full event logging to all hooks and libs

### Changed

- docs: add interactive mode to config table in CLAUDE.md

---


## [0.5.3] - 2026-04-12

### Fixed

- fall back to terminal review when IDE openDiff RPC fails for all files

---


## [0.5.2] - 2026-04-12

### Fixed

- always reinitialize state on SessionStart, not only when session_start is null

---


## [0.5.1] - 2026-04-12

### Fixed

- pass new_file_contents to openDiff RPC; clear stale decisions on first capture

---


## [0.5.0] - 2026-04-12

### Added

- restructure plugin — extract modules, fix file-transition, add multi-round support

---


## [0.4.1] - 2026-04-09

### Fixed

- amend version bump into triggering commit instead of chore commit

---


## [0.4.0] - 2026-04-09

### Added

- add stderr debug logging to ide.py for connection troubleshooting

---


## [0.3.3] - 2026-04-09

### Fixed

- set Stop hook timeout to 600s (0 is invalid per schema)

---


## [0.3.2] - 2026-04-09

### Fixed

- remove Stop hook timeout so openDiff can wait for user review

---


## [0.3.1] - 2026-04-09

### Fixed

- ask all reconfigure questions in a single AskUserQuestion call

---


## [0.3.0] - 2026-04-09

### Added

- add Copilot interactive provider + tests

### Fixed

- restore pause.md slash command content (was empty)
- use shadow/real as separate old/new paths in openDiff

---


## [0.2.2] - 2026-04-09

### Fixed

- clarify VS Code diff UX — Ctrl+S to accept, Revert to reject hunks

---


## [0.2.1] - 2026-04-09

### Fixed

- implement WebSocket MCP transport for openDiff (stdlib only)

---


## [0.2.0] - 2026-04-09

### Added

- add pause/resume, CI tests, slash command optimizations

### Changed

- Merge branch 'main' of https://github.com/Gorluxor/Claude-Code-Diff-Review into main

---


## [0.1.11] - 2026-04-09

### Fixed

- reconfigure slash command uses Claude UI instead of /dev/tty

---


## [0.1.10] - 2026-04-09

### Fixed

- always create config on first run; reconfigure restores config on tty failure

---


## [0.1.9] - 2026-04-09

### Fixed

- mark bin/claude-diff executable in git and bump to v0.1.9

### Changed

- chore: revert manual version bump — workflow handles versioning via tags

---


## [0.1.8] - 2026-04-09

### Fixed

- trigger first automatic release to verify full pipeline

### Changed

- chore: add workflow_dispatch to release workflow

---


## [0.1.7] - 2026-04-09

### Fixed

- **Slash commands now appear in the CLI** — `plugin.json` was missing the `"commands"` and `"skills"` declarations, so the plugin manifest never registered the commands directory. Added both fields so Claude Code discovers and registers all slash commands on install.

---

## [0.1.6] - 2026-04-09

### Added

- **Native slash commands** — `/claude-diff-review:accept`, `/claude-diff-review:restore`, `/claude-diff-review:status`, `/claude-diff-review:diff`, `/claude-diff-review:reconfigure` are now available directly in Claude Code with pre-approved tool access (no permission dialog, minimal tokens).

---

## [0.1.5] - 2026-04-09

### Added

- **First-run setup wizard** — on the very first session start (before Claude begins), an interactive prompt asks you to choose your review mode and scope. No LLM tokens used.
- **`claude-diff reconfigure`** — re-run the setup wizard at any time to change your preferences.

---

## [0.1.4] - 2026-04-09

### Added

- Shadow directory is now verified as readable and writable at the start of every session. If it is not accessible, diff tracking is automatically disabled with a clear warning — Claude is never blocked.

### Fixed

- Per-hunk accept/reject now correctly reconstructs files when a mix of hunks are accepted and rejected. Previously, a silent bug caused the wrong content to be written in mixed-decision scenarios.

### Tests

- New test coverage for shadow directory permissions, the VS Code IDE MCP integration, terminal per-hunk review (y/n/a/d shortcuts), and file reconstruction correctness.

---

## [0.1.3] - 2026-04-09

### Added

- **Native VS Code interactive review** — when the VS Code extension is active, each changed file opens in VS Code's built-in side-by-side diff editor. Use "Revert Selected Ranges" to reject individual hunks, save, and the plugin handles the rest.
- **Terminal fallback** — when VS Code is not connected, a `git add -p`-style prompt lets you accept or reject each hunk from the terminal.
- **Claude re-engagement** — if you reject or modify any changes, Claude is automatically re-engaged with a summary of exactly what you changed and why, so it can iterate.

### Changed

- `interactive` is now the default `review_mode`. No configuration needed to get per-hunk review.

---

## [0.1.2] - 2026-04-09

### Added

- Full Claude Code **plugin system** support — installable via `claude plugin marketplace add` or `--plugin-dir` for local testing.
- `CLAUDE.md` guidance so Claude Code understands the project structure when working inside this repo.

### Changed

- README updated to lead with plugin install as the primary method.

---

## [0.1.1] - 2026-04-09

### Added

- **Progressive per-file review** (`review_scope: "file"`) — as Claude moves from one file to the next, the completed file's diff opens in VS Code immediately. Review file A while Claude is still editing files B and C.
- `claude-diff config review_scope <session|file>` to switch between end-of-turn and progressive modes.
- MIT `LICENSE` file.

### Changed

- README overhauled with feature table, config reference, and architecture overview.

---

## [0.1.0] - 2026-04-09

Initial release.

### Added

- **Automatic shadow capture** — originals are saved before Claude touches anything. You always have a safe baseline to diff against or restore from.
- **Consolidated diffs at the end of Claude's turn** — instead of reviewing each edit one by one, you see one clean diff per file after Claude finishes its whole response.
- **`claude-diff` CLI** — `status`, `accept`, `restore`, `diff`, `config`, `install`, `uninstall`.
- **`review_mode` config** — `interactive` (default), `vscode`, `terminal`, `summary`.
- **Session isolation** — concurrent Claude Code sessions never interfere with each other.
- **Binary and new-file handling** — binary files are tracked but skipped from diffing; new files show as fully added.
- **One-command installer** (`install.sh`).
