#!/bin/bash
cat << "EOF"
 _____                 _                               
|  __ \               | |                              
| |__) |__ _ ___ _ __ | |__   ___ _ __ _ __ _   _     
|  _  // _` / __| '_ \| '_ \ / _ \ '__| '__| | | |    
| | \ \ (_| \__ \ |_) | |_) |  __/ |  | |  | |_| |    
|_|  \_\__,_|___/ .__/|_.__/ \___|_|  |_|   \__, |    
                | |                          __/ |     
                |_|                         |___/      
              
       Virtual USB Gadget Drive Installer              
           (Fixed Boot Persistence)

# Raspberry Pi USB Gadget Configuration Script
# Creates a persistent FAT32 USB drive that appears when Pi is connected via USB
# Compatible with Windows, Mac, and Linux

EOF

# Exit on error
set -e

# Must run as root
if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root. Try using sudo."
  exit 1
fi

echo "====================================================="
echo "Raspberry Pi Virtual USB Gadget Drive Setup"
echo "====================================================="
echo "This script will configure your Raspberry Pi Zero W to"
echo "appear as a USB flash drive when connected via USB."
echo ""
echo "Features:"
echo "- FAT32 formatted for Windows compatibility"
echo "- Persistent across reboots"
echo "- Automatically mounts and loads on boot"
echo "- Works with Windows, Mac, and Linux"
echo ""

# Check for existing setup
USB_FILE_PATH="/piusb.bin"
MOUNT_POINT="/mnt/usb_share"

if [ -f "$USB_FILE_PATH" ]; then
  EXISTING_SIZE=$(du -m "$USB_FILE_PATH" | cut -f1)
  echo "DETECTED: Existing USB drive image ($EXISTING_SIZE MB)"
  echo ""
  read -p "Do you want to keep the existing setup? (y/n): " KEEP_EXISTING
  
  if [[ "$KEEP_EXISTING" == "y" || "$KEEP_EXISTING" == "Y" ]]; then
    echo "Keeping existing USB drive image."
    CONFIG_ONLY=true
  else
    echo "Will create a new USB drive image."
    CONFIG_ONLY=false
  fi
else
  CONFIG_ONLY=false
fi

# Create or reformat USB image
if [ "$CONFIG_ONLY" = false ]; then
  echo ""
  echo "Please select the size for your USB drive:"
  echo "1. 2GB"
  echo "2. 4GB"
  echo "3. 8GB"
  echo "4. 16GB"
  echo "5. 32GB"
  echo "6. Custom size (specify in MB)"
  
  read -p "Enter selection (1-6): " SIZE_CHOICE
  
  case $SIZE_CHOICE in
    1) SIZE_MB=2048 ;;
    2) SIZE_MB=4096 ;;
    3) SIZE_MB=8192 ;;
    4) SIZE_MB=16384 ;;
    5) SIZE_MB=32768 ;;
    6) read -p "Enter custom size in MB: " SIZE_MB ;;
    *) echo "Invalid choice. Using 4GB as default."; SIZE_MB=4096 ;;
  esac
  
  echo ""
  echo "Selected size: $SIZE_MB MB ($(echo "scale=2; $SIZE_MB/1024" | bc) GB)"
  
  # Confirm before proceeding
  echo ""
  echo "WARNING: This will create a $SIZE_MB MB file on your Pi."
  echo "Make sure you have enough free space on your SD card."
  
  # Check available space
  AVAILABLE_SPACE=$(df -m / | awk 'NR==2 {print $4}')
  echo "Available space: $AVAILABLE_SPACE MB"
  
  if [ "$AVAILABLE_SPACE" -lt "$SIZE_MB" ]; then
    echo ""
    echo "ERROR: Not enough space available on SD card."
    echo "Required: $SIZE_MB MB, Available: $AVAILABLE_SPACE MB"
    echo "Please free up space or choose a smaller size."
    exit 1
  fi
  
  echo ""
  read -p "Continue? (y/n): " CONFIRM
  
  if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Operation cancelled."
    exit 0
  fi
  
  # Create the USB image file
  echo ""
  echo "====================================================="
  echo "Creating USB image file ($SIZE_MB MB)..."
  echo "This may take several minutes depending on the size."
  echo "====================================================="
  
  # Remove existing file if present
  if [ -f "$USB_FILE_PATH" ]; then
    echo "Removing existing USB image..."
    umount "$MOUNT_POINT" 2>/dev/null || true
    rm -f "$USB_FILE_PATH"
  fi
  
  # Create new file with dd (showing progress)
  echo "Creating image file..."
  dd if=/dev/zero of="$USB_FILE_PATH" bs=1M count="$SIZE_MB" status=progress
  
  # Format as FAT32 with proper parameters for Windows compatibility
  echo ""
  echo "Formatting as FAT32 (Windows compatible)..."
  mkfs.vfat -F 32 -n "PI_USB" "$USB_FILE_PATH"
  
  echo ""
  echo "✓ USB image file created and formatted successfully!"
  
