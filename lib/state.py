"""
claude-diff-review: Shared state management for hook scripts.

Manages the shadow directory, edit tracking, and session lifecycle.
All state is stored in a session-specific temp directory to avoid conflicts
between concurrent Claude Code sessions.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional


# ──────────────────────────────────────────────────────────────────────
# Session directory
# ──────────────────────────────────────────────────────────────────────

def get_session_dir() -> Path:
    """
    Return the session-specific working directory.
    
    Uses CLAUDE_SESSION_ID env var (set by Claude Code) to isolate
    concurrent sessions. Falls back to a default if not available.
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "default")
    base = Path.home() / ".claude-diff-review" / "sessions" / session_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_shadow_dir() -> Path:
    """Return the shadow directory where originals are preserved."""
    shadow = get_session_dir() / "shadow"
    shadow.mkdir(parents=True, exist_ok=True)
    return shadow


def get_state_file() -> Path:
    """Return path to the session state JSON file."""
    return get_session_dir() / "state.json"


# ──────────────────────────────────────────────────────────────────────
# Working directory (project root)
# ──────────────────────────────────────────────────────────────────────

def get_working_dir() -> Path:
    """
    Determine the project working directory.
    
    Claude Code sets CWD to the project root. We also check
    CLAUDE_WORKING_DIR as a fallback.
    """
    wd = os.environ.get("CLAUDE_WORKING_DIR", os.getcwd())
    return Path(wd).resolve()


# ──────────────────────────────────────────────────────────────────────
# State read/write
# ──────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load the session state, returning defaults if not found."""
    sf = get_state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "edited_files": {},       # path -> edit count
        "shadow_created": [],     # paths that have shadow copies
        "binary_files": [],       # paths detected as binary
        "new_files": [],          # paths that didn't exist before
        "previewed_files": [],    # paths already opened in VS Code (file scope)
        "session_start": None,
        "auto_open_diff": True,   # open VS Code diffs automatically on Stop
        "mode": "review",         # "review" = show diffs, "auto" = skip diffs
    }


def save_state(state: dict) -> None:
    """Persist session state to disk."""
    sf = get_state_file()
    sf.write_text(json.dumps(state, indent=2))


# ──────────────────────────────────────────────────────────────────────
# Shadow file operations
# ──────────────────────────────────────────────────────────────────────

def get_shadow_path(file_path: str) -> Path:
    """
    Map a real file path to its shadow location.
    
    Preserves directory structure under .shadow/ so that
    src/foo.py -> .shadow/src/foo.py
    """
    real = Path(file_path).resolve()
    wd = get_working_dir()

    try:
        rel = real.relative_to(wd)
    except ValueError:
        # File outside project — use full path hash as prefix
        rel = Path(str(real).replace("/", "__"))

    shadow_path = get_shadow_dir() / rel
    shadow_path.parent.mkdir(parents=True, exist_ok=True)
    return shadow_path


def is_binary_file(file_path: str, sample_size: int = 8192) -> bool:
    """
    Heuristic check for binary files.
    
    Reads up to sample_size bytes and checks for null bytes.
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(sample_size)
        return b"\x00" in chunk
    except (OSError, IOError):
        return False


def capture_original(file_path: str) -> bool:
    """
    Copy the original file to shadow if not already captured.
    
    Returns True if a new copy was made, False if already existed.
    Handles: existing files, new files (empty shadow), and binary files.
    """
    state = load_state()
    real = Path(file_path).resolve()
    key = str(real)

    if key in state["shadow_created"]:
        return False

    shadow = get_shadow_path(file_path)

    if real.exists():
        if is_binary_file(str(real)):
            # Mark as binary — we'll skip diffing but still track
            state.setdefault("binary_files", []).append(key)
        shutil.copy2(str(real), str(shadow))
    else:
        # New file — create empty shadow so diff shows "added"
        shadow.write_text("")
        state.setdefault("new_files", []).append(key)

    state["shadow_created"].append(key)
    save_state(state)
    return True


def record_edit(file_path: str) -> int:
    """
    Increment the edit counter for a file.
    
    Returns the new count.
    """
    state = load_state()
    real = str(Path(file_path).resolve())
    count = state["edited_files"].get(real, 0) + 1
    state["edited_files"][real] = count
    save_state(state)
    return count


def get_edited_files() -> dict:
    """Return dict of {absolute_path: edit_count} for all edited files."""
    state = load_state()
    return state.get("edited_files", {})


# ──────────────────────────────────────────────────────────────────────
# Restoration
# ──────────────────────────────────────────────────────────────────────

def restore_file(file_path: str) -> bool:
    """
    Restore a file from its shadow copy.
    
    Returns True on success.
    """
    shadow = get_shadow_path(file_path)
    real = Path(file_path).resolve()

    if not shadow.exists():
        return False

    # If shadow is empty and file didn't exist before, remove it
    if shadow.stat().st_size == 0:
        if real.exists():
            real.unlink()
        return True

    shutil.copy2(str(shadow), str(real))
    return True


def restore_all() -> list:
    """Restore all edited files. Returns list of restored paths."""
    restored = []
    for path in get_edited_files():
        if restore_file(path):
            restored.append(path)
    return restored


# ──────────────────────────────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────────────────────────────

def cleanup_session() -> None:
    """Remove all session state and shadow files."""
    session_dir = get_session_dir()
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)


def cleanup_old_sessions(max_age_hours: int = 24) -> int:
    """
    Remove sessions older than max_age_hours.
    
    Returns number of sessions cleaned.
    """
    base = Path.home() / ".claude-diff-review" / "sessions"
    if not base.exists():
        return 0

    cleaned = 0
    cutoff = time.time() - (max_age_hours * 3600)

    for session_dir in base.iterdir():
        if session_dir.is_dir():
            state_file = session_dir / "state.json"
            mtime = state_file.stat().st_mtime if state_file.exists() else session_dir.stat().st_mtime
            if mtime < cutoff:
                shutil.rmtree(session_dir, ignore_errors=True)
                cleaned += 1

    return cleaned


# ──────────────────────────────────────────────────────────────────────
# Hook I/O helpers
# ──────────────────────────────────────────────────────────────────────

def read_hook_input() -> dict:
    """Read JSON input from stdin (provided by Claude Code to hooks)."""
    try:
        raw = sys.stdin.read()
        if raw.strip():
            return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def extract_file_path(hook_input: dict) -> Optional[str]:
    """
    Extract the target file path from hook input.
    
    Handles Edit, Write, and MultiEdit tool inputs.
    """
    tool_input = hook_input.get("tool_input", {})

    # Edit tool: has file_path
    if "file_path" in tool_input:
        return tool_input["file_path"]

    # Write tool: has file_path
    if "path" in tool_input:
        return tool_input["path"]

    # MultiEdit: has file_path
    if "file_path" in tool_input:
        return tool_input["file_path"]

    # Fallback: check environment variable
    env_path = os.environ.get("CLAUDE_TOOL_INPUT_FILE_PATH")
    if env_path:
        return env_path

    return None


def hook_allow(reason: str = "", additional_context: str = "") -> None:
    """Output JSON to allow the tool call to proceed."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": reason or "Auto-approved by claude-diff-review",
        }
    }
    if additional_context:
        output["hookSpecificOutput"]["additionalContext"] = additional_context
    print(json.dumps(output))
    sys.exit(0)


def hook_deny(reason: str) -> None:
    """Output JSON to deny the tool call."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
    sys.exit(0)
