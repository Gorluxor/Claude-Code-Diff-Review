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
    log_event,
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

    basename = os.path.basename(file_path)

    # Capture the original before Claude touches it
    was_new = capture_original(file_path)
    log_event(
        "pre_tool_use",
        "Captured original" if was_new else "Already shadowed",
        file=basename,
    )

    # If this is the first touch of the file this session, clear any stale
    # decision (e.g. "accepted" left over from a previous session when
    # SessionStart didn't run — mid-session plugin enable via /reload-plugins).
    if was_new:
        state = load_state()
        decisions = state.get("decisions", {})
        abs_path = str(Path(file_path).resolve())
        if abs_path in decisions:
            old_decision = decisions[abs_path]
            del decisions[abs_path]
            state["decisions"] = decisions
            save_state(state)
            log_event("pre_tool_use", "Cleared stale decision", file=basename, was=old_decision)

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
                log_event(
                    "pre_tool_use", "File transition — progressive preview",
                    from_file=os.path.basename(previous_file),
                    to_file=basename,
                )

        # Track what Claude is currently editing
        state["current_file"] = incoming_abs
        save_state(state)

    if was_new:
        context = f"[diff-review] Captured original: {basename}"
    else:
        context = ""

    hook_allow(
        reason=f"Shadowed {'(new)' if was_new else '(exists)'}: {file_path}",
        additional_context=context,
    )


if __name__ == "__main__":
    main()
