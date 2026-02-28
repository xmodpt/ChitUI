# ChitUI Integration Guide for Leak Detector Plugin

This document is for ChitUI maintainers who want to integrate the Leak Detector plugin into the main ChitUI application.

## Overview

The Leak Detector plugin requires special integration because it exposes two API endpoints that the ESP32 device calls directly. These endpoints must be registered at the main Flask app level, not within the plugin's blueprint.

## Required Changes to ChitUI

### Option 1: Modify Plugin Manager (Recommended)

If you want automatic endpoint registration for all plugins that need it, modify the `PluginManager` class:

**File: `plugins/manager.py`**

```python
class PluginManager:
    def load_plugin(self, plugin_name):
        """Load and initialize a plugin"""
        try:
            # ... existing code ...

            # Call on_startup
            plugin_instance.on_startup(self.app, self.socketio)

            # NEW: Check if plugin has external endpoint registration
            module = sys.modules.get(f'plugins.{plugin_name}')
            if hasattr(module, 'register_esp32_endpoints'):
                logger.info(f"Registering ESP32 endpoints for {plugin_name}")
                module.register_esp32_endpoints(self.app)

            # ... rest of existing code ...

        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_name}: {e}")
```

This allows the plugin's `register_esp32_endpoints()` function to be called automatically during plugin loading.

### Option 2: Manual Registration in main.py

Add this code to your main application file after Flask app initialization:

**File: `main.py` or `app.py`**

```python
# After creating Flask app and before starting server

# Import and register leak detector endpoints
try:
    from plugins.leak_detector import register_esp32_endpoints
    register_esp32_endpoints(app)
    logger.info("Leak Detector ESP32 endpoints registered")
except ImportError:
    logger.debug("Leak Detector plugin not installed")
except Exception as e:
    logger.error(f"Failed to register Leak Detector endpoints: {e}")
```

### Option 3: Hook System (If ChitUI has plugin hooks)

If ChitUI supports a plugin hook system, add a hook for external endpoint registration:

```python
# In plugin loading code
hooks = {
    'on_startup': plugin_instance.on_startup,
    'on_register_endpoints': getattr(module, 'register_esp32_endpoints', None)
}

# Call the hook if it exists
if hooks['on_register_endpoints']:
    hooks['on_register_endpoints'](app)
```

## API Endpoints Required

The plugin needs these two endpoints at the main app level:

### 1. POST /api/leak_alert

Receives leak detection alerts from ESP32.

**Request Body:**
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

**Response:**
```json
{
  "success": true,
  "message": "Alert received"
}
```

### 2. POST /api/sensor_status

Receives status updates from ESP32.

**Request Body:**
```json
{
  "status": "online",
  "ip": "192.168.1.100",
  "chip": "ESP32-S3",
  "version": "1.0.0"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Status received"
}
```

## Why These Endpoints Need Special Treatment

1. **External Device Communication**: The ESP32 device needs stable, predictable URLs that won't change
2. **Namespace Consistency**: Using `/api/*` follows ChitUI's existing API pattern
3. **Plugin Independence**: The plugin blueprint uses `/plugin/leak_detector/*` for internal routes
4. **Cross-Origin Support**: Main app level allows easier CORS configuration if needed

## mDNS Service Registration

For automatic ESP32 discovery, ensure ChitUI registers its mDNS service:

**File: `main.py` or appropriate location**

```python
from zeroconf import ServiceInfo, Zeroconf
import socket

def register_mdns():
    """Register ChitUI mDNS service for auto-discovery"""
    try:
        zeroconf = Zeroconf()

        # Get local IP
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)

        # Register ChitUI service
        service_info = ServiceInfo(
            "_chitui._tcp.local.",
            f"ChitUI._chitui._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=5000,
            properties={
                'version': '1.0',
                'name': 'ChitUI'
            },
            server=f"{hostname}.local."
        )

        zeroconf.register_service(service_info)
        logger.info(f"mDNS service registered: {local_ip}:5000")

        return zeroconf

    except Exception as e:
        logger.error(f"Failed to register mDNS service: {e}")
        return None

# In main startup code
zeroconf_instance = register_mdns()

# In shutdown code
if zeroconf_instance:
    zeroconf_instance.close()
```

**Dependencies:**
```bash
pip install zeroconf
```

## WebSocket Events

The plugin uses these Socket.IO events:

**Client → Server:**
- `subscribe_leak_detector` - Client subscribes to updates

**Server → Client:**
- `leak_detector_data` - Initial data payload
- `leak_detector_update` - Periodic updates
- `leak_detector_alert` - Critical alert notification

These are handled internally by the plugin, no main app changes needed.

## UI Integration

The plugin automatically integrates into the ChitUI toolbar via the plugin system:

