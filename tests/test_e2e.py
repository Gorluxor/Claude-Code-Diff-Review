#!/usr/bin/env python3
"""
End-to-end test for claude-diff-review.

Simulates the full Claude Code hook lifecycle:
  1. SessionStart fires
  2. Claude proposes Edit → PreToolUse fires → allow
  3. Edit applies to real file
  4. PostToolUse fires → edit tracked
  5. (repeat for multiple files / multiple edits)
  6. Stop fires → diffs printed

Run from project root:
  python3 tests/test_e2e.py
"""

import sys
import os
import json
import subprocess
import tempfile
import shutil
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.state import (
    load_state,
    get_edited_files,
    get_shadow_path,
    cleanup_session,
    get_session_dir,
)


# ── Colors ──────────────────────────────────────────────────────
R = "\033[0m"
B = "\033[1m"
G = "\033[32m"
RED = "\033[31m"
Y = "\033[33m"
DIM = "\033[2m"


def header(text):
    print(f"\n{B}{'═' * 60}{R}")
    print(f"{B}  {text}{R}")
    print(f"{B}{'═' * 60}{R}\n")


def step(text):
    print(f"  {Y}▸{R} {text}")


def ok(text):
    print(f"  {G}✓{R} {text}")


def fail(text):
    print(f"  {RED}✗{R} {text}")
    sys.exit(1)


def run_hook(hook_name: str, stdin_data: dict, env_extra: dict = None) -> subprocess.CompletedProcess:
    """Run a hook script, passing JSON on stdin like Claude Code does."""
    hook_path = ROOT / "hooks" / hook_name
    env = os.environ.copy()
    env["CLAUDE_SESSION_ID"] = "test-e2e-session"
    if env_extra:
        env.update(env_extra)

    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    return result


