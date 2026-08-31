#!/bin/bash
# ChitUI - Complete Installation Script
# This script installs all components with optional choices for each step

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ACTUAL_USER="${SUDO_USER:-$USER}"
ACTUAL_HOME=$(eval echo ~$ACTUAL_USER)

# Banner
clear
echo -e "${CYAN}${BOLD}"
cat << "EOF"
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              ███████╗██╗  ██╗██╗████████╗██╗   ██╗██╗        ║
║              ██╔════╝██║  ██║██║╚══██╔══╝██║   ██║██║        ║
║              ██║     ███████║██║   ██║   ██║   ██║██║        ║
║              ██║     ██╔══██║██║   ██║   ██║   ██║██║        ║
║              ███████╗██║  ██║██║   ██║   ╚██████╔╝██║        ║
║              ╚══════╝╚═╝  ╚═╝╚═╝   ╚═╝    ╚═════╝ ╚═╝        ║
║                                                              ║
║                  Complete Installation Script                ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

echo -e "${BLUE}Welcome to ChitUI Installation!${NC}"
echo -e "${BLUE}This installer will guide you through setting up ChitUI.${NC}"
echo ""
echo -e "${YELLOW}You will be asked before installing each component.${NC}"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}✗ Please do NOT run this script as root or with sudo${NC}"
    echo -e "${YELLOW}  The script will prompt for sudo password when needed${NC}"
    exit 1
fi

echo -e "${BLUE}Configuration:${NC}"
echo -e "  User:           ${GREEN}$ACTUAL_USER${NC}"
echo -e "  Home:           ${GREEN}$ACTUAL_HOME${NC}"
echo -e "  Install Path:   ${GREEN}$SCRIPT_DIR${NC}"
echo ""

# Check if installed in recommended location
RECOMMENDED_PATH="$ACTUAL_HOME/ChitUI"
if [ "$SCRIPT_DIR" != "$RECOMMENDED_PATH" ]; then
    echo -e "${YELLOW}Note: ChitUI is recommended to be installed at ~/ChitUI${NC}"
    echo -e "${YELLOW}      Current location: $SCRIPT_DIR${NC}"
    echo ""
fi

read -p "$(echo -e ${BOLD}Press Enter to begin installation...${NC})" -r
echo ""

# ============================================================================
# STEP 1: System Requirements Check
# ============================================================================

echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  Step 1: System Requirements Check           ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

# Check Python 3
echo -e "${BLUE}Checking for Python 3...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python 3 is not installed${NC}"
    echo ""
    echo -e "${YELLOW}Python 3 is required to run ChitUI.${NC}"
    read -p "$(echo -e ${BLUE}Would you like to install Python 3 now? [Y/n]${NC} )" -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo -e "${YELLOW}Installing Python 3...${NC}"
        sudo apt update
        sudo apt install -y python3 python3-pip python3-dev
        echo -e "${GREEN}✓ Python 3 installed${NC}"
    else
        echo -e "${RED}Cannot continue without Python 3${NC}"
        exit 1
    fi
else
    PYTHON_VERSION=$(python3 --version)
    echo -e "${GREEN}✓ Found: $PYTHON_VERSION${NC}"
fi

# Check pip3
echo -e "${BLUE}Checking for pip3...${NC}"
if ! command -v pip3 &> /dev/null; then
    echo -e "${YELLOW}pip3 not found, installing...${NC}"
    sudo apt install -y python3-pip
fi
echo -e "${GREEN}✓ pip3 is available${NC}"

# Check git
# Used by the built-in updater (Settings -> General -> Software Updates).
# Without git, updates still work - ChitUI falls back to downloading the
# release tarball from GitHub - but a git install upgrades faster and keeps
# your local history.
echo -e "${BLUE}Checking for git...${NC}"
if ! command -v git &> /dev/null; then
    echo -e "${YELLOW}⚠ git is not installed${NC}"
    echo -e "${YELLOW}  Recommended: the built-in updater uses it for faster upgrades.${NC}"
    echo -e "${YELLOW}  Without it, updates fall back to downloading a release tarball.${NC}"
    read -p "$(echo -e ${BLUE}Install git? [Y/n]${NC} )" -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        sudo apt install -y git
        echo -e "${GREEN}✓ git installed${NC}"
    fi
else
    echo -e "${GREEN}✓ git is available${NC}"
fi

# Check CA certificates
# The updater talks to https://api.github.com. On minimal or long-unpatched
# images the CA bundle is missing or stale, which shows up as an opaque
# SSL certificate verify failed error rather than anything obviously
# certificate-related - so check for it up front.
echo -e "${BLUE}Checking CA certificates...${NC}"
if ! dpkg -s ca-certificates &> /dev/null; then
    echo -e "${YELLOW}⚠ ca-certificates is not installed${NC}"
    echo -e "${YELLOW}  HTTPS requests (including the update check) will fail without it.${NC}"
    read -p "$(echo -e ${BLUE}Install ca-certificates? [Y/n]${NC} )" -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        sudo apt install -y ca-certificates
        sudo update-ca-certificates || true
        echo -e "${GREEN}✓ ca-certificates installed${NC}"
    fi
else
    echo -e "${GREEN}✓ ca-certificates is available${NC}"
fi

echo ""
echo -e "${GREEN}✓ System requirements check complete!${NC}"
echo ""

