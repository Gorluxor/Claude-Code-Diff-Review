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
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.state import (
    get_edited_files,
    get_shadow_path,
    load_state,
    save_state,
    is_paused,
    clear_round,
    log_event,
)
from lib.diff import (
    RESET, BOLD, DIM, RED, GREEN, YELLOW, CYAN, MAGENTA,
    format_path, count_diff_lines, print_terminal_diff,
    open_vscode_diff, print_summary_header,
)
from lib.review import run_interactive_review, run_vscode_review

# Re-export for backward compatibility with tests that import from stop
from lib.review import _review_file_hunks, _run_copilot_review  # noqa: F401


def main():
    if is_paused():
        log_event("stop", "Paused — skipping review")
        sys.exit(0)

    state = load_state()

    if state.get("mode") == "auto":
        log_event("stop", "Mode: auto — skipping review")
        sys.exit(0)

    edited_files = get_edited_files()
    if not edited_files:
        log_event("stop", "No edited files — nothing to review")
        sys.exit(0)

    # ── Read config ─────────────────────────────────────────────────
    config_path = Path.home() / ".claude-diff-review" / "config.json"
    review_mode = "interactive"
    review_scope = "session"
    interactive_provider = "claude-code"
    vscode_wait = True
    shadow_update = "session"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            review_mode = config.get("review_mode", "interactive")
            review_scope = config.get("review_scope", "session")
            interactive_provider = config.get("interactive_provider", "claude-code")
            vscode_wait = config.get("vscode_wait", True)
            shadow_update = config.get("shadow_update", "session")
        except Exception:
            pass

    review_mode = os.environ.get("CLAUDE_DIFF_MODE", review_mode)
    log_event(
        "stop", "Stop hook fired",
        review_mode=review_mode,
        scope=review_scope,
        provider=interactive_provider,
        vscode_wait=vscode_wait,
        shadow_update=shadow_update,
        tracked_files=len(edited_files),
    )

    # ── Handle round transition ─────────────────────────────────────
    # If there are decisions from a previous round, filter files
    prev_decisions = state.get("decisions", {})
    accepted_prev = {p for p, d in prev_decisions.items() if d == "accepted"}

    # Files to review this round: exclude previously accepted files
    files_to_review = {
        p: c for p, c in edited_files.items()
        if p not in accepted_prev
    }

    if not files_to_review:
        log_event("stop", "All files already accepted in previous round(s) — exiting")
        sys.stderr.write(
            f"\n{DIM}  [diff-review] All files already accepted in previous round(s).{RESET}\n"
        )
        sys.exit(0)

    log_event("stop", "Files to review", count=len(files_to_review),
              files=",".join(os.path.basename(p) for p in sorted(files_to_review)))

    # ── Interactive mode ────────────────────────────────────────────
    if review_mode == "interactive":
        state["current_file"] = None
        save_state(state)
        log_event("stop", "Dispatching to interactive review", provider=interactive_provider)
        run_interactive_review(files_to_review, state, provider=interactive_provider,
                               shadow_update=shadow_update)
        # always exits internally

    # ── VS Code mode (blocking or fire-and-forget) ──────────────────
    if review_mode == "vscode" and vscode_wait:
        state["current_file"] = None
        save_state(state)
        log_event("stop", "Dispatching to VS Code blocking review")
        run_vscode_review(files_to_review, state, re_engage=True, wait=True,
                          shadow_update=shadow_update)
        # always exits internally

    # ── Non-interactive modes (vscode no-wait / terminal / summary) ─
    print_summary_header(files_to_review)
    vscode_available = True
    previewed = set(state.get("previewed_files", []))

    for abs_path, edit_count in sorted(files_to_review.items()):
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
            # Skip files already previewed progressively
            if review_scope == "file" and abs_path in previewed:
                continue
            if vscode_available:
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
        if review_scope == "file" and previewed:
            sys.stderr.write(f"  {DIM}({len(previewed)} already previewed progressively){RESET}\n")
    sys.stderr.write(
        f"  {DIM}To accept and clean up:{RESET} {BOLD}claude-diff accept{RESET}\n"
    )
    sys.stderr.write(f"{DIM}{'─' * 60}{RESET}\n\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