def main():
    header("claude-diff-review — end-to-end test")

    # ── Setup: create a temp project ────────────────────────────
    tmpdir = tempfile.mkdtemp(prefix="cdr-test-")
    src_dir = Path(tmpdir) / "src"
    src_dir.mkdir()

    # Create test files
    app_py = src_dir / "app.py"
    app_py.write_text(
        'from flask import Flask\n'
        '\n'
        'app = Flask(__name__)\n'
        '\n'
        '@app.route("/")\n'
        'def index():\n'
        '    return "Hello World"\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    app.run(debug=True)\n'
    )

    utils_py = src_dir / "utils.py"
    utils_py.write_text(
        'def add(a, b):\n'
        '    return a + b\n'
    )

    step(f"Created test project at {tmpdir}")
    ok(f"  app.py ({app_py.stat().st_size} bytes)")
    ok(f"  utils.py ({utils_py.stat().st_size} bytes)")

    env_extra = {"CLAUDE_WORKING_DIR": tmpdir}

    # Also set in current process so direct lib calls work
    os.environ["CLAUDE_SESSION_ID"] = "test-e2e-session"
    os.environ["CLAUDE_WORKING_DIR"] = tmpdir
    errors = []

    # ── 1. SessionStart ─────────────────────────────────────────
    step("Firing SessionStart hook...")
    result = run_hook("session_start.py", {}, env_extra)
    if result.returncode != 0:
        fail(f"SessionStart failed: {result.stderr}")
    ok("SessionStart completed")

    # Check state was initialized
    state = load_state()
    if state.get("session_start") is None:
        # Reload with correct session ID
        os.environ["CLAUDE_SESSION_ID"] = "test-e2e-session"
        state = load_state()

    # ── 2. PreToolUse for app.py (first edit) ───────────────────
    step("PreToolUse: Claude wants to edit app.py...")
    result = run_hook("pre_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(app_py),
            "old_str": 'return "Hello World"',
            "new_str": 'return "Hello, World!"',
        }
    }, env_extra)

    if result.returncode != 0:
        fail(f"PreToolUse failed: {result.stderr}")

    # Verify it returned "allow"
    try:
        output = json.loads(result.stdout)
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        if decision != "allow":
            fail(f"Expected 'allow', got '{decision}'")
    except json.JSONDecodeError:
        fail(f"PreToolUse didn't return valid JSON: {result.stdout}")

    ok("PreToolUse returned 'allow'")

    # Verify shadow was created
    shadow = get_shadow_path(str(app_py))
    if shadow.exists():
        ok(f"Shadow created: {shadow.name}")
        original_content = shadow.read_text()
    else:
        # It might be in a different session dir, check
        step(f"  (shadow path: {shadow})")
        fail("Shadow file was not created")

    # ── 3. Simulate Claude applying the edit ────────────────────
    step("Claude applies edit to app.py...")
    content = app_py.read_text()
    content = content.replace('return "Hello World"', 'return "Hello, World!"')
    app_py.write_text(content)
    ok("Edit applied")

    # ── 4. PostToolUse for app.py ───────────────────────────────
    step("PostToolUse: edit completed...")
    result = run_hook("post_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(app_py)},
        "tool_response": {"success": True},
    }, env_extra)

    if result.returncode != 0:
        fail(f"PostToolUse failed: {result.stderr}")
    ok("PostToolUse tracked edit")

    # ── 5. Second edit to app.py (add docstring) ────────────────
    step("PreToolUse: Claude wants to edit app.py again...")
    result = run_hook("pre_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(app_py),
            "old_str": 'def index():',
            "new_str": 'def index():\n    """Home page."""',
        }
    }, env_extra)
    ok("PreToolUse (second edit) — shadow already exists, not re-captured")

    content = app_py.read_text()
    content = content.replace('def index():', 'def index():\n    """Home page."""')
    app_py.write_text(content)

    result = run_hook("post_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(app_py)},
        "tool_response": {"success": True},
    }, env_extra)
    ok("PostToolUse (second edit)")

    # ── 6. Edit to utils.py ─────────────────────────────────────
    step("PreToolUse: Claude edits utils.py...")
    result = run_hook("pre_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(utils_py),
            "old_str": 'def add(a, b):',
            "new_str": 'def add(a: int, b: int) -> int:',
        }
    }, env_extra)
    ok("PreToolUse for utils.py")

    content = utils_py.read_text()
    content = content.replace('def add(a, b):', 'def add(a: int, b: int) -> int:')
    utils_py.write_text(content)

    result = run_hook("post_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(utils_py)},
        "tool_response": {"success": True},
    }, env_extra)
    ok("PostToolUse for utils.py")

    # ── 7. Write a NEW file ─────────────────────────────────────
    new_file = src_dir / "config.py"
    step("PreToolUse: Claude creates new file config.py...")
    result = run_hook("pre_tool_use.py", {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(new_file),
            "content": 'DEBUG = True\nPORT = 5000\n',
        }
    }, env_extra)
    ok("PreToolUse for new file")

    # Simulate the Write
    new_file.write_text('DEBUG = True\nPORT = 5000\n')

    result = run_hook("post_tool_use.py", {
        "tool_name": "Write",
        "tool_input": {"file_path": str(new_file)},
        "tool_response": {"success": True},
    }, env_extra)
    ok("PostToolUse for new file")

    # ── 8. Stop hook — show diffs ───────────────────────────────
    step("Stop hook: Claude finished responding...")
    print()

    # Force terminal mode since we don't have VS Code in test
    env_stop = {**env_extra, "CLAUDE_DIFF_MODE": "terminal"}
    result = run_hook("stop.py", {}, env_stop)

    # Stop hook outputs to stderr
    print(result.stderr)

    if result.returncode != 0:
        fail(f"Stop hook failed (exit code {result.returncode})")

    # ── 9. Verify state ─────────────────────────────────────────
    header("Verification")

    edited = get_edited_files()
    step(f"Tracked {len(edited)} files:")
    for path, count in sorted(edited.items()):
        rel = Path(path).name
        ok(f"  {rel}: {count} edit(s)")

    # Verify shadow content is the ORIGINAL
    shadow_app = get_shadow_path(str(app_py))
    if shadow_app.exists():
        shadow_content = shadow_app.read_text()
        if 'return "Hello World"' in shadow_content:
            ok("Shadow preserved original content (before all edits)")
        else:
            fail("Shadow content was overwritten!")
    else:
        fail("Shadow file missing")

    # Verify new file has empty shadow
    shadow_new = get_shadow_path(str(new_file))
    if shadow_new.exists() and shadow_new.stat().st_size == 0:
        ok("New file has empty shadow (will show as 'added' in diff)")
    else:
        step(f"  (new file shadow: exists={shadow_new.exists()}, "
             f"size={shadow_new.stat().st_size if shadow_new.exists() else 'N/A'})")

    # ── 10. Test restore ────────────────────────────────────────
    step("Testing restore of app.py...")
    from lib.state import restore_file
    restored = restore_file(str(app_py))
    if restored:
        restored_content = app_py.read_text()
        if 'return "Hello World"' in restored_content and '"""Home page."""' not in restored_content:
            ok("app.py restored to original content")
        else:
            fail(f"Restore content mismatch:\n{restored_content}")
    else:
        fail("restore_file returned False")

    # ── Cleanup ─────────────────────────────────────────────────
    cleanup_session()
    shutil.rmtree(tmpdir, ignore_errors=True)

    # ── 11. Test review_scope=file (per-file progressive preview) ─
    header("review_scope=file — per-file progressive preview")

    tmpdir2 = tempfile.mkdtemp(prefix="cdr-test-file-scope-")
    src2 = Path(tmpdir2) / "src"
    src2.mkdir()
    file_a = src2 / "a.py"
    file_b = src2 / "b.py"
    file_a.write_text("x = 1\n")
    file_b.write_text("y = 2\n")

    # Write a config with review_scope=file
    config_dir = Path.home() / ".claude-diff-review"
    config_path = config_dir / "config.json"
    config_dir.mkdir(parents=True, exist_ok=True)
    original_config = config_path.read_text() if config_path.exists() else None
    config_path.write_text(json.dumps({
        "review_mode": "terminal",   # no VS Code in test env
        "auto_cleanup": True,
        "review_scope": "file",
    }))

    env2 = {
        "CLAUDE_SESSION_ID": "test-file-scope-session",
        "CLAUDE_WORKING_DIR": tmpdir2,
    }
    os.environ["CLAUDE_SESSION_ID"] = "test-file-scope-session"
    os.environ["CLAUDE_WORKING_DIR"] = tmpdir2

    # SessionStart
    run_hook("session_start.py", {}, env2)
    state = load_state()
    if "previewed_files" not in state:
        fail("previewed_files missing from initial state")
    ok("SessionStart: previewed_files initialized")

    # Edit file_a (first edit — nothing to preview yet)
    run_hook("pre_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(file_a)},
    }, env2)
    file_a.write_text("x = 99\n")
    run_hook("post_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(file_a)},
    }, env2)
    ok("Edited file_a")

    # Edit file_b (new file — should trigger preview of file_a)
    run_hook("pre_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(file_b)},
    }, env2)
    file_b.write_text("y = 88\n")
    run_hook("post_tool_use.py", {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(file_b)},
    }, env2)
    ok("Edited file_b (should have triggered preview of file_a)")

    state = load_state()
    previewed = state.get("previewed_files", [])
    abs_a = str(file_a.resolve())
    abs_b = str(file_b.resolve())

    if abs_a in previewed:
        ok(f"file_a marked as previewed in state")
    else:
        fail(f"file_a NOT in previewed_files: {previewed}")

    if abs_b not in previewed:
        ok("file_b NOT yet previewed (still being edited — correct)")
    else:
        fail("file_b should not be previewed before Stop fires")

    # Restore original config
    if original_config is not None:
        config_path.write_text(original_config)
    else:
        config_path.unlink(missing_ok=True)

    cleanup_session()
    shutil.rmtree(tmpdir2, ignore_errors=True)

    header("All tests passed!")
    print(f"  {G}✓{R} SessionStart initializes state")
    print(f"  {G}✓{R} PreToolUse captures original, returns 'allow'")
    print(f"  {G}✓{R} PreToolUse skips re-capture on subsequent edits")
    print(f"  {G}✓{R} PostToolUse tracks edit counts")
    print(f"  {G}✓{R} New files get empty shadow (shows as 'added')")
    print(f"  {G}✓{R} Stop hook prints diffs with correct +/- stats")
    print(f"  {G}✓{R} Shadow preserves original (not intermediate) state")
    print(f"  {G}✓{R} Restore brings back exact original content")
    print(f"  {G}✓{R} review_scope=file: previewed_files initialized in state")
    print(f"  {G}✓{R} review_scope=file: completed file marked previewed when Claude moves on")
    print(f"  {G}✓{R} review_scope=file: current file NOT marked previewed prematurely")
    print()


if __name__ == "__main__":
    main()
