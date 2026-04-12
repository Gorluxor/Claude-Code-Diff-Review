"""
claude-diff-review: Interactive review orchestration.

Handles IDE (MCP openDiff), terminal (per-hunk y/n), and Copilot review flows.
Extracted from hooks/stop.py for reuse across hooks and CLI.
"""

import json
import sys
import os
import subprocess
import difflib
from pathlib import Path

from lib.state import (
    get_shadow_path,
    get_working_dir,
    load_state,
    save_state,
    log_event,
)
from lib.diff import (
    RESET, BOLD, DIM, RED, GREEN, YELLOW, CYAN, MAGENTA,
    format_path, open_vscode_diff,
)


# ──────────────────────────────────────────────────────────────────────
# Re-engagement message builder
# ──────────────────────────────────────────────────────────────────────

def _build_rejection_message(decisions: dict) -> str:
    """
    Build the re-engagement message Claude sees when the user rejected or
    modified some changes.

    decisions: {abs_path: {"type": "rejected"|"modified",
                            "original": str, "claude": str, "final": str}}
    """
    rejected = {p: d for p, d in decisions.items() if d["type"] == "rejected"}
    modified = {p: d for p, d in decisions.items() if d["type"] == "modified"}

    lines = ["The user reviewed your changes interactively.", ""]

    if rejected:
        lines.append(f"**{len(rejected)} file(s) fully rejected** (reverted to original):")
        for p in rejected:
            lines.append(f"  - {format_path(p)}")
        lines.append("")

    if modified:
        lines.append(f"**{len(modified)} file(s) accepted with user modifications:**")
        for p in modified:
            lines.append(f"  - {format_path(p)}")
        lines.append("")

    if rejected:
        lines += ["## Rejected changes", ""]
        for abs_path, d in rejected.items():
            lines.append(f"### {format_path(abs_path)}")
            diff = list(difflib.unified_diff(
                d["original"].splitlines(),
                d["claude"].splitlines(),
                fromfile="original",
                tofile="claude's version (rejected)",
                lineterm="",
            ))
            for line in diff[:60]:
                lines.append(f"  {line}")
            lines.append("")

    if modified:
        lines += ["## User modifications (your version → user's final version)", ""]
        for abs_path, d in modified.items():
            lines.append(f"### {format_path(abs_path)}")
            diff = list(difflib.unified_diff(
                d["claude"].splitlines(),
                d["final"].splitlines(),
                fromfile="your version",
                tofile="user's final version",
                lineterm="",
            ))
            for line in diff[:60]:
                lines.append(f"  {line}")
            lines.append("")

    lines.append(
        "Please review the above and ask if the user wants further changes, "
        "or acknowledge their edits."
    )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Interactive review — IDE path (native VS Code openDiff RPC)
# ──────────────────────────────────────────────────────────────────────

