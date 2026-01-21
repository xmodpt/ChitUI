"""
Base plugin class for ChitUI plugin system
"""

from abc import ABC, abstractmethod
from flask import Blueprint
import os


class ChitUIPlugin(ABC):
    """
    Base class that all ChitUI plugins must inherit from.

    Plugins are self-contained modules that can extend ChitUI functionality
    without modifying the core application code.
    """

    def __init__(self, plugin_dir):
        """
        Initialize the plugin.

        Args:
            plugin_dir: Absolute path to the plugin directory
        """
        self.plugin_dir = plugin_dir
        self.manifest = self.load_manifest()
        self.enabled = True
        self.blueprint = None

    @abstractmethod
    def get_name(self):
        """Return the plugin name"""
        pass

    @abstractmethod
    def get_version(self):
        """Return the plugin version"""
        pass

    def get_description(self):
        """Return the plugin description"""
        return ""

    def get_author(self):
        """Return the plugin author"""
        return "Unknown"

    def get_dependencies(self):
        """
        Return list of Python package dependencies.
        These will be installed when the plugin is enabled.

        Returns:
            List of package names (e.g., ['requests>=2.0.0', 'numpy'])
        """
        return []

    def load_manifest(self):
        """Load plugin.json manifest file"""
        import json
        manifest_path = os.path.join(self.plugin_dir, 'plugin.json')
        if os.path.exists(manifest_path):
            with open(manifest_path, 'r') as f:
                return json.load(f)
        return {}

    def install_dependencies(self):
        """Install plugin dependencies using pip"""
        import subprocess
        deps = self.get_dependencies()
        if deps:
            for dep in deps:
                try:
                    # Try with --break-system-packages first (for Debian 12+)
                    subprocess.check_call(['pip3', 'install', dep, '--break-system-packages'])
                except subprocess.CalledProcessError:
                    try:
                        # Fallback to --user if --break-system-packages fails
                        subprocess.check_call(['pip3', 'install', '--user', dep])
                    except subprocess.CalledProcessError:
                        return False
        return True

    def get_blueprint(self):
        """
        Return Flask Blueprint for this plugin's routes.
        Plugins can register their own HTTP endpoints.

        Returns:
            Flask Blueprint or None
        """
        return self.blueprint

    def get_static_folder(self):
        """Return path to plugin's static files"""
        static_path = os.path.join(self.plugin_dir, 'static')
        if os.path.exists(static_path):
            return static_path
        return None

    def get_template_folder(self):
        """Return path to plugin's templates"""
        template_path = os.path.join(self.plugin_dir, 'templates')
        if os.path.exists(template_path):
            return template_path
        return None

    def get_ui_integration(self):
        """
        Return UI integration configuration.

        Returns:
            Dict with keys:
            - 'type': 'card', 'toolbar', 'tab', or 'modal'
            - 'location': where to place the UI element
            - 'template': HTML template file name
            - 'icon': icon class (e.g., 'bi-terminal')
            - 'title': display title
        """
        return None

    def has_settings(self):
        """
        Return whether this plugin has a settings/configuration page.

        Plugins should override this to return True if they provide a settings page
        via the /plugin/<plugin_id>/settings endpoint.

        Returns:
            bool: True if plugin has settings, False otherwise
        """
        return False

    def on_startup(self, app, socketio):
        """
        Called when plugin is loaded at app startup.

        Args:
            app: Flask app instance
            socketio: SocketIO instance
        """
        pass

    def on_shutdown(self):
        """Called when plugin is disabled or app shuts down"""
        pass

    def on_printer_connected(self, printer_id, printer_info):
        """
        Called when a printer connects.

        Args:
            printer_id: Unique printer identifier
            printer_info: Dict with printer information
        """
        pass

    def on_printer_disconnected(self, printer_id):
        """Called when a printer disconnects"""
        pass

    def on_printer_message(self, printer_id, message):
        """
        Called when a message is received from a printer.

        Args:
            printer_id: Unique printer identifier
            message: Message data
        """
        pass

    def register_socket_handlers(self, socketio):
        """
        Register custom SocketIO event handlers.

        Args:
            socketio: SocketIO instance
        """
        pass
