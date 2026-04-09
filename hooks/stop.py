#!/usr/bin/env python3
"""
Stop hook for claude-diff-review.

Fires when Claude finishes its response. Behaviour depends on review_mode config:

  "interactive" (default)
      Checks for a live VS Code IDE connection (Claude Code extension).
      If found: calls the native openDiff MCP RPC — opens VS Code's built-in
        side-by-side diff editor per file. User accepts/edits/rejects per hunk
        natively, then saves. We read the result and re-engage Claude if anything
        was rejected or modified.
      If not found: falls back to terminal per-hunk y/n review (like git add -p),
        with code --diff opened for visual context.

  "vscode"   : Opens `code --diff` for each file (passive, view only)
  "terminal" : Prints colored unified diffs to stderr
  "summary"  : Prints a summary of changes with no diff content

Exit codes:
  0 = allow stop
  (block JSON printed to stdout causes Claude Code to re-engage Claude)
"""

import json
import sys
import os
import subprocess
import difflib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.state import (
    get_edited_files,
    get_shadow_path,
    get_working_dir,
    load_state,
    is_paused,
)


# ──────────────────────────────────────────────────────────────────────
# ANSI colors
# ──────────────────────────────────────────────────────────────────────

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────

def format_path(abs_path: str) -> str:
    wd = get_working_dir()
    try:
        return str(Path(abs_path).relative_to(wd))
    except ValueError:
        return abs_path


def count_diff_lines(shadow_path: Path, real_path: Path) -> tuple:
    try:
        old = shadow_path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        old = []
    try:
        new = real_path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        new = []
    adds = dels = 0
    for line in difflib.unified_diff(old, new):
        if line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            dels += 1
    return adds, dels


def print_terminal_diff(shadow_path: Path, real_path: Path, rel_name: str):
    try:
        old = shadow_path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        old = []
    try:
        new = real_path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        new = []
    diff = list(difflib.unified_diff(
        old, new,
        fromfile=f"a/{rel_name} (original)",
        tofile=f"b/{rel_name} (edited)",
        lineterm="",
    ))
    if not diff:
        sys.stderr.write(f"  {DIM}(no changes){RESET}\n")
        return
    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            sys.stderr.write(f"  {BOLD}{line}{RESET}\n")
        elif line.startswith("@@"):
            sys.stderr.write(f"  {CYAN}{line}{RESET}\n")
        elif line.startswith("+"):
            sys.stderr.write(f"  {GREEN}{line}{RESET}\n")
        elif line.startswith("-"):
            sys.stderr.write(f"  {RED}{line}{RESET}\n")
        else:
            sys.stderr.write(f"  {line}\n")


