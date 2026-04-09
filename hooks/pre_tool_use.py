#!/usr/bin/env python3
"""
PreToolUse hook for claude-diff-review.

Fires before every Edit/Write/MultiEdit. Captures the original file
to .shadow/ (first edit only), then returns "allow" so Claude's edit
proceeds normally.

In file-scope mode (review_scope=file), detects when Claude moves to a
different file and opens a preview diff for the completed file.

Exit codes:
  0 = allow (with JSON output)
"""

import sys
import os
import json
import subprocess
from pathlib import Path

# Add parent dir to path so we can import lib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.state import (
    read_hook_input,
    extract_file_path,
    capture_original,
    hook_allow,
    load_state,
    save_state,
    get_shadow_path,
    is_paused,
)


def _preview_completed_file(abs_path: str, review_mode: str) -> None:
    """Open a preview diff for a file Claude has finished editing."""
    shadow = get_shadow_path(abs_path)
    real = Path(abs_path)

    if not real.exists() or not shadow.exists():
        return

    if review_mode == "vscode":
        rel = real.name
        for cmd in ("code", "code-insiders"):
            try:
                subprocess.Popen(
                    [cmd, "--diff", str(shadow), str(real), "--title", f"Review: {rel}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except FileNotFoundError:
                continue


def main():
    if is_paused():
        hook_allow("claude-diff-review paused")
        return

    hook_input = read_hook_input()
    file_path = extract_file_path(hook_input)

    if not file_path:
        hook_allow("No file path detected — passthrough")
        return

    # Check if we're in "auto" mode (no review)
    state = load_state()
    if state.get("mode") == "auto":
        hook_allow("Mode: auto — skipping shadow capture")
        return

    # Capture the original before Claude touches it
    was_new = capture_original(file_path)

    # ── File-transition detection (progressive preview) ────────────────
    # When review_scope=file and Claude moves to a different file, the
    # previous file is "done" — preview it immediately.
    config_path = Path.home() / ".claude-diff-review" / "config.json"
    review_scope = "session"
    review_mode = "vscode"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            review_scope = config.get("review_scope", "session")
            review_mode = config.get("review_mode", "vscode")
        except Exception:
            pass

    if review_scope == "file":
        # Reload state (capture_original may have updated it)
        state = load_state()
        incoming_abs = str(Path(file_path).resolve())
        previous_file = state.get("current_file")

        # Detect file transition: Claude moved from one file to another
        if previous_file and previous_file != incoming_abs:
            previewed = state.get("previewed_files", [])
            if previous_file not in previewed:
                _preview_completed_file(previous_file, review_mode)
                previewed.append(previous_file)
                state["previewed_files"] = previewed

        # Track what Claude is currently editing
        state["current_file"] = incoming_abs
        save_state(state)

    if was_new:
        context = f"[diff-review] Captured original: {os.path.basename(file_path)}"
    else:
        context = ""

    hook_allow(
        reason=f"Shadowed {'(new)' if was_new else '(exists)'}: {file_path}",
        additional_context=context,
    )


if __name__ == "__main__":
    main()
