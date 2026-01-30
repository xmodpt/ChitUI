"""
Leak Detector Plugin for ChitUI
ESP32-based resin leak detection system with real-time monitoring
"""

from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
import os
import json
import sys
import threading
import time
from loguru import logger
from plugins.base import ChitUIPlugin

# HTTP client for polling ESP32 sensor data
try:
    import requests as http_requests
    HTTP_REQUESTS_AVAILABLE = True
except ImportError:
    HTTP_REQUESTS_AVAILABLE = False

# Try to import GPIO for relay control
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    logger.info("RPi.GPIO not available - relay control will run in simulation mode")


class Plugin(ChitUIPlugin):
    """ESP32 Leak Detector Plugin for ChitUI"""

    def __init__(self, plugin_dir):
        super().__init__(plugin_dir)
        self.name = "Leak Detector"
        self.version = "1.0.0"
        self.author = "ChitUI Developer"
        self.description = "ESP32-based resin leak detector with real-time monitoring"

        # Configuration file path
        self.config_file = os.path.join(os.path.expanduser('~'), '.chitui', 'leak_detector_config.json')

        # Relay state file (persistent across reboots)
        self.relay_state_file = os.path.join(os.path.expanduser('~'), '.chitui', 'leak_detector_relay_state.json')

        # Relay action log file
        self.relay_log_file = os.path.join(os.path.expanduser('~'), '.chitui', 'leak_detector_relay_log.json')

        # Default configuration
        self.config = {
            'sensor1_name': 'Sensor 1',
            'sensor1_location': 'Vat Center',
            'sensor1_enabled': True,
            'sensor2_name': 'Sensor 2',
            'sensor2_location': 'Front Edge',
            'sensor2_enabled': True,
            'sensor3_name': 'Sensor 3',
            'sensor3_location': 'Build Plate',
            'sensor3_enabled': True,
            'devices': [],  # List of known ESP32 devices
            # Relay configuration - direct GPIO control
            'relay_enabled': False,  # Enable relay activation on leak detection
            'relay_gpio_pin': 17,    # GPIO pin for relay (BCM numbering)
            'relay_type': 'NO',      # NO (Normally Open) or NC (Normally Closed)
            # Notification settings (requires Chitu Notify plugin)
            'notify_leak_detected': False,   # Send notification when leak is detected
            'notify_leak_reset': False,      # Send notification when leak detection is reset
            'notify_relay_armed': False,     # Send notification when safety relay is armed
            'notify_relay_disarmed': False   # Send notification when safety relay is disarmed
        }

        # Relay state (persistent)
        self.relay_state = {
            'armed': False,           # Is relay currently armed (activated due to leak)
            'armed_at': None,         # When was it armed
            'armed_reason': None,     # Why was it armed (which sensor triggered)
            'last_disarmed_at': None  # When was it last disarmed
        }

        # Relay action log
        self.relay_log = []
        self.max_relay_log = 100  # Keep last 100 relay actions

        # Load saved configuration
        self.load_config()

        # Load persistent relay state
        self.load_relay_state()

        # Load relay log
        self.load_relay_log()

        # Initialize relay GPIO (if enabled and state is armed)
        self._init_relay_gpio()

        # Store sensor data and alerts
        self.sensors = {}
        self.alerts = []
        self.max_alerts = 50  # Keep last 50 alerts
        self.device_status = {
            'online': False,
            'ip': None,
            'chip': None,
            'version': None,
            'last_update': None
        }

        # Connection monitoring
        self.last_communication = None
        self.connection_timeout = 30  # 30 seconds - mark offline if no communication
        self.connection_check_interval = 10  # 10 seconds
        self.connection_monitor_thread = None
        self.monitor_running = False

        # ESP32 heartbeat check
        self.poll_interval = 15  # Check if ESP32 is alive every 15 seconds
        self.poll_thread = None
        self.polling_running = False

        # Socket.IO reference for real-time updates
        self.socketio = None

    def get_name(self):
        return self.name

    def get_version(self):
        return self.version

    def get_description(self):
        return self.description

    def get_author(self):
        return self.author

    def get_ui_integration(self):
        """Return UI integration configuration"""
        return {
            'type': 'toolbar',
            'location': 'top',
            'icon': 'bi-droplet-fill',
            'title': 'Leak Detector',
            'template': 'leak_detector.html'
        }

    def has_settings(self):
        """This plugin has a settings page"""
        return True

    def _get_chitu_notify_plugin(self):
        """Get the Chitu Notify plugin instance if available"""
        try:
            main_module = sys.modules.get('main') or sys.modules.get('__main__')
            if main_module:
                plugin_manager = getattr(main_module, 'plugin_manager', None)
                if plugin_manager:
                    return plugin_manager.get_plugin('chitu_notify')
        except Exception as e:
            logger.debug(f"Error getting chitu_notify plugin: {e}")
        return None

    def _is_notify_available(self):
        """Check if Chitu Notify plugin is available and enabled"""
        return self._get_chitu_notify_plugin() is not None

    def _send_notification(self, alarm_id, extra_message=None):
        """Send notification via Chitu Notify plugin if enabled"""
        # Check if this notification type is enabled in config
        config_key = f'notify_{alarm_id}'
        if not self.config.get(config_key, False):
            return  # Notification not enabled

        notify_plugin = self._get_chitu_notify_plugin()
        if not notify_plugin:
            return  # Chitu Notify not available

        try:
            notify_plugin.send_notification(alarm_id, extra_message)
        except Exception as e:
            logger.error(f"Error sending leak detector notification: {e}")

    def load_config(self):
        """Load configuration from file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    saved_config = json.load(f)
                    self.config.update(saved_config)
                logger.info("Leak detector configuration loaded")
        except Exception as e:
            logger.error(f"Error loading leak detector config: {e}")

    def save_config(self):
        """Save configuration to file"""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info("Leak detector configuration saved")
        except Exception as e:
            logger.error(f"Error saving leak detector config: {e}")

    def load_relay_state(self):
        """Load persistent relay state from file"""
        try:
            if os.path.exists(self.relay_state_file):
                with open(self.relay_state_file, 'r') as f:
                    saved_state = json.load(f)
                    self.relay_state.update(saved_state)
                logger.info(f"Relay state loaded - Armed: {self.relay_state['armed']}")
        except Exception as e:
            logger.error(f"Error loading relay state: {e}")

    def save_relay_state(self):
        """Save relay state to file (persists across reboots)"""
        try:
            os.makedirs(os.path.dirname(self.relay_state_file), exist_ok=True)
            with open(self.relay_state_file, 'w') as f:
                json.dump(self.relay_state, f, indent=2)
            logger.info(f"Relay state saved - Armed: {self.relay_state['armed']}")
        except Exception as e:
            logger.error(f"Error saving relay state: {e}")

    def load_relay_log(self):
        """Load relay action log from file"""
        try:
            if os.path.exists(self.relay_log_file):
                with open(self.relay_log_file, 'r') as f:
                    self.relay_log = json.load(f)
                logger.info(f"Relay log loaded - {len(self.relay_log)} entries")
        except Exception as e:
            logger.error(f"Error loading relay log: {e}")
            self.relay_log = []

    def save_relay_log(self):
        """Save relay action log to file"""
        try:
            os.makedirs(os.path.dirname(self.relay_log_file), exist_ok=True)
            with open(self.relay_log_file, 'w') as f:
                json.dump(self.relay_log, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving relay log: {e}")

    def add_relay_log_entry(self, action, details=None):
        """Add an entry to the relay log"""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'details': details or {}
        }
        self.relay_log.insert(0, entry)  # Most recent first

        # Limit log size
        if len(self.relay_log) > self.max_relay_log:
            self.relay_log = self.relay_log[:self.max_relay_log]

        self.save_relay_log()
        logger.info(f"Relay log: {action} - {details}")

    def _get_relay_gpio_level(self, state):
        """Get the correct GPIO level based on relay type (NO/NC)"""
        relay_type = self.config.get('relay_type', 'NO')

        if relay_type == 'NC':  # Normally Closed - invert logic
            return GPIO.LOW if state else GPIO.HIGH
        else:  # Normally Open (default)
            return GPIO.HIGH if state else GPIO.LOW

    def _init_relay_gpio(self):
        """Initialize relay GPIO pin"""
        if not self.config.get('relay_enabled', False):
            return

        if not GPIO_AVAILABLE:
            logger.info("GPIO not available - relay running in simulation mode")
            return

        try:
            pin = self.config.get('relay_gpio_pin', 17)

            # Set GPIO mode if not already set
            try:
                GPIO.setmode(GPIO.BCM)
            except ValueError:
                # Mode already set, that's fine
                pass

            GPIO.setwarnings(False)
            GPIO.setup(pin, GPIO.OUT)

            # If relay was armed (power cut due to leak), keep it OFF
            # Otherwise, keep relay ON (normal operation - printer has power)
            if self.relay_state.get('armed', False):
                gpio_level = self._get_relay_gpio_level(False)  # OFF = power cut
                GPIO.output(pin, gpio_level)
                logger.warning(f"Relay on GPIO {pin} kept OFF (power cut - leak detected before reboot)")
            else:
                gpio_level = self._get_relay_gpio_level(True)  # ON = normal operation
                GPIO.output(pin, gpio_level)
                logger.info(f"Relay on GPIO {pin} initialized ON (normal operation - printer has power)")

        except Exception as e:
            logger.error(f"Error initializing relay GPIO: {e}")

    def _set_relay(self, state):
        """Set the relay state"""
        logger.warning(f"DEBUG _set_relay: state={state}, relay_enabled={self.config.get('relay_enabled')}")

        if not self.config.get('relay_enabled', False):
            logger.warning("DEBUG _set_relay: Relay not enabled in config, skipping")
            return False

        pin = self.config.get('relay_gpio_pin', 17)
        logger.warning(f"DEBUG _set_relay: Using GPIO pin {pin}")

        if not GPIO_AVAILABLE:
            logger.warning(f"DEBUG _set_relay: Simulation mode - GPIO {pin} set to {'ON' if state else 'OFF'}")
            return True

        try:
            # Ensure GPIO mode is set
            try:
                GPIO.setmode(GPIO.BCM)
                logger.warning("DEBUG _set_relay: GPIO mode set to BCM")
            except ValueError:
                logger.warning("DEBUG _set_relay: GPIO mode already set")

            GPIO.setwarnings(False)

            # Ensure pin is set up as output before writing
            logger.warning(f"DEBUG _set_relay: Setting up GPIO {pin} as OUTPUT")
            GPIO.setup(pin, GPIO.OUT)

            # Set the relay state
            gpio_level = self._get_relay_gpio_level(state)
            logger.warning(f"DEBUG _set_relay: Writing {'HIGH' if gpio_level else 'LOW'} to GPIO {pin}")
            GPIO.output(pin, gpio_level)
            logger.warning(f"DEBUG _set_relay: SUCCESS - Relay on GPIO {pin} set to {'ON' if state else 'OFF'}")
            return True
        except Exception as e:
            logger.error(f"DEBUG _set_relay: ERROR setting relay state on GPIO {pin}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def arm_relay(self, reason=None):
        """
        Arm the relay (CUT POWER due to leak detection).
        Turns the relay OFF to cut power to the printer.
        Persists across reboots until manually disarmed.
        """
        logger.warning(f"DEBUG arm_relay: reason={reason}, relay_enabled={self.config.get('relay_enabled')}, gpio_pin={self.config.get('relay_gpio_pin')}")

        if self.relay_state.get('armed', False):
            logger.warning("DEBUG arm_relay: Already armed (power already cut), skipping")
            return False

        pin = self.config.get('relay_gpio_pin', 17)

        # Turn relay OFF to cut power
        logger.warning(f"DEBUG arm_relay: Attempting to CUT POWER on GPIO {pin}...")
        if self._set_relay(False):  # OFF = cut power
            self.relay_state['armed'] = True
            self.relay_state['armed_at'] = datetime.now().isoformat()
            self.relay_state['armed_reason'] = reason
            self.save_relay_state()

            # Log the action
            self.add_relay_log_entry('ARMED', {
                'reason': reason,
                'gpio_pin': pin
            })

            # Emit update to clients
            self._emit_relay_update()

            # Send push notification if enabled
            self._send_notification('leak_relay_armed', f"Reason: {reason}")

            logger.warning(f"RELAY on GPIO {pin} ARMED - POWER CUT due to: {reason}")
            return True

        return False

    def disarm_relay(self, user=None):
        """
        Disarm the relay (RESTORE POWER - return to normal state).
        Turns the relay back ON to restore power to the printer.
        Must be manually triggered by user.
        """
        if not self.relay_state.get('armed', False):
            logger.info("Relay not armed, skipping disarm")
            return False

        pin = self.config.get('relay_gpio_pin', 17)

        # Turn relay ON to restore power
        if self._set_relay(True):  # ON = restore power
            armed_at = self.relay_state.get('armed_at')
            armed_reason = self.relay_state.get('armed_reason')

            self.relay_state['armed'] = False
            self.relay_state['last_disarmed_at'] = datetime.now().isoformat()
            self.relay_state['armed_at'] = None
            self.relay_state['armed_reason'] = None
            self.save_relay_state()

            # Log the action
            self.add_relay_log_entry('DISARMED', {
                'disarmed_by': user or 'user',
                'was_armed_at': armed_at,
                'was_armed_reason': armed_reason,
                'gpio_pin': pin
            })

            # Emit update to clients
            self._emit_relay_update()

            # Send push notification if enabled
            self._send_notification('leak_relay_disarmed', f"Disarmed by: {user or 'user'}")

            logger.warning(f"RELAY on GPIO {pin} DISARMED - POWER RESTORED by: {user or 'user'}")
            return True

        return False

    def _emit_relay_update(self):
        """Emit relay state update to all connected clients"""
        if self.socketio:
            self.socketio.emit('leak_detector_relay_update', {
                'relay_state': self.relay_state,
                'relay_enabled': self.config.get('relay_enabled', False),
                'relay_gpio_pin': self.config.get('relay_gpio_pin', 17),
                'timestamp': datetime.now().isoformat()
            })

    def on_startup(self, app, socketio):
        """Called when plugin is loaded"""
        logger.info("Leak Detector plugin starting up...")

        self.socketio = socketio

        # Create Flask blueprint for API endpoints
        blueprint = Blueprint('leak_detector', __name__, url_prefix='/plugin/leak_detector')

        @blueprint.route('/status', methods=['GET'])
        def get_status():
            """Get current device status and sensor data"""
            return jsonify({
                'device': self.device_status,
                'sensors': self.sensors,
                'alerts': self.alerts[-10:]  # Last 10 alerts
            })

        @blueprint.route('/alerts', methods=['GET'])
        def get_alerts():
            """Get all alerts history"""
            return jsonify({
                'alerts': self.alerts,
                'count': len(self.alerts)
            })

        @blueprint.route('/clear_alerts', methods=['POST'])
        def clear_alerts():
            """Clear all alerts"""
            self.alerts = []
            self._emit_update()
            return jsonify({'success': True, 'message': 'Alerts cleared'})

        @blueprint.route('/reset_detection', methods=['POST'])
        def reset_detection():
            """Reset detection state (clear sensor alerts but keep history)"""
            try:
                # Clear alert flags on all sensors
                for sensor_id in list(self.sensors.keys()):
                    if sensor_id in self.sensors:
                        self.sensors[sensor_id]['alert'] = False

                # Emit update to all clients
                self._emit_update()

                # Send push notification if enabled
                self._send_notification('leak_reset', 'Leak detection system has been reset. Monitoring resumed.')

                logger.info("Leak detection state reset")
                return jsonify({'success': True, 'message': 'Detection state reset'})
            except Exception as e:
                logger.error(f"Error resetting detection state: {e}")
                return jsonify({'success': False, 'message': str(e)}), 500

        @blueprint.route('/sensors', methods=['GET'])
        def get_sensors():
            """Get current sensor readings"""
            return jsonify(self.sensors)

        @blueprint.route('/config', methods=['GET'])
        def get_config():
            """Get current configuration"""
            return jsonify(self.config)

        @blueprint.route('/config', methods=['POST'])
        def update_config():
            """Update configuration"""
            try:
                data = request.get_json()

                # Update sensor names and locations
                for i in [1, 2, 3]:
                    name_key = f'sensor{i}_name'
                    location_key = f'sensor{i}_location'
                    enabled_key = f'sensor{i}_enabled'

                    if name_key in data:
                        self.config[name_key] = data[name_key]
                    if location_key in data:
                        self.config[location_key] = data[location_key]
                    if enabled_key in data:
                        self.config[enabled_key] = bool(data[enabled_key])

                # Update devices list
                if 'devices' in data:
                    self.config['devices'] = data['devices']

                # Update relay configuration
                if 'relay_enabled' in data:
                    self.config['relay_enabled'] = bool(data['relay_enabled'])
                if 'relay_gpio_pin' in data:
                    pin = int(data['relay_gpio_pin'])
                    # Validate pin range
                    if pin < 2 or pin > 27:
                        return jsonify({
                            'success': False,
                            'message': f'Invalid GPIO pin: {pin}. Must be between 2 and 27.'
                        }), 400
                    self.config['relay_gpio_pin'] = pin
                if 'relay_type' in data:
                    relay_type = data['relay_type'].upper()
                    if relay_type in ['NO', 'NC']:
                        self.config['relay_type'] = relay_type

                # Update notification settings
                for notify_key in ['notify_leak_detected', 'notify_leak_reset', 'notify_relay_armed', 'notify_relay_disarmed']:
                    if notify_key in data:
                        self.config[notify_key] = bool(data[notify_key])

                # Save configuration
                self.save_config()

                # Re-initialize relay if settings changed
                if any(key in data for key in ['relay_enabled', 'relay_gpio_pin', 'relay_type']):
                    self._init_relay_gpio()

                # Emit config update to clients
                if self.socketio:
                    self.socketio.emit('leak_detector_config_updated', self.config)

                return jsonify({
                    'success': True,
                    'config': self.config,
                    'message': 'Configuration updated successfully'
                })

            except Exception as e:
                logger.error(f"Error updating config: {e}")
                return jsonify({
                    'success': False,
                    'message': str(e)
                }), 500

        @blueprint.route('/settings', methods=['GET'])
        def get_settings():
            """Get settings HTML"""
            settings_template = os.path.join(self.get_template_folder(), 'settings.html')
            if os.path.exists(settings_template):
                with open(settings_template, 'r') as f:
                    return f.read()
            return 'Settings template not found', 404

        @blueprint.route('/notify_available', methods=['GET'])
        def notify_available():
            """Check if Chitu Notify plugin is available"""
            return jsonify({
                'available': self._is_notify_available()
            })

        @blueprint.route('/relay/status', methods=['GET'])
        def get_relay_status():
            """Get relay status and configuration"""
            return jsonify({
                'relay_state': self.relay_state,
                'relay_enabled': self.config.get('relay_enabled', False),
                'relay_gpio_pin': self.config.get('relay_gpio_pin', 17),
                'relay_type': self.config.get('relay_type', 'NO'),
                'gpio_available': GPIO_AVAILABLE
            })

        @blueprint.route('/relay/disarm', methods=['POST'])
        def disarm_relay_route():
            """Disarm the relay (return to normal state)"""
            try:
                data = request.get_json() or {}
                user = data.get('user', 'user')

                if self.disarm_relay(user):
                    return jsonify({
                        'success': True,
                        'message': 'Relay disarmed successfully',
                        'relay_state': self.relay_state
                    })
                else:
                    return jsonify({
                        'success': False,
                        'message': 'Relay was not armed'
                    })

            except Exception as e:
                logger.error(f"Error disarming relay: {e}")
                return jsonify({'success': False, 'message': str(e)}), 500

        @blueprint.route('/relay/log', methods=['GET'])
        def get_relay_log():
            """Get relay action log"""
            return jsonify({
                'log': self.relay_log,
                'count': len(self.relay_log)
            })

        @blueprint.route('/relay/log/clear', methods=['POST'])
        def clear_relay_log():
            """Clear relay action log"""
            self.relay_log = []
            self.save_relay_log()
            return jsonify({'success': True, 'message': 'Relay log cleared'})

        @blueprint.route('/debug', methods=['GET'])
        def debug_info():
            """Debug endpoint - shows raw ESP32 data and internal state"""
            esp_ip = self._get_esp32_ip()
            esp32_raw_sensors = None
            esp32_raw_status = None
            esp32_error = None

            if esp_ip and HTTP_REQUESTS_AVAILABLE:
                try:
                    resp = http_requests.get(f"http://{esp_ip}/api/sensors", timeout=3)
                    esp32_raw_sensors = resp.json() if resp.status_code == 200 else f"HTTP {resp.status_code}: {resp.text[:500]}"
                except Exception as e:
                    esp32_error = str(e)

                try:
                    resp = http_requests.get(f"http://{esp_ip}/api/status", timeout=3)
                    esp32_raw_status = resp.json() if resp.status_code == 200 else f"HTTP {resp.status_code}"
                except Exception:
                    pass

            return jsonify({
                'esp32_ip': esp_ip,
                'esp32_raw_sensors': esp32_raw_sensors,
                'esp32_raw_status': esp32_raw_status,
                'esp32_fetch_error': esp32_error,
                'internal_device_status': self.device_status,
                'internal_sensors': self.sensors,
                'internal_alerts_count': len(self.alerts),
                'last_communication': self.last_communication.isoformat() if self.last_communication else None,
                'polling_running': self.polling_running,
                'http_requests_available': HTTP_REQUESTS_AVAILABLE
            })

        # Register the blueprint
        app.register_blueprint(blueprint)

        # Register ESP32 API endpoints at app level
        self._register_esp32_endpoints(app)

        # Register Socket.IO handlers
        self._register_socket_handlers(socketio)

        # Start connection monitoring thread
        self._start_connection_monitor()

        # Start ESP32 sensor polling thread
        self._start_polling()

        logger.info("Leak Detector plugin started successfully")

    def on_shutdown(self):
        """Called when plugin is unloaded"""
        logger.info("Leak Detector plugin shutting down...")
        self._stop_connection_monitor()
        self._stop_polling()

    def _register_socket_handlers(self, socketio):
        """Register WebSocket event handlers"""

        @socketio.on('subscribe_leak_detector')
        def handle_subscribe():
            """Client subscribes to leak detector updates"""
            logger.debug("Client subscribed to leak detector")
            socketio.emit('leak_detector_data', {
                'device': self.device_status,
                'sensors': self.sensors,
                'alerts': self.alerts[-10:]
            })

    def _emit_update(self):
        """Emit real-time update to all connected clients"""
        if self.socketio:
            self.socketio.emit('leak_detector_update', {
                'device': self.device_status,
                'sensors': self.sensors,
                'alerts': self.alerts[-10:],
                'timestamp': datetime.now().isoformat()
            })

    def _emit_alert(self, alert):
        """Emit urgent leak alert notification"""
        if self.socketio:
            self.socketio.emit('leak_detector_alert', alert)

    def _update_last_communication(self):
        """Update the last communication timestamp"""
        self.last_communication = datetime.now()
        logger.debug(f"Last communication updated: {self.last_communication}")

    def _check_connection_status(self):
        """
        Check if device is still online based on last communication.

        IMPORTANT: This only updates device_status['online'] flag.
        Sensor alert states (self.sensors[X]['alert']) are NOT cleared when device goes offline.
        Red alert states persist until either:
        - ESP32 sends an all-clear message
        - User manually resets detection via reset button
        """
        if self.last_communication is None:
            # Never received communication
            if self.device_status['online']:
                self.device_status['online'] = False
                logger.info("Device marked as offline (never received communication)")
                self._emit_update()
            return

        time_since_last = datetime.now() - self.last_communication

        if time_since_last.total_seconds() > self.connection_timeout:
            if self.device_status['online']:
                self.device_status['online'] = False
                # NOTE: Sensor alerts remain active - only device status changes
                logger.warning(f"Device marked as offline (no communication for {time_since_last.total_seconds():.0f} seconds)")
                self._emit_update()

    def _connection_monitor_loop(self):
        """Background thread to monitor ESP32 connection"""
        logger.info(f"Connection monitor started (checking every {self.connection_check_interval} seconds)")

        while self.monitor_running:
            try:
                time.sleep(self.connection_check_interval)
                if self.monitor_running:  # Check again after sleep
                    self._check_connection_status()
            except Exception as e:
                logger.error(f"Error in connection monitor: {e}")

    def _start_connection_monitor(self):
        """Start the connection monitoring thread"""
        if not self.monitor_running:
            self.monitor_running = True
            self.connection_monitor_thread = threading.Thread(
                target=self._connection_monitor_loop,
                daemon=True,
                name="LeakDetectorConnectionMonitor"
            )
            self.connection_monitor_thread.start()
            logger.info("Connection monitor thread started")

    def _stop_connection_monitor(self):
        """Stop the connection monitoring thread"""
        if self.monitor_running:
            self.monitor_running = False
            if self.connection_monitor_thread:
                self.connection_monitor_thread.join(timeout=2)
            logger.info("Connection monitor thread stopped")

    # ========== ESP32 Heartbeat Check ==========

    def _get_esp32_ip(self):
        """Get the ESP32 device IP from device status or known devices list"""
        # First try current device status
        ip = self.device_status.get('ip')
        if ip:
            return ip

        # Fall back to known devices list
        devices = self.config.get('devices', [])
        for dev in devices:
            dev_ip = dev.get('ip')
            if dev_ip:
                return dev_ip

        return None

    def _extract_device_info(self, sensor_data):
        """Extract device metadata from ESP32 /api/sensors response"""
        if not isinstance(sensor_data, dict):
            return
        # Store ESP32 configuration metadata
        if sensor_data.get('calibrated') is not None:
            self.device_status['calibrated'] = sensor_data.get('calibrated')
        if sensor_data.get('confirmationsRequired') is not None:
            self.device_status['confirmations_required'] = sensor_data.get('confirmationsRequired')
        if sensor_data.get('thresholdSensitivity') is not None:
            self.device_status['threshold_sensitivity'] = sensor_data.get('thresholdSensitivity')
        # Mark chip as ESP32 (we know this from the firmware)
        if not self.device_status.get('chip'):
            self.device_status['chip'] = 'ESP32'
        logger.info(f"ESP32 device info: calibrated={sensor_data.get('calibrated')}, "
                     f"confirmations={sensor_data.get('confirmationsRequired')}, "
                     f"sensitivity={sensor_data.get('thresholdSensitivity')}")

    def _heartbeat_check(self):
        """
        Check if ESP32 is alive AND read sensor data to detect alerts.

        Since ESP32 -> ChitUI push may be blocked by firewall (connection refused),
        ChitUI proactively pulls sensor state from ESP32 during each heartbeat.
        This ensures alerts are detected even when the ESP32 can't push to ChitUI.
        """
        if not HTTP_REQUESTS_AVAILABLE:
            return

        esp_ip = self._get_esp32_ip()
        if not esp_ip:
            return

        try:
            url = f"http://{esp_ip}/api/sensors"
            resp = http_requests.get(url, timeout=3)

            if resp.status_code == 200:
                # Update last communication (keeps device marked online)
                self._update_last_communication()

                was_offline = not self.device_status.get('online')

                if was_offline:
                    self.device_status['online'] = True
                    self.device_status['ip'] = esp_ip
                    self.device_status['last_update'] = datetime.now().isoformat()
                    logger.info(f"ESP32 at {esp_ip} is online")

                # Parse sensor data and check for alerts
                try:
                    sensor_data = resp.json()
                    logger.info(f"ESP32 sensor response: {sensor_data}")

                    # Extract device info on first connect
                    if was_offline:
                        self._extract_device_info(sensor_data)

                    self._process_esp32_sensor_data(sensor_data, esp_ip)
                except (ValueError, KeyError) as e:
                    logger.warning(f"Could not parse ESP32 sensor response: {e}")

                self._emit_update()

        except (http_requests.exceptions.ConnectionError,
                http_requests.exceptions.Timeout):
            # Device not reachable - connection monitor will handle marking offline
            pass
        except Exception as e:
            logger.debug(f"ESP32 heartbeat error: {e}")

    def _process_esp32_sensor_data(self, data, esp_ip):
        """
        Process sensor data pulled from ESP32 /api/sensors endpoint.
        Detects new alerts and all-clear transitions.

        ESP32 response format:
        {
            "calibrated": true,
            "confirmationsRequired": 3,
            "thresholdSensitivity": 50,
            "sensor1": {"confirmed": true, "count": 3, "enabled": true, "leak": true, "value": 184482},
            "sensor2": {"confirmed": false, "count": 0, "enabled": false, "leak": false, "value": 12178},
            "sensor3": {"confirmed": false, "count": 0, "enabled": false, "leak": false, "value": 12402}
        }
        """
        if not isinstance(data, dict):
            logger.warning(f"ESP32 sensor data is not a dict: {type(data).__name__}")
            return

        # Extract sensor objects from keys like "sensor1", "sensor2", "sensor3"
        for key, sensor_info in data.items():
            if not key.startswith('sensor') or not isinstance(sensor_info, dict):
                continue

            # Extract sensor number from key (e.g., "sensor1" -> 1)
            try:
                sensor_num = int(key.replace('sensor', ''))
            except ValueError:
                continue

            # Skip if sensor is disabled on the ESP32 side
            if not sensor_info.get('enabled', True):
                continue

            # Check if sensor is enabled in ChitUI config
            sensor_enabled_key = f'sensor{sensor_num}_enabled'
            if not self.config.get(sensor_enabled_key, True):
                continue

            sensor_id = f"sensor{sensor_num}"
            # ESP32 uses "leak" for alert state, "confirmed" for confirmed leak
            is_leak = sensor_info.get('leak', False)
            is_confirmed = sensor_info.get('confirmed', False)
            value = sensor_info.get('value')
            count = sensor_info.get('count', 0)

            # Get previous alert state for this sensor
            prev_alert = self.sensors.get(sensor_id, {}).get('alert', False)

            if (is_leak and is_confirmed) and not prev_alert:
                # NEW CONFIRMED LEAK - sensor was clear, now alerting
                sensor_name = self.config.get(f'sensor{sensor_num}_name', f'Sensor {sensor_num}')
                sensor_location = self.config.get(f'sensor{sensor_num}_location', 'Unknown')
                logger.warning(f"HEARTBEAT ALERT: {sensor_name} confirmed leak detected - Value: {value}, Count: {count}")

                alert = {
                    'sensor': sensor_num,
                    'location': sensor_location,
                    'value': value,
                    'count': count,
                    'device_ip': esp_ip,
                    'received_at': datetime.now().isoformat(),
                    'alert': True,
                    'confirmed': True,
                    'source': 'heartbeat_poll'
                }

                # Add to alerts list
                self.alerts.insert(0, alert)
                if len(self.alerts) > self.max_alerts:
                    self.alerts = self.alerts[:self.max_alerts]

                # Update sensor state
                self.sensors[sensor_id] = {
                    'value': value,
                    'location': sensor_location,
                    'alert': True,
                    'confirmed': True,
                    'count': count,
                    'leak': True,
                    'last_update': datetime.now().isoformat()
                }

                # Emit urgent alert
                self._emit_alert(alert)

                # Send push notification
                self._send_notification('leak_detected', f"{sensor_name} at {sensor_location} - Value: {value}")

                # Activate relay if enabled
                if self.config.get('relay_enabled', False):
                    reason = f"Leak detected by {sensor_name} at {sensor_location}"
                    self.arm_relay(reason)

            elif is_leak and not is_confirmed and not prev_alert:
                # Leak detected but not yet confirmed (count < threshold)
                # Update sensor with intermediate state
                self.sensors[sensor_id] = {
                    'value': value,
                    'location': self.config.get(f'sensor{sensor_num}_location', 'Unknown'),
                    'alert': False,
                    'confirmed': False,
                    'count': count,
                    'leak': True,
                    'last_update': datetime.now().isoformat()
                }

            elif not is_leak and prev_alert:
                # ALL CLEAR - sensor was alerting, now clear
                logger.info(f"HEARTBEAT ALL CLEAR: Sensor {sensor_num} returned to normal via polling")

                self.sensors[sensor_id] = {
                    'value': value,
                    'location': self.config.get(f'sensor{sensor_num}_location', 'Unknown'),
                    'alert': False,
                    'confirmed': False,
                    'count': 0,
                    'leak': False,
                    'last_update': datetime.now().isoformat()
                }

            else:
                # Update sensor value (no state change)
                if sensor_id not in self.sensors:
                    self.sensors[sensor_id] = {
                        'alert': False,
                        'confirmed': False,
                        'leak': False,
                        'count': 0,
                        'location': self.config.get(f'sensor{sensor_num}_location', 'Unknown'),
                    }
                self.sensors[sensor_id]['value'] = value
                self.sensors[sensor_id]['count'] = count
                self.sensors[sensor_id]['leak'] = is_leak
                self.sensors[sensor_id]['last_update'] = datetime.now().isoformat()

    def _has_active_alert(self):
        """Check if any sensor currently has an active alert"""
        return any(s.get('alert', False) for s in self.sensors.values())

    def _heartbeat_loop(self):
        """Background thread that checks if ESP32 is alive periodically"""
        logger.info(f"ESP32 heartbeat check started (every {self.poll_interval}s)")

        while self.polling_running:
            try:
                self._heartbeat_check()
                # Poll faster (5s) during active alerts for quicker all-clear detection
                interval = 5 if self._has_active_alert() else self.poll_interval
                time.sleep(interval)
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}")
                time.sleep(self.poll_interval)

    def _start_polling(self):
        """Start the ESP32 heartbeat check thread"""
        if not self.polling_running and HTTP_REQUESTS_AVAILABLE:
            self.polling_running = True
            self.poll_thread = threading.Thread(
                target=self._heartbeat_loop,
                daemon=True,
                name="LeakDetectorESP32Heartbeat"
            )
            self.poll_thread.start()
            logger.info("ESP32 heartbeat check thread started")

    def _stop_polling(self):
        """Stop the ESP32 polling thread"""
        if self.polling_running:
            self.polling_running = False
            if self.poll_thread:
                self.poll_thread.join(timeout=2)
            logger.info("ESP32 sensor polling thread stopped")

    def _register_esp32_endpoints(self, app):
        """Register ESP32-facing API endpoints at the main app level"""

        @app.route('/api/leak_alert', methods=['POST'])
        def leak_alert():
            """Receive leak alert or all-clear from ESP32"""
            try:
                # Use force=True to parse JSON regardless of Content-Type header
                # ESP32 Arduino HTTP client may not set Content-Type: application/json
                data = request.get_json(force=True, silent=True)

                if data is None:
                    # Try to parse raw body as JSON fallback
                    raw_body = request.get_data(as_text=True)
                    logger.warning(f"leak_alert: get_json returned None. Content-Type: {request.content_type}, Raw body: {raw_body}")
                    try:
                        data = json.loads(raw_body) if raw_body else None
                    except (json.JSONDecodeError, ValueError):
                        pass

                if data is None:
                    logger.error("leak_alert: Could not parse request body as JSON")
                    return jsonify({'success': False, 'error': 'Invalid JSON body'}), 400

                logger.info(f"Received leak notification: {data}")

                # Update last communication timestamp
                self._update_last_communication()

                sensor_num = data.get('sensor')
                is_alert = data.get('alert', True)
                is_all_clear = data.get('all_clear', False)

                # Check if sensor is enabled in config
                sensor_enabled_key = f'sensor{sensor_num}_enabled'
                if not self.config.get(sensor_enabled_key, True):
                    logger.info(f"Ignoring notification from disabled sensor {sensor_num}")
                    return jsonify({'success': True, 'message': 'Sensor disabled, notification ignored'}), 200

                sensor_id = f"sensor{sensor_num}"

                # Handle ALL CLEAR message
                if is_all_clear or not is_alert:
                    logger.info(f"ALL CLEAR: Sensor {sensor_num} ({data.get('location')}) returned to normal - Value: {data.get('value')}")

                    # Update sensor state to clear alert
                    if sensor_id in self.sensors:
                        self.sensors[sensor_id]['alert'] = False
                        self.sensors[sensor_id]['value'] = data.get('value')
                        self.sensors[sensor_id]['last_update'] = datetime.now().isoformat()
                    else:
                        self.sensors[sensor_id] = {
                            'value': data.get('value'),
                            'location': data.get('location'),
                            'alert': False,
                            'last_update': datetime.now().isoformat()
                        }

                    # Emit update to clear UI
                    self._emit_update()

                    return jsonify({'success': True, 'message': 'All clear received'}), 200

                # Handle LEAK ALERT message
                # Create alert record
                alert = {
                    'sensor': sensor_num,
                    'location': data.get('location'),
                    'value': data.get('value'),
                    'threshold': data.get('threshold'),
                    'timestamp': data.get('timestamp'),
                    'device_ip': data.get('device_ip'),
                    'received_at': datetime.now().isoformat(),
                    'alert': True
                }

                # Add confirmation data if present
                if 'confirmed' in data:
                    alert['confirmed'] = data.get('confirmed')
                if 'confirmations' in data:
                    alert['confirmations'] = data.get('confirmations')

                # Add to alerts list
                self.alerts.insert(0, alert)  # Most recent first

                # Limit alerts history
                if len(self.alerts) > self.max_alerts:
                    self.alerts = self.alerts[:self.max_alerts]

                # Update sensor data
                self.sensors[sensor_id] = {
                    'value': data.get('value'),
                    'location': data.get('location'),
                    'alert': True,
                    'last_update': datetime.now().isoformat()
                }

                # Emit real-time updates
                self._emit_alert(alert)
                self._emit_update()

                # Send push notification if enabled
                sensor_name = self.config.get(f'sensor{sensor_num}_name', f'Sensor {sensor_num}')
                sensor_location = data.get('location') or self.config.get(f'sensor{sensor_num}_location', 'Unknown')
                self._send_notification('leak_detected', f"{sensor_name} at {sensor_location} - Value: {data.get('value')}")

                logger.warning(f"LEAK ALERT: Sensor {sensor_num} ({data.get('location')}) - Value: {data.get('value')}")

                # Activate relay if enabled
                relay_enabled = self.config.get('relay_enabled', False)
                logger.warning(f"DEBUG: Checking relay - enabled: {relay_enabled}, gpio_pin: {self.config.get('relay_gpio_pin')}")

                if relay_enabled:
                    sensor_name = self.config.get(f'sensor{sensor_num}_name', f'Sensor {sensor_num}')
                    sensor_location = data.get('location') or self.config.get(f'sensor{sensor_num}_location', 'Unknown')
                    reason = f"Leak detected by {sensor_name} at {sensor_location}"
                    logger.warning(f"DEBUG: Calling arm_relay with reason: {reason}")
                    result = self.arm_relay(reason)
                    logger.warning(f"DEBUG: arm_relay returned: {result}")
                else:
                    logger.warning("DEBUG: Relay activation skipped - relay_enabled is False")

                return jsonify({'success': True, 'message': 'Alert received'}), 200

            except Exception as e:
                logger.error(f"Error processing leak notification: {e}")
                return jsonify({'success': False, 'error': str(e)}), 500

        @app.route('/api/sensor_status', methods=['POST'])
        def sensor_status():
            """Receive status update from ESP32"""
            try:
                # Use force=True to parse JSON regardless of Content-Type header
                data = request.get_json(force=True, silent=True)

                if data is None:
                    raw_body = request.get_data(as_text=True)
                    logger.warning(f"sensor_status: get_json returned None. Content-Type: {request.content_type}, Raw body: {raw_body}")
                    try:
                        data = json.loads(raw_body) if raw_body else None
                    except (json.JSONDecodeError, ValueError):
                        pass

                if data is None:
                    logger.error("sensor_status: Could not parse request body as JSON")
                    return jsonify({'success': False, 'error': 'Invalid JSON body'}), 400

                logger.info(f"Received sensor status: {data}")

                # Update last communication timestamp
                self._update_last_communication()

                # Update device status
                self.device_status = {
                    'online': data.get('status') == 'online',
                    'ip': data.get('ip'),
                    'chip': data.get('chip'),
                    'version': data.get('version'),
                    'last_update': datetime.now().isoformat()
                }

                # Add/update device in known devices list
                device_ip = data.get('ip')
                if device_ip:
                    # Check if device exists in config
                    existing_device = None
                    for dev in self.config.get('devices', []):
                        if dev.get('ip') == device_ip:
                            existing_device = dev
                            break

                    if existing_device:
                        # Update existing device
                        existing_device.update(self.device_status)
                    else:
                        # Add new device
                        if 'devices' not in self.config:
                            self.config['devices'] = []
                        self.config['devices'].append(self.device_status.copy())
                        self.save_config()

                # Emit update
                self._emit_update()

                logger.info(f"Status update from {data.get('ip')}: {data.get('status')}")

                return jsonify({'success': True, 'message': 'Status received'}), 200

            except Exception as e:
                logger.error(f"Error processing sensor status: {e}")
                return jsonify({'success': False, 'error': str(e)}), 500

        @app.route('/api/leak_alert', methods=['GET'])
        def leak_alert_test():
            """Test endpoint - verify leak_alert route is reachable (GET for browser testing)"""
            return jsonify({
                'success': True,
                'message': 'Leak alert endpoint is reachable. Use POST to send alerts.',
                'device_online': self.device_status.get('online', False),
                'active_sensors': len(self.sensors),
                'active_alerts': sum(1 for s in self.sensors.values() if s.get('alert', False))
            })

