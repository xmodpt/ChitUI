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
import queue
import traceback
from pathlib import Path
import struct
from PIL import Image, ImageOps
import shutil

from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

# Watchdog for live file watching (optional - install with: pip install watchdog)
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

# ===== Plugin System Imports =====
from plugins import PluginManager

# ===== Self-update Support =====
# Checks GitHub releases and applies them; see updater.py
import updater

# ===== Plugin Store =====
# Catalog, update detection and server-side plugin installs; see plugin_store.py
import plugin_store

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

# Secret key for sessions - persist to file so sessions survive reboots
def _get_or_create_secret_key():
    """Load secret key from file, or generate and save a new one."""
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', '.secret_key')
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r') as f:
                return f.read().strip()
    except Exception:
        pass
    # Generate new key and save it
    new_key = os.urandom(24).hex()
    try:
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, 'w') as f:
            f.write(new_key)
        os.chmod(key_file, 0o600)
    except Exception:
        pass  # Fall back to ephemeral key if save fails
    return new_key

app.config['SECRET_KEY'] = _get_or_create_secret_key()

# Don't let the browser sit on a stale chitui.js. Depending on the installed
# Flask version the default can be a 12-hour max-age, which means edits to the
# frontend silently don't apply until the cache expires. These files are a few
# hundred KB on a LAN; revalidating every time costs nothing.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# ===== WebSocket and Real-time Communication Setup =====
# SocketIO for real-time bidirectional communication with web clients
# async_mode='threading' allows concurrent handling of multiple connections
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

# Global state management
websockets = {}  # Dictionary to store active WebSocket connections to printers {printer_id: ws_connection}
_ws_threads = {}  # Thread running run_forever() for each printer {printer_id: Thread}
printers = {}    # Dictionary to store discovered printers {printer_id: printer_info}
uart_connections = {}  # Active UART/serial printer connections {printer_id: UARTPrinter}

# ===== Printer connection stability tuning =====
# Chitu mainboards run a small Mongoose server that also serves files on the
# same port (3030) as the SDCP websocket. While the printer is exposing/lifting
# or serving a file it can easily take several seconds to answer a ping, so the
# keepalive must be forgiving. These values can be overridden with env vars.
#
# Pings are now DISABLED by default (interval 0). During a print the mainboard
# can be unresponsive for longer than any sane pong timeout on a single layer,
# and a late pong made websocket-client tear the socket down and reconnect -
# which is what made a perfectly healthy printer flap offline. The printer
# already pushes sdcp/status roughly once a second, so that traffic is used as
# the liveness signal instead (see ws_msg_handler). Set CHITUI_WS_PING_INTERVAL
# to a non-zero value to restore protocol-level pings.
WS_PING_INTERVAL = int(os.environ.get('CHITUI_WS_PING_INTERVAL', 0))    # 0 = no ws pings
WS_PING_TIMEOUT = int(os.environ.get('CHITUI_WS_PING_TIMEOUT', 30))     # only used if interval > 0
WS_RECONNECT_DELAY = int(os.environ.get('CHITUI_WS_RECONNECT', 2))      # seconds before reconnecting
# How long a printer may be without any traffic before it is reported offline.
PRINTER_OFFLINE_GRACE = int(os.environ.get('CHITUI_OFFLINE_GRACE', 20))

# Last time traffic (or a live socket) was observed for each printer
# {printer_id: timestamp}
_printer_last_seen: dict = {}

# ===== Plugin System Initialization =====
# Load and initialize plugins from the 'plugins' directory
# Plugins can extend functionality (GPIO control, cameras, monitoring, etc.)
plugin_manager = PluginManager(os.path.join(os.path.dirname(__file__), 'plugins'))

# In-memory store for streaming plugin install jobs  {job_id: queue.Queue}
_plugin_install_jobs: dict = {}

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
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(PROJECT_ROOT, 'data')

# LOCAL_FOLDER is always the Pi's local storage - never aliased to USB.
LOCAL_FOLDER = os.path.join(DATA_FOLDER, 'uploads')
os.makedirs(LOCAL_FOLDER, exist_ok=True)

if not USE_USB_GADGET:
    UPLOAD_FOLDER = LOCAL_FOLDER

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

def _clean_temp_folder():
    """Remove leftover files AND directories from the temp folder.

    This used to skip directories, which meant a scratch folder left behind
    by an interrupted update or plugin install (containing a release tarball
    that can run to tens of megabytes) survived every reboot with nothing to
    ever remove it. Both are cleared now, and the freed space is logged.
    """
    try:
        files = 0
        dirs = 0
        freed = 0
        for entry in Path(TEMP_FOLDER).iterdir():
            try:
                if entry.is_file():
                    freed += entry.stat().st_size
                    entry.unlink()
                    files += 1
                elif entry.is_dir():
                    for root, _sub, names in os.walk(entry):
                        for name in names:
                            try:
                                freed += os.path.getsize(os.path.join(root, name))
                            except OSError:
                                pass
                    shutil.rmtree(entry, ignore_errors=True)
                    dirs += 1
            except Exception as inner:
                logger.warning(f"[temp] Could not remove {entry.name}: {inner}")

        if files or dirs:
            logger.info(f"[temp] Removed {files} stale file(s) and {dirs} "
                        f"stale folder(s), freeing {freed / (1024 * 1024):.1f} MB")
    except Exception as e:
        logger.warning(f"[temp] Could not clean temp folder: {e}")

_clean_temp_folder()

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
        """No-op. Kept so extract_thumbnails() keeps its shape.

        Orientation is now explicit and applied centrally by
        extract_thumbnail_for_file(); see apply_thumbnail_transform(). The old
        brightness-based guess rotated correct previews by 90 degrees whenever a
        model happened to be tall and light against a dark background.
        """
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
        # get_flattened_data() replaces getdata() in Pillow 14+
        if hasattr(img, 'get_flattened_data'):
            pixels = list(img.get_flattened_data())
        else:
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
        """No-op - see the note on CtbThumbnailExtractor._smart_rotate."""
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


# ── Thumbnail orientation ─────────────────────────────────────────────────────
#
# There is no reliable way to work out which way up a 3D render "should" be from
# its pixels, so ChitUI doesn't try to guess. Orientation is explicit:
#
#   * a global default, for when a slicer writes every preview the same wrong way
#     (a bottom-up scanline order shows up as every thumbnail being flipped)
#   * a per-file override, because only a human knows how a given model should sit
#
# The old _detect_orientation() compared the brightness of edge rows against edge
# columns and rotated 90 degrees when the vertical ones won by 10%. That can't
# express a 180 degree flip at all, and it fires on any model that happens to be
# tall and light against a dark background - silently rotating correct previews.
# It is no longer used for anything.

VALID_THUMB_TRANSFORMS = ('none', 'rot90', 'rot180', 'rot270', 'flipv', 'fliph')


def get_global_thumbnail_transform() -> str:
    """Default transform applied to every extracted thumbnail."""
    try:
        value = load_settings().get('thumbnail_transform', 'none')
        return value if value in VALID_THUMB_TRANSFORMS else 'none'
    except Exception:
        return 'none'


def apply_thumbnail_transform(img, transform: str):
    """Apply one named transform to a PIL image. Unknown names pass through."""
    if not transform or transform == 'none':
        return img
    try:
        if transform == 'rot90':
            return img.rotate(-90, expand=True)   # clockwise
        if transform == 'rot180':
            return img.rotate(180, expand=True)
        if transform == 'rot270':
            return img.rotate(90, expand=True)    # counter-clockwise
        if transform == 'flipv':
            return ImageOps.flip(img)             # top-bottom, fixes bottom-up rows
        if transform == 'fliph':
            return ImageOps.mirror(img)           # left-right
    except Exception as e:
        logger.warning(f"[thumb] transform '{transform}' failed: {e}")
    return img


def compose_thumbnail_transforms(*transforms):
    """Collapse a chain of transforms into the single equivalent rotation.

    Only used for the rotate buttons, where repeated clicks would otherwise
    stack into an ever-growing list.
    """
    rotation = 0
    flip_v = False
    for t in transforms:
        if t == 'rot90':
            rotation = (rotation + 90) % 360
        elif t == 'rot180':
            rotation = (rotation + 180) % 360
        elif t == 'rot270':
            rotation = (rotation + 270) % 360
        elif t == 'flipv':
            flip_v = not flip_v
        elif t == 'fliph':
            flip_v = not flip_v
            rotation = (rotation + 180) % 360
    if flip_v:
        # A vertical flip isn't a rotation, so it can't be folded away - keep it
        # and report the rotation separately.
        return 'flipv' if rotation == 0 else f'flipv+{rotation}'
    return {0: 'none', 90: 'rot90', 180: 'rot180', 270: 'rot270'}[rotation]


def get_file_thumbnail_transform(filename: str) -> str:
    """Per-file override if set, otherwise the global default."""
    try:
        entry = file_db.get_file(filename) or {}
        override = entry.get('thumbnail_transform')
        if override in VALID_THUMB_TRANSFORMS:
            return override
    except Exception:
        pass
    return get_global_thumbnail_transform()


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

        # Apply the orientation the user asked for, if any
        transform = get_file_thumbnail_transform(filepath.name)
        if transform != 'none':
            for thumb in (small_thumb, big_thumb):
                path = os.path.join(output_dir, thumb)
                if os.path.exists(path):
                    try:
                        with Image.open(path) as im:
                            apply_thumbnail_transform(im.convert('RGB'), transform).save(path)
                    except Exception as e:
                        logger.warning(f"[thumb] could not orient {thumb}: {e}")

        return True, small_thumb, big_thumb
    except Exception as e:
        logger.error(f"Error extracting thumbnail: {e}")
        import traceback
        traceback.print_exc()
        return False, '', ''



# ── Shared thumbnail-fetch state ──────────────────────────────────────────────
# Both thumbnail paths (the on-demand /thumbnails/<f> handler and the file-list
# prefetcher) pull source files off the printer over HTTP. They share this state
# so they can't stampede the printer with duplicate or endlessly-repeated work.

# Stems currently being fetched, so two callers never download the same file at once
_thumb_in_progress: set = set()
_thumb_in_progress_lock = threading.Lock()

# The printer's HTTP server and its SDCP websocket are the same Mongoose
# instance on port 3030, and it has a very small connection pool. Fetching
# thumbnail headers in parallel starves the websocket, which then misses pings
# and drops the connection. Serialize prefetching and pace it a little.
_thumb_fetch_gate = threading.Lock()
_THUMB_FETCH_PACING = float(os.environ.get('CHITUI_THUMB_PACING', 0.5))  # seconds between files

# Stems whose extraction FAILED, with the time of the last attempt.
#
# Without this, a file whose thumbnail can't be extracted gets re-downloaded in
# full every single time a file list arrives - and a file list arrives on every
# refresh, printer reconnect, and tab switch. A 49 MB .goo that fails to parse
# will saturate the printer's WiFi link forever, which is exactly what makes
# unrelated downloads crawl.
_thumb_failed: dict = {}
_thumb_failed_lock = threading.Lock()
_THUMB_RETRY_AFTER = 6 * 3600  # seconds before re-attempting a failed extraction

# Thumbnails live in the file header, so there is no reason to pull the whole
# model. GOO keeps both previews at a fixed offset ~195 KB in; CTB stores a
# preview offset in its header, usually also near the start. Grab a generous
# slice and only fall back to the full file if extraction fails on the slice.
_THUMB_PARTIAL_BYTES = {'.goo': 512 * 1024, '.ctb': 2 * 1024 * 1024}


def _thumb_recently_failed(stem: str) -> bool:
    """True if extraction for this stem failed recently enough to skip retrying."""
    with _thumb_failed_lock:
        last = _thumb_failed.get(stem)
    return last is not None and (time.time() - last) < _THUMB_RETRY_AFTER


def _mark_thumb_failed(stem: str):
    with _thumb_failed_lock:
        _thumb_failed[stem] = time.time()


def _clear_thumb_failed(stem: str):
    with _thumb_failed_lock:
        _thumb_failed.pop(stem, None)


def _download_source_for_thumbnail(url: str, dest_path: str, ext: str) -> bool:
    """Fetch just enough of a print file to extract its thumbnail.

    Tries a ranged request for the header first. Returns True if something
    usable landed at dest_path. Callers must still handle extraction failure -
    a partial file may not be enough for an unusual CTB layout, in which case
    they can retry with full=True.
    """
    partial = _THUMB_PARTIAL_BYTES.get(ext)
    headers = {'Range': f'bytes=0-{partial - 1}'} if partial else {}
    try:
        resp = requests.get(url, timeout=(5, 120), stream=True, headers=headers)
        # 206 = server honoured the range. 200 = it ignored it and is sending
        # the whole file; that still works, just wastes bandwidth.
        if resp.status_code not in (200, 206):
            resp.close()
            return False
        written = 0
        limit = partial if (partial and resp.status_code == 200) else None
        with open(dest_path, 'wb') as tf:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                tf.write(chunk)
                written += len(chunk)
                # Server ignored Range - stop ourselves once we have the header
                if limit and written >= limit:
                    break
        resp.close()
        return written > 0
    except Exception as e:
        logger.debug(f"[thumb] partial fetch failed for {url}: {e}")
        return False