def _run_ide_review(
    edited_files: dict, state: dict, ide_server: dict, re_engage: bool
) -> None:
    """
    Review via Claude Code's native VS Code openDiff MCP RPC.

    For each file:
      1. Call openDiff → VS Code opens side-by-side diff, user edits/saves.
      2. FILE_SAVED = user saved (possibly with per-hunk reverts).
         DIFF_REJECTED = user explicitly rejected all changes.
         TAB_CLOSED = treat as accept.
      3. Compare final file against Claude's version to detect modifications.
    """
    from lib.ide import open_diff_in_ide

    log_event(
        "review", "IDE review started",
        ide=ide_server.get("ide_name", "IDE"),
        port=ide_server["port"],
        transport=ide_server.get("transport", "sse"),
    )

    sys.stderr.write(f"\n{BOLD}{MAGENTA}{'─' * 60}{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}  ◆ claude-diff-review — interactive (VS Code){RESET}\n")
    sys.stderr.write(
        f"{DIM}  Connected to {ide_server.get('ide_name', 'IDE')} "
        f"on port {ide_server['port']}{RESET}\n"
    )
    sys.stderr.write(f"{BOLD}{MAGENTA}{'─' * 60}{RESET}\n\n")
    sys.stderr.write(
        f"  {DIM}Left = original · Right = Claude's version{RESET}\n"
        f"  {DIM}→ {RESET}{BOLD}Ctrl+S{RESET}{DIM} to accept  "
        f"→ {RESET}{BOLD}Revert{RESET}{DIM} arrows to reject individual hunks{RESET}\n\n"
    )

    decisions: dict = {}
    previewed = set(state.get("previewed_files", []))
    accepted_prev = {p for p, d in state.get("decisions", {}).items() if d == "accepted"}

    rpc_none_count = 0   # files where openDiff returned None (RPC failure)
    rpc_responded_count = 0  # files where openDiff returned a real response

    for abs_path in sorted(edited_files):
        # Skip files already previewed progressively or accepted in previous rounds
        if abs_path in previewed or abs_path in accepted_prev:
            log_event("review", "File skipped", file=os.path.basename(abs_path),
                      reason="previewed" if abs_path in previewed else "accepted_prev")
            continue

        real = Path(abs_path)
        shadow = get_shadow_path(abs_path)
        rel = format_path(abs_path)

        if not real.exists() or abs_path in state.get("binary_files", []):
            log_event("review", "File skipped", file=os.path.basename(abs_path),
                      reason="missing_or_binary")
            continue

        try:
            original_content = shadow.read_text(errors="replace") if shadow.exists() else ""
            claude_content = real.read_text(errors="replace")
        except Exception:
            continue

        if original_content == claude_content:
            log_event("review", "File unchanged — skipped", file=os.path.basename(abs_path))
            sys.stderr.write(f"  {DIM}–  {rel} (unchanged){RESET}\n")
            continue

        log_event("review", "openDiff called", file=os.path.basename(abs_path))
        sys.stderr.write(f"  {CYAN}▶{RESET}  {BOLD}{rel}{RESET}  {DIM}(opening diff…){RESET}\n")
        sys.stderr.flush()

        response = open_diff_in_ide(
            ide_server, str(shadow), str(real),
            f"Review: {rel}", timeout=600,
        )

        log_event("review", "openDiff response", file=os.path.basename(abs_path),
                  response=response if response is not None else "None(RPC_FAILED)")

        if response is None:
            rpc_none_count += 1
            sys.stderr.write(
                f"  {YELLOW}?{RESET}  {BOLD}{rel}{RESET}  "
                f"{DIM}no IDE response — RPC failed{RESET}\n"
            )
            continue

        rpc_responded_count += 1

        if response == "DIFF_REJECTED":
            real.write_text(original_content)
            decisions[abs_path] = {
                "type": "rejected",
                "original": original_content,
                "claude": claude_content,
                "final": original_content,
            }
            sys.stderr.write(f"  {RED}✗{RESET}  {BOLD}{rel}{RESET}  {DIM}rejected{RESET}\n")

        elif response == "FILE_SAVED":
            try:
                final_content = real.read_text(errors="replace")
            except Exception:
                final_content = claude_content

            if final_content.rstrip() == original_content.rstrip():
                decisions[abs_path] = {
                    "type": "rejected",
                    "original": original_content,
                    "claude": claude_content,
                    "final": final_content,
                }
                sys.stderr.write(
                    f"  {RED}✗{RESET}  {BOLD}{rel}{RESET}  "
                    f"{DIM}rejected (saved as original){RESET}\n"
                )
            elif final_content != claude_content:
                decisions[abs_path] = {
                    "type": "modified",
                    "original": original_content,
                    "claude": claude_content,
                    "final": final_content,
                }
                sys.stderr.write(
                    f"  {YELLOW}~{RESET}  {BOLD}{rel}{RESET}  "
                    f"{DIM}accepted with modifications{RESET}\n"
                )
            else:
                sys.stderr.write(
                    f"  {GREEN}✓{RESET}  {BOLD}{rel}{RESET}  {DIM}accepted{RESET}\n"
                )

        else:
            sys.stderr.write(
                f"  {GREEN}✓{RESET}  {BOLD}{rel}{RESET}  {DIM}accepted{RESET}\n"
            )

    # If ALL RPC calls failed (none responded), the IDE connection is broken —
    # fall back to terminal review instead of silently accepting everything.
    if rpc_none_count > 0 and rpc_responded_count == 0:
        log_event("review", "All RPC calls failed — falling back to terminal review",
                  failed=rpc_none_count)
        sys.stderr.write(
            f"\n  {YELLOW}⚠  IDE openDiff RPC failed for all {rpc_none_count} file(s).{RESET}\n"
            f"  {DIM}Falling back to terminal review…{RESET}\n"
        )
        sys.stderr.write(f"{DIM}{'─' * 60}{RESET}\n\n")
        _run_terminal_review(edited_files, state, re_engage)
        return  # _run_terminal_review calls sys.exit(0)

    # Record decisions in state
    _record_decisions(decisions, state)

    rejected = sum(1 for d in decisions.values() if d["type"] == "rejected")
    modified = sum(1 for d in decisions.values() if d["type"] == "modified")
    accepted = rpc_responded_count - len(decisions)
    log_event("review", "IDE review complete",
              accepted=accepted, rejected=rejected, modified=modified)

    sys.stderr.write(f"\n{DIM}{'─' * 60}{RESET}\n")

    if decisions and re_engage:
        log_event("review", "Re-engaging Claude", files_with_changes=len(decisions))
        sys.stderr.write(
            f"  {YELLOW}{len(decisions)} file(s) had rejections/modifications "
            f"— re-engaging Claude…{RESET}\n"
        )
        sys.stderr.write(f"{DIM}{'─' * 60}{RESET}\n\n")
        print(json.dumps({"decision": "block", "reason": _build_rejection_message(decisions)}))
    elif decisions:
        sys.stderr.write(
            f"  {YELLOW}{len(decisions)} file(s) had rejections/modifications.{RESET}\n"
            f"  {DIM}Claude not notified — describe changes manually to re-engage.{RESET}\n"
        )
        sys.stderr.write(f"{DIM}{'─' * 60}{RESET}\n\n")
    else:
        sys.stderr.write(f"  {GREEN}All changes accepted.{RESET}\n")
        sys.stderr.write(f"{DIM}{'─' * 60}{RESET}\n\n")

    sys.exit(0)