else
  # If keeping existing, verify it's FAT32
  echo ""
  echo "Verifying existing USB image format..."
  FILE_TYPE=$(file -b "$USB_FILE_PATH" 2>/dev/null || echo "unknown")
  
  if [[ ! "$FILE_TYPE" =~ "FAT" ]]; then
    echo "WARNING: Existing file may not be FAT32 format."
    read -p "Would you like to reformat it as FAT32? (This will ERASE all data) (y/n): " REFORMAT
    
    if [[ "$REFORMAT" == "y" || "$REFORMAT" == "Y" ]]; then
      echo "Reformatting as FAT32..."
      umount "$MOUNT_POINT" 2>/dev/null || true
      mkfs.vfat -F 32 -n "PI_USB" "$USB_FILE_PATH"
      echo "✓ Reformatted successfully."
    else
      echo "Keeping existing format. Note: Windows compatibility may be limited."
    fi
  else
    echo "✓ Existing USB image is FAT32 formatted."
  fi
fi

# Configure USB gadget mode
echo ""
echo "====================================================="
echo "Configuring USB Gadget Mode"
echo "====================================================="

# Set up dtoverlay in config.txt
CONFIG_PATH="/boot/firmware/config.txt"
if [ ! -f "$CONFIG_PATH" ]; then
  CONFIG_PATH="/boot/config.txt"
fi

if [ ! -f "$CONFIG_PATH" ]; then
  echo "ERROR: Cannot find boot config file!"
  exit 1
fi

# Create backup
if [ ! -f "${CONFIG_PATH}.backup" ]; then
  cp "$CONFIG_PATH" "${CONFIG_PATH}.backup"
  echo "✓ Created backup of config.txt"
fi

# Remove any existing dwc2 entries to avoid duplicates
sed -i '/dtoverlay=dwc2/d' "$CONFIG_PATH"

# Add dtoverlay=dwc2 under [all] section
if grep -q "^\[all\]" "$CONFIG_PATH"; then
  sed -i '/^\[all\]/a dtoverlay=dwc2' "$CONFIG_PATH"
  echo "✓ Added dwc2 overlay after existing [all] section"
else
  echo -e "\n[all]\ndtoverlay=dwc2" >> "$CONFIG_PATH"
  echo "✓ Added [all] section with dwc2 overlay"
fi

# Set up modules-load in cmdline.txt
CMDLINE_PATH="/boot/firmware/cmdline.txt"
if [ ! -f "$CMDLINE_PATH" ]; then
  CMDLINE_PATH="/boot/cmdline.txt"
fi

if [ ! -f "$CMDLINE_PATH" ]; then
  echo "ERROR: Cannot find cmdline.txt file!"
  exit 1
fi

# Create backup
if [ ! -f "${CMDLINE_PATH}.backup" ]; then
  cp "$CMDLINE_PATH" "${CMDLINE_PATH}.backup"
  echo "✓ Created backup of cmdline.txt"
fi

# Remove old modules-load entries to avoid duplicates
sed -i 's/modules-load=dwc2,g_mass_storage//g' "$CMDLINE_PATH"
sed -i 's/modules-load=dwc2//g' "$CMDLINE_PATH"

