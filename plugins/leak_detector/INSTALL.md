# Leak Detector Plugin Installation Guide

## Quick Start

### 1. Install Plugin in ChitUI

**Method A: Via Web Interface (Recommended)**
```bash
# Create plugin zip
cd /path/to/leak_detector/parent/directory
zip -r leak_detector.zip leak_detector/

# Then upload via ChitUI:
# 1. Navigate to Settings → Plugins
# 2. Click "Upload Plugin"
# 3. Select leak_detector.zip
# 4. Enable the plugin
```

**Method B: Manual Installation**
```bash
# Copy plugin to ChitUI plugins directory
cp -r leak_detector /path/to/ChitUI/plugins/

# Restart ChitUI
cd /path/to/ChitUI
sudo systemctl restart chitui
# OR
./run.sh
```

### 2. Register ESP32 API Endpoints

**IMPORTANT:** The plugin requires two main-level API endpoints to be registered in ChitUI's `main.py` or `app.py`.

Add this code to your ChitUI's main application file after initializing the Flask app:

```python
# In main.py or app.py, after app initialization

from plugins.leak_detector import register_esp32_endpoints

# Register ESP32-facing endpoints
register_esp32_endpoints(app)
```

**Alternative:** If ChitUI has automatic plugin endpoint registration, ensure the plugin manager calls `register_esp32_endpoints(app)` when loading the leak_detector plugin.

### 3. Enable the Plugin

1. Restart ChitUI
2. Navigate to Settings → Plugins
3. Find "Leak Detector" in the list
4. Toggle to "Enabled"
5. Refresh the page

### 4. Flash ESP32 Firmware

**Prerequisites:**
- Arduino IDE or PlatformIO
- ESP32-S3 or ESP32-C3 board
- USB cable

**Arduino IDE:**
```
1. Install ESP32 board support:
   - File → Preferences → Additional Board Manager URLs
   - Add: https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json

2. Install boards:
   - Tools → Board → Board Manager
   - Search "ESP32"
   - Install "esp32 by Espressif Systems"

3. Install libraries:
   - WiFiManager
   - (other libraries are built-in)

4. Configure:
   - Tools → Board → ESP32S3 Dev Module (or your board)
   - Tools → Upload Speed → 921600
   - Tools → Flash Size → 4MB

5. Upload:
   - Open esp32_s3_leak_detector.ino
   - Click Upload
```

**PlatformIO (Advanced):**
Create `platformio.ini`:
```ini
[env:esp32-s3]
platform = espressif32
board = esp32-s3-devkitc-1
framework = arduino
lib_deps =
    tzapu/WiFiManager@^2.0.16-rc.2
monitor_speed = 115200
upload_speed = 921600
```

Then:
```bash
pio run --target upload
pio device monitor
```

### 5. Configure ESP32 WiFi

**First Boot:**
1. ESP32 creates WiFi AP: `LeakSensor-Setup`
2. Connect to this network
3. Browser should auto-open to captive portal
   - If not, navigate to `http://192.168.4.1`
4. Select your WiFi network
5. Enter password
6. Click Save

**Device will reboot and connect to your network.**

### 6. Configure ChitUI Connection

**Option 1: Auto-Discovery (Recommended)**
- ESP32 will automatically find ChitUI via mDNS
- No configuration needed
- Look for "ChitUI found" in serial output

**Option 2: Manual Configuration**
1. Find ESP32 IP address:
   - Check serial monitor
   - Check router DHCP leases
   - Look for device named "leak-sensor"

2. Navigate to ESP32 web interface:
   - `http://<ESP32_IP>`

3. Click "Configure"

4. Enter ChitUI details:
   - IP: Your ChitUI server IP (e.g., 192.168.1.100)
   - Port: Usually 5000

5. Click Save

### 7. Verify Installation

**Check ESP32 Serial Output:**
```
=== Resin Leak Detector ===
Chip: ESP32-S3 (using touchRead)
WiFi connected
  IP: 192.168.1.50
Web server started
mDNS responder started
OTA ready
Searching for ChitUI...
  Found 1 ChitUI server(s)
  ChitUI: http://192.168.1.100:5000
Calibrating sensors...
  Sensor 1: 245
  Sensor 2: 238
  Sensor 3: 251
System ready!
```

