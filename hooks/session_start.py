#!/usr/bin/env python3
"""
SessionStart hook for claude-diff-review.

Initializes a fresh session state and cleans up stale sessions
older than 24 hours.

Exit codes:
  0 = success
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.state import (
    load_state,
    save_state,
    cleanup_old_sessions,
    get_session_dir,
)


def main():
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
