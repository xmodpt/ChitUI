import os
import json
from flask import Blueprint, jsonify, request
from plugins.base import ChitUIPlugin

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    print("RPi.GPIO not available - running in simulation mode")


class Plugin(ChitUIPlugin):
    """
    GPIO Relay Control Plugin

    Controls up to 4 relays connected to Raspberry Pi GPIO pins.
    Default GPIO pins: 17, 27, 22, 23 (BCM numbering)
    """

    def __init__(self, plugin_dir):
        super().__init__(plugin_dir)
        self.plugin_dir = plugin_dir
        self.socketio = None

        # Configuration file path
        self.config_file = os.path.join(os.path.expanduser('~'), '.chitui', 'gpio_relay_config.json')

        # Default configuration
        self.config = {
            'relay1_pin': 17,
            'relay2_pin': 27,
            'relay3_pin': 22,
            'relay4_pin': 23,
            'relay1_name': 'Relay 1',
            'relay2_name': 'Relay 2',
            'relay3_name': 'Relay 3',
            'relay4_name': 'Relay 4',
            'relay1_type': 'NO',  # NO (Normally Open) or NC (Normally Closed)
            'relay2_type': 'NO',
            'relay3_type': 'NO',
            'relay4_type': 'NO',
            'relay1_icon': 'fa-bolt',  # FontAwesome icon
            'relay2_icon': 'fa-power-off',
            'relay3_icon': 'fa-fan',
            'relay4_icon': 'fa-lightbulb',
            'relay1_state': False,
            'relay2_state': False,
            'relay3_state': False,
            'relay4_state': False,
            'relay1_enabled': True,  # Enable/disable relay visibility
            'relay2_enabled': True,
            'relay3_enabled': True,
            'relay4_enabled': True,
            'relay1_show_label': True,  # Show/hide relay label
            'relay2_show_label': True,
            'relay3_show_label': True,
            'relay4_show_label': True,
            'show_text': True  # Global show text labels on buttons (deprecated, use per-relay settings)
        }

        # Load saved configuration
        self.load_config()

        # Initialize GPIO
        self.init_gpio()

    def get_name(self):
        return "GPIO Relay Control"

    def get_version(self):
        return "1.0.0"

    def get_description(self):
        return "Control up to 4 GPIO relays from the toolbar"

    def get_author(self):
        return "ChitUI Developer"

    def get_ui_integration(self):
        """Return UI integration configuration"""
        return {
            'type': 'toolbar',
            'location': 'top',
            'icon': 'bi-lightning-charge',
            'title': 'GPIO Relays',
            'template': 'gpio_relay_control.html'
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
        except Exception as e:
            print(f"Error loading GPIO relay config: {e}")

    def save_config(self):
        """Save configuration to file"""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving GPIO relay config: {e}")

    def init_gpio(self):
        """Initialize GPIO pins"""
        if not GPIO_AVAILABLE:
            print("GPIO not available - skipping initialization")
            return

        try:
            # Set GPIO mode to BCM numbering
            GPIO.setmode(GPIO.BCM)

            # Enable warnings temporarily for debugging
            GPIO.setwarnings(True)

            # Setup relay pins as outputs (only for enabled relays)
            for i in range(1, 5):
                if self.config.get(f'relay{i}_enabled', True):
                    pin = self.config[f'relay{i}_pin']
                    try:
                        # Clean up the pin first in case it was used before
                        GPIO.cleanup(pin)

                        # Setup pin as output
                        GPIO.setup(pin, GPIO.OUT)

                        # Set initial state
                        initial_level = self.get_gpio_level(i, self.config[f'relay{i}_state'])
                        GPIO.output(pin, initial_level)

                        print(f"✓ Relay {i} (GPIO {pin}): Initialized successfully")
                    except Exception as e:
                        print(f"✗ Relay {i} (GPIO {pin}): FAILED - {e}")
                        # Continue with other relays even if one fails

            print("GPIO relay pins initialization completed")
        except Exception as e:
            print(f"Error initializing GPIO: {e}")

    def get_gpio_level(self, relay_num, state):
        """
        Get the correct GPIO level for a relay based on its type (NO/NC)

        Args:
            relay_num: Relay number (1-4)
            state: Desired state (True=ON, False=OFF)

        Returns:
            GPIO.HIGH or GPIO.LOW
        """
        relay_type = self.config.get(f'relay{relay_num}_type', 'NO')

        if relay_type == 'NC':  # Normally Closed - invert logic
            return GPIO.LOW if state else GPIO.HIGH
        else:  # Normally Open (default)
            return GPIO.HIGH if state else GPIO.LOW

    def set_relay_state(self, relay_num, state):
        """Set relay state (True=ON, False=OFF)"""
        if not GPIO_AVAILABLE:
            print(f"Simulation: Relay {relay_num} set to {'ON' if state else 'OFF'}")
            # Update config even in simulation mode
            self.config[f'relay{relay_num}_state'] = state
            self.save_config()
            return True

        try:
            pin_key = f'relay{relay_num}_pin'
            state_key = f'relay{relay_num}_state'

            if pin_key not in self.config:
                return False

            pin = self.config[pin_key]
            gpio_level = self.get_gpio_level(relay_num, state)
            GPIO.output(pin, gpio_level)

            # Update and save state
            self.config[state_key] = state
            self.save_config()

            # Emit state change to all connected clients
            if self.socketio:
                self.socketio.emit('gpio_relay_state_changed', {
                    'relay': relay_num,
                    'state': state
                })

            return True
        except Exception as e:
            print(f"Error setting relay {relay_num} state: {e}")
            return False

    def get_relay_state(self, relay_num):
        """Get current relay state"""
        state_key = f'relay{relay_num}_state'
        return self.config.get(state_key, False)

    def toggle_relay(self, relay_num):
        """Toggle relay state"""
        current_state = self.get_relay_state(relay_num)
        return self.set_relay_state(relay_num, not current_state)

    def get_all_states(self):
        """Get all relay states"""
        return {
            'relay1': {
                'name': self.config['relay1_name'],
                'pin': self.config['relay1_pin'],
                'state': self.config['relay1_state'],
                'enabled': self.config.get('relay1_enabled', True)
            },
            'relay2': {
                'name': self.config['relay2_name'],
                'pin': self.config['relay2_pin'],
                'state': self.config['relay2_state'],
                'enabled': self.config.get('relay2_enabled', True)
            },
            'relay3': {
                'name': self.config['relay3_name'],
                'pin': self.config['relay3_pin'],
                'state': self.config['relay3_state'],
                'enabled': self.config.get('relay3_enabled', True)
            },
            'relay4': {
                'name': self.config['relay4_name'],
                'pin': self.config['relay4_pin'],
                'state': self.config['relay4_state'],
                'enabled': self.config.get('relay4_enabled', True)
            },
            'gpio_available': GPIO_AVAILABLE
        }

    def on_startup(self, app, socketio):
        """Called when plugin is loaded"""
        self.socketio = socketio

        # Create Flask blueprint for API routes
        blueprint = Blueprint(
            'gpio_relay_control',
            __name__,
            static_folder=self.get_static_folder(),
            static_url_path='/static'
        )

        @blueprint.route('/status', methods=['GET'])
        def get_status():
            """Get status of all relays"""
            return jsonify(self.get_all_states())

        @blueprint.route('/relay/<int:relay_num>/toggle', methods=['POST'])
        def toggle_relay_route(relay_num):
            """Toggle a relay"""
            if relay_num not in [1, 2, 3, 4]:
                return jsonify({'error': 'Invalid relay number'}), 400

            success = self.toggle_relay(relay_num)
            if success:
                return jsonify({
                    'success': True,
                    'relay': relay_num,
                    'state': self.get_relay_state(relay_num)
                })
            else:
                return jsonify({'error': 'Failed to toggle relay'}), 500

        @blueprint.route('/relay/<int:relay_num>/set', methods=['POST'])
        def set_relay_route(relay_num):
            """Set relay state explicitly"""
            if relay_num not in [1, 2, 3, 4]:
                return jsonify({'error': 'Invalid relay number'}), 400

            data = request.get_json()
            if 'state' not in data:
                return jsonify({'error': 'State not provided'}), 400

            state = bool(data['state'])
            success = self.set_relay_state(relay_num, state)

            if success:
                return jsonify({
                    'success': True,
                    'relay': relay_num,
                    'state': state
                })
            else:
                return jsonify({'error': 'Failed to set relay state'}), 500

        @blueprint.route('/config', methods=['GET'])
        def get_config():
            """Get current configuration"""
            return jsonify(self.config)

        @blueprint.route('/config', methods=['POST'])
        def update_config():
            """Update configuration"""
            data = request.get_json()

            # Update relay names if provided
            for i in [1, 2, 3, 4]:
                name_key = f'relay{i}_name'
                if name_key in data:
                    self.config[name_key] = data[name_key]

            # Update relay types (NO/NC) if provided
            for i in [1, 2, 3, 4]:
                type_key = f'relay{i}_type'
                if type_key in data:
                    self.config[type_key] = data[type_key]

            # Update relay icons if provided
            for i in [1, 2, 3, 4]:
                icon_key = f'relay{i}_icon'
                if icon_key in data:
                    self.config[icon_key] = data[icon_key]

            # Update relay enabled state if provided
            for i in [1, 2, 3, 4]:
                enabled_key = f'relay{i}_enabled'
                if enabled_key in data:
                    self.config[enabled_key] = bool(data[enabled_key])

            # Update relay label visibility if provided
            for i in [1, 2, 3, 4]:
                show_label_key = f'relay{i}_show_label'
                if show_label_key in data:
                    self.config[show_label_key] = bool(data[show_label_key])

            # Update show_text if provided
            if 'show_text' in data:
                self.config['show_text'] = bool(data['show_text'])

            # Update GPIO pins if provided
            pins_changed = False
            for i in [1, 2, 3, 4]:
                pin_key = f'relay{i}_pin'
                if pin_key in data:
                    new_pin = int(data[pin_key])
                    # Validate pin range
                    if new_pin < 2 or new_pin > 27:
                        return jsonify({'success': False, 'message': f'Invalid GPIO pin: {new_pin}. Must be between 2 and 27.'}), 400
                    if self.config[pin_key] != new_pin:
                        self.config[pin_key] = new_pin
                        pins_changed = True

            # Check for duplicate pins (only among enabled relays)
            enabled_pins = []
            for i in [1, 2, 3, 4]:
                if self.config.get(f'relay{i}_enabled', True):
                    enabled_pins.append(self.config[f'relay{i}_pin'])
            if len(enabled_pins) != len(set(enabled_pins)):
                return jsonify({'success': False, 'message': 'Each enabled relay must use a different GPIO pin.'}), 400

            self.save_config()

            if self.socketio:
                self.socketio.emit('gpio_relay_config_updated', self.config)

            response = {'success': True, 'config': self.config}
            if pins_changed:
                response['restart_required'] = True
                response['message'] = 'GPIO pin configuration updated. Please restart ChitUI for changes to take effect.'

            return jsonify(response)

        @blueprint.route('/settings', methods=['GET'])
        def get_settings():
            """Get settings HTML"""
            settings_template = os.path.join(self.get_template_folder(), 'settings.html')
            if os.path.exists(settings_template):
                with open(settings_template, 'r') as f:
                    return f.read()
            return 'Settings template not found', 404

        # Register blueprint
        app.register_blueprint(blueprint, url_prefix='/plugin/gpio_relay_control')

        print("GPIO Relay Control plugin started")

    def register_socket_handlers(self, socketio):
        """Register Socket.IO event handlers"""
        self.socketio = socketio

        @socketio.on('gpio_relay_toggle')
        def handle_toggle(data):
            """Handle relay toggle from client"""
            relay_num = data.get('relay')
            if relay_num in [1, 2, 3, 4]:
                self.toggle_relay(relay_num)
                socketio.emit('gpio_relay_state_changed', {
                    'relay': relay_num,
                    'state': self.get_relay_state(relay_num)
                })

        @socketio.on('gpio_relay_request_status')
        def handle_status_request():
            """Send current status to requesting client"""
            socketio.emit('gpio_relay_status', self.get_all_states())

    def on_shutdown(self):
        """Called when plugin is disabled"""
        if GPIO_AVAILABLE:
            try:
                # Turn off all relays
                for i in range(1, 5):
                    if self.config.get(f'relay{i}_enabled', True):
                        self.set_relay_state(i, False)

                # Cleanup GPIO
                GPIO.cleanup()
                print("GPIO cleanup completed")
            except Exception as e:
                print(f"Error during GPIO cleanup: {e}")
