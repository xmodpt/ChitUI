"""
Terminal Plugin for ChitUI

Provides a terminal interface to monitor printer communication
and send raw commands to the printer.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from plugins.base import ChitUIPlugin
from flask import Blueprint, render_template_string, jsonify, request
from datetime import datetime
from collections import deque
import threading


class ConsoleCapture:
    """Captures stdout/stderr output for display in terminal"""

    def __init__(self, max_lines=1000):
        self.buffer = deque(maxlen=max_lines)
        self.lock = threading.Lock()
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr

    def write(self, text):
        """Capture output and store in buffer"""
        # Write to original stdout/stderr
        self.original_stdout.write(text)
        self.original_stdout.flush()

        # Store in buffer (skip empty lines)
        if text and text.strip():
            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            with self.lock:
                self.buffer.append(f"[{timestamp}] {text.rstrip()}")

        return len(text)

    def flush(self):
        """Flush original stdout"""
        self.original_stdout.flush()

    def isatty(self):
        """Return False - we're not a TTY"""
        return False

    def fileno(self):
        """Return the file descriptor of original stdout"""
        return self.original_stdout.fileno()

    def __getattr__(self, name):
        """Delegate unknown attributes to original stdout"""
        return getattr(self.original_stdout, name)

    def get_lines(self, last_n=500):
        """Get last N lines from buffer"""
        with self.lock:
            lines = list(self.buffer)
            return lines[-last_n:] if len(lines) > last_n else lines


