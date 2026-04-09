#!/usr/bin/env python3
"""
Stop hook for claude-diff-review.

Fires when Claude finishes its response. For every file that was edited
during this turn, opens a VS Code diff view showing the original (shadow)
vs. the current (edited) version.

Supports three review modes:
  - "vscode"  : Opens `code --diff` for each file (default)
  - "terminal" : Prints colored git-style diffs to stderr
  - "summary"  : Just prints a summary of changes

Exit codes:
  0 = allow stop (don't force continuation)
"""

import json
import sys
import os
import subprocess
import difflib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.state import (
    read_hook_input,
    get_edited_files,
    get_shadow_path,
    get_working_dir,
    load_state,
    save_state,
    cleanup_session,
    is_binary_file,
    write_conflict_markers,
    resolve_conflict_markers,
)


# ──────────────────────────────────────────────────────────────────────
# ANSI colors for terminal output
# ──────────────────────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"
BG_GREEN = "\033[42m"
BG_RED = "\033[41m"


def format_path(abs_path: str) -> str:
    """Convert absolute path to project-relative for display."""
    wd = get_working_dir()
    try:
        return str(Path(abs_path).relative_to(wd))
    except ValueError:
        return abs_path


def count_diff_lines(shadow_path: Path, real_path: Path) -> tuple:
    """Return (additions, deletions) between two files."""
    try:
        old = shadow_path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        old = []
    try:
        new = real_path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        new = []

    additions = 0
    deletions = 0
    for line in difflib.unified_diff(old, new):
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return additions, deletions


def print_terminal_diff(shadow_path: Path, real_path: Path, rel_name: str):
    """Print a colored unified diff to stderr."""
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
        lineterm=""
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


