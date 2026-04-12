# claude-diff-review

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-hooks-8B5CF6.svg)](https://claude.ai/code)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](https://github.com/Gorluxor/Claude-Code-Diff-Review)

**Review Claude Code edits with per-hunk accept/reject instead of approving them one-by-one.**

Claude Code's default flow interrupts you for every file edit. `claude-diff-review` flips the model: auto-approve edits, capture originals in the background, then open a native VS Code diff per file when Claude finishes. Accept what you like, reject individual hunks, restore what you don't — and if you reject anything, Claude is automatically re-engaged with the details.

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
│  Clears stale decisions on re-edit │
└────────────────────────────────────┘
         │
         ▼  (Claude finishes responding)
         │
┌─ Stop hook → lib/review.py ────────┐
│  interactive (default):            │
│    VS Code native diff per file    │
│    Per-hunk accept / reject        │
│    Rejections re-engage Claude     │
│  vscode: code --diff --wait        │
│  terminal: colored per-hunk y/n    │
│  summary: +/- counts only          │
└────────────────────────────────────┘
         │
         ▼
  All accepted → session ends
  Any rejected → Claude gets context
                 and iterates
```

---

## Features

- **Zero interruptions** — edits auto-approved while originals are preserved
- **Native VS Code diff** — per-hunk accept/reject in VS Code's built-in diff editor (requires Claude Code extension)
- **Terminal fallback** — `git add -p`-style per-hunk y/n review when VS Code isn't connected
- **Automatic re-engagement** — rejections and user modifications are summarised and sent back to Claude
- **Multi-round review** — previously accepted files are excluded from subsequent rounds
- **Shadow baseline modes** — `round` (diff shows only new changes since last review) or `session` (diff shows all session changes)
- **Full event log** — every hook and review decision is logged; `claude-diff log` for real-time debugging
- **Two review scopes** — batch all diffs at end (`session`) or open progressively as Claude works (`file`)
- **Fast-save guard** — stray Ctrl+S within 2s of diff opening automatically reopens the diff
- **Session isolation** — concurrent Claude sessions don't interfere
- **Binary-safe** — binary files tracked but skipped from diffing
- **Global pause/resume** — `claude-diff pause` / `claude-diff resume` to bypass all hooks temporarily

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

When Claude finishes, the Stop hook fires. In the default **interactive** mode with the Claude Code VS Code extension connected:

```
────────────────────────────────────────────────────────────
  ◆ claude-diff-review — interactive (VS Code)
  Connected to VS Code on port 46132
────────────────────────────────────────────────────────────

  Left = original · Right = Claude's version
  → Ctrl+S to accept  → Revert arrows to reject individual hunks

  ▶  src/app.py        (opening diff…)
  ✓  src/app.py        accepted
  ▶  tests/test_app.py (opening diff…)
  ~  tests/test_app.py accepted with modifications

────────────────────────────────────────────────────────────
  1 file had modifications — re-engaging Claude…
────────────────────────────────────────────────────────────
```

Claude then receives a structured summary of what was rejected or modified and can iterate.

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
| `claude-diff log` | Show session event log |
| `claude-diff log -f` | Follow log in real time |
| `claude-diff config` | Show current configuration |
| `claude-diff config <key> <value>` | Set a config value |
| `claude-diff pause` | Suspend all hooks until resumed |
| `claude-diff resume` | Re-enable hooks |
| `claude-diff cleanup` | Remove all session data |
| `claude-diff install` | Install/reinstall hooks |
| `claude-diff uninstall` | Remove hooks from Claude Code |

---

## Configuration

Config lives at `~/.claude-diff-review/config.json`. A setup wizard runs on first use to configure the key options.

```json
{
  "review_mode": "interactive",
  "interactive_provider": "claude-code",
  "review_scope": "session",
  "shadow_update": "round",
  "vscode_wait": true,
  "auto_cleanup": true
}
```

| Key | Values | Default | Description |
|-----|--------|---------|-------------|
| `review_mode` | `interactive` \| `vscode` \| `terminal` \| `summary` | `interactive` | How diffs are presented |
| `interactive_provider` | `claude-code` \| `copilot` | `claude-code` | Backend for interactive mode |
| `review_scope` | `session` \| `file` | `session` | When diffs open |
| `shadow_update` | `session` \| `round` | `round` | What the diff baseline is |
| `vscode_wait` | `true` \| `false` | `true` | Whether `vscode` mode blocks per file |
| `auto_cleanup` | `true` \| `false` | `true` | Remove shadows after `accept` |

### `review_mode` in detail

**`interactive`** (default) — Connects to the Claude Code VS Code extension via MCP. Opens VS Code's native side-by-side diff editor per file. Use Ctrl+S to accept, the revert arrows to reject individual hunks. Blocks until you close each tab, then re-engages Claude with any rejections.

Falls back to terminal per-hunk review (`git add -p` style) if no VS Code IDE connection is found.

**`vscode`** — Opens `code --diff --wait` per file. Blocks until you close the tab; detects accept/reject/modified by comparing content. Re-engages Claude on rejections. Falls back to terminal if `code` is not in PATH. Set `vscode_wait: false` for the old non-blocking fire-and-forget behavior.

**`terminal`** — Colored unified diff printed to stderr, then per-hunk y/n prompts in the terminal.

**`summary`** — File list with +/- counts only, no diff content.

```bash
claude-diff config review_mode terminal
# or per-invocation:
CLAUDE_DIFF_MODE=terminal claude
```

### `shadow_update` in detail

**`round`** (default) — After each accepted or modified review, the shadow baseline advances to the accepted file. The next diff shows only what changed since that review.

**`session`** — Shadow is captured once at session start and never updated. Every diff shows the full set of changes Claude made this conversation. Good for auditing.

```bash
claude-diff config shadow_update session   # cumulative diffs
claude-diff config shadow_update round     # incremental diffs (default)
```

### `review_scope` in detail

**`session`** (default) — All diffs open together when Claude finishes its full response.

**`file`** — As Claude moves from one file to the next, the diff for the completed file opens immediately. Remaining files open at Stop as usual.

```bash
claude-diff config review_scope file
```

### `interactive_provider` in detail

**`claude-code`** (default) — Uses the Claude Code VS Code extension's MCP `openDiff` tool. Blocking, per-file, with full decision detection and re-engagement.

**`copilot`** — Stages all edited files in git so VS Code's Source Control panel (and Copilot's "Review Changes") picks them up. Non-blocking — Claude cannot be re-engaged automatically.

---

## Architecture

```
hooks/
  session_start.py   # SessionStart: init state, first-run wizard, cleanup old sessions
  pre_tool_use.py    # PreToolUse: capture originals; file-scope progressive preview
  post_tool_use.py   # PostToolUse: track edit counts; clear stale decisions on re-edit
  stop.py            # Stop: slim dispatcher → lib/review
lib/
  state.py           # Session dirs, shadow ops, state r/w, event logging
  diff.py            # ANSI colors, diff stats, terminal diff, VS Code diff helpers
  review.py          # IDE review, terminal per-hunk, VS Code blocking, Copilot, re-engagement
  ide.py             # MCP client: SSE + WebSocket openDiff RPC to VS Code
bin/
  claude-diff        # CLI: status, accept, diff, log, config, install, pause/resume, …
tests/
  test_e2e.py        # 23 end-to-end lifecycle tests (no external deps)
```

Session state: `~/.claude-diff-review/sessions/<CLAUDE_SESSION_ID>/`

```
sessions/<id>/
  state.json     # edit tracking, decisions, round number
  events.log     # append-only hook event log
  shadow/        # original file copies (pre-session baseline)
```

### Hook events

| Hook | Matcher | What it does |
|------|---------|--------------|
| `SessionStart` | — | Initializes state, runs first-run wizard, cleans sessions older than 24h |
| `PreToolUse` | `Edit\|Write\|MultiEdit` | Captures original to shadow; in `file` scope, opens diffs for completed files |
| `PostToolUse` | `Edit\|Write\|MultiEdit` | Increments per-file edit counter; clears stale accepted decision on re-edit |
| `Stop` | — | Dispatches to review mode; handles re-engagement on rejection |

### Why edits land in the real file

We considered redirecting edits to temp files, but Claude needs to read back its own changes mid-session. So edits apply normally; the pre-edit copy is kept in `shadow/` for diffing.

---

## Troubleshooting

### Interactive mode: diffs not opening

- Verify the Claude Code VS Code extension is installed and the workspace is open
- Check the event log for connection details: `claude-diff log`
- The plugin tries SSE then WebSocket transport; look for `Found lock:` entries to confirm detection
- Fallback: `claude-diff config review_mode vscode` (uses `code --diff` instead of MCP)

### VS Code diffs not opening (`vscode` mode)

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

### Debugging with the event log

```bash
claude-diff log           # show last 50 events
claude-diff log -n 100    # show last 100 events
claude-diff log -f        # follow in real time (like tail -f)
```

Events are color-coded by hook type and show timing, file names, and decision outcomes.

### Shadow copies taking too much space

```bash
claude-diff cleanup        # remove all sessions
claude-diff accept         # or accept the current session
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

Simulates the full hook lifecycle (SessionStart → PreToolUse → edit → PostToolUse × N → Stop) against a temporary directory. 23 tests, no external dependencies.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
