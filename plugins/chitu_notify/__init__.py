"""
Chitu Notify Plugin for ChitUI
Push notifications via ntfy.sh for relay events, leak alerts, printer status, and more.
"""

import os
import json
import random
import string
import threading
from datetime import datetime
from loguru import logger
from flask import Blueprint, jsonify, request
from plugins.base import ChitUIPlugin

try:
    import requests as http_requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("requests library not available - notifications will not be sent")


class Plugin(ChitUIPlugin):
    """Chitu Notify Plugin - Push notifications via ntfy.sh"""

    def __init__(self, plugin_dir):
        super().__init__(plugin_dir)
        self.socketio = None
        self.app = None  # Reference to Flask app for accessing printers

        # Configuration file path
        self.config_file = os.path.join(
            os.path.expanduser('~'), '.chitui', 'chitu_notify_config.json'
        )

        # Notification log file
        self.log_file = os.path.join(
            os.path.expanduser('~'), '.chitui', 'chitu_notify_log.json'
        )

        # Default configuration
        self.config = {
            'enabled': True,
            'ntfy_url': 'https://ntfy.sh',
            'service_name': '',
            'topic': '',
            # Alarm definitions - each alarm has a name, enabled state, priority, and tags
            'alarms': {
                'relay_on': {
                    'name': 'Power Relay ON',
                    'enabled': True,
                    'priority': 'default',
                    'tags': 'electric_plug,white_check_mark',
                    'message': 'Power relay has been turned ON.'
                },
                'relay_off': {
                    'name': 'Power Relay OFF',
                    'enabled': True,
                    'priority': 'high',
                    'tags': 'electric_plug,x',
                    'message': 'Power relay has been turned OFF.'
                },
                'leak_detected': {
                    'name': 'LEAK DETECTED',
                    'enabled': True,
                    'priority': 'urgent',
                    'tags': 'warning,droplet',
                    'message': 'Resin leak detected! Check immediately.'
                },
                'leak_reset': {
                    'name': 'Leak Reset',
                    'enabled': True,
                    'priority': 'default',
                    'tags': 'white_check_mark,droplet',
                    'message': 'Leak detection system has been reset. Monitoring resumed.'
                },
                'leak_relay_armed': {
                    'name': 'Safety Relay ARMED',
                    'enabled': True,
                    'priority': 'urgent',
                    'tags': 'rotating_light,zap',
                    'message': 'Safety relay armed - power has been CUT due to leak detection.'
                },
                'leak_relay_disarmed': {
                    'name': 'Safety Relay Disarmed',
                    'enabled': True,
                    'priority': 'high',
                    'tags': 'white_check_mark,zap',
                    'message': 'Safety relay disarmed - power has been restored.'
                },
                'printer_connected': {
                    'name': 'Printer Connected',
                    'enabled': True,
                    'priority': 'default',
                    'tags': 'printer,link',
                    'message': 'Printer has connected.'
                },
                'printer_disconnected': {
                    'name': 'Printer Disconnected',
                    'enabled': True,
                    'priority': 'high',
                    'tags': 'printer,broken_heart',
                    'message': 'Printer has disconnected.'
                },
                'print_started': {
                    'name': 'Print Started',
                    'enabled': True,
                    'priority': 'default',
                    'tags': 'printer,rocket',
                    'message': 'A print job has started.'
                },
                'print_paused': {
                    'name': 'Print Paused',
                    'enabled': True,
                    'priority': 'default',
                    'tags': 'printer,pause_button',
                    'message': 'Print job has been paused.'
                },
                'print_stopped': {
                    'name': 'Print Stopped',
                    'enabled': True,
                    'priority': 'high',
                    'tags': 'printer,stop_button',
                    'message': 'Print job has been stopped.'
                },
                'print_completed': {
                    'name': 'Print Completed',
                    'enabled': True,
                    'priority': 'default',
                    'tags': 'printer,tada',
                    'message': 'Print job completed successfully!'
                },
                'print_failed': {
                    'name': 'Print Failed',
                    'enabled': True,
                    'priority': 'high',
                    'tags': 'printer,x',
                    'message': 'Print job has failed.'
                },
                'system_boot': {
                    'name': 'System Boot',
                    'enabled': True,
                    'priority': 'default',
                    'tags': 'arrows_counterclockwise,computer',
                    'message': 'ChitUI has started up.'
                }
            }
        }

        # Notification log
        self.notification_log = []
        self.max_log_entries = 50

        # Load saved configuration
        self.load_config()

        # Load notification log
        self.load_log()

        # Generate topic if not set
        if not self.config.get('topic'):
            self._generate_topic()

    def get_name(self):
        return "Chitu Notify"

    def get_version(self):
        return "1.0.0"

    def get_description(self):
        return "Push notifications via ntfy.sh for relay events, leak alerts, printer status, and more"

    def get_author(self):
        return "ChitUI Developer"

    def get_dependencies(self):
        return ['requests']

    def get_ui_integration(self):
        return {
            'type': 'toolbar',
            'location': 'top',
            'icon': 'bi-bell-fill',
            'title': 'Chitu Notify',
            'template': 'chitu_notify.html'
        }

    def has_settings(self):
        """This plugin has a settings page"""
        return True

    def _generate_topic(self, printer_serial=None):
        """
        Generate a unique ntfy topic from service name + printer serial or random suffix.

        Args:
            printer_serial: Optional printer serial number to use as unique suffix.
                           If not provided, uses random characters.
        """
        service_name = self.config.get('service_name', '').strip()
        if not service_name:
            service_name = 'chitui'
        # Sanitize service name: lowercase, replace spaces with hyphens
        service_name = service_name.lower().replace(' ', '-')
        # Remove any non-alphanumeric characters except hyphens
        service_name = ''.join(
            c for c in service_name if c.isalnum() or c == '-'
        )

        if printer_serial:
            # Use printer serial (last 10 chars if longer, or full serial)
            # Sanitize serial: lowercase, alphanumeric only
            clean_serial = ''.join(c for c in printer_serial if c.isalnum()).lower()
            suffix = clean_serial[-10:] if len(clean_serial) > 10 else clean_serial
            self.config['printer_serial'] = printer_serial  # Store original serial
        else:
            # Fallback to random suffix
            suffix = ''.join(
                random.choices(string.ascii_lowercase + string.digits, k=10)
            )
            self.config['printer_serial'] = None

        self.config['topic'] = f"{service_name}-{suffix}"
        self.save_config()
        logger.info(f"Generated ntfy topic: {self.config['topic']}")

    def load_config(self):
        """Load configuration from file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    saved_config = json.load(f)

                # Deep merge alarms
                if 'alarms' in saved_config:
                    for alarm_id, alarm_data in saved_config['alarms'].items():
                        if alarm_id in self.config['alarms']:
                            self.config['alarms'][alarm_id].update(alarm_data)
                        else:
                            self.config['alarms'][alarm_id] = alarm_data
                    del saved_config['alarms']

                # Merge top-level keys
                self.config.update(saved_config)
                logger.info("Chitu Notify configuration loaded")
        except Exception as e:
            logger.error(f"Error loading chitu_notify config: {e}")

    def save_config(self):
        """Save configuration to file"""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info("Chitu Notify configuration saved")
        except Exception as e:
            logger.error(f"Error saving chitu_notify config: {e}")

    def load_log(self):
        """Load notification log from file"""
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r') as f:
                    self.notification_log = json.load(f)
        except Exception as e:
            logger.error(f"Error loading notification log: {e}")
            self.notification_log = []

    def save_log(self):
        """Save notification log to file"""
        try:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            with open(self.log_file, 'w') as f:
                json.dump(self.notification_log, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving notification log: {e}")

    def _add_log_entry(self, alarm_id, title, message, success, error=None):
        """Add an entry to the notification log"""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'alarm_id': alarm_id,
            'title': title,
            'message': message,
            'success': success,
            'error': error
        }
        self.notification_log.insert(0, entry)
        if len(self.notification_log) > self.max_log_entries:
            self.notification_log = self.notification_log[:self.max_log_entries]
        self.save_log()

        # Emit log update to connected clients
        if self.socketio:
            self.socketio.emit('chitu_notify_log_update', {
                'entry': entry,
                'count': len(self.notification_log)
            })

    def send_notification(self, alarm_id, extra_message=None):
        """
        Send a push notification for a given alarm.

        Args:
            alarm_id: The alarm identifier (e.g., 'relay_on', 'leak_detected')
            extra_message: Optional extra text to append to the message
        """
        if not self.config.get('enabled', True):
            logger.debug("Chitu Notify is disabled, skipping notification")
            return False

        alarm = self.config.get('alarms', {}).get(alarm_id)
        if not alarm:
            logger.warning(f"Unknown alarm: {alarm_id}")
            return False

        if not alarm.get('enabled', True):
            logger.debug(f"Alarm {alarm_id} is disabled, skipping")
            return False

        if not REQUESTS_AVAILABLE:
            logger.error("requests library not available")
            self._add_log_entry(alarm_id, alarm['name'], '', False, 'requests library not available')
            return False

        topic = self.config.get('topic', '')
        if not topic:
            logger.error("No ntfy topic configured")
            self._add_log_entry(alarm_id, alarm['name'], '', False, 'No topic configured')
            return False

        ntfy_url = self.config.get('ntfy_url', 'https://ntfy.sh')
        url = f"{ntfy_url}/{topic}"

        title = alarm.get('name', alarm_id)
        message = alarm.get('message', '')
        if extra_message:
            message = f"{message}\n{extra_message}"

        headers = {
            'Title': title,
            'Priority': alarm.get('priority', 'default'),
            'Content-Type': 'text/plain; charset=utf-8'
        }

        tags = alarm.get('tags', '')
        if tags:
            headers['Tags'] = tags

        def _do_send():
            try:
                response = http_requests.post(
                    url,
                    data=message.encode('utf-8'),
                    headers=headers,
                    timeout=10
                )
                if response.status_code == 200:
                    logger.info(f"Notification sent: {title}")
                    self._add_log_entry(alarm_id, title, message, True)
                else:
                    logger.error(f"Failed to send notification: {response.status_code} - {response.text}")
                    self._add_log_entry(alarm_id, title, message, False, f"HTTP {response.status_code}")
            except Exception as e:
                logger.error(f"Error sending notification: {e}")
                self._add_log_entry(alarm_id, title, message, False, str(e))

        # Send in background thread to avoid blocking
        threading.Thread(target=_do_send, daemon=True).start()
        return True

    def on_startup(self, app, socketio):
        """Called when plugin is loaded"""
        self.socketio = socketio
        self.app = app  # Store reference to access global printers dict

        # Create Flask blueprint
        blueprint = Blueprint(
            'chitu_notify',
            __name__,
            static_folder=self.get_static_folder(),
            static_url_path='/static'
        )

        @blueprint.route('/status', methods=['GET'])
        def get_status():
            """Get notification service status"""
            return jsonify({
                'enabled': self.config.get('enabled', True),
                'topic': self.config.get('topic', ''),
                'ntfy_url': self.config.get('ntfy_url', 'https://ntfy.sh'),
                'service_name': self.config.get('service_name', ''),
                'requests_available': REQUESTS_AVAILABLE,
                'recent_log': self.notification_log[:10]
            })

        @blueprint.route('/config', methods=['GET'])
        def get_config():
            """Get current configuration"""
            return jsonify(self.config)

        @blueprint.route('/config', methods=['POST'])
        def update_config():
            """Update configuration"""
            try:
                data = request.get_json()

                # Update enabled state
                if 'enabled' in data:
                    self.config['enabled'] = bool(data['enabled'])

                # Update ntfy URL
                if 'ntfy_url' in data:
                    url = data['ntfy_url'].strip().rstrip('/')
                    if url:
                        self.config['ntfy_url'] = url

                # Update service name and regenerate topic
                if 'service_name' in data:
                    new_name = data['service_name'].strip()
                    old_name = self.config.get('service_name', '')
                    self.config['service_name'] = new_name
                    if new_name != old_name or not self.config.get('topic'):
                        self._generate_topic()

                # Update topic directly (if user wants to set a custom one)
                if 'topic' in data and data.get('topic_manual'):
                    self.config['topic'] = data['topic'].strip()

                # Update alarms
                if 'alarms' in data:
                    for alarm_id, alarm_data in data['alarms'].items():
                        if alarm_id in self.config['alarms']:
                            for key in ['name', 'enabled', 'priority', 'tags', 'message']:
                                if key in alarm_data:
                                    if key == 'enabled':
                                        self.config['alarms'][alarm_id][key] = bool(alarm_data[key])
                                    else:
                                        self.config['alarms'][alarm_id][key] = alarm_data[key]

                self.save_config()

                # Emit config update
                if self.socketio:
                    self.socketio.emit('chitu_notify_config_updated', self.config)

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

        @blueprint.route('/printers', methods=['GET'])
        def get_printers():
            """Get list of available printers with their serial numbers"""
            try:
                # Access the global printers dict from main module via sys.modules
                import sys
                main_module = sys.modules.get('main') or sys.modules.get('__main__')
                printers_dict = getattr(main_module, 'printers', {}) if main_module else {}

                printers_list = []
                for printer_id, printer_info in printers_dict.items():
                    printers_list.append({
                        'id': printer_id,
                        'name': printer_info.get('name', 'Unknown'),
                        'serial': printer_id  # MainboardID is the serial
                    })

                logger.debug(f"Found {len(printers_list)} printers: {[p['name'] for p in printers_list]}")

                return jsonify({
                    'success': True,
                    'printers': printers_list,
                    'current_serial': self.config.get('printer_serial')
                })
            except Exception as e:
                logger.error(f"Error getting printers: {e}")
                import traceback
                traceback.print_exc()
                return jsonify({
                    'success': False,
                    'printers': [],
                    'message': str(e)
                })

        @blueprint.route('/regenerate_topic', methods=['POST'])
        def regenerate_topic():
            """Regenerate the ntfy topic with printer serial or random suffix"""
            data = request.get_json() or {}
            printer_serial = data.get('printer_serial')
            self._generate_topic(printer_serial)
            return jsonify({
                'success': True,
                'topic': self.config['topic'],
                'printer_serial': self.config.get('printer_serial')
            })

        @blueprint.route('/test', methods=['POST'])
        def test_notification():
            """Send a test notification"""
            data = request.get_json() or {}
            alarm_id = data.get('alarm_id', 'system_boot')
            extra = data.get('message', 'This is a test notification from Chitu Notify.')
            result = self.send_notification(alarm_id, extra)
            return jsonify({
                'success': result,
                'message': 'Test notification sent' if result else 'Failed to send test notification'
            })

        @blueprint.route('/log', methods=['GET'])
        def get_log():
            """Get notification log"""
            return jsonify({
                'log': self.notification_log,
                'count': len(self.notification_log)
            })

        @blueprint.route('/log/clear', methods=['POST'])
        def clear_log():
            """Clear notification log"""
            self.notification_log = []
            self.save_log()
            return jsonify({'success': True, 'message': 'Log cleared'})

        @blueprint.route('/settings', methods=['GET'])
        def get_settings():
            """Get settings HTML"""
            settings_template = os.path.join(self.get_template_folder(), 'settings.html')
            if os.path.exists(settings_template):
                with open(settings_template, 'r') as f:
                    return f.read()
            return 'Settings template not found', 404

        # Register blueprint
        app.register_blueprint(blueprint, url_prefix='/plugin/chitu_notify')

        # Send system boot notification
        self.send_notification('system_boot')

        logger.info("Chitu Notify plugin started")

    def register_socket_handlers(self, socketio):
        """Register Socket.IO event handlers"""
        self.socketio = socketio

        @socketio.on('chitu_notify_request_status')
        def handle_status_request():
            """Send current status to requesting client"""
            socketio.emit('chitu_notify_status', {
                'enabled': self.config.get('enabled', True),
                'topic': self.config.get('topic', ''),
                'ntfy_url': self.config.get('ntfy_url', 'https://ntfy.sh'),
                'service_name': self.config.get('service_name', ''),
                'recent_log': self.notification_log[:10]
            })

        # Listen for relay state changes from GPIO Relay Control plugin
        @socketio.on('gpio_relay_state_changed')
        def handle_relay_change(data):
            """Handle relay state change events"""
            relay_num = data.get('relay')
            state = data.get('state')
            if state:
                self.send_notification(
                    'relay_on',
                    f"Relay {relay_num} has been turned ON."
                )
            else:
                self.send_notification(
                    'relay_off',
                    f"Relay {relay_num} has been turned OFF."
                )

        # Listen for leak detector events
        @socketio.on('leak_detector_alert')
        def handle_leak_alert(data):
            """Handle leak detection alert"""
            sensor = data.get('sensor', '?')
            location = data.get('location', 'Unknown')
            value = data.get('value', 'N/A')
            self.send_notification(
                'leak_detected',
                f"Sensor {sensor} ({location}) - Value: {value}"
            )

        @socketio.on('leak_detector_relay_update')
        def handle_leak_relay(data):
            """Handle leak detector relay state changes"""
            relay_state = data.get('relay_state', {})
            if relay_state.get('armed'):
                reason = relay_state.get('armed_reason', 'Leak detected')
                self.send_notification(
                    'leak_relay_armed',
                    f"Reason: {reason}"
                )
            else:
                self.send_notification(
                    'leak_relay_disarmed'
                )

        # Listen for test notifications from the UI
        @socketio.on('chitu_notify_send_test')
        def handle_send_test(data):
            """Handle test notification request from UI"""
            alarm_id = data.get('alarm_id', 'system_boot')
            self.send_notification(alarm_id, 'Test notification from Chitu Notify.')

    def on_printer_connected(self, printer_id, printer_info):
        """Called when a printer connects"""
        name = printer_info.get('name', printer_id) if isinstance(printer_info, dict) else printer_id
        self.send_notification(
            'printer_connected',
            f"Printer: {name}"
        )

    def on_printer_disconnected(self, printer_id):
        """Called when a printer disconnects"""
        self.send_notification(
            'printer_disconnected',
            f"Printer ID: {printer_id}"
        )

    def _format_time(self, ticks):
        """Format time in milliseconds to HH:MM:SS"""
        if not ticks or ticks <= 0:
            return "Unknown"
        total_seconds = int(ticks / 1000)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def on_printer_message(self, printer_id, message):
        """Called when a message is received from a printer"""
        if not isinstance(message, dict):
            return

        status = message.get('Status', {})
        if not status:
            return

        # Track print status per printer
        if not hasattr(self, '_printer_print_status'):
            self._printer_print_status = {}

        # Get PrintInfo for detailed status
        print_info = status.get('PrintInfo', {})
        if isinstance(print_info, list) and len(print_info) > 0:
            print_info = print_info[0] if isinstance(print_info[0], dict) else {}
        elif not isinstance(print_info, dict):
            print_info = {}

        current_print_status = print_info.get('Status')
        prev_print_status = self._printer_print_status.get(printer_id)

        # Only process if we have a valid status and it changed
        if current_print_status is not None and current_print_status != prev_print_status:
            self._printer_print_status[printer_id] = current_print_status

            filename = print_info.get('Filename', 'Unknown')
            total_ticks = print_info.get('TotalTicks', 0)
            current_ticks = print_info.get('CurrentTicks', 0)
            total_layers = print_info.get('TotalLayer', 0)
            current_layer = print_info.get('CurrentLayer', 0)

            # Print status codes from sdcp.js:
            # 0 = IDLE, 1 = HOMING, 2 = DROPPING, 3 = EXPOSURING, 4 = LIFTING
            # 5 = PAUSING, 6 = PAUSED, 7 = STOPPING, 8 = STOPPED, 9 = COMPLETE
            # 10 = FILE_CHECKING

            # Print Started - transition from idle/checking to active printing states
            if current_print_status in (1, 2, 3, 4) and prev_print_status in (None, 0, 10):
                estimated_time = self._format_time(total_ticks)
                self.send_notification(
                    'print_started',
                    f"File: {filename}\nEstimated time: {estimated_time}\nLayers: {total_layers}"
                )

            # Print Paused
            elif current_print_status == 6 and prev_print_status != 6:
                elapsed_time = self._format_time(current_ticks)
                remaining_time = self._format_time(total_ticks - current_ticks) if total_ticks > current_ticks else "Unknown"
                progress = f"{current_layer}/{total_layers}" if total_layers > 0 else "Unknown"
                self.send_notification(
                    'print_paused',
                    f"File: {filename}\nProgress: {progress} layers\nElapsed: {elapsed_time}\nRemaining: {remaining_time}"
                )

            # Print Stopped
            elif current_print_status == 8 and prev_print_status not in (None, 8):
                elapsed_time = self._format_time(current_ticks)
                progress = f"{current_layer}/{total_layers}" if total_layers > 0 else "Unknown"
                self.send_notification(
                    'print_stopped',
                    f"File: {filename}\nStopped at layer: {progress}\nTime elapsed: {elapsed_time}"
                )

            # Print Completed
            elif current_print_status == 9 and prev_print_status != 9:
                total_time = self._format_time(current_ticks)
                self.send_notification(
                    'print_completed',
                    f"File: {filename}\nTotal layers: {total_layers}\nTotal time: {total_time}"
                )

            # Print Failed (error detected while printing)
            elif current_print_status == 0 and prev_print_status in (1, 2, 3, 4, 5, 6, 7):
                # Only notify if there was an error number
                error_number = print_info.get('ErrorNumber', 0)
                if error_number and error_number != 0:
                    elapsed_time = self._format_time(current_ticks)
                    progress = f"{current_layer}/{total_layers}" if total_layers > 0 else "Unknown"
                    self.send_notification(
                        'print_failed',
                        f"File: {filename}\nFailed at layer: {progress}\nError code: {error_number}\nTime elapsed: {elapsed_time}"
                    )

    def on_shutdown(self):
        """Called when plugin is disabled"""
        logger.info("Chitu Notify plugin shut down")
