"""
lib/ide.py — VS Code IDE MCP client for claude-diff-review.

Claude Code's VS Code extension runs an MCP server accessible via HTTP SSE or
WebSocket. The port is published in ~/.claude/ide/<PORT>.lock when the extension
connects to a Claude Code session.

We use this to call the native `openDiff` RPC, which opens VS Code's built-in
diff editor (side-by-side, with per-hunk Revert Selected Ranges) and blocks
until the user accepts, rejects, or closes the tab.

Only SSE transport is supported (WebSocket requires a third-party library).
Falls back to terminal review if no IDE server is found or connection fails.
"""

import json
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


def find_ide_server() -> Optional[dict]:
    """
    Locate a running VS Code IDE server from Claude Code lockfiles.

    Claude Code writes ~/.claude/ide/<PORT>.lock when the VS Code extension
    connects. The file contains JSON with transport type and optional auth token.

    Returns a dict with keys: port, transport, auth_token, ide_name
    Returns None if no active IDE server is found.
    """
    ide_dir = Path.home() / ".claude" / "ide"
    if not ide_dir.exists():
        return None

    for lock_file in sorted(ide_dir.glob("*.lock")):
        try:
            port = int(lock_file.stem)
            data = json.loads(lock_file.read_text())
            return {
                "port": port,
                "transport": data.get("transport", "sse"),
                "auth_token": data.get("authToken"),
                "ide_name": data.get("ideName", "IDE"),
            }
        except Exception:
            continue

    return None


def open_diff_in_ide(
    server: dict,
    file_path: str,
    new_content: str,
    tab_name: str,
    timeout: int = 600,
) -> Optional[str]:
    """
    Open a native VS Code diff review tab via Claude Code's MCP openDiff RPC.

    Before calling this:
      - file_path on disk must contain the ORIGINAL content (pre-edit baseline)
      - new_content is Claude's proposed version (shown on the right side)

    VS Code shows: left = current file (original), right = new_content (proposed).
    The user can accept all, reject all, or revert individual hunks before saving.

    If the user saves (FILE_SAVED), VS Code writes their final version to file_path.
    If the user rejects (DIFF_REJECTED), file_path stays as original.
    If the user closes the tab (TAB_CLOSED), Claude's version is treated as accepted.

    Only supports SSE transport. Returns None for WebSocket servers or on error.

    Returns: "FILE_SAVED" | "DIFF_REJECTED" | "TAB_CLOSED" | None
    """
    if server.get("transport") == "ws":
        return None  # WebSocket requires 'websockets' library — fall back to terminal

    port = server["port"]
    auth = server.get("auth_token")
    base_url = f"http://localhost:{port}"

    headers = {"Content-Type": "application/json"}
    if auth:
        headers["X-Claude-Code-Ide-Authorization"] = auth

    result: dict = {"value": None}
    endpoint_url: list = [None]
    endpoint_ready = threading.Event()
    done = threading.Event()

    def sse_reader() -> None:
        """Background thread: reads the MCP SSE stream for endpoint URL and responses."""
        sse_headers = {
            **headers,
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        try:
            req = urllib.request.Request(f"{base_url}/sse", headers=sse_headers)
            with urllib.request.urlopen(req, timeout=timeout + 60) as resp:
                event_type = ""
                for raw in resp:
                    if done.is_set():
                        return
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()
                        if event_type == "endpoint":
                            # MCP SSE server announces the POST endpoint URL
                            endpoint_url[0] = data
                            endpoint_ready.set()
                        elif event_type == "message" and data:
                            _handle_message(data, result, done)
                    elif not line:
                        event_type = ""
        except Exception:
            pass
        finally:
            endpoint_ready.set()  # Unblock sender if SSE connection failed

    t = threading.Thread(target=sse_reader, daemon=True)
    t.start()

    # Wait for the session endpoint URL (sent immediately on connect)
    if not endpoint_ready.wait(timeout=10) or not endpoint_url[0]:
        done.set()
        return None

    # Resolve relative endpoint to full URL
    ep = endpoint_url[0]
    if not ep.startswith("http"):
        ep = f"{base_url}{ep}"

    # Send the openDiff tool call
    rpc = {
        "jsonrpc": "2.0",
        "id": "cdr-1",
        "method": "tools/call",
        "params": {
            "name": "openDiff",
            "arguments": {
                "old_file_path": file_path,
                "new_file_path": file_path,
                "new_file_contents": new_content,
                "tab_name": tab_name,
            },
        },
    }
    try:
        req = urllib.request.Request(
            ep,
            data=json.dumps(rpc).encode(),
            headers=headers,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15).close()
    except Exception:
        done.set()
        return None

    # Block until the user acts (FILE_SAVED / DIFF_REJECTED / TAB_CLOSED)
    done.wait(timeout=timeout)
    return result["value"]


def _handle_message(data: str, result: dict, done: threading.Event) -> None:
    """Parse an SSE 'message' event and extract the openDiff result if present."""
    try:
        msg = json.loads(data)
        content = (
            msg.get("result", {}).get("content", [])
            if isinstance(msg.get("result"), dict)
            else []
        )
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item["text"].strip()
                if text in ("FILE_SAVED", "DIFF_REJECTED", "TAB_CLOSED"):
                    result["value"] = text
                    done.set()
                    return
    except Exception:
        pass
