# claude-diff-review

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-hooks-8B5CF6.svg)](https://claude.ai/code)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](https://github.com/Gorluxor/Claude-Code-Diff-Review)

**Review Claude Code edits as consolidated diffs instead of approving them one-by-one.**

Claude Code's default flow interrupts you for every file edit. `claude-diff-review` flips the model: auto-approve edits, capture originals in the background, then show you a clean diff per file in VS Code when Claude finishes. Accept what you like, restore what you don't.

---

## How it works

```
You ask Claude to refactor something
         │
         ▼
┌─ PreToolUse hook ──────────────────┐
│  Copies original to shadow/        │
│  Returns "allow" → edit proceeds   │
│  (file scope: opens prev diffs)    │
└────────────────────────────────────┘
         │
         ▼  (Claude edits 1–N files)
         │
┌─ PostToolUse hook ─────────────────┐
│  Tracks: which files, edit count   │
└────────────────────────────────────┘
         │
         ▼  (Claude finishes responding)
         │
┌─ Stop hook ────────────────────────┐
│  For each edited file:             │
│    Opens VS Code diff view         │
│  Prints summary with +/- stats     │
└────────────────────────────────────┘
         │
         ▼
  You review in VS Code's diff view
  Then: claude-diff accept
    or: claude-diff restore
```

---

## Features

- **Zero interruptions** — edits auto-approved while originals are preserved
- **Consolidated review** — one clean diff per file instead of piecemeal approvals
- **Two review scopes** — batch all diffs at end (`session`) or open progressively as Claude works (`file`)
- **Three diff modes** — VS Code side-by-side, colored terminal output, or summary only
- **Per-file restore** — reject one file without affecting others
- **Session isolation** — concurrent Claude sessions don't interfere
- **Binary-safe** — binary files are tracked but skipped from diffing

---

## Installation

### Via plugin marketplace (recommended)

```bash
claude plugin marketplace add https://github.com/Gorluxor/Claude-Code-Diff-Review
```

Then open `/plugins` inside Claude Code and install **claude-diff-review**. Restart Claude Code when prompted.

### Test locally without installing

```bash
git clone https://github.com/Gorluxor/Claude-Code-Diff-Review.git
claude --plugin-dir ./Claude-Code-Diff-Review
```

### Standalone install (no plugin system)

```bash
git clone https://github.com/Gorluxor/Claude-Code-Diff-Review.git
cd Claude-Code-Diff-Review
bash install.sh
```

Then **restart Claude Code**.

<details>
<summary>Manual standalone installation</summary>

```bash
# Copy files
mkdir -p ~/.claude-diff-review/app
cp -r hooks lib bin ~/.claude-diff-review/app/

# Link CLI
ln -sf ~/.claude-diff-review/app/bin/claude-diff ~/.local/bin/claude-diff

# Install hooks into Claude Code
claude-diff install
```

</details>

---

## Usage

Just use Claude Code normally. When it edits files, you'll see:

```
[diff-review] Captured original: app.py
[diff-review] Tracked edit #1 to app.py
[diff-review] Tracked edit #2 to app.py
```

When Claude finishes its response:

```
────────────────────────────────────────────────────────────
  ◆ claude-diff-review
  3 files changed, 7 edits total
────────────────────────────────────────────────────────────

  src/app.py        +24 -8   (3 edits)
  src/utils.py      +12 -3   (2 edits)
  tests/test_app.py +45 -0   (2 edits)

────────────────────────────────────────────────────────────
  Diffs opened in VS Code.
  To reject all changes:  claude-diff restore
  To accept and clean up: claude-diff accept
  To reject one file:     claude-diff restore <path>
────────────────────────────────────────────────────────────
```

---

## CLI reference

| Command | Description |
|---------|-------------|
| `claude-diff status` | Show tracked files and diff stats |
| `claude-diff accept` | Accept all changes, clean up shadows |
| `claude-diff restore` | Reject ALL changes, restore originals |
| `claude-diff restore src/app.py` | Reject changes to one file |
| `claude-diff diff` | Re-open all diffs in VS Code |
| `claude-diff diff src/app.py` | Open diff for one file |
| `claude-diff diff --mode terminal` | Print diffs to terminal instead |
| `claude-diff config` | Show current configuration |
| `claude-diff config <key> <value>` | Set a config value |
| `claude-diff cleanup` | Remove all session data |
| `claude-diff install` | Install/reinstall hooks |
| `claude-diff uninstall` | Remove hooks from Claude Code |

---

## Configuration

Config lives at `~/.claude-diff-review/config.json`:

```json
{
  "review_mode": "vscode",
  "auto_cleanup": true,
  "review_scope": "session"
}
```

| Key | Values | Default | Description |
|-----|--------|---------|-------------|
| `review_mode` | `vscode` \| `terminal` \| `summary` | `vscode` | How diffs are displayed |
| `review_scope` | `session` \| `file` | `session` | When diffs are opened |
| `auto_cleanup` | `true` \| `false` | `true` | Remove shadow copies after `accept` |

### `review_scope` in detail

**`session`** (default) — All diffs open together when Claude finishes its full response. Best for large refactors where you want to see everything at once.

**`file`** — As Claude moves from one file to the next, the diff for the completed file opens in VS Code immediately. You can review file A while Claude is still working on files B and C. Any remaining unreviewed files open at Stop as usual.

```bash
# Switch to per-file progressive review
claude-diff config review_scope file

# Switch back to batch review at end of turn
claude-diff config review_scope session
```

### `review_mode` in detail

**`vscode`** (default) — Opens `code --diff` for each file. Falls back to terminal if `code` is not in PATH.

**`terminal`** — Prints colored unified diffs inline. Useful in SSH sessions or when VS Code is not available.

**`summary`** — Only prints file names and +/- counts. No diff content.

```bash
claude-diff config review_mode terminal
# or per-invocation:
CLAUDE_DIFF_MODE=terminal claude-diff diff
```

---

## Architecture

```
~/.claude-diff-review/
├── app/                          # Installed application
│   ├── hooks/
│   │   ├── session_start.py      # SessionStart: init state
│   │   ├── pre_tool_use.py       # PreToolUse: capture originals, file-scope preview
│   │   ├── post_tool_use.py      # PostToolUse: track edits
│   │   └── stop.py               # Stop: show remaining diffs, print summary
│   ├── lib/
│   │   ├── __init__.py
│   │   └── state.py              # Shared state management
│   └── bin/
│       └── claude-diff           # CLI entry point
├── config.json                   # User configuration
└── sessions/                     # Per-session state (keyed by CLAUDE_SESSION_ID)
    └── <session-id>/
        ├── state.json            # Edit tracking
        └── shadow/               # Original file copies
```

### Hook events

| Hook | Matcher | What it does |
|------|---------|--------------|
| `SessionStart` | — | Initializes state, cleans sessions older than 24h |
| `PreToolUse` | `Edit\|Write\|MultiEdit` | Copies original to shadow; in `file` scope, opens diffs for completed files |
| `PostToolUse` | `Edit\|Write\|MultiEdit` | Increments per-file edit counter |
| `Stop` | — | Opens VS Code diffs, prints summary |

### Why edits land in the real file

We considered redirecting edits to temp files, but Claude needs to read back its own changes. If Claude edits line 10 then reads the file to verify, it must see the edit. So edits apply normally; the pre-edit copy is kept in `shadow/` for diffing.

### Session isolation

Each Claude Code session gets its own state directory keyed by `CLAUDE_SESSION_ID`. Concurrent sessions never interfere.

---

## Troubleshooting

### VS Code diffs not opening

- Verify `code` is in your PATH: `which code`
- macOS: open VS Code → `Cmd+Shift+P` → "Install 'code' command in PATH"
- Fallback: `claude-diff config review_mode terminal`

### Hooks not firing

- Restart Claude Code after installing
- Check hooks are registered: run `/hooks` inside Claude Code
- Verify settings: `cat ~/.claude/settings.json | python3 -m json.tool`

### Permission prompts still appearing

The installer adds `Edit`, `Write`, `MultiEdit` to `permissions.allow`. If prompts persist:

1. Check settings precedence — managed settings or `.claude/settings.local.json` may override
2. Try `Shift+Tab` in Claude Code to cycle to "auto-accept edits" mode
3. Verify: `cat ~/.claude/settings.json | grep -A5 permissions`

### Shadow copies taking too much space

```bash
claude-diff cleanup        # remove all sessions
claude-diff accept         # or just accept the current session
```

Sessions auto-clean after 24 hours.

---

## Uninstalling

```bash
claude-diff uninstall
rm -rf ~/.claude-diff-review
rm -f ~/.local/bin/claude-diff
```

---

## Running tests

```bash
python3 tests/test_e2e.py
```

The test simulates the full hook lifecycle (SessionStart → PreToolUse → edit → PostToolUse × N → Stop) against a temporary directory, verifying shadow capture, diff output, and restore.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
