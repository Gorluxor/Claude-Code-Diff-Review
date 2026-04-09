#!/usr/bin/env bash
#
# claude-diff-review installer
#
# Usage:
#   curl -fsSL .../install.sh | bash
#   — or —
#   ./install.sh
#

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

info()  { echo -e "${BLUE}ℹ${RESET} $*"; }
ok()    { echo -e "${GREEN}✓${RESET} $*"; }
warn()  { echo -e "${YELLOW}⚠${RESET} $*"; }
fail()  { echo -e "${RED}✗${RESET} $*"; exit 1; }

# ── Detect install location ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${CLAUDE_DIFF_INSTALL_DIR:-$HOME/.claude-diff-review/app}"

echo ""
echo -e "${BOLD}${MAGENTA}  ◆ claude-diff-review installer${RESET}"
echo -e "${DIM}  ─────────────────────────────────${RESET}"
echo ""

# ── Prerequisites ──────────────────────────────────────────────────
info "Checking prerequisites..."

# Python 3
if ! command -v python3 &>/dev/null; then
    fail "python3 is required but not found. Please install Python 3.8+."
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "  Python ${PYTHON_VERSION}"

# Claude Code
if command -v claude &>/dev/null; then
    ok "  Claude Code CLI found"
else
    warn "  Claude Code CLI not found in PATH (install will continue)"
fi

# VS Code (optional)
if command -v code &>/dev/null; then
    ok "  VS Code CLI found"
elif command -v code-insiders &>/dev/null; then
    ok "  VS Code Insiders CLI found"
else
    warn "  VS Code CLI not found — terminal diffs will be used as fallback"
fi

# ── Copy files ─────────────────────────────────────────────────────
info "Installing to ${INSTALL_DIR}..."

mkdir -p "$INSTALL_DIR"

# If running from the source directory, copy everything
if [ -f "$SCRIPT_DIR/bin/claude-diff" ]; then
    cp -r "$SCRIPT_DIR/hooks" "$INSTALL_DIR/"
    cp -r "$SCRIPT_DIR/lib" "$INSTALL_DIR/"
    cp -r "$SCRIPT_DIR/bin" "$INSTALL_DIR/"
else
    fail "Cannot find source files. Run this script from the project root."
fi

chmod +x "$INSTALL_DIR/bin/claude-diff"
chmod +x "$INSTALL_DIR/hooks/"*.py

ok "Files installed"

# ── Symlink CLI ────────────────────────────────────────────────────
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

LINK_TARGET="$BIN_DIR/claude-diff"
ln -sf "$INSTALL_DIR/bin/claude-diff" "$LINK_TARGET"
chmod +x "$LINK_TARGET"

# Check if ~/.local/bin is in PATH
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "$BIN_DIR is not in your PATH."
    echo ""
    echo -e "  Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    echo -e "  ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
    echo ""
fi

ok "CLI linked: ${LINK_TARGET}"

# ── Default config ─────────────────────────────────────────────────
CONFIG_DIR="$HOME/.claude-diff-review"
CONFIG_FILE="$CONFIG_DIR/config.json"

if [ ! -f "$CONFIG_FILE" ]; then
    mkdir -p "$CONFIG_DIR"
    cat > "$CONFIG_FILE" << 'EOF'
{
  "review_mode": "interactive",
  "auto_cleanup": true,
  "review_scope": "session"
}
EOF
    ok "Default config created: ${CONFIG_FILE}"
else
    ok "Existing config preserved: ${CONFIG_FILE}"
fi

# ── Install hooks into Claude Code ─────────────────────────────────
echo ""
info "Installing hooks into Claude Code..."

python3 "$INSTALL_DIR/bin/claude-diff" install

# ── Done ───────────────────────────────────────────────────────────
echo -e "${BOLD}${MAGENTA}  ─────────────────────────────────${RESET}"
echo ""
echo -e "  ${BOLD}Quick start:${RESET}"
echo -e "  1. Restart Claude Code"
echo -e "  2. Ask Claude to edit some files"
echo -e "  3. When Claude finishes → diffs open in VS Code"
echo -e "  4. ${BOLD}claude-diff accept${RESET} or ${BOLD}claude-diff restore${RESET}"
echo ""
echo -e "  ${DIM}Run ${BOLD}claude-diff --help${RESET}${DIM} for all commands${RESET}"
echo ""
