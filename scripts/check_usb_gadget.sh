#!/bin/bash
# USB Gadget Diagnostic Script for ChitUI
# This script checks your USB gadget configuration and provides setup guidance

echo "======================================"
echo "ChitUI USB Gadget Diagnostic Tool"
echo "======================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "⚠️  Not running as root"
  echo "   Some checks require root access"
  echo "   Run with: sudo bash check_usb_gadget.sh"
  echo ""
fi

# Check 1: USB Gadget folder
echo "1. Checking USB Gadget folder..."
if [ -d "/mnt/usb_share" ]; then
  echo "   ✓ /mnt/usb_share exists"
  if [ -w "/mnt/usb_share" ]; then
    echo "   ✓ /mnt/usb_share is writable"
  else
    echo "   ✗ /mnt/usb_share is NOT writable"
    echo "   💡 Fix: sudo chmod 777 /mnt/usb_share"
  fi
else
  echo "   ✗ /mnt/usb_share does not exist"
  echo "   💡 Create it with: sudo mkdir -p /mnt/usb_share"
fi
echo ""

# Check 2: Configfs
echo "2. Checking Configfs..."
if [ -d "/sys/kernel/config/usb_gadget" ]; then
  echo "   ✓ Configfs USB gadget available"
  gadgets=$(ls /sys/kernel/config/usb_gadget/ 2>/dev/null)
  if [ -n "$gadgets" ]; then
    echo "   ✓ Found gadgets: $gadgets"
    for gadget in $gadgets; do
      udc_path="/sys/kernel/config/usb_gadget/$gadget/UDC"
      if [ -f "$udc_path" ]; then
        udc_value=$(cat "$udc_path" 2>/dev/null)
        if [ -n "$udc_value" ]; then
          echo "   ✓ $gadget UDC: $udc_value (ACTIVE)"
        else
          echo "   ⚠️  $gadget UDC: (not connected)"
        fi
      fi
    done
  else
    echo "   ⚠️  No gadgets configured"
  fi
else
  echo "   ✗ Configfs USB gadget not available"
fi
echo ""

# Check 3: UDC Controllers
echo "3. Checking UDC Controllers..."
if [ -d "/sys/class/udc" ]; then
  udcs=$(ls /sys/class/udc/ 2>/dev/null)
  if [ -n "$udcs" ]; then
    echo "   ✓ Found UDC controllers:"
    for udc in $udcs; do
      echo "      - $udc"
    done
  else
    echo "   ✗ No UDC controllers found"
  fi
else
  echo "   ✗ /sys/class/udc not available"
fi
echo ""

# Check 4: Legacy g_mass_storage
echo "4. Checking legacy g_mass_storage module..."
if lsmod 2>/dev/null | grep -q "g_mass_storage"; then
  echo "   ⚠️  g_mass_storage module is loaded (legacy mode)"
  echo "   💡 This module doesn't support automatic refresh"
  echo "   💡 Consider migrating to configfs for better control"
elif [ -d "/sys/module/g_mass_storage" ]; then
  echo "   ⚠️  g_mass_storage module detected (legacy mode)"
else
  echo "   ✓ Not using legacy g_mass_storage"
fi
echo ""

# Check 5: Recommended setup
echo "======================================"
echo "RECOMMENDATIONS:"
echo "======================================"
echo ""

if [ ! -d "/sys/kernel/config/usb_gadget" ]; then
  echo "❌ Configfs not available"
  echo ""
  echo "To enable automatic USB gadget refresh, you need to:"
  echo "1. Enable configfs in your kernel (usually enabled by default on modern Pi OS)"
  echo "2. Mount configfs: sudo mount -t configfs none /sys/kernel/config"
  echo "3. Set up USB gadget using configfs instead of g_mass_storage module"
  echo ""
  echo "Quick setup script for Raspberry Pi:"
  echo "  See: https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/configuration/use-a-raspberry-pi-for-file-storage.adoc"
  echo ""
elif [ ! -w "/sys/kernel/config/usb_gadget" ] && [ "$EUID" -ne 0 ]; then
  echo "⚠️  Run ChitUI as root to enable automatic refresh:"
  echo ""
  echo "  sudo python3 main.py"
  echo ""
else
  echo "✓ Your system appears to be correctly configured!"
  echo ""
  echo "If automatic refresh still doesn't work:"
  echo "1. Make sure ChitUI is running as root: sudo python3 main.py"
  echo "2. Check ChitUI logs for error messages"
  echo "3. Verify USB cable is connected between Pi and printer"
  echo ""
fi

echo "======================================"
echo "CURRENT WORKAROUNDS:"
echo "======================================"
echo ""
echo "Until automatic refresh is working, you can:"
echo "1. Manually refresh file list on printer screen after upload"
echo "2. Disconnect and reconnect USB cable between uploads"
echo "3. Printer may auto-detect after a few seconds (varies by printer)"
echo ""
echo "The file IS saved correctly - it just takes time for printer to see it."
echo ""