# ──────────────────────────────────────────────────────────────────────
# Interactive review — terminal fallback (git add -p style)
# ──────────────────────────────────────────────────────────────────────

_CONTEXT = 3


def _print_hunk(
    old_lines: list, new_lines: list, opcodes: list,
    opcode_idx: int, hunk_num: int, total_hunks: int,
) -> None:
    """Render one diff hunk with context lines."""
    tag, i1, i2, j1, j2 = opcodes[opcode_idx]

    ctx_before: list = []
    if opcode_idx > 0:
        ptag, pi1, pi2, _, _ = opcodes[opcode_idx - 1]
        if ptag == "equal":
            ctx_before = old_lines[max(pi1, pi2 - _CONTEXT):pi2]

    ctx_after: list = []
    if opcode_idx < len(opcodes) - 1:
        ntag, ni1, ni2, _, _ = opcodes[opcode_idx + 1]
        if ntag == "equal":
            ctx_after = old_lines[ni1:min(ni1 + _CONTEXT, ni2)]

    sys.stderr.write(f"\n  {DIM}┄ hunk {hunk_num}/{total_hunks} ┄{RESET}\n")
    for line in ctx_before:
        sys.stderr.write(f"  {DIM}  {line.rstrip()}{RESET}\n")
    for line in old_lines[i1:i2]:
        sys.stderr.write(f"  {RED}- {line.rstrip()}{RESET}\n")
    for line in new_lines[j1:j2]:
        sys.stderr.write(f"  {GREEN}+ {line.rstrip()}{RESET}\n")
    for line in ctx_after:
        sys.stderr.write(f"  {DIM}  {line.rstrip()}{RESET}\n")