# Add modules-load
sed -i 's/rootwait/rootwait modules-load=dwc2/' "$CMDLINE_PATH"
echo "✓ Added USB gadget modules to cmdline.txt"

# Create mount point
echo ""
echo "Setting up mount point..."
mkdir -p "$MOUNT_POINT"
echo "✓ Created mount point at $MOUNT_POINT"

# Configure fstab for automatic mounting
echo ""
echo "Configuring automatic mounting..."

# Remove any existing entries for this path
sed -i "\|$USB_FILE_PATH|d" /etc/fstab

# Add the updated entry to fstab with Windows-compatible options
echo "$USB_FILE_PATH $MOUNT_POINT vfat users,umask=000,uid=1000,gid=1000 0 0" >> /etc/fstab
echo "✓ Added to /etc/fstab for automatic mounting"

# Remove g_mass_storage from /etc/modules (we'll use rc.local instead for better control)
echo ""
echo "Configuring module loading..."
sed -i '/g_mass_storage/d' /etc/modules
sed -i '/dwc2/d' /etc/modules
echo "✓ Cleaned /etc/modules"

# Create the USB gadget initialization script
echo ""
echo "Creating USB gadget initialization script..."

cat > /usr/local/bin/init-usb-gadget.sh << 'INITEOF'
#!/bin/bash
# USB Gadget Initialization Script
# This runs at boot to set up the USB gadget properly

# Wait for system to be ready
sleep 5

# Ensure the mount point exists
mkdir -p /mnt/usb_share

# Mount the USB image if not already mounted
if ! mount | grep -q "/mnt/usb_share"; then
    mount /mnt/usb_share 2>/dev/null || mount -t vfat -o loop,rw,umask=000,uid=1000,gid=1000 /piusb.bin /mnt/usb_share
fi

# Wait a moment for mount to complete
sleep 2

# Unload any existing gadget modules
modprobe -r g_mass_storage 2>/dev/null || true
modprobe -r dwc2 2>/dev/null || true

# Small delay
sleep 1

# Load dwc2 module first
modprobe dwc2

# Small delay
sleep 1

# Load g_mass_storage with all parameters
modprobe g_mass_storage file=/piusb.bin stall=0 ro=0 removable=1 idVendor=0x0951 idProduct=0x1666 iManufacturer=Kingston iProduct=DataTraveler iSerialNumber=74A53CDF

# Log success
logger "USB Gadget initialized successfully"
echo "USB Gadget initialized at $(date)" >> /var/log/usb-gadget.log
INITEOF

chmod +x /usr/local/bin/init-usb-gadget.sh
echo "✓ Created /usr/local/bin/init-usb-gadget.sh"

# Create systemd service for USB gadget initialization (better than rc.local)
echo ""
echo "Creating USB gadget systemd service..."

cat > /etc/systemd/system/usb-gadget.service << 'SERVICEEOF'
[Unit]
Description=Initialize USB Gadget Mode
After=local-fs.target systemd-modules-load.service
Before=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/init-usb-gadget.sh
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICEEOF

chmod 644 /etc/systemd/system/usb-gadget.service
systemctl daemon-reload
systemctl enable usb-gadget.service
echo "✓ Created and enabled usb-gadget.service"

# Try to mount now
echo ""
echo "Mounting USB drive..."
if mount | grep -q "$MOUNT_POINT"; then
  echo "✓ Already mounted"
else
  if mount "$MOUNT_POINT" 2>/dev/null; then
    echo "✓ Successfully mounted"
  else
    echo "! Mount will occur on next boot"
  fi
fi

# Add README file to the drive
if mount | grep -q "$MOUNT_POINT"; then
  echo ""
  echo "Creating README file on USB drive..."
  cat > "$MOUNT_POINT/README.txt" << READMEEOF
=====================================================
Raspberry Pi Virtual USB Drive
=====================================================

Created: $(date)
Format: FAT32
Device: $(hostname)

This is a Raspberry Pi configured as a USB flash drive.

