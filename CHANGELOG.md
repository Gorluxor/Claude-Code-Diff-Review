# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
