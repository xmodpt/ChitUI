# Raspberry Pi Stats Plugin

Display comprehensive Raspberry Pi system statistics and information in ChitUI.

## Features

- **System Information**
  - Device hostname and model
  - Operating system and Python version
  - CPU cores and threads
  - System uptime

- **Real-time Statistics** (auto-refreshes every 5 seconds)
  - CPU usage (overall and per-core)
  - CPU frequency and temperature
  - Memory usage
  - Disk usage
  - Network I/O statistics

## Installation

This plugin is included with ChitUI and **automatically installs** its dependencies.

### Automatic Installation (Default)

Simply restart ChitUI and the plugin will:
- Automatically install `psutil` if not present
- Load and display system statistics
- No manual intervention needed!

### Manual Installation (If Needed)

If automatic installation fails, install psutil manually:

```bash
pip3 install psutil
```

Then restart ChitUI.

For detailed installation instructions and troubleshooting, see [INSTALL.md](INSTALL.md).

## UI Integration

The plugin adds a **"System Stats"** tab under the printer information section, providing an easy-to-read dashboard of your Raspberry Pi's performance.

## API Endpoints

The plugin exposes the following HTTP endpoints:

- `GET /plugin/rpi_stats/system-info` - Get static system information
- `GET /plugin/rpi_stats/stats` - Get current real-time statistics

## Requirements

- Python 3.7+
- psutil (automatically installed)
- Works best on Raspberry Pi hardware (temperature monitoring)
- Compatible with other Linux systems with limited temperature support

## Temperature Monitoring

The plugin attempts to read CPU temperature from multiple sources:
1. `/sys/class/thermal/thermal_zone0/temp` (standard Linux thermal zone)
2. `vcgencmd measure_temp` (Raspberry Pi specific)
3. `psutil.sensors_temperatures()` (if available)

If none of these sources are available, temperature will not be displayed.

## Version

1.0.0

## Author

ChitUI