class Plugin(ChitUIPlugin):
    """Terminal plugin implementation"""

    def __init__(self, plugin_dir):
        super().__init__(plugin_dir)
        self.message_log = []
        self.max_log_size = 1000
        self.socketio = None

        # Console output capture
        self.console_capture = ConsoleCapture(max_lines=1000)

        # Message filtering - only show these types
        self.filter_enabled = True
        self.allowed_message_types = {
            'system_command',  # System commands (M codes, system G-codes)
            'print_command',   # Print-related commands
            'print_status',    # Print status updates
            'system_error'     # System errors
        }

    def get_name(self):
        return "Terminal"

    def get_version(self):
        return "1.0.0"

    def get_description(self):
        return "Monitor printer communication and send raw commands"

    def get_author(self):
        return "ChitUI"

    def on_startup(self, app, socketio):
        """Initialize the plugin"""
        self.socketio = socketio

        # Redirect stdout to capture console output
        # NOTE: This captures print() statements and other stdout output
        sys.stdout = self.console_capture
        sys.stderr = self.console_capture

        # Create Flask blueprint for plugin routes
        self.blueprint = Blueprint(
            'terminal',
            __name__,
            static_folder=self.get_static_folder(),
            template_folder=self.get_template_folder()
        )

        # Register routes
        @self.blueprint.route('/messages')
        def get_messages():
            """Get message log"""
            return jsonify({'messages': self.message_log})

        @self.blueprint.route('/clear', methods=['POST'])
        def clear_messages():
            """Clear message log"""
            self.message_log = []
            return jsonify({'ok': True})

        @self.blueprint.route('/filter', methods=['GET'])
        def get_filter_settings():
            """Get current filter settings"""
            return jsonify({
                'enabled': self.filter_enabled,
                'allowed_types': list(self.allowed_message_types)
            })

        @self.blueprint.route('/filter', methods=['POST'])
        def update_filter_settings():
            """Update filter settings"""
            data = request.get_json()
            if 'enabled' in data:
                self.filter_enabled = bool(data['enabled'])
            return jsonify({'ok': True, 'enabled': self.filter_enabled})

        @self.blueprint.route('/console')
        def get_console():
            """Get live console output"""
            lines = self.console_capture.get_lines(last_n=500)

            return jsonify({
                'source': 'Live Python Console (stdout/stderr)',
                'lines': lines,
                'count': len(lines)
            })

    def register_socket_handlers(self, socketio):
        """Register SocketIO handlers"""

        @socketio.on('terminal_send_command')
        def handle_send_command(data):
            """Handle raw command sent from terminal"""
            printer_id = data.get('printer_id')
            command = data.get('command')

            if not printer_id or not command:
                return {'ok': False, 'msg': 'Missing printer_id or command'}

            # Format command for display
            if isinstance(command, dict):
                cmd_display = f"Cmd: {command.get('Cmd', '?')}"
                if 'Data' in command and command['Data']:
                    cmd_display += f" | Data: {command['Data']}"
            elif isinstance(command, (int, str)):
                # Simple command number
                cmd_names = {
                    0: "Get Status",
                    1: "Get Attributes",
                    128: "Start Print",
                    129: "Pause Print",
                    130: "Stop Print",
                    131: "Resume Print",
                    258: "Get File List",
                    320: "Get History"
                }
                cmd_num = int(command) if isinstance(command, str) and command.isdigit() else command
                cmd_name = cmd_names.get(cmd_num, f"Cmd {cmd_num}")
                cmd_display = cmd_name
            else:
                cmd_display = str(command)

            # Log the outgoing command
            self.log_message(printer_id, 'SEND', cmd_display)

            # Emit to main app for sending to printer
            socketio.emit('terminal_command', {
                'printer_id': printer_id,
                'command': command
            })

            return {'ok': True}

    def on_printer_message(self, printer_id, message):
        """Log incoming printer messages"""
        try:
            # Format the message for display
            msg_str = self.format_message(message)

            # Apply filtering if enabled
            if self.filter_enabled:
                message_type = self.categorize_message(msg_str)
                if message_type not in self.allowed_message_types:
                    return  # Skip this message

            self.log_message(printer_id, 'RECV', msg_str)
        except Exception as e:
            print(f"Error logging message: {e}")

    def format_message(self, message):
        """Format message for readable terminal display"""
        if not isinstance(message, dict):
            return str(message)

        # Extract readable content from message dictionary
        # Common message formats from 3D printer firmware:

        # SDCP Protocol Messages (ELEGOO printers)
        if 'Topic' in message:
            topic = message.get('Topic', '')

            # Response messages to commands
            if 'response' in topic.lower():
                # Extract the Data field from the response
                if 'Data' in message:
                    response_data = message['Data']
                    # Check what type of response this is
                    if isinstance(response_data, dict):
                        if 'Status' in response_data:
                            return self.format_sdcp_status(response_data['Status'])
                        elif 'Attributes' in response_data:
                            return self.format_sdcp_attributes(response_data['Attributes'])
                        elif 'FileList' in response_data:
                            files = response_data.get('FileList', [])
                            return f"Files: {len(files)} items"
                        else:
                            # Generic response
                            import json
                            return f"Response: {json.dumps(response_data)}"
                return "Response received"

            # Status messages
            if 'status' in topic.lower() and 'Status' in message:
                return self.format_sdcp_status(message['Status'])

            # Attribute messages (device info)
            if 'attributes' in topic.lower() and 'Attributes' in message:
                return self.format_sdcp_attributes(message['Attributes'])

            # Other SDCP messages - show topic
            return f"SDCP: {topic}"

        # Command sent to printer
        if 'command' in message:
            cmd = message['command']
            if isinstance(cmd, dict):
                # Handle nested command structure
                return self.format_command_dict(cmd)
            return str(cmd)

        # Response from printer
        if 'response' in message:
            resp = message['response']
            if isinstance(resp, str):
                return resp
            elif isinstance(resp, dict):
                return self.format_command_dict(resp)

        # Raw line from printer
        if 'line' in message:
            return str(message['line'])

        # Temperature report
        if 'temps' in message or 'temperature' in message:
            return self.format_temperature(message)

        # Status update
        if 'status' in message:
            status = message['status']
            if isinstance(status, str):
                return f"Status: {status}"
            elif isinstance(status, dict):
                return self.format_command_dict(status)

        # Print progress
        if 'progress' in message:
            progress = message['progress']
            return f"Progress: {progress}%"

        # File operation
        if 'file' in message:
            file_name = message['file']
            operation = message.get('operation', 'unknown')
            return f"{operation.upper()}: {file_name}"

        # Error message
        if 'error' in message:
            return f"ERROR: {message['error']}"

        # Generic message with 'msg' or 'message' field
        if 'msg' in message:
            return str(message['msg'])
        if 'message' in message and isinstance(message['message'], str):
            return message['message']

        # If message has only one key, display its value
        if len(message) == 1:
            key, value = list(message.items())[0]
            if isinstance(value, str):
                return value
            elif isinstance(value, dict):
                return self.format_command_dict(value)

        # Fallback: display as JSON for unknown formats
        import json
        return json.dumps(message, indent=2)

    def format_command_dict(self, cmd_dict):
        """Format command dictionary for display"""
        if not isinstance(cmd_dict, dict):
            return str(cmd_dict)

        # G-code or M-code command
        if 'code' in cmd_dict:
            code = cmd_dict['code']
            params = cmd_dict.get('params', {})
            param_str = ' '.join(f"{k}{v}" for k, v in params.items())
            return f"{code} {param_str}".strip()

        # Command with type and data
        if 'type' in cmd_dict and 'data' in cmd_dict:
            return f"{cmd_dict['type']}: {cmd_dict['data']}"

        # Simple key-value pairs
        parts = []
        for k, v in cmd_dict.items():
            if isinstance(v, (str, int, float)):
                parts.append(f"{k}={v}")

        if parts:
            return ' '.join(parts)

        # Fallback to JSON
        import json
        return json.dumps(cmd_dict)

    def format_temperature(self, temp_dict):
        """Format temperature data for display"""
        parts = []

        # Hotend temperature
        if 'tool0' in temp_dict or 't0' in temp_dict:
            temp_data = temp_dict.get('tool0', temp_dict.get('t0'))
            if isinstance(temp_data, dict):
                actual = temp_data.get('actual', temp_data.get('current', '?'))
                target = temp_data.get('target', '?')
                parts.append(f"T0:{actual}/{target}")
            else:
                parts.append(f"T0:{temp_data}")

        # Bed temperature
        if 'bed' in temp_dict or 'b' in temp_dict:
            temp_data = temp_dict.get('bed', temp_dict.get('b'))
            if isinstance(temp_data, dict):
                actual = temp_data.get('actual', temp_data.get('current', '?'))
                target = temp_data.get('target', '?')
                parts.append(f"B:{actual}/{target}")
            else:
                parts.append(f"B:{temp_data}")

        if parts:
            return ' '.join(parts)

        # Fallback
        import json
        return json.dumps(temp_dict)

    def format_sdcp_status(self, status_dict):
        """Format SDCP status messages for ELEGOO printers"""
        parts = []

        # Print information (most important)
        if 'PrintInfo' in status_dict:
            print_info = status_dict['PrintInfo']
            status_code = print_info.get('Status', 'Unknown')

            # Map status codes to readable names
            status_names = {
                0: 'Idle',
                1: 'Printing',
                2: 'Paused',
                3: 'Complete',
                8: 'Idle',  # Another idle state
                # Add more as discovered
            }
            status_name = status_names.get(status_code, f'Status{status_code}')

            current_layer = print_info.get('CurrentLayer', 0)
            total_layer = print_info.get('TotalLayer', 0)
            filename = print_info.get('Filename', 'N/A')
            error_num = print_info.get('ErrorNumber', 0)

            parts.append(f"Status: {status_name}")

            if total_layer > 0:
                progress = (current_layer / total_layer * 100) if total_layer > 0 else 0
                parts.append(f"Layer: {current_layer}/{total_layer} ({progress:.1f}%)")

            if filename and filename != 'N/A':
                parts.append(f"File: {filename}")

            if error_num != 0:
                parts.append(f"ERROR: {error_num}")

        # Temperature
        if 'TempOfUVLED' in status_dict:
            temp = status_dict['TempOfUVLED']
            parts.append(f"LED Temp: {temp:.1f}°C")

        # Release film position
        if 'ReleaseFilm' in status_dict:
            film_pos = status_dict['ReleaseFilm']
            parts.append(f"Film: {film_pos}")

        return ' | '.join(parts) if parts else 'Status: OK'

    def format_sdcp_attributes(self, attr_dict):
        """Format SDCP attributes messages for ELEGOO printers"""
        parts = []

        # Machine name and firmware
        machine_name = attr_dict.get('MachineName', attr_dict.get('Name', 'Unknown'))
        firmware = attr_dict.get('FirmwareVersion', 'N/A')

        parts.append(f"{machine_name}")
        parts.append(f"FW: {firmware}")

        # Network info
        if 'MainboardIP' in attr_dict:
            ip = attr_dict['MainboardIP']
            parts.append(f"IP: {ip}")

        # Resolution
        if 'Resolution' in attr_dict:
            res = attr_dict['Resolution']
            parts.append(f"Res: {res}")

        # Remaining memory
        if 'RemainingMemory' in attr_dict:
            mem_bytes = attr_dict['RemainingMemory']
            mem_gb = mem_bytes / (1024**3)
            parts.append(f"Free: {mem_gb:.2f}GB")

        # Device status summary
        if 'DevicesStatus' in attr_dict:
            devices = attr_dict['DevicesStatus']
            # Only show if there's a problem
            failed_devices = [k for k, v in devices.items() if v != 1]
            if failed_devices:
                parts.append(f"DEVICE ERRORS: {', '.join(failed_devices)}")

        return ' | '.join(parts)

    def categorize_message(self, message):
        """Categorize message type for filtering"""
        msg_lower = message.lower()

        # System errors
        if any(err in msg_lower for err in ['error', 'err:', 'fail', 'warning', 'warn:', 'exception', 'fault']):
            return 'system_error'

        # SDCP Protocol messages (ELEGOO printers)
        if any(sdcp in msg_lower for sdcp in ['status:', 'layer:', 'file:', 'idle', 'printing', 'paused', 'complete']):
            return 'print_status'

        # SDCP device attributes (firmware, model, IP, etc.)
        if any(attr in msg_lower for attr in ['fw:', 'ip:', 'res:', 'free:', 'saturn', 'mars', 'jupiter', 'elegoo']):
            return 'system_command'

        # SDCP command names and responses
        if any(cmd in msg_lower for cmd in ['get status', 'get attributes', 'get file', 'get history',
                                              'start print', 'pause print', 'stop print', 'resume print',
                                              'response:', 'files:', 'response received']):
            return 'print_command'

        # System commands (M codes and system G-codes)
        if any(cmd in msg_lower for cmd in ['m110', 'm111', 'm112', 'm115', 'm117', 'm118', 'm119',
                                              'm120', 'm121', 'm122', 'm123', 'm124', 'm125',
                                              'm503', 'm504', 'm505', 'm997', 'm999']):
            return 'system_command'

        # Print status (temperature, position, status reports)
        if any(status in msg_lower for status in ['t:', 'b:', 'ok t:', 'x:', 'y:', 'z:', 'e:',
                                                    'count ', 'progress', 'percent',
                                                    'position', 'busy:', 'ok b:', 'temp:']):
            return 'print_status'

        # Print commands (temperature control, movement, print-related)
        if any(cmd in msg_lower for cmd in ['m104', 'm105', 'm106', 'm107', 'm108', 'm109',
                                              'm140', 'm141', 'm190', 'm191',
                                              'g28', 'g29', 'g90', 'g91', 'g92',
                                              'm0', 'm1', 'm17', 'm18', 'm20', 'm21', 'm22',
                                              'm23', 'm24', 'm25', 'm26', 'm27', 'm28', 'm29',
                                              'm30', 'm31', 'm32', 'm33']):
            return 'print_command'

        # If starts with G or M followed by number, likely a command
        import re
        if re.match(r'^[gm]\d+', msg_lower.strip()):
            # Check if it's movement (G0, G1) or print-related
            if re.match(r'^g[01]\s', msg_lower.strip()):
                return 'print_command'
            # Other G/M codes as system commands
            return 'system_command'

        # Default: don't show
        return 'other'

    def log_message(self, printer_id, direction, message):
        """Add a message to the log"""
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

        log_entry = {
            'timestamp': timestamp,
            'printer_id': printer_id,
            'direction': direction,
            'message': message
        }

        self.message_log.append(log_entry)

        # Keep log size under limit
        if len(self.message_log) > self.max_log_size:
            self.message_log = self.message_log[-self.max_log_size:]

        # Broadcast to connected clients
        if self.socketio:
            self.socketio.emit('terminal_message', log_entry)

    def get_ui_integration(self):
        """Return UI integration configuration"""
        return {
            'type': 'card',
            'location': 'main',
            'icon': 'bi-terminal',
            'title': 'Terminal',
            'template': 'terminal.html'
        }