```json
{
  "ui_integration": {
    "type": "toolbar",
    "location": "top",
    "icon": "bi-droplet-fill",
    "title": "Leak Detector",
    "template": "leak_detector.html"
  }
}
```

Ensure your plugin manager supports `toolbar` type UI integrations.

## Testing the Integration

### 1. Test API Endpoints

```bash
# Test status endpoint
curl -X POST http://localhost:5000/api/sensor_status \
  -H "Content-Type: application/json" \
  -d '{
    "status": "online",
    "ip": "192.168.1.50",
    "chip": "ESP32-S3",
    "version": "1.0.0"
  }'

# Expected: {"success": true, "message": "Status received"}

# Test alert endpoint
curl -X POST http://localhost:5000/api/leak_alert \
  -H "Content-Type: application/json" \
  -d '{
    "sensor": 1,
    "location": "vat_center",
    "value": 450,
    "threshold": 150,
    "timestamp": "00:15:23",
    "device_ip": "192.168.1.50",
    "alert": true
  }'

# Expected: {"success": true, "message": "Alert received"}
```

### 2. Test Plugin Endpoints

```bash
# Test status retrieval
curl http://localhost:5000/plugin/leak_detector/status

# Expected: JSON with device, sensors, and alerts

# Test alert clearing
curl -X POST http://localhost:5000/plugin/leak_detector/clear_alerts

# Expected: {"success": true, "message": "Alerts cleared"}
```

### 3. Test WebSocket Events

Open browser console on ChitUI page:

```javascript
// Should see these events when leak detector is active
socket.on('leak_detector_data', data => console.log('Initial data:', data));
socket.on('leak_detector_update', data => console.log('Update:', data));
socket.on('leak_detector_alert', alert => console.log('ALERT:', alert));

// Subscribe
socket.emit('subscribe_leak_detector');
```

### 4. Test mDNS Discovery

```bash
# On the network, test mDNS
avahi-browse -rt _chitui._tcp

# Should show ChitUI service with IP and port
```

## Security Considerations

### 1. Authentication (Recommended)

Add authentication to ESP32 endpoints if running on untrusted network:

```python
from functools import wraps
from flask import request, jsonify

# Simple token-based auth
LEAK_DETECTOR_TOKEN = "your-secure-token-here"

def require_esp32_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-ESP32-Token')
        if token != LEAK_DETECTOR_TOKEN:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/api/leak_alert', methods=['POST'])
@require_esp32_auth
def leak_alert():
    # ... existing code ...
```

Then update ESP32 code to send token:
```cpp
http.addHeader("X-ESP32-Token", "your-secure-token-here");
```

### 2. Rate Limiting

Prevent spam/DoS attacks:

```python
from flask_limiter import Limiter

limiter = Limiter(app, key_func=get_remote_address)

@app.route('/api/leak_alert', methods=['POST'])
@limiter.limit("10 per minute")  # Max 10 alerts per minute
def leak_alert():
    # ... existing code ...
```

### 3. Input Validation

Already implemented in the plugin, but ensure validation:
- Sensor ID is 1-3
- Values are numeric
- Strings are sanitized
- JSON is valid

## Configuration Options

Consider adding these to ChitUI settings:

```json
{
  "leak_detector": {
    "enabled": true,
    "max_alerts": 50,
    "alert_retention_hours": 24,
    "notification_sound": true,
    "notification_browser": true
  }
}
```

## Logging

The plugin logs to ChitUI's main logger:

```
INFO: Leak Detector plugin starting up...
INFO: Leak Detector plugin started successfully
WARNING: LEAK ALERT: Sensor 1 (vat_center) - Value: 450
INFO: Status update from 192.168.1.50: online
```

## Performance Considerations

- Plugin is lightweight, minimal CPU/memory usage
- WebSocket broadcasts only on events (not polling)
- Alert history capped at 50 items
- No database required (in-memory storage)
- No blocking I/O operations

## Compatibility

- **ChitUI Version**: Any version with plugin system support
- **Python**: 3.7+
- **Flask**: Any recent version
- **Flask-SocketIO**: Required for real-time updates
- **Bootstrap**: 5.x for UI (assumes ChitUI uses Bootstrap)

## Future Enhancements

Consider these for future versions:

1. **Database Storage**: Persist alerts to database
2. **Email/SMS Notifications**: Send alerts via external services
3. **Multi-Device Support**: Support multiple ESP32 detectors
4. **Alert Rules**: Configure custom thresholds per sensor
5. **Historical Graphs**: Chart sensor values over time
6. **Integration with Printer**: Auto-pause print on leak detection

## Support

For integration issues:
1. Check ChitUI logs for errors
2. Verify endpoint registration succeeded
3. Test endpoints with curl
4. Check WebSocket connection in browser console
5. Review plugin loading in plugin manager logs
