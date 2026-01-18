# GPIO Relay Control Plugin

Control 3 GPIO relays from the ChitUI toolbar with real-time status updates.

## Features

- **3 Independent Relay Controls** - Control 3 separate GPIO relays
- **Toolbar Integration** - Buttons positioned on the far right of the toolbar
- **Real-time Updates** - Socket.IO integration for instant state synchronization
- **Custom Names** - Rename each relay for easier identification
- **Visual Feedback** - LED-like indicators show relay state (ON/OFF)
- **Settings Modal** - Configure relay names and view GPIO pin assignments
- **Persistent State** - Relay states are saved and restored on reboot
- **Simulation Mode** - Works without GPIO hardware for testing

## Hardware Setup

### Default GPIO Pin Assignment (BCM Numbering)

- **Relay 1**: GPIO 17
- **Relay 2**: GPIO 27
- **Relay 3**: GPIO 22

### Wiring

For Raspberry Pi Zero W2, connect your relay modules as follows:

```
Raspberry Pi GPIO    →    Relay Module
------------------------------------------
GPIO 17 (Pin 11)     →    Relay 1 IN
GPIO 27 (Pin 13)     →    Relay 2 IN
GPIO 22 (Pin 15)     →    Relay 3 IN
GND     (Pin 6)      →    GND
5V      (Pin 2)      →    VCC (if relay needs 5V)
```

**Important Notes:**
- Most relay modules are **active LOW** - they turn ON when GPIO is LOW
- Use appropriate power supply for your relay modules
- Consider using optocouplers for electrical isolation
- Check your relay module's voltage requirements (3.3V or 5V)

## Installation

The plugin will be automatically discovered when placed in the `plugins/` directory.

### Dependencies

The plugin requires `RPi.GPIO` which will be installed automatically:

```bash
pip install RPi.GPIO
```

## Usage

### Enable the Plugin

1. Go to the ChitUI plugin manager
2. Find "GPIO Relay Control" in the list
3. Click "Enable"
4. The relay control buttons will appear in the top-right corner of the toolbar

### Control Relays

1. **Toggle Relay**: Click any relay button (R1, R2, R3) to toggle its state
2. **Visual Feedback**:
   - **Gray button** = Relay OFF
   - **Green pulsing button** = Relay ON
3. **Settings**: Click the gear icon to open settings

### Configure Relay Names

1. Click the **gear icon** in the relay toolbar
2. Enter custom names for each relay (e.g., "Lights", "Fan", "Heater")
3. Click "Save"
4. Hover over relay buttons to see the custom names

## API Endpoints

The plugin provides REST API endpoints for integration:

### Get All Relay States
```
GET /plugin/gpio_relay_control/status
```

Response:
```json
{
  "relay1": {
    "name": "Relay 1",
    "pin": 17,
    "state": false
  },
  "relay2": {
    "name": "Relay 2",
    "pin": 27,
    "state": false
  },
  "relay3": {
    "name": "Relay 3",
    "pin": 22,
    "state": false
  },
  "gpio_available": true
}
```

### Toggle Relay
```
POST /plugin/gpio_relay_control/relay/<relay_num>/toggle
```

Example:
```bash
curl -X POST http://localhost:5000/plugin/gpio_relay_control/relay/1/toggle
```

### Set Relay State Explicitly
```
POST /plugin/gpio_relay_control/relay/<relay_num>/set
Content-Type: application/json

{
  "state": true
}
```

### Get Configuration
```
GET /plugin/gpio_relay_control/config
```

### Update Configuration
```
POST /plugin/gpio_relay_control/config
Content-Type: application/json

{
  "relay1_name": "My Custom Name",
  "relay2_name": "Another Name",
  "relay3_name": "Third Name"
}
```

## Socket.IO Events

### Client → Server

**Toggle Relay:**
```javascript
socket.emit('gpio_relay_toggle', {
  relay: 1  // Relay number (1, 2, or 3)
});
```

**Request Status:**
```javascript
socket.emit('gpio_relay_request_status');
```

### Server → Client

**State Changed:**
```javascript
socket.on('gpio_relay_state_changed', function(data) {
  console.log(data.relay);  // Relay number
  console.log(data.state);  // true (ON) or false (OFF)
});
```

**Status Update:**
```javascript
socket.on('gpio_relay_status', function(data) {
  console.log(data);  // Full status object
});
```

**Config Updated:**
```javascript
socket.on('gpio_relay_config_updated', function(config) {
  console.log(config);  // Updated configuration
});
```

## Configuration Files

### Plugin Config
Location: `~/.chitui/gpio_relay_config.json`

Contains:
- GPIO pin assignments
- Relay names
- Last known relay states

### Plugin Settings
Location: `~/.chitui/plugin_settings.json`

Contains:
- Plugin enable/disable state

## Troubleshooting

### Buttons Not Appearing

1. Check plugin is enabled in plugin manager
2. Refresh the browser page
3. Check browser console for JavaScript errors

### Relays Not Responding

1. **Check GPIO Permissions:**
   ```bash
   sudo usermod -a -G gpio $USER
   ```
   Log out and log back in

2. **Check RPi.GPIO Installation:**
   ```bash
   python3 -c "import RPi.GPIO; print('GPIO Available')"
   ```

3. **Check Wiring:**
   - Verify GPIO pin connections
   - Check relay module power supply
   - Test with multimeter

### Simulation Mode Warning

If you see "Running in simulation mode":
- RPi.GPIO is not available
- Plugin will work but won't control actual GPIO pins
- Good for testing without hardware

### Permission Denied Errors

Run ChitUI with sudo if GPIO access is denied:
```bash
sudo python3 main.py
```

Or add user to gpio group:
```bash
sudo usermod -a -G gpio $USER
```

## Safety Considerations

⚠️ **Important Safety Notes:**

1. **Electrical Safety**
   - Never work on live circuits
   - Use proper insulation and enclosures
   - Consider using optocouplers for isolation

2. **Relay Ratings**
   - Don't exceed relay voltage/current ratings
   - Use appropriately rated relays for your load
   - Consider inrush current for motors and transformers

3. **GPIO Protection**
   - Use current-limiting resistors if needed
   - Don't connect GPIO directly to high voltage
   - Consider using relay modules with built-in protection

4. **Testing**
   - Test with low-voltage loads first
   - Verify relay activation before connecting critical loads
   - Have a manual override/emergency stop

## Customization

### Change GPIO Pins

Edit the configuration in `~/.chitui/gpio_relay_config.json`:

```json
{
  "relay1_pin": 17,
  "relay2_pin": 27,
  "relay3_pin": 22
}
```

**Note:** Restart ChitUI after changing GPIO pins.

### Add More Relays

To add more than 3 relays, you'll need to modify:
1. `__init__.py` - Add relay4, relay5, etc.
2. `gpio_relay_control.html` - Add more buttons
3. `relay-control.js` - Add handlers for new relays

## License

This plugin is part of the ChitUI project.

## Support

For issues and questions, please refer to the main ChitUI documentation or create an issue in the project repository.

## Version History

### v1.0.0
- Initial release
- 3 relay control
- Toolbar integration
- Real-time Socket.IO updates
- Configuration modal
- Persistent state storage
