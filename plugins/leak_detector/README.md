# Leak Detector Plugin for ChitUI

ESP32-based resin leak detection system with real-time monitoring and alerts for 3D printer safety.

## Overview

This plugin integrates an ESP32 leak detector device with ChitUI, providing:

- **Real-time monitoring** of 3 leak sensors
- **Instant alerts** when leaks are detected
- **Device status tracking** (online/offline, IP address, chip type)
- **Alert history** with timestamps and sensor locations
- **WebSocket-based live updates** for instant notifications
- **Browser notifications** for critical alerts (when enabled)

## Features

### Device Integration
- Automatic discovery via mDNS
- Manual configuration via web interface
- WiFi connectivity with OTA updates
- Capacitive touch sensors (ESP32-S3) or analog sensors (ESP32-C3)

### Monitoring Capabilities
- **3 Sensor Zones:**
  - Sensor 1: Vat Center
  - Sensor 2: Front Edge
  - Sensor 3: Build Plate
- Real-time value display
- Alert threshold monitoring
- Auto-calibration on startup

### Alert System
- Visual alerts in ChitUI toolbar
- Browser push notifications (optional)
- Alert history with timestamps
- Configurable alert cooldown period
- One-click alert clearing

## Installation

### 1. ESP32 Hardware Setup

**Components:**
- ESP32-S3 or ESP32-C3 development board
- 3x capacitive touch sensors or analog moisture sensors
- LED indicator (GPIO 8)
- Reset button (GPIO 7)
- WiFi connection

**Pin Configuration:**
```
SENSOR_PIN_1 = 4    // Vat center
SENSOR_PIN_2 = 5    // Front edge
SENSOR_PIN_3 = 6    // Build plate
LED_PIN = 8         // Status LED
RESET_BUTTON = 7    // Config reset
```

### 2. Flash ESP32 Firmware

1. Open `esp32_s3_leak_detector.ino` in Arduino IDE
2. Install required libraries:
   - WiFi
   - WiFiManager
   - HTTPClient
   - Preferences
   - ESPmDNS
   - ArduinoOTA
   - WebServer
3. Select your board (ESP32-S3 or ESP32-C3)
4. Upload to device

### 3. Install ChitUI Plugin

**Option A: Upload via ChitUI Interface**
1. Zip the `leak_detector` directory
2. Navigate to ChitUI Settings → Plugins
3. Click "Upload Plugin"
4. Select the zip file
5. Enable the plugin

**Option B: Manual Installation**
1. Copy the `leak_detector` directory to ChitUI's `plugins/` folder
2. Restart ChitUI
3. Enable plugin in Settings → Plugins

### 4. Configure ESP32 Connection

**Automatic Discovery (Recommended):**
- The ESP32 will automatically discover ChitUI via mDNS
- No configuration needed if both devices are on the same network

**Manual Configuration:**
1. Connect to ESP32's WiFi access point: `LeakSensor-Setup`
2. Configure WiFi credentials
3. Access ESP32 web interface at its IP address
4. Click "Configure"
5. Enter ChitUI IP address and port (default: 5000)
6. Save configuration

## Usage

### Initial Setup

1. **Power on ESP32** - LED will light up during startup
2. **Wait for calibration** - Ensure chassis is dry during the 2-second calibration period
3. **Verify connection** - Check ChitUI toolbar for "Leak Detector" icon with green indicator

### Monitoring

- Click the **droplet icon** in ChitUI toolbar to open the leak detector panel
- Monitor real-time sensor values
- Check device status (online/offline, IP, chip type)
- View recent alerts

### When a Leak is Detected

1. **ESP32 Response:**
   - LED flashes 3 times
   - Serial output logs the event
   - HTTP POST sent to ChitUI

2. **ChitUI Response:**
   - Sensor card turns red with animation
   - Alert appears in alerts list
   - Browser notification (if enabled)
   - WebSocket broadcast to all connected clients

3. **User Actions:**
   - Investigate the indicated sensor location
   - Clear leak/moisture
   - Alerts clear automatically after 5 seconds cooldown
   - Click "Clear Alerts" to reset history

### Recalibration

If sensors drift or after hardware changes:
1. Access ESP32 web interface
2. Click "Recalibrate"
3. Ensure chassis is dry
4. Wait for calibration to complete

## API Endpoints

### ESP32 → ChitUI

