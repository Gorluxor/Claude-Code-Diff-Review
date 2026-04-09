"""
lib/ide.py — VS Code IDE MCP client for claude-diff-review.

Claude Code's VS Code extension runs an MCP server accessible via HTTP SSE or
WebSocket. The port is published in ~/.claude/ide/<PORT>.lock when the extension
connects to a Claude Code session.

We use this to call the native `openDiff` RPC, which opens VS Code's built-in
diff editor (side-by-side, with per-hunk Revert Selected Ranges) and blocks
until the user accepts, rejects, or closes the tab.

Both SSE and WebSocket transports are supported (stdlib only, no third-party deps).
Falls back to terminal review if no IDE server is found or connection fails.
"""

import base64
import json
import os
import socket
import struct
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


def _dbg(msg: str) -> None:
    sys.stderr.write(f"[diff-review:ide] {msg}\n")
    sys.stderr.flush()


def find_ide_server() -> Optional[dict]:
    """
    Locate a running VS Code IDE server from Claude Code lockfiles.

    Claude Code writes ~/.claude/ide/<PORT>.lock when the VS Code extension
    connects. The file contains JSON with transport type and optional auth token.

    Prefers the lock file whose workspaceFolders includes the current working
    directory, falling back to the first available lock file.

    Returns a dict with keys: port, transport, auth_token, ide_name
    Returns None if no active IDE server is found.
    """
    ide_dir = Path.home() / ".claude" / "ide"
    if not ide_dir.exists():
        _dbg(f"IDE dir not found: {ide_dir}")
        return None

    cwd = str(Path.cwd().resolve())
    _dbg(f"Looking for IDE server matching CWD: {cwd}")
    best = None

    for lock_file in sorted(ide_dir.glob("*.lock")):
        try:
            port = int(lock_file.stem)
            data = json.loads(lock_file.read_text())
            transport = data.get("transport", "sse")
            workspaces = data.get("workspaceFolders", [])
            _dbg(f"  Found lock: port={port} transport={transport} workspaces={workspaces}")
            entry = {
                "port": port,
                "transport": transport,
                "auth_token": data.get("authToken"),
                "ide_name": data.get("ideName", "IDE"),
            }
            # Prefer server whose workspace matches CWD
            if any(cwd.startswith(str(Path(w).resolve()))
                   for w in workspaces):
                _dbg(f"  → Matched CWD, using port {port}")
                return entry
            if best is None:
                best = entry
        except Exception as e:
            _dbg(f"  Error reading {lock_file}: {e}")
            continue

    if best:
        _dbg(f"No CWD match — falling back to port {best['port']}")
    else:
        _dbg("No IDE lock files found")
    return best


