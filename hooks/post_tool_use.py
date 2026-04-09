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
)


def main():
    hook_input = read_hook_input()
    file_path = extract_file_path(hook_input)

    if not file_path:
        sys.exit(0)

    state = load_state()
    if state.get("mode") == "auto":
        sys.exit(0)

    count = record_edit(file_path)

    # Optional: output additional context so Claude knows the edit was tracked
    # (This shows up in verbose mode with Ctrl+O)
    basename = os.path.basename(file_path)
    sys.stderr.write(f"[diff-review] Tracked edit #{count} to {basename}\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
