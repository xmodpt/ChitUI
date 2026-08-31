#!/bin/bash
# ============================================================================
# ChitUI — Raspberry Pi UART / Serial Port Setup
# ============================================================================
# Fixes the most common causes of [Errno 5] Input/output error on /dev/ttyS0
# and correctly configures the Pi's hardware UART for use with ChitUI.
#
# What this script does:
#   1. Detects your Pi model and UART mapping
#   2. Disables the serial console (getty) that conflicts with ChitUI
#   3. Moves Bluetooth off the hardware UART (Pi 3/4/5)
#   4. Enables the hardware UART overlay in /boot/firmware/config.txt
#   5. Adds your user to the 'dialout' group
#   6. Runs a loopback write test to verify the port works
#   7. Prints the correct port to use in ChitUI settings
#
# Usage:
#   bash scripts/setup_uart.sh
#   (no sudo needed — will prompt when required)
# ============================================================================

set -e

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'
BOLD='\033[1m'

# ── Helpers ───────────────────────────────────────────────────────────────────
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✗${NC} $*"; }
info() { echo -e "${BLUE}  →${NC} $*"; }
section() {
    echo ""
    echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
    printf "${CYAN}${BOLD}║  %-44s║${NC}\n" "$1"
    echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
    echo ""
}

ACTUAL_USER="${SUDO_USER:-$USER}"
CHANGES_MADE=0
REBOOT_NEEDED=0

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo -e "${CYAN}${BOLD}"
cat << "EOF"
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              ███████╗██╗  ██╗██╗████████╗██╗   ██╗██╗       ║
║              ██╔════╝██║  ██║██║╚══██╔══╝██║   ██║██║       ║
║              ██║     ███████║██║   ██║   ██║   ██║██║       ║
║              ██║     ██╔══██║██║   ██║   ██║   ██║██║       ║
║              ███████╗██║  ██║██║   ██║   ╚██████╔╝██║       ║
║              ╚══════╝╚═╝  ╚═╝╚═╝   ╚═╝    ╚═════╝ ╚═╝       ║
║                                                              ║
║              UART / Serial Port Setup Script                ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"
echo -e "${BLUE}This script will configure your Raspberry Pi's serial port"
echo -e "so ChitUI can communicate with your UART-connected printer.${NC}"
echo ""
echo -e "${YELLOW}Common error this fixes:${NC}"
echo -e "  ${RED}UART write error: write failed: [Errno 5] Input/output error${NC}"
echo ""

if [ "$EUID" -eq 0 ]; then
    err "Do not run as root. Run as your normal user — sudo will be called when needed."
    exit 1
fi

read -p "$(echo -e ${BOLD}Press Enter to begin...${NC})" -r
echo ""

# ============================================================================
# STEP 1: Detect Pi model and current UART mapping
# ============================================================================
section "Step 1: Detecting Pi Model & UART Mapping"

PI_MODEL="unknown"
if [ -f /proc/device-tree/model ]; then
    PI_MODEL=$(tr -d '\0' < /proc/device-tree/model)
fi
info "Model: ${PI_MODEL}"

# Determine boot config location (varies between OS versions)
if [ -f /boot/firmware/config.txt ]; then
    BOOT_CFG="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    BOOT_CFG="/boot/config.txt"
else
    err "Cannot find /boot/firmware/config.txt or /boot/config.txt"
    exit 1
fi
info "Boot config: ${BOOT_CFG}"

# Determine Pi generation
PI_GEN=0
echo "$PI_MODEL" | grep -qi "Pi 5"  && PI_GEN=5
echo "$PI_MODEL" | grep -qi "Pi 4"  && PI_GEN=4
echo "$PI_MODEL" | grep -qi "Pi 3"  && PI_GEN=3
echo "$PI_MODEL" | grep -qi "Pi 2"  && PI_GEN=2
echo "$PI_MODEL" | grep -qi "Pi Ze" && PI_GEN=0  # Zero = gen 0 for our purposes

# Bluetooth exists on Pi 3 and above (not Zero W for our purposes here)
HAS_BT=0
[ "$PI_GEN" -ge 3 ] && echo "$PI_MODEL" | grep -qiE "Pi [3-9]|Pi Zero 2" && HAS_BT=1

