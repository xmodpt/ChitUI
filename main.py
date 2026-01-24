"""
ChitUI Plus - Web Interface for Chitu 3D Printer Control

This is the main Flask application that provides a web-based interface for controlling
Chitu-based 3D printers. It supports both network and USB gadget modes for file transfer,
real-time monitoring via WebSocket connections, and a plugin system for extensibility.

Features:
- Automatic printer discovery via UDP broadcast
- File upload/management (both network and USB gadget mode)
- Real-time printer status monitoring via WebSocket
- Print control (start, pause, resume, stop)
- Temperature monitoring and control
- Plugin system for extensibility (GPIO relays, cameras, etc.)
- User authentication and settings management

Architecture:
- Flask: Web framework for HTTP endpoints
- Flask-SocketIO: Real-time bidirectional communication with web clients
- WebSocket: Direct communication with printer's SDCP protocol
- Threading: Concurrent handling of printer connections and monitoring

Configuration:
- PORT: Web server port (default: 8080)
- USB_GADGET_PATH: Path to USB gadget mount point (default: /mnt/usb_share)
- ENABLE_USB_GADGET: Enable/disable USB gadget mode (default: true)
- USB_AUTO_REFRESH: Auto-refresh USB after upload (default: false)
- DEBUG: Enable debug logging (default: false)

Author: ChitUI Developer
License: MIT
"""

# ===== Core Flask and Web Framework Imports =====
from flask import Flask, Response, request, stream_with_context, jsonify, send_file, render_template_string, session, redirect
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO
from functools import wraps

# ===== System and Utility Imports =====
from threading import Thread
from loguru import logger
import socket
import json
import os
import websocket
import time
import sys
import requests
import hashlib
import uuid
import threading
import subprocess
from pathlib import Path
import struct
from PIL import Image
import shutil

# ===== Plugin System Imports =====
from plugins import PluginManager

# ===== Optional Camera Support =====
# Camera support is optional - requires opencv-python-headless package
# Used by the IP camera plugin for viewing network cameras
try:
    import cv2
    CAMERA_SUPPORT = True
except ImportError:
    CAMERA_SUPPORT = False
    logger.warning("Camera support not available - install opencv-python-headless")


# ========================================================================
# APPLICATION INITIALIZATION AND CONFIGURATION
# ========================================================================

# ===== Logging Configuration =====
# Configure loguru logger for structured logging with color support
debug = False
log_level = "INFO"
if os.environ.get("DEBUG"):
    debug = True
    log_level = "DEBUG"

logger.remove()  # Remove default handler
logger.add(sys.stdout, colorize=debug, level=log_level)

# ===== Web Server Configuration =====
# Port for the web interface (can be overridden via PORT environment variable)
port = 8080
if os.environ.get("PORT") is not None:
    port = int(os.environ.get("PORT"))

# ===== Flask Application Setup =====
# Initialize Flask app with static files served from 'web' directory
discovery_timeout = 1  # Timeout in seconds for printer discovery
app = Flask(__name__,
            static_url_path='',
            static_folder='web')

# Secret key for sessions - generate unique one on first run or use environment variable
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())

# ===== WebSocket and Real-time Communication Setup =====
# SocketIO for real-time bidirectional communication with web clients
# async_mode='threading' allows concurrent handling of multiple connections
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

# Global state management
websockets = {}  # Dictionary to store active WebSocket connections to printers {printer_id: ws_connection}
printers = {}    # Dictionary to store discovered printers {printer_id: printer_info}

# ===== Plugin System Initialization =====
# Load and initialize plugins from the 'plugins' directory
# Plugins can extend functionality (GPIO control, cameras, monitoring, etc.)
plugin_manager = PluginManager(os.path.join(os.path.dirname(__file__), 'plugins'))

# ========================================================================
# STORAGE AND FILE UPLOAD CONFIGURATION
# ========================================================================
#
# ChitUI supports two file upload modes:
#
# 1. USB Gadget Mode (Recommended for Raspberry Pi):
#    - Emulates a USB flash drive that the printer can access directly
#    - Files saved to /mnt/usb_share appear on the printer as USB storage
#    - Requires USB OTG configuration and proper kernel modules (dwc2, g_mass_storage)
#    - Faster and more reliable than network uploads
#
# 2. Network Upload Mode (Fallback):
#    - Files uploaded directly to printer via SDCP protocol
#    - Works over WiFi/Ethernet connection to printer
#    - Used automatically if USB gadget is not available or disabled
#
# ========================================================================

# USB Gadget folder - mount point for the virtual USB drive
# This folder is exposed to the printer as USB storage
USB_GADGET_FOLDER = os.environ.get('USB_GADGET_PATH', '/mnt/usb_share')

# USB Gadget Master Switch
# Set ENABLE_USB_GADGET='false' to completely disable USB gadget mode
# Useful if USB gadget causes printer crashes or stability issues
ENABLE_USB_GADGET = os.environ.get('ENABLE_USB_GADGET', 'true').lower() not in ['0', 'false', 'no', 'off']

# USB Gadget Auto-Refresh Configuration
# When enabled, automatically triggers USB reconnect after file upload
# This forces the printer to detect new/changed files immediately
# WARNING: Can cause printer crashes on some models - disable if experiencing issues
# Set USB_AUTO_REFRESH='false' to disable and refresh manually
USB_AUTO_REFRESH = os.environ.get('USB_AUTO_REFRESH', 'false').lower() not in ['0', 'false', 'no', 'off']

# Runtime USB Gadget Status
# These variables track whether USB gadget is actually available and working
USE_USB_GADGET = False      # Will be set to True if USB gadget is available and writable
USB_GADGET_ERROR = None     # Will contain error message if USB gadget fails

if not ENABLE_USB_GADGET:
    USB_GADGET_ERROR = "USB Gadget mode manually disabled (ENABLE_USB_GADGET=false). Using network upload only."
    logger.warning(f"⚠ {USB_GADGET_ERROR}")
    logger.info("ℹ All files will be uploaded directly to printer via network")
    USE_USB_GADGET = False
elif os.path.exists(USB_GADGET_FOLDER):
    # Test if writable
    test_file = os.path.join(USB_GADGET_FOLDER, '.write_test')
    try:
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        logger.info(f"✓ USB gadget found and writable at {USB_GADGET_FOLDER}")
        UPLOAD_FOLDER = USB_GADGET_FOLDER
        USE_USB_GADGET = True
    except PermissionError as e:
        USB_GADGET_ERROR = f"Permission denied - USB gadget folder is not writable. Check permissions: sudo chmod 777 {USB_GADGET_FOLDER}"
        logger.error(f"✗ {USB_GADGET_ERROR}")
        logger.warning("⚠ Files will be uploaded directly to printer via network instead")
        USE_USB_GADGET = False
    except OSError as e:
        USB_GADGET_ERROR = f"USB gadget folder exists but cannot be used: {e}"
        logger.error(f"✗ {USB_GADGET_ERROR}")
        logger.warning("⚠ Files will be uploaded directly to printer via network instead")
        USE_USB_GADGET = False
else:
    USB_GADGET_ERROR = f"USB gadget not found at {USB_GADGET_FOLDER}. To enable USB gadget mode, create this folder and mount your USB gadget device there."
    logger.warning(f"⚠ {USB_GADGET_ERROR}")
    logger.info("ℹ Files will be uploaded directly to printer via network")
    USE_USB_GADGET = False

# Data folder for settings - use fixed location in project directory
# This ensures settings persist regardless of which user runs the app
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(PROJECT_ROOT, 'data')
if not USE_USB_GADGET:
    UPLOAD_FOLDER = os.path.join(DATA_FOLDER, 'uploads')
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'ctb', 'goo', 'prz'}
SETTINGS_FILE = os.path.join(DATA_FOLDER, 'chitui_settings.json')

# Create directories if they don't exist
os.makedirs(DATA_FOLDER, exist_ok=True)

# ========================================================================
# NEW: FILE MANAGEMENT WITH THUMBNAILS
# ========================================================================
# New folder structure for better organization:
# - temp/: Temporary upload staging (for safe processing)
# - thumbnails/: Extracted thumbnail images
# - file_associations.json: Database mapping files to thumbnails
# Note: Files themselves are stored in their final destinations (/local/ or /mnt/usb_share/), not duplicated

TEMP_FOLDER = os.path.join(DATA_FOLDER, 'temp')
THUMBNAILS_FOLDER = os.path.join(DATA_FOLDER, 'thumbnails')
DATABASE_FILE = os.path.join(DATA_FOLDER, 'file_associations.json')

# Create folders
os.makedirs(TEMP_FOLDER, exist_ok=True)
os.makedirs(THUMBNAILS_FOLDER, exist_ok=True)

logger.info(f"Data folder: {DATA_FOLDER}")
logger.info(f"Temp folder: {TEMP_FOLDER}")
logger.info(f"Thumbnails folder: {THUMBNAILS_FOLDER}")
logger.info(f"Upload folder: {UPLOAD_FOLDER}")
logger.info(f"Settings file: {SETTINGS_FILE}")
logger.info(f"Running as user: {os.getenv('USER', 'unknown')} (UID: {os.getuid()})")
# ===== END CONFIG =====


# ========================================================================
# FILE DATABASE AND THUMBNAIL EXTRACTION CLASSES
# ========================================================================

class FileDatabase:
    """Simple JSON database to manage file-thumbnail associations."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """Create database file if it doesn't exist."""
        if not self.db_path.exists():
            self._save_data({})

    def _load_data(self) -> dict:
        """Load database from JSON file."""
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except:
            return {}

    def _save_data(self, data: dict):
        """Save database to JSON file."""
        with open(self.db_path, 'w') as f:
            json.dump(data, f, indent=2)

    def add_file(self, filename: str, thumbnail_small: str, thumbnail_big: str):
        """Add or update a file entry with its thumbnails."""
        from datetime import datetime
        data = self._load_data()
        data[filename] = {
            'thumbnail_small': thumbnail_small,
            'thumbnail_big': thumbnail_big,
            'uploaded_at': datetime.now().isoformat()
        }
        self._save_data(data)

    def get_file(self, filename: str) -> dict:
        """Get file information by filename."""
        data = self._load_data()
        return data.get(filename, {})

    def delete_file(self, filename: str):
        """Remove file entry from database."""
        data = self._load_data()
        if filename in data:
            del data[filename]
            self._save_data(data)

    def get_all_files(self) -> dict:
        """Get all file entries."""
        return self._load_data()


class CtbThumbnailExtractor:
    """Extracts thumbnail images from .ctb files (Chitubox format)."""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)

        if not self.filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

    def _decode_rgb565(self, pixel: int):
        """Decode RGB565 to RGB888."""
        red = ((pixel >> 11) & 0x1F) * 255 // 31
        green = ((pixel >> 5) & 0x3F) * 255 // 63
        blue = (pixel & 0x1F) * 255 // 31
        return (red, green, blue)

    def _extract_preview_image(self, data: bytes, width: int, height: int):
        """Extract preview image from RGB565 data."""
        expected_size = width * height * 2
        if len(data) < expected_size:
            raise ValueError(f"Insufficient data")

        img = Image.new('RGB', (width, height))
        pixels = []

        for i in range(0, expected_size, 2):
            # CTB uses little-endian
            pixel = struct.unpack('<H', data[i:i+2])[0]
            pixels.append(self._decode_rgb565(pixel))

        img.putdata(pixels)
        return img

    def _detect_orientation(self, img: Image.Image) -> int:
        """Detect if image needs rotation."""
        width, height = img.size
        pixels = list(img.getdata())

        try:
            # Calculate edge content
            top_sum = sum((r + g + b) for r, g, b in [pixels[x] for x in range(min(width, len(pixels)))])
            top_edge = top_sum / width if width > 0 else 0

            bottom_start = (height - 1) * width
            bottom_sum = sum((r + g + b) for r, g, b in [pixels[min(bottom_start + x, len(pixels)-1)] for x in range(width)])
            bottom_edge = bottom_sum / width if width > 0 else 0

            left_sum = sum((r + g + b) for r, g, b in [pixels[min(y * width, len(pixels)-1)] for y in range(height)])
            left_edge = left_sum / height if height > 0 else 0

            right_sum = sum((r + g + b) for r, g, b in [pixels[min(y * width + width - 1, len(pixels)-1)] for y in range(height)])
            right_edge = right_sum / height if height > 0 else 0

            vertical_edge_content = (left_edge + right_edge) / 2
            horizontal_edge_content = (top_edge + bottom_edge) / 2

            if vertical_edge_content > horizontal_edge_content * 1.1:
                return 270
        except:
            pass

        return 0

    def _smart_rotate(self, img: Image.Image) -> Image.Image:
        """Rotate image if needed."""
        rotation = self._detect_orientation(img)
        if rotation == 270:
            return img.rotate(90, expand=True)
        return img

    def extract_thumbnails(self, output_dir: str = None):
        """Extract thumbnails from CTB file."""
        with open(self.filepath, 'rb') as f:
            # Read CTB header
            magic = struct.unpack('<I', f.read(4))[0]
            version = struct.unpack('<I', f.read(4))[0]

            # Skip to preview offsets (at offset 8)
            f.seek(8)
            preview_small_offset = struct.unpack('<I', f.read(4))[0]
            preview_large_offset = struct.unpack('<I', f.read(4))[0]

            # Extract small preview
            f.seek(preview_small_offset)
            small_width = struct.unpack('<I', f.read(4))[0]
            small_height = struct.unpack('<I', f.read(4))[0]
            small_data_size = struct.unpack('<I', f.read(4))[0]
            small_data = f.read(small_data_size)

            small_preview = self._extract_preview_image(small_data, small_width, small_height)

            # Extract large preview
            f.seek(preview_large_offset)
            large_width = struct.unpack('<I', f.read(4))[0]
            large_height = struct.unpack('<I', f.read(4))[0]
            large_data_size = struct.unpack('<I', f.read(4))[0]
            large_data = f.read(large_data_size)

            big_preview = self._extract_preview_image(large_data, large_width, large_height)

        # Rotate if needed
        small_preview = self._smart_rotate(small_preview)
        big_preview = self._smart_rotate(big_preview)

        # Save if output directory specified
        if output_dir:
            output_path = Path(output_dir)
            base_name = self.filepath.stem
            small_path = output_path / f"{base_name}_small.png"
            big_path = output_path / f"{base_name}_big.png"

            small_preview.save(small_path)
            big_preview.save(big_path)

        return small_preview, big_preview


