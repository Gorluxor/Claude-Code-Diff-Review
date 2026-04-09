#!/usr/bin/env python3
"""
PreToolUse hook for claude-diff-review.

Fires before every Edit/Write/MultiEdit. Captures the original file
to .shadow/ (first edit only), then returns "allow" so Claude's edit
proceeds normally.

In file-scope mode (review_scope=file), also opens VS Code diffs for
any previously edited files when Claude moves on to a new file.

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


def _open_vscode_diff_bg(abs_path: str) -> None:
    """Open VS Code diff for abs_path vs its shadow in the background."""
    shadow = get_shadow_path(abs_path)
    real = Path(abs_path)
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
        # Can't determine file — allow anyway, don't block Claude
        hook_allow("No file path detected — passthrough")
        return

    # Check if we're in "auto" mode (no review)
    state = load_state()
    if state.get("mode") == "auto":
        hook_allow("Mode: auto — skipping shadow capture")
        return

    # Capture the original before Claude touches it
    was_new = capture_original(file_path)

    # ── Per-file progressive preview ──────────────────────────────────
    # When review_scope=file and Claude just moved to a new file, open
    # VS Code diffs for all previously edited but not-yet-previewed files.
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

    if review_scope == "file" and was_new:
        # Claude moved to a new file — preview completed files.
        # Reload state because capture_original() may have updated it.
        state = load_state()
        incoming_abs = str(Path(file_path).resolve())
        previewed = set(state.get("previewed_files", []))
        newly_previewed = []

        for edited_abs in state.get("edited_files", {}):
            if edited_abs in previewed:
                continue
            if edited_abs == incoming_abs:
                # Don't preview the file Claude is about to start — not done yet
                continue
            if review_mode == "vscode":
                _open_vscode_diff_bg(edited_abs)
            newly_previewed.append(edited_abs)

        if newly_previewed:
            state["previewed_files"] = list(previewed | set(newly_previewed))
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
