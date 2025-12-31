#!/bin/bash
# Start USB Gadget Script
# Loads modules and mounts USB gadget

echo "============================================"
echo "Starting USB Gadget"
echo "============================================"
echo ""

# Check if /piusb.bin exists
if [ ! -f /piusb.bin ]; then
    echo "✗ Error: /piusb.bin not found"
    echo ""
    echo "Please create the USB disk image first:"
    echo "  sudo dd if=/dev/zero of=/piusb.bin bs=1M count=512"
    echo "  sudo mkfs.vfat /piusb.bin"
    echo "  sudo mkdir -p /mnt/usb_share"
    echo ""
    exit 1
fi

# Step 1: Load kernel modules
echo "Step 1: Loading kernel modules..."
modprobe dwc2 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✓ dwc2 loaded"
else
    echo "⚠ dwc2 already loaded or failed"
fi
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
sleep 2

# Step 3: Connect via configfs if available
echo "Step 3: Connecting USB gadget..."
if [ -d "/sys/kernel/config/usb_gadget" ]; then
    echo "Checking for configfs gadgets..."
    for gadget in /sys/kernel/config/usb_gadget/*; do
        if [ -d "$gadget" ]; then
            UDC_FILE="$gadget/UDC"
            if [ -f "$UDC_FILE" ]; then
                # Find UDC device
                UDC_DEVICE=$(ls /sys/class/udc/ 2>/dev/null | head -n1)
                if [ -n "$UDC_DEVICE" ]; then
                    echo "$UDC_DEVICE" > "$UDC_FILE" 2>/dev/null
                    echo "✓ Connected via configfs: $(basename $gadget) -> $UDC_DEVICE"
                fi
            fi
        fi
    done
else
    echo "Using legacy g_mass_storage (no configfs)"
fi
sleep 2

# Step 4: Mount
echo "Step 4: Mounting /mnt/usb_share..."
if ! mount | grep -q "/mnt/usb_share"; then
    # Create mount point if it doesn't exist
    mkdir -p /mnt/usb_share

    # Try mounting from fstab first
    mount /mnt/usb_share 2>/dev/null

    # If that fails, mount manually
    if ! mount | grep -q "/mnt/usb_share"; then
        mount -t vfat -o loop,rw,umask=000,uid=1000,gid=1000 /piusb.bin /mnt/usb_share
    fi

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
echo "✓ USB gadget started!"
echo "============================================"
echo ""
echo "The Pi should now appear as a USB drive on the printer."
echo "Drive name: PI_USB"
echo ""
echo "To verify:"
echo "  ls /mnt/usb_share/"
echo "  mount | grep usb_share"
echo ""
