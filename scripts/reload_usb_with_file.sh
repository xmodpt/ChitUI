#!/bin/bash
# Manual USB Gadget Reload Script with File Copy
# Use this to copy a file and make the printer detect it

echo "============================================"
echo "USB Gadget File Copy & Reload"
echo "============================================"
echo ""

# Check if file argument provided
if [ -z "$1" ]; then
    echo "Usage: $0 <file.goo>"
    echo "Example: $0 criptex.goo"
    exit 1
fi

SOURCE_FILE="$1"
if [ ! -f "$SOURCE_FILE" ]; then
    echo "Error: File '$SOURCE_FILE' not found"
    exit 1
fi

FILENAME=$(basename "$SOURCE_FILE")

# Step 1: Mount if not already mounted
echo "Step 1: Mounting /mnt/usb_share..."
if ! mount | grep -q "/mnt/usb_share"; then
    mount /mnt/usb_share 2>/dev/null || mount -t vfat -o loop,rw,umask=000,uid=1000,gid=1000 /piusb.bin /mnt/usb_share
    sleep 1
else
    echo "Already mounted"
fi

# Step 2: Copy file
echo "Step 2: Copying $FILENAME to /mnt/usb_share..."
cp "$SOURCE_FILE" /mnt/usb_share/
if [ $? -eq 0 ]; then
    echo "✓ Copy successful"
else
    echo "✗ Copy failed"
    exit 1
fi

# Step 3: Sync filesystem to disk (CRITICAL!)
echo "Step 3: Syncing filesystem to disk..."
sync
echo "✓ Filesystem synced"
sleep 1

# Step 4: Unmount (CRITICAL!)
echo "Step 4: Unmounting /mnt/usb_share..."
umount /mnt/usb_share
if [ $? -eq 0 ]; then
    echo "✓ Unmounted"
else
    echo "⚠ Unmount failed (continuing anyway)"
fi
sleep 1

# Step 5: Disconnect USB gadget from printer
echo "Step 5: Disconnecting USB gadget from printer..."
modprobe -r g_mass_storage 2>/dev/null || true
modprobe -r dwc2 2>/dev/null || true
echo "✓ USB disconnected"
sleep 4  # Give printer time to detect disconnect

# Step 6: Reconnect USB gadget to printer
echo "Step 6: Reconnecting USB gadget to printer..."
modprobe dwc2
sleep 1

modprobe g_mass_storage file=/piusb.bin stall=0 ro=0 removable=1 \
    idVendor=0x0951 idProduct=0x1666 \
    iManufacturer=Kingston iProduct=DataTraveler \
    iSerialNumber=74A53CDF

if [ $? -eq 0 ]; then
    echo "✓ USB reconnected"
else
    echo "✗ Module reload failed"
    exit 1
fi
sleep 4  # Give printer time to detect reconnect and enumerate

# Step 7: Remount for future access
echo "Step 7: Remounting /mnt/usb_share..."
mount /mnt/usb_share 2>/dev/null || mount -t vfat -o loop,rw,umask=000,uid=1000,gid=1000 /piusb.bin /mnt/usb_share
echo "✓ Remounted"

echo ""
echo "============================================"
echo "✓ USB gadget reload complete!"
echo "============================================"
echo ""
echo "File '$FILENAME' should now be visible on the printer."
echo "Wait ~10 seconds for printer to finish scanning."
echo ""
echo "To verify on Pi: ls /mnt/usb_share/"
echo ""