class GooThumbnailExtractor:
    """Extracts thumbnail images from .goo files."""

    VERSION_SIZE = 4
    SOFTWARE_INFO_SIZE = 32
    SOFTWARE_VERSION_SIZE = 24
    FILE_TIME_SIZE = 24
    PRINTER_NAME_SIZE = 32
    PRINTER_TYPE_SIZE = 32
    PROFILE_NAME_SIZE = 32

    SMALL_PREVIEW_SIZE = (116, 116)
    BIG_PREVIEW_SIZE = (290, 290)

    def __init__(self, filepath: str, offset_adjustment: int = 8):
        self.filepath = Path(filepath)
        self.offset_adjustment = offset_adjustment

        if not self.filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

    def _calculate_preview_offset(self) -> int:
        offset = 0
        offset += self.VERSION_SIZE
        offset += self.SOFTWARE_INFO_SIZE
        offset += self.SOFTWARE_VERSION_SIZE
        offset += self.FILE_TIME_SIZE
        offset += self.PRINTER_NAME_SIZE
        offset += self.PRINTER_TYPE_SIZE
        offset += self.PROFILE_NAME_SIZE
        offset += 2 + 2 + 2
        offset += self.offset_adjustment
        return offset

    def _decode_rgb565(self, pixel: int):
        red = ((pixel >> 11) & 0x1F) * 255 // 31
        green = ((pixel >> 5) & 0x3F) * 255 // 63
        blue = (pixel & 0x1F) * 255 // 31
        return (red, green, blue)

    def _extract_preview_image(self, data: bytes, width: int, height: int,
                               endian: str = '>'):
        expected_size = width * height * 2
        if len(data) < expected_size:
            raise ValueError(f"Insufficient data")

        img = Image.new('RGB', (width, height))
        pixels = []

        for i in range(0, expected_size, 2):
            pixel = struct.unpack(f'{endian}H', data[i:i+2])[0]
            pixels.append(self._decode_rgb565(pixel))

        img.putdata(pixels)
        return img

    def _detect_orientation(self, img: Image.Image) -> int:
        width, height = img.size

        # Even if image is square, check if CONTENT inside is landscape/portrait
        pixels = list(img.getdata())

        try:
            # Calculate edge content
            top_sum = 0
            for x in range(width):
                if x < len(pixels):
                    r, g, b = pixels[x]
                    top_sum += r + g + b
            top_edge = top_sum / width if width > 0 else 0

            bottom_start = (height - 1) * width
            bottom_sum = 0
            for x in range(width):
                idx = bottom_start + x
                if idx < len(pixels):
                    r, g, b = pixels[idx]
                    bottom_sum += r + g + b
            bottom_edge = bottom_sum / width if width > 0 else 0

            left_sum = 0
            for y in range(height):
                idx = y * width
                if idx < len(pixels):
                    r, g, b = pixels[idx]
                    left_sum += r + g + b
            left_edge = left_sum / height if height > 0 else 0

            right_sum = 0
            for y in range(height):
                idx = y * width + (width - 1)
                if idx < len(pixels):
                    r, g, b = pixels[idx]
                    right_sum += r + g + b
            right_edge = right_sum / height if height > 0 else 0

            # Calculate content distribution
            vertical_edge_content = (left_edge + right_edge) / 2
            horizontal_edge_content = (top_edge + bottom_edge) / 2

            # If more content on vertical edges, rotate to portrait
            if vertical_edge_content > horizontal_edge_content * 1.1:
                return 270
        except Exception as e:
            pass

        return 0

    def _smart_rotate(self, img: Image.Image) -> Image.Image:
        rotation = self._detect_orientation(img)

        if rotation == 270:
            return img.rotate(90, expand=True)
        return img

    def extract_thumbnails(self, output_dir: str = None):
        actual_offset = self._calculate_preview_offset()
        endian = '>'

        with open(self.filepath, 'rb') as f:
            f.seek(actual_offset)

            small_size = self.SMALL_PREVIEW_SIZE[0] * self.SMALL_PREVIEW_SIZE[1] * 2
            small_data = f.read(small_size)
            small_preview = self._extract_preview_image(
                small_data,
                self.SMALL_PREVIEW_SIZE[0],
                self.SMALL_PREVIEW_SIZE[1],
                endian
            )

            big_size = self.BIG_PREVIEW_SIZE[0] * self.BIG_PREVIEW_SIZE[1] * 2
            big_data = f.read(big_size)
            big_preview = self._extract_preview_image(
                big_data,
                self.BIG_PREVIEW_SIZE[0],
                self.BIG_PREVIEW_SIZE[1],
                endian
            )

        small_preview = self._smart_rotate(small_preview)
        big_preview = self._smart_rotate(big_preview)

        if output_dir:
            output_path = Path(output_dir)
            base_name = self.filepath.stem
            small_path = output_path / f"{base_name}_small.png"
            big_path = output_path / f"{base_name}_big.png"

            # Save the rotated images
            small_preview.save(small_path)
            big_preview.save(big_path)

        return small_preview, big_preview


# Initialize database
file_db = FileDatabase(DATABASE_FILE)


def extract_thumbnail_for_file(filepath: Path, output_to_thumbnails: bool = True):
    """
    Extract thumbnail for a specific file (GOO or CTB).

    Returns: (success, small_thumbnail_filename, big_thumbnail_filename)
    """
    if not filepath.exists():
        return False, '', ''

    try:
        # Determine output directory
        if output_to_thumbnails:
            output_dir = THUMBNAILS_FOLDER
        else:
            output_dir = str(filepath.parent)

        # Determine file type and use appropriate extractor
        if filepath.suffix.lower() == '.goo':
            extractor = GooThumbnailExtractor(str(filepath))
        elif filepath.suffix.lower() == '.ctb':
            extractor = CtbThumbnailExtractor(str(filepath))
        elif filepath.suffix.lower() == '.prz':
            # PRZ files don't have embedded thumbnails
            return False, '', ''
        else:
            return False, '', ''

        extractor.extract_thumbnails(output_dir=output_dir)

        # Return thumbnail filenames
        small_thumb = f"{filepath.stem}_small.png"
        big_thumb = f"{filepath.stem}_big.png"

        return True, small_thumb, big_thumb
    except Exception as e:
        logger.error(f"Error extracting thumbnail: {e}")
        import traceback
        traceback.print_exc()
        return False, '', ''

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# ========================================================================
# USB GADGET HELPER FUNCTIONS
# ========================================================================
#
# These functions manage the USB gadget interface that emulates a USB flash drive
# for the printer. The main challenge is forcing the printer to detect file changes
# after uploading new files to the virtual USB drive.
#
# ========================================================================

def trigger_usb_gadget_refresh():
    """
    Trigger USB gadget to refresh/reconnect so printer detects new/changed files.

    This function attempts multiple methods to force a USB re-enumeration:
    1. Filesystem sync to ensure all data is written
    2. ConfigFS UDC disconnect/reconnect (preferred method)
    3. Fallback to /sys/class/udc interface
    4. Module reload as last resort

    The reconnect causes the printer to see the USB drive as "ejected and re-inserted",
    triggering a file list refresh.

    Returns:
        bool: True if refresh was successful, False otherwise

    Note:
        Requires root permissions to write to UDC control files.
        Some printers may crash during reconnect - use USB_AUTO_REFRESH=false if this occurs.
    """
    if not USE_USB_GADGET:
        logger.warning("USB gadget is not enabled, skipping refresh")
        return False

    try:
        # Method 1: Ensure all data is written to disk
        os.sync()
        logger.debug("Synced filesystem")

        # Method 2: Try to find and use configfs UDC paths
        configfs_gadget_dirs = []
        configfs_base = '/sys/kernel/config/usb_gadget'

        if os.path.exists(configfs_base):
            try:
                configfs_gadget_dirs = [os.path.join(configfs_base, d) for d in os.listdir(configfs_base)]
            except:
                pass

        # Add known UDC paths
        gadget_paths = []
        for gadget_dir in configfs_gadget_dirs:
            udc_path = os.path.join(gadget_dir, 'UDC')
            if os.path.exists(udc_path):
                gadget_paths.append(udc_path)

        # Also try common hardcoded paths
        gadget_paths.extend([
            '/sys/kernel/config/usb_gadget/pi4/UDC',
            '/sys/kernel/config/usb_gadget/mass_storage/UDC',
            '/sys/kernel/config/usb_gadget/g1/UDC',
        ])

        for udc_path in gadget_paths:
            if os.path.exists(udc_path):
                try:
                    # Read current UDC value
                    with open(udc_path, 'r') as f:
                        udc_value = f.read().strip()

                    if udc_value and udc_value != '' and udc_value != 'none':
                        # Disconnect and reconnect
                        logger.info(f"Attempting USB gadget reconnect via {udc_path}")

                        # Disconnect
                        with open(udc_path, 'w') as f:
                            f.write('')
                        time.sleep(0.5)

                        # Reconnect
                        with open(udc_path, 'w') as f:
                            f.write(udc_value)

                        logger.info("✓ USB gadget reconnected successfully")
                        return True
                except PermissionError:
                    logger.warning(f"No permission to write to {udc_path}")
                    logger.info("💡 Run ChitUI as root: sudo python3 main.py")
                except Exception as e:
                    logger.debug(f"Could not use {udc_path}: {e}")

        # Method 3: Try to find UDC via /sys/class/udc
        udc_class_dir = '/sys/class/udc'
        if os.path.exists(udc_class_dir):
            try:
                udcs = os.listdir(udc_class_dir)
                if udcs:
                    logger.info(f"Found UDC controllers: {udcs}")
                    logger.info("⚠ UDC found but cannot trigger refresh without configfs access")
            except:
                pass

        # Method 4: Try forced_eject for g_mass_storage (triggers media change notification)
        forced_eject_path = '/sys/module/g_mass_storage/parameters/forced_eject'
        if os.path.exists(forced_eject_path):
            try:
                logger.info("Using g_mass_storage forced_eject to trigger refresh...")
                with open(forced_eject_path, 'w') as f:
                    f.write('1')
                time.sleep(0.5)
                with open(forced_eject_path, 'w') as f:
                    f.write('0')
                logger.info("✓ USB gadget media change signaled")
                return True
            except PermissionError:
                logger.warning(f"No permission to write to {forced_eject_path}")
                logger.info("💡 Run ChitUI as root: sudo python3 main.py")
            except Exception as e:
                logger.debug(f"Could not use forced_eject: {e}")

        # Method 5: Try legacy g_mass_storage module reload
        mass_storage_params = '/sys/module/g_mass_storage/parameters'
        if os.path.exists(mass_storage_params):
            logger.info("Detected legacy g_mass_storage module - attempting reload...")
            try:
                # Read the current module parameters
                file_param = os.path.join(mass_storage_params, 'file')
                if os.path.exists(file_param):
                    with open(file_param, 'r') as f:
                        usb_file = f.read().strip()

                    logger.info(f"USB file: {usb_file}")

                    # Find modprobe executable
                    modprobe_cmd = None
                    for path in ['/sbin/modprobe', '/usr/sbin/modprobe', 'modprobe']:
                        try:
                            result = subprocess.run([path, '--version'], capture_output=True, timeout=2)
                            if result.returncode == 0:
                                modprobe_cmd = path
                                break
                        except (FileNotFoundError, subprocess.TimeoutExpired):
                            continue

                    if not modprobe_cmd:
                        logger.error("modprobe command not found in /sbin, /usr/sbin, or PATH")
                        return False

                    # Reload the module to trigger reconnection
                    logger.info("Unloading g_mass_storage module...")
                    subprocess.run([modprobe_cmd, '-r', 'g_mass_storage'], check=False, capture_output=True)
                    time.sleep(1)

                    logger.info("Reloading g_mass_storage module...")
                    # Use the parameters from your virtual_usb_gadget_fixed.sh
                    result = subprocess.run([
                        modprobe_cmd, 'g_mass_storage',
                        f'file={usb_file}',
                        'stall=0',
                        'ro=0',
                        'removable=1',
                        'idVendor=0x0951',
                        'idProduct=0x1666',
                        'iManufacturer=Kingston',
                        'iProduct=DataTraveler',
                        'iSerialNumber=74A53CDF'
                    ], check=False, capture_output=True)

                    if result.returncode == 0:
                        logger.info("✓ USB gadget module reloaded successfully")
                        return True
                    else:
                        logger.warning(f"Failed to reload module: {result.stderr.decode()}")
                        return False

            except PermissionError:
                logger.warning("No permission to reload g_mass_storage module")
                logger.info("💡 Run ChitUI as root: sudo python3 main.py")
                return False
            except Exception as e:
                logger.error(f"Error reloading g_mass_storage: {e}")
                return False

        # No method worked
        logger.info("⚠ Could not trigger USB gadget reconnect - printer will need to poll for changes")
        logger.info("💡 Options:")
        logger.info("   1. Run ChitUI as root: sudo python3 main.py")
        logger.info("   2. Manually refresh on printer screen")
        logger.info("   3. Reconnect USB cable between Pi and printer")
        return False

    except Exception as e:
        logger.error(f"Error triggering USB gadget refresh: {e}")
        return False

