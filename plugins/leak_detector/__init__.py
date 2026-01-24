"""
Leak Detector Plugin for ChitUI
ESP32-based resin leak detection system with real-time monitoring
"""

from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
import logging
import os
import json
import threading
import time
from plugins.base import ChitUIPlugin

logger = logging.getLogger(__name__)

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
            # Relay configuration - uses GPIO Relay Control plugin
            'relay_enabled': False,  # Enable relay activation on leak detection
            'relay_number': 1        # Which relay from GPIO Relay Control plugin to use (1-4)
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

        # Reference to plugin manager (set during startup)
        self.plugin_manager = None

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
        self.connection_timeout = 360  # 6 minutes (in seconds)
        self.connection_check_interval = 300  # 5 minutes (in seconds)
        self.connection_monitor_thread = None
        self.monitor_running = False

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

    def _get_relay_plugin(self):
        """Get the GPIO Relay Control plugin instance"""
        if self.plugin_manager is None:
            return None
        return self.plugin_manager.get_plugin('gpio_relay_control')

    def _get_relay_plugin_config(self):
        """Get the configuration from GPIO Relay Control plugin"""
        relay_plugin = self._get_relay_plugin()
        if relay_plugin is None:
            return None
        return relay_plugin.config

    def get_available_relays(self):
        """Get list of available relays from GPIO Relay Control plugin"""
        relay_config = self._get_relay_plugin_config()
        if relay_config is None:
            return []

        relays = []
        for i in range(1, 5):
            relay_info = {
                'number': i,
                'name': relay_config.get(f'relay{i}_name', f'Relay {i}'),
                'pin': relay_config.get(f'relay{i}_pin', 0),
                'type': relay_config.get(f'relay{i}_type', 'NO'),
                'enabled': relay_config.get(f'relay{i}_enabled', True),
                'icon': relay_config.get(f'relay{i}_icon', 'fa-bolt'),
                'state': relay_config.get(f'relay{i}_state', False)
            }
            relays.append(relay_info)

        return relays

    def _init_relay_gpio(self):
        """Initialize relay - restore armed state if needed after reboot"""
        if not self.config.get('relay_enabled', False):
            return

        # If relay was armed (persistent state), restore it
        if self.relay_state.get('armed', False):
            relay_num = self.config.get('relay_number', 1)
            relay_plugin = self._get_relay_plugin()

            if relay_plugin:
                relay_plugin.set_relay_state(relay_num, True)
                logger.warning(f"Relay {relay_num} restored to ARMED state (persistent from before reboot)")
            else:
                logger.warning("Relay was armed but GPIO Relay Control plugin not available")

    def _set_relay(self, state):
        """Set the relay state using GPIO Relay Control plugin"""
        if not self.config.get('relay_enabled', False):
            logger.debug("Relay not enabled, skipping")
            return False

        relay_num = self.config.get('relay_number', 1)
        relay_plugin = self._get_relay_plugin()

        if relay_plugin is None:
            logger.error("GPIO Relay Control plugin not available")
            return False

        try:
            success = relay_plugin.set_relay_state(relay_num, state)
            if success:
                relay_config = self._get_relay_plugin_config()
                relay_name = relay_config.get(f'relay{relay_num}_name', f'Relay {relay_num}') if relay_config else f'Relay {relay_num}'
                logger.info(f"Relay '{relay_name}' (#{relay_num}) set to {'ON' if state else 'OFF'}")
            return success
        except Exception as e:
            logger.error(f"Error setting relay state: {e}")
            return False

    def arm_relay(self, reason=None):
        """
        Arm the relay (activate it due to leak detection).
        Persists across reboots until manually disarmed.
        """
        if self.relay_state.get('armed', False):
            logger.info("Relay already armed, skipping")
            return False

        relay_num = self.config.get('relay_number', 1)
        relay_config = self._get_relay_plugin_config()
        relay_name = relay_config.get(f'relay{relay_num}_name', f'Relay {relay_num}') if relay_config else f'Relay {relay_num}'

        # Activate the relay
        if self._set_relay(True):
            self.relay_state['armed'] = True
            self.relay_state['armed_at'] = datetime.now().isoformat()
            self.relay_state['armed_reason'] = reason
            self.save_relay_state()

            # Log the action
            self.add_relay_log_entry('ARMED', {
                'reason': reason,
                'relay_number': relay_num,
                'relay_name': relay_name
            })

            # Emit update to clients
            self._emit_relay_update()

            logger.warning(f"RELAY '{relay_name}' ARMED due to: {reason}")
            return True

        return False

    def disarm_relay(self, user=None):
        """
        Disarm the relay (return to normal state).
        Must be manually triggered by user.
        """
        if not self.relay_state.get('armed', False):
            logger.info("Relay not armed, skipping disarm")
            return False

        relay_num = self.config.get('relay_number', 1)
        relay_config = self._get_relay_plugin_config()
        relay_name = relay_config.get(f'relay{relay_num}_name', f'Relay {relay_num}') if relay_config else f'Relay {relay_num}'

        # Deactivate the relay
        if self._set_relay(False):
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
                'relay_number': relay_num,
                'relay_name': relay_name
            })

            # Emit update to clients
            self._emit_relay_update()

            logger.info(f"RELAY '{relay_name}' DISARMED by: {user or 'user'}")
            return True

        return False

    def _emit_relay_update(self):
        """Emit relay state update to all connected clients"""
        if self.socketio:
            relay_num = self.config.get('relay_number', 1)
            relay_config = self._get_relay_plugin_config()
            relay_name = relay_config.get(f'relay{relay_num}_name', f'Relay {relay_num}') if relay_config else f'Relay {relay_num}'

            self.socketio.emit('leak_detector_relay_update', {
                'relay_state': self.relay_state,
                'relay_enabled': self.config.get('relay_enabled', False),
                'relay_number': relay_num,
                'relay_name': relay_name,
                'timestamp': datetime.now().isoformat()
            })

    def is_relay_plugin_available(self):
        """Check if GPIO relay control plugin is installed and enabled"""
        if self.plugin_manager is None:
            return {'available': False, 'reason': 'Plugin manager not initialized', 'relays': []}

        # Check if gpio_relay_control plugin exists
        discovered = self.plugin_manager.discover_plugins()
        if 'gpio_relay_control' not in discovered:
            return {'available': False, 'reason': 'GPIO Relay Control plugin not installed', 'relays': []}

        # Check if it's enabled
        if not discovered['gpio_relay_control'].get('enabled', False):
            return {'available': False, 'reason': 'GPIO Relay Control plugin is disabled', 'relays': []}

        # Get available relays
        relays = self.get_available_relays()

        # Check if GPIO is available on the system
        if not GPIO_AVAILABLE:
            return {
                'available': True,
                'gpio_available': False,
                'reason': 'GPIO not available (simulation mode)',
                'relays': relays
            }

        return {'available': True, 'gpio_available': True, 'reason': None, 'relays': relays}

    def on_startup(self, app, socketio):
        """Called when plugin is loaded"""
        logger.info("Leak Detector plugin starting up...")

        self.socketio = socketio

        # Get reference to plugin manager from app context
        if hasattr(app, 'plugin_manager'):
            self.plugin_manager = app.plugin_manager

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
                if 'relay_number' in data:
                    relay_num = int(data['relay_number'])
                    # Validate relay number range
                    if relay_num < 1 or relay_num > 4:
                        return jsonify({
                            'success': False,
                            'message': f'Invalid relay number: {relay_num}. Must be between 1 and 4.'
                        }), 400
                    self.config['relay_number'] = relay_num

                # Save configuration
                self.save_config()

                # Re-initialize relay if settings changed
                if any(key in data for key in ['relay_enabled', 'relay_number']):
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

        @blueprint.route('/relay/status', methods=['GET'])
        def get_relay_status():
            """Get relay status and configuration"""
            relay_num = self.config.get('relay_number', 1)
            relay_config = self._get_relay_plugin_config()

            # Get relay details from plugin config
            relay_name = f'Relay {relay_num}'
            relay_pin = 0
            relay_type = 'NO'
            if relay_config:
                relay_name = relay_config.get(f'relay{relay_num}_name', relay_name)
                relay_pin = relay_config.get(f'relay{relay_num}_pin', 0)
                relay_type = relay_config.get(f'relay{relay_num}_type', 'NO')

            return jsonify({
                'relay_state': self.relay_state,
                'relay_enabled': self.config.get('relay_enabled', False),
                'relay_number': relay_num,
                'relay_name': relay_name,
                'relay_pin': relay_pin,
                'relay_type': relay_type,
                'gpio_available': GPIO_AVAILABLE
            })

        @blueprint.route('/relay/available', methods=['GET'])
        def get_available_relays():
            """Get list of available relays from GPIO Relay Control plugin"""
            relays = self.get_available_relays()
            return jsonify({
                'relays': relays,
                'count': len(relays)
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

        @blueprint.route('/relay/plugin_available', methods=['GET'])
        def relay_plugin_available():
            """Check if GPIO relay plugin is available"""
            return jsonify(self.is_relay_plugin_available())

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

        # Register the blueprint
        app.register_blueprint(blueprint)

        # Register ESP32 API endpoints at app level
        self._register_esp32_endpoints(app)

        # Register Socket.IO handlers
        self._register_socket_handlers(socketio)

        # Start connection monitoring thread
        self._start_connection_monitor()

        logger.info("Leak Detector plugin started successfully")

    def on_shutdown(self):
        """Called when plugin is unloaded"""
        logger.info("Leak Detector plugin shutting down...")
        self._stop_connection_monitor()

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

    def _register_esp32_endpoints(self, app):
        """Register ESP32-facing API endpoints at the main app level"""

        @app.route('/api/leak_alert', methods=['POST'])
        def leak_alert():
            """Receive leak alert or all-clear from ESP32"""
            try:
                data = request.get_json()
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

                logger.warning(f"LEAK ALERT: Sensor {sensor_num} ({data.get('location')}) - Value: {data.get('value')}")

                # Activate relay if enabled
                if self.config.get('relay_enabled', False):
                    sensor_name = self.config.get(f'sensor{sensor_num}_name', f'Sensor {sensor_num}')
                    sensor_location = data.get('location') or self.config.get(f'sensor{sensor_num}_location', 'Unknown')
                    reason = f"Leak detected by {sensor_name} at {sensor_location}"
                    self.arm_relay(reason)

                return jsonify({'success': True, 'message': 'Alert received'}), 200

            except Exception as e:
                logger.error(f"Error processing leak notification: {e}")
                return jsonify({'success': False, 'error': str(e)}), 500

        @app.route('/api/sensor_status', methods=['POST'])
        def sensor_status():
            """Receive status update from ESP32"""
            try:
                data = request.get_json()
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

