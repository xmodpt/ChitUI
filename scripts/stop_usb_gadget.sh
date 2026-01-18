#!/bin/bash
# Stop USB Gadget Script
# Unmounts and unloads USB gadget modules

echo "============================================"
echo "Stopping USB Gadget"
echo "============================================"
echo ""

# Step 1: Sync filesystem
echo "Step 1: Syncing filesystem to disk..."
sync
echo "✓ Filesystem synced"
sleep 1

# Step 2: Unmount
echo "Step 2: Unmounting /mnt/usb_share..."
if mount | grep -q "/mnt/usb_share"; then
    umount /mnt/usb_share
    if [ $? -eq 0 ]; then
        echo "✓ Unmounted"
    else
        echo "⚠ Unmount failed"
        # Try force unmount
        echo "Trying force unmount..."
        umount -f /mnt/usb_share 2>/dev/null || umount -l /mnt/usb_share 2>/dev/null
    fi
else
    echo "Already unmounted"
fi
sleep 1

# Step 3: Disconnect via configfs if available
echo "Step 3: Disconnecting USB gadget..."
if [ -d "/sys/kernel/config/usb_gadget" ]; then
    echo "Using configfs method..."
    for gadget in /sys/kernel/config/usb_gadget/*; do
        if [ -d "$gadget" ]; then
            UDC_FILE="$gadget/UDC"
            if [ -f "$UDC_FILE" ]; then
                echo "" > "$UDC_FILE" 2>/dev/null
                echo "✓ Disconnected via configfs: $(basename $gadget)"
            fi
        fi
    done
fi
sleep 1

# Step 4: Unload kernel modules
echo "Step 4: Unloading kernel modules..."
modprobe -r g_mass_storage 2>/dev/null && echo "✓ g_mass_storage unloaded" || echo "- g_mass_storage not loaded"
modprobe -r dwc2 2>/dev/null && echo "✓ dwc2 unloaded" || echo "- dwc2 not loaded"

echo ""
echo "============================================"
echo "✓ USB gadget stopped!"
echo "============================================"
echo ""
echo "The Pi is now disconnected from the printer."
echo "USB mass storage is disabled."
echo ""
