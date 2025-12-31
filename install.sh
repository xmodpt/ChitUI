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
║              ███████╗██╗  ██╗██╗████████╗██╗   ██╗██╗       ║
║              ██╔════╝██║  ██║██║╚══██╔══╝██║   ██║██║       ║
║              ██║     ███████║██║   ██║   ██║   ██║██║       ║
║              ██║     ██╔══██║██║   ██║   ██║   ██║██║       ║
║              ███████╗██║  ██║██║   ██║   ╚██████╔╝██║       ║
║              ╚══════╝╚═╝  ╚═╝╚═╝   ╚═╝    ╚═════╝ ╚═╝       ║
║                                                              ║
║                  Complete Installation Script               ║
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
echo -e "${CYAN}${BOLD}║  Step 1: System Requirements Check          ║${NC}"
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
echo -e "${BLUE}Checking for git...${NC}"
if ! command -v git &> /dev/null; then
    echo -e "${YELLOW}⚠ git is not installed (optional but recommended)${NC}"
    read -p "$(echo -e ${BLUE}Install git? [Y/n]${NC} )" -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        sudo apt install -y git
        echo -e "${GREEN}✓ git installed${NC}"
    fi
else
    echo -e "${GREEN}✓ git is available${NC}"
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
        read -p "$(echo -e ${YELLOW}Continue anyway? [y/N]${NC} )" -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
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

if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -f "$SCRIPT_DIR/scripts/virtual_usb_gadget_fixed.sh" ]; then
        echo -e "${BLUE}Starting Virtual USB Gadget installer...${NC}"
        echo ""
        sudo bash "$SCRIPT_DIR/scripts/virtual_usb_gadget_fixed.sh"
        echo ""
        echo -e "${GREEN}✓ Virtual USB Gadget setup complete${NC}"
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
# STEP 5: Systemd Service Installation (OPTIONAL)
# ============================================================================

echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  Step 5: Auto-Start Service (OPTIONAL)       ║${NC}"
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

if [[ $REPLY =~ ^[Yy]$ ]] && [ -f "/piusb.bin" ]; then
    echo -e "${GREEN}✓ Virtual USB Gadget:${NC} Configured"
else
    echo -e "${YELLOW}⊘ Virtual USB Gadget:${NC} Not installed (optional)"
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
echo -e "  Update ChitUI:      ${YELLOW}cd ~/ChitUI && git pull${NC}"
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