# ===== END USB GADGET HELPERS =====


# Camera globals
camera_stream_active = False
camera_latest_frame = None
camera_frame_lock = threading.Lock()
camera_instance = None
camera_printer_ip = None


# ============ CAMERA CLASSES ============

class RTSPCamera:
    def __init__(self, printer_ip):
        self.rtsp_url = f"rtsp://{printer_ip}:554/video"
        self.cap = None
        self.running = False
        
    def start(self):
        self.running = True
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;udp'
        
        logger.info(f"Connecting to camera: {self.rtsp_url}")
        
        try:
            self.cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            
            if not self.cap.isOpened():
                logger.error("Failed to open camera stream")
                return False
            
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
            # Try to read first frame
            for i in range(10):
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    logger.info(f"Camera connected: {frame.shape}")
                    return True
                time.sleep(0.5)
            
            logger.error("No frames received from camera")
            return False
            
        except Exception as e:
            logger.error(f"Camera error: {e}")
            return False
    
    def read(self):
        if not self.cap or not self.running:
            return False, None
        
        # Skip frames to reduce latency
        for _ in range(3):
            self.cap.grab()
        
        ret, frame = self.cap.retrieve()
        return ret, frame
    
    def stop(self):
        self.running = False
        try:
            if self.cap:
                self.cap.release()
                self.cap = None
        except Exception as e:
            logger.error(f"Error releasing camera: {e}")


def camera_capture_frames():
    global camera_latest_frame, camera_stream_active, camera_instance
    
    logger.info("Camera capture thread started")
    frame_count = 0
    
    while camera_stream_active and camera_instance:
        try:
            ret, frame = camera_instance.read()
            
            if ret and frame is not None:
                # Resize for web streaming
                frame = cv2.resize(frame, (640, 480))
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                
                if ret:
                    with camera_frame_lock:
                        camera_latest_frame = buffer.tobytes()
                    frame_count += 1
                    
        except Exception as e:
            logger.error(f"Camera capture error: {e}")
            break
    
    logger.info(f"Camera capture stopped. Total frames: {frame_count}")


def camera_generate():
    global camera_latest_frame, camera_stream_active
    
    last_frame = None
    
    while camera_stream_active:
        with camera_frame_lock:
            frame = camera_latest_frame
        
        if frame and frame != last_frame:
            last_frame = frame
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            time.sleep(0.01)


# ============ CAMERA ROUTES ============