USAGE:
------
1. Connect your Raspberry Pi to a computer via the USB DATA port
   (NOT the power port)
2. The drive will appear as "PI_USB" on your computer
3. You can read and write files just like a regular USB drive
4. Files are stored on the Pi's SD card at: $MOUNT_POINT

SPECIFICATIONS:
--------------
- Format: FAT32 (Windows, Mac, Linux compatible)
- Persistent across reboots
- Automatically loads on boot
- Maximum compatibility mode

TROUBLESHOOTING:
---------------
If the drive doesn't appear after connecting:
1. Make sure you're using the USB DATA port (not PWR)
2. Try a different USB cable
3. Wait 10-20 seconds for the computer to recognize the device
4. On the Pi, check: sudo systemctl status usb-gadget.service
5. On the Pi, check logs: sudo journalctl -u usb-gadget.service
6. On the Pi, manually reload: sudo /usr/local/bin/reload-usb-gadget.sh

For more information, see: /home/pi/README_USB_DRIVE.txt

=====================================================
READMEEOF
  chmod 666 "$MOUNT_POINT/README.txt"
  echo "✓ Created README.txt on USB drive"
fi

# Create manual reload script (improved version)
echo ""
echo "Creating manual USB gadget reload script..."

cat > /usr/local/bin/reload-usb-gadget.sh << 'RELOADEOF'
#!/bin/bash
# Manual USB Gadget Reload Script
# Use this if the USB drive doesn't appear on the computer

echo "============================================"
echo "Reloading USB Gadget Modules"
echo "============================================"
echo ""

# Check if mounted
if ! mount | grep -q "/mnt/usb_share"; then
    echo "Mounting /mnt/usb_share..."
    mount /mnt/usb_share 2>/dev/null || mount -t vfat -o loop,rw,umask=000,uid=1000,gid=1000 /piusb.bin /mnt/usb_share
    sleep 2
fi

echo "Unloading existing modules..."
modprobe -r g_mass_storage 2>/dev/null || true
modprobe -r dwc2 2>/dev/null || true

echo "Waiting..."
sleep 2

echo "Loading dwc2 module..."
modprobe dwc2

echo "Waiting..."
sleep 1

echo "Loading g_mass_storage module..."
modprobe g_mass_storage file=/piusb.bin stall=0 ro=0 removable=1 idVendor=0x0951 idProduct=0x1666 iManufacturer=Kingston iProduct=DataTraveler iSerialNumber=74A53CDF

echo ""
echo "============================================"
echo "✓ USB gadget modules reloaded!"
echo "============================================"
echo ""
echo "The Pi should now appear as a USB drive on your computer."
echo "Drive name: PI_USB"
echo ""
echo "If it still doesn't appear:"
echo "1. Check USB cable and port (use DATA port, not PWR)"
echo "2. Wait 20-30 seconds"
echo "3. Check service: sudo systemctl status usb-gadget.service"
echo "4. Check logs: sudo journalctl -u usb-gadget.service"
echo "5. Check mount: mount | grep usb_share"
RELOADEOF

chmod +x /usr/local/bin/reload-usb-gadget.sh
echo "✓ Created /usr/local/bin/reload-usb-gadget.sh"

# Create symlink in /home/pi for convenience
ln -sf /usr/local/bin/reload-usb-gadget.sh /home/pi/reload_usb_gadget.sh 2>/dev/null || true
chown -h pi:pi /home/pi/reload_usb_gadget.sh 2>/dev/null || true

# Create comprehensive README for the user
cat > /home/pi/README_USB_DRIVE.txt << 'FINALREADME'
=====================================================
Raspberry Pi Virtual USB Gadget Drive
=====================================================

Your Raspberry Pi has been configured as a USB flash drive!

CONFIGURATION:
--------------
USB Image File: /piusb.bin
Format: FAT32 (Windows, Mac, Linux compatible)
Mount Point: /mnt/usb_share
Volume Label: PI_USB
Persistent: Yes (survives reboots)