# ============================================================================
# STEP 2: Python Dependencies
# ============================================================================

echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  Step 2: Python Dependencies                 ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${BLUE}ChitUI requires the following Python packages:${NC}"
echo -e "  • flask              - Web framework"
echo -e "  • flask-socketio     - WebSocket support"
echo -e "  • loguru             - Logging"
echo -e "  • websocket-client   - WebSocket client"
echo -e "  • requests           - HTTP library"
echo -e "  • werkzeug           - WSGI utilities"
echo -e "  • python-socketio    - Socket.IO support"
echo -e "  • opencv-python-headless - Camera support (headless)"
echo -e "  • psutil             - System statistics"
echo -e "  • pillow             - Image processing"
echo -e "  • pyserial           - UART printer support (Mars 2 etc.)"
echo -e "  • RPi.GPIO           - Raspberry Pi GPIO control"
echo -e "  • zeroconf           - mDNS/Bonjour for ESP32 auto-discovery (Leak Detector plugin)"
echo -e "  • beautifulsoup4     - HTML parsing (bs4)"
echo -e "  • watchdog           - Live file watching (falls back to polling if absent)"
echo ""
echo -e "${BLUE}The same list lives in ${YELLOW}requirements.txt${BLUE}, which the built-in${NC}"
echo -e "${BLUE}updater re-runs on every upgrade. Keep the two in sync.${NC}"
echo ""
echo -e "${BLUE}To install them by hand at any time:${NC}"
echo -e "  ${YELLOW}cd $SCRIPT_DIR && pip3 install -r requirements.txt --break-system-packages${NC}"
echo ""

# Check for dependencies
echo -e "${BLUE}Checking installed packages...${NC}"
MISSING_DEPS=()
INSTALLED_DEPS=()

check_package() {
    if python3 -c "import $1" 2>/dev/null; then
        INSTALLED_DEPS+=("$2")
        echo -e "${GREEN}✓${NC} $2"
        return 0
    else
        MISSING_DEPS+=("$2")
        echo -e "${RED}✗${NC} $2 (missing)"
        return 0  # Return 0 to prevent set -e from exiting
    fi
}

check_package "flask" "flask"
check_package "flask_socketio" "flask-socketio"
check_package "loguru" "loguru"
check_package "websocket" "websocket-client"
check_package "requests" "requests"
check_package "werkzeug" "werkzeug"
check_package "socketio" "python-socketio"
check_package "cv2" "opencv-python-headless"
check_package "psutil" "psutil"
check_package "PIL" "pillow"
check_package "serial" "pyserial"
check_package "RPi.GPIO" "RPi.GPIO"
check_package "zeroconf" "zeroconf"
check_package "bs4" "beautifulsoup4"
check_package "watchdog" "watchdog"

echo ""