def _ws_open_diff_in_ide(
    server: dict,
    old_file_path: str,
    new_file_path: str,
    tab_name: str,
    timeout: int = 600,
) -> Optional[str]:
    """
    WebSocket transport implementation of openDiff MCP RPC.

    Implements the WebSocket protocol and MCP initialize handshake using only
    Python stdlib (socket, struct, base64, os). No third-party libraries needed.

    Returns: "FILE_SAVED" | "DIFF_REJECTED" | "TAB_CLOSED" | None
    """
    port = server["port"]
    auth = server.get("auth_token")

    _dbg(f"WS: connecting to port {port} (auth={'yes' if auth else 'no'})")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(("localhost", port))
        _dbg("WS: TCP connected")

        # ── HTTP → WebSocket upgrade ──────────────────────────────────────
        ws_key = base64.b64encode(os.urandom(16)).decode()
        handshake = "\r\n".join([
            "GET / HTTP/1.1",
            f"Host: localhost:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {ws_key}",
            "Sec-WebSocket-Version: 13",
            *([ f"X-Claude-Code-Ide-Authorization: {auth}" ] if auth else []),
            "\r\n",
        ])
        sock.sendall(handshake.encode())

        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk

        if b" 101 " not in buf:
            _dbg(f"WS: upgrade failed — response: {buf[:200]!r}")
            sock.close()
            return None
        _dbg("WS: upgrade OK (101 Switching Protocols)")

        # ── Frame send/recv helpers ───────────────────────────────────────

        def send_text(text: str) -> None:
            payload = text.encode("utf-8")
            mask = os.urandom(4)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            n = len(payload)
            if n <= 125:
                header = bytes([0x81, 0x80 | n]) + mask
            elif n <= 65535:
                header = bytes([0x81, 0xFE]) + struct.pack(">H", n) + mask
            else:
                header = bytes([0x81, 0xFF]) + struct.pack(">Q", n) + mask
            sock.sendall(header + masked)

        def recv_frame() -> tuple:
            """Return (opcode, text). Transparently handles pings."""
            while True:
                hdr = b""
                while len(hdr) < 2:
                    hdr += sock.recv(2 - len(hdr))
                opcode = hdr[0] & 0x0F
                is_masked = hdr[1] & 0x80
                n = hdr[1] & 0x7F

                if n == 126:
                    extra = b""
                    while len(extra) < 2:
                        extra += sock.recv(2 - len(extra))
                    n = struct.unpack(">H", extra)[0]
                elif n == 127:
                    extra = b""
                    while len(extra) < 8:
                        extra += sock.recv(8 - len(extra))
                    n = struct.unpack(">Q", extra)[0]

                mk = b""
                if is_masked:
                    while len(mk) < 4:
                        mk += sock.recv(4 - len(mk))

                payload = b""
                while len(payload) < n:
                    payload += sock.recv(n - len(payload))
                if is_masked:
                    payload = bytes(b ^ mk[i % 4] for i, b in enumerate(payload))

                if opcode == 0x9:  # ping → send pong and continue
                    sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                    continue
                if opcode == 0x8:  # close
                    raise ConnectionError("WebSocket closed by server")

                return opcode, payload.decode("utf-8", errors="replace")

        def rpc(msg: dict) -> dict:
            send_text(json.dumps(msg))
            while True:
                _, text = recv_frame()
                try:
                    resp = json.loads(text)
                    if resp.get("id") == msg.get("id"):
                        return resp
                except Exception:
                    pass

        # ── MCP initialize handshake ──────────────────────────────────────
        _dbg("WS: sending MCP initialize")
        init = rpc({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-diff-review", "version": "1.0"},
            },
            "id": 1,
        })
        if "error" in init:
            _dbg(f"WS: MCP initialize error: {init['error']}")
            sock.close()
            return None
        _dbg("WS: MCP initialized OK")

        send_text(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }))

        # ── openDiff call — block until user acts ─────────────────────────
        _dbg(f"WS: calling openDiff — old={old_file_path!r} new={new_file_path!r} tab={tab_name!r}")
        sock.settimeout(timeout)
        resp = rpc({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "openDiff",
                "arguments": {
                    "old_file_path": old_file_path,
                    "new_file_path": new_file_path,
                    "tab_name": tab_name,
                },
            },
            "id": 2,
        })
        sock.close()
        _dbg(f"WS: openDiff response: {json.dumps(resp)[:300]}")

        content = (
            resp.get("result", {}).get("content", [])
            if isinstance(resp.get("result"), dict)
            else []
        )
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                val = item["text"].strip()
                if val in ("FILE_SAVED", "DIFF_REJECTED", "TAB_CLOSED"):
                    _dbg(f"WS: openDiff result = {val}")
                    return val

        _dbg(f"WS: no recognized result in response content: {content}")
        return None

    except Exception as e:
        _dbg(f"WS: exception: {e!r}")
        try:
            sock.close()
        except Exception:
            pass
        return None


def open_diff_in_ide(
    server: dict,
    old_file_path: str,
    new_file_path: str,
    tab_name: str,
    timeout: int = 600,
) -> Optional[str]:
    """
    Open a native VS Code diff review tab via Claude Code's MCP openDiff RPC.

    old_file_path: shadow file (original content) — shown on the LEFT (read-only)
    new_file_path: real file (Claude's version)   — shown on the RIGHT (editable)

    VS Code shows: left = original, right = Claude's version.
    The user saves to accept, uses Revert arrows to reject individual hunks.

    If the user saves (FILE_SAVED), VS Code writes their final version to new_file_path.
    If the user rejects (DIFF_REJECTED), the caller should restore original to new_file_path.
    If the user closes the tab (TAB_CLOSED), Claude's version is kept as-is.

    Returns: "FILE_SAVED" | "DIFF_REJECTED" | "TAB_CLOSED" | None
    """
    _dbg(f"open_diff_in_ide: transport={server.get('transport')} port={server.get('port')}")
    if server.get("transport") == "ws":
        return _ws_open_diff_in_ide(server, old_file_path, new_file_path, tab_name, timeout)

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
                "old_file_path": old_file_path,
                "new_file_path": new_file_path,
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