def _try_extract_with_fallback(url: str, source_name: str, ext: str):
    """Partial fetch → extract. If that fails, full fetch → extract.

    Returns (ok, small, big).
    """
    tmp_path = os.path.join(TEMP_FOLDER, source_name)

    for attempt, full in enumerate((False, True)):
        try:
            if full:
                resp = requests.get(url, timeout=(5, 300), stream=True)
                if resp.status_code != 200:
                    resp.close()
                    return False, '', ''
                with open(tmp_path, 'wb') as tf:
                    for chunk in resp.iter_content(chunk_size=262144):
                        if chunk:
                            tf.write(chunk)
                resp.close()
                logger.info(f"[thumb] Full fetch fallback for {source_name}")
            else:
                if not _download_source_for_thumbnail(url, tmp_path, ext):
                    continue

            ok, small, big = extract_thumbnail_for_file(
                Path(tmp_path), output_to_thumbnails=True)
            if ok:
                return True, small, big
        except Exception as e:
            logger.debug(f"[thumb] attempt {attempt} failed for {source_name}: {e}")
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    return False, '', ''


def fetch_printer_thumbnails_for_filelist(printer_ip, file_list):
    """Background task: for each /local/ or /usb/ file in file_list that is
    missing a cached thumbnail, fetch the file header from the printer's
    Mongoose HTTP server, extract the thumbnail, and cache it.
    Called whenever a fresh file list arrives from the printer.
    """
    SDCP_TO_HTTP = [
        ('/local/', f'http://{printer_ip}:3030/media/mmcblk0p3/'),
        ('/usb/',   f'http://{printer_ip}:3030/mnt/udisk/'),
        ('/usb/',   f'http://{printer_ip}:3030/mnt/usb/'),
        ('/usb/',   f'http://{printer_ip}:3030/mnt/'),
    ]
    for file_path in file_list:
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in ('.goo', '.ctb'):
            continue
        source_name = os.path.basename(file_path)
        stem = os.path.splitext(source_name)[0]
        small_path = os.path.join(THUMBNAILS_FOLDER, f"{stem}_small.png")
        big_path   = os.path.join(THUMBNAILS_FOLDER, f"{stem}_big.png")
        if os.path.exists(small_path) and os.path.exists(big_path):
            continue  # already cached

        # Don't grind away on a file we already know we can't parse
        if _thumb_recently_failed(stem):
            logger.debug(f"[thumb-prefetch] Skipping {source_name}, extraction failed recently")
            continue

        # Don't start a second download of a file already in flight
        with _thumb_in_progress_lock:
            if stem in _thumb_in_progress:
                continue
            _thumb_in_progress.add(stem)

        try:
            # One file at a time, across all prefetch threads, so we never have
            # several HTTP transfers competing with the SDCP websocket on the
            # printer's single Mongoose server.
            with _thumb_fetch_gate:
                for sdcp_prefix, http_base in SDCP_TO_HTTP:
                    if not file_path.startswith(sdcp_prefix):
                        continue
                    url = http_base + source_name
                    try:
                        head = requests.head(url, timeout=5)
                        if head.status_code != 200:
                            continue
                        logger.info(f"[thumb-prefetch] Fetching header of {source_name} from {url}")
                        ok, small, big = _try_extract_with_fallback(url, source_name, ext)
                        if ok:
                            file_db.add_file(source_name, small, big)
                            _clear_thumb_failed(stem)
                            logger.info(f"[thumb-prefetch] Cached thumbnail for {source_name}")
                        else:
                            _mark_thumb_failed(stem)
                            logger.warning(
                                f"[thumb-prefetch] Could not extract thumbnail for {source_name}; "
                                f"not retrying for {_THUMB_RETRY_AFTER // 3600}h")
                        break
                    except Exception as e:
                        logger.debug(f"[thumb-prefetch] Failed {url}: {e}")
                        continue
                # Give the printer's websocket room to breathe between files
                if _THUMB_FETCH_PACING > 0:
                    time.sleep(_THUMB_FETCH_PACING)
        finally:
            with _thumb_in_progress_lock:
                _thumb_in_progress.discard(stem)


def scan_and_extract_missing_thumbnails(extra_folders=None):
    """Startup scan: extract thumbnails for files not yet in the database.
    extra_folders: list of additional folder paths to scan (e.g. USB mount
    passed in after it has been confirmed writable in main()).
    """
    folders_to_scan = []
    if extra_folders:
        for ep in extra_folders:
            p = Path(ep)
            if p.exists() and p not in folders_to_scan:
                folders_to_scan.append(p)
    if USE_USB_GADGET and os.path.exists(USB_GADGET_FOLDER):
        p = Path(USB_GADGET_FOLDER)
        if p not in folders_to_scan:
            folders_to_scan.append(p)
    if os.path.exists(LOCAL_FOLDER):
        p = Path(LOCAL_FOLDER)
        if p not in folders_to_scan:
            folders_to_scan.append(p)
    processed = 0
    for folder in folders_to_scan:
        for f in folder.iterdir():
            if f.suffix.lower() not in ('.goo', '.ctb'):
                continue
            entry = file_db.get_file(f.name)
            small_path = Path(THUMBNAILS_FOLDER) / f"{f.stem}_small.png"
            big_path   = Path(THUMBNAILS_FOLDER) / f"{f.stem}_big.png"
            if entry and small_path.exists() and big_path.exists():
                continue
            logger.info(f"[scan] Extracting missing thumbnail: {f.name}")
            ok, small, big = extract_thumbnail_for_file(f, output_to_thumbnails=True)
            if ok:
                file_db.add_file(f.name, small, big)
                processed += 1
    if processed:
        logger.info(f"[scan] Extracted thumbnails for {processed} previously-unindexed file(s)")
    else:
        logger.info("[scan] All files already have thumbnails")


if WATCHDOG_AVAILABLE:
    class PrintFileWatcher(FileSystemEventHandler):
        """Auto-extract thumbnails when new .goo/.ctb files appear."""
        def _handle(self, path):
            p = Path(path)
            if p.suffix.lower() not in ('.goo', '.ctb'):
                return
            logger.info(f"[watcher] New file detected: {p.name}")
            ok, small, big = extract_thumbnail_for_file(p, output_to_thumbnails=True)
            if ok:
                file_db.add_file(p.name, small, big)
        def on_created(self, event):
            if not event.is_directory:
                self._handle(event.src_path)
        def on_moved(self, event):
            if not event.is_directory:
                self._handle(event.dest_path)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024  # 512 MB max upload

