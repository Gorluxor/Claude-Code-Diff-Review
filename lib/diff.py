"""
claude-diff-review: Display helpers for diff output.

ANSI formatting, diff stats, terminal/VS Code diff display.
Extracted from hooks/stop.py for reuse across hooks and CLI.
"""

import sys
import difflib
import subprocess
from pathlib import Path

from lib.state import get_working_dir


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
# Path formatting
# ──────────────────────────────────────────────────────────────────────

def format_path(abs_path: str) -> str:
    """Relativize a path against the working directory."""
    wd = get_working_dir()
    try:
        return str(Path(abs_path).relative_to(wd))
    except ValueError:
        return abs_path


# ──────────────────────────────────────────────────────────────────────
# Diff stats
# ──────────────────────────────────────────────────────────────────────

def count_diff_lines(shadow_path: Path, real_path: Path) -> tuple:
    """Return (additions, deletions) between shadow and real file."""
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


# ──────────────────────────────────────────────────────────────────────
# Terminal diff display
# ──────────────────────────────────────────────────────────────────────

def print_terminal_diff(shadow_path: Path, real_path: Path, rel_name: str):
    """Print colored unified diff to stderr."""
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


# ──────────────────────────────────────────────────────────────────────
# VS Code diff
# ──────────────────────────────────────────────────────────────────────

def open_vscode_diff(shadow_path: Path, real_path: Path, rel_name: str) -> bool:
    """Open code --diff in the background. Returns True on success."""
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


# ──────────────────────────────────────────────────────────────────────
# Summary header
# ──────────────────────────────────────────────────────────────────────

def print_summary_header(edited_files: dict):
    """Print the review banner with file/edit counts."""
    total_files = len(edited_files)
    total_edits = sum(edited_files.values())
    sys.stderr.write(f"\n{BOLD}{MAGENTA}{'─' * 60}{RESET}\n")
    sys.stderr.write(f"{BOLD}{MAGENTA}  ◆ claude-diff-review{RESET}\n")
    sys.stderr.write(
        f"{DIM}  {total_files} file{'s' if total_files != 1 else ''} changed, "
        f"{total_edits} edit{'s' if total_edits != 1 else ''} total{RESET}\n"
    )
    sys.stderr.write(f"{BOLD}{MAGENTA}{'─' * 60}{RESET}\n\n")
