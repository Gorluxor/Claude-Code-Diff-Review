#!/usr/bin/env python3
"""
SessionStart hook for claude-diff-review.

Initializes a fresh session state and cleans up stale sessions
older than 24 hours.

On first run (no config file yet), opens a short interactive setup
wizard via /dev/tty — no LLM tokens consumed.

Exit codes:
  0 = success
"""

import sys
import os
import json
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.state import (
    load_state,
    save_state,
    cleanup_old_sessions,
    get_session_dir,
    check_shadow_dir_permissions,
    is_paused,
)


# ── ANSI (only when writing to a real terminal) ──────────────────────
BOLD  = "\033[1m"
DIM   = "\033[2m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
RESET = "\033[0m"


CONFIG_PATH = Path.home() / ".claude-diff-review" / "config.json"

_REVIEW_MODES  = ["interactive", "vscode", "terminal", "summary"]
_REVIEW_SCOPES = ["session", "file"]


def _ask(tty, prompt: str, choices: list, default: str) -> str:
    """
    Write a single-line prompt to tty, read one answer.
    Returns the default on empty input or if tty is unavailable.
    """
    choices_str = "/".join(
        BOLD + c + RESET if c == default else DIM + c + RESET
        for c in choices
    )
    tty.write(f"  {CYAN}?{RESET} {prompt} [{choices_str}]: ")
    tty.flush()
    answer = tty.readline().strip().lower()
    return answer if answer in choices else default


def _run_setup_wizard() -> None:
    """
    First-run interactive config wizard — writes ~/.claude-diff-review/config.json.
    Silently skipped if no terminal is available.
    """
    try:
        tty = open("/dev/tty", "r+")
    except Exception:
        return  # non-interactive environment — skip, use defaults

    try:
        tty.write(f"\n{BOLD}{CYAN}  ◆ claude-diff-review — first-run setup{RESET}\n")
        tty.write(f"{DIM}  ──────────────────────────────────────{RESET}\n")
        tty.write(
            f"  {DIM}interactive{RESET}  Native VS Code side-by-side diff, per-hunk accept/reject\n"
            f"  {DIM}vscode{RESET}       Open code --diff (view only, no in-editor accept/reject)\n"
            f"  {DIM}terminal{RESET}     Coloured unified diff printed to the terminal\n"
            f"  {DIM}summary{RESET}      File list with +/- counts only\n\n"
        )

        mode  = _ask(tty, "Review mode", _REVIEW_MODES,  "interactive")

        tty.write(
            f"\n  {DIM}session{RESET}  Show all diffs together when Claude finishes its turn\n"
            f"  {DIM}file{RESET}     Show each file's diff as soon as Claude moves on to the next\n\n"
        )

        scope = _ask(tty, "Review scope", _REVIEW_SCOPES, "session")

        config = {
            "review_mode":  mode,
            "review_scope": scope,
            "auto_cleanup": True,
        }
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2))

        tty.write(
            f"\n  {GREEN}✓{RESET} Config saved → {CONFIG_PATH}\n"
            f"{DIM}  ──────────────────────────────────────{RESET}\n\n"
        )
    finally:
        tty.close()


_DEFAULTS = {
    "review_mode": "interactive",
    "review_scope": "session",
    "auto_cleanup": True,
}


def _ensure_config() -> None:
    """
    Guarantee a config file always exists.

    On first run: try the interactive wizard (needs a real tty).
    If the wizard can't run (no tty, CI, hook subprocess), fall back
    to writing the defaults silently so the rest of the plugin works.
    """
    if CONFIG_PATH.exists():
        return
    try:
        _run_setup_wizard()
    except Exception:
        pass
    # If the wizard didn't create the file (no tty available), write defaults.
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(_DEFAULTS, indent=2))
        sys.stderr.write("[diff-review] Config created with defaults.\n")


def main():
    # First-run: ensure config exists (wizard or silent defaults)
    try:
        _ensure_config()
    except Exception:
        pass  # never block Claude

    # If globally paused, emit a minimal context note and exit
    if is_paused():
        print(json.dumps({"additionalContext": "[claude-diff-review is paused]"}))
        sys.exit(0)

    # Clean up old sessions (non-blocking, best-effort)
    try:
        cleaned = cleanup_old_sessions(max_age_hours=24)
        if cleaned > 0:
            sys.stderr.write(
                f"[diff-review] Cleaned {cleaned} stale session(s)\n"
            )
    except Exception:
        pass

    # Initialize session state
    state = load_state()
    if state.get("session_start") is None:
        state["session_start"] = time.time()
        state["edited_files"] = {}
        state["shadow_created"] = []
        state["previewed_files"] = []
        save_state(state)

    # Verify the shadow directory is readable and writable
    perm_ok, perm_err = check_shadow_dir_permissions()
    if not perm_ok:
        sys.stderr.write(
            f"[diff-review] ⚠  Shadow directory not accessible: {perm_err}\n"
            "[diff-review] Diff tracking disabled for this session.\n"
        )
        state["mode"] = "auto"
        save_state(state)

    session_dir = get_session_dir()
    sys.stderr.write(
        f"[diff-review] Session initialized → {session_dir.name[:8]}...\n"
    )

    # Output additional context for Claude
    output = {
        "additionalContext": (
            "[claude-diff-review is active] "
            "File edits are being tracked. The user will review "
            "consolidated diffs in VS Code after you finish."
        )
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