**POST /api/leak_alert**
```json
{
  "sensor": 1,
  "location": "vat_center",
  "value": 450,
  "threshold": 150,
  "timestamp": "00:15:23",
  "device_ip": "192.168.1.100",
  "alert": true
}
```

**POST /api/sensor_status**
```json
{
  "status": "online",
  "ip": "192.168.1.100",
  "chip": "ESP32-S3",
  "version": "1.0.0"
}
```

### ChitUI Plugin API

**GET /plugin/leak_detector/status**
- Returns device status, sensors, and recent alerts

**GET /plugin/leak_detector/alerts**
- Returns full alert history

**GET /plugin/leak_detector/sensors**
- Returns current sensor readings

**POST /plugin/leak_detector/clear_alerts**
- Clears all alerts

## WebSocket Events

**Client → Server:**
- `subscribe_leak_detector` - Subscribe to updates

**Server → Client:**
- `leak_detector_data` - Initial data on subscription
- `leak_detector_update` - Periodic updates
- `leak_detector_alert` - Critical leak alert notification

## Configuration

### ESP32 Parameters

Edit in `esp32_s3_leak_detector.ino`:

```cpp
#define THRESHOLD 150          // Detection threshold
#define CHECK_INTERVAL 1000    // Check frequency (ms)
#define ALERT_COOLDOWN 5000    // Alert cooldown (ms)
#define CALIBRATION_SAMPLES 20 // Calibration samples
```

### Plugin Settings

- Maximum stored alerts: 50 (oldest are removed)
- Default display: Last 10 alerts
- No external dependencies required

## Troubleshooting

### ESP32 Not Connecting to WiFi
1. Hold reset button for 5+ seconds to clear settings
2. Connect to `LeakSensor-Setup` AP
3. Reconfigure WiFi credentials

### ESP32 Can't Find ChitUI
1. Ensure both devices are on same network
2. Check ChitUI mDNS service is running
3. Manually configure ChitUI IP via ESP32 web interface

### No Alerts Appearing in ChitUI
1. Check ESP32 serial output for HTTP errors
2. Verify ChitUI URL is correct (check ESP32 logs)
3. Ensure plugin is enabled in ChitUI settings
4. Check ChitUI logs for incoming requests

### False Alarms
1. Recalibrate sensors
2. Increase threshold value
3. Check sensor positioning
4. Ensure sensors are dry during calibration

### LED Indicators
- **Solid on (startup)** - Booting
- **Flashing (5 times)** - Config mode active
- **Off** - Normal operation
- **3 flashes** - Leak detected
- **10 flashes** - OTA update error
- **Pulsing** - OTA update in progress

## OTA Updates

Update ESP32 firmware over-the-air:

```bash
# Using Arduino IDE
1. Sketch → Export Compiled Binary
2. Tools → Port → Select "leak-sensor at <IP>"
3. Upload

# Using platformio
pio run --target upload --upload-port <IP>
```

**OTA Password:** `chitui2025`

## Development

### Adding New Features

**ESP32 side:**
1. Modify `.ino` file
2. Add new sensors or endpoints
3. Update API calls to ChitUI

**ChitUI Plugin side:**
1. Add routes in `__init__.py`
2. Update template in `templates/leak_detector.html`
3. Modify WebSocket handlers as needed

### File Structure
```
leak_detector/
├── plugin.json              # Plugin manifest
├── __init__.py             # Main plugin code
├── templates/
│   └── leak_detector.html  # UI template
├── static/
│   └── css/               # (optional) CSS files
└── README.md              # This file
```

## Safety Notes

- This is a **monitoring tool**, not a safety device
- Do not rely solely on this system for critical applications
- Regular visual inspection is still required
- Test alert system regularly
- Keep sensors clean and properly positioned

## Version History

### 1.0.0 (2026-01-18)
- Initial release
- 3-sensor monitoring
- Real-time WebSocket updates
- Browser notifications
- Alert history
- ESP32-S3 and ESP32-C3 support
- mDNS auto-discovery
- OTA updates

## License

This plugin is part of the ChitUI ecosystem.

## Support

For issues or questions:
- Check ESP32 serial output for debugging
- Review ChitUI logs
- Verify network connectivity
- Ensure all dependencies are installed

## Credits

Developed for ChitUI - Web-based control interface for Chitu 3D printers
