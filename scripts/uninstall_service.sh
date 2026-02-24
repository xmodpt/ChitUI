#!/bin/bash
# ChitUI Plus Service Uninstaller
# This script removes the ChitUI Plus systemd service

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     ChitUI Plus Service Uninstaller          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

SERVICE_NAME="chitui"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Check if service exists
if [ ! -f "$SERVICE_FILE" ]; then
    echo -e "${YELLOW}⚠ Service is not installed${NC}"
    exit 0
fi

echo -e "${YELLOW}This will remove the ChitUI Plus service from your system.${NC}"
echo -e "${YELLOW}The application files will NOT be deleted.${NC}"
echo ""
read -p "$(echo -e ${RED}Are you sure? [y/N]${NC} )" -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}Cancelled${NC}"
    exit 0
fi

echo ""
echo -e "${BLUE}Removing service...${NC}"

# Stop the service if running
if sudo systemctl is-active --quiet "$SERVICE_NAME.service"; then
    echo -e "${YELLOW}Stopping service...${NC}"
    sudo systemctl stop "$SERVICE_NAME.service"
    echo -e "${GREEN}✓${NC} Service stopped"
fi

# Disable the service
if sudo systemctl is-enabled --quiet "$SERVICE_NAME.service" 2>/dev/null; then
    echo -e "${YELLOW}Disabling service...${NC}"
    sudo systemctl disable "$SERVICE_NAME.service"
    echo -e "${GREEN}✓${NC} Service disabled"
fi

# Remove service file
echo -e "${YELLOW}Removing service file...${NC}"
sudo rm -f "$SERVICE_FILE"
echo -e "${GREEN}✓${NC} Service file removed"

# Reload systemd daemon
sudo systemctl daemon-reload
sudo systemctl reset-failed
echo -e "${GREEN}✓${NC} Systemd daemon reloaded"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        Service Uninstalled Successfully!    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Note:${NC} Application files are still in: $(dirname "$0")"
echo -e "${BLUE}Note:${NC} To run manually, use: ${YELLOW}./run.sh${NC}"
echo ""
