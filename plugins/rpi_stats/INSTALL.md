# Installation Instructions

## Installing psutil

The Raspberry Pi Stats plugin requires the `psutil` library. Install it using one of the following methods:

### Method 1: Automatic Installation (Recommended)

The plugin will **automatically** install psutil when it's first loaded. Just restart ChitUI and the plugin will:

1. Check if psutil is already installed
2. If not, try to install it using `pip3 install --user psutil` (no sudo needed)
3. Fallback to other installation methods if needed
4. Load the plugin with psutil enabled

**No manual intervention required** in most cases!

### Method 2: Manual Installation

If automatic installation fails, install psutil manually:

```bash
pip3 install psutil
```

Or with sudo if needed:

```bash
sudo pip3 install psutil
```

### Method 3: Using apt (Debian/Ubuntu/Raspberry Pi OS)

```bash
sudo apt-get update
sudo apt-get install python3-psutil
```

## Verifying Installation

To verify psutil is installed correctly:

```bash
python3 -c "import psutil; print('psutil version:', psutil.__version__)"
```

You should see output like: `psutil version: 5.9.0`

## Troubleshooting

### Plugin loads but shows no data

1. **Check ChitUI logs:**
   Look for messages about dependency installation when the plugin loads.
   You should see:
   - `Installing dependency: psutil` - Plugin is trying to install
   - `✓ Successfully installed: psutil` - Success!
   - `✗ Failed to install psutil` - Installation failed

2. **If automatic installation failed:**
   ```bash
   # Try user install (no sudo)
   pip3 install --user psutil

   # Or system install
   pip3 install psutil

   # Then restart ChitUI
   ```

3. **Verify installation:**
   ```bash
   python3 -c "import psutil; print('psutil OK')"
   ```

### Permission errors when installing

If you get permission errors:

```bash
# Try with sudo
sudo pip3 install psutil

# Or install in user directory
pip3 install --user psutil
```

### Temperature not showing

Temperature monitoring requires specific hardware sensors:

- **Raspberry Pi**: Should work automatically with `/sys/class/thermal/thermal_zone0/temp`
- **Other Linux systems**: May require additional sensor packages or may not be supported

This is normal and the rest of the plugin will still function.

## Support

If you continue to have issues, check:
- ChitUI logs for error messages
- Browser console (F12) for JavaScript errors
- Ensure ChitUI has been restarted after installing psutil
