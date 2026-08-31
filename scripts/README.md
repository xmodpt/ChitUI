# ChitUI - Scripts Directory

This directory contains utility scripts for ChitUI installation, service management, and USB gadget configuration.

## Installation Scripts

### virtual_usb_gadget_fixed.sh
Complete setup script for USB gadget mode on Raspberry Pi.

**Usage:**
```bash
sudo ./scripts/virtual_usb_gadget_fixed.sh
```

**What it does:**
- Configures kernel modules (dwc2, g_mass_storage)
- Creates virtual USB drive image (/piusb.bin)
- Sets up automatic mounting at /mnt/usb_share
- Configures boot files (/boot/config.txt, /boot/cmdline.txt)
- Creates systemd service for auto-start on boot

**Requirements:**
- Raspberry Pi with OTG support (Pi Zero, Zero W, Zero 2 W, Pi 4, Pi 5)
- Root permissions
- Free space on SD card for virtual drive image

---

### install_service.sh
Installs ChitUI as a systemd service.

**Usage:**
```bash
./scripts/install_service.sh
```

**What it does:**
- Creates systemd service file
- Enables auto-start on boot
- Configures logging to ~/.chitui/service.log
- Sets up automatic restart on failure

**Service commands after installation:**
```bash
sudo systemctl status chitui    # Check status
sudo systemctl restart chitui   # Restart service
sudo systemctl stop chitui      # Stop service
journalctl -u chitui -f         # View logs
```

---

### uninstall_service.sh
Removes the ChitUI systemd service.

**Usage:**
```bash
./scripts/uninstall_service.sh
```

---

## USB Gadget Management Scripts

### reload_usb_gadget.sh
Reloads the USB gadget to force the printer to detect file changes.

**Usage:**
```bash
sudo ./scripts/reload_usb_gadget.sh
```

**What it does:**
- Unmounts /mnt/usb_share
- Unloads USB gadget kernel modules
- Reloads modules
- Remounts /mnt/usb_share
- Triggers printer to re-enumerate USB device

**When to use:**
- After uploading new files via web interface
- When printer doesn't see new files
- To force file list refresh

**Note:** Some printers may crash during reload. If this happens, disable USB_AUTO_REFRESH in ChitUI settings.

---

### start_usb_gadget.sh
Starts the USB gadget service.

**Usage:**
```bash
sudo ./scripts/start_usb_gadget.sh
```

---

### stop_usb_gadget.sh
Stops the USB gadget service.

**Usage:**
```bash
sudo ./scripts/stop_usb_gadget.sh
```

---

### check_usb_gadget.sh
Diagnostic script to check USB gadget configuration.

**Usage:**
```bash
bash ./scripts/check_usb_gadget.sh
```

**What it checks:**
- Kernel modules loaded (dwc2, g_mass_storage)
- Virtual drive image exists (/piusb.bin)
- Mount point exists and is writable (/mnt/usb_share)
- UDC controller status
- Boot configuration files
- Systemd service status

**Use this when:**
- USB gadget not working
- Troubleshooting connection issues
- Verifying installation

---

### reload_usb_with_file.sh
Reloads USB gadget and creates a test file.

**Usage:**
```bash
sudo ./scripts/reload_usb_with_file.sh
```

**What it does:**
- Reloads USB gadget
- Creates a test file on the virtual drive
- Useful for testing if printer can see new files

---

## Application Control Scripts

### restart.sh
Restarts ChitUI service.

**Usage:**
```bash
./scripts/restart.sh
```

**What it does:**
- Stops chitui systemd service
- Waits for clean shutdown
- Starts service again

**When to use:**
- After configuration changes
- After updating code
- To apply plugin changes

---

## Permissions

Most USB gadget scripts require root permissions because they:
- Load/unload kernel modules
- Mount/unmount filesystems
- Write to system configuration files

The installer configures passwordless sudo for these operations if you choose to install USB gadget support.

---

## Troubleshooting

### USB Gadget Not Working

1. Run diagnostic check:
   ```bash
   bash ./scripts/check_usb_gadget.sh
   ```

2. Check if modules are loaded:
   ```bash
   lsmod | grep -E 'dwc2|g_mass_storage'
   ```

3. Check mount point:
   ```bash
   ls -la /mnt/usb_share
   df -h | grep usb_share
   ```

4. Check kernel messages:
   ```bash
   dmesg | tail -50
   ```

### Service Not Starting

1. Check service status:
   ```bash
   sudo systemctl status chitui-plus
   ```

2. View logs:
   ```bash
   journalctl -u chitui-plus -n 50 --no-pager
   ```

3. Check for port conflicts:
   ```bash
   sudo lsof -i :8080
   ```

### Printer Crashes on USB Reload

If your printer crashes when USB gadget reloads:

1. Disable auto-refresh in ChitUI settings
2. Set environment variable: `USB_AUTO_REFRESH=false`
3. Manually refresh by power-cycling printer or unplugging USB

---

## Notes

- Always use `sudo` for USB gadget scripts
- USB gadget requires OTG-capable USB port
- Some Raspberry Pi models need additional configuration
- Service scripts don't require sudo (unless configured otherwise)
- All scripts are designed to be run from any directory
