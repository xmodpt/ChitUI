#!/bin/bash
# ChitUI Service Installer
# This script installs ChitUI as a systemd service that starts automatically on boot

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     ChitUI Service Installer               ║${NC}"
echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}✗ Please do NOT run this script as root or with sudo${NC}"
    echo -e "${YELLOW}  The script will prompt for sudo password when needed${NC}"
    exit 1
fi

# Get the actual user (not root if using sudo)
ACTUAL_USER="${SUDO_USER:-$USER}"
ACTUAL_HOME=$(eval echo ~$ACTUAL_USER)
# Get project root (parent directory of scripts/)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"

echo -e "${BLUE}Configuration:${NC}"
echo -e "  User:           ${GREEN}$ACTUAL_USER${NC}"
echo -e "  Home:           ${GREEN}$ACTUAL_HOME${NC}"
echo -e "  Install Path:   ${GREEN}$SCRIPT_DIR${NC}"
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python 3 is not installed${NC}"
    echo -e "${YELLOW}  Please install Python 3 first:${NC}"
    echo -e "  sudo apt update && sudo apt install python3 python3-pip"
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo -e "${GREEN}✓${NC} Found: $PYTHON_VERSION"

# Check if main.py exists
if [ ! -f "$SCRIPT_DIR/main.py" ]; then
    echo -e "${RED}✗ main.py not found in $SCRIPT_DIR${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Found main.py"

# Check for required Python packages
echo ""
echo -e "${BLUE}Checking Python dependencies...${NC}"
MISSING_DEPS=()

python3 -c "import flask" 2>/dev/null || MISSING_DEPS+=("flask")
python3 -c "import flask_socketio" 2>/dev/null || MISSING_DEPS+=("flask-socketio")
python3 -c "import loguru" 2>/dev/null || MISSING_DEPS+=("loguru")
python3 -c "import websocket" 2>/dev/null || MISSING_DEPS+=("websocket-client")
python3 -c "import requests" 2>/dev/null || MISSING_DEPS+=("requests")

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo -e "${YELLOW}⚠ Missing dependencies: ${MISSING_DEPS[*]}${NC}"
    echo ""
    echo -e "${BLUE}Install them with:${NC}"
    echo -e "  pip3 install ${MISSING_DEPS[*]}"
    echo ""
    read -p "Do you want to install them now? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        pip3 install ${MISSING_DEPS[*]}
        echo -e "${GREEN}✓${NC} Dependencies installed"
    else
        echo -e "${RED}✗ Cannot continue without dependencies${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✓${NC} All dependencies installed"
fi

# Create the service file
SERVICE_NAME="chitui"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo ""
echo -e "${BLUE}Creating systemd service file...${NC}"

# Create temporary service file
TEMP_SERVICE=$(mktemp)
cat > "$TEMP_SERVICE" << EOF
[Unit]
Description=ChitUI - 3D Printer Management Interface
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$ACTUAL_USER
Group=$ACTUAL_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 main.py
Restart=on-failure
RestartSec=5
StandardOutput=append:$ACTUAL_HOME/.chitui/service.log
StandardError=append:$ACTUAL_HOME/.chitui/service.log

# Security settings
NoNewPrivileges=true
PrivateTmp=true

# Environment
Environment="PATH=$ACTUAL_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
EOF

# Install the service file (requires sudo)
echo -e "${YELLOW}Installing service file (requires sudo)...${NC}"
sudo cp "$TEMP_SERVICE" "$SERVICE_FILE"
sudo chmod 644 "$SERVICE_FILE"
rm "$TEMP_SERVICE"

echo -e "${GREEN}✓${NC} Service file created at $SERVICE_FILE"

# Create log directory
mkdir -p "$ACTUAL_HOME/.chitui"
echo -e "${GREEN}✓${NC} Log directory created at $ACTUAL_HOME/.chitui"

# Reload systemd daemon
echo ""
echo -e "${BLUE}Configuring systemd...${NC}"
sudo systemctl daemon-reload
echo -e "${GREEN}✓${NC} Systemd daemon reloaded"

# Enable the service
sudo systemctl enable "$SERVICE_NAME.service"
echo -e "${GREEN}✓${NC} Service enabled (will start on boot)"

# Ask if user wants to start the service now
echo ""
echo -ne "${BLUE}Start the service now? [Y/n]${NC} "
read -n 1 -r
echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    # Stop any existing instance
    if pgrep -f "python3.*main.py" > /dev/null; then
        echo -e "${YELLOW}⚠ Stopping existing ChitUI instances...${NC}"
        pkill -f "python3.*main.py" || true
        sleep 2
    fi

    sudo systemctl start "$SERVICE_NAME.service"
    sleep 2

    if sudo systemctl is-active --quiet "$SERVICE_NAME.service"; then
        echo -e "${GREEN}✓${NC} Service started successfully!"
    else
        echo -e "${RED}✗ Service failed to start${NC}"
        echo -e "${YELLOW}  Check logs with: journalctl -u $SERVICE_NAME -n 50${NC}"
    fi
fi

# Print status and instructions
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          Installation Complete!             ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Service Management Commands:${NC}"
echo -e "  Status:   ${YELLOW}sudo systemctl status $SERVICE_NAME${NC}"
echo -e "  Start:    ${YELLOW}sudo systemctl start $SERVICE_NAME${NC}"
echo -e "  Stop:     ${YELLOW}sudo systemctl stop $SERVICE_NAME${NC}"
echo -e "  Restart:  ${YELLOW}sudo systemctl restart $SERVICE_NAME${NC}"
echo -e "  Logs:     ${YELLOW}journalctl -u $SERVICE_NAME -f${NC}"
echo -e "  Disable:  ${YELLOW}sudo systemctl disable $SERVICE_NAME${NC}"
echo ""
echo -e "${BLUE}Log Files:${NC}"
echo -e "  Service:  ${YELLOW}$ACTUAL_HOME/.chitui/service.log${NC}"
echo -e "  App:      ${YELLOW}$SCRIPT_DIR/chitui.log${NC}"
echo ""
echo -e "${BLUE}Access ChitUI:${NC}"
echo -e "  Local:    ${YELLOW}http://localhost:8080${NC}"
echo -e "  Network:  ${YELLOW}http://$(hostname -I | awk '{print $1}'):8080${NC}"
echo ""
echo -e "${GREEN}ChitUI will now start automatically on boot!${NC}"
echo ""