# Resolve current serial symlinks
SERIAL0=$(readlink -f /dev/serial0 2>/dev/null || echo "not found")
SERIAL1=$(readlink -f /dev/serial1 2>/dev/null || echo "not found")
info "serial0 → ${SERIAL0}"
info "serial1 → ${SERIAL1}"

# Determine recommended port
if [ "$HAS_BT" -eq 1 ]; then
    info "Pi with Bluetooth detected — hardware UART (ttyAMA0) is currently shared with BT"
    info "This script will move BT to mini-UART and give ttyAMA0 to your printer"
    RECOMMENDED_PORT="/dev/ttyAMA0"
else
    info "No Bluetooth conflict — ttyAMA0 / ttyS0 are available"
    RECOMMENDED_PORT="/dev/ttyS0"
fi

ok "Detection complete"

# ============================================================================
# STEP 2: Disable serial console (getty)
# ============================================================================
section "Step 2: Disable Serial Console"

echo -e "${BLUE}The Pi runs a login shell (getty) on the serial port by default."
echo -e "This conflicts with ChitUI and causes [Errno 5].${NC}"
echo ""

CONSOLE_ACTIVE=0
if systemctl is-active --quiet serial-getty@ttyS0.service 2>/dev/null; then
    CONSOLE_ACTIVE=1
    warn "serial-getty@ttyS0 is ACTIVE — this is the cause of your [Errno 5] error"
fi
if systemctl is-active --quiet serial-getty@ttyAMA0.service 2>/dev/null; then
    CONSOLE_ACTIVE=1
    warn "serial-getty@ttyAMA0 is ACTIVE"
fi

if [ "$CONSOLE_ACTIVE" -eq 0 ]; then
    ok "Serial console is already disabled"
else
    read -p "$(echo -e ${BOLD}Disable serial console now? [Y/n]${NC} )" -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        sudo systemctl stop    serial-getty@ttyS0.service   2>/dev/null || true
        sudo systemctl disable serial-getty@ttyS0.service   2>/dev/null || true
        sudo systemctl stop    serial-getty@ttyAMA0.service 2>/dev/null || true
        sudo systemctl disable serial-getty@ttyAMA0.service 2>/dev/null || true
        ok "Serial console disabled"
        CHANGES_MADE=1
        REBOOT_NEEDED=1

        # Also remove console=serial0 from cmdline.txt if present
        CMDLINE=""
        if   [ -f /boot/firmware/cmdline.txt ]; then CMDLINE="/boot/firmware/cmdline.txt"
        elif [ -f /boot/cmdline.txt ];           then CMDLINE="/boot/cmdline.txt"
        fi
        if [ -n "$CMDLINE" ]; then
            if grep -q "console=serial0\|console=ttyS0\|console=ttyAMA0" "$CMDLINE"; then
                info "Removing serial console from kernel cmdline..."
                sudo sed -i 's/console=serial0,[0-9]* //g' "$CMDLINE"
                sudo sed -i 's/console=ttyS0,[0-9]* //g'   "$CMDLINE"
                sudo sed -i 's/console=ttyAMA0,[0-9]* //g' "$CMDLINE"
                ok "Removed serial console from ${CMDLINE}"
            else
                ok "Kernel cmdline already clean"
            fi
        fi
    else
        warn "Skipped — serial console still active, [Errno 5] may persist"
    fi
fi

# ============================================================================
# STEP 3: Handle Bluetooth / UART conflict (Pi 3/4/5 only)
# ============================================================================
section "Step 3: Bluetooth vs Hardware UART"

if [ "$HAS_BT" -eq 0 ]; then
    ok "No Bluetooth on this Pi — no conflict to resolve"