HOW TO USE:
-----------
1. Connect your Raspberry Pi to a computer using the USB DATA port
   (This is usually labeled "USB" not "PWR" or "POWER")

2. Wait 10-20 seconds for your computer to recognize the device

3. The drive will appear as "PI_USB" on your computer

4. You can now read and write files to this drive

5. Files are actually stored on the Pi's SD card at /mnt/usb_share

IMPORTANT NOTES:
----------------
- Always use the USB DATA port, NOT the power port
- The Pi must be powered on for the drive to work
- Changes made from the computer will be visible at /mnt/usb_share on the Pi
- Changes made at /mnt/usb_share on the Pi will be visible on the computer
- This setup persists across reboots and loads automatically

AUTOMATIC LOADING:
------------------
The USB gadget now loads automatically on boot via systemd service.
You should not need to manually run any scripts after reboot.

Service name: usb-gadget.service
Init script: /usr/local/bin/init-usb-gadget.sh

TROUBLESHOOTING:
----------------

Drive doesn't appear on computer after reboot:
→ Run: sudo /usr/local/bin/reload-usb-gadget.sh
   (or: sudo ~/reload_usb_gadget.sh)

Check if the drive is mounted on the Pi:
→ Run: mount | grep /mnt/usb_share

Check USB gadget service status:
→ Run: sudo systemctl status usb-gadget.service

Check USB gadget service logs:
→ Run: sudo journalctl -u usb-gadget.service

Check if modules are loaded:
→ Run: lsmod | grep g_mass_storage
→ Run: lsmod | grep dwc2

Manually load modules:
→ Run: sudo modprobe dwc2
→ Run: sudo modprobe g_mass_storage file=/piusb.bin stall=0 ro=0 removable=1 idVendor=0x0951 idProduct=0x1666

Restart the service:
→ Run: sudo systemctl restart usb-gadget.service

View system boot log:
→ Run: sudo journalctl -b | grep -i usb

Check module loading at boot:
→ Run: dmesg | grep -i gadget
→ Run: dmesg | grep -i dwc2

ACCESSING FILES ON THE PI:
--------------------------
You can access the USB drive contents directly on the Pi:
→ cd /mnt/usb_share
→ ls -la

To copy files from Pi to USB drive:
→ sudo cp /path/to/file /mnt/usb_share/

To copy files from USB drive to Pi:
→ sudo cp /mnt/usb_share/filename /path/to/destination/

FILE PERMISSIONS:
-----------------
All files are readable and writable by everyone (umask=000)
This ensures maximum compatibility with Windows and other systems.

TECHNICAL DETAILS:
------------------
- Uses g_mass_storage kernel module with Kingston DataTraveler ID
- USB gadget mode enabled via dwc2 overlay
- Automatic mounting via /etc/fstab
- Automatic module loading via systemd service
- FAT32 filesystem for universal compatibility
- Initialization script with proper delays for reliable loading

CONFIGURATION FILES:
--------------------
Boot config: /boot/firmware/config.txt (or /boot/config.txt)
→ Contains: dtoverlay=dwc2

Boot cmdline: /boot/firmware/cmdline.txt (or /boot/cmdline.txt)
→ Contains: modules-load=dwc2

Auto-mount: /etc/fstab
→ Contains: /piusb.bin /mnt/usb_share vfat users,umask=000,uid=1000,gid=1000 0 0

SYSTEMD SERVICE:
----------------
Service file: /etc/systemd/system/usb-gadget.service
Init script: /usr/local/bin/init-usb-gadget.sh
Reload script: /usr/local/bin/reload-usb-gadget.sh

To view service status:
→ sudo systemctl status usb-gadget.service

To restart service:
→ sudo systemctl restart usb-gadget.service

To view service logs:
→ sudo journalctl -u usb-gadget.service -f

To disable service (stops automatic loading):
→ sudo systemctl disable usb-gadget.service

To re-enable service:
→ sudo systemctl enable usb-gadget.service

LOG FILES:
----------
Systemd journal: sudo journalctl -u usb-gadget.service
Custom log: /var/log/usb-gadget.log
System log: dmesg | grep gadget