@app.route('/camera/start', methods=['POST'])
def camera_start():
    global camera_stream_active, camera_instance, camera_latest_frame, camera_printer_ip
    
    if not CAMERA_SUPPORT:
        return jsonify({'ok': False, 'msg': 'Camera support not installed. Run: pip install opencv-python-headless'})
    
    if camera_stream_active:
        return jsonify({'ok': False, 'msg': 'Camera already running'})
    
    try:
        # Get printer IP from first available printer or use saved printer
        if not printers:
            return jsonify({'ok': False, 'msg': 'No printers connected'})
        
        # Use the first printer's IP
        first_printer = next(iter(printers.values()))
        camera_printer_ip = first_printer['ip']
        
        logger.info(f"Starting camera for printer: {camera_printer_ip}")
        
        camera_latest_frame = None
        camera_instance = RTSPCamera(camera_printer_ip)
        
        if camera_instance.start():
            camera_stream_active = True
            Thread(target=camera_capture_frames, daemon=True).start()
            time.sleep(1)  # Give it a moment to capture first frame
            return jsonify({'ok': True})
        else:
            camera_stream_active = False
            camera_instance = None
            return jsonify({'ok': False, 'msg': 'Could not connect to camera. Is the printer printing?'})
            
    except Exception as e:
        logger.error(f"Error starting camera: {e}")
        camera_stream_active = False
        camera_instance = None
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/camera/stop', methods=['POST'])
def camera_stop():
    global camera_stream_active, camera_instance, camera_latest_frame
    
    try:
        # Stop the stream first
        camera_stream_active = False
        
        # Give the camera thread a moment to stop
        time.sleep(0.2)
        
        camera_latest_frame = None
        
        if camera_instance:
            try:
                camera_instance.stop()
            except Exception as e:
                logger.error(f"Error stopping camera instance: {e}")
            camera_instance = None
        
        logger.info("Camera stopped")
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f"Error in camera_stop: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/camera/video')
def camera_video():
    if not camera_stream_active:
        return Response('Camera not active', status=404)
    return Response(camera_generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/thumbnail/<printer_id>')
def proxy_thumbnail(printer_id):
    """Proxy thumbnail images from printer to avoid CORS issues"""
    try:
        thumbnail_url = request.args.get('url')
        if not thumbnail_url:
            return Response('No thumbnail URL provided', status=400)

        # Fetch the thumbnail from the printer
        import requests
        response = requests.get(thumbnail_url, timeout=10)

        if response.status_code == 200:
            # Return the image with appropriate content type
            content_type = response.headers.get('Content-Type', 'image/bmp')
            return Response(response.content, mimetype=content_type)
        else:
            logger.error(f"Failed to fetch thumbnail: {response.status_code}")
            return Response('Failed to fetch thumbnail', status=response.status_code)
    except Exception as e:
        logger.error(f"Error proxying thumbnail: {e}")
        return Response(f'Error: {str(e)}', status=500)


# ============ SETTINGS FUNCTIONS ============

def migrate_old_settings():
    """Migrate settings from old user home directory location to new project directory"""
    # Check if settings already exist in new location
    if os.path.exists(SETTINGS_FILE):
        return  # Already migrated or using new location

    # Check old locations for existing settings
    old_locations = [
        os.path.expanduser('~/.chitui/chitui_settings.json'),  # Current user
        '/home/user/.chitui/chitui_settings.json',              # user account
        '/root/.chitui/chitui_settings.json'                    # root account
    ]

    for old_path in old_locations:
        if os.path.exists(old_path):
            try:
                logger.info(f"Migrating settings from {old_path} to {SETTINGS_FILE}")
                with open(old_path, 'r') as f:
                    settings = json.load(f)

                # Save to new location
                with open(SETTINGS_FILE, 'w') as f:
                    json.dump(settings, f, indent=2)

                logger.info(f"✓ Settings migrated successfully from {old_path}")
                logger.info(f"  - {len(settings.get('printers', {}))} printers")
                logger.info(f"  - Auth configured: {'auth' in settings}")
                return
            except Exception as e:
                logger.error(f"Error migrating settings from {old_path}: {e}")

    logger.info("No existing settings found to migrate")

def load_settings():
    """Load settings from persistent storage"""
    # Try to migrate old settings first
    migrate_old_settings()

    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                logger.info(f"Loaded settings: {len(settings.get('printers', {}))} printers configured")
                return settings
        except Exception as e:
            logger.error(f"Error loading settings: {e}")
    return {"printers": {}, "auto_discover": False}


def save_settings(settings):
    """Save settings to persistent storage"""
    try:
        # Ensure data folder exists
        os.makedirs(DATA_FOLDER, exist_ok=True)

        # Write to temp file first, then rename (atomic operation)
        temp_file = SETTINGS_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(settings, f, indent=2)

        # Atomic rename to prevent corruption
        os.replace(temp_file, SETTINGS_FILE)
        logger.info(f"Settings saved successfully to {SETTINGS_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


# ============ AUTHENTICATION ============

def init_auth():
    """Initialize authentication with default admin user"""
    settings = load_settings()

    # Check if auth exists and is properly configured
    auth_missing = 'auth' not in settings
    auth_incomplete = 'auth' in settings and 'password_hash' not in settings.get('auth', {})

    if auth_missing or auth_incomplete:
        if auth_incomplete:
            logger.error("Auth configuration is incomplete/corrupt - reinitializing")

        # Create default admin user with default password 'admin'
        settings['auth'] = {
            'password_hash': generate_password_hash('admin'),
            'require_password_change': True,
            'session_timeout': 0  # 0 = no timeout, otherwise seconds
        }

        success = save_settings(settings)
        if not success:
            logger.error("CRITICAL: Failed to save auth settings!")
        else:
            logger.warning("⚠ Default admin password set to 'admin'. Please change it on first login!")

    elif 'session_timeout' not in settings.get('auth', {}):
        # Add session_timeout to existing auth config
        settings['auth']['session_timeout'] = 0
        save_settings(settings)

    # Verify auth is properly configured
    auth = settings.get('auth', {})
    if 'password_hash' in auth:
        logger.info("✓ Authentication initialized successfully")
    else:
        logger.error("✗ Authentication initialization FAILED - password_hash missing!")

    return auth


def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            if request.path.startswith('/auth/'):
                return f(*args, **kwargs)
            return jsonify({'error': 'Authentication required'}), 401

        # Check session timeout
        auth = load_settings().get('auth', {})
        session_timeout = auth.get('session_timeout', 0)

        if session_timeout > 0:  # 0 means no timeout
            last_activity = session.get('last_activity')
            current_time = time.time()

            if last_activity:
                time_elapsed = current_time - last_activity
                if time_elapsed > session_timeout:
                    session.clear()
                    logger.info(f"Session expired after {session_timeout} seconds of inactivity")
                    if request.path.endswith('.html') or request.path == '/':
                        return redirect('/')
                    return jsonify({'error': 'Session expired'}), 401

            # Update last activity time
            session['last_activity'] = current_time

        # Check if password change is required
        if auth.get('require_password_change') and request.path != '/change-password.html' and not request.path.startswith('/auth/'):
            if request.path.endswith('.html') or request.path == '/':
                return redirect('/change-password.html')
            return jsonify({'error': 'Password change required'}), 403

        return f(*args, **kwargs)
    return decorated_function


# ============ WEB ROUTES ============

@app.after_request
def add_no_cache_headers(response):
    """Add no-cache headers to JavaScript and CSS files to prevent caching issues"""
    if request.path.endswith(('.js', '.css', '.html')):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# ============ AUTHENTICATION ROUTES ============

@app.route('/auth/login', methods=['POST'])
def auth_login():
    """Handle login requests"""
    try:
        data = request.json
        password = data.get('password', '')

        settings = load_settings()
        auth = settings.get('auth', {})

        # Check if auth is properly configured with password_hash
        if not auth or 'password_hash' not in auth:
            logger.error("Auth configuration is missing or corrupt - reinitializing")
            # Reinitialize auth
            init_auth()
            # Reload settings
            settings = load_settings()
            auth = settings.get('auth', {})

            if not auth or 'password_hash' not in auth:
                logger.error("Failed to reinitialize auth")
                return jsonify({'success': False, 'message': 'Authentication system error - please restart ChitUI'}), 500

        # Verify password
        if check_password_hash(auth['password_hash'], password):
            session['logged_in'] = True
            session.permanent = True  # Keep session after browser close
            session['last_activity'] = time.time()  # Track session activity

            logger.info("User logged in successfully")

            return jsonify({
                'success': True,
                'require_password_change': auth.get('require_password_change', False)
            })
        else:
            logger.warning("Failed login attempt")
            return jsonify({'success': False, 'message': 'Invalid password'}), 401

    except Exception as e:
        logger.error(f"Login error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'message': 'Login failed'}), 500


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
    """Handle logout requests"""
    session.clear()
    logger.info("User logged out")
    return jsonify({'success': True})


@app.route('/auth/change-password', methods=['POST'])
def auth_change_password():
    """Handle password change requests"""
    try:
        if not session.get('logged_in'):
            return jsonify({'success': False, 'message': 'Not authenticated'}), 401

        data = request.json
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')

        if len(new_password) < 8:
            return jsonify({'success': False, 'message': 'Password must be at least 8 characters'}), 400

        # Check for weak passwords
        weak_passwords = ['admin', 'password', '12345678', 'chitui', 'qwerty']
        if new_password.lower() in weak_passwords:
            return jsonify({'success': False, 'message': 'Please choose a stronger password'}), 400

        settings = load_settings()
        auth = settings.get('auth', {})

        # Verify current password
        if not check_password_hash(auth['password_hash'], current_password):
            return jsonify({'success': False, 'message': 'Current password is incorrect'}), 401

        # Update password
        settings['auth']['password_hash'] = generate_password_hash(new_password)
        settings['auth']['require_password_change'] = False

        if save_settings(settings):
            logger.info("Password changed successfully")
            return jsonify({'success': True, 'message': 'Password changed successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to save new password'}), 500

    except Exception as e:
        logger.error(f"Password change error: {e}")
        return jsonify({'success': False, 'message': 'Failed to change password'}), 500


@app.route('/auth/session-timeout', methods=['POST'])
@login_required
def update_session_timeout():
    """Update session timeout setting"""
    try:
        data = request.json
        timeout = int(data.get('timeout', 0))

        if timeout < 0:
            return jsonify({'success': False, 'message': 'Timeout must be 0 or positive'}), 400

        settings = load_settings()
        if 'auth' not in settings:
            settings['auth'] = {}

        settings['auth']['session_timeout'] = timeout

        if save_settings(settings):
            logger.info(f"Session timeout updated to {timeout} seconds")
            return jsonify({'success': True, 'message': 'Session timeout updated'})
        else:
            return jsonify({'success': False, 'message': 'Failed to save timeout setting'}), 500

    except Exception as e:
        logger.error(f"Session timeout update error: {e}")
        return jsonify({'success': False, 'message': 'Failed to update timeout'}), 500


@app.route('/auth/session-timeout', methods=['GET'])
@login_required
def get_session_timeout():
    """Get current session timeout setting"""
    try:
        settings = load_settings()
        auth = settings.get('auth', {})
        timeout = auth.get('session_timeout', 0)
        return jsonify({'timeout': timeout})
    except Exception as e:
        logger.error(f"Error getting session timeout: {e}")
        return jsonify({'timeout': 0})


@app.route("/")
def web_index():
    """Main application page - requires authentication"""
    if not session.get('logged_in'):
        return app.send_static_file('login.html')

    # Check if password change is required
    auth = load_settings().get('auth', {})
    if auth.get('require_password_change'):
        return redirect('/change-password.html')

    return app.send_static_file('index.html')


@app.route('/settings', methods=['GET'])
@login_required
def get_settings():
    """Get current settings"""
    settings = load_settings()
    # Don't send password hash to frontend
    if 'auth' in settings:
        settings = settings.copy()
        settings['auth'] = {'require_password_change': settings['auth'].get('require_password_change', False)}
    return jsonify(settings)


@app.route('/settings', methods=['POST'])
@login_required
def update_settings():
    """Update settings"""
    try:
        settings = request.json
        if save_settings(settings):
            return jsonify({"success": True, "message": "Settings saved successfully"})
        else:
            return jsonify({"success": False, "message": "Failed to save settings"}), 500
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/status', methods=['GET'])
@login_required
def get_status():
    """Get application status including USB gadget info"""
    return jsonify({
        "usb_gadget": {
            "enabled": USE_USB_GADGET,
            "path": USB_GADGET_FOLDER if USE_USB_GADGET else None,
            "error": USB_GADGET_ERROR
        },
        "upload_folder": UPLOAD_FOLDER,
        "data_folder": DATA_FOLDER,
        "camera_support": CAMERA_SUPPORT
    })


@app.route('/python-packages', methods=['GET'])
def get_python_packages():
    """Get list of installed Python packages with versions"""
    try:
        # Run pip list to get installed packages
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'list', '--format=json'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            logger.error(f"Error getting packages: {result.stderr}")
            return jsonify({
                "success": False,
                "error": "Failed to retrieve packages",
                "stderr": result.stderr
            }), 500

        packages = json.loads(result.stdout)

        # Sort packages by name
        packages_sorted = sorted(packages, key=lambda x: x['name'].lower())

        return jsonify({
            "success": True,
            "packages": packages_sorted,
            "count": len(packages_sorted)
        })

    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "error": "Timeout while retrieving packages"
        }), 500
    except Exception as e:
        logger.error(f"Error getting Python packages: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ===== Maintenance API =====

@app.route('/maintenance/restart', methods=['POST'])
def restart_application():
    """Restart the ChitUI application"""
    try:
        logger.warning("Application restart requested by user")

        def do_restart():
            """Exit with code 42 to signal restart to wrapper script"""
            import time

            # Give time for the HTTP response to be sent
            time.sleep(2)

            logger.info("Exiting for restart (exit code 42)...")
            logger.info("If using run.sh, the application will restart automatically")

            # Exit with code 42 - the run.sh wrapper will catch this and restart
            os._exit(42)

        # Start the restart in a background thread
        Thread(target=do_restart, daemon=True).start()

        return jsonify({
            "success": True,
            "message": "Application is restarting..."
        })
    except Exception as e:
        logger.error(f"Error restarting application: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@app.route('/maintenance/reboot', methods=['POST'])
def reboot_system():
    """Reboot the Raspberry Pi system"""
    try:
        logger.warning("System reboot requested by user")

        def do_reboot():
            """Reboot the system after a short delay"""
            import time
            time.sleep(2)
            logger.info("Rebooting system...")
            subprocess.run(['sudo', 'reboot'], check=False)

        # Start the reboot in a background thread
        Thread(target=do_reboot, daemon=True).start()

        return jsonify({
            "success": True,
            "message": "System is rebooting..."
        })
    except Exception as e:
        logger.error(f"Error rebooting system: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


# ===== Plugin Management API =====

@app.route('/plugins', methods=['GET'])
def get_plugins():
    """Get list of all plugins"""
    return jsonify(plugin_manager.get_plugin_info())


@app.route('/plugins/<plugin_id>/enable', methods=['POST'])
def enable_plugin(plugin_id):
    """Enable a plugin"""
    try:
        plugin_manager.enable_plugin(plugin_id)
        # Load the plugin if not already loaded
        if plugin_id not in plugin_manager.get_all_plugins():
            plugin_manager.load_plugin(plugin_id, app, socketio)
        return jsonify({"success": True, "message": f"Plugin {plugin_id} enabled"})
    except Exception as e:
        logger.error(f"Error enabling plugin {plugin_id}: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/plugins/<plugin_id>/disable', methods=['POST'])
def disable_plugin(plugin_id):
    """Disable a plugin"""
    try:
        plugin_manager.disable_plugin(plugin_id)
        return jsonify({"success": True, "message": f"Plugin {plugin_id} disabled"})
    except Exception as e:
        logger.error(f"Error disabling plugin {plugin_id}: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/plugins/<plugin_id>/delete', methods=['POST'])
def delete_plugin(plugin_id):
    """Delete a plugin"""
    try:
        import shutil
        plugin_path = os.path.join(plugin_manager.plugins_dir, plugin_id)
        if os.path.exists(plugin_path):
            # Disable first
            plugin_manager.disable_plugin(plugin_id)
            # Delete directory
            shutil.rmtree(plugin_path)
            return jsonify({"success": True, "message": f"Plugin {plugin_id} deleted"})
        else:
            return jsonify({"success": False, "message": "Plugin not found"}), 404
    except Exception as e:
        logger.error(f"Error deleting plugin {plugin_id}: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/plugins/upload', methods=['POST'])
def upload_plugin():
    """Upload and install a plugin from ZIP file"""
    import zipfile
    import tempfile

    try:
        if 'plugin' not in request.files:
            return jsonify({"success": False, "message": "No plugin file provided"}), 400

        plugin_file = request.files['plugin']

        if plugin_file.filename == '':
            return jsonify({"success": False, "message": "No file selected"}), 400

        if not plugin_file.filename.endswith('.zip'):
            return jsonify({"success": False, "message": "Plugin must be a ZIP file"}), 400

        # Create temp directory for extraction
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, 'plugin.zip')
            plugin_file.save(zip_path)

            # Extract ZIP
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)

            # Find the plugin directory (should be the only directory in temp_dir)
            extracted_items = [item for item in os.listdir(temp_dir) if item != 'plugin.zip']

            if len(extracted_items) != 1 or not os.path.isdir(os.path.join(temp_dir, extracted_items[0])):
                return jsonify({
                    "success": False,
                    "message": "Invalid plugin structure. ZIP must contain a single directory."
                }), 400

            plugin_dir_name = extracted_items[0]
            extracted_plugin_path = os.path.join(temp_dir, plugin_dir_name)

            # Validate plugin structure
            manifest_path = os.path.join(extracted_plugin_path, 'plugin.json')
            init_path = os.path.join(extracted_plugin_path, '__init__.py')

            if not os.path.exists(manifest_path):
                return jsonify({
                    "success": False,
                    "message": "Invalid plugin: missing plugin.json"
                }), 400

            if not os.path.exists(init_path):
                return jsonify({
                    "success": False,
                    "message": "Invalid plugin: missing __init__.py"
                }), 400

            # Read and validate manifest
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)

            required_fields = ['name', 'version', 'author']
            for field in required_fields:
                if field not in manifest:
                    return jsonify({
                        "success": False,
                        "message": f"Invalid plugin.json: missing '{field}' field"
                    }), 400

            # Check if plugin already exists
            target_path = os.path.join(plugin_manager.plugins_dir, plugin_dir_name)
            if os.path.exists(target_path):
                return jsonify({
                    "success": False,
                    "message": f"Plugin '{plugin_dir_name}' already exists. Delete it first to reinstall."
                }), 409

            # Copy plugin to plugins directory
            import shutil
            shutil.copytree(extracted_plugin_path, target_path)

            logger.info(f"Plugin {plugin_dir_name} installed successfully")

            return jsonify({
                "success": True,
                "message": f"Plugin '{manifest['name']}' installed successfully. Reload the page to use it.",
                "plugin_id": plugin_dir_name,
                "plugin_name": manifest['name']
            })

    except zipfile.BadZipFile:
        return jsonify({"success": False, "message": "Invalid ZIP file"}), 400
    except json.JSONDecodeError:
        return jsonify({"success": False, "message": "Invalid plugin.json format"}), 400
    except Exception as e:
        logger.error(f"Error uploading plugin: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/plugins/ui', methods=['GET'])
def get_plugin_ui():
    """Get UI integration for all loaded plugins"""
    ui_elements = []
    for plugin_name, plugin in plugin_manager.get_all_plugins().items():
        ui_config = plugin.get_ui_integration()
        if ui_config:
            template_file = ui_config.get('template')
            if template_file:
                template_path = os.path.join(plugin.get_template_folder(), template_file)
                if os.path.exists(template_path):
                    with open(template_path, 'r') as f:
                        ui_config['html'] = f.read()

            ui_config['plugin_id'] = plugin_name
            ui_elements.append(ui_config)

    return jsonify(ui_elements)


@app.route('/discover', methods=['POST'])
def manual_discover():
    """Manually trigger printer discovery"""
    try:
        discovered = discover_printers()
        if discovered and len(discovered) > 0:
            settings = load_settings()
            for printer_id, printer in discovered.items():
                settings["printers"][printer_id] = {
                    "ip": printer["ip"],
                    "name": printer["name"],
                    "model": printer.get("model", "Unknown"),
                    "brand": printer.get("brand", "Unknown"),
                    "enabled": settings["printers"].get(printer_id, {}).get("enabled", True),
                    "manual": False
                }
            save_settings(settings)
            
            connect_printers(discovered)
            socketio.emit('printers', printers)
            
            return jsonify({"success": True, "printers": discovered, "count": len(discovered)})
        else:
            return jsonify({"success": False, "message": "No printers discovered"}), 404
    except Exception as e:
        logger.error(f"Error during discovery: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/printer/images', methods=['GET'])
@login_required
def get_printer_images():
    """Get list of available printer images"""
    try:
        img_folder = os.path.join(os.path.dirname(__file__), 'web', 'img')
        images = []

        if os.path.exists(img_folder):
            for file in os.listdir(img_folder):
                if file.endswith('.webp') or file.endswith('.png') or file.endswith('.jpg'):
                    images.append(file)

        images.sort()  # Sort alphabetically
        return jsonify({"success": True, "images": images})
    except Exception as e:
        logger.error(f"Error getting printer images: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/printer/manual', methods=['POST'])
def add_manual_printer():
    """Add a printer manually by IP"""
    try:
        data = request.json
        printer_ip = data.get('ip')
        printer_name = data.get('name', f'Printer-{printer_ip}')
        printer_image = data.get('image', '')
        usb_device_type = data.get('usb_device_type', 'physical')

        if not printer_ip:
            return jsonify({"success": False, "message": "IP address required"}), 400

        printer_id = hashlib.md5(printer_ip.encode()).hexdigest()

        settings = load_settings()
        if printer_id in settings.get("printers", {}):
            return jsonify({"success": False, "message": "Printer already exists"}), 400

        printer = {
            'connection': printer_id,
            'name': printer_name,
            'model': 'Manual',
            'brand': 'Unknown',
            'ip': printer_ip,
            'protocol': 'Unknown',
            'firmware': 'Unknown',
            'usb_device_type': usb_device_type
        }
        if printer_image:
            printer['image'] = printer_image

        printers[printer_id] = printer

        settings["printers"][printer_id] = {
            "ip": printer_ip,
            "name": printer_name,
            "model": "Manual",
            "brand": "Unknown",
            "enabled": True,
            "manual": True,
            "usb_device_type": usb_device_type
        }
        if printer_image:
            settings["printers"][printer_id]["image"] = printer_image
        save_settings(settings)
        
        url = f"ws://{printer_ip}:3030/websocket"
        logger.info(f"Attempting to connect to printer at {url}")
        
        websocket.setdefaulttimeout(2)
        ws = websocket.WebSocketApp(url,
                                    on_message=ws_msg_handler,
                                    on_open=lambda _: ws_connected_handler(printer['name']),
                                    on_close=lambda _, s, m: logger.info(
                                        "Connection to '{n}' closed: {m} ({s})".format(n=printer['name'], m=m, s=s)),
                                    on_error=lambda _, e: logger.warning(
                                        "Connection to '{n}' error: {e}".format(n=printer['name'], e=e))
                                    )
        websockets[printer_id] = ws
        Thread(target=lambda: ws.run_forever(reconnect=1), daemon=True).start()
        
        time.sleep(0.5)
        
        socketio.emit('printers', printers)
        return jsonify({"success": True, "printer": printer, "printer_id": printer_id})
    except Exception as e:
        logger.error(f"Error adding manual printer: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/printer/<printer_id>', methods=['PUT'])
def update_printer(printer_id):
    """Update a printer's settings"""
    try:
        data = request.json
        printer_ip = data.get('ip')
        printer_name = data.get('name')
        printer_image = data.get('image', '')
        usb_device_type = data.get('usb_device_type', 'physical')

        if not printer_ip or not printer_name:
            return jsonify({"success": False, "message": "IP address and name required"}), 400

        settings = load_settings()
        if printer_id not in settings["printers"]:
            return jsonify({"success": False, "message": "Printer not found"}), 404

        # Update settings
        settings["printers"][printer_id]["ip"] = printer_ip
        settings["printers"][printer_id]["name"] = printer_name
        settings["printers"][printer_id]["usb_device_type"] = usb_device_type
        if printer_image:
            settings["printers"][printer_id]["image"] = printer_image
        elif "image" in settings["printers"][printer_id]:
            # Remove image if not provided
            del settings["printers"][printer_id]["image"]

        save_settings(settings)

        # Update runtime printer data
        if printer_id in printers:
            printers[printer_id]["ip"] = printer_ip
            printers[printer_id]["name"] = printer_name
            printers[printer_id]["usb_device_type"] = usb_device_type
            if printer_image:
                printers[printer_id]["image"] = printer_image
            elif "image" in printers[printer_id]:
                del printers[printer_id]["image"]

            # If IP changed, reconnect
            old_ip = printers[printer_id].get("ip")
            if old_ip != printer_ip:
                # Close old connection
                if printer_id in websockets:
                    websockets[printer_id].close()
                    del websockets[printer_id]

                # Start new connection
                url = f"ws://{printer_ip}:3030/websocket"
                logger.info(f"Reconnecting to printer at {url}")

                websocket.setdefaulttimeout(2)
                ws = websocket.WebSocketApp(url,
                                            on_message=ws_msg_handler,
                                            on_open=lambda _: ws_connected_handler(printers[printer_id]['name']),
                                            on_close=lambda _, s, m: logger.info(
                                                "Connection to '{n}' closed: {m} ({s})".format(n=printers[printer_id]['name'], m=m, s=s)),
                                            on_error=lambda _, e: logger.warning(
                                                "Connection to '{n}' error: {e}".format(n=printers[printer_id]['name'], e=e))
                                            )
                websockets[printer_id] = ws
                Thread(target=lambda: ws.run_forever(reconnect=1), daemon=True).start()

        socketio.emit('printers', printers)
        return jsonify({"success": True, "message": "Printer updated"})
    except Exception as e:
        logger.error(f"Error updating printer: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/printer/<printer_id>', methods=['DELETE'])
def remove_printer(printer_id):
    """Remove a printer"""
    try:
        if printer_id in websockets:
            websockets[printer_id].close()
            del websockets[printer_id]

        if printer_id in printers:
            del printers[printer_id]

        settings = load_settings()
        if printer_id in settings["printers"]:
            del settings["printers"][printer_id]
            save_settings(settings)

        socketio.emit('printers', printers)
        return jsonify({"success": True, "message": "Printer removed"})
    except Exception as e:
        logger.error(f"Error removing printer: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/printer/default', methods=['POST'])
@login_required
def set_default_printer():
    """Set a printer as the default"""
    try:
        data = request.json
        printer_id = data.get('printer_id')

        if not printer_id:
            return jsonify({"success": False, "message": "Printer ID required"}), 400

        settings = load_settings()
        if printer_id not in settings.get("printers", {}):
            return jsonify({"success": False, "message": "Printer not found"}), 404

        settings["default_printer"] = printer_id
        save_settings(settings)

        logger.info(f"Set default printer to: {printer_id}")
        return jsonify({"success": True, "message": "Default printer set", "printer_id": printer_id})
    except Exception as e:
        logger.error(f"Error setting default printer: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ============ FILE UPLOAD ROUTES ============

@app.route('/progress')
def progress():
    """Server-sent events for upload progress"""
    upload_id = request.args.get('upload_id', 'default')

    def publish_progress():
        max_iterations = 200  # Prevent infinite loop (100 seconds max)
        iterations = 0

        while iterations < max_iterations:
            with uploadProgressLock:
                current_progress = uploadProgress.get(upload_id, 0)

            yield f"data:{current_progress}\n\n"

            if current_progress >= 100:
                # Clean up this upload's progress after sending 100%
                time.sleep(1)
                with uploadProgressLock:
                    if upload_id in uploadProgress:
                        del uploadProgress[upload_id]
                break

            time.sleep(0.5)
            iterations += 1

        # Final cleanup in case loop exited due to timeout
        with uploadProgressLock:
            if upload_id in uploadProgress:
                del uploadProgress[upload_id]

    response = Response(stream_with_context(publish_progress()), mimetype="text/event-stream")
    response.headers['Cache-Control'] = 'no-cache, no-transform'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    response.headers['Content-Type'] = 'text/event-stream'
    response.timeout = None
    return response


@app.route('/thumbnails/<filename>')
def serve_thumbnail(filename):
    """Serve thumbnails from the thumbnails folder."""
    from flask import send_from_directory
    return send_from_directory(THUMBNAILS_FOLDER, filename)


@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        # Check if another upload is in progress
        if not uploadLock.acquire(blocking=False):
            logger.warning("Upload already in progress")
            return Response('{"upload": "error", "msg": "Another upload is already in progress. Please wait."}', status=429, mimetype="application/json")

        try:
            if 'file' not in request.files:
                logger.error("No 'file' parameter in request.")
                return Response('{"upload": "error", "msg": "Malformed request - no file."}', status=400, mimetype="application/json")
            file = request.files['file']
            if file.filename == '':
                logger.error('No file selected to be uploaded.')
                return Response('{"upload": "error", "msg": "No file selected."}', status=400, mimetype="application/json")
            form_data = request.form.to_dict()
            if 'printer' not in form_data or form_data['printer'] == "":
                logger.error("No 'printer' parameter in request.")
                return Response('{"upload": "error", "msg": "Malformed request - no printer."}', status=400, mimetype="application/json")
            printer = printers[form_data['printer']]
            if file and not allowed_file(file.filename):
                logger.error("Invalid filetype.")
                return Response('{"upload": "error", "msg": "Invalid filetype."}', status=400, mimetype="application/json")

            # Get destination (local or usb)
            # If USB gadget is available, use it by default; otherwise use 'local' for network upload
            destination = form_data.get('destination', 'usb' if USE_USB_GADGET else 'local')
            logger.info(f"Upload destination: {destination}")
            logger.info(f"USB gadget mode: {'enabled' if USE_USB_GADGET else 'disabled'}")

            # Generate unique upload ID for progress tracking
            upload_id = form_data.get('upload_id', str(uuid.uuid4()))

            filename = secure_filename(file.filename)

            # Determine upload path based on printer's USB device type
            printer_id = form_data['printer']
            usb_device_type = printers[printer_id].get('usb_device_type', 'physical')

            # For virtual USB gadget, always use /mnt/usb_share
            if destination == 'usb' and usb_device_type == 'virtual':
                upload_folder = USB_GADGET_FOLDER
                # Verify mount point is available and writable
                if not os.path.exists(upload_folder):
                    logger.error(f"USB gadget folder not found: {upload_folder}")
                    return Response('{"upload": "error", "msg": "USB mount point not found. Please mount USB gadget first."}', status=500, mimetype="application/json")
                if not os.access(upload_folder, os.W_OK):
                    logger.error(f"USB gadget folder not writable: {upload_folder}")
                    return Response('{"upload": "error", "msg": "USB mount point not writable. Check permissions."}', status=500, mimetype="application/json")
            else:
                upload_folder = app.config['UPLOAD_FOLDER']

            filepath = os.path.join(upload_folder, filename)
            logger.info(f"Saving '{filename}' to {filepath} (upload_id: {upload_id})")
            logger.info(f"USB device type: {usb_device_type}, Upload folder: {upload_folder}")
            try:
                # Initialize progress
                with uploadProgressLock:
                    uploadProgress[upload_id] = 0

                # NEW WORKFLOW: Save to temp first, extract thumbnails, then proceed
                temp_filepath = os.path.join(TEMP_FOLDER, filename)
                file.save(temp_filepath)
                logger.info(f"✓ File '{filename}' saved to temp folder")

                # Extract thumbnails (for .goo/.ctb files)
                thumb_success, thumb_small, thumb_big = extract_thumbnail_for_file(Path(temp_filepath), output_to_thumbnails=True)
                if thumb_success:
                    logger.info(f"✓ Thumbnails extracted: {thumb_small}, {thumb_big}")
                else:
                    logger.info("⚠ No thumbnails extracted (file type doesn't support it or extraction failed)")

                # Now move to final destination
                shutil.move(temp_filepath, filepath)
                logger.info(f"✓ File moved to final destination: {filepath}")

                # Update database association
                if thumb_success:
                    file_db.add_file(filename, thumb_small, thumb_big)
                    logger.info(f"✓ Database updated with file-thumbnail association")

                # Check destination: if user selected USB and printer is configured for USB, process accordingly
                # For virtual USB: file is already saved to /mnt/usb_share, just need to reload
                # For physical USB or network upload: upload to printer via network
                if destination == 'usb' and (USE_USB_GADGET or usb_device_type == 'virtual'):
                    # File saved to USB gadget - update progress
                    with uploadProgressLock:
                        uploadProgress[upload_id] = 50
                    socketio.emit('upload_progress', {'upload_id': upload_id, 'progress': 50}, namespace='/')

                    logger.info("Destination: USB Gadget (Pi's virtual USB)")

                    # For virtual USB gadget, unmount/mount/reload
                    if usb_device_type == 'virtual':
                        logger.info("Virtual USB gadget detected - performing unmount/mount/reload")
                        with uploadProgressLock:
                            uploadProgress[upload_id] = 75
                        socketio.emit('upload_progress', {'upload_id': upload_id, 'progress': 75}, namespace='/')

                        reload_usb_gadget()
                        # Script handles all delays - no extra sleep needed
                        refresh_success = True
                    else:
                        # Physical USB or auto-refresh disabled
                        refresh_success = False
                        if USB_AUTO_REFRESH:
                            logger.info("Triggering USB gadget refresh to notify printer...")
                            with uploadProgressLock:
                                uploadProgress[upload_id] = 75
                            socketio.emit('upload_progress', {'upload_id': upload_id, 'progress': 75}, namespace='/')
                            refresh_success = trigger_usb_gadget_refresh()

                            if refresh_success:
                                logger.info("Waiting for printer to detect new file...")
                                time.sleep(2)
                        else:
                            logger.info("USB auto-refresh disabled (USB_AUTO_REFRESH=false)")
                            logger.info("💡 Manually refresh on printer or set USB_AUTO_REFRESH=true")

                    # Set progress to 100%
                    with uploadProgressLock:
                        uploadProgress[upload_id] = 100
                    socketio.emit('upload_progress', {'upload_id': upload_id, 'progress': 100}, namespace='/')

                    logger.info("✓ Upload to USB gadget complete!")

                    # Emit page refresh for virtual USB gadget
                    if usb_device_type == 'virtual':
                        socketio.emit('refresh_page', {'reason': 'virtual_usb_upload'})
                        msg = "File saved to USB gadget. Page will refresh to show updated file list."
                    elif USB_AUTO_REFRESH and refresh_success:
                        msg = "File saved to USB gadget. Printer should detect it automatically."
                    elif USB_AUTO_REFRESH and not refresh_success:
                        msg = "File saved to USB gadget. Auto-refresh failed - manually refresh on printer."
                        logger.warning("USB gadget refresh failed - manual intervention may be needed")
                        logger.info("💡 To enable automatic refresh, run: sudo python3 main.py")
                    else:
                        msg = "File saved to USB gadget. Manually refresh on printer or reconnect USB to detect it."

                    return Response(
                        json.dumps({
                            "upload": "success",
                            "msg": msg,
                            "upload_id": upload_id,
                            "usb_gadget": True,
                            "filename": filename,
                            "refresh_triggered": refresh_success
                        }),
                        status=200,
                        mimetype="application/json"
                    )
                else:
                    # Upload to printer via network (either local or usb storage on printer)
                    logger.info(f"Uploading to printer '{printer['name']}' - {destination} storage...")
                    success = upload_file_to_printer(printer['ip'], filepath, upload_id, destination)

                    if success:
                        # Emit page refresh for physical USB uploads
                        if destination == 'usb':
                            socketio.emit('refresh_page', {'reason': 'physical_usb_upload'})

                        return Response(
                            json.dumps({
                                "upload": "success",
                                "msg": "File uploaded to printer",
                                "upload_id": upload_id,
                                "usb_gadget": False,
                                "filename": filename
                            }),
                            status=200,
                            mimetype="application/json"
                        )
                    else:
                        return Response(
                            json.dumps({
                                "upload": "error",
                                "msg": "Failed to upload to printer",
                                "upload_id": upload_id,
                                "usb_gadget": False
                            }),
                            status=500,
                            mimetype="application/json"
                        )

            except Exception as e:
                logger.error(f"Upload failed: {e}")
                return Response(f'{{"upload": "error", "msg": "Upload failed: {str(e)}", "upload_id": "{upload_id}"}}', status=500, mimetype="application/json")
        finally:
            # Always release the lock
            uploadLock.release()
    else:
        return Response("u r doin it rong", status=405, mimetype='text/plain')


@app.route('/download/<printer_id>/<filename>')
def download_file(printer_id, filename):
    """
    Download a file from the printer's storage.
    Files are served from UPLOAD_FOLDER (either USB gadget or local storage).
    """
    from flask import send_from_directory

    try:
        # Remove path prefixes like /local/ or /usb/ if present
        clean_filename = filename
        if filename.startswith('/local/'):
            clean_filename = filename[7:]  # Remove '/local/'
        elif filename.startswith('/usb/'):
            clean_filename = filename[5:]  # Remove '/usb/'

        # Secure the filename to prevent directory traversal
        safe_filename = secure_filename(clean_filename)

        # Get the printer's USB device type to determine file location
        usb_device_type = 'physical'
        if printer_id in printers:
            usb_device_type = printers[printer_id].get('usb_device_type', 'physical')

        # Determine file location
        if usb_device_type == 'virtual' and USE_USB_GADGET:
            file_folder = USB_GADGET_FOLDER
        else:
            file_folder = app.config['UPLOAD_FOLDER']

        # Check if file exists
        file_path = os.path.join(file_folder, safe_filename)
        if not os.path.exists(file_path):
            logger.warning(f"Download requested for non-existent file: {file_path}")
            return Response('{"error": "File not found"}', status=404, mimetype="application/json")

        logger.info(f"Downloading file: {safe_filename} from {file_folder}")
        return send_from_directory(file_folder, safe_filename, as_attachment=True)

    except Exception as e:
        logger.error(f"Download failed: {e}")
        return Response(f'{{"error": "Download failed: {str(e)}"}}', status=500, mimetype="application/json")


@app.route('/usb-gadget/storage', methods=['GET'])
def get_usb_gadget_storage():
    """Get USB gadget storage information"""
    try:
        import shutil

        # Check if USB gadget is available
        if not os.path.exists('/mnt/usb_share'):
            return jsonify({
                "success": False,
                "available": False,
                "message": "USB gadget mount point not found"
            })

        # Get disk usage for /mnt/usb_share
        stat = shutil.disk_usage('/mnt/usb_share')

        # Also try to get the size of /piusb.bin for total capacity
        total_bytes = stat.total
        if os.path.exists('/piusb.bin'):
            bin_size = os.path.getsize('/piusb.bin')
            # Use bin file size if it's larger (more accurate)
            if bin_size > total_bytes:
                total_bytes = bin_size

        return jsonify({
            "success": True,
            "available": True,
            "total": total_bytes,
            "used": stat.used,
            "free": stat.free,
            "percent": round((stat.used / total_bytes * 100), 1) if total_bytes > 0 else 0
        })
    except Exception as e:
        logger.error(f"Error getting USB gadget storage info: {e}")
        return jsonify({
            "success": False,
            "available": False,
            "message": str(e)
        }), 500


@app.route('/usb-gadget/refresh', methods=['POST'])
def refresh_usb_gadget_endpoint():
    """Manually trigger USB gadget refresh to notify printer of file changes"""
    if not USE_USB_GADGET and not os.path.exists('/mnt/usb_share'):
        return jsonify({
            "success": False,
            "message": "USB gadget is not available",
            "error": USB_GADGET_ERROR if not USE_USB_GADGET else "USB gadget mount point not found"
        }), 400

    try:
        logger.info("Manual USB gadget refresh requested via API")
        success = reload_usb_gadget()
        if success:
            return jsonify({
                "success": True,
                "message": "USB gadget disconnected and reconnected successfully. Printer should detect the change in ~4 seconds."
            })
        else:
            return jsonify({
                "success": False,
                "message": "USB gadget reload failed. Check logs for details or try running with sudo."
            }), 500
    except Exception as e:
        logger.error(f"Error in USB gadget refresh endpoint: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def upload_file_to_printer(printer_ip, filepath, upload_id, destination='local'):
    """Upload file to printer in chunks via HTTP API

    Args:
        printer_ip: IP address of the printer
        filepath: Path to the file to upload
        upload_id: Unique ID for tracking upload progress
        destination: Upload destination - 'local' for internal storage or 'usb' for USB storage
    """
    part_size = 1048576  # 1MB chunks
    filename = os.path.basename(filepath)

    # Initialize progress for this upload
    with uploadProgressLock:
        uploadProgress[upload_id] = 0

    # Calculate MD5 hash
    md5_hash = hashlib.md5()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            md5_hash.update(byte_block)

    file_stats = os.stat(filepath)
    post_data = {
        'S-File-MD5': md5_hash.hexdigest(),
        'Check': 1,
        'Offset': 0,
        'Uuid': uuid.uuid4(),
        'TotalSize': file_stats.st_size,
    }

    # Use same endpoint for both destinations
    url = 'http://{ip}:3030/uploadFile/upload'.format(ip=printer_ip)

    # USB uploads: send complete file in one request (no chunking)
    if destination == 'usb':
        logger.info(f"Upload destination: USB storage")
        logger.info(f"Uploading complete file to USB storage (size: {file_stats.st_size} bytes)...")

        # Update progress to 30% (upload starting)
        with uploadProgressLock:
            uploadProgress[upload_id] = 30

        # Read entire file
        with open(filepath, 'rb') as f:
            file_data = f.read()

        # Try multiple USB upload methods with fallback
        upload_methods = [
            {
                'name': 'USB path prefix in filename',
                'filename': f'usb/{filename}',
                'post_data': post_data.copy()
            },
            {
                'name': 'Path parameter with /usb',
                'filename': filename,
                'post_data': {**post_data, 'Path': '/usb'}
            },
            {
                'name': 'Path parameter with usb',
                'filename': filename,
                'post_data': {**post_data, 'Path': 'usb'}
            },
            {
                'name': 'Destination parameter',
                'filename': filename,
                'post_data': {**post_data, 'Destination': 'usb'}
            }
        ]

        # Update progress to 40%
        with uploadProgressLock:
            uploadProgress[upload_id] = 40

        for method in upload_methods:
            logger.info(f"Trying method: {method['name']}")
            logger.debug(f"  Filename: {method['filename']}")
            logger.debug(f"  Post data keys: {list(method['post_data'].keys())}")

            post_files = {'File': (method['filename'], file_data)}

            try:
                # Update progress to 60%
                with uploadProgressLock:
                    uploadProgress[upload_id] = 60

                response = requests.post(url, data=method['post_data'], files=post_files, timeout=120)

                # Log response details
                logger.info(f"Response status: {response.status_code}")
                logger.debug(f"Response headers: {response.headers}")

                # Try to parse JSON response
                try:
                    status = json.loads(response.text)
                    logger.debug(f"Response JSON: {status}")
                except json.JSONDecodeError:
                    logger.warning(f"Non-JSON response: {response.text[:200]}")
                    # Some printers return plain text on success
                    if response.status_code == 200:
                        logger.info(f"✓ Upload successful (HTTP 200, non-JSON response)")
                        with uploadProgressLock:
                            uploadProgress[upload_id] = 100
                        logger.info(f"✓ Method '{method['name']}' worked! Saving for future uploads.")
                        return True
                    else:
                        logger.warning(f"Method '{method['name']}' failed with status {response.status_code}")
                        continue

                # Check if upload succeeded
                if status.get('success') or status.get('status') == 'success':
                    logger.info(f"✓ Upload successful!")
                    with uploadProgressLock:
                        uploadProgress[upload_id] = 100
                    logger.info(f"✓ Method '{method['name']}' worked! Saving for future uploads.")
                    return True
                else:
                    logger.warning(f"Method '{method['name']}' failed: {status}")
                    continue

            except requests.exceptions.Timeout:
                logger.warning(f"Method '{method['name']}' timed out (120s)")
                continue
            except requests.exceptions.RequestException as req_err:
                logger.warning(f"Method '{method['name']}' request error: {req_err}")
                continue
            except Exception as e:
                logger.warning(f"Method '{method['name']}' error: {e}")
                continue

        # If we get here, all methods failed
        logger.error("All USB upload methods failed!")
        logger.error("Please check:")
        logger.error("  1. USB drive is inserted in printer's USB port")
        logger.error("  2. USB drive is formatted as FAT32 or exFAT")
        logger.error("  3. Printer firmware supports network uploads to USB")
        with uploadProgressLock:
            uploadProgress[upload_id] = 0
        return False

    # Local uploads: send file in chunks
    else:
        num_parts = (int)(file_stats.st_size / part_size)
        logger.info(f"Uploading file in {num_parts + 1} parts...")

        i = 0
        while i <= num_parts:
            offset = i * part_size
            progress_value = round(i / num_parts * 100) if num_parts > 0 else 100

            # Update progress (thread-safe)
            with uploadProgressLock:
                uploadProgress[upload_id] = progress_value

            # Emit progress via WebSocket (bypasses SSE buffering issues)
            socketio.emit('upload_progress', {
                'upload_id': upload_id,
                'progress': progress_value
            }, namespace='/')

            with open(filepath, 'rb') as f:
                f.seek(offset)
                file_part = f.read(part_size)
                logger.debug(f"Uploading part {i}/{num_parts} (offset: {offset})")

                if not upload_file_part(url, post_data, filename, file_part, offset):
                    logger.error("Uploading file to printer failed.")
                    # Set progress to 0 to indicate failure
                    with uploadProgressLock:
                        uploadProgress[upload_id] = 0
                    return False

                logger.debug(f"Part {i}/{num_parts} uploaded.")
            i += 1

        # Set progress to 100% (thread-safe)
        with uploadProgressLock:
            uploadProgress[upload_id] = 100

        # Emit 100% via WebSocket
        socketio.emit('upload_progress', {
            'upload_id': upload_id,
            'progress': 100
        }, namespace='/')

        logger.info(f"✓ Upload complete!")

    # Delete the temporary file after successful upload
    try:
        os.remove(filepath)
        logger.debug(f"Temporary file {filepath} removed")
    except OSError as e:
        logger.warning(f"Could not remove temporary file {filepath}: {e}")

    return True


def upload_file_part(url, post_data, file_name, file_part, offset):
    """Upload a single chunk to the printer"""
    post_data['Offset'] = offset
    post_files = {'File': (file_name, file_part)}

    try:
        response = requests.post(url, data=post_data, files=post_files, timeout=30)

        # Log response details for debugging
        logger.debug(f"Upload response status: {response.status_code}")
        logger.debug(f"Upload response headers: {response.headers}")

        # Try to parse JSON response
        try:
            status = json.loads(response.text)
        except json.JSONDecodeError as json_err:
            logger.error(f"Failed to parse JSON response from printer")
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response body: {response.text[:500]}")  # First 500 chars
            return False

        if status.get('success'):
            return True
        else:
            logger.error(f"Upload part failed: {status}")
            return False
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Upload request error: {req_err}")
        return False
    except Exception as e:
        logger.error(f"Upload part error: {e}")
        return False


# Global variables for upload progress tracking (thread-safe)
uploadProgress = {}  # Dictionary to track progress per upload session
uploadProgressLock = threading.Lock()
uploadLock = threading.Lock()  # Prevent concurrent uploads


# ============ SOCKETIO HANDLERS ============

@socketio.on('connect')
def sio_handle_connect(auth):
    logger.info('Client connected')
    logger.info(f'Available printers: {list(printers.keys())}')
    socketio.emit('printers', printers)


@socketio.on('disconnect')
def sio_handle_disconnect():
    logger.info('Client disconnected')


@socketio.on('printers')
def sio_handle_printers(data):
    logger.debug('client.printers >> '+str(data))
    load_saved_printers()


@socketio.on('printer_info')
def sio_handle_printer_status(data):
    logger.debug(f"client.printer_info >> {data['id']}")
    get_printer_status(data['id'])
    get_printer_attributes(data['id'])


@socketio.on('printer_files')
def sio_handle_printer_files(data):
    logger.debug(f'client.printer_files >> {json.dumps(data)}')
    get_printer_files(data['id'], data['url'])


def unmount_usb_gadget():
    """Unmount the USB gadget mount point"""
    try:
        logger.info("Unmounting USB gadget...")
        result = subprocess.run(['umount', '/mnt/usb_share'],
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info("USB gadget unmounted successfully")
            return True
        else:
            logger.warning(f"Unmount returned non-zero: {result.stderr}")
            return True  # Continue anyway
    except subprocess.TimeoutExpired:
        logger.error("USB gadget unmount timed out")
        return False
    except Exception as e:
        logger.error(f"Error unmounting USB gadget: {e}")
        return False

def mount_usb_gadget():
    """Mount the USB gadget mount point with read-write permissions"""
    try:
        logger.info("Mounting USB gadget...")

        # Check if already mounted and if it's read-only
        try:
            check_result = subprocess.run(['mountpoint', '-q', '/mnt/usb_share'],
                                        capture_output=True)
            if check_result.returncode == 0:
                # Already mounted - check if read-only
                mount_info = subprocess.run(['mount'], capture_output=True, text=True)
                for line in mount_info.stdout.split('\n'):
                    if '/mnt/usb_share' in line:
                        if 'ro,' in line or '(ro)' in line:
                            logger.warning("USB gadget is mounted read-only, remounting as read-write...")
                            # Remount as read-write
                            result = subprocess.run(['mount', '-o', 'remount,rw', '/mnt/usb_share'],
                                                  capture_output=True, text=True, timeout=5)
                            if result.returncode == 0:
                                logger.info("✓ USB gadget remounted as read-write")
                                return True
                            else:
                                logger.error(f"Failed to remount as rw: {result.stderr}")
                                # Try unmounting and mounting fresh
                                logger.info("Trying full unmount/mount cycle...")
                                unmount_usb_gadget()
                                time.sleep(0.5)
                        else:
                            logger.info("USB gadget already mounted as read-write")
                            return True
        except Exception as e:
            logger.debug(f"Mountpoint check error (continuing): {e}")

        # Try mounting from fstab first
        result = subprocess.run(['mount', '/mnt/usb_share'],
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info("USB gadget mounted successfully from fstab")
            return True

        # If fstab mount failed, try manual mount with explicit options
        logger.info("Fstab mount failed, trying manual mount with rw options...")
        result = subprocess.run([
            'mount', '-t', 'vfat', '-o',
            'loop,rw,umask=000,uid=1000,gid=1000',
            '/piusb.bin', '/mnt/usb_share'
        ], capture_output=True, text=True, timeout=5)

        if result.returncode == 0:
            logger.info("USB gadget mounted successfully with manual mount")
            return True
        else:
            logger.warning(f"Mount failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("USB gadget mount timed out")
        return False
    except Exception as e:
        logger.error(f"Error mounting USB gadget: {e}")
        return False

def reload_usb_gadget():
    """Reload the USB gadget to reflect file changes on the printer"""
    try:
        logger.info("Reloading USB gadget to notify printer...")

        # Use the reload script - it works when run manually so call it from Python
        script_path = os.path.join(os.path.dirname(__file__), 'scripts', 'reload_usb_gadget.sh')
        if os.path.exists(script_path):
            logger.info(f"Running reload script: {script_path}")
            result = subprocess.run(['sudo', 'bash', script_path],
                                  capture_output=True, text=True, timeout=30)

            # Log script output
            if result.stdout:
                for line in result.stdout.strip().split('\n'):
                    logger.info(f"  SCRIPT: {line}")
            if result.stderr:
                for line in result.stderr.strip().split('\n'):
                    logger.warning(f"  SCRIPT ERR: {line}")

            if result.returncode == 0:
                logger.info("✓ USB gadget reload complete!")
                return True
            else:
                logger.error(f"✗ Reload script failed (exit code {result.returncode})")
                return False

        logger.error(f"Reload script not found at {script_path}")
        return False

    except subprocess.TimeoutExpired:
        logger.error("USB gadget reload timed out")
        return False
    except Exception as e:
        logger.error(f"Error reloading USB gadget: {e}")
        return False

def delete_file_from_mount(file_path):
    """Delete a file directly from the USB gadget mount point"""
    try:
        # Convert printer path to mount point path
        # e.g., /usb/file.goo -> /mnt/usb_share/file.goo
        if file_path.startswith('/usb/'):
            mount_path = file_path.replace('/usb/', '/mnt/usb_share/', 1)
        else:
            logger.error(f"Invalid file path for mount delete: {file_path}")
            return False

        # Get filename for cascade delete
        filename = os.path.basename(mount_path)

        # Check if file exists
        if not os.path.exists(mount_path):
            logger.warning(f"File not found at mount point: {mount_path}")
            return False

        # CASCADE DELETE: Get thumbnail info before deleting
        file_data = file_db.get_file(filename)
        thumb_small = file_data.get('thumbnail_small', '')
        thumb_big = file_data.get('thumbnail_big', '')

        # Delete the file from mount point
        os.remove(mount_path)
        logger.info(f"Deleted file from mount point: {mount_path}")

        # CASCADE DELETE: Delete associated thumbnails
        if thumb_small:
            small_path = os.path.join(THUMBNAILS_FOLDER, thumb_small)
            if os.path.exists(small_path):
                os.remove(small_path)
                logger.info(f"Deleted thumbnail: {thumb_small}")

        if thumb_big:
            big_path = os.path.join(THUMBNAILS_FOLDER, thumb_big)
            if os.path.exists(big_path):
                os.remove(big_path)
                logger.info(f"Deleted thumbnail: {thumb_big}")

        # CASCADE DELETE: Remove from database
        file_db.delete_file(filename)
        logger.info(f"Removed {filename} from database")

        # Reload the USB gadget so the printer sees the change
        logger.info(">>> Starting USB gadget reload after delete...")
        reload_result = reload_usb_gadget()
        if reload_result:
            logger.info("✓ USB gadget reload completed successfully")
        else:
            logger.warning("⚠ USB gadget reload returned False - printer may not detect change")

        return True
    except Exception as e:
        logger.error(f"Error deleting file from mount point: {e}")
        return False

@socketio.on('action_delete')
def sio_handle_action_delete(data):
    logger.debug(f'client.action_delete >> {json.dumps(data)}')

    printer_id = data['id']
    file_path = data['data']

    # Get the printer's USB device type setting
    usb_device_type = 'physical'  # Default to physical
    if printer_id in printers:
        usb_device_type = printers[printer_id].get('usb_device_type', 'physical')
        logger.info(f"Printer {printer_id} USB device type: {usb_device_type}")
    else:
        logger.warning(f"Printer {printer_id} not found in printers dictionary, using default USB type")

    logger.info(f"Delete request: file={file_path}, printer={printer_id}, usb_type={usb_device_type}")

    # Check if this is a USB file and we're using virtual gadget
    if file_path.startswith('/usb/') and usb_device_type == 'virtual':
        # Delete directly from mount point and reload gadget
        logger.info("✓ Using VIRTUAL USB GADGET delete method (direct mount point delete + reload)")
        success = delete_file_from_mount(file_path)
        if success:
            logger.info(f"✓ Successfully deleted {file_path} from virtual USB gadget")
            socketio.emit('toast', {
                'message': f'File deleted from virtual USB gadget: {os.path.basename(file_path)}',
                'type': 'success'
            })
            # Trigger page refresh after successful virtual USB delete
            socketio.emit('refresh_page', {'reason': 'virtual_usb_delete'})
        else:
            logger.error(f"✗ Failed to delete {file_path} from virtual USB gadget")
            socketio.emit('toast', {
                'message': f'Failed to delete file from virtual USB gadget',
                'type': 'error'
            })
    elif file_path.startswith('/usb/'):
        # Physical USB drive - use standard SDCP delete command
        logger.info("✓ Using PHYSICAL USB delete method (SDCP 259 command to printer)")
        send_printer_cmd(printer_id, 259, {"FileList": [file_path]})
        # Trigger page refresh after successful physical USB delete
        socketio.emit('refresh_page', {'reason': 'physical_usb_delete'})
    else:
        # Local storage - use standard SDCP delete command
        logger.info("✓ Using LOCAL STORAGE delete method (SDCP 259 command to printer)")
        send_printer_cmd(printer_id, 259, {"FileList": [file_path]})


@socketio.on('action_print')
def sio_handle_action_print(data):
    logger.debug(f'client.action_print >> {json.dumps(data)}')
    send_printer_cmd(data['id'], 128, {
                     "Filename": data['data'], "StartLayer": 0})


@socketio.on('action_pause')
def sio_handle_action_pause(data):
    logger.debug(f'client.action_pause >> {json.dumps(data)}')
    send_printer_cmd(data['id'], 129)


@socketio.on('action_resume')
def sio_handle_action_resume(data):
    logger.debug(f'client.action_resume >> {json.dumps(data)}')
    send_printer_cmd(data['id'], 131)


@socketio.on('action_stop')
def sio_handle_action_stop(data):
    logger.debug(f'client.action_stop >> {json.dumps(data)}')
    send_printer_cmd(data['id'], 130)


@socketio.on('action_clear_history')
def sio_handle_action_clear_history(data):
    logger.info(f"Clearing print history for printer {data['id']}")
    # SDCP command 320 = Clear print history
    send_printer_cmd(data['id'], 320)


@socketio.on('action_wipe_storage')
def sio_handle_action_wipe_storage(data):
    logger.warning(f"FORMATTING LOCAL STORAGE on printer {data['id']}")
    printer_id = data['id']
    
    if printer_id not in printers:
        logger.error(f"Printer {printer_id} not found")
        return
    
    # SDCP command 322 = Format local storage
    # This is the same as the "Format Local Storage" button in printer settings
    send_printer_cmd(printer_id, 322)
    
    logger.info("Format local storage command sent")


@socketio.on('get_attributes')
def sio_handle_get_attributes(data):
    logger.debug(f'client.get_attributes >> {json.dumps(data)}')
    get_printer_attributes(data['id'])


@socketio.on('get_task_details')
def sio_handle_get_task_details(data):
    logger.debug(f'client.get_task_details >> {json.dumps(data)}')
    send_printer_cmd(data['id'], 321, {"Id": [data['taskId']]})


@socketio.on('terminal_command')
def sio_handle_terminal_command(data):
    """Handle commands from terminal plugin"""
    printer_id = data.get('printer_id')
    command = data.get('command')

    logger.debug(f'terminal_command >> printer:{printer_id} cmd:{command}')

    if not printer_id:
        logger.error("No printer_id provided in terminal command")
        return

    # Parse command - could be JSON dict or simple command number
    try:
        if isinstance(command, dict):
            # Already parsed JSON with Cmd and optional Data
            cmd = command.get('Cmd', command.get('cmd'))
            cmd_data = command.get('Data', command.get('data', {}))
        elif isinstance(command, str):
            # Try to parse as JSON first
            try:
                parsed = json.loads(command)
                cmd = parsed.get('Cmd', parsed.get('cmd'))
                cmd_data = parsed.get('Data', parsed.get('data', {}))
            except json.JSONDecodeError:
                # Not JSON, treat as command number
                cmd = int(command)
                cmd_data = {}
        elif isinstance(command, int):
            cmd = command
            cmd_data = {}
        else:
            logger.error(f"Invalid command format: {command}")
            return

        # Send the command
        send_printer_cmd(printer_id, cmd, cmd_data)
        logger.info(f"Terminal command sent: Cmd={cmd} Data={cmd_data}")

    except Exception as e:
        logger.error(f"Failed to parse terminal command: {e}")


# ============ PRINTER CONTROL FUNCTIONS ============

def get_printer_status(id):
    send_printer_cmd(id, 0)


def get_printer_attributes(id):
    send_printer_cmd(id, 1)


def get_printer_files(id, url):
    send_printer_cmd(id, 258, {"Url": url})


def send_printer_cmd(id, cmd, data={}):
    printer = printers.get(id)
    if not printer:
        logger.error(f"Printer {id} not found")
        return False
        
    if id not in websockets:
        logger.error(f"No websocket connection for printer {id}")
        return False
        
    ts = int(time.time())
    payload = {
        "Id": printer['connection'],
        "Data": {
            "Cmd": cmd,
            "Data": data,
            "RequestID": os.urandom(8).hex(),
            "MainboardID": id,
            "TimeStamp": ts,
            "From": 0
        },
        "Topic": "sdcp/request/" + id
    }
    logger.debug("printer << \n{p}", p=json.dumps(payload, indent=4))
    
    try:
        websockets[id].send(json.dumps(payload))
        return True
    except Exception as e:
        logger.error(f"Failed to send command to printer {id}: {e}")
        return False


# ============ PRINTER DISCOVERY & CONNECTION ============

def discover_printers():
    logger.info("Starting printer discovery.")
    msg = b'M99999'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                         socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(discovery_timeout)
    sock.bind(('', 54781))
    sock.sendto(msg, ("255.255.255.255", 3000))
    socketOpen = True
    discovered_printers = {}
    while (socketOpen):
        try:
            data = sock.recv(8192)
            save_discovered_printer(data, discovered_printers)
        except TimeoutError:
            sock.close()
            break
    logger.info("Discovery done.")
    return discovered_printers


def save_discovered_printer(data, printer_dict):
    j = json.loads(data.decode('utf-8'))
    printer = {}
    printer['connection'] = j['Id']
    printer['name'] = j['Data']['Name']
    printer['model'] = j['Data']['MachineName']
    printer['brand'] = j['Data']['BrandName']
    printer['ip'] = j['Data']['MainboardIP']
    printer['protocol'] = j['Data']['ProtocolVersion']
    printer['firmware'] = j['Data']['FirmwareVersion']
    printer['online'] = False  # Initially offline until connected
    printer_dict[j['Data']['MainboardID']] = printer
    printers[j['Data']['MainboardID']] = printer
    logger.info("Discovered: {n} ({i})".format(
        n=printer['name'], i=printer['ip']))
    return printer_dict


def connect_printers(printers_to_connect):
    for id, printer in printers_to_connect.items():
        url = "ws://{ip}:3030/websocket".format(ip=printer['ip'])
        logger.info("Connecting to: {n}".format(n=printer['name']))
        websocket.setdefaulttimeout(1)
        ws = websocket.WebSocketApp(url,on_message=ws_msg_handler,
                                    on_open=lambda _, printer_id=id: ws_connected_handler(printer_id),
                                    on_close=lambda _, s, m, printer_id=id: ws_disconnected_handler(printer_id, s, m),
                                    on_error=lambda _, e, printer_id=id: ws_error_handler(printer_id, e)
                                    )
        websockets[id] = ws
        # Add aggressive ping/pong to detect dead connections quickly
        # ping_interval: send ping every 3 seconds
        # ping_timeout: wait 2 seconds for pong response before closing
        # This ensures disconnections are detected within ~5 seconds maximum
        Thread(target=lambda: ws.run_forever(
            reconnect=1,
            ping_interval=3,
            ping_timeout=2
        ), daemon=True).start()

    return True


def ws_connected_handler(printer_id):
    if printer_id in printers:
        printers[printer_id]['online'] = True
        logger.info("Connected to: {n}".format(n=printers[printer_id]['name']))
        socketio.emit('printers', printers)


def ws_disconnected_handler(printer_id, status_code, message):
    if printer_id in printers:
        printers[printer_id]['online'] = False
        logger.info("Connection to '{n}' closed: {m} ({s})".format(
            n=printers[printer_id]['name'], m=message, s=status_code))
        socketio.emit('printers', printers)


def ws_error_handler(printer_id, error):
    if printer_id in printers:
        logger.info("Connection to '{n}' error: {e}".format(
            n=printers[printer_id]['name'], e=error))


def check_printer_connections():
    """Background task to verify printer connections are actually alive"""
    while True:
        time.sleep(5)  # Check every 5 seconds
        changed = False
        for printer_id, ws in list(websockets.items()):
            if printer_id in printers:
                # Check if websocket is actually connected
                is_connected = ws.sock is not None and ws.sock.connected
                current_online = printers[printer_id].get('online', False)

                # Update status if it changed
                if is_connected and not current_online:
                    printers[printer_id]['online'] = True
                    changed = True
                    logger.info(f"Printer '{printers[printer_id]['name']}' connection restored")
                elif not is_connected and current_online:
                    printers[printer_id]['online'] = False
                    changed = True
                    logger.info(f"Printer '{printers[printer_id]['name']}' connection lost")

        # Notify frontend if any status changed
        if changed:
            socketio.emit('printers', printers)


def ws_msg_handler(ws, msg):
    try:
        data = json.loads(msg)
        logger.debug("printer >> \n{m}", m=json.dumps(data, indent=4))

        # Notify plugins of printer message
        printer_id = data.get('MainboardID')
        if printer_id:
            plugin_manager.notify_printer_message(printer_id, data)

        if data['Topic'].startswith("sdcp/response/"):
            socketio.emit('printer_response', data)
        elif data['Topic'].startswith("sdcp/status/"):
            socketio.emit('printer_status', data)
        elif data['Topic'].startswith("sdcp/attributes/"):
            socketio.emit('printer_attributes', data)
        elif data['Topic'].startswith("sdcp/error/"):
            socketio.emit('printer_error', data)
        elif data['Topic'].startswith("sdcp/notice/"):
            socketio.emit('printer_notice', data)
        else:
            logger.warning("--- UNKNOWN MESSAGE ---")
            logger.warning(data)
            logger.warning("--- UNKNOWN MESSAGE ---")
    except Exception as e:
        logger.error(f"Error handling websocket message: {e}")


def load_saved_printers():
    """Load and connect to saved printers from settings"""
    settings = load_settings()
    
    if settings.get("auto_discover", False):
        logger.info("Auto-discovery is enabled, discovering printers...")
        discover_printers()
    
    for printer_id, printer_config in settings.get("printers", {}).items():
        if printer_config.get("enabled", True):
            if printer_id not in printers:
                printer = {
                    'connection': printer_id,
                    'name': printer_config['name'],
                    'model': printer_config.get('model', 'Unknown'),
                    'brand': printer_config.get('brand', 'Unknown'),
                    'ip': printer_config['ip'],
                    'protocol': printer_config.get('protocol', 'Unknown'),
                    'firmware': printer_config.get('firmware', 'Unknown'),
                    'usb_device_type': printer_config.get('usb_device_type', 'physical'),  # Load USB device type setting
                    'online': False  # Initially offline until connected
                }
                # Add image if present in config
                if 'image' in printer_config:
                    printer['image'] = printer_config['image']
                printers[printer_id] = printer
                logger.info(f"Loaded saved printer: {printer_config['name']} ({printer_config['ip']}) - USB type: {printer['usb_device_type']}")

            if printer_id not in websockets:
                connect_printers({printer_id: printers[printer_id]})


# ============ MAIN ============

def main():
    # Initialize authentication
    logger.info("Initializing authentication...")
    init_auth()

    # Mount USB gadget if enabled
    global USE_USB_GADGET, UPLOAD_FOLDER
    if ENABLE_USB_GADGET and os.path.exists(USB_GADGET_FOLDER):
        logger.info("Mounting USB gadget on startup...")
        if mount_usb_gadget():
            logger.info("✓ USB gadget mounted successfully")

            # Re-test if writable after mounting
            test_file = os.path.join(USB_GADGET_FOLDER, '.write_test')
            try:
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
                logger.info(f"✓ USB gadget is writable")
                UPLOAD_FOLDER = USB_GADGET_FOLDER
                USE_USB_GADGET = True
            except (PermissionError, OSError) as e:
                logger.error(f"✗ USB gadget not writable after mount: {e}")
                logger.warning("⚠ Files will be uploaded directly to printer via network")
                USE_USB_GADGET = False
        else:
            logger.warning("⚠ USB gadget mount failed - will try again on first upload")

    settings = load_settings()

    # Load plugins
    logger.info("Loading plugins...")
    # Make plugin_manager accessible to plugins via app context
    app.plugin_manager = plugin_manager
    plugin_manager.load_all_plugins(app, socketio)

    if settings.get("auto_discover", True):
        logger.info("Starting with auto-discovery enabled")
        discovered = discover_printers()
        if discovered:
            connect_printers(discovered)
            socketio.emit('printers', printers)
        else:
            logger.warning("No printers discovered.")

    load_saved_printers()

    # Start background connection health checker
    logger.info("Starting printer connection health monitor...")
    Thread(target=check_printer_connections, daemon=True).start()


# Initialize the application (runs on both direct execution and Gunicorn import)
main()

if __name__ == "__main__":

    logger.info("=" * 60)
    logger.info("ChitUI Starting")
    logger.info("=" * 60)
    logger.info(f"Python Environment:")
    logger.info(f"  → Python executable: {sys.executable}")
    logger.info(f"  → Python version: {sys.version.split()[0]}")
    logger.info(f"  → Running as user: {os.getenv('USER', 'unknown')}")
    if os.getenv('SUDO_USER'):
        logger.info(f"  → Original user (sudo): {os.getenv('SUDO_USER')}")
        logger.warning(f"  ⚠ Running with sudo - pip operations will affect root's Python environment")
    logger.info(f"Features:")
    logger.info(f"  ✓ Printer Management")
    if USE_USB_GADGET:
        logger.info(f"  ✓ File Upload (USB Gadget Mode)")
        logger.info(f"     → Files saved to: {UPLOAD_FOLDER}")
        logger.info(f"     → Connect USB to printer to access files")
        if USB_AUTO_REFRESH:
            logger.info(f"     → Auto-refresh: ENABLED (may crash some printers!)")
            logger.warning(f"     ⚠ If printer crashes, set USB_AUTO_REFRESH=false")
        else:
            logger.info(f"     → Auto-refresh: DISABLED (manual refresh needed)")
            logger.info(f"     → Set USB_AUTO_REFRESH=true to enable")
    else:
        logger.info(f"  ✓ File Upload (Network Transfer)")
        logger.info(f"     → Files uploaded directly to printer")
    if CAMERA_SUPPORT:
        logger.info(f"  ✓ Camera Streaming (RTSP)")
    else:
        logger.info(f"  ✗ Camera Streaming (install opencv-python-headless)")
    logger.info("=" * 60)
    logger.info(f"Data folder: {DATA_FOLDER}")
    logger.info(f"Settings file: {SETTINGS_FILE}")
    logger.info("=" * 60)

    socketio.run(app, host='0.0.0.0', port=port,
                 debug=debug, use_reloader=debug, log_output=True,
                 allow_unsafe_werkzeug=True)