**Check ChitUI Interface:**
1. Look for droplet icon in toolbar
2. Click icon to open leak detector panel
3. Verify "Online" status shows green
4. Confirm device IP and chip type are displayed
5. Sensor values should show numbers (not "--")

## Dependencies

### ESP32 (Arduino Libraries)
- WiFi (built-in)
- WiFiManager - `tzapu/WiFiManager@^2.0.16-rc.2`
- HTTPClient (built-in)
- Preferences (built-in)
- ESPmDNS (built-in)
- ArduinoOTA (built-in)
- WebServer (built-in)

### ChitUI Plugin
- **No external Python dependencies required**
- Uses Flask (already in ChitUI)
- Uses Flask-SocketIO (already in ChitUI)

## Hardware Setup

### Recommended Components

**ESP32 Development Board:**
- ESP32-S3-DevKitC-1 (recommended - has touch sensors)
- ESP32-C3-DevKitM-1 (alternative - uses analog)
- Any ESP32 board with WiFi

**Sensors:**
For ESP32-S3 (capacitive touch):
- Use conductive tape or copper strips
- Connect to GPIO 4, 5, 6
- No external components needed

For ESP32-C3 (analog):
- Moisture sensors or capacitive sensors with analog output
- Connect to GPIO 4, 5, 6
- May need pull-up/down resistors

**Other Components:**
- LED (any color) → GPIO 8 + resistor (220Ω-1kΩ)
- Push button → GPIO 7 (active low, uses internal pull-up)

### Wiring Diagram

```
ESP32-S3 / ESP32-C3
┌─────────────────┐
│                 │
│  GPIO 4  ────── │ Sensor 1 (Vat Center)
│  GPIO 5  ────── │ Sensor 2 (Front Edge)
│  GPIO 6  ────── │ Sensor 3 (Build Plate)
│                 │
│  GPIO 8  ────── │ LED + Resistor → GND
│  GPIO 7  ────── │ Reset Button → GND
│                 │
│  USB     ────── │ Power / Programming
│  GND     ────── │ Common Ground
│                 │
└─────────────────┘
```

## Troubleshooting

### Plugin Not Appearing in ChitUI
- Verify plugin is in `plugins/` directory
- Check `plugin.json` is valid JSON
- Restart ChitUI completely
- Check ChitUI logs for errors

### API Endpoints Not Working
- Ensure `register_esp32_endpoints(app)` is called in main.py
- Restart ChitUI after adding the registration
- Test with curl:
  ```bash
  curl -X POST http://<chitui-ip>:5000/api/sensor_status \
    -H "Content-Type: application/json" \
    -d '{"status":"online","ip":"test","chip":"test","version":"1.0"}'
  ```

### ESP32 Can't Connect to WiFi
- Hold reset button for 5+ seconds to clear settings
- Try different WiFi network (2.4GHz required)
- Check WiFi credentials are correct
- Reduce distance to router

### ESP32 Can't Find ChitUI
- Ensure both on same network/subnet
- Try manual configuration
- Check ChitUI mDNS is running:
  ```bash
  avahi-browse -rt _chitui._tcp
  ```
- Verify ChitUI is accessible at port 5000

### No Data in ChitUI
- Check ESP32 serial output for HTTP errors
- Verify ESP32 has correct ChitUI URL
- Test ChitUI API endpoints manually
- Check firewall isn't blocking requests

### Touch Sensors Not Working (ESP32-S3)
- Ensure you selected ESP32-S3 board in Arduino IDE
- Touch sensors need direct contact with conductive material
- Try increasing threshold if too sensitive
- Try decreasing threshold if not sensitive enough

### Analog Sensors Not Working (ESP32-C3)
- Ensure sensors output 0-3.3V
- Check sensor power connections
- Verify correct board selected in Arduino IDE
- Check sensor wiring

## Uninstallation

### Remove from ChitUI
1. Settings → Plugins
2. Disable "Leak Detector"
3. Delete plugin folder:
   ```bash
   rm -rf /path/to/ChitUI/plugins/leak_detector/
   ```
4. Remove endpoint registration from main.py
5. Restart ChitUI

### Erase ESP32
```bash
# Using esptool
esptool.py erase_flash

# Then re-flash or use for other project
```

## Support

Check the main README.md for detailed usage and troubleshooting information.
