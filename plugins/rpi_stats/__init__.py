"""
Raspberry Pi Stats Plugin for ChitUI

Displays system statistics and information about the Raspberry Pi
including CPU usage, memory, temperature, disk usage, and more.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from plugins.base import ChitUIPlugin
from flask import Blueprint, jsonify
from loguru import logger
import platform
import socket

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False


class Plugin(ChitUIPlugin):
    """Raspberry Pi Stats plugin implementation"""

    def __init__(self, plugin_dir):
        super().__init__(plugin_dir)
        self.socketio = None
        self.app = None

    def get_name(self):
        return "Raspberry Pi Stats"

    def get_version(self):
        return "1.0.0"

    def get_description(self):
        return "Display Raspberry Pi system statistics and information"

    def get_author(self):
        return "ChitUI"

    def get_dependencies(self):
        """Require psutil for system monitoring"""
        return ["psutil"]

    def on_startup(self, app, socketio):
        """Initialize the plugin"""
        self.socketio = socketio
        self.app = app

        # Try to import psutil again if it wasn't available initially
        # (in case dependencies were just installed)
        global psutil, PSUTIL_AVAILABLE
        if not PSUTIL_AVAILABLE:
            try:
                import psutil
                PSUTIL_AVAILABLE = True
                logger.info("✓ psutil loaded successfully")
            except ImportError:
                logger.warning("⚠ psutil not available - some stats will not be displayed")
                logger.info("The plugin will try to install it automatically, or you can install manually:")
                logger.info("  pip3 install psutil")

        # Create Flask blueprint for plugin routes
        self.blueprint = Blueprint(
            'rpi_stats',
            __name__,
            static_folder=self.get_static_folder(),
            template_folder=self.get_template_folder()
        )

        # Register routes
        @self.blueprint.route('/system-info')
        def get_system_info():
            """Get general system information"""
            try:
                hostname = socket.gethostname()

                # Get CPU info
                cpu_count = psutil.cpu_count(logical=False) if psutil else "N/A"
                cpu_count_logical = psutil.cpu_count(logical=True) if psutil else "N/A"

                # Get memory info
                if psutil:
                    memory = psutil.virtual_memory()
                    total_memory_gb = memory.total / (1024**3)
                else:
                    total_memory_gb = "N/A"

                # Get uptime
                if psutil:
                    boot_time = psutil.boot_time()
                    import time
                    uptime_seconds = time.time() - boot_time
                    uptime_days = int(uptime_seconds // 86400)
                    uptime_hours = int((uptime_seconds % 86400) // 3600)
                    uptime_minutes = int((uptime_seconds % 3600) // 60)
                    uptime_str = f"{uptime_days}d {uptime_hours}h {uptime_minutes}m"
                else:
                    uptime_str = "N/A"

                # Get OS info
                os_info = f"{platform.system()} {platform.release()}"

                # Get Python version
                python_version = platform.python_version()

                # Try to detect if it's a Raspberry Pi
                is_rpi = False
                rpi_model = "Unknown"
                try:
                    with open('/proc/device-tree/model', 'r') as f:
                        rpi_model = f.read().strip('\x00')
                        is_rpi = 'Raspberry Pi' in rpi_model
                except:
                    pass

                return jsonify({
                    'hostname': hostname,
                    'is_raspberry_pi': is_rpi,
                    'model': rpi_model if is_rpi else platform.machine(),
                    'os': os_info,
                    'python_version': python_version,
                    'cpu_cores': cpu_count,
                    'cpu_threads': cpu_count_logical,
                    'total_memory_gb': round(total_memory_gb, 2) if isinstance(total_memory_gb, float) else total_memory_gb,
                    'uptime': uptime_str
                })
            except Exception as e:
                return jsonify({'error': str(e)}), 500

        @self.blueprint.route('/stats')
        def get_stats():
            """Get current system statistics"""
            try:
                if not psutil:
                    return jsonify({'error': 'psutil not available'}), 500

                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=0.1)
                cpu_freq = psutil.cpu_freq()
                cpu_freq_current = round(cpu_freq.current, 0) if cpu_freq else 0

                # Memory usage
                memory = psutil.virtual_memory()
                memory_percent = memory.percent
                memory_used_gb = memory.used / (1024**3)
                memory_total_gb = memory.total / (1024**3)

                # Disk usage
                disk = psutil.disk_usage('/')
                disk_percent = disk.percent
                disk_used_gb = disk.used / (1024**3)
                disk_total_gb = disk.total / (1024**3)

                # Temperature (Raspberry Pi specific)
                temperature = self.get_cpu_temperature()

                # Network I/O
                net_io = psutil.net_io_counters()
                bytes_sent_mb = net_io.bytes_sent / (1024**2)
                bytes_recv_mb = net_io.bytes_recv / (1024**2)

                # CPU per-core usage
                cpu_per_core = psutil.cpu_percent(interval=0.1, percpu=True)

                return jsonify({
                    'cpu': {
                        'percent': round(cpu_percent, 1),
                        'frequency_mhz': cpu_freq_current,
                        'per_core': [round(p, 1) for p in cpu_per_core]
                    },
                    'memory': {
                        'percent': round(memory_percent, 1),
                        'used_gb': round(memory_used_gb, 2),
                        'total_gb': round(memory_total_gb, 2)
                    },
                    'disk': {
                        'percent': round(disk_percent, 1),
                        'used_gb': round(disk_used_gb, 2),
                        'total_gb': round(disk_total_gb, 2)
                    },
                    'temperature': temperature,
                    'network': {
                        'sent_mb': round(bytes_sent_mb, 2),
                        'recv_mb': round(bytes_recv_mb, 2)
                    }
                })
            except Exception as e:
                return jsonify({'error': str(e)}), 500


    def get_cpu_temperature(self):
        """Get CPU temperature (Raspberry Pi specific)"""
        try:
            # Try Raspberry Pi thermal zone first
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = float(f.read().strip()) / 1000.0
                return round(temp, 1)
        except:
            pass

        # Try vcgencmd (Raspberry Pi)
        try:
            import subprocess
            result = subprocess.run(['vcgencmd', 'measure_temp'],
                                    capture_output=True, text=True, timeout=1)
            if result.returncode == 0:
                # Output format: temp=42.8'C
                temp_str = result.stdout.strip().split('=')[1].replace("'C", "")
                return round(float(temp_str), 1)
        except:
            pass

        # Try psutil sensors (if available)
        try:
            if psutil and hasattr(psutil, 'sensors_temperatures'):
                temps = psutil.sensors_temperatures()
                if temps:
                    # Get first available temperature
                    for name, entries in temps.items():
                        if entries:
                            return round(entries[0].current, 1)
        except:
            pass

        return None

    def get_ui_integration(self):
        """Return UI integration configuration"""
        return {
            'type': 'tab',
            'location': 'printer-info',
            'icon': 'bi-cpu',
            'title': 'System Stats',
            'template': 'rpi_stats.html'
        }