else
    echo -e "${BLUE}On Pi 3/4/5, Bluetooth occupies the full hardware UART (ttyAMA0)."
    echo -e "This pushes your app onto the mini-UART (ttyS0) which is unstable"
    echo -e "and depends on CPU clock speed — causing [Errno 5] under load."
    echo ""
    echo -e "Fix: move Bluetooth to the mini-UART instead.${NC}"
    echo ""
    echo -e "${YELLOW}  Option A (recommended): disable-bt${NC}"
    echo -e "  Disables Bluetooth entirely. Frees ttyAMA0 for your printer."
    echo ""
    echo -e "${YELLOW}  Option B: miniuart-bt${NC}"
    echo -e "  Keeps Bluetooth on mini-UART. ttyAMA0 still available for printer."
    echo -e "  Bluetooth quality degrades slightly. Good if you need BT."
    echo ""
    echo -e "${YELLOW}  Option C: skip${NC}"
    echo -e "  Leave as-is. Use ttyS0 with reduced reliability."
    echo ""

    BT_OVERLAY=""
    if grep -q "dtoverlay=disable-bt"  "$BOOT_CFG" 2>/dev/null; then
        ok "disable-bt overlay already set"
        BT_OVERLAY="disable-bt"
    elif grep -q "dtoverlay=miniuart-bt" "$BOOT_CFG" 2>/dev/null; then
        ok "miniuart-bt overlay already set"
        BT_OVERLAY="miniuart-bt"
    fi

    if [ -z "$BT_OVERLAY" ]; then
        read -p "$(echo -e ${BOLD}Choose [A/b/c]:${NC} )" -n 1 -r
        echo ""
        CHOICE="${REPLY,,}"

        if [[ "$CHOICE" == "b" ]]; then
            BT_OVERLAY="miniuart-bt"
        elif [[ "$CHOICE" == "c" ]]; then
            warn "Skipping BT fix — ttyS0 may be unreliable"
            RECOMMENDED_PORT="/dev/ttyS0"
            BT_OVERLAY="skip"
        else
            BT_OVERLAY="disable-bt"
        fi

        if [[ "$BT_OVERLAY" != "skip" ]]; then
            info "Adding dtoverlay=${BT_OVERLAY} to ${BOOT_CFG}..."
            # Remove any conflicting BT overlays first
            sudo sed -i '/dtoverlay=disable-bt/d'  "$BOOT_CFG"
            sudo sed -i '/dtoverlay=miniuart-bt/d' "$BOOT_CFG"
            echo "dtoverlay=${BT_OVERLAY}" | sudo tee -a "$BOOT_CFG" > /dev/null
            ok "dtoverlay=${BT_OVERLAY} added"

            if [[ "$BT_OVERLAY" == "disable-bt" ]]; then
                sudo systemctl disable hciuart 2>/dev/null || true
                ok "hciuart service disabled"
            fi

            RECOMMENDED_PORT="/dev/ttyAMA0"
            CHANGES_MADE=1
            REBOOT_NEEDED=1
        fi
    fi
fi

# ============================================================================
# STEP 4: Enable UART hardware in config.txt
# ============================================================================
section "Step 4: Enable Hardware UART"

echo -e "${BLUE}Ensuring enable_uart=1 is set in ${BOOT_CFG}...${NC}"
echo ""

if grep -q "^enable_uart=1" "$BOOT_CFG" 2>/dev/null; then
    ok "enable_uart=1 already set"
elif grep -q "^enable_uart=0" "$BOOT_CFG" 2>/dev/null; then
    warn "enable_uart=0 found — changing to enable_uart=1"
    sudo sed -i 's/^enable_uart=0/enable_uart=1/' "$BOOT_CFG"
    ok "enable_uart=1 set"
    CHANGES_MADE=1
    REBOOT_NEEDED=1
else
    info "enable_uart=1 not found — adding it..."
    echo "enable_uart=1" | sudo tee -a "$BOOT_CFG" > /dev/null
    ok "enable_uart=1 added to ${BOOT_CFG}"
    CHANGES_MADE=1
    REBOOT_NEEDED=1
fi

# ============================================================================
# STEP 5: User group permissions
# ============================================================================
section "Step 5: User Permissions (dialout group)"

echo -e "${BLUE}The 'dialout' group is required to access serial ports without sudo.${NC}"
echo ""

if groups "$ACTUAL_USER" | grep -q dialout; then
    ok "${ACTUAL_USER} is already in the dialout group"
else
    info "Adding ${ACTUAL_USER} to dialout group..."
    sudo usermod -aG dialout "$ACTUAL_USER"
    ok "${ACTUAL_USER} added to dialout"
    warn "You will need to log out and back in (or reboot) for this to take effect"
    CHANGES_MADE=1
fi

# Also ensure gpio group membership while we're at it
if ! groups "$ACTUAL_USER" | grep -q gpio; then
    sudo usermod -aG gpio "$ACTUAL_USER" 2>/dev/null || true
    ok "Added ${ACTUAL_USER} to gpio group"
fi

# ============================================================================
# STEP 6: Verify pyserial is installed
# ============================================================================
section "Step 6: Verify pyserial"