UNINSTALLING:
-------------
If you want to remove this configuration:

1. Disable service:
   sudo systemctl disable usb-gadget.service
   sudo systemctl stop usb-gadget.service

2. Remove service file:
   sudo rm /etc/systemd/system/usb-gadget.service
   sudo systemctl daemon-reload

3. Remove USB image:
   sudo umount /mnt/usb_share
   sudo rm /piusb.bin

4. Remove fstab entry:
   sudo sed -i '\|/piusb.bin|d' /etc/fstab

5. Remove scripts:
   sudo rm /usr/local/bin/init-usb-gadget.sh
   sudo rm /usr/local/bin/reload-usb-gadget.sh

6. Remove from cmdline.txt:
   Manually edit to remove modules-load parameter

7. Remove from config.txt:
   Manually remove dtoverlay=dwc2 line

8. Reboot

=====================================================
For questions or issues, check the Raspberry Pi forums
or the USB gadget mode documentation.
=====================================================
FINALREADME

chown pi:pi /home/pi/README_USB_DRIVE.txt 2>/dev/null || true
echo "✓ Created comprehensive README at /home/pi/README_USB_DRIVE.txt"

# Final summary
echo ""
echo "====================================================="
echo "Installation Complete!"
echo "====================================================="
echo ""
echo "Your Raspberry Pi is now configured as a USB drive!"
echo ""
echo "SUMMARY:"
echo "--------"
echo "✓ USB Image: $USB_FILE_PATH"
echo "✓ Format: FAT32 (Windows compatible)"
echo "✓ Size: $(du -h "$USB_FILE_PATH" 2>/dev/null | cut -f1 || echo "Unknown")"
echo "✓ Mount Point: $MOUNT_POINT"
echo "✓ Volume Label: PI_USB"
echo "✓ Auto-load: Yes (via systemd service)"
echo ""
echo "SERVICE CONFIGURED:"
echo "-------------------"
echo "✓ usb-gadget.service - Auto-loads USB gadget on boot"
echo ""
echo "SCRIPTS CREATED:"
echo "----------------"
echo "✓ /usr/local/bin/init-usb-gadget.sh (runs at boot)"
echo "✓ /usr/local/bin/reload-usb-gadget.sh (manual reload)"
echo "✓ ~/reload_usb_gadget.sh (convenience symlink)"
echo ""
echo "HOW TO USE:"
echo "-----------"
echo "1. Reboot your Pi (REQUIRED for changes to take effect)"
echo "2. Connect Pi to computer via USB DATA port"
echo "3. Wait 20-30 seconds"
echo "4. Drive appears as 'PI_USB' on your computer"
echo ""
echo "AFTER REBOOT:"
echo "-------------"
echo "Everything should load automatically!"
echo ""
echo "If the drive doesn't appear, run:"
echo "  sudo ~/reload_usb_gadget.sh"
echo ""
echo "Check service status:"
echo "  sudo systemctl status usb-gadget.service"
echo ""
echo "Check logs:"
echo "  sudo journalctl -u usb-gadget.service"
echo ""
echo "DOCUMENTATION:"
echo "--------------"
echo "→ Full guide: /home/pi/README_USB_DRIVE.txt"
echo "→ On USB drive: $MOUNT_POINT/README.txt"
echo ""
echo "====================================================="
echo "A reboot is REQUIRED for all changes to take effect."
echo "====================================================="
echo ""

# Ask about rebooting
read -p "Would you like to reboot now? (RECOMMENDED) (y/n): " REBOOT_NOW

if [[ "$REBOOT_NOW" == "y" || "$REBOOT_NOW" == "Y" ]]; then
  echo ""
  echo "Rebooting in 5 seconds..."
  echo "After reboot, connect your Pi to a computer via USB DATA port."
  echo "Wait 20-30 seconds for the drive to appear."
  sleep 5
  reboot
else
  echo ""
  echo "Please reboot manually when ready:"
  echo "  sudo reboot"
  echo ""
  echo "After reboot, the USB gadget will load automatically!"
fi