def _review_file_hunks(abs_path: str, tty) -> dict:
    """Per-hunk y/n review for one file. Writes accepted hunks back to disk."""
    real = Path(abs_path)
    shadow = get_shadow_path(abs_path)

    try:
        old_lines = (
            shadow.read_text(errors="replace").splitlines(True)
            if shadow.exists() else []
        )
        new_lines = real.read_text(errors="replace").splitlines(True)
    except Exception:
        return {"accepted": 0, "rejected": 0, "rejected_hunks": []}

    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    opcodes = sm.get_opcodes()
    changes = [(idx, op) for idx, op in enumerate(opcodes) if op[0] != "equal"]

    if not changes:
        return {"accepted": 0, "rejected": 0, "rejected_hunks": []}

    rel = format_path(abs_path)
    sys.stderr.write(
        f"\n{BOLD}{CYAN}── {rel}{RESET} "
        f"{DIM}({len(changes)} hunk{'s' if len(changes) != 1 else ''}){RESET}\n"
    )

    accepted_indices: set = set()
    rejected_hunks: list = []
    shortcut = None  # "accept_all" | "reject_all"

    for change_idx, (opcode_idx, (_tag, i1, i2, j1, j2)) in enumerate(changes):
        if shortcut == "accept_all":
            accepted_indices.add(change_idx)
            continue
        elif shortcut == "reject_all":
            rejected_hunks.append({
                "original": "".join(old_lines[i1:i2]),
                "claude": "".join(new_lines[j1:j2]),
            })
            continue

        _print_hunk(old_lines, new_lines, opcodes, opcode_idx, change_idx + 1, len(changes))

        if not tty:
            accepted_indices.add(change_idx)
            continue

        while True:
            tty.write(
                f"\n  {BOLD}y{RESET} accept  "
                f"{BOLD}n{RESET} reject  "
                f"{BOLD}a{RESET} accept all  "
                f"{BOLD}d{RESET} reject all  "
                f"{BOLD}s{RESET} skip file: "
            )
            tty.flush()
            answer = tty.readline().strip().lower()

            if answer in ("y", "yes", ""):
                accepted_indices.add(change_idx)
                break
            elif answer in ("n", "no"):
                rejected_hunks.append({
                    "original": "".join(old_lines[i1:i2]),
                    "claude": "".join(new_lines[j1:j2]),
                })
                break
            elif answer == "a":
                accepted_indices.add(change_idx)
                shortcut = "accept_all"
                break
            elif answer == "d":
                rejected_hunks.append({
                    "original": "".join(old_lines[i1:i2]),
                    "claude": "".join(new_lines[j1:j2]),
                })
                shortcut = "reject_all"
                break
            elif answer == "s":
                accepted_indices.add(change_idx)
                shortcut = "accept_all"
                break

    # Reconstruct file from decisions
    result_lines = []
    change_idx = 0
    for opcode_tag, i1, i2, j1, j2 in opcodes:
        if opcode_tag == "equal":
            result_lines.extend(new_lines[j1:j2])
        else:
            if change_idx in accepted_indices:
                result_lines.extend(new_lines[j1:j2])
            else:
                result_lines.extend(old_lines[i1:i2])
            change_idx += 1

    real.write_text("".join(result_lines))
    return {
        "accepted": len(accepted_indices),
        "rejected": len(rejected_hunks),
        "rejected_hunks": rejected_hunks,
    }


