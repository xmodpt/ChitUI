"""
Leak Detector Plugin for ChitUI
ESP32-based resin leak detection system with real-time monitoring
"""

from flask import Blueprint, jsonify, request
from datetime import datetime
import logging
import os
import json
from plugins.base import ChitUIPlugin

logger = logging.getLogger(__name__)


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
            'devices': []  # List of known ESP32 devices
        }

        # Load saved configuration
        self.load_config()

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

                # Save configuration
                self.save_config()

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

        # Register the blueprint
        app.register_blueprint(blueprint)

        # Register ESP32 API endpoints at app level
        self._register_esp32_endpoints(app)

        # Register Socket.IO handlers
        self._register_socket_handlers(socketio)

        logger.info("Leak Detector plugin started successfully")

    def on_shutdown(self):
        """Called when plugin is unloaded"""
        logger.info("Leak Detector plugin shutting down...")

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

    def _register_esp32_endpoints(self, app):
        """Register ESP32-facing API endpoints at the main app level"""

        @app.route('/api/leak_alert', methods=['POST'])
        def leak_alert():
            """Receive leak alert from ESP32"""
            try:
                data = request.get_json()
                logger.info(f"Received leak alert: {data}")

                sensor_num = data.get('sensor')

                # Check if sensor is enabled in config
                sensor_enabled_key = f'sensor{sensor_num}_enabled'
                if not self.config.get(sensor_enabled_key, True):
                    logger.info(f"Ignoring alert from disabled sensor {sensor_num}")
                    return jsonify({'success': True, 'message': 'Sensor disabled, alert ignored'}), 200

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

                # Add to alerts list
                self.alerts.insert(0, alert)  # Most recent first

                # Limit alerts history
                if len(self.alerts) > self.max_alerts:
                    self.alerts = self.alerts[:self.max_alerts]

                # Update sensor data only if enabled
                sensor_id = f"sensor{sensor_num}"
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

                return jsonify({'success': True, 'message': 'Alert received'}), 200

            except Exception as e:
                logger.error(f"Error processing leak alert: {e}")
                return jsonify({'success': False, 'error': str(e)}), 500

        @app.route('/api/sensor_status', methods=['POST'])
        def sensor_status():
            """Receive status update from ESP32"""
            try:
                data = request.get_json()
                logger.info(f"Received sensor status: {data}")

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

