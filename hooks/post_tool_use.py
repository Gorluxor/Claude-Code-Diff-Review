#!/usr/bin/env python3
"""
PostToolUse hook for claude-diff-review.

Fires after every successful Edit/Write/MultiEdit. Records the edit
in the session state so the Stop hook knows which files to diff.

Exit codes:
  0 = success (no blocking)
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.state import (
    read_hook_input,
    extract_file_path,
    record_edit,
    load_state,
    save_state,
    is_paused,
    log_event,
)
from pathlib import Path


def main():
    if is_paused():
        sys.exit(0)

    hook_input = read_hook_input()
    file_path = extract_file_path(hook_input)

    if not file_path:
        sys.exit(0)

    state = load_state()
    if state.get("mode") == "auto":
        sys.exit(0)

    count = record_edit(file_path)

    basename = os.path.basename(file_path)
    log_event("post_tool_use", "Edit recorded", file=basename, count=count)

    # If this file was previously accepted, clear that decision so the new
    # edit re-enters the review queue at the next Stop hook.
    abs_path = str(Path(file_path).resolve())
    state = load_state()
    decisions = state.get("decisions", {})
    if decisions.get(abs_path) == "accepted":
        del decisions[abs_path]
        state["decisions"] = decisions
        save_state(state)
        log_event("post_tool_use", "Cleared accepted decision — re-queued for review",
                  file=basename)
        sys.stderr.write(f"[diff-review] Re-queued {basename} for review (new edit after accept)\n")

    sys.stderr.write(f"[diff-review] Tracked edit #{count} to {basename}\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