# Tell Werkzeug to write incoming upload temp files to our TEMP_FOLDER
# (on the data partition) instead of /tmp (on the SD card root).
# This prevents ENOSPC on the root filesystem when uploading large files
# while the thumbnail cache is active.
import tempfile as _tempfile
_tempfile.tempdir = TEMP_FOLDER


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
camera_capture_thread = None  # Reference to capture thread for proper cleanup


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

        # Skip frames to reduce latency, checking running flag each iteration
        # to allow quick exit when camera is stopped
        for _ in range(3):
            if not self.running or not self.cap:
                return False, None
            self.cap.grab()

        if not self.running or not self.cap:
            return False, None

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
    global camera_stream_active, camera_instance, camera_latest_frame, camera_printer_ip, camera_capture_thread

    if not CAMERA_SUPPORT:
        return jsonify({'ok': False, 'msg': 'Camera support not installed. Run: pip install opencv-python-headless'})

    if camera_stream_active:
        return jsonify({'ok': True, 'msg': 'Camera already running'})

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
            # Save thread reference for proper cleanup on stop
            camera_capture_thread = Thread(target=camera_capture_frames, daemon=True)
            camera_capture_thread.start()
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
    global camera_stream_active, camera_instance, camera_latest_frame, camera_capture_thread

    try:
        # Signal the capture thread to stop
        camera_stream_active = False

        # Also signal the camera instance to stop reading frames
        if camera_instance:
            camera_instance.running = False

        # Wait for capture thread to finish (up to 2 seconds)
        # This ensures the thread exits before we release the camera
        if camera_capture_thread and camera_capture_thread.is_alive():
            logger.info("Waiting for camera capture thread to finish...")
            camera_capture_thread.join(timeout=2.0)
            if camera_capture_thread.is_alive():
                logger.warning("Camera capture thread did not stop in time")

        camera_capture_thread = None
        camera_latest_frame = None

        # Now safe to release the camera since thread has stopped
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

    auth = settings.get('auth', {})
    auth_missing = not auth
    auth_incomplete = auth and 'password_hash' not in auth

    # Only reinitialize if there is genuinely no password hash at all
    if auth_missing or auth_incomplete:
        if auth_incomplete:
            logger.error("Auth configuration is incomplete/corrupt - reinitializing")

        # Preserve any existing valid fields, only add what is missing
        if 'password_hash' not in auth:
            settings.setdefault('auth', {})
            settings['auth']['password_hash'] = generate_password_hash('admin')
            settings['auth'].setdefault('require_password_change', True)
            settings['auth'].setdefault('session_timeout', 0)

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
    """Update settings.

    This used to be `save_settings(request.json)` - a blind whole-file
    overwrite. Two things went wrong with that:

      * GET /settings deliberately strips auth.password_hash before sending
        settings to the browser, and saveSettings() posts that same object
        straight back. Clicking Save in the Settings dialog therefore erased
        the stored password hash.
      * Any key written by another component after the page was loaded (the
        raspicam block, for instance) was absent from the browser's copy and
        got dropped on the next Save.

    Top-level keys present in the payload still replace their stored value
    outright, so removing a printer from the dialog still works. Keys the
    payload does not mention are left alone, and auth is merged rather than
    replaced so a stripped hash can never clobber the real one.
    """
    try:
        incoming = request.json
        if not isinstance(incoming, dict):
            return jsonify({"success": False, "message": "Invalid settings payload"}), 400

        current = load_settings()
        merged = dict(current)

        for key, value in incoming.items():
            if key == 'auth' and isinstance(value, dict):
                auth = dict(current.get('auth', {}))
                auth.update(value)
                stored_hash = current.get('auth', {}).get('password_hash')
                if not value.get('password_hash') and stored_hash:
                    auth['password_hash'] = stored_hash
                merged['auth'] = auth
            else:
                merged[key] = value

        preserved = [k for k in current if k not in incoming]
        if preserved:
            logger.debug(f"update_settings: preserving untouched keys {preserved}")

        if save_settings(merged):
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

        if result.returncode == 0 and result.stdout.strip():
            packages = json.loads(result.stdout)
            packages_sorted = sorted(packages, key=lambda x: x['name'].lower())
            return jsonify({
                "success": True,
                "packages": packages_sorted,
                "count": len(packages_sorted)
            })

        # pip list returned empty output or non-zero exit — fall through to fallback
        if result.returncode != 0:
            logger.warning(f"pip list failed (rc={result.returncode}): {result.stderr}")
        else:
            logger.warning("pip list returned empty output; using importlib.metadata fallback")

    except subprocess.TimeoutExpired:
        logger.warning("pip list timed out; using importlib.metadata fallback")
    except Exception as e:
        logger.warning(f"pip list error ({e}); using importlib.metadata fallback")

    # Fallback: read package metadata directly via stdlib (no subprocess required)
    try:
        import importlib.metadata as _meta
        packages_sorted = sorted(
            [{"name": d.metadata["Name"], "version": d.version}
             for d in _meta.distributions()
             if d.metadata.get("Name")],
            key=lambda x: x['name'].lower()
        )
        return jsonify({
            "success": True,
            "packages": packages_sorted,
            "count": len(packages_sorted)
        })
    except Exception as e:
        logger.error(f"Error getting Python packages: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/thumbnails/orient', methods=['POST'])
@login_required
def thumbnails_orient():
    """Rotate or flip one file's cached thumbnails, and remember the choice.

    Body: {"filename": "goblin.goo", "transform": "rot90"|"rot180"|"rot270"|"flipv"|"fliph"|"none"}

    The transform is applied to the cached PNGs immediately and stored against
    the file, so it survives a cache clear and gets re-applied if the thumbnail
    is ever re-extracted from the printer.
    """
    try:
        data = request.get_json(silent=True) or {}
        filename = (data.get('filename') or '').strip()
        transform = (data.get('transform') or '').strip()

        if not filename:
            return jsonify({'success': False, 'message': 'filename is required'}), 400
        if transform not in VALID_THUMB_TRANSFORMS:
            return jsonify({'success': False,
                            'message': f'transform must be one of {", ".join(VALID_THUMB_TRANSFORMS)}'}), 400

        # Strip any /local/ or /usb/ prefix and neutralise path traversal
        base = secure_filename(os.path.basename(filename))
        stem = os.path.splitext(base)[0]
        if not stem:
            return jsonify({'success': False, 'message': 'Invalid filename'}), 400

        rotated = []
        for suffix in ('_small.png', '_big.png'):
            path = os.path.join(THUMBNAILS_FOLDER, f"{stem}{suffix}")
            if not os.path.exists(path):
                continue
            try:
                with Image.open(path) as im:
                    apply_thumbnail_transform(im.convert('RGB'), transform).save(path)
                rotated.append(os.path.basename(path))
            except Exception as e:
                logger.warning(f"[thumb] rotate failed for {path}: {e}")

        if not rotated:
            return jsonify({'success': False,
                            'message': 'No cached thumbnail found for that file'}), 404

        # Fold the new transform into whatever was already stored, so pressing
        # rotate four times returns to the original rather than stacking.
        entry = file_db.get_file(base) or {}
        combined = compose_thumbnail_transforms(
            entry.get('thumbnail_transform', 'none'), transform)
        db = file_db._load_data()
        db.setdefault(base, {
            'thumbnail_small': f"{stem}_small.png",
            'thumbnail_big': f"{stem}_big.png",
        })['thumbnail_transform'] = combined
        file_db._save_data(db)

        logger.info(f"[thumb] Oriented {base} by {transform} (stored as {combined})")
        return jsonify({'success': True, 'rotated': rotated,
                        'stored_transform': combined})
    except Exception as e:
        logger.error(f"Error orienting thumbnail: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/thumbnails/orient/global', methods=['GET', 'POST'])
@login_required
def thumbnails_orient_global():
    """Get or set the default transform applied to every new thumbnail.

    Use this when a slicer writes every preview the same wrong way round -
    a bottom-up scanline order, for instance, makes every thumbnail come out
    vertically flipped, and 'flipv' fixes the lot in one go.
    """
    if request.method == 'GET':
        return jsonify({'success': True,
                        'transform': get_global_thumbnail_transform(),
                        'options': list(VALID_THUMB_TRANSFORMS)})
    try:
        data = request.get_json(silent=True) or {}
        transform = (data.get('transform') or '').strip()
        if transform not in VALID_THUMB_TRANSFORMS:
            return jsonify({'success': False,
                            'message': f'transform must be one of {", ".join(VALID_THUMB_TRANSFORMS)}'}), 400

        settings = load_settings()
        settings['thumbnail_transform'] = transform
        save_settings(settings)

        # Existing cached thumbnails were written under the old default, so they
        # stay as they are. Clearing the cache re-extracts them with the new one.
        logger.info(f"[thumb] Global thumbnail transform set to {transform}")
        return jsonify({'success': True, 'transform': transform,
                        'note': 'Applies to newly extracted thumbnails. '
                                'Clear the thumbnail cache to re-apply to existing ones.'})
    except Exception as e:
        logger.error(f"Error setting global thumbnail transform: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


# ===== Network Configuration API =====
#
# The frontend (web/js/network.js) has always called these three endpoints;
# they were never implemented, which is why Settings > Network showed "error"
# and dashes for every field - the AJAX call was simply 404ing.
#
# All privileged work goes through scripts/network_helper.sh, the single
# command granted passwordless sudo by scripts/setup_network_sudo.sh. Nothing
# here builds a shell string: arguments are passed as a list and re-validated
# inside the helper.

NETWORK_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'scripts', 'network_helper.sh')
NETWORK_SETUP_CMD = f"sudo bash {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts', 'setup_network_sudo.sh')}"

# Static-IP changes can strand the Pi on an address nobody can reach, so a
# change is provisional until the browser confirms it can still talk to us.
NETWORK_CONFIRM_WINDOW = 90
_net_pending = {}          # {'timer': Thread, 'revert': callable, 'deadline': ts}
_net_pending_lock = threading.Lock()


def _run_network_helper(args, timeout=25):
    """Run the privileged helper. Returns (ok, stdout, stderr)."""
    if not os.path.exists(NETWORK_HELPER):
        return False, '', f'helper script missing: {NETWORK_HELPER}'
    try:
        proc = subprocess.run(
            ['sudo', '-n', NETWORK_HELPER] + [str(a) for a in args],
            capture_output=True, text=True, timeout=timeout)
        return proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', 'helper timed out'
    except Exception as e:
        return False, '', str(e)


def _network_sudo_ok():
    """True if we can invoke the helper as root without a password prompt."""
    ok, _, _ = _run_network_helper(['status', 'lo'], timeout=10)
    return ok


def _list_network_interfaces():
    """Physical, up-capable interfaces - no loopback, docker, veth or tailscale."""
    skip_prefixes = ('lo', 'docker', 'veth', 'br-', 'tailscale', 'zt', 'wg')
    found = []
    try:
        for name in sorted(os.listdir('/sys/class/net')):
            if name.startswith(skip_prefixes):
                continue
            found.append(name)
    except Exception as e:
        logger.debug(f"[network] interface scan failed: {e}")
    return found


def _pick_default_interface(interfaces):
    """Prefer whichever interface currently carries the default route."""
    try:
        proc = subprocess.run(['ip', '-4', 'route', 'show', 'default'],
                              capture_output=True, text=True, timeout=5)
        for token in proc.stdout.split():
            if token in interfaces:
                return token
    except Exception:
        pass
    return interfaces[0] if interfaces else None


def _parse_helper_status(text):
    """Turn the helper's key=value output into a dict."""
    out = {}
    for line in text.splitlines():
        if '=' in line:
            k, _, v = line.partition('=')
            out[k.strip()] = v.strip()
    return out


@app.route('/network/status', methods=['GET'])
@login_required
def network_status():
    """Current + configured network state for Settings > Network."""
    try:
        settings = load_settings()
        net_cfg = settings.get('network', {})

        interfaces = _list_network_interfaces()
        iface = request.args.get('interface') or net_cfg.get('interface') \
            or _pick_default_interface(interfaces)

        sudo_ok = _network_sudo_ok()

        current = {}
        configured_mode = net_cfg.get('mode', 'dhcp')

        if iface:
            ok, out, err = _run_network_helper(['status', iface])
            if ok:
                parsed = _parse_helper_status(out)
                dns = [d for d in (parsed.get('dns') or '').split(',') if d]
                current = {
                    'ip': parsed.get('ip') or None,
                    'netmask': parsed.get('netmask') or None,
                    'prefix': int(parsed['prefix']) if parsed.get('prefix', '').isdigit() else None,
                    'gateway': parsed.get('gateway') or None,
                    'dns': dns,
                    'backend': parsed.get('backend'),
                }
                # The live system is the source of truth for mode; the saved
                # setting is only a fallback when we can't read the system.
                if parsed.get('mode'):
                    configured_mode = parsed['mode']
            else:
                logger.debug(f"[network] helper status failed: {err}")
                # Fall back to reading the address without sudo so the card
                # isn't blank just because the sudoers rule isn't installed yet.
                current = _read_interface_unprivileged(iface)

        with _net_pending_lock:
            pending = bool(_net_pending)

        return jsonify({
            'success': True,
            'interfaces': interfaces,
            'interface': iface,
            'current': current,
            'configured_mode': configured_mode,
            'configured_static': net_cfg.get('static', {}),
            'configured_port': settings.get('web_port', int(os.environ.get('PORT', 8080))),
            'sudo_ok': sudo_ok,
            'setup_cmd': NETWORK_SETUP_CMD,
            'pending_confirmation': pending,
        })
    except Exception as e:
        logger.error(f"Error reading network status: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


def _read_interface_unprivileged(iface):
    """Best-effort address read using plain `ip`, no sudo required."""
    info = {'ip': None, 'netmask': None, 'prefix': None, 'gateway': None, 'dns': []}
    try:
        proc = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'dev', iface],
                              capture_output=True, text=True, timeout=5)
        parts = proc.stdout.split()
        if 'inet' in parts:
            cidr = parts[parts.index('inet') + 1]
            addr, _, prefix = cidr.partition('/')
            info['ip'] = addr
            if prefix.isdigit():
                info['prefix'] = int(prefix)
                bits = (0xffffffff << (32 - int(prefix))) & 0xffffffff
                info['netmask'] = '.'.join(str((bits >> s) & 0xff) for s in (24, 16, 8, 0))
    except Exception:
        pass
    try:
        proc = subprocess.run(['ip', '-4', 'route', 'show', 'default', 'dev', iface],
                              capture_output=True, text=True, timeout=5)
        tokens = proc.stdout.split()
        if 'via' in tokens:
            info['gateway'] = tokens[tokens.index('via') + 1]
    except Exception:
        pass
    try:
        with open('/etc/resolv.conf') as f:
            info['dns'] = [ln.split()[1] for ln in f
                           if ln.startswith('nameserver') and len(ln.split()) > 1]
    except Exception:
        pass
    return info


def _schedule_network_revert(iface, previous_mode, previous_static):
    """Arm the auto-revert. Cancelled by /network/confirm."""
    def revert():
        time.sleep(NETWORK_CONFIRM_WINDOW)
        with _net_pending_lock:
            if not _net_pending:
                return  # confirmed in time
            _net_pending.clear()
        logger.warning("Network change was not confirmed in time - reverting")
        if previous_mode == 'static' and previous_static.get('ip'):
            _run_network_helper([
                'static', iface,
                previous_static.get('ip'), previous_static.get('netmask', '255.255.255.0'),
                previous_static.get('gateway', ''),
            ] + (previous_static.get('dns') or [])[:2])
        else:
            _run_network_helper(['dhcp', iface])
        settings = load_settings()
        settings.setdefault('network', {})['mode'] = previous_mode
        settings['network']['static'] = previous_static
        save_settings(settings)

    t = Thread(target=revert, daemon=True)
    with _net_pending_lock:
        _net_pending.clear()
        _net_pending['deadline'] = time.time() + NETWORK_CONFIRM_WINDOW
    t.start()


@app.route('/network/apply', methods=['POST'])
@login_required
def network_apply():
    """Apply DHCP/static settings and/or record a new web server port."""
    try:
        data = request.get_json(silent=True) or {}
        mode = (data.get('mode') or 'dhcp').lower()
        iface = data.get('interface')
        confirm_risk = bool(data.get('confirm_risk'))

        if mode not in ('dhcp', 'static'):
            return jsonify({'success': False, 'message': f'Unknown mode: {mode}'}), 400

        interfaces = _list_network_interfaces()
        if not iface:
            iface = _pick_default_interface(interfaces)
        if iface not in interfaces:
            return jsonify({'success': False,
                            'message': f'Unknown network interface: {iface}'}), 400

        settings = load_settings()
        net_cfg = settings.setdefault('network', {})
        previous_mode = net_cfg.get('mode', 'dhcp')
        previous_static = dict(net_cfg.get('static', {}))

        # ── Web server port (no privileges needed, just persisted) ────────────
        port_changed = False
        new_port = None
        if data.get('port') not in (None, ''):
            try:
                new_port = int(data['port'])
            except (TypeError, ValueError):
                return jsonify({'success': False, 'message': 'Port must be a number'}), 400
            if not (1 <= new_port <= 65535):
                return jsonify({'success': False, 'message': 'Port must be between 1 and 65535'}), 400
            current_port = settings.get('web_port', int(os.environ.get('PORT', 8080)))
            if new_port != current_port:
                settings['web_port'] = new_port
                port_changed = True

        # ── Network change ────────────────────────────────────────────────────
        applied_network = False
        warning = None

        wants_network_change = (
            mode != previous_mode or
            (mode == 'static' and {
                'ip': data.get('ip'), 'netmask': data.get('netmask'),
                'gateway': data.get('gateway'), 'dns': data.get('dns') or []
            } != previous_static)
        )

        if wants_network_change:
            if not _network_sudo_ok():
                return jsonify({
                    'success': False,
                    'needs_sudo_setup': True,
                    'setup_cmd': NETWORK_SETUP_CMD,
                    'message': 'ChitUI does not have permission to change network settings yet.'
                }), 403

            if mode == 'static':
                if not confirm_risk:
                    return jsonify({
                        'success': False,
                        'requires_confirmation': True,
                        'message': 'A static IP can make ChitUI unreachable. Confirm to continue.'
                    }), 409

                ip_addr = (data.get('ip') or '').strip()
                netmask = (data.get('netmask') or '255.255.255.0').strip()
                gateway = (data.get('gateway') or '').strip()
                dns = [d.strip() for d in (data.get('dns') or []) if d and d.strip()][:2]

                if not ip_addr or not gateway:
                    return jsonify({'success': False,
                                    'message': 'Static mode needs both an IP address and a gateway'}), 400

                ok, out, err = _run_network_helper(
                    ['static', iface, ip_addr, netmask, gateway] + dns)
                if not ok:
                    return jsonify({'success': False,
                                    'message': f'Failed to apply static IP: {err or out}'}), 500

                net_cfg['mode'] = 'static'
                net_cfg['static'] = {'ip': ip_addr, 'netmask': netmask,
                                     'gateway': gateway, 'dns': dns}
                applied_network = True
                _schedule_network_revert(iface, previous_mode, previous_static)
                warning = (f'Reverting in {NETWORK_CONFIRM_WINDOW}s unless you confirm '
                           f'ChitUI is still reachable.')
            else:
                ok, out, err = _run_network_helper(['dhcp', iface])
                if not ok:
                    return jsonify({'success': False,
                                    'message': f'Failed to switch to DHCP: {err or out}'}), 500
                net_cfg['mode'] = 'dhcp'
                applied_network = True

            net_cfg['interface'] = iface

        save_settings(settings)

        message = 'Network settings saved.'
        if applied_network and mode == 'static':
            message = f'Static IP {data.get("ip")} is being applied on {iface}.'
        elif applied_network:
            message = f'{iface} switched to DHCP.'
        elif port_changed:
            message = 'Port saved.'

        return jsonify({
            'success': True,
            'message': message,
            'warning': warning,
            'applied_network': applied_network,
            'port_changed': port_changed,
            'new_port': new_port,
            'confirmation_window': NETWORK_CONFIRM_WINDOW,
        })
    except Exception as e:
        logger.error(f"Error applying network settings: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/network/confirm', methods=['POST'])
@login_required
def network_confirm():
    """The browser still reaches us - cancel the pending auto-revert."""
    with _net_pending_lock:
        was_pending = bool(_net_pending)
        _net_pending.clear()
    if was_pending:
        logger.info("Network change confirmed by user - keeping new settings")
    return jsonify({'success': True, 'confirmed': was_pending})


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


# ============================================================================
# SOFTWARE UPDATE API
# ============================================================================
# Checks GitHub for newer ChitUI releases and applies them on request.
# The heavy lifting lives in updater.py; these routes are a thin shell around
# it so the update logic stays testable on its own.

@app.route('/updates/ping', methods=['GET'])
def update_ping():
    """
    Tiny liveness probe with no auth.

    The browser polls this after an update restart to find out when the
    server is back and which version came up. It deliberately skips
    @login_required so the poll keeps working if the session was dropped.
    """
    return jsonify({
        "ok": True,
        "version": updater.get_current_version()
    })


@app.route('/updates/status', methods=['GET'])
@login_required
def update_status():
    """
    Current update situation, served from the cache unless ?force=1.

    Called on every page load, so it must be cheap: updater.check_for_updates
    only talks to GitHub once the cached answer has expired.
    """
    force = request.args.get('force') in ('1', 'true', 'yes')
    on_load = request.args.get('on_load') in ('1', 'true', 'yes')
    upd_settings = updater.get_update_settings(load_settings())

    # A page load must not reach out to GitHub when the user has turned the
    # on-load check off. Answer from what we know and skip the network.
    if on_load and not upd_settings.get('check_on_load', True):
        return jsonify({
            "success": True,
            "enabled": upd_settings.get('enabled', True),
            "current_version": updater.get_current_version(),
            "update_available": False,
            "release": None,
            "checked_at": None,
            "settings": upd_settings,
            "update_in_progress": updater.is_update_running(),
            "skipped_on_load": True
        })

    try:
        result = updater.check_for_updates(upd_settings, force=force)
    except Exception as e:
        logger.error(f"Update check failed: {e}")
        return jsonify({
            "success": False,
            "enabled": upd_settings.get('enabled', True),
            "current_version": updater.get_current_version(),
            "update_available": False,
            "error": str(e)
        }), 500

    result['settings'] = upd_settings
    result['update_in_progress'] = updater.is_update_running()
    return jsonify(result)


@app.route('/updates/settings', methods=['GET', 'POST'])
@login_required
def update_settings_route():
    """Read or patch the "updates" block of chitui_settings.json."""
    if request.method == 'GET':
        return jsonify({
            "success": True,
            "settings": updater.get_update_settings(load_settings())
        })

    patch = request.json if isinstance(request.json, dict) else {}
    settings = updater.merge_update_settings(load_settings(), patch)

    if not save_settings(settings):
        return jsonify({"success": False, "message": "Could not save settings"}), 500

    return jsonify({
        "success": True,
        "settings": updater.get_update_settings(settings)
    })


@app.route('/updates/skip', methods=['POST'])
@login_required
def update_skip():
    """Stop nagging about one particular version."""
    body = request.json if isinstance(request.json, dict) else {}
    version = body.get('version')
    if not version:
        return jsonify({"success": False, "message": "No version supplied"}), 400

    settings = updater.merge_update_settings(load_settings(),
                                             {'skipped_version': version})
    if not save_settings(settings):
        return jsonify({"success": False, "message": "Could not save settings"}), 500

    logger.info(f"User chose to skip ChitUI version {version}")
    return jsonify({"success": True, "skipped_version": version})


@app.route('/updates/start', methods=['POST'])
@login_required
def update_start():
    """
    Begin an upgrade and hand back a job id to stream the log from.

    The release is re-fetched server-side rather than trusted from the
    request body: the browser could otherwise point the updater at an
    arbitrary tarball URL.
    """
    body = request.json if isinstance(request.json, dict) else {}
    requested = body.get('version')

    upd_settings = updater.get_update_settings(load_settings())

    try:
        check = updater.check_for_updates(upd_settings, force=False)
    except Exception as e:
        return jsonify({"success": False, "message": f"Update check failed: {e}"}), 500

    release = check.get('release')
    if not release:
        return jsonify({
            "success": False,
            "message": check.get('error') or "No release information available."
        }), 400

    latest = release.get('version')

    if requested and requested != latest:
        return jsonify({
            "success": False,
            "message": (f"Version {requested} is no longer the current release "
                        f"(latest is {latest}). Re-check for updates and try again.")
        }), 409

    if not updater.is_newer(latest, updater.get_current_version()):
        return jsonify({
            "success": False,
            "message": f"Already running {updater.get_current_version()} - nothing to install."
        }), 400

    job, error = updater.start_update(release, upd_settings)
    if job is None:
        return jsonify({"success": False, "message": error}), 409

    logger.warning(f"ChitUI update to {latest} started (job {job.id})")
    return jsonify({
        "success": True,
        "job_id": job.id,
        "version": latest
    })


@app.route('/updates/job/<job_id>/stream', methods=['GET'])
@login_required
def update_job_stream(job_id):
    """
    SSE log stream for a running update.

    The job buffers its whole log, so a browser that reconnects replays
    everything it missed instead of watching an empty terminal.
    """
    job = updater.get_job(job_id)
    if job is None:
        return jsonify({"error": "Unknown job id"}), 404

    def generate():
        try:
            for item in job.follow():
                if item is None:
                    yield ": ping\n\n"       # keep the connection alive
                    continue
                event = item.get('type', 'log')
                yield f"event: {event}\ndata: {json.dumps(item)}\n\n"
        except GeneratorExit:
            pass

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


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
    """
    Upload a plugin ZIP and start a background install job.
    Returns a job_id that the client uses to subscribe to the SSE log stream.
    """
    import zipfile
    import tempfile

    if 'plugin' not in request.files:
        return jsonify({"success": False, "message": "No plugin file provided"}), 400

    plugin_file = request.files['plugin']

    if plugin_file.filename == '':
        return jsonify({"success": False, "message": "No file selected"}), 400

    if not plugin_file.filename.endswith('.zip'):
        return jsonify({"success": False, "message": "Plugin must be a ZIP file"}), 400

    # Save the upload to a persistent temp file (the background thread will clean it up)
    tmp = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
    try:
        plugin_file.save(tmp.name)
    finally:
        tmp.close()

    job_id = str(uuid.uuid4())
    log_queue: queue.Queue = queue.Queue()
    _plugin_install_jobs[job_id] = log_queue

    def _install_worker(zip_path: str, q: queue.Queue):
        """Run plugin installation in a background thread, posting log lines to q."""
        import tempfile as _tempfile

        def log(msg: str, level: str = 'info'):
            q.put({"type": "log", "level": level, "msg": msg})
            if level == 'error':
                logger.error(msg)
            else:
                logger.info(msg)

        try:
            log("=== ChitUI Plugin Installer ===")
            log(f"Processing upload...")

            with _tempfile.TemporaryDirectory() as temp_dir:
                # ── Validate ZIP ────────────────────────────────────────────
                log("Verifying ZIP archive...")
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zf:
                        zf.extractall(temp_dir)
                except zipfile.BadZipFile:
                    log("ERROR: File is not a valid ZIP archive.", "error")
                    q.put({"type": "done", "success": False, "message": "Invalid ZIP file"})
                    return

                # ── Find plugin root directory ───────────────────────────────
                extracted_items = [
                    item for item in os.listdir(temp_dir)
                    if not item.startswith('__MACOSX')
                ]
                dirs = [i for i in extracted_items if os.path.isdir(os.path.join(temp_dir, i))]

                if len(dirs) != 1:
                    log("ERROR: ZIP must contain exactly one top-level directory.", "error")
                    q.put({"type": "done", "success": False,
                           "message": "Invalid plugin structure. ZIP must contain a single directory."})
                    return

                plugin_dir_name = dirs[0]
                extracted_plugin_path = os.path.join(temp_dir, plugin_dir_name)
                log(f"Found plugin directory: {plugin_dir_name}")

                # ── Validate required files ──────────────────────────────────
                manifest_path = os.path.join(extracted_plugin_path, 'plugin.json')
                init_path = os.path.join(extracted_plugin_path, '__init__.py')

                if not os.path.exists(manifest_path):
                    log("ERROR: Missing plugin.json manifest.", "error")
                    q.put({"type": "done", "success": False, "message": "Invalid plugin: missing plugin.json"})
                    return

                if not os.path.exists(init_path):
                    log("ERROR: Missing __init__.py entry point.", "error")
                    q.put({"type": "done", "success": False, "message": "Invalid plugin: missing __init__.py"})
                    return

                # ── Parse manifest ───────────────────────────────────────────
                log("Reading plugin.json...")
                try:
                    with open(manifest_path, 'r') as f:
                        manifest = json.load(f)
                except json.JSONDecodeError:
                    log("ERROR: plugin.json is not valid JSON.", "error")
                    q.put({"type": "done", "success": False, "message": "Invalid plugin.json format"})
                    return

                for field in ('name', 'version', 'author'):
                    if field not in manifest:
                        log(f"ERROR: plugin.json missing required field: '{field}'", "error")
                        q.put({"type": "done", "success": False,
                               "message": f"Invalid plugin.json: missing '{field}' field"})
                        return

                plugin_name = manifest['name']
                log(f"Plugin: {plugin_name}  v{manifest['version']}  by {manifest['author']}")
                if manifest.get('description'):
                    log(f"Description: {manifest['description']}")

                # ── Check for conflicts ──────────────────────────────────────
                target_path = os.path.join(plugin_manager.plugins_dir, plugin_dir_name)
                if os.path.exists(target_path):
                    log(f"ERROR: Plugin '{plugin_dir_name}' is already installed. Delete it first.", "error")
                    q.put({"type": "done", "success": False,
                           "message": f"Plugin '{plugin_dir_name}' already exists. Delete it first to reinstall."})
                    return

                # ── Install Python dependencies ──────────────────────────────
                # Check requirements.txt (preferred) then plugin.json[dependencies]
                reqs_file = os.path.join(extracted_plugin_path, 'requirements.txt')
                json_deps = manifest.get('dependencies', [])

                if os.path.exists(reqs_file):
                    log("Found requirements.txt – installing Python dependencies...")
                    pip_cmd = [sys.executable, '-m', 'pip', 'install', '-r', reqs_file]
                    try:
                        proc = subprocess.Popen(
                            pip_cmd + ['--break-system-packages'],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True
                        )
                        for line in proc.stdout:
                            log(line.rstrip())
                        proc.wait()
                        if proc.returncode != 0:
                            log("pip install failed, retrying without --break-system-packages...", "warn")
                            proc2 = subprocess.Popen(
                                pip_cmd + ['--user'],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True
                            )
                            for line in proc2.stdout:
                                log(line.rstrip())
                            proc2.wait()
                            if proc2.returncode != 0:
                                log("ERROR: Failed to install requirements.txt dependencies.", "error")
                                q.put({"type": "done", "success": False,
                                       "message": "Dependency installation failed."})
                                return
                    except Exception as dep_err:
                        log(f"ERROR: {dep_err}", "error")
                        q.put({"type": "done", "success": False, "message": str(dep_err)})
                        return
                    log("requirements.txt dependencies installed successfully.")

                elif json_deps:
                    log(f"Found {len(json_deps)} dependency(s) in plugin.json – installing...")
                    pip_base = [sys.executable, '-m', 'pip', 'install']
                    for dep in json_deps:
                        log(f"  Installing: {dep}")
                        try:
                            proc = subprocess.Popen(
                                pip_base + [dep, '--break-system-packages'],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True
                            )
                            for line in proc.stdout:
                                log(f"    {line.rstrip()}")
                            proc.wait()
                            if proc.returncode != 0:
                                proc2 = subprocess.Popen(
                                    pip_base + [dep, '--user'],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True
                                )
                                for line in proc2.stdout:
                                    log(f"    {line.rstrip()}")
                                proc2.wait()
                                if proc2.returncode != 0:
                                    log(f"ERROR: Failed to install dependency: {dep}", "error")
                                    q.put({"type": "done", "success": False,
                                           "message": f"Failed to install dependency: {dep}"})
                                    return
                        except Exception as dep_err:
                            log(f"ERROR: {dep_err}", "error")
                            q.put({"type": "done", "success": False, "message": str(dep_err)})
                            return
                    log("All plugin.json dependencies installed successfully.")
                else:
                    log("No Python dependencies required.")

                # ── Copy plugin into plugins directory ───────────────────────
                log(f"Installing plugin into: {plugin_manager.plugins_dir}/{plugin_dir_name}")
                try:
                    shutil.copytree(extracted_plugin_path, target_path)
                except Exception as copy_err:
                    log(f"ERROR: Failed to copy plugin files: {copy_err}", "error")
                    q.put({"type": "done", "success": False, "message": str(copy_err)})
                    return

                # ── Hot-load the plugin into the running process ─────────────
                log("Activating plugin in running server...")
                try:
                    result = plugin_manager.load_plugin(plugin_dir_name, app, socketio)
                    if result is not None:
                        log(f"Plugin '{plugin_name}' activated successfully!")
                    else:
                        log("Warning: Plugin installed but could not be activated in the "
                            "running server. A restart may be required.", "warn")
                except Exception as load_err:
                    log(f"Warning: Could not auto-activate plugin ({load_err}). A server restart may be required.", "warn")

                log(f"Plugin '{plugin_name}' installed successfully!")
                log("Click 'Reboot Server' to restart and activate the plugin.")
                q.put({
                    "type": "done",
                    "success": True,
                    "message": f"Plugin '{plugin_name}' installed successfully.",
                    "plugin_id": plugin_dir_name,
                    "plugin_name": plugin_name
                })

        except Exception as exc:
            logger.error(f"Unexpected install error: {exc}")
            import traceback
            traceback.print_exc()
            q.put({"type": "done", "success": False, "message": str(exc)})
        finally:
            # Clean up the uploaded temp file
            try:
                os.unlink(zip_path)
            except OSError:
                pass

    thread = threading.Thread(target=_install_worker, args=(tmp.name, log_queue), daemon=True)
    thread.start()

    return jsonify({"success": True, "job_id": job_id})


@app.route('/plugins/install/<job_id>/stream', methods=['GET'])
def plugin_install_stream(job_id):
    """
    SSE endpoint – streams live installation log for a given job_id.
    Clients subscribe using EventSource and receive 'log' and 'done' events.
    """
    log_queue = _plugin_install_jobs.get(job_id)
    if log_queue is None:
        return jsonify({"error": "Unknown job id"}), 404

    def generate():
        try:
            while True:
                try:
                    item = log_queue.get(timeout=30)
                except queue.Empty:
                    # Keep-alive ping so the connection stays open
                    yield ": ping\n\n"
                    continue

                event_type = item.get("type", "log")
                data = json.dumps(item)
                yield f"event: {event_type}\ndata: {data}\n\n"

                if event_type == "done":
                    # Clean up the job entry after a short grace period
                    def _cleanup():
                        time.sleep(60)
                        _plugin_install_jobs.pop(job_id, None)
                    threading.Thread(target=_cleanup, daemon=True).start()
                    break
        except GeneratorExit:
            pass

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


# ===== Plugin Store API =====
# Backs the Plugin Store dialog and the "plugin updates available" warning.
# Catalog fetching and installation live in plugin_store.py.

@app.route('/plugins/store/catalog', methods=['GET'])
@login_required
def plugin_store_catalog():
    """Full catalog, annotated with what is installed and what has an update."""
    force = request.args.get('force') in ('1', 'true', 'yes')
    store_settings = plugin_store.get_store_settings(load_settings())

    if not store_settings.get('enabled', True):
        return jsonify({
            "success": True, "plugins": [], "update_count": 0,
            "error": None, "disabled": True
        })

    try:
        return jsonify(plugin_store.build_catalog(
            plugin_manager, store_settings, force=force))
    except Exception as e:
        logger.error(f"Plugin store catalog failed: {e}")
        return jsonify({"success": False, "plugins": [], "error": str(e)}), 500


@app.route('/plugins/store/updates', methods=['GET'])
@login_required
def plugin_store_updates():
    """
    Just the update count and the list of what changed.

    The main screen polls this on load, so it stays deliberately small and
    answers from the cache unless ?force=1.
    """
    force = request.args.get('force') in ('1', 'true', 'yes')
    on_load = request.args.get('on_load') in ('1', 'true', 'yes')
    store_settings = plugin_store.get_store_settings(load_settings())

    if not store_settings.get('enabled', True):
        return jsonify({"success": True, "update_count": 0, "updates": [],
                        "disabled": True})

    if on_load and not store_settings.get('check_on_load', True):
        return jsonify({"success": True, "update_count": 0, "updates": [],
                        "skipped_on_load": True})

    try:
        return jsonify(plugin_store.count_updates(
            plugin_manager, store_settings, force=force))
    except Exception as e:
        logger.error(f"Plugin update check failed: {e}")
        return jsonify({"success": False, "update_count": 0, "updates": [],
                        "error": str(e)}), 500


@app.route('/plugins/store/settings', methods=['GET', 'POST'])
@login_required
def plugin_store_settings():
    """Read or patch the "plugin_store" block of chitui_settings.json."""
    if request.method == 'GET':
        return jsonify({"success": True,
                        "settings": plugin_store.get_store_settings(load_settings())})

    patch = request.json if isinstance(request.json, dict) else {}
    settings = plugin_store.merge_store_settings(load_settings(), patch)
    if not save_settings(settings):
        return jsonify({"success": False, "message": "Could not save settings"}), 500
    return jsonify({"success": True,
                    "settings": plugin_store.get_store_settings(settings)})


@app.route('/plugins/store/install', methods=['POST'])
@login_required
def plugin_store_install():
    """
    Install or update one plugin from the catalog.

    The download URL is taken from the catalog rather than the request body:
    trusting the body would let a caller point the installer at any ZIP on the
    internet and have the server unpack it into plugins/.
    """
    body = request.json if isinstance(request.json, dict) else {}
    slug = body.get('slug')
    if not slug:
        return jsonify({"success": False, "message": "No plugin slug supplied"}), 400

    store_settings = plugin_store.get_store_settings(load_settings())
    if not store_settings.get('enabled', True):
        return jsonify({"success": False,
                        "message": "The plugin store is disabled in settings."}), 403

    try:
        catalog = plugin_store.build_catalog(plugin_manager, store_settings)
    except Exception as e:
        return jsonify({"success": False, "message": f"Catalog lookup failed: {e}"}), 500

    entry = next((p for p in catalog.get('plugins', []) if p.get('slug') == slug), None)
    if entry is None:
        return jsonify({"success": False,
                        "message": f"'{slug}' is not in the plugin catalog."}), 404

    if entry.get('blocked'):
        return jsonify({"success": False,
                        "message": entry.get('blocked_reason',
                                             "This plugin is not compatible with "
                                             "your ChitUI version.")}), 409

    download_url = entry.get('download_url')
    if not download_url:
        return jsonify({"success": False,
                        "message": f"'{entry['name']}' has no download URL in the catalog."}), 400

    job_id = plugin_store.start_install(
        plugin_manager, app, socketio, _plugin_install_jobs,
        slug, download_url, entry.get('name'))

    logger.info(f"Plugin store install started: {slug} -> {entry.get('version')} "
                f"(job {job_id})")
    return jsonify({"success": True, "job_id": job_id,
                    "plugin_name": entry.get('name'),
                    "version": entry.get('version')})


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
@login_required
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
    """Get list of available printer images.

    This lists whatever sits directly in web/img, which means anything dropped
    there shows up in the printer picture picker. The built-in theme preview
    used to live there and appeared as a selectable printer image; it now lives
    in web/img/themes/. Subdirectories are skipped, so that folder - and any
    future one - stays out of this list on its own.
    """
    IMAGE_EXTENSIONS = ('.webp', '.png', '.jpg', '.jpeg')

    # Belt and braces for installations upgrading from a build that still has
    # the preview at the old path.
    NON_PRINTER_IMAGES = {'theme_default_preview.png'}

    try:
        img_folder = os.path.join(os.path.dirname(__file__), 'web', 'img')
        images = []

        if os.path.exists(img_folder):
            for file in os.listdir(img_folder):
                if file in NON_PRINTER_IMAGES:
                    continue
                if not file.lower().endswith(IMAGE_EXTENSIONS):
                    continue
                # Skip directories: 'themes' would otherwise slip through if it
                # were ever named with an image extension, and a stray symlink
                # to a folder should not be offered as a picture either.
                if not os.path.isfile(os.path.join(img_folder, file)):
                    continue
                images.append(file)

        images.sort()  # Sort alphabetically
        return jsonify({"success": True, "images": images})
    except Exception as e:
        logger.error(f"Error getting printer images: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/printer/manual', methods=['POST'])
@login_required
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
@login_required
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
            # Read the old IP BEFORE overwriting it. This used to be read after
            # the assignment below, so old_ip was always equal to printer_ip and
            # the "IP changed" branch never ran - editing a printer's address
            # silently left ChitUI talking to the old one until a restart.
            old_ip = printers[printer_id].get("ip")

            printers[printer_id]["ip"] = printer_ip
            printers[printer_id]["name"] = printer_name
            printers[printer_id]["usb_device_type"] = usb_device_type
            if printer_image:
                printers[printer_id]["image"] = printer_image
            elif "image" in printers[printer_id]:
                del printers[printer_id]["image"]

            if old_ip != printer_ip:
                # This branch used to carry its own copy of the connection code,
                # with a broken on_open (it passed the printer *name* where a
                # printer_id was expected, so the printer never got marked
                # online), no entry in _ws_threads, and a global
                # setdefaulttimeout(2) side effect. Use the one real
                # implementation instead.
                logger.info(f"Printer IP changed {old_ip} -> {printer_ip}, reconnecting")
                printers[printer_id]['online'] = False
                _printer_last_seen.pop(printer_id, None)
                connect_printers({printer_id: printers[printer_id]}, force=True)

        socketio.emit('printers', printers)
        return jsonify({"success": True, "message": "Printer updated"})
    except Exception as e:
        logger.error(f"Error updating printer: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


def _shutdown_printer_socket(printer_id):
    """Close a printer's websocket without letting it break the caller.

    WebSocketApp.close() is not thread-safe in websocket-client (checked
    against 1.9.1):

        def close(self, **kwargs):
            self.keep_running = False
            if self.sock:
                self.sock.close(**kwargs)
                if self.sock.close_frame is not None:   # <- self.sock may be
                    ...                                 #    None by now
                self.sock = None

    self.sock.close() wakes the reader thread, whose teardown() sets
    self.sock = None before this thread gets to the next line, so close()
    raises AttributeError: 'NoneType' object has no attribute 'close_frame'.
    The window is only open while a reader thread is actually running, which
    is why this only ever bit an *online* printer.

    The connection is genuinely closed by the time it raises - keep_running is
    already False and the socket is already shut - so swallowing the error is
    safe, and it is the only way to stop a library race from taking the rest of
    the removal down with it.
    """
    ws = websockets.pop(printer_id, None)
    if ws is None:
        return
    try:
        ws.close()
    except Exception as e:
        logger.debug(f"Ignoring error while closing socket for {printer_id}: {e}")
        # close() sets keep_running=False before it can fail, but set it again
        # in case it failed even earlier, so run_forever() cannot reconnect.
        try:
            ws.keep_running = False
        except Exception:
            pass


@app.route('/printer/<printer_id>', methods=['DELETE'])
@login_required
def remove_printer(printer_id):
    """Remove a printer.

    Every step is independent. This used to be one try block starting with
    websockets[printer_id].close(), so the websocket-client race described in
    _shutdown_printer_socket() aborted the whole handler before anything was
    deleted: the route answered 500, the printer stayed in settings, and
    check_printer_connections() saw the dead reader thread and reconnected it a
    couple of seconds later. From the UI that looked like a printer that simply
    refused to be deleted, and no amount of retrying helped because the retry
    hit the same race.
    """
    try:
        _shutdown_printer_socket(printer_id)

        # Drop the thread registry entry too. The reader thread takes a few
        # seconds to unwind after close(); if the same printer were re-added in
        # that window, _ws_thread_alive() would report it as still connected and
        # connect_printers() would skip it.
        _ws_threads.pop(printer_id, None)
        _printer_last_seen.pop(printer_id, None)

        printers.pop(printer_id, None)

        settings = load_settings()
        saved = settings.setdefault("printers", {})
        if printer_id in saved:
            del saved[printer_id]
            if settings.get("default_printer") == printer_id:
                settings.pop("default_printer", None)
            if not save_settings(settings):
                # Otherwise the printer is gone from memory but still on disk,
                # and load_saved_printers() brings it straight back.
                logger.error(f"Removed printer {printer_id} but could not persist settings")
                socketio.emit('printers', printers)
                return jsonify({"success": False,
                                "message": "Printer removed, but settings could not be saved"}), 500

        socketio.emit('printers', printers)
        return jsonify({"success": True, "message": "Printer removed"})
    except Exception as e:
        logger.error(f"Error removing printer: {e}")
        logger.error(traceback.format_exc())
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


# Thread pool for background thumbnail fetches - limits concurrent downloads
# so we don't flood the printer or starve the WebSocket thread
_thumb_pool = _ThreadPoolExecutor(max_workers=2, thread_name_prefix='thumb')

# NOTE: _thumb_in_progress / _thumb_in_progress_lock are defined near
# fetch_printer_thumbnails_for_filelist() so the on-demand path and the
# file-list prefetch path share one guard and can't duplicate each other's work.

def _fetch_and_cache_thumbnail(stem: str, thumb_path: str):
    """
    Worker run in _thumb_pool: try each known HTTP path on each printer,
    download the source file, extract the thumbnail PNG, cache it.
    Never blocks the Flask request thread.
    """
    SDCP_TO_HTTP = [
        '/media/mmcblk0p3/',   # printer internal storage (/local/)
        '/mnt/udisk/',         # USB stick
        '/mnt/usb/',
        '/mnt/',
    ]
    try:
        for printer_id, printer in list(printers.items()):
            printer_ip = printer.get('ip')
            if not printer_ip:
                continue
            for ext in ('.goo', '.ctb'):
                source_name = stem + ext
                for http_dir in SDCP_TO_HTTP:
                    url = f"http://{printer_ip}:3030{http_dir}{source_name}"
                    try:
                        head = requests.head(url, timeout=5)
                        if head.status_code != 200:
                            continue
                        logger.info(f"[thumb] Fetching header of {source_name} from {url}")
                        ok, small, big = _try_extract_with_fallback(url, source_name, ext)
                        if ok:
                            file_db.add_file(source_name, small, big)
                            _clear_thumb_failed(stem)
                            logger.info(f"[thumb] Cached: {source_name}")
                            return  # done
                        # Source file exists but won't parse. Record it so the
                        # prefetcher stops pulling it off the printer on every
                        # file list refresh.
                        _mark_thumb_failed(stem)
                        logger.warning(f"[thumb] Could not extract thumbnail for {source_name}")
                    except Exception as e:
                        logger.debug(f"[thumb] {url}: {e}")
                        continue
    finally:
        with _thumb_in_progress_lock:
            _thumb_in_progress.discard(stem)


@app.route('/thumbnails/<filename>')
def serve_thumbnail(filename):
    """Serve a thumbnail PNG.

    Fast path  — PNG already on disk → serve immediately (1-2ms).
    Medium path — file is on the Pi  → extract inline (fast, no network).
    Slow path   — file is on printer → return 202 Accepted immediately,
                  kick off a background fetch, browser retries after 3s.
    """
    from flask import send_from_directory
    import re as _re

    thumb_path = os.path.join(THUMBNAILS_FOLDER, filename)

    # ── Fast path ─────────────────────────────────────────────────────────────
    if os.path.exists(thumb_path):
        return send_from_directory(THUMBNAILS_FOLDER, filename)

    # ── Parse stem from filename ───────────────────────────────────────────────
    m = _re.match(r'^(.+)_(big|small)\.png$', filename)
    if not m:
        return Response('Not found', status=404)
    stem = m.group(1)

    # ── Medium path: file is on the Pi ────────────────────────────────────────
    for ext in ('.goo', '.ctb'):
        candidate = stem + ext
        for folder in [LOCAL_FOLDER, USB_GADGET_FOLDER if USE_USB_GADGET else None]:
            if folder and os.path.exists(os.path.join(folder, candidate)):
                source_file = os.path.join(folder, candidate)
                ok, _, _ = extract_thumbnail_for_file(
                    Path(source_file), output_to_thumbnails=True)
                if ok and os.path.exists(thumb_path):
                    file_db.add_file(candidate, f"{stem}_small.png", f"{stem}_big.png")
                    return send_from_directory(THUMBNAILS_FOLDER, filename)

    # ── Slow path: file is on the printer ─────────────────────────────────────
    # Return 202 immediately so Flask/WebSocket threads are never blocked.
    # Schedule a background fetch if one isn't already running for this stem.
    with _thumb_in_progress_lock:
        already_queued = stem in _thumb_in_progress
        if not already_queued:
            _thumb_in_progress.add(stem)

    if not already_queued:
        _thumb_pool.submit(_fetch_and_cache_thumbnail, stem, thumb_path)

    # 202 tells the browser "not ready yet, come back later"
    return Response('Fetching thumbnail, please retry', status=202)


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
            # Look the printer up explicitly. printers[...] raised a bare
            # KeyError here, and because the enclosing try has only a finally,
            # it escaped to Flask and produced its stock HTML 500 page - which
            # the browser then showed verbatim in an alert box. The upload
            # itself had already finished, so it always failed at ~99%: Flask
            # parses the whole request body before this line ever runs.
            printer = printers.get(form_data['printer'])
            if printer is None:
                logger.error(f"Upload for unknown printer id '{form_data['printer']}'. "
                             f"Known ids: {list(printers.keys())}")
                return Response(json.dumps({
                    "upload": "error",
                    "msg": ("ChitUI is not connected to that printer. Reload the page, "
                            "and re-add the printer in Settings if it is missing."),
                }), status=409, mimetype="application/json")
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
            usb_device_type = printer.get('usb_device_type', 'physical')

            # Route to correct folder:
            # - destination=usb + virtual USB gadget active → USB_GADGET_FOLDER (/mnt/usb_share)
            # - destination=usb + physical USB printer      → LOCAL_FOLDER (then upload via network)
            # - destination=local                           → LOCAL_FOLDER always
            if destination == 'usb' and USE_USB_GADGET:
                upload_folder = USB_GADGET_FOLDER
                if not os.path.exists(upload_folder):
                    logger.error(f"USB gadget folder not found: {upload_folder}")
                    return Response('{"upload": "error", "msg": "USB mount point not found. Please mount USB gadget first."}', status=500, mimetype="application/json")
                if not os.access(upload_folder, os.W_OK):
                    logger.error(f"USB gadget folder not writable: {upload_folder}")
                    return Response('{"upload": "error", "msg": "USB mount point not writable. Check permissions."}', status=500, mimetype="application/json")
            else:
                upload_folder = LOCAL_FOLDER

            filepath = os.path.join(upload_folder, filename)
            logger.info(f"Saving '{filename}' to {filepath} (upload_id: {upload_id})")
            logger.info(f"USB device type: {usb_device_type}, Upload folder: {upload_folder}")
            try:
                # Initialize progress
                with uploadProgressLock:
                    uploadProgress[upload_id] = 0

                # Save to temp first so we can extract thumbnails before writing to final dest.
                # For USB gadget uploads: temp is on the same SD card as data/, so the
                # subsequent move to /mnt/usb_share (FAT32) would be a slow cross-fs copy.
                # Instead we save directly to the final path and extract from there.
                if upload_folder == USB_GADGET_FOLDER:
                    # Save directly to USB mount - skip temp to avoid slow FAT32 cross-copy
                    file.save(filepath)
                    logger.info(f"✓ File '{filename}' saved directly to USB gadget: {filepath}")
                    temp_filepath = filepath  # reuse variable for thumbnail extraction below
                else:
                    # Save to temp first, extract thumbnails, then move to final destination
                    temp_filepath = os.path.join(TEMP_FOLDER, filename)
                    file.save(temp_filepath)
                    logger.info(f"✓ File '{filename}' saved to temp folder")

                # Extract thumbnails (for .goo/.ctb files)
                thumb_success, thumb_small, thumb_big = extract_thumbnail_for_file(Path(temp_filepath), output_to_thumbnails=True)
                if thumb_success:
                    logger.info(f"✓ Thumbnails extracted: {thumb_small}, {thumb_big}")
                else:
                    logger.info("⚠ No thumbnails extracted (file type doesn't support it or extraction failed)")

                # Move to final destination only if we used temp
                if temp_filepath != filepath:
                    shutil.move(temp_filepath, filepath)
                    logger.info(f"✓ File moved to final destination: {filepath}")
                else:
                    logger.info(f"✓ File already at final destination: {filepath}")

                # Update database association
                if thumb_success:
                    file_db.add_file(filename, thumb_small, thumb_big)
                    logger.info(f"✓ Database updated with file-thumbnail association")

                if destination == 'usb' and USE_USB_GADGET:
                    logger.info("Destination: USB Gadget (Pi's virtual USB)")

                    # Set progress to 100% immediately - file is already on disk
                    with uploadProgressLock:
                        uploadProgress[upload_id] = 100
                    socketio.emit('upload_progress', {'upload_id': upload_id, 'progress': 100}, namespace='/')
                    logger.info("✓ Upload to USB gadget complete!")

                    # Run gadget reload in background so we can return the response immediately.
                    # The client will receive the success response, then get a file-list refresh
                    # event once the gadget has finished reloading (~8 seconds later).
                    def _reload_and_notify():
                        try:
                            reload_usb_gadget()
                            # Wait for printer to re-enumerate the USB drive after reconnect
                            time.sleep(5)
                            socketio.emit('refresh_file_list', {
                                'reason': 'usb_upload_complete',
                                'filename': filename
                            })
                            logger.info(f"[upload] USB reload complete, file list refresh emitted")
                        except Exception as e:
                            logger.error(f"[upload] Background USB reload failed: {e}")

                    Thread(target=_reload_and_notify, daemon=True).start()

                    msg = "File saved to USB gadget. File list will refresh automatically in a few seconds."

                    return Response(
                        json.dumps({
                            "upload": "success",
                            "msg": msg,
                            "upload_id": upload_id,
                            "usb_gadget": True,
                            "filename": filename,
                            "refresh_triggered": True
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

            except OSError as e:
                import errno as _errno
                if e.errno == _errno.ENOSPC:
                    logger.error("Upload failed: disk full - clearing temp folder")
                    _clean_temp_folder()
                    return Response(
                        f'{{"upload": "error", "msg": "Disk full on the Raspberry Pi. Temp files have been cleared - please try uploading again.", "upload_id": "{upload_id}"}}',
                        status=507, mimetype="application/json")
                logger.error(f"Upload failed: {e}")
                return Response(f'{{"upload": "error", "msg": "Upload failed: {str(e)}", "upload_id": "{upload_id}"}}', status=500, mimetype="application/json")
            except Exception as e:
                logger.error(f"Upload failed: {e}")
                return Response(f'{{"upload": "error", "msg": "Upload failed: {str(e)}", "upload_id": "{upload_id}"}}', status=500, mimetype="application/json")
        except Exception as e:
            # Anything raised before the inner try - a bad form field, a
            # missing folder, a filename secure_filename() reduces to nothing -
            # used to escape to Flask and come back as its stock HTML error
            # page. The uploader shows the raw response body, so the user got a
            # wall of HTML in an alert instead of a usable message.
            logger.error(f"Unhandled error during upload: {e}")
            logger.error(traceback.format_exc())
            return Response(json.dumps({
                "upload": "error",
                "msg": f"Upload failed: {e}",
            }), status=500, mimetype="application/json")
        finally:
            # Always release the lock
            uploadLock.release()
    else:
        return Response("u r doin it rong", status=405, mimetype='text/plain')


# Download mode:
#   'auto'   - redirect LAN clients straight to the printer, proxy everyone else
#   'proxy'  - always stream through the Pi (needed for Tailscale/remote access)
#   'direct' - always redirect to the printer
# Proxying costs real throughput: the Pi has to pull the file off the printer's
# WiFi and push it out again through the Werkzeug dev server, roughly halving
# the achievable rate. A LAN browser can talk to the printer itself.
DOWNLOAD_MODE = os.environ.get('DOWNLOAD_MODE', 'auto').lower()

# Remembers recent download requests so a double-firing UI shows up in the log
# instead of silently costing bandwidth.
_recent_downloads = {}
_recent_downloads_lock = threading.Lock()


def _note_download_request(key: str) -> float:
    """Record a download request. Returns seconds since the last identical one."""
    now = time.time()
    with _recent_downloads_lock:
        previous = _recent_downloads.get(key)
        _recent_downloads[key] = now
        # Keep the dict from growing forever
        if len(_recent_downloads) > 64:
            cutoff = now - 300
            for k in [k for k, t in _recent_downloads.items() if t < cutoff]:
                _recent_downloads.pop(k, None)
    return (now - previous) if previous else float('inf')


def _client_can_reach_printer(client_ip: str, printer_ip: str) -> bool:
    """True if the browser looks like it's on the same LAN segment as the printer.

    Deliberately conservative: only a /24 match on a private range counts.
    Tailscale clients (100.64.0.0/10) and anything else fall through to the
    proxy, which always works.
    """
    try:
        import ipaddress
        c = ipaddress.ip_address(client_ip)
        p = ipaddress.ip_address(printer_ip)
        if not (c.is_private and p.is_private):
            return False
        return c.packed[:3] == p.packed[:3]
    except Exception:
        return False


def _printer_http_candidates(printer_ip, safe_filename, origin=None):
    """Build the list of Mongoose URLs on the printer that might hold a file.

    The printer's SDCP file list reports paths like '/local/model.goo' or
    '/usb/model.goo', but its Mongoose HTTP file server exposes the real
    mount points ('/media/mmcblk0p3/', '/mnt/udisk/', ...). Same mapping used
    by the thumbnail prefetcher.

    Args:
        printer_ip: IP of the printer.
        safe_filename: Bare filename, already sanitised.
        origin: '/local/', '/usb/' or None. When known, that storage is tried
                first; the others are still tried as a fallback because the
                browser may have stripped the prefix.
    """
    local_bases = [f'http://{printer_ip}:3030/media/mmcblk0p3/']
    usb_bases = [
        f'http://{printer_ip}:3030/mnt/udisk/',
        f'http://{printer_ip}:3030/mnt/usb/',
        f'http://{printer_ip}:3030/mnt/',
    ]

    if origin == '/usb/':
        bases = usb_bases + local_bases
    elif origin == '/local/':
        bases = local_bases + usb_bases
    else:
        bases = local_bases + usb_bases

    return [base + safe_filename for base in bases]


@app.route('/download/<printer_id>/<filename>')
def download_file(printer_id, filename):
    """
    Download a file from the printer's storage.

    Two sources are checked, in order:
      1. The Pi itself - USB gadget share or the local uploads folder. This is
         where files live that were uploaded through ChitUI.
      2. The printer's own storage, proxied over its Mongoose HTTP server.
         Files sliced elsewhere and sent straight to the printer (or copied on
         with a USB stick) only exist there, never on the Pi - this is what
         used to 404.
    """
    from flask import send_from_directory

    try:
        # Remove path prefixes like /local/ or /usb/ if present. Remember which
        # storage the file claimed to be on so the proxy can try it first.
        clean_filename = filename
        origin = None
        if filename.startswith('/local/'):
            clean_filename = filename[7:]  # Remove '/local/'
            origin = '/local/'
        elif filename.startswith('/usb/'):
            clean_filename = filename[5:]  # Remove '/usb/'
            origin = '/usb/'

        # Secure the filename to prevent directory traversal
        safe_filename = secure_filename(clean_filename)
        if not safe_filename:
            return Response('{"error": "Invalid filename"}', status=400, mimetype="application/json")

        # Make double-fires visible. One click should produce one line here.
        gap = _note_download_request(f"{request.remote_addr}:{printer_id}:{safe_filename}")
        if gap < 5:
            logger.warning(
                f"DUPLICATE download request for {safe_filename} from {request.remote_addr} "
                f"- {gap:.2f}s after the previous one. The browser sent this twice; "
                f"if you clicked once, the page is running a stale chitui.js.")
        else:
            logger.info(f"Download request: {safe_filename} from {request.remote_addr}")

        # ── Source 1: the Pi ──────────────────────────────────────────────────
        usb_path   = os.path.join(USB_GADGET_FOLDER, safe_filename) if USE_USB_GADGET else None
        local_path = os.path.join(LOCAL_FOLDER, safe_filename)

        if usb_path and os.path.exists(usb_path):
            logger.info(f"Downloading file: {safe_filename} from {USB_GADGET_FOLDER}")
            return send_from_directory(USB_GADGET_FOLDER, safe_filename, as_attachment=True)

        if os.path.exists(local_path):
            logger.info(f"Downloading file: {safe_filename} from {LOCAL_FOLDER}")
            return send_from_directory(LOCAL_FOLDER, safe_filename, as_attachment=True)

        # ── Source 2: proxy from the printer ──────────────────────────────────
        printer = printers.get(printer_id)
        printer_ip = printer.get('ip') if printer else None

        if not printer_ip:
            logger.warning(f"Download: file not on Pi and printer {printer_id} has no known IP")
            return Response('{"error": "File not found"}', status=404, mimetype="application/json")

        # ── Fast path: let a LAN browser fetch straight from the printer ──────
        # Proxying means the Pi pulls the file over WiFi and re-serves it through
        # the single-threaded dev server. Cutting the Pi out of the data path is
        # worth several times the throughput. Remote clients still get the proxy.
        client_ip = request.remote_addr or ''
        want_direct = (
            DOWNLOAD_MODE == 'direct' or
            (DOWNLOAD_MODE == 'auto' and _client_can_reach_printer(client_ip, printer_ip))
        )
        if want_direct:
            for url in _printer_http_candidates(printer_ip, safe_filename, origin):
                try:
                    head = requests.head(url, timeout=5)
                    if head.status_code == 200:
                        logger.info(f"Download: redirecting {client_ip} straight to {url}")
                        return redirect(url, code=302)
                except requests.RequestException:
                    continue
            logger.info("Download: no direct URL found, falling back to proxy")

        for url in _printer_http_candidates(printer_ip, safe_filename, origin):
            try:
                head = requests.head(url, timeout=5)
                if head.status_code != 200:
                    continue

                logger.info(f"Downloading file: {safe_filename} proxied from {url}")
                upstream = requests.get(url, timeout=(5, 300), stream=True)
                if upstream.status_code != 200:
                    upstream.close()
                    continue

                def generate(resp=upstream):
                    try:
                        # 256 KB chunks: fewer syscalls and less GIL churn than
                        # 64 KB, which matters on a Pi pushing a 50 MB model.
                        for chunk in resp.iter_content(chunk_size=262144):
                            if chunk:
                                yield chunk
                    finally:
                        resp.close()

                headers = {
                    'Content-Disposition': f'attachment; filename="{safe_filename}"',
                    # Chrome weighs Content-Length when deciding whether a download
                    # looks legitimate, and without it the progress bar is unknown
                    # for the whole transfer. Mongoose sends it, so pass it through.
                    'X-Content-Type-Options': 'nosniff',
                    'Cache-Control': 'no-store',
                }
                content_length = upstream.headers.get('Content-Length')
                if content_length:
                    headers['Content-Length'] = content_length

                return Response(
                    stream_with_context(generate()),
                    status=200,
                    headers=headers,
                    mimetype='application/octet-stream',
                )
            except requests.RequestException as e:
                logger.debug(f"Download probe failed for {url}: {e}")
                continue

        logger.warning(f"Download requested for non-existent file: {safe_filename} "
                       f"(not on Pi, not on printer {printer_ip})")
        return Response('{"error": "File not found"}', status=404, mimetype="application/json")

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
            # Run with sudo if not already root; requires NOPASSWD sudoers entry
            # (see install instructions) or running ChitUI as root
            cmd = ['bash', script_path] if os.geteuid() == 0 else ['sudo', 'bash', script_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

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

def discover_printers(attempts: int = 3, per_attempt_timeout: float = 1.5):
    """Broadcast M99999 and collect the replies.

    Three things were wrong with the original single-shot version:
      * one broadcast with a 1 second window was easy to miss - a mainboard
        that is mid-exposure or mid-lift simply doesn't answer in time, and
        UDP broadcast frames get dropped on Wi-Fi. We now retry.
      * the socket was only closed on TimeoutError. A malformed reply raised
        out of save_discovered_printer(), leaking the socket and leaving port
        54781 bound for the life of the process, so every later discovery
        failed with EADDRINUSE. Now everything is wrapped in try/finally.
      * no SO_REUSEADDR, so a restart could collide with the old binding.
    """
    logger.info("Starting printer discovery.")
    msg = b'M99999'
    discovered_printers = {}
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                             socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(per_attempt_timeout)
        sock.bind(('', 54781))

        for attempt in range(max(1, attempts)):
            try:
                sock.sendto(msg, ("255.255.255.255", 3000))
            except OSError as e:
                # Network isn't up yet - common when ChitUI autostarts on boot.
                logger.warning(f"Discovery broadcast failed ({e}), retrying...")
                time.sleep(1)
                continue

            while True:
                try:
                    data = sock.recv(8192)
                except (TimeoutError, socket.timeout):
                    break
                try:
                    save_discovered_printer(data, discovered_printers)
                except Exception as e:
                    logger.warning(f"Ignoring bad discovery reply: {e}")

            if discovered_printers:
                break
            if attempt + 1 < attempts:
                logger.info(f"No replies yet, discovery attempt {attempt + 2}/{attempts}...")
    except OSError as e:
        logger.error(f"Discovery socket error: {e}")
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    logger.info(f"Discovery done ({len(discovered_printers)} found).")
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
    mainboard_id = j['Data']['MainboardID']
    existing = printers.get(mainboard_id)
    if existing:
        # Already known (e.g. a second discovery pass, or a saved printer we are
        # already connected to). Refresh the discovered fields but keep the live
        # connection state and any config-side keys - blindly replacing the entry
        # reset 'online' to False on a healthy printer, which showed up as a
        # bogus offline -> online flap a few seconds later.
        existing.update({k: v for k, v in printer.items() if k != 'online'})
        printer = existing
    else:
        printer['online'] = False  # Initially offline until connected
    printer_dict[mainboard_id] = printer
    printers[mainboard_id] = printer
    logger.info("Discovered: {n} ({i})".format(
        n=printer['name'], i=printer['ip']))
    return printer_dict


def _ws_thread_alive(printer_id) -> bool:
    """True if a run_forever() reader thread is still running for this printer."""
    t = _ws_threads.get(printer_id)
    return bool(t and t.is_alive())


def connect_printers(printers_to_connect, force=False):
    """Open (or re-open) one websocket per printer.

    Idempotent on purpose. /discover used to call this unconditionally, which
    spawned a *second* WebSocketApp per printer and orphaned the first one -
    the old thread kept running and kept its socket open. A Chitu mainboard
    only accepts a handful of concurrent connections on port 3030, so those
    orphans eventually filled every slot and new connections were refused,
    which is what made a printer that is plainly powered on and printing sit
    at "offline" in the UI for minutes at a time.
    """
    for id, printer in printers_to_connect.items():
        if not force and _ws_thread_alive(id):
            logger.debug("Already connected/connecting to {n}, skipping".format(
                n=printer.get('name', id)))
            continue

        old = websockets.pop(id, None)
        if old is not None:
            logger.info("Replacing stale connection for {n}".format(
                n=printer.get('name', id)))
            try:
                old.close()
            except Exception:
                pass

        url = "ws://{ip}:3030/websocket".format(ip=printer['ip'])
        logger.info("Connecting to: {n}".format(n=printer['name']))
        # 1 second was far too tight for a Chitu mainboard that is busy with an
        # exposure/lift cycle or serving files from its Mongoose server.
        websocket.setdefaulttimeout(5)
        ws = websocket.WebSocketApp(url,on_message=ws_msg_handler,
                                    on_open=lambda _, printer_id=id: ws_connected_handler(printer_id),
                                    on_close=lambda _, s, m, printer_id=id: ws_disconnected_handler(printer_id, s, m),
                                    on_error=lambda _, e, printer_id=id: ws_error_handler(printer_id, e)
                                    )
        websockets[id] = ws

        # TCP keepalive. Without it, a peer that vanished without a FIN (Pi
        # reboot, printer power cycle, Wi-Fi drop) leaves a half-open socket
        # that the kernel only reaps after ~2 hours. With these settings we
        # notice within ~25 seconds and reconnect.
        sockopt = (
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        )
        if hasattr(socket, 'TCP_KEEPIDLE'):
            sockopt += (
                (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10),
                (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5),
                (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3),
            )

        # Protocol-level ping/pong is off by default now (see WS_PING_INTERVAL
        # above): a mainboard mid-exposure can miss any pong deadline, and the
        # resulting teardown/reconnect is exactly the flap we were chasing.
        # Liveness comes from the sdcp/status stream instead.
        run_kwargs = {'reconnect': WS_RECONNECT_DELAY, 'sockopt': sockopt}
        if WS_PING_INTERVAL:
            run_kwargs['ping_interval'] = WS_PING_INTERVAL
            run_kwargs['ping_timeout'] = WS_PING_TIMEOUT

        t = Thread(target=lambda w=ws, k=run_kwargs: w.run_forever(**k), daemon=True)
        _ws_threads[id] = t
        t.start()

    return True


def ws_connected_handler(printer_id):
    if printer_id in printers:
        _printer_last_seen[printer_id] = time.time()
        logger.info("Connected to: {n}".format(n=printers[printer_id]['name']))
        # Only announce a change if the printer was actually considered offline.
        # A reconnect after a short blip is a non-event for the UI.
        if not printers[printer_id].get('online', False):
            printers[printer_id]['online'] = True
            socketio.emit('printers', printers)


def ws_disconnected_handler(printer_id, status_code, message):
    if printer_id in printers:
        logger.info("Connection to '{n}' closed: {m} ({s})".format(
            n=printers[printer_id]['name'], m=message, s=status_code))
        # Deliberately do NOT flip 'online' here. websocket-client reconnects
        # within a second or two, and marking the printer offline on every
        # transient close is what made the status flap. check_printer_connections()
        # owns the flag and only declares a printer offline after
        # PRINTER_OFFLINE_GRACE seconds without a live socket.


def ws_error_handler(printer_id, error):
    if printer_id in printers:
        logger.info("Connection to '{n}' error: {e}".format(
            n=printers[printer_id]['name'], e=error))


def check_printer_connections():
    """Background task to verify printer connections are actually alive.

    This is the single owner of the 'online' flag. It applies hysteresis: a
    printer is marked online as soon as a live socket is seen, but is only
    marked offline after PRINTER_OFFLINE_GRACE seconds with no live socket.

    The grace period matters because ws.run_forever(reconnect=...) sets
    ws.sock to None while it is reconnecting. Without it, every reconnect
    attempt - including successful ones - produced a spurious
    offline -> online pair, which the frontend turned into page reloads.
    """
    rediscover_after = 0

    while True:
        time.sleep(2)  # was 5 - halves the worst-case "still offline" lag
        changed = False
        now = time.time()

        for printer_id in list(printers.keys()):
            printer = printers.get(printer_id)
            if not printer:
                continue
            # UART printers have no websocket; their own layer owns the flag.
            if printer.get('connection_type') == 'uart':
                continue

            ws = websockets.get(printer_id)
            is_connected = bool(ws and ws.sock is not None and ws.sock.connected)
            current_online = printer.get('online', False)

            if is_connected:
                _printer_last_seen[printer_id] = now
                if not current_online:
                    printer['online'] = True
                    changed = True
                    logger.info(f"Printer '{printer['name']}' connection restored")
                continue

            # Not connected. If the reader thread died there is nobody left to
            # reconnect: websocket-client only retries from inside run_forever,
            # so once that returns the printer was stuck offline until ChitUI
            # was restarted. Respawn it.
            if printer.get('ip') and not _ws_thread_alive(printer_id):
                logger.warning(
                    f"Reader thread for '{printer['name']}' is gone - reconnecting")
                connect_printers({printer_id: printer}, force=True)

            if current_online:
                last_seen = _printer_last_seen.get(printer_id)
                if last_seen is None:
                    _printer_last_seen[printer_id] = now
                elif now - last_seen > PRINTER_OFFLINE_GRACE:
                    printer['online'] = False
                    changed = True
                    logger.info(
                        f"Printer '{printer['name']}' connection lost "
                        f"(no traffic for {int(now - last_seen)}s)")
                else:
                    logger.debug(
                        f"Printer '{printer['name']}' quiet for "
                        f"{int(now - last_seen)}s - within grace period, not flagging offline")

        # If we know about no printers at all, the boot-time discovery pass
        # missed them (network not up yet, mainboard busy, broadcast dropped).
        # Retry quietly instead of waiting for someone to click Discover.
        #
        # Only when the user actually asked for auto-discovery. This block used
        # to run unconditionally, so a fresh install with auto_discover off
        # still broadcast M99999 two seconds after boot and populated the
        # dashboard with a printer nobody had added.
        # Settings are re-read every pass so toggling the checkbox takes effect
        # without a restart.
        if not printers and now > rediscover_after:
            # Advance the clock before the settings check, not after, or a
            # disabled auto-discover would re-read the settings file on every
            # single 2 second pass.
            rediscover_after = now + 30
            if load_settings().get("auto_discover", False):
                try:
                    found = discover_printers(attempts=1)
                    if found:
                        connect_printers(found)
                        changed = True
                except Exception as e:
                    logger.debug(f"Background rediscovery failed: {e}")

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
            # Any message at all is proof the printer is alive. This is the
            # liveness signal that replaced websocket ping/pong: the printer
            # pushes sdcp/status roughly once a second on its own, so it is
            # both cheaper and far more reliable than asking it for a pong
            # while it is busy exposing a layer.
            _printer_last_seen[printer_id] = time.time()
            if printer_id in printers and not printers[printer_id].get('online', False):
                printers[printer_id]['online'] = True
                logger.info("Printer '{n}' is talking to us - marking online".format(
                    n=printers[printer_id].get('name', printer_id)))
                socketio.emit('printers', printers)

            plugin_manager.notify_printer_message(printer_id, data)

        if data['Topic'].startswith("sdcp/response/"):
            socketio.emit('printer_response', data)
            # If this is a file list response (Cmd 258), kick off thumbnail prefetch
            try:
                cmd = data.get('Data', {}).get('Data', {}).get('Cmd')
                file_list = data.get('Data', {}).get('Data', {}).get('FileList', [])
                if cmd == 258 and file_list and printer_id and printer_id in printers:
                    printer_ip = printers[printer_id].get('ip')
                    if printer_ip:
                        # Only queue files, not directories (type==1 are files)
                        files_only = [f.get('name', f) if isinstance(f, dict) else f
                                      for f in file_list
                                      if isinstance(f, str) or (isinstance(f, dict) and f.get('type', 1) == 1)]
                        if files_only:
                            Thread(target=fetch_printer_thumbnails_for_filelist,
                                   args=(printer_ip, files_only), daemon=True).start()
            except Exception:
                pass
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
    
    # main() already runs a discovery pass when auto-discovery is enabled;
    # only repeat it here if nothing is known yet (e.g. called at runtime).
    if settings.get("auto_discover", False) and not printers:
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

            # Was: `if printer_id not in websockets`. That entry survives a dead
            # reader thread, so a printer that dropped once could never be
            # reconnected from here. connect_printers() is idempotent now, so
            # just call it and let it decide.
            if not _ws_thread_alive(printer_id):
                connect_printers({printer_id: printers[printer_id]})


# ============ MAIN ============

def main():
    # Initialize authentication
    logger.info("Initializing authentication...")
    init_auth()

    # Mount USB gadget if enabled
    global USE_USB_GADGET, UPLOAD_FOLDER, LOCAL_FOLDER
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

    # ---- Register route modules that live outside main.py -------------
    # These were never wired up, so every endpoint they define returned 404:
    # /themes/active.css (the <link> in index.html line 19) and the whole
    # /uart/* + /identity API. Both show up in the browser console as failed
    # requests - the stylesheet one as "Verify stylesheet URLs".
    try:
        from themes import init_themes
        init_themes(app,
                    login_required=login_required,
                    load_settings=load_settings,
                    save_settings=save_settings,
                    data_folder=DATA_FOLDER,
                    project_root=PROJECT_ROOT,
                    logger=logger)
        logger.info("✓ Theme routes registered (/themes/*)")
    except Exception as e:
        logger.error(f"✗ Could not register theme routes: {e}")

    try:
        from raspicam import init_raspicam
        init_raspicam(app,
                      login_required=login_required,
                      load_settings=load_settings,
                      save_settings=save_settings,
                      logger=logger)
        logger.info("✓ Pi Camera routes registered (/raspicam/*)")
    except Exception as e:
        logger.error(f"✗ Could not register Pi Camera routes: {e}")

    try:
        from uart import register_uart_routes, UART_SUPPORT
        register_uart_routes(app, login_required, printers, uart_connections,
                             socketio, load_settings, save_settings,
                             USB_GADGET_FOLDER, UPLOAD_FOLDER,
                             THUMBNAILS_FOLDER, USE_USB_GADGET,
                             extract_thumbnail_for_file, file_db, Path,
                             plugin_manager)
        logger.info(f"✓ UART routes registered (/uart/*) - pyserial available: {UART_SUPPORT}")
    except Exception as e:
        logger.error(f"✗ Could not register UART routes: {e}")

    # Discovery + connection run in the background. They used to block the rest
    # of main(), and main() runs at import time - so the web server did not
    # start listening until discovery had finished, and discovery itself sat
    # behind the USB gadget mount and the whole plugin load. Doing it in a
    # thread means the UI is up immediately and the printer connects in
    # parallel rather than after everything else.
    def _startup_printers():
        # Default False, matching load_settings() and load_saved_printers().
        # It used to default to True here, so any settings file without the key
        # (older installs, hand-edited files, a failed migration) discovered on
        # every boot even though the UI showed the checkbox unticked.
        if settings.get("auto_discover", False):
            logger.info("Starting with auto-discovery enabled")
            try:
                discovered = discover_printers()
            except Exception as e:
                logger.error(f"Discovery failed: {e}")
                discovered = {}
            if discovered:
                connect_printers(discovered)
                socketio.emit('printers', printers)
            else:
                logger.warning("No printers discovered.")

        try:
            load_saved_printers()
        except Exception as e:
            logger.error(f"Could not load saved printers: {e}")

    Thread(target=_startup_printers, daemon=True).start()

    # Start background connection health checker
    logger.info("Starting printer connection health monitor...")
    Thread(target=check_printer_connections, daemon=True).start()

    # Scan for files copied outside ChitUI that are missing thumbnails
    # Pass USB_GADGET_FOLDER explicitly so the thread sees the post-mount state
    logger.info("Starting thumbnail scan for externally-copied files...")
    _usb_folder = USB_GADGET_FOLDER if USE_USB_GADGET else None
    Thread(target=scan_and_extract_missing_thumbnails,
           kwargs={'extra_folders': [_usb_folder] if _usb_folder else []},
           daemon=True).start()

    # Start live file watcher
    if WATCHDOG_AVAILABLE:
        _observer = Observer()
        _watcher = PrintFileWatcher()
        watched = set()
        if USE_USB_GADGET and os.path.exists(USB_GADGET_FOLDER):
            _observer.schedule(_watcher, USB_GADGET_FOLDER, recursive=False)
            watched.add(USB_GADGET_FOLDER)
        if os.path.exists(LOCAL_FOLDER) and LOCAL_FOLDER not in watched:
            _observer.schedule(_watcher, LOCAL_FOLDER, recursive=False)
        _observer.daemon = True
        _observer.start()
        logger.info("File watcher started")
    else:
        logger.info("File watcher not available (pip install watchdog to enable)")


# Initialize the application (runs on both direct execution and Gunicorn import)
main()

if __name__ == "__main__":

    logger.info("=" * 60)
    logger.info(f"ChitUI {updater.get_current_version()} Starting")
    logger.info("=" * 60)

    # Reclaim space from any update or plugin install that was interrupted
    # (power cut, kill -9) before its own cleanup could run.
    updater.cleanup_temp_files("startup")
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