if [ ${#MISSING_DEPS[@]} -eq 0 ]; then
    echo -e "${GREEN}✓ All Python dependencies are already installed!${NC}"
else
    echo -e "${YELLOW}Missing packages: ${MISSING_DEPS[*]}${NC}"
    echo ""
    read -p "$(echo -e ${BLUE}Install missing Python packages? [Y/n]${NC} )" -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo -e "${YELLOW}Installing Python packages...${NC}"
        echo ""

        # Install with progress
        for pkg in "${MISSING_DEPS[@]}"; do
            echo -e "${BLUE}Installing $pkg...${NC}"
            pip3 install "$pkg" --break-system-packages || {
                echo -e "${RED}Failed to install $pkg with --break-system-packages${NC}"
                echo -e "${YELLOW}Trying with --user flag...${NC}"
                pip3 install --user "$pkg"
            }
        done

        echo ""
        echo -e "${GREEN}✓ Python dependencies installed!${NC}"
    else
        echo -e "${RED}Warning: ChitUI may not work without all dependencies${NC}"
        echo -e "${YELLOW}You can install them later with:${NC}"
        echo -e "  ${CYAN}cd $SCRIPT_DIR && pip3 install -r requirements.txt --break-system-packages${NC}"
        read -p "$(echo -e ${YELLOW}Continue anyway? [y/N]${NC} )" -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
fi

echo ""

# ── Update-checker connectivity test ──────────────────────────────────────
# The built-in updater needs to reach api.github.com over HTTPS. Testing it
# here turns a later silent "no updates found" into an answer the user gets
# while they are still in front of the installer.
echo -e "${BLUE}Testing connectivity to GitHub (used by the update checker)...${NC}"
if python3 - <<'PYEOF' 2>/dev/null
import sys
try:
    import requests
    r = requests.get("https://api.github.com/rate_limit", timeout=10)
    sys.exit(0 if r.status_code == 200 else 1)
except Exception:
    sys.exit(1)
PYEOF
then
    echo -e "${GREEN}✓ api.github.com is reachable - update checking will work${NC}"
else
    echo -e "${YELLOW}⚠ Could not reach api.github.com${NC}"
    echo -e "${YELLOW}  ChitUI will still run normally; only update checking is affected.${NC}"
    echo -e "${YELLOW}  Common causes: no internet yet, a proxy, or stale CA certificates.${NC}"
    echo -e "${YELLOW}  You can disable the check in Settings -> General -> Software Updates.${NC}"
fi

echo ""

# ============================================================================
# STEP 3: Application Configuration
# ============================================================================

echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  Step 3: Application Configuration           ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

# Create config directory
if [ ! -d "$ACTUAL_HOME/.chitui" ]; then
    mkdir -p "$ACTUAL_HOME/.chitui"
    echo -e "${GREEN}✓ Created config directory: $ACTUAL_HOME/.chitui${NC}"
else
    echo -e "${GREEN}✓ Config directory exists: $ACTUAL_HOME/.chitui${NC}"
fi

# Setup passwordless sudo for USB gadget reload script
echo -e "${YELLOW}Setting up passwordless sudo for USB gadget reload...${NC}"
SUDOERS_FILE="/etc/sudoers.d/chitui-$ACTUAL_USER"
SUDOERS_LINE="$ACTUAL_USER ALL=(ALL) NOPASSWD: ALL"

# Write the sudoers entry
echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" > /dev/null
sudo chmod 0440 "$SUDOERS_FILE"

# Validate it
if sudo visudo -c -f "$SUDOERS_FILE" 2>/dev/null; then
    echo -e "${GREEN}✓ Passwordless sudo configured for $ACTUAL_USER: $SUDOERS_FILE${NC}"
else
    echo -e "${RED}✗ Sudoers entry invalid — removing to avoid lockout${NC}"
    sudo rm -f "$SUDOERS_FILE"
fi

# Make run.sh and helper scripts executable
chmod +x "$SCRIPT_DIR/run.sh" 2>/dev/null
chmod +x "$SCRIPT_DIR"/scripts/*.sh 2>/dev/null

# Narrow sudoers rule for Settings -> Network (static IP / DHCP / DNS changes)
# (works even if the blanket ChitUI sudo rule above is removed later —
#  same reasoning as the Tailscale rule further down)
if [ -f "$SCRIPT_DIR/scripts/setup_network_sudo.sh" ]; then
    echo -e "${YELLOW}Setting up passwordless sudo for Network settings...${NC}"
    if sudo bash "$SCRIPT_DIR/scripts/setup_network_sudo.sh" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Passwordless sudo configured for network settings: /etc/sudoers.d/chitui-network${NC}"
    else
        echo -e "${YELLOW}⚠ Could not pre-configure network settings sudo (harmless — the blanket rule above already covers it; you can also run scripts/setup_network_sudo.sh manually later)${NC}"
    fi
fi

# Fix data folder ownership — ensures settings survive if app was previously run as root
DATA_DIR="$SCRIPT_DIR/data"
if [ -d "$DATA_DIR" ]; then
    CURRENT_OWNER=$(stat -c '%U' "$DATA_DIR")
    if [ "$CURRENT_OWNER" != "$ACTUAL_USER" ]; then
        echo -e "${YELLOW}⚠ data/ folder owned by '$CURRENT_OWNER', fixing to '$ACTUAL_USER'...${NC}"
        sudo chown -R "$ACTUAL_USER":"$ACTUAL_USER" "$DATA_DIR"
        echo -e "${GREEN}✓ Fixed data folder ownership${NC}"
    else
        echo -e "${GREEN}✓ data/ folder ownership is correct ($ACTUAL_USER)${NC}"
    fi
else
    mkdir -p "$DATA_DIR"
    echo -e "${GREEN}✓ Created data folder: $DATA_DIR${NC}"
fi

# Check main.py
if [ ! -f "$SCRIPT_DIR/main.py" ]; then
    echo -e "${RED}✗ main.py not found in $SCRIPT_DIR${NC}"
    echo -e "${YELLOW}  Please ensure you're in the ChitUI directory${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Found main.py${NC}"

echo ""

# ============================================================================
# STEP 4: Virtual USB Gadget (OPTIONAL)
# ============================================================================

echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  Step 4: Virtual USB Gadget (OPTIONAL)       ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${BLUE}What is Virtual USB Gadget?${NC}"
echo -e "  The Virtual USB Gadget makes your Raspberry Pi appear as a USB"
echo -e "  flash drive when connected to your 3D printer via USB cable."
echo ""
echo -e "${YELLOW}Note: This is OPTIONAL!${NC}"
echo -e "  • ${GREEN}Install it${NC} if you want to connect your Pi directly via USB"
echo -e "  • ${GREEN}Skip it${NC} if you prefer using a physical USB drive"
echo -e "  • ${GREEN}Skip it${NC} if you're using network-only (no USB connection)"
echo ""
echo -e "${BLUE}Requirements for Virtual USB Gadget:${NC}"
echo -e "  • Raspberry Pi Zero, Zero W, Zero 2 W, or Pi 4/5"
echo -e "  • OTG-capable USB port"
echo -e "  • Free space on SD card for virtual drive image"
echo ""

read -p "$(echo -e ${BOLD}Do you want to install Virtual USB Gadget? [y/N]${NC} )" -n 1 -r
echo
echo ""

USB_GADGET_INSTALLED=false

if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -f "$SCRIPT_DIR/scripts/virtual_usb_gadget_fixed.sh" ]; then
        echo -e "${BLUE}Starting Virtual USB Gadget installer...${NC}"
        echo ""
        sudo bash "$SCRIPT_DIR/scripts/virtual_usb_gadget_fixed.sh"
        echo ""
        echo -e "${GREEN}✓ Virtual USB Gadget setup complete${NC}"
        USB_GADGET_INSTALLED=true
    else
        echo -e "${RED}✗ Virtual USB Gadget script not found${NC}"
        echo -e "${YELLOW}  Looking for: $SCRIPT_DIR/scripts/virtual_usb_gadget_fixed.sh${NC}"
    fi
else
    echo -e "${BLUE}⊘ Skipping Virtual USB Gadget installation${NC}"
    echo -e "${YELLOW}  You can install it later by running:${NC}"
    echo -e "${YELLOW}  sudo ./scripts/virtual_usb_gadget_fixed.sh${NC}"
fi

echo ""

# Configure passwordless sudo and permissions for USB gadget (if USB gadget was installed)
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}Configuring permissions for USB gadget...${NC}"
    echo ""

    # 1. Configure passwordless sudo for USB gadget operations
    SUDOERS_FILE="/etc/sudoers.d/chitui-usb-gadget"

    echo -e "${BLUE}  Setting up passwordless sudo...${NC}"
    # Create sudoers entry for commands needed by USB gadget management
    cat << 'SUDOEOF' | sudo tee "$SUDOERS_FILE" > /dev/null
# ChitUI USB Gadget - Allow modprobe without password
%sudo ALL=(ALL) NOPASSWD: /sbin/modprobe
# Allow writing to UDC control files for USB reconnect
%sudo ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/kernel/config/usb_gadget/*/UDC
# Allow sync command
%sudo ALL=(ALL) NOPASSWD: /bin/sync
SUDOEOF
    sudo chmod 0440 "$SUDOERS_FILE"

    if [ -f "$SUDOERS_FILE" ]; then
        echo -e "${GREEN}  ✓ Configured passwordless sudo${NC}"
    else
        echo -e "${YELLOW}  ⚠ Failed to configure passwordless sudo${NC}"
    fi

    # 2. Set permissions on USB gadget mount point (if it exists)
    if [ -d "/mnt/usb_share" ]; then
        echo -e "${BLUE}  Setting permissions on /mnt/usb_share...${NC}"
        sudo chmod 777 /mnt/usb_share
        echo -e "${GREEN}  ✓ USB gadget folder is writable${NC}"
    else
        echo -e "${YELLOW}  ⚠ /mnt/usb_share not found yet (will be created on reboot)${NC}"
        echo -e "${YELLOW}    After reboot, run: sudo chmod 777 /mnt/usb_share${NC}"
    fi

    # 3. Add user to necessary groups for USB/GPIO access
    echo -e "${BLUE}  Adding user to required groups...${NC}"
    sudo usermod -a -G gpio,dialout,plugdev "$ACTUAL_USER" 2>/dev/null || true
    echo -e "${GREEN}  ✓ User added to access groups${NC}"

    echo ""
    echo -e "${GREEN}✓ USB gadget permissions configured${NC}"
    echo -e "${YELLOW}  Note: You may need to log out and back in for group changes to take effect${NC}"
fi

echo ""

# ============================================================================
# STEP 5: UART / Serial Printer Support (OPTIONAL)
# ============================================================================

echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  Step 5: UART / Serial Printer Support       ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${BLUE}What is UART Printer Support?${NC}"
echo -e "  Allows ChitUI to control older resin printers that don't have"
echo -e "  network/WiFi capability, by connecting them directly to the"
echo -e "  Raspberry Pi's GPIO serial pins (TX/RX)."
echo ""
echo -e "  ${GREEN}Tested with:${NC} Elegoo Mars 2"
echo -e "  ${YELLOW}May work with:${NC} Other Elegoo/Anycubic printers with UART interface"
echo ""
echo -e "${YELLOW}Note: This is OPTIONAL!${NC}"
echo -e "  • ${GREEN}Install it${NC} if you have an older printer to connect via GPIO pins"
echo -e "  • ${GREEN}Skip it${NC}  if all your printers use WiFi / network"
echo ""
echo -e "${BLUE}Wiring (3.3 V logic ONLY — never connect 5 V):${NC}"
echo -e "  Pi GPIO14 TX  (pin  8) → Printer RX"
echo -e "  Pi GPIO15 RX  (pin 10) → Printer TX"
echo -e "  Pi GND        (pin  6) → Printer GND"
echo -e "  ${RED}DO NOT connect printer's power rail to the Pi!${NC}"
echo ""

read -p "$(echo -e ${BOLD}Do you want to configure UART serial support? [y/N]${NC} )" -n 1 -r
echo
echo ""

UART_INSTALLED=false

if [[ $REPLY =~ ^[Yy]$ ]]; then

    UART_INSTALLED=true
    REBOOT_NEEDED_UART=0

    # ── Detect boot config location ───────────────────────────────────────
    if   [ -f /boot/firmware/config.txt ]; then BOOT_CFG="/boot/firmware/config.txt"
    elif [ -f /boot/config.txt ];           then BOOT_CFG="/boot/config.txt"
    else
        echo -e "${RED}✗ Cannot find boot config.txt — skipping UART config${NC}"
        UART_INSTALLED=false
    fi

    if [ "$UART_INSTALLED" = true ]; then

        # ── Detect Pi model & Bluetooth ───────────────────────────────────
        PI_MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "unknown")
        echo -e "${BLUE}  Pi model: ${PI_MODEL}${NC}"

        HAS_BT=0
        echo "$PI_MODEL" | grep -qiE "Pi [3-9]|Pi Zero 2" && HAS_BT=1

        SERIAL0=$(readlink -f /dev/serial0 2>/dev/null || echo "not found")
        echo -e "${BLUE}  serial0 → ${SERIAL0}${NC}"
        echo ""

        # ── 1. Disable serial console (getty) ────────────────────────────
        echo -e "${BLUE}  [1/4] Disabling serial console (getty)...${NC}"

        CONSOLE_DISABLED=0
        for SVC in serial-getty@ttyS0.service serial-getty@ttyAMA0.service; do
            if systemctl is-active --quiet "$SVC" 2>/dev/null || \
               systemctl is-enabled --quiet "$SVC" 2>/dev/null; then
                sudo systemctl stop    "$SVC" 2>/dev/null || true
                sudo systemctl disable "$SVC" 2>/dev/null || true
                CONSOLE_DISABLED=1
            fi
        done

        # Remove console=serial0 from kernel cmdline
        for CMDLINE in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
            if [ -f "$CMDLINE" ] && grep -q "console=serial0\|console=ttyS0\|console=ttyAMA0" "$CMDLINE"; then
                sudo sed -i 's/console=serial0,[0-9]* //g' "$CMDLINE"
                sudo sed -i 's/console=ttyS0,[0-9]* //g'   "$CMDLINE"
                sudo sed -i 's/console=ttyAMA0,[0-9]* //g' "$CMDLINE"
                echo -e "${GREEN}  ✓ Removed serial console from kernel cmdline${NC}"
                REBOOT_NEEDED_UART=1
            fi
        done

        if [ "$CONSOLE_DISABLED" -eq 1 ]; then
            echo -e "${GREEN}  ✓ Serial console (getty) disabled${NC}"
            REBOOT_NEEDED_UART=1
        else
            echo -e "${GREEN}  ✓ Serial console already disabled${NC}"
        fi

        # ── 2. Bluetooth / UART conflict (Pi 3/4/5 only) ─────────────────
        echo -e "${BLUE}  [2/4] Configuring Bluetooth / UART mapping...${NC}"

        if [ "$HAS_BT" -eq 0 ]; then
            echo -e "${GREEN}  ✓ No Bluetooth on this Pi — no conflict${NC}"
            RECOMMENDED_PORT="/dev/ttyS0"
        else
            echo -e "${YELLOW}  Pi with Bluetooth detected.${NC}"
            echo -e "  On Pi 3/4/5, Bluetooth shares the hardware UART (ttyAMA0)."
            echo -e "  Recommended: move Bluetooth to mini-UART, freeing ttyAMA0 for the printer."
            echo ""
            echo -e "    ${YELLOW}A)${NC} disable-bt  — Disable Bluetooth entirely ${GREEN}(recommended)${NC}"
            echo -e "    ${YELLOW}B)${NC} miniuart-bt — Keep Bluetooth on mini-UART (slower BT, stable serial)"
            echo -e "    ${YELLOW}C)${NC} Skip        — Leave as-is (use ttyS0, less reliable)"
            echo ""
            read -p "$(echo -e "  ${BOLD}Choose [A/b/c]:${NC} ")" -n 1 -r
            echo ""
            BT_CHOICE="${REPLY,,}"

            if [[ "$BT_CHOICE" == "b" ]]; then
                BT_OVERLAY="miniuart-bt"
            elif [[ "$BT_CHOICE" == "c" ]]; then
                BT_OVERLAY="skip"
                RECOMMENDED_PORT="/dev/ttyS0"
            else
                BT_OVERLAY="disable-bt"
            fi

            if [[ "$BT_OVERLAY" != "skip" ]]; then
                # Remove any conflicting overlays first
                sudo sed -i '/dtoverlay=disable-bt/d'  "$BOOT_CFG"
                sudo sed -i '/dtoverlay=miniuart-bt/d' "$BOOT_CFG"
                echo "dtoverlay=${BT_OVERLAY}" | sudo tee -a "$BOOT_CFG" > /dev/null
                sudo systemctl disable hciuart 2>/dev/null || true
                echo -e "${GREEN}  ✓ dtoverlay=${BT_OVERLAY} added to ${BOOT_CFG}${NC}"
                RECOMMENDED_PORT="/dev/ttyAMA0"
                REBOOT_NEEDED_UART=1
            else
                echo -e "${YELLOW}  ⚠ Skipped BT fix — ttyS0 may be unreliable under load${NC}"
                RECOMMENDED_PORT="/dev/ttyS0"
            fi
        fi

        # ── 3. Enable hardware UART ───────────────────────────────────────
        echo -e "${BLUE}  [3/4] Enabling hardware UART in ${BOOT_CFG}...${NC}"

        if grep -q "^enable_uart=1" "$BOOT_CFG" 2>/dev/null; then
            echo -e "${GREEN}  ✓ enable_uart=1 already set${NC}"
        elif grep -q "^enable_uart=0" "$BOOT_CFG" 2>/dev/null; then
            sudo sed -i 's/^enable_uart=0/enable_uart=1/' "$BOOT_CFG"
            echo -e "${GREEN}  ✓ enable_uart changed to 1${NC}"
            REBOOT_NEEDED_UART=1
        else
            echo "enable_uart=1" | sudo tee -a "$BOOT_CFG" > /dev/null
            echo -e "${GREEN}  ✓ enable_uart=1 added to ${BOOT_CFG}${NC}"
            REBOOT_NEEDED_UART=1
        fi

        # ── 4. Groups + sudoers for UART ──────────────────────────────────
        echo -e "${BLUE}  [4/4] Setting up user permissions for serial port...${NC}"

        sudo usermod -aG dialout,gpio "$ACTUAL_USER" 2>/dev/null || true
        echo -e "${GREEN}  ✓ ${ACTUAL_USER} added to dialout + gpio groups${NC}"

        # Passwordless sudo for UART-related commands (used by setup_uart.sh)
        SUDOERS_UART="/etc/sudoers.d/chitui-uart"
        cat << SUEOF | sudo tee "$SUDOERS_UART" > /dev/null
# ChitUI UART — allow serial port and UART config without password
# (both /bin and /usr/bin listed for pre/post usr-merge systems)
$ACTUAL_USER ALL=(ALL) NOPASSWD: /bin/systemctl stop serial-getty@ttyS0.service, /usr/bin/systemctl stop serial-getty@ttyS0.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: /bin/systemctl disable serial-getty@ttyS0.service, /usr/bin/systemctl disable serial-getty@ttyS0.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: /bin/systemctl stop serial-getty@ttyAMA0.service, /usr/bin/systemctl stop serial-getty@ttyAMA0.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: /bin/systemctl disable serial-getty@ttyAMA0.service, /usr/bin/systemctl disable serial-getty@ttyAMA0.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: /bin/systemctl disable hciuart, /usr/bin/systemctl disable hciuart
$ACTUAL_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /boot/firmware/config.txt
$ACTUAL_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /boot/config.txt
$ACTUAL_USER ALL=(ALL) NOPASSWD: /bin/sed -i * /boot/firmware/config.txt, /usr/bin/sed -i * /boot/firmware/config.txt
$ACTUAL_USER ALL=(ALL) NOPASSWD: /bin/sed -i * /boot/config.txt, /usr/bin/sed -i * /boot/config.txt
$ACTUAL_USER ALL=(ALL) NOPASSWD: /bin/sed -i * /boot/firmware/cmdline.txt, /usr/bin/sed -i * /boot/firmware/cmdline.txt
$ACTUAL_USER ALL=(ALL) NOPASSWD: /bin/sed -i * /boot/cmdline.txt, /usr/bin/sed -i * /boot/cmdline.txt
SUEOF
        sudo chmod 0440 "$SUDOERS_UART"

        if sudo visudo -c -f "$SUDOERS_UART" 2>/dev/null; then
            echo -e "${GREEN}  ✓ Passwordless sudo configured for UART setup${NC}"
        else
            echo -e "${RED}  ✗ Sudoers entry invalid — removing${NC}"
            sudo rm -f "$SUDOERS_UART"
        fi

        # Make setup_uart.sh executable
        chmod +x "$SCRIPT_DIR/scripts/setup_uart.sh" 2>/dev/null || true

        echo ""
        echo -e "${GREEN}✓ UART serial support configured!${NC}"
        echo ""
        echo -e "${BLUE}  Recommended port for ChitUI settings: ${YELLOW}${RECOMMENDED_PORT}${NC}"
        echo -e "${BLUE}  Baud rate: ${YELLOW}115200${NC}  (standard for Elegoo Mars 2)"
        echo ""

        if [ "$REBOOT_NEEDED_UART" -eq 1 ]; then
            echo -e "${YELLOW}  ⚠ UART changes require a reboot to take effect.${NC}"
            echo -e "${YELLOW}    The installer will prompt to reboot at the end.${NC}"
            NEEDS_REBOOT=1
        fi

    fi  # UART_INSTALLED check

else
    echo -e "${BLUE}⊘ Skipping UART serial support${NC}"
    echo -e "${YELLOW}  You can configure it later by running:${NC}"
    echo -e "${YELLOW}  bash $SCRIPT_DIR/scripts/setup_uart.sh${NC}"
fi

echo ""

# ============================================================================
# STEP 6: Tailscale Remote Access (OPTIONAL)
# ============================================================================

echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  Step 6: Tailscale Remote Access (OPTIONAL)  ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${BLUE}What is Tailscale Remote Access?${NC}"
echo -e "  Tailscale creates a private encrypted network between your devices,"
echo -e "  so you can reach ChitUI (and the ChitUI Remote Android app) from"
echo -e "  ${GREEN}anywhere${NC} — without port forwarding and without exposing the Pi"
echo -e "  to the internet."
echo ""
echo -e "${YELLOW}Note: This is OPTIONAL!${NC}"
echo -e "  • ${GREEN}Install it${NC} if you want secure remote access from outside your LAN"
echo -e "  • ${GREEN}Skip it${NC}  if you only use ChitUI on your local network"
echo ""
echo -e "${BLUE}This step will:${NC}"
echo -e "  • Install Tailscale (official installer)"
echo -e "  • Grant ChitUI permission to manage ${YELLOW}only${NC} the tailscale command"
echo -e "  • Login/QR pairing is done later from the ChitUI web UI (Tailscale tab)"
echo ""

read -p "$(echo -e ${BOLD}Install Tailscale Remote Access? [y/N]${NC} )" -n 1 -r
echo
echo ""

TAILSCALE_INSTALLED=false

if [[ $REPLY =~ ^[Yy]$ ]]; then
    # 1. Install Tailscale if missing
    if command -v tailscale &> /dev/null; then
        echo -e "${GREEN}✓ Tailscale already installed ($(tailscale version | head -1))${NC}"
        TAILSCALE_INSTALLED=true
    else
        echo -e "${YELLOW}Installing Tailscale...${NC}"
        if curl -fsSL https://tailscale.com/install.sh | sudo sh; then
            echo -e "${GREEN}✓ Tailscale installed${NC}"
            TAILSCALE_INSTALLED=true
        else
            echo -e "${RED}✗ Tailscale installation failed (no internet?)${NC}"
            echo -e "${YELLOW}  You can retry later from the ChitUI web UI (Tailscale tab)${NC}"
        fi
    fi

    # 2. Narrow sudoers rule so the web UI can manage it
    #    (works even if the blanket ChitUI sudo rule is removed later)
    if [ "$TAILSCALE_INSTALLED" = true ]; then
        SUDOERS_TS="/etc/sudoers.d/chitui-tailscale"
        TS_PATHS=""
        for p in /usr/bin/tailscale /usr/sbin/tailscale /usr/local/bin/tailscale; do
            [ -x "$p" ] && TS_PATHS="${TS_PATHS:+$TS_PATHS, }$p"
        done

        if [ -n "$TS_PATHS" ]; then
            cat << SUDOEOF | sudo tee "$SUDOERS_TS" > /dev/null
# ChitUI Tailscale — allow the web UI to manage the tailscale command only
$ACTUAL_USER ALL=(ALL) NOPASSWD: $TS_PATHS
SUDOEOF
            sudo chmod 0440 "$SUDOERS_TS"

            if sudo visudo -c -f "$SUDOERS_TS" 2>/dev/null; then
                echo -e "${GREEN}✓ Passwordless sudo configured for tailscale: $SUDOERS_TS${NC}"
            else
                echo -e "${RED}✗ Sudoers entry invalid — removing${NC}"
                sudo rm -f "$SUDOERS_TS"
            fi
        fi

        echo ""
        echo -e "${GREEN}✓ Tailscale ready!${NC}"
        echo -e "${BLUE}  Next step (after installation finishes):${NC}"
        echo -e "  1. Open the ChitUI web UI → ${YELLOW}Tailscale tab${NC}"
        echo -e "  2. Click ${YELLOW}Start Login${NC} and scan the QR code with your phone"
        echo -e "  3. Scan the server QR code into the ${YELLOW}ChitUI Remote${NC} app"
    fi
else
    echo -e "${BLUE}⊘ Skipping Tailscale Remote Access${NC}"
    echo -e "${YELLOW}  You can install it anytime from the ChitUI web UI (Tailscale tab)${NC}"
fi

echo ""

# ============================================================================
# STEP 7: Systemd Service Installation (OPTIONAL)
# ============================================================================

echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  Step 7: Auto-Start Service (OPTIONAL)       ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${BLUE}What is the Auto-Start Service?${NC}"
echo -e "  Installs ChitUI as a system service that:"
echo -e "  • ${GREEN}Starts automatically${NC} when your Pi boots"
echo -e "  • ${GREEN}Restarts automatically${NC} if it crashes"
echo -e "  • ${GREEN}Runs in the background${NC} (no terminal needed)"
echo ""
echo -e "${YELLOW}Alternative: Run manually${NC}"
echo -e "  If you skip this, you can run ChitUI manually with:"
echo -e "  ${CYAN}./run.sh${NC}"
echo ""

read -p "$(echo -e ${BOLD}Install Auto-Start Service? [Y/n]${NC} )" -n 1 -r
echo
echo ""

if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    if [ -f "$SCRIPT_DIR/scripts/install_service.sh" ]; then
        echo -e "${BLUE}Starting service installer...${NC}"
        echo ""
        bash "$SCRIPT_DIR/scripts/install_service.sh"
        SERVICE_INSTALLED=true
    else
        echo -e "${RED}✗ Service installer script not found${NC}"
        echo -e "${YELLOW}  Looking for: $SCRIPT_DIR/scripts/install_service.sh${NC}"
        SERVICE_INSTALLED=false
    fi
else
    echo -e "${BLUE}⊘ Skipping service installation${NC}"
    echo -e "${YELLOW}  You can install it later by running:${NC}"
    echo -e "${YELLOW}  ./scripts/install_service.sh${NC}"
    SERVICE_INSTALLED=false
fi

echo ""

# ============================================================================
# Installation Complete
# ============================================================================

clear
echo -e "${GREEN}${BOLD}"
cat << "EOF"
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║          ✓  Installation Complete Successfully!             ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

echo -e "${CYAN}${BOLD}Installation Summary:${NC}"
echo ""

echo -e "${GREEN}✓ Python Dependencies:${NC} Installed"
echo -e "${GREEN}✓ Configuration:${NC} Ready"

if [ "$USB_GADGET_INSTALLED" = true ] && [ -f "/piusb.bin" ]; then
    echo -e "${GREEN}✓ Virtual USB Gadget:${NC} Configured"
else
    echo -e "${YELLOW}⊘ Virtual USB Gadget:${NC} Not installed (optional)"
fi

if [ "$UART_INSTALLED" = true ]; then
    echo -e "${GREEN}✓ UART Serial Support:${NC} Configured  (port: ${RECOMMENDED_PORT:-/dev/ttyAMA0})"
else
    echo -e "${YELLOW}⊘ UART Serial Support:${NC} Not installed (optional)"
fi

if [ "$TAILSCALE_INSTALLED" = true ]; then
    echo -e "${GREEN}✓ Tailscale Remote Access:${NC} Installed  (finish login in web UI → Tailscale tab)"
else
    echo -e "${YELLOW}⊘ Tailscale Remote Access:${NC} Not installed (optional)"
fi

if [ "$SERVICE_INSTALLED" = true ]; then
    echo -e "${GREEN}✓ Auto-Start Service:${NC} Enabled"
else
    echo -e "${YELLOW}⊘ Auto-Start Service:${NC} Not installed"
fi

echo ""
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}${BOLD}  How to Access ChitUI${NC}"
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""

