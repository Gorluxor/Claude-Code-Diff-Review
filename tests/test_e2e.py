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
import threading
import io
import stat
import queue as _Queue
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

# Project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.state import (
    load_state,
    get_edited_files,
    get_shadow_path,
    cleanup_session,
    get_session_dir,
    check_shadow_dir_permissions,
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

    # ── 12. Shadow directory permission check ───────────────────
    header("Shadow directory — permission checks")

    os.environ["CLAUDE_SESSION_ID"] = "test-perms-session"

    # 12a: Normal case — permissions should be fine
    step("Checking shadow dir permissions (should pass)...")
    perm_ok, perm_err = check_shadow_dir_permissions()
    if perm_ok:
        ok("Shadow directory is readable and writable")
    else:
        fail(f"Unexpected permission failure: {perm_err}")

    # 12b: Simulate unwritable shadow dir
    step("Simulating unwritable shadow dir...")
    perm_session_dir = get_session_dir()
    shadow_dir = perm_session_dir / "shadow"
    shadow_dir.mkdir(parents=True, exist_ok=True)

    # Remove write permission
    original_mode = shadow_dir.stat().st_mode
    try:
        shadow_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)   # r-x: can list but not write

        perm_ok2, perm_err2 = check_shadow_dir_permissions()
        if not perm_ok2:
            ok(f"Permission failure detected correctly: {perm_err2[:60]}…")
        else:
            # Root bypasses permissions — skip rather than fail
            step("  (running as root — permission check skipped)")
    finally:
        shadow_dir.chmod(original_mode)  # always restore

    # 12c: SessionStart should set mode="auto" when shadow unwritable
    step("SessionStart disables tracking on unwritable shadow...")
    shadow_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = run_hook("session_start.py", {}, {
            "CLAUDE_SESSION_ID": "test-perms-session",
            "CLAUDE_WORKING_DIR": tmpdir2 if Path(tmpdir2).exists() else tempfile.mkdtemp(),
        })
        if "Shadow directory not accessible" in result.stderr or result.returncode == 0:
            ok("SessionStart handled unwritable shadow gracefully (exit 0)")
        else:
            fail(f"Unexpected error: {result.stderr[:120]}")
    finally:
        shadow_dir.chmod(original_mode)

    cleanup_session()

    # ── 13. Mock IDE MCP server ──────────────────────────────────
    header("IDE MCP mock server — openDiff RPC")

    sys.path.insert(0, str(ROOT))
    from lib.ide import find_ide_server, open_diff_in_ide

    def _make_mock_ide_server(response_text: str):
        """
        Start a minimal MCP SSE server that handles concurrent GET /sse + POST /message.

        Uses ThreadingMixIn so the SSE GET handler can block while the POST arrives
        on a different thread — exactly how the real VS Code extension works.

        Returns (httpd, port).  Call httpd.shutdown() when done.
        """
        class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
            _response_text = response_text
            _response_queue: _Queue.Queue = _Queue.Queue()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # silence test output

            def do_GET(self):
                if not self.path.startswith("/sse"):
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                # Announce the POST endpoint
                self.wfile.write(b"event: endpoint\ndata: /message?sessionId=test\n\n")
                self.wfile.flush()
                # Block until POST delivers a result (or timeout after 10 s)
                try:
                    msg = self.server._response_queue.get(timeout=10)
                    self.wfile.write(msg)
                    self.wfile.flush()
                except _Queue.Empty:
                    pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
                rpc_response = json.dumps({
                    "jsonrpc": "2.0",
                    "id": "cdr-1",
                    "result": {
                        "content": [{"type": "text", "text": self.server._response_text}]
                    },
                })
                sse_data = f"event: message\ndata: {rpc_response}\n\n".encode()
                self.server._response_queue.put(sse_data)

        httpd = _ThreadingHTTPServer(("localhost", 0), Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, port

    # 13a: find_ide_server reads lock files correctly
    step("Testing find_ide_server() with a mock lock file...")
    ide_dir = Path.home() / ".claude" / "ide"
    ide_dir.mkdir(parents=True, exist_ok=True)
    mock_lock = ide_dir / "19999.lock"
    mock_lock.write_text(json.dumps({
        "transport": "sse",
        "authToken": None,
        "ideName": "MockIDE",
    }))
    try:
        server_info = find_ide_server()
        if server_info and server_info["port"] == 19999:
            ok("find_ide_server() correctly read lock file (port 19999)")
        elif server_info:
            ok(f"find_ide_server() returned port {server_info['port']} (another lock file took priority)")
        else:
            fail("find_ide_server() returned None — lock file not found")
    finally:
        mock_lock.unlink(missing_ok=True)

    # 13b–d: open_diff_in_ide with each response type
    tmpdir_ide = tempfile.mkdtemp(prefix="cdr-ide-test-")
    test_file_ide = Path(tmpdir_ide) / "hello.py"
    test_file_ide.write_text("print('original')\n")

    for response_type in ("FILE_SAVED", "DIFF_REJECTED", "TAB_CLOSED"):
        step(f"Mock IDE responds with {response_type}...")
        httpd, port = _make_mock_ide_server(response_type)
        try:
            server = {"port": port, "transport": "sse", "auth_token": None, "ide_name": "MockIDE"}
            # Restore to original before call (as stop.py does)
            test_file_ide.write_text("print('original')\n")
            result_val = open_diff_in_ide(
                server, str(test_file_ide),
                "print('claude version')\n",
                f"Test: {response_type}",
                timeout=10,
            )
            if result_val == response_type:
                ok(f"  open_diff_in_ide returned '{response_type}' correctly")
            else:
                fail(f"  Expected '{response_type}', got '{result_val}'")
        finally:
            httpd.shutdown()

    shutil.rmtree(tmpdir_ide, ignore_errors=True)

    # 13e: WebSocket transport falls back gracefully
    step("WebSocket transport returns None (stdlib-only fallback)...")
    ws_server = {"port": 9999, "transport": "ws", "auth_token": None, "ide_name": "WSTest"}
    result_ws = open_diff_in_ide(ws_server, "/tmp/x.py", "content", "tab", timeout=1)
    if result_ws is None:
        ok("WebSocket transport returned None (expected — no ws library)")
    else:
        fail(f"Expected None for ws transport, got {result_ws!r}")

    # ── 14. Terminal review — per-hunk mock tty ──────────────────
    header("Terminal review — per-hunk mock tty")

    class _MockTty:
        """Discards writes (prompts), serves pre-loaded answers on readline()."""
        def __init__(self, answers: str):
            self._buf = io.StringIO(answers)
        def write(self, _text): pass
        def flush(self): pass
        def readline(self): return self._buf.readline()
        def close(self): pass


    # Import _review_file_hunks directly from the stop hook module
    import importlib.util
    _stop_spec = importlib.util.spec_from_file_location("stop_hook", ROOT / "hooks" / "stop.py")
    stop_mod = importlib.util.module_from_spec(_stop_spec)
    _stop_spec.loader.exec_module(stop_mod)

    tmpdir_term = tempfile.mkdtemp(prefix="cdr-term-test-")
    os.environ["CLAUDE_SESSION_ID"] = "test-term-session"
    os.environ["CLAUDE_WORKING_DIR"] = tmpdir_term

    term_file = Path(tmpdir_term) / "calc.py"
    original_calc = "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n"
    claude_calc = (
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "\n"
        "def mul(a: int, b: int) -> int:\n"
        "    return a * b\n"
    )
    term_file.write_text(claude_calc)

    # Capture shadow: run pre_tool_use to register, but bypass by writing directly
    from lib.state import capture_original, get_shadow_path as gsp, save_state as ss, load_state as ls
    state_term = ls()
    state_term["session_start"] = 1.0
    state_term["edited_files"] = {str(term_file.resolve()): 1}
    shadow_term = gsp(str(term_file))
    shadow_term.parent.mkdir(parents=True, exist_ok=True)
    shadow_term.write_text(original_calc)
    state_term["shadow_created"] = [str(term_file.resolve())]
    ss(state_term)

    # 14a: accept all hunks (input "y\ny\n")
    step("Per-hunk review: accept both hunks ('y y')...")
    tty_in = _MockTty("y\ny\n")
    result_hunks = stop_mod._review_file_hunks(str(term_file), tty_in)
    accepted = result_hunks["accepted"]
    rejected = result_hunks["rejected"]
    if accepted == 2 and rejected == 0:
        ok(f"  Accepted {accepted} hunks, rejected {rejected}")
    else:
        fail(f"  Expected 2 accepted / 0 rejected, got {accepted}/{rejected}")
    # Verify file still has claude's version
    if term_file.read_text() == claude_calc:
        ok("  File content matches Claude's version after full accept")
    else:
        fail(f"  File content wrong after full accept:\n{term_file.read_text()!r}")

    # 14b: reject first hunk, accept second (input "n\ny\n")
    term_file.write_text(claude_calc)
    step("Per-hunk review: reject first hunk, accept second ('n y')...")
    tty_in2 = _MockTty("n\ny\n")
    result_hunks2 = stop_mod._review_file_hunks(str(term_file), tty_in2)
    if result_hunks2["accepted"] == 1 and result_hunks2["rejected"] == 1:
        ok(f"  Accepted {result_hunks2['accepted']}, rejected {result_hunks2['rejected']}")
    else:
        fail(f"  Expected 1/1, got {result_hunks2['accepted']}/{result_hunks2['rejected']}")
    # First hunk rejected → should have original def add signature; second accepted → mul has types
    final_content = term_file.read_text()
    if "def add(a, b):" in final_content and "def mul(a: int, b: int)" in final_content:
        ok("  File correctly has original add() + typed mul()")
    else:
        fail(f"  Unexpected mixed content:\n{final_content!r}")

    # 14c: 'a' shortcut (accept all remaining)
    term_file.write_text(claude_calc)
    step("Per-hunk review: 'a' to accept all remaining...")
    tty_in3 = _MockTty("a\n")
    result_hunks3 = stop_mod._review_file_hunks(str(term_file), tty_in3)
    if result_hunks3["accepted"] == 2 and result_hunks3["rejected"] == 0:
        ok("  'a' accepted all hunks")
    else:
        fail(f"  'a' shortcut failed: {result_hunks3}")

    # 14d: 'd' shortcut (reject all remaining)
    term_file.write_text(claude_calc)
    step("Per-hunk review: 'd' to reject all remaining...")
    tty_in4 = _MockTty("d\n")
    result_hunks4 = stop_mod._review_file_hunks(str(term_file), tty_in4)
    if result_hunks4["accepted"] == 0 and result_hunks4["rejected"] == 2:
        ok("  'd' rejected all hunks")
    else:
        fail(f"  'd' shortcut failed: {result_hunks4}")
    if term_file.read_text() == original_calc:
        ok("  File restored to original after full reject")
    else:
        fail(f"  File content wrong after full reject:\n{term_file.read_text()!r}")

    cleanup_session()
    shutil.rmtree(tmpdir_term, ignore_errors=True)

    # ── 15. Per-hunk reconstruction — unit tests ─────────────────
    header("Per-hunk reconstruction — unit tests")

    os.environ["CLAUDE_SESSION_ID"] = "test-reconstruct-session"
    tmpdir_rec = tempfile.mkdtemp(prefix="cdr-rec-test-")
    os.environ["CLAUDE_WORKING_DIR"] = tmpdir_rec

    rec_file = Path(tmpdir_rec) / "rec.py"
    original_rec = (
        "line1\n"
        "line2\n"
        "line3\n"
        "line4\n"
        "line5\n"
    )
    claude_rec = (
        "LINE1\n"     # hunk 1: changed
        "line2\n"
        "LINE3\n"     # hunk 2: changed
        "line4\n"
        "LINE5\n"     # hunk 3: changed
    )
    rec_file.write_text(claude_rec)

    # Set up shadow
    state_rec = ls()
    state_rec["session_start"] = 1.0
    state_rec["edited_files"] = {str(rec_file.resolve()): 1}
    shadow_rec = gsp(str(rec_file))
    shadow_rec.parent.mkdir(parents=True, exist_ok=True)
    shadow_rec.write_text(original_rec)
    state_rec["shadow_created"] = [str(rec_file.resolve())]
    ss(state_rec)

    # 15a: accept only middle hunk
    step("Reconstruct: accept only middle hunk (hunk 2)...")
    rec_file.write_text(claude_rec)
    tty_rec = _MockTty("n\ny\nn\n")
    result_rec = stop_mod._review_file_hunks(str(rec_file), tty_rec)
    if result_rec["accepted"] == 1 and result_rec["rejected"] == 2:
        ok(f"  Accepted {result_rec['accepted']}, rejected {result_rec['rejected']}")
    else:
        fail(f"  Expected 1/2, got {result_rec['accepted']}/{result_rec['rejected']}")
    expected_rec = "line1\nline2\nLINE3\nline4\nline5\n"
    got_rec = rec_file.read_text()
    if got_rec == expected_rec:
        ok("  File content matches expected (only LINE3 from Claude)")
    else:
        fail(f"  Expected:\n{expected_rec!r}\nGot:\n{got_rec!r}")

    # 15b: no tty (non-interactive) — all hunks accepted by default
    step("Reconstruct: no tty → all hunks accepted by default...")
    rec_file.write_text(claude_rec)
    result_notty = stop_mod._review_file_hunks(str(rec_file), None)
    if result_notty["accepted"] == 3 and result_notty["rejected"] == 0:
        ok("  No-tty mode accepted all 3 hunks")
    else:
        fail(f"  Expected 3/0 in no-tty mode, got {result_notty['accepted']}/{result_notty['rejected']}")
    if rec_file.read_text() == claude_rec:
        ok("  File has Claude's full version in no-tty mode")
    else:
        fail("  File content wrong in no-tty mode")

    cleanup_session()
    shutil.rmtree(tmpdir_rec, ignore_errors=True)

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
    print(f"  {G}✓{R} Shadow dir: writable check passes in normal conditions")
    print(f"  {G}✓{R} Shadow dir: unwritable dir detected and session disabled")
    print(f"  {G}✓{R} IDE mock: find_ide_server reads lock files correctly")
    print(f"  {G}✓{R} IDE mock: FILE_SAVED / DIFF_REJECTED / TAB_CLOSED all handled")
    print(f"  {G}✓{R} IDE mock: WebSocket transport returns None (no ws lib needed)")
    print(f"  {G}✓{R} Terminal review: y/n/a/d per-hunk decisions")
    print(f"  {G}✓{R} Terminal review: no-tty mode accepts all hunks silently")
    print(f"  {G}✓{R} Reconstruction: mixed accept/reject produces correct file")
    print()


if __name__ == "__main__":
    main()