if python3 -c "import serial" 2>/dev/null; then
    PYSERIAL_VER=$(python3 -c "import serial; print(serial.__version__)")
    ok "pyserial ${PYSERIAL_VER} is installed"
else
    warn "pyserial is not installed — installing now..."
    pip3 install pyserial --break-system-packages 2>/dev/null || \
    pip3 install --user pyserial
    if python3 -c "import serial" 2>/dev/null; then
        ok "pyserial installed"
    else
        err "Failed to install pyserial — run: pip3 install pyserial --break-system-packages"
    fi
fi

# ============================================================================
# STEP 7: Quick write test (only if no reboot needed)
# ============================================================================
section "Step 7: Serial Port Write Test"

if [ "$REBOOT_NEEDED" -eq 1 ]; then
    warn "Skipping live test — changes require a reboot first"
    info "After rebooting, run this script again to verify the port works"
else
    TEST_PORT="${RECOMMENDED_PORT}"
    echo -e "${BLUE}Testing write to ${TEST_PORT}...${NC}"
    echo ""

    if [ ! -e "$TEST_PORT" ]; then
        err "Port ${TEST_PORT} does not exist"
        info "Check your wiring and that the UART overlay is active"
    elif [ ! -w "$TEST_PORT" ]; then
        err "Cannot write to ${TEST_PORT} — permission denied"
        warn "Try logging out and back in so the dialout group takes effect"
    else
        # Try a non-destructive write (M4002 is a safe ping command)
        if python3 - << 'PYEOF'
import serial, time, sys
try:
    s = serial.Serial(
        port=None,
        baudrate=115200,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        write_timeout=2,
    )
    import sys
    s.port = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyAMA0'
    s.open()
    s.write(b"M4002\r\n")
    s.flush()
    s.close()
    print("OK")
    sys.exit(0)
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)
PYEOF
        then
            ok "Write to ${TEST_PORT} succeeded — port is working"
        else
            err "Write test failed on ${TEST_PORT}"
            warn "This may still work after a full reboot if changes were applied above"
        fi
    fi
fi

# ============================================================================
# Summary
# ============================================================================
clear
echo -e "${GREEN}${BOLD}"
cat << "EOF"
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║                  ✓  UART Setup Complete                     ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}${BOLD}  Summary${NC}"
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""

if [ "$CHANGES_MADE" -eq 0 ]; then
    ok "Everything was already correctly configured"
else
    ok "Configuration changes applied"
fi

echo ""
echo -e "${CYAN}${BOLD}  Use this port in ChitUI settings:${NC}"
echo ""
echo -e "    ${GREEN}${BOLD}${RECOMMENDED_PORT}${NC}"
echo ""
echo -e "  Baud rate: ${YELLOW}115200${NC}  (standard for Elegoo Mars 2)"
echo ""

echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}${BOLD}  Wiring Reminder (3.3 V logic ONLY)${NC}"
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Pi GPIO14  (TX, pin 8)   →  Printer RX"
echo -e "  Pi GPIO15  (RX, pin 10)  →  Printer TX"
echo -e "  Pi GND     (pin 6)       →  Printer GND"
echo ""
echo -e "  ${RED}${BOLD}DO NOT connect 5 V — 3.3 V logic only!${NC}"
echo -e "  ${YELLOW}Power the Pi from its own USB-C supply, not from the printer.${NC}"
echo ""

if [ "$REBOOT_NEEDED" -eq 1 ]; then
    echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}${BOLD}  ⚠  Reboot Required${NC}"
    echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  Changes to ${BOOT_CFG} and system services require a reboot."
    echo ""
    read -p "$(echo -e ${BOLD}Reboot now? [Y/n]${NC} )" -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo ""
        info "Rebooting in 3 seconds..."
        sleep 3
        sudo reboot
    else
        echo ""
        warn "Remember to reboot before testing your UART printer in ChitUI"
        echo -e "  ${YELLOW}sudo reboot${NC}"
        echo ""
    fi
else
    echo -e "${GREEN}${BOLD}  No reboot needed — your serial port is ready!${NC}"
    echo ""
    echo -e "  Start or restart ChitUI and add your printer with:"
    echo -e "  Port:     ${YELLOW}${RECOMMENDED_PORT}${NC}"
    echo -e "  Baudrate: ${YELLOW}115200${NC}"
    echo ""
fi
