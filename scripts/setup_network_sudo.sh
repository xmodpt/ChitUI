#!/bin/bash
# ============================================================================
# ChitUI — One-Time Network Sudo Setup
# ============================================================================
# Grants ChitUI permission to run scripts/network_helper.sh (and only that
# script) as root without a password, so Settings → Network can change the
# Pi's IP configuration.
#
# Usage (over SSH, once):
#   sudo bash scripts/setup_network_sudo.sh
# ============================================================================

set -e

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✗${NC} $*"; }
info() { echo -e "${BLUE}  →${NC} $*"; }

echo ""
echo -e "${BOLD}ChitUI — Network Sudo Setup${NC}"
echo "───────────────────────────────────────────────"

if [[ $EUID -ne 0 ]]; then
    err "This script must be run with sudo:"
    echo "     sudo bash $0"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/network_helper.sh"
SUDOERS_FILE="/etc/sudoers.d/chitui-network"

# ── Locate the helper ────────────────────────────────────────────────────────
if [[ ! -f "$HELPER" ]]; then
    err "network_helper.sh not found at: $HELPER"
    exit 1
fi
chmod 755 "$HELPER"
ok "Helper script: $HELPER"

# ── Work out which user runs ChitUI ──────────────────────────────────────────
CHITUI_USER=""
if systemctl list-unit-files 2>/dev/null | grep -q '^chitui\.service'; then
    CHITUI_USER=$(systemctl show -p User --value chitui.service 2>/dev/null)
fi
[[ -z "$CHITUI_USER" || "$CHITUI_USER" == "root" ]] && CHITUI_USER="${SUDO_USER:-}"
[[ -z "$CHITUI_USER" ]] && CHITUI_USER=$(stat -c '%U' "$SCRIPT_DIR/..")

if ! id "$CHITUI_USER" >/dev/null 2>&1; then
    err "Could not determine the user ChitUI runs as (guessed: '$CHITUI_USER')"
    exit 1
fi
ok "ChitUI user: $CHITUI_USER"

# ── Install the sudoers rule ─────────────────────────────────────────────────
TMP_SUDOERS=$(mktemp)
cat > "$TMP_SUDOERS" <<EOF
# Installed by ChitUI scripts/setup_network_sudo.sh
# Allows ChitUI to change the system IPv4 configuration via a single,
# argument-validating helper script. Nothing else is granted.
$CHITUI_USER ALL=(root) NOPASSWD: $HELPER
EOF

if ! visudo -cqf "$TMP_SUDOERS"; then
    err "Generated sudoers rule failed validation — nothing was changed."
    rm -f "$TMP_SUDOERS"
    exit 1
fi

install -m 0440 -o root -g root "$TMP_SUDOERS" "$SUDOERS_FILE"
rm -f "$TMP_SUDOERS"
ok "Installed sudoers rule: $SUDOERS_FILE"

# ── Verify it actually works as that user ────────────────────────────────────
if sudo -u "$CHITUI_USER" sudo -n "$HELPER" status lo >/dev/null 2>&1; then
    ok "Verified: $CHITUI_USER can run the helper passwordless"
else
    warn "The rule is installed but the test call failed."
    echo ""
    echo "     Most common cause: ChitUI's systemd unit sets NoNewPrivileges=true,"
    echo "     which blocks sudo entirely regardless of the sudoers rule."
    echo "     Check with:  systemctl cat chitui.service | grep NoNewPrivileges"
fi

# ── Backend check ────────────────────────────────────────────────────────────
if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager; then
    ok "Network backend: NetworkManager"
elif systemctl is-active --quiet dhcpcd; then
    ok "Network backend: dhcpcd"
else
    warn "Neither NetworkManager nor dhcpcd is active — applying settings will fail."
fi

echo ""
echo -e "${GREEN}${BOLD}Done.${NC} Reopen ChitUI → Settings → Network; the warning banner should be gone."
echo ""
echo "To undo:  sudo rm $SUDOERS_FILE"
echo ""