if [ "$SERVICE_INSTALLED" = true ]; then
    echo -e "${BLUE}Service Management:${NC}"
    echo -e "  Check Status:  ${YELLOW}sudo systemctl status chitui${NC}"
    echo -e "  View Logs:     ${YELLOW}journalctl -u chitui -f${NC}"
    echo -e "  Restart:       ${YELLOW}sudo systemctl restart chitui${NC}"
    echo -e "  Stop:          ${YELLOW}sudo systemctl stop chitui${NC}"
    echo ""
else
    echo -e "${BLUE}To Start ChitUI:${NC}"
    echo -e "  ${YELLOW}cd $SCRIPT_DIR${NC}"
    echo -e "  ${YELLOW}./run.sh${NC}"
    echo ""
fi

echo -e "${BLUE}Access the Web Interface:${NC}"
echo -e "  Local:    ${YELLOW}http://localhost:8080${NC}"
echo -e "  Network:  ${YELLOW}http://$(hostname -I | awk '{print $1}'):8080${NC}"
echo ""

echo -e "${BLUE}Default Login:${NC}"
echo -e "  Username: ${YELLOW}admin${NC}"
echo -e "  Password: ${YELLOW}admin${NC}"
echo -e "  ${RED}(You will be prompted to change it on first login)${NC}"
echo ""

echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}${BOLD}  Additional Resources${NC}"
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "${BLUE}Documentation:${NC}"
echo -e "  Main README:        ${YELLOW}~/ChitUI/README.md${NC}"
echo -e "  Scripts Guide:      ${YELLOW}~/ChitUI/scripts/README.md${NC}"
echo -e "  Plugin READMEs:     ${YELLOW}~/ChitUI/plugins/*/README.md${NC}"
echo ""

echo -e "${BLUE}Useful Commands:${NC}"
echo -e "  Test manually:      ${YELLOW}cd ~/ChitUI && python3 main.py${NC}"
echo -e "  Check USB Gadget:   ${YELLOW}cd ~/ChitUI && bash scripts/check_usb_gadget.sh${NC}"
echo -e "  Reinstall deps:     ${YELLOW}cd ~/ChitUI && pip3 install -r requirements.txt --break-system-packages${NC}"
echo ""
echo -e "${BLUE}Updating ChitUI:${NC}"
echo -e "  ${GREEN}From the web interface:${NC} Settings → General → Software Updates → Check for Updates"
echo -e "  ${BLUE}ChitUI checks GitHub for new releases and can install them for you.${NC}"
echo -e "  ${BLUE}Your settings, uploads, thumbnails, themes and plugins are preserved,${NC}"
echo -e "  ${BLUE}and the previous version is backed up to ${YELLOW}data/backups/${BLUE}.${NC}"
echo -e "  ${BLUE}From a terminal instead:${NC}  ${YELLOW}cd ~/ChitUI && git pull${NC}"
echo ""

if [ "$SERVICE_INSTALLED" = true ]; then
    echo -e "${GREEN}${BOLD}ChitUI is now running and will start automatically on boot!${NC}"
else
    echo -e "${YELLOW}${BOLD}To start ChitUI, run: ./run.sh${NC}"
fi

echo ""
echo -e "${CYAN}Thank you for installing ChitUI!${NC}"
echo -e "${CYAN}Happy printing! 🖨️${NC}"
echo ""

# ── Reboot prompt (only if UART changes require it) ───────────────────────
if [ "${NEEDS_REBOOT:-0}" -eq 1 ]; then
    echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}${BOLD}  ⚠  A reboot is required${NC}"
    echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  UART configuration changes to ${BOOT_CFG} and system"
    echo -e "  services will not take effect until the Pi reboots."
    echo ""
    read -p "$(echo -e ${BOLD}Reboot now? [Y/n]${NC} )" -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo ""
        echo -e "${YELLOW}Rebooting in 3 seconds...${NC}"
        sleep 3
        sudo reboot
    else
        echo ""
        echo -e "${YELLOW}Remember to reboot before using your UART printer:${NC}"
        echo -e "  ${CYAN}sudo reboot${NC}"
        echo ""
    fi
fi