def _run_terminal_review(edited_files: dict, state: dict, re_engage: bool) -> None:
    """Terminal per-hunk review fallback when no VS Code IDE is connected."""
    log_event("review", "Terminal review started", files=len(edited_files))
    sys.stderr.write(f"\n{BOLD}{MAGENTA}{'─' * 60}{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}  ◆ claude-diff-review — interactive (terminal){RESET}\n")
    sys.stderr.write(f"{DIM}  No VS Code IDE connection — using terminal review{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}{'─' * 60}{RESET}\n\n")

    previewed = set(state.get("previewed_files", []))
    accepted_prev = {p for p, d in state.get("decisions", {}).items() if d == "accepted"}

    vscode_ok = True
    for abs_path in sorted(edited_files):
        if abs_path in previewed or abs_path in accepted_prev:
            continue
        shadow = get_shadow_path(abs_path)
        real = Path(abs_path)
        if real.exists() and vscode_ok:
            vscode_ok = open_vscode_diff(shadow, real, format_path(abs_path))
    if vscode_ok:
        sys.stderr.write(f"  {DIM}VS Code diffs opened for reference.{RESET}\n\n")

    try:
        tty = open("/dev/tty", "r+")
    except Exception:
        tty = None

    all_rejections: dict = {}
    total_accepted = total_rejected = 0

    for abs_path, edit_count in sorted(edited_files.items()):
        if abs_path in previewed or abs_path in accepted_prev:
            continue
        if abs_path in state.get("binary_files", []) or not Path(abs_path).exists():
            continue
        result = _review_file_hunks(abs_path, tty)
        total_accepted += result["accepted"]
        total_rejected += result["rejected"]
        rel = format_path(abs_path)
        log_event("review", "Terminal hunk decision", file=os.path.basename(abs_path),
                  accepted=result["accepted"], rejected=result["rejected"])
        if result["rejected"]:
            all_rejections[abs_path] = result
            sys.stderr.write(
                f"\n  {YELLOW}↺{RESET}  {BOLD}{rel}{RESET}  "
                f"{GREEN}+{result['accepted']} accepted{RESET}  "
                f"{RED}-{result['rejected']} rejected{RESET}\n"
            )
        elif result["accepted"]:
            sys.stderr.write(f"\n  {GREEN}✓{RESET}  {BOLD}{rel}{RESET}  {DIM}all accepted{RESET}\n")

    if tty:
        tty.close()

    sys.stderr.write(
        f"\n{DIM}{'─' * 60}{RESET}\n"
        f"  {DIM}{GREEN}+{total_accepted} accepted{RESET}  {RED}-{total_rejected} rejected{RESET}\n"
        f"{DIM}{'─' * 60}{RESET}\n\n"
    )

    # Record decisions
    decisions = {}
    for abs_path in edited_files:
        if abs_path in previewed or abs_path in accepted_prev:
            continue
        if abs_path in all_rejections:
            decisions[abs_path] = "rejected"
        else:
            decisions[abs_path] = "accepted"
    _record_decisions_simple(decisions, state)
    log_event("review", "Terminal review complete",
              total_accepted=total_accepted, total_rejected=total_rejected,
              files_rejected=len(all_rejections))

    if all_rejections and re_engage:
        decisions_detail = {}
        for abs_path, r in all_rejections.items():
            try:
                shadow = get_shadow_path(abs_path)
                original = shadow.read_text(errors="replace") if shadow.exists() else ""
            except Exception:
                original = ""
            decisions_detail[abs_path] = {
                "type": "rejected",
                "original": original,
                "claude": "",
                "final": Path(abs_path).read_text(errors="replace"),
            }
        print(json.dumps({"decision": "block", "reason": _build_rejection_message(decisions_detail)}))
    elif all_rejections:
        sys.stderr.write(
            f"  {YELLOW}Rejections recorded.{RESET}  "
            f"{DIM}Claude not notified — describe changes manually to re-engage.{RESET}\n\n"
        )

    sys.exit(0)


