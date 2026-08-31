#!/bin/bash
# Reload USB Gadget Script
# Stops and restarts USB gadget to make printer detect changes

echo "============================================"
echo "Reloading USB Gadget"
echo "============================================"
echo ""

# ==================== STOP ====================
echo ">>> STOPPING USB GADGET <<<"
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
        umount -f /mnt/usb_share 2>/dev/null || umount -l /mnt/usb_share 2>/dev/null
    fi
else
    echo "Already unmounted"
fi
sleep 1

# Step 3: Disconnect via configfs if available
echo "Step 3: Disconnecting USB gadget from printer..."
if [ -d "/sys/kernel/config/usb_gadget" ]; then
    for gadget in /sys/kernel/config/usb_gadget/*; do
        if [ -d "$gadget" ]; then
            UDC_FILE="$gadget/UDC"
            if [ -f "$UDC_FILE" ]; then
                echo "" > "$UDC_FILE" 2>/dev/null
                echo "✓ Disconnected via configfs"
            fi
        fi
    done
fi

# Step 4: Unload kernel modules
echo "Step 4: Unloading kernel modules..."
modprobe -r g_mass_storage 2>/dev/null && echo "✓ g_mass_storage unloaded" || echo "- g_mass_storage not loaded"
modprobe -r dwc2 2>/dev/null && echo "✓ dwc2 unloaded" || echo "- dwc2 not loaded"
echo "✓ USB disconnected from printer"
sleep 1  # Give printer time to detect disconnect

echo ""
echo "--- Waiting 1 second ---"
sleep 1
echo ""

# ==================== START ====================
echo ">>> STARTING USB GADGET <<<"
echo ""

# Check if /piusb.bin exists
if [ ! -f /piusb.bin ]; then
    echo "✗ Error: /piusb.bin not found"
    exit 1
fi

# Step 1: Load kernel modules
echo "Step 1: Loading dwc2 module..."
modprobe dwc2 2>/dev/null
echo "✓ dwc2 loaded"
sleep 1

echo "Step 2: Loading g_mass_storage module..."
modprobe g_mass_storage \
    file=/piusb.bin \
    stall=0 \
    ro=0 \
    removable=1 \
    idVendor=0x0951 \
    idProduct=0x1666 \
    iManufacturer=Kingston \
    iProduct=DataTraveler \
    iSerialNumber=74A53CDF

if [ $? -eq 0 ]; then
    echo "✓ g_mass_storage loaded"
else
    echo "✗ g_mass_storage failed to load"
    exit 1
fi

# Step 3: Connect via configfs if available
echo "Step 3: Connecting USB gadget to printer..."
if [ -d "/sys/kernel/config/usb_gadget" ]; then
    for gadget in /sys/kernel/config/usb_gadget/*; do
        if [ -d "$gadget" ]; then
            UDC_FILE="$gadget/UDC"
            if [ -f "$UDC_FILE" ]; then
                UDC_DEVICE=$(ls /sys/class/udc/ 2>/dev/null | head -n1)
                if [ -n "$UDC_DEVICE" ]; then
                    echo "$UDC_DEVICE" > "$UDC_FILE" 2>/dev/null
                    echo "✓ Connected via configfs"
                fi
            fi
        fi
    done
fi
echo "✓ USB reconnected to printer"
sleep 1  # Give printer time to detect reconnect

# Step 4: Mount
echo "Step 4: Mounting /mnt/usb_share..."
if ! mount | grep -q "/mnt/usb_share"; then
    mkdir -p /mnt/usb_share
    mount /mnt/usb_share 2>/dev/null || mount -t vfat -o loop,rw,umask=000,uid=1000,gid=1000 /piusb.bin /mnt/usb_share

    if mount | grep -q "/mnt/usb_share"; then
        echo "✓ Mounted"
    else
        echo "✗ Mount failed"
        exit 1
    fi
else
    echo "Already mounted"
fi

echo ""
echo "============================================"
echo "✓ USB gadget reload complete!"
echo "============================================"
echo ""
echo "The printer should detect changes in ~3 seconds."
echo ""
echo "To verify: ls /mnt/usb_share/"
echo ""