def open_vscode_diff(shadow_path: Path, real_path: Path, rel_name: str) -> bool:
    for cmd in ("code", "code-insiders"):
        try:
            subprocess.Popen(
                [cmd, "--diff", str(shadow_path), str(real_path),
                 "--title", f"Review: {rel_name}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except FileNotFoundError:
            continue
    return False


def print_summary_header(edited_files: dict):
    total_files = len(edited_files)
    total_edits = sum(edited_files.values())
    sys.stderr.write(f"\n{BOLD}{MAGENTA}{'─' * 60}{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}  ◆ claude-diff-review{RESET}\n")
    sys.stderr.write(
        f"{DIM}  {total_files} file{'s' if total_files != 1 else ''} changed, "
        f"{total_edits} edit{'s' if total_edits != 1 else ''} total{RESET}\n"
    )
    sys.stderr.write(f"{BOLD}{MAGENTA}{'─' * 60}{RESET}\n\n")


# ──────────────────────────────────────────────────────────────────────
# Interactive review — IDE path (native VS Code openDiff RPC)
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


def _run_ide_review(
    edited_files: dict, state: dict, ide_server: dict, re_engage: bool
) -> None:
    """
    Review via Claude Code's native VS Code openDiff MCP RPC.

    For each file:
      1. Save Claude's content; restore file to original (shadow).
      2. Call openDiff → VS Code opens side-by-side diff, user edits/saves.
      3. FILE_SAVED = user saved (possibly with per-hunk reverts).
         DIFF_REJECTED = user explicitly rejected all changes.
         TAB_CLOSED = treat as accept.
      4. Compare final file against Claude's version to detect modifications.
    """
    from lib.ide import open_diff_in_ide

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

    for abs_path in sorted(edited_files):
        real = Path(abs_path)
        shadow = get_shadow_path(abs_path)
        rel = format_path(abs_path)

        if not real.exists() or abs_path in state.get("binary_files", []):
            continue

        try:
            original_content = shadow.read_text(errors="replace") if shadow.exists() else ""
            claude_content = real.read_text(errors="replace")
        except Exception:
            continue

        if original_content == claude_content:
            sys.stderr.write(f"  {DIM}–  {rel} (unchanged){RESET}\n")
            continue

        sys.stderr.write(f"  {CYAN}▶{RESET}  {BOLD}{rel}{RESET}  {DIM}(opening diff…){RESET}\n")
        sys.stderr.flush()

        # Pass shadow as left (original) and real as right (Claude's version).
        # Do NOT touch the file on disk — VS Code reads both paths directly.
        response = open_diff_in_ide(
            ide_server, str(shadow), str(real),
            f"Review: {rel}", timeout=600,
        )

        if response == "DIFF_REJECTED":
            # User explicitly rejected — restore original
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
            # TAB_CLOSED or None (timeout/error) → keep Claude's version as-is
            label = "no response — kept Claude's version" if response is None else "accepted"
            color = YELLOW if response is None else GREEN
            marker = "?" if response is None else "✓"
            sys.stderr.write(
                f"  {color}{marker}{RESET}  {BOLD}{rel}{RESET}  {DIM}{label}{RESET}\n"
            )

    sys.stderr.write(f"\n{DIM}{'─' * 60}{RESET}\n")

    if decisions and re_engage:
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
    sys.stderr.write(f"\n{BOLD}{MAGENTA}{'─' * 60}{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}  ◆ claude-diff-review — interactive (terminal){RESET}\n")
    sys.stderr.write(f"{DIM}  No VS Code IDE connection — using terminal review{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}{'─' * 60}{RESET}\n\n")

    vscode_ok = True
    for abs_path in sorted(edited_files):
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
        if abs_path in state.get("binary_files", []) or not Path(abs_path).exists():
            continue
        result = _review_file_hunks(abs_path, tty)
        total_accepted += result["accepted"]
        total_rejected += result["rejected"]
        rel = format_path(abs_path)
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

    if all_rejections and re_engage:
        decisions = {}
        for abs_path, r in all_rejections.items():
            try:
                shadow = get_shadow_path(abs_path)
                original = shadow.read_text(errors="replace") if shadow.exists() else ""
            except Exception:
                original = ""
            # Reconstruct claude's version from original + rejected hunks
            decisions[abs_path] = {
                "type": "rejected",
                "original": original,
                "claude": "",
                "final": Path(abs_path).read_text(errors="replace"),
            }
        print(json.dumps({"decision": "block", "reason": _build_rejection_message(decisions)}))
    elif all_rejections:
        sys.stderr.write(
            f"  {YELLOW}Rejections recorded.{RESET}  "
            f"{DIM}Claude not notified — describe changes manually to re-engage.{RESET}\n\n"
        )

    sys.exit(0)


def run_interactive_review(
    edited_files: dict, state: dict, re_engage: bool = True
) -> None:
    """
    Entry point for interactive review.
    Uses native VS Code IDE RPC if available, falls back to terminal.
    """
    from lib.ide import find_ide_server
    ide_server = find_ide_server()
    if ide_server:
        _run_ide_review(edited_files, state, ide_server, re_engage)
    else:
        _run_terminal_review(edited_files, state, re_engage)


# ──────────────────────────────────────────────────────────────────────
# Non-interactive modes (vscode / terminal / summary)
# ──────────────────────────────────────────────────────────────────────

def main():
    if is_paused():
        sys.exit(0)

    state = load_state()

    if state.get("mode") == "auto":
        sys.exit(0)

    edited_files = get_edited_files()
    if not edited_files:
        sys.exit(0)

    config_path = Path.home() / ".claude-diff-review" / "config.json"
    review_mode = "interactive"
    review_scope = "session"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            review_mode = config.get("review_mode", "interactive")
            review_scope = config.get("review_scope", "session")
        except Exception:
            pass

    review_mode = os.environ.get("CLAUDE_DIFF_MODE", review_mode)

    if review_mode == "interactive":
        run_interactive_review(edited_files, state)
        # always exits internally

    print_summary_header(edited_files)
    vscode_available = True

    for abs_path, edit_count in sorted(edited_files.items()):
        rel = format_path(abs_path)
        shadow = get_shadow_path(abs_path)
        real = Path(abs_path)

        is_new = abs_path in state.get("new_files", [])
        is_bin = abs_path in state.get("binary_files", [])

        if is_bin:
            sys.stderr.write(
                f"  {BOLD}{rel}{RESET}  {YELLOW}[binary]{RESET}  "
                f"{DIM}({edit_count} edit{'s' if edit_count != 1 else ''}){RESET}\n"
            )
            continue

        additions, deletions = count_diff_lines(shadow, real)
        badge = (
            f"{GREEN}[new]{RESET} " if is_new
            else f"{RED}[deleted]{RESET} " if not real.exists()
            else ""
        )
        add_str = f"{GREEN}+{additions}{RESET}" if additions else f"{DIM}+0{RESET}"
        del_str = f"{RED}-{deletions}{RESET}" if deletions else f"{DIM}-0{RESET}"
        edits_str = f"{DIM}({edit_count} edit{'s' if edit_count != 1 else ''}){RESET}"
        sys.stderr.write(f"  {badge}{BOLD}{rel}{RESET}  {add_str} {del_str}  {edits_str}\n")

        if review_mode == "terminal":
            sys.stderr.write("\n")
            print_terminal_diff(shadow, real, rel)
            sys.stderr.write("\n")

        elif review_mode == "vscode":
            already_previewed = (
                review_scope == "file"
                and abs_path in state.get("previewed_files", [])
            )
            if vscode_available and not already_previewed:
                opened = open_vscode_diff(shadow, real, rel)
                if not opened:
                    vscode_available = False
                    sys.stderr.write(
                        f"\n  {YELLOW}⚠ VS Code CLI not found. "
                        f"Falling back to terminal diff.{RESET}\n\n"
                    )
                    print_terminal_diff(shadow, real, rel)
                    sys.stderr.write("\n")

    sys.stderr.write(f"\n{DIM}{'─' * 60}{RESET}\n")
    if review_mode == "vscode" and vscode_available:
        sys.stderr.write(f"  {CYAN}Diffs opened in VS Code.{RESET}\n")
        if review_scope == "file":
            n = len(state.get("previewed_files", []))
            if n:
                sys.stderr.write(f"  {DIM}({n} already previewed progressively){RESET}\n")
    sys.stderr.write(
        f"  {DIM}To reject all changes:{RESET}  {BOLD}claude-diff restore{RESET}\n"
        f"  {DIM}To accept and clean up:{RESET} {BOLD}claude-diff accept{RESET}\n"
        f"  {DIM}To reject one file:{RESET}     {BOLD}claude-diff restore <path>{RESET}\n"
    )
    sys.stderr.write(f"{DIM}{'─' * 60}{RESET}\n\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