# ──────────────────────────────────────────────────────────────────────
# Copilot provider
# ──────────────────────────────────────────────────────────────────────

def _run_copilot_review(edited_files: dict, state: dict) -> None:
    """
    Stage all Claude-edited files in git so VS Code's Source Control panel
    (and Copilot's Review Changes) picks them up.
    """
    sys.stderr.write(f"\n{BOLD}{MAGENTA}{'─' * 60}{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}  ◆ claude-diff-review — interactive (Copilot){RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}{'─' * 60}{RESET}\n\n")

    staged = []
    for abs_path in sorted(edited_files):
        if not Path(abs_path).exists():
            continue
        try:
            subprocess.run(
                ["git", "add", abs_path],
                capture_output=True,
                cwd=str(get_working_dir()),
                check=True,
            )
            staged.append(format_path(abs_path))
        except Exception:
            pass

    if staged:
        sys.stderr.write(f"  {GREEN}✓{RESET}  Staged {len(staged)} file(s) in git:\n")
        for rel in staged:
            sys.stderr.write(f"      {DIM}{rel}{RESET}\n")
        sys.stderr.write(
            f"\n  {CYAN}Next steps in VS Code:{RESET}\n"
            f"  {DIM}1. Open Source Control panel  (Ctrl+Shift+G){RESET}\n"
            f"  {DIM}2. Click ✦ Copilot → Review and Comment{RESET}\n"
            f"  {DIM}3. Accept / discard per suggestion{RESET}\n\n"
            f"  {DIM}Or open the Copilot Chat panel and ask it to review staged changes.{RESET}\n"
        )
    else:
        sys.stderr.write(f"  {YELLOW}⚠  No files could be staged (not a git repo?){RESET}\n")
        sys.stderr.write(f"  {DIM}Falling back to VS Code diffs.{RESET}\n\n")
        for abs_path in sorted(edited_files):
            shadow = get_shadow_path(abs_path)
            real = Path(abs_path)
            if real.exists():
                open_vscode_diff(shadow, real, format_path(abs_path))

    sys.stderr.write(f"{DIM}{'─' * 60}{RESET}\n\n")
    sys.exit(0)


# ──────────────────────────────────────────────────────────────────────
# Decision recording helpers
# ──────────────────────────────────────────────────────────────────────

def _record_decisions(decisions: dict, state: dict) -> None:
    """Record IDE review decisions (detailed dict) into state as simple strings."""
    state_decisions = state.get("decisions", {})
    for abs_path, d in decisions.items():
        state_decisions[abs_path] = d["type"]  # "rejected" | "modified"
    # Files reviewed but not in decisions dict were accepted
    for abs_path in state.get("edited_files", {}):
        if abs_path not in state_decisions and abs_path not in decisions:
            previewed = set(state.get("previewed_files", []))
            if abs_path not in previewed:
                state_decisions[abs_path] = "accepted"
    state["decisions"] = state_decisions
    save_state(state)


def _record_decisions_simple(decisions: dict, state: dict) -> None:
    """Record simple string decisions into state."""
    state_decisions = state.get("decisions", {})
    state_decisions.update(decisions)
    state["decisions"] = state_decisions
    save_state(state)


# ──────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────

def run_interactive_review(
    edited_files: dict, state: dict, provider: str = "claude-code",
    re_engage: bool = True,
) -> None:
    """
    Entry point for interactive review.

    provider="claude-code" — native VS Code openDiff MCP, one file at a time (blocking)
    provider="copilot"     — stage in git + Copilot Source Control review (non-blocking)
    """
    if provider == "copilot":
        _run_copilot_review(edited_files, state)
        # exits internally

    from lib.ide import find_ide_server
    ide_server = find_ide_server()
    if ide_server:
        _run_ide_review(edited_files, state, ide_server, re_engage)
    else:
        _run_terminal_review(edited_files, state, re_engage)
