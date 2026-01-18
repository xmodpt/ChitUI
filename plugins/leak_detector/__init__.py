"""
Leak Detector Plugin for ChitUI
ESP32-based resin leak detection system with real-time monitoring
"""

from flask import Blueprint, jsonify, request
from datetime import datetime
import logging
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

        # Register the blueprint
        app.register_blueprint(blueprint)

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
            }, broadcast=True)

    def _emit_alert(self, alert):
        """Emit urgent leak alert notification"""
        if self.socketio:
            self.socketio.emit('leak_detector_alert', alert, broadcast=True)


# Create global plugin instance
plugin_instance = None


def get_plugin_instance():
    """Get the global plugin instance"""
    global plugin_instance
    if plugin_instance is None:
        plugin_instance = Plugin()
    return plugin_instance


# =============================================================================
# ESP32 API Endpoints (called from ESP32 device)
# These endpoints are registered at the main app level, not in plugin blueprint
# =============================================================================

def register_esp32_endpoints(app):
    """
    Register ESP32-facing API endpoints at the main app level.
    These should be called from main.py when loading the plugin.
    """
    plugin = get_plugin_instance()

    @app.route('/api/leak_alert', methods=['POST'])
    def leak_alert():
        """Receive leak alert from ESP32"""
        try:
            data = request.get_json()

            # Create alert record
            alert = {
                'sensor': data.get('sensor'),
                'location': data.get('location'),
                'value': data.get('value'),
                'threshold': data.get('threshold'),
                'timestamp': data.get('timestamp'),
                'device_ip': data.get('device_ip'),
                'received_at': datetime.now().isoformat(),
                'alert': True
            }

            # Add to alerts list
            plugin.alerts.insert(0, alert)  # Most recent first

            # Limit alerts history
            if len(plugin.alerts) > plugin.max_alerts:
                plugin.alerts = plugin.alerts[:plugin.max_alerts]

            # Update sensor data
            sensor_id = f"sensor{data.get('sensor')}"
            plugin.sensors[sensor_id] = {
                'value': data.get('value'),
                'location': data.get('location'),
                'alert': True,
                'last_update': datetime.now().isoformat()
            }

            # Emit real-time updates
            plugin._emit_alert(alert)
            plugin._emit_update()

            logger.warning(f"LEAK ALERT: Sensor {data.get('sensor')} ({data.get('location')}) - Value: {data.get('value')}")

            return jsonify({'success': True, 'message': 'Alert received'}), 200

        except Exception as e:
            logger.error(f"Error processing leak alert: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/sensor_status', methods=['POST'])
    def sensor_status():
        """Receive status update from ESP32"""
        try:
            data = request.get_json()

            # Update device status
            plugin.device_status = {
                'online': data.get('status') == 'online',
                'ip': data.get('ip'),
                'chip': data.get('chip'),
                'version': data.get('version'),
                'last_update': datetime.now().isoformat()
            }

            # Emit update
            plugin._emit_update()

            logger.info(f"Status update from {data.get('ip')}: {data.get('status')}")

            return jsonify({'success': True, 'message': 'Status received'}), 200

        except Exception as e:
            logger.error(f"Error processing sensor status: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
