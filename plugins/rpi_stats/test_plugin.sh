#!/bin/bash
# Quick test script to verify the Raspberry Pi Stats plugin setup

echo "=== Raspberry Pi Stats Plugin - Health Check ==="
echo ""

# Check if psutil is installed
echo "1. Checking psutil installation..."
if python3 -c "import psutil" 2>/dev/null; then
    VERSION=$(python3 -c "import psutil; print(psutil.__version__)")
    echo "   ✓ psutil is installed (version $VERSION)"
else
    echo "   ✗ psutil is NOT installed"
    echo "   Run: pip3 install --user psutil"
    exit 1
fi

# Check if plugin directory exists
echo ""
echo "2. Checking plugin files..."
# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/__init__.py" ]; then
    echo "   ✓ Plugin files exist"
else
    echo "   ✗ Plugin files not found"
    exit 1
fi

# Test basic psutil functionality
echo ""
echo "3. Testing psutil functionality..."
python3 << 'EOF'
import psutil
print(f"   ✓ CPU cores: {psutil.cpu_count()}")
print(f"   ✓ CPU usage: {psutil.cpu_percent(interval=0.1)}%")
print(f"   ✓ Memory: {round(psutil.virtual_memory().percent, 1)}%")
print(f"   ✓ Disk: {round(psutil.disk_usage('/').percent, 1)}%")

# Try temperature
try:
    with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
        temp = float(f.read().strip()) / 1000.0
        print(f"   ✓ Temperature: {temp}°C")
except:
    print(f"   ⚠ Temperature sensor not available (normal on non-RPi systems)")
EOF

echo ""
echo "=== All checks passed! ==="
echo ""
echo "Next steps:"
echo "1. Restart ChitUI: python3 main.py"
echo "2. Open ChitUI in your browser"
echo "3. Navigate to any printer's information page"
echo "4. Click the 'System Stats' tab"
echo ""