def open_vscode_diff(shadow_path: Path, real_path: Path, rel_name: str):
    """Open VS Code diff view for a single file."""
    try:
        subprocess.Popen(
            [
                "code", "--diff",
                str(shadow_path),
                str(real_path),
                "--title", f"Review: {rel_name}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except FileNotFoundError:
        # `code` CLI not found — try `code-insiders`
        try:
            subprocess.Popen(
                [
                    "code-insiders", "--diff",
                    str(shadow_path),
                    str(real_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except FileNotFoundError:
            return False


def print_summary_header(edited_files: dict):
    """Print a styled summary banner."""
    total_files = len(edited_files)
    total_edits = sum(edited_files.values())

    sys.stderr.write(f"\n{BOLD}{MAGENTA}{'─' * 60}{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}  ◆ claude-diff-review{RESET}\n")
    sys.stderr.write(f"{DIM}  {total_files} file{'s' if total_files != 1 else ''} changed, "
                     f"{total_edits} edit{'s' if total_edits != 1 else ''} total{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}{'─' * 60}{RESET}\n\n")


def _build_rejection_message(all_rejections: dict, conflict_counts: dict) -> str:
    """Build the re-engagement message Claude sees after the user rejects hunks."""
    lines = [
        "The user reviewed your changes interactively and rejected some of them.",
        "",
        "## What was rejected",
        "",
    ]

    for abs_path, result in all_rejections.items():
        rel = format_path(abs_path)
        total = conflict_counts.get(abs_path, len(result["rejected_hunks"]))
        accepted = total - result["rejected"]
        lines.append(
            f"**{rel}** — {accepted}/{total} hunks accepted, "
            f"{result['rejected']} rejected"
        )

    lines += ["", "## Rejected hunks (original was kept)", ""]

    for abs_path, result in all_rejections.items():
        rel = format_path(abs_path)
        lines.append(f"### {rel}")
        for i, hunk in enumerate(result["rejected_hunks"], 1):
            lines.append(f"\nHunk {i} — user kept original instead of your change:")
            if hunk["original"].strip():
                lines.append("Original (kept):")
                for ln in hunk["original"].splitlines():
                    lines.append(f"  {ln}")
            else:
                lines.append("  Original: (empty — your addition was removed)")
            lines.append("Your proposed change (rejected):")
            if hunk["claude"].strip():
                for ln in hunk["claude"].splitlines():
                    lines.append(f"  {ln}")
            else:
                lines.append("  (empty — your deletion was reverted)")
        lines.append("")

    lines += [
        "Please ask the user what they'd prefer for the rejected hunks, "
        "or proceed if you understand why they were rejected.",
    ]
    return "\n".join(lines)


def run_interactive_review(edited_files: dict, state: dict) -> None:
    """
    Interactive review mode:
      1. Rewrite each changed file with Git conflict markers.
      2. Open the files in VS Code (inline Accept/Reject buttons per hunk).
      3. Wait for the user to press Enter.
      4. Resolve remaining (unresolved) markers → reject → restore original.
      5. If any rejections: output block JSON to re-engage Claude.
    """
    sys.stderr.write(f"\n{BOLD}{MAGENTA}{'─' * 60}{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}  ◆ claude-diff-review — interactive review{RESET}\n")
    sys.stderr.write(
        f"{DIM}  {len(edited_files)} file(s) to review{RESET}\n"
    )
    sys.stderr.write(f"{BOLD}{MAGENTA}{'─' * 60}{RESET}\n\n")

    files_with_markers = {}  # abs_path -> conflict count
    vscode_ok = True

    for abs_path in sorted(edited_files):
        real = Path(abs_path)
        shadow = get_shadow_path(abs_path)
        rel = format_path(abs_path)

        if not real.exists() or abs_path in state.get("binary_files", []):
            sys.stderr.write(f"  {DIM}skip  {rel} (binary or deleted){RESET}\n")
            continue

        count = write_conflict_markers(shadow, real)
        if count == 0:
            sys.stderr.write(f"  {DIM}–     {rel} (no diff){RESET}\n")
            continue

        files_with_markers[abs_path] = count
        sys.stderr.write(
            f"  {CYAN}↯{RESET}  {BOLD}{rel}{RESET}  "
            f"{DIM}({count} conflict{'s' if count != 1 else ''}){RESET}\n"
        )

        if vscode_ok:
            try:
                subprocess.Popen(
                    ["code", abs_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                vscode_ok = False

    if not files_with_markers:
        sys.exit(0)

    # Persist conflict counts so `claude-diff finalize` can compute accepted %
    state["conflict_counts"] = files_with_markers
    save_state(state)

    sys.stderr.write(f"\n")
    if vscode_ok:
        sys.stderr.write(f"  {BOLD}VS Code opened.{RESET} Resolve each conflict inline:\n")
        sys.stderr.write(f"  {GREEN}Accept Incoming Change{RESET} → keep Claude's version\n")
        sys.stderr.write(f"  {RED}Accept Current Change{RESET}  → revert to original\n")
    else:
        sys.stderr.write(
            f"  {YELLOW}⚠ VS Code CLI not found.{RESET} "
            f"Resolve markers manually, then run:\n"
            f"  {BOLD}claude-diff finalize{RESET}\n"
        )

    sys.stderr.write(
        f"\n  {BOLD}Press Enter when you've finished reviewing...{RESET}\n"
    )
    sys.stderr.flush()

    try:
        with open("/dev/tty") as tty:
            tty.readline()
    except Exception:
        pass  # non-interactive environment — fall through immediately

    # Resolve remaining markers
    all_rejections = {}
    total_rejected = 0

    sys.stderr.write(f"\n")
    for abs_path, total_hunks in files_with_markers.items():
        rel = format_path(abs_path)
        result = resolve_conflict_markers(Path(abs_path))
        rejected = result["rejected"]
        accepted = total_hunks - rejected
        total_rejected += rejected

        if rejected:
            all_rejections[abs_path] = result
            sys.stderr.write(
                f"  {YELLOW}↺{RESET}  {BOLD}{rel}{RESET}  "
                f"{GREEN}+{accepted} accepted{RESET}  {RED}-{rejected} rejected{RESET}\n"
            )
        else:
            sys.stderr.write(
                f"  {GREEN}✓{RESET}  {BOLD}{rel}{RESET}  {DIM}all accepted{RESET}\n"
            )

    total_conflict_hunks = sum(files_with_markers.values())
    total_accepted = total_conflict_hunks - total_rejected
    sys.stderr.write(
        f"\n{DIM}  {total_accepted} accepted, {total_rejected} rejected "
        f"across {len(files_with_markers)} file(s){RESET}\n"
    )
    sys.stderr.write(f"{DIM}{'─' * 60}{RESET}\n\n")

    if all_rejections:
        reason = _build_rejection_message(all_rejections, files_with_markers)
        print(json.dumps({"decision": "block", "reason": reason}))

    sys.exit(0)


def main():
    state = load_state()

    # Skip if in auto mode
    if state.get("mode") == "auto":
        sys.exit(0)

    edited_files = get_edited_files()
    if not edited_files:
        sys.exit(0)

    # Determine review mode and scope from config
    config_path = Path.home() / ".claude-diff-review" / "config.json"
    review_mode = "vscode"  # default
    review_scope = "session"  # default
    if config_path.exists():
        try:
            config = __import__("json").loads(config_path.read_text())
            review_mode = config.get("review_mode", "vscode")
            review_scope = config.get("review_scope", "session")
        except Exception:
            pass

    # Also check env var override
    review_mode = os.environ.get("CLAUDE_DIFF_MODE", review_mode)

    if review_mode == "interactive":
        run_interactive_review(edited_files, state)
        # run_interactive_review exits internally

    print_summary_header(edited_files)

    vscode_available = True
    file_stats = []

    for abs_path, edit_count in sorted(edited_files.items()):
        rel = format_path(abs_path)
        shadow = get_shadow_path(abs_path)
        real = Path(abs_path)

        # Determine file status
        is_new = abs_path in state.get("new_files", [])
        is_bin = abs_path in state.get("binary_files", [])

        if is_bin:
            # Binary file — can't diff meaningfully
            tag = f"{YELLOW}[binary]{RESET}"
            edits_str = f"{DIM}({edit_count} edit{'s' if edit_count != 1 else ''}){RESET}"
            sys.stderr.write(f"  {BOLD}{rel}{RESET}  {tag}  {edits_str}\n")
            continue

        additions, deletions = count_diff_lines(shadow, real)
        file_stats.append((rel, edit_count, additions, deletions))

        # Status badge
        if is_new:
            badge = f"{GREEN}[new]{RESET} "
        elif not real.exists():
            badge = f"{RED}[deleted]{RESET} "
        else:
            badge = ""

        # Stats line
        add_str = f"{GREEN}+{additions}{RESET}" if additions else f"{DIM}+0{RESET}"
        del_str = f"{RED}-{deletions}{RESET}" if deletions else f"{DIM}-0{RESET}"
        edits_str = f"{DIM}({edit_count} edit{'s' if edit_count != 1 else ''}){RESET}"

        sys.stderr.write(f"  {badge}{BOLD}{rel}{RESET}  {add_str} {del_str}  {edits_str}\n")

        if review_mode == "terminal":
            sys.stderr.write(f"\n")
            print_terminal_diff(shadow, real, rel)
            sys.stderr.write(f"\n")

        elif review_mode == "vscode":
            # In file-scope mode, skip files already previewed progressively
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
                    sys.stderr.write(f"\n")

    # Footer with restore instructions
    sys.stderr.write(f"\n{DIM}{'─' * 60}{RESET}\n")

    if review_mode == "vscode" and vscode_available:
        sys.stderr.write(
            f"  {CYAN}Diffs opened in VS Code.{RESET}\n"
        )
        if review_scope == "file":
            previewed_count = len(state.get("previewed_files", []))
            if previewed_count:
                sys.stderr.write(
                    f"  {DIM}({previewed_count} file(s) already "
                    f"previewed progressively){RESET}\n"
                )

    sys.stderr.write(
        f"  {DIM}To reject all changes:{RESET}  "
        f"{BOLD}claude-diff restore{RESET}\n"
    )
    sys.stderr.write(
        f"  {DIM}To accept and clean up:{RESET} "
        f"{BOLD}claude-diff accept{RESET}\n"
    )
    sys.stderr.write(
        f"  {DIM}To reject one file:{RESET}     "
        f"{BOLD}claude-diff restore <path>{RESET}\n"
    )
    sys.stderr.write(f"{DIM}{'─' * 60}{RESET}\n\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
