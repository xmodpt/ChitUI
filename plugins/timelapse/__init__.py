"""
Timelapse Plugin for ChitUI

Records a timelapse of your 3D print using the printer's built-in RTSP camera.
Captures one frame per layer change from the shared camera buffer and assembles
them into an MP4 video using ffmpeg (or OpenCV as fallback).

Camera sharing: reads from the main app's shared camera_latest_frame buffer so
the live camera view remains available simultaneously.
"""

import os
import sys
import json
import time
import shutil
import threading
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from plugins.base import ChitUIPlugin

# Candidate directories for timelapse storage (tried in order)
TIMELAPSE_DIR_CANDIDATES = [
    '/timelapse',
    os.path.join(os.path.expanduser('~'), 'timelapse'),
]
FRAMES_TMP_BASE = '/tmp'

DEFAULT_CONFIG = {
    'fps': 30,
    'preset': 'fast',
    'storage_dir': '',       # empty = auto-detect from candidates
    'camera_source': 'system',  # 'system' | 'usb' | 'ip_camera'
    'camera_usb_index': 0,   # USB/v4l2 device index (source='usb')
    'camera_ip_id': '',      # ip_camera plugin camera ID (source='ip_camera')
}


def _detect_usb_cameras():
    """Probe OpenCV device indices 0-9 and return available USB cameras."""
    cameras = []
    try:
        import cv2
        for i in range(10):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                cameras.append({'index': i, 'name': f'Camera {i} (USB/v4l2)'})
                cap.release()
    except Exception:
        pass
    return cameras

# SDCP print status codes
PRINT_STATUS_IDLE = 0
PRINT_STATUS_PRINTING = 1
PRINT_STATUS_PAUSED = 2
PRINT_STATUS_COMPLETE = 3


class Plugin(ChitUIPlugin):

    def __init__(self, plugin_dir):
        super().__init__(plugin_dir)

        # Per-printer state keyed by printer_id
        self._lock = threading.Lock()
        self.timelapse_dir = None        # Set in on_startup()
        self.recording = {}              # printer_id -> bool
        self.frames_dir = {}             # printer_id -> str (tmp dir path)
        self.frame_count = {}            # printer_id -> int
        self.last_layer = {}             # printer_id -> int
        self.current_job = {}            # printer_id -> str (filename base)
        self.camera_started_by_us = {}  # printer_id -> bool
        self.pending_timelapse = {}      # printer_id -> bool (armed for next print)

        self.socketio = None

        # Plugin-owned capture state (USB or ip_camera sources)
        self._custom_frame = {}          # printer_id -> bytes | None
        self._custom_frame_lock = {}     # printer_id -> threading.Lock
        self._custom_camera_running = {} # printer_id -> bool
        self._custom_camera_thread = {}  # printer_id -> Thread

        self.config_file = os.path.expanduser('~/.chitui/timelapse_config.json')
        self.config = dict(DEFAULT_CONFIG)
        self._load_config()

    # ── Config persistence ────────────────────────────────────────────────────

    def _load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    saved = json.load(f)
                self.config.update(saved)
        except Exception as e:
            print(f"[Timelapse] Could not load config: {e}")

    def _save_config(self):
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"[Timelapse] Could not save config: {e}")

    # ── Plugin metadata ──────────────────────────────────────────────────────

    def get_name(self):
        return "Timelapse"

    def get_version(self):
        return "1.0.0"

    def get_description(self):
        return "Record timelapses of your 3D prints — one frame per layer, assembled into MP4"

    def get_author(self):
        return "ChitUI"

    def get_dependencies(self):
        return ['opencv-python-headless']

    def get_ui_integration(self):
        return {
            'type': 'card',
            'location': 'sidebar',
            'icon': 'bi-camera-reels',
            'title': 'Timelapse',
            'template': 'timelapse.html'
        }

    # ── Startup / routes ─────────────────────────────────────────────────────

    def on_startup(self, app, socketio):
        self.socketio = socketio

        # Find/create timelapse storage directory
        # If a custom path is configured, try that first before the defaults
        custom_dir = self.config.get('storage_dir', '').strip()
        candidates = ([custom_dir] if custom_dir else []) + list(TIMELAPSE_DIR_CANDIDATES)
        for candidate in candidates:
            try:
                os.makedirs(candidate, exist_ok=True)
                self.timelapse_dir = candidate
                print(f"[Timelapse] Storage directory: {candidate}")
                break
            except (PermissionError, OSError) as e:
                print(f"[Timelapse] Cannot use {candidate}: {e}")

        if not self.timelapse_dir:
            print("[Timelapse] WARNING: No writable storage directory found. "
                  "Timelapse saving will be disabled.")

        self.blueprint = Blueprint(
            'timelapse',
            __name__,
            template_folder=self.get_template_folder(),
        )

        @self.blueprint.route('/status')
        def get_status():
            printers_status = {}
            for pid in list(self.recording.keys()):
                printers_status[pid] = {
                    'recording': self.recording.get(pid, False),
                    'frame_count': self.frame_count.get(pid, 0),
                    'job': self.current_job.get(pid),
                }
            return jsonify({
                'recording': any(self.recording.values()),
                'printers': printers_status,
                'pending': dict(self.pending_timelapse),
                'storage_dir': self.timelapse_dir,
            })

        @self.blueprint.route('/list')
        def list_timelapses():
            files = []
            if self.timelapse_dir:
                try:
                    for f in sorted(
                        Path(self.timelapse_dir).glob('*.mp4'),
                        key=lambda x: x.stat().st_mtime,
                        reverse=True
                    ):
                        files.append({
                            'name': f.name,
                            'size_mb': round(f.stat().st_size / (1024 * 1024), 1),
                            'date': datetime.fromtimestamp(
                                f.stat().st_mtime
                            ).strftime('%Y-%m-%d %H:%M'),
                        })
                except Exception as e:
                    print(f"[Timelapse] Error listing files: {e}")
            return jsonify(files)

        @self.blueprint.route('/download/<filename>')
        def download_timelapse(filename):
            if not self.timelapse_dir:
                return jsonify({'error': 'No storage directory configured'}), 503
            filepath = os.path.join(self.timelapse_dir, filename)
            if not os.path.abspath(filepath).startswith(
                    os.path.abspath(self.timelapse_dir)):
                return jsonify({'error': 'Invalid filename'}), 400
            if not os.path.exists(filepath):
                return jsonify({'error': 'File not found'}), 404
            return send_file(filepath, as_attachment=True)

        @self.blueprint.route('/delete/<filename>', methods=['DELETE'])
        def delete_timelapse(filename):
            if not self.timelapse_dir:
                return jsonify({'error': 'No storage directory configured'}), 503
            filepath = os.path.join(self.timelapse_dir, filename)
            if not os.path.abspath(filepath).startswith(
                    os.path.abspath(self.timelapse_dir)):
                return jsonify({'error': 'Invalid filename'}), 400
            if os.path.exists(filepath):
                os.remove(filepath)
                return jsonify({'ok': True})
            return jsonify({'error': 'File not found'}), 404

        @self.blueprint.route('/detect', methods=['GET'])
        def detect_cameras():
            """Return available USB cameras and/or ip_camera plugin cameras.

            Optional query param ``type``:
              - ``usb``  – only scan USB/v4l2 devices (skips IP camera lookup)
              - ``ip``   – only list IP cameras (skips slow USB scan)
              - ``all``  – both (default, legacy behaviour)
            """
            detect_type = request.args.get('type', 'all')
            try:
                usb_cameras = _detect_usb_cameras() if detect_type in ('usb', 'all') else []
                ip_cameras = self._get_ip_cameras() if detect_type in ('ip', 'all') else []
                return jsonify({
                    'ok': True,
                    'usb_cameras': usb_cameras,
                    'ip_cameras': ip_cameras,
                })
            except Exception as e:
                print(f"[Timelapse] Camera detect error: {e}")
                return jsonify({'ok': False, 'usb_cameras': [], 'ip_cameras': []}), 500

        @self.blueprint.route('/settings', methods=['GET'])
        def get_settings():
            settings_path = os.path.join(self.get_template_folder(), 'settings.html')
            if os.path.exists(settings_path):
                with open(settings_path, 'r') as f:
                    return f.read()
            return 'Settings template not found', 404

        @self.blueprint.route('/config', methods=['GET', 'POST'])
        def timelapse_config():
            if request.method == 'GET':
                return jsonify({
                    'ok': True,
                    'fps': self.config.get('fps', DEFAULT_CONFIG['fps']),
                    'preset': self.config.get('preset', DEFAULT_CONFIG['preset']),
                    'storage_dir': self.config.get('storage_dir', DEFAULT_CONFIG['storage_dir']),
                    'active_storage_dir': self.timelapse_dir or '',
                    'camera_source': self.config.get('camera_source', DEFAULT_CONFIG['camera_source']),
                    'camera_usb_index': self.config.get('camera_usb_index', DEFAULT_CONFIG['camera_usb_index']),
                    'camera_ip_id': self.config.get('camera_ip_id', DEFAULT_CONFIG['camera_ip_id']),
                })
            data = request.get_json() or {}
            fps = data.get('fps', self.config.get('fps', DEFAULT_CONFIG['fps']))
            preset = data.get('preset', self.config.get('preset', DEFAULT_CONFIG['preset']))
            storage_dir = data.get('storage_dir', self.config.get('storage_dir', DEFAULT_CONFIG['storage_dir']))
            camera_source = data.get('camera_source', self.config.get('camera_source', DEFAULT_CONFIG['camera_source']))
            camera_usb_index = data.get('camera_usb_index', self.config.get('camera_usb_index', DEFAULT_CONFIG['camera_usb_index']))
            camera_ip_id = data.get('camera_ip_id', self.config.get('camera_ip_id', DEFAULT_CONFIG['camera_ip_id']))
            try:
                fps = int(fps)
                if fps < 1 or fps > 120:
                    return jsonify({'ok': False, 'error': 'FPS must be between 1 and 120'}), 400
            except (ValueError, TypeError):
                return jsonify({'ok': False, 'error': 'Invalid FPS value'}), 400
            if preset not in ('ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow'):
                return jsonify({'ok': False, 'error': 'Invalid preset'}), 400
            if camera_source not in ('system', 'usb', 'ip_camera'):
                return jsonify({'ok': False, 'error': 'Invalid camera source'}), 400
            try:
                camera_usb_index = int(camera_usb_index)
            except (ValueError, TypeError):
                camera_usb_index = 0
            self.config['fps'] = fps
            self.config['preset'] = preset
            self.config['storage_dir'] = storage_dir.strip()
            self.config['camera_source'] = camera_source
            self.config['camera_usb_index'] = camera_usb_index
            self.config['camera_ip_id'] = str(camera_ip_id).strip()
            self._save_config()
            return jsonify({'ok': True})

    # ── Socket handlers ───────────────────────────────────────────────────────

    def register_socket_handlers(self, socketio):

        @socketio.on('timelapse_arm')
        def handle_arm(data):
            printer_id = data.get('printer_id')
            if printer_id:
                with self._lock:
                    self.pending_timelapse[printer_id] = True
                print(f"[Timelapse] Armed for printer: {printer_id}")
            return {'ok': True}

        @socketio.on('timelapse_disarm')
        def handle_disarm(data):
            printer_id = data.get('printer_id')
            if printer_id:
                with self._lock:
                    self.pending_timelapse[printer_id] = False
            return {'ok': True}

    # ── Camera access ─────────────────────────────────────────────────────────

    def _get_main(self):
        return sys.modules.get('__main__') or sys.modules.get('main')

    def _get_camera_frame(self, printer_id=None):
        """Return the latest JPEG frame for this printer.

        If a custom camera URL is configured and running, reads from the
        plugin's own capture buffer.  Otherwise falls back to the shared
        buffer maintained by the main app.
        """
        # Custom camera path
        if printer_id and self._custom_camera_running.get(printer_id):
            lock = self._custom_frame_lock.get(printer_id)
            if lock:
                with lock:
                    return self._custom_frame.get(printer_id)

        # Main-app shared buffer path
        main = self._get_main()
        if not main:
            return None
        if not getattr(main, 'camera_stream_active', False):
            return None
        lock = getattr(main, 'camera_frame_lock', None)
        if lock is None:
            return None
        with lock:
            return getattr(main, 'camera_latest_frame', None)

    def _get_ip_cameras(self):
        """Return list of cameras configured in the ip_camera plugin, if loaded."""
        main = self._get_main()
        if not main:
            return []
        pm = getattr(main, 'plugin_manager', None)
        if not pm:
            return []
        ip_plugin = pm.get_plugin('ip_camera')
        if not ip_plugin:
            return []
        cameras = []
        for i, cfg in enumerate(getattr(ip_plugin, 'camera_configs', [])):
            cameras.append({
                'id': f'camera_{i}',
                'name': cfg.get('name', f'Camera {i}'),
                'url': cfg.get('url', ''),
            })
        return cameras

    def _get_ip_camera_url(self, camera_id):
        """Resolve a camera_id (e.g. 'camera_0') from the ip_camera plugin to its URL."""
        for cam in self._get_ip_cameras():
            if cam['id'] == camera_id:
                return cam['url']
        return ''

    def _start_capture(self, printer_id, source):
        """Start a plugin-owned capture thread.

        source: int  → USB/v4l2 device index
                str  → RTSP/HTTP URL
        """
        try:
            import cv2
        except ImportError:
            print("[Timelapse] OpenCV not available")
            return False

        self._custom_frame[printer_id] = None
        self._custom_frame_lock[printer_id] = threading.Lock()
        self._custom_camera_running[printer_id] = True

        is_url = isinstance(source, str)

        def _capture():
            try:
                if is_url:
                    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;udp'
                    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
                else:
                    cap = cv2.VideoCapture(int(source))
                if not cap.isOpened():
                    print(f"[Timelapse] Cannot open camera source: {source}")
                    self._custom_camera_running[printer_id] = False
                    return
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                while self._custom_camera_running.get(printer_id, False):
                    for _ in range(3):
                        if not self._custom_camera_running.get(printer_id, False):
                            break
                        cap.grab()
                    ret, frame = cap.retrieve()
                    if ret and frame is not None:
                        ok, buf = cv2.imencode('.jpg', frame,
                                              [cv2.IMWRITE_JPEG_QUALITY, 80])
                        if ok:
                            with self._custom_frame_lock[printer_id]:
                                self._custom_frame[printer_id] = buf.tobytes()
                    else:
                        time.sleep(0.05)
                cap.release()
            except Exception as e:
                print(f"[Timelapse] Capture error: {e}")
                self._custom_camera_running[printer_id] = False

        t = threading.Thread(target=_capture, daemon=True)
        t.start()
        self._custom_camera_thread[printer_id] = t
        time.sleep(2.0)  # Allow first frames to arrive
        return self._custom_camera_running.get(printer_id, False)

    def _ensure_camera(self, printer_id):
        """Ensure a camera is running and frames are available.

        Three modes (set in config['camera_source']):
          'system'    – printer's built-in RTSP, via main app's shared buffer
          'usb'       – USB/v4l2 device by index (plugin-owned capture thread)
          'ip_camera' – camera from the ip_camera plugin (plugin-owned thread)
        """
        source = self.config.get('camera_source', 'system')

        if source == 'usb':
            if self._custom_camera_running.get(printer_id):
                return True
            index = int(self.config.get('camera_usb_index', 0))
            print(f"[Timelapse] Starting USB camera index {index}")
            ok = self._start_capture(printer_id, index)
            if not ok:
                print(f"[Timelapse] Failed to open USB camera {index}")
            return ok

        if source == 'ip_camera':
            if self._custom_camera_running.get(printer_id):
                return True
            cam_id = self.config.get('camera_ip_id', '')
            url = self._get_ip_camera_url(cam_id)
            if not url:
                print(f"[Timelapse] IP camera '{cam_id}' not found or has no URL")
                return False
            print(f"[Timelapse] Starting IP camera '{cam_id}': {url}")
            ok = self._start_capture(printer_id, url)
            if not ok:
                print(f"[Timelapse] Failed to open IP camera {url}")
            return ok

        # --- 'system': printer built-in RTSP via main app's shared buffer ---
        main = self._get_main()
        if not main:
            return False

        if getattr(main, 'camera_stream_active', False):
            self.camera_started_by_us[printer_id] = False
            return True

        if not getattr(main, 'CAMERA_SUPPORT', False):
            print("[Timelapse] Camera support not available (opencv not installed?)")
            return False

        RTSPCamera = getattr(main, 'RTSPCamera', None)
        camera_capture_frames = getattr(main, 'camera_capture_frames', None)
        if not RTSPCamera or not camera_capture_frames:
            return False

        printers = getattr(main, 'printers', {})
        printer = printers.get(printer_id) or (
            next(iter(printers.values()), None) if printers else None)
        if not printer:
            return False

        printer_ip = printer.get('ip')
        if not printer_ip:
            return False

        try:
            camera = RTSPCamera(printer_ip)
            if camera.start():
                main.camera_instance = camera
                main.camera_stream_active = True
                main.camera_latest_frame = None
                from threading import Thread
                t = Thread(target=camera_capture_frames, daemon=True)
                t.start()
                main.camera_capture_thread = t
                time.sleep(1.5)
                self.camera_started_by_us[printer_id] = True
                print(f"[Timelapse] Camera started by timelapse plugin for {printer_ip}")
                return True
            return False
        except Exception as e:
            print(f"[Timelapse] Failed to start camera: {e}")
            return False

    def _stop_camera_if_ours(self, printer_id):
        """Stop whatever camera this plugin started for the given printer."""
        # Stop custom camera thread if running
        if self._custom_camera_running.get(printer_id):
            self._custom_camera_running[printer_id] = False
            t = self._custom_camera_thread.pop(printer_id, None)
            if t and t.is_alive():
                t.join(timeout=2.0)
            self._custom_frame.pop(printer_id, None)
            self._custom_frame_lock.pop(printer_id, None)
            return

        # Stop shared main-app camera if we were the ones who started it
        if not self.camera_started_by_us.get(printer_id):
            return
        main = self._get_main()
        if not main:
            return
        try:
            main.camera_stream_active = False
            cam = getattr(main, 'camera_instance', None)
            if cam:
                cam.running = False
            t = getattr(main, 'camera_capture_thread', None)
            if t and t.is_alive():
                t.join(timeout=2.0)
            if cam:
                cam.stop()
            main.camera_instance = None
        except Exception as e:
            print(f"[Timelapse] Error stopping camera: {e}")
        self.camera_started_by_us[printer_id] = False

    # ── Recording lifecycle ───────────────────────────────────────────────────

    def _start_recording(self, printer_id, filename_base):
        frames_dir = os.path.join(FRAMES_TMP_BASE, f'timelapse_{printer_id}_{int(time.time())}')
        os.makedirs(frames_dir, exist_ok=True)

        with self._lock:
            self.recording[printer_id] = True
            self.frames_dir[printer_id] = frames_dir
            self.frame_count[printer_id] = 0
            self.last_layer[printer_id] = -1
            self.current_job[printer_id] = filename_base

        print(f"[Timelapse] Recording started: {filename_base} (printer={printer_id})")
        self._emit_status(printer_id, recording=True, frame_count=0)

    def _capture_frame(self, printer_id):
        frame_data = self._get_camera_frame(printer_id)
        if not frame_data:
            return False

        frames_dir = self.frames_dir.get(printer_id)
        if not frames_dir:
            return False

        count = self.frame_count.get(printer_id, 0)
        frame_path = os.path.join(frames_dir, f'frame_{count:06d}.jpg')
        try:
            with open(frame_path, 'wb') as f:
                f.write(frame_data)
            with self._lock:
                self.frame_count[printer_id] = count + 1
            return True
        except Exception as e:
            print(f"[Timelapse] Frame save error: {e}")
            return False

    def _finish_recording(self, printer_id):
        with self._lock:
            if not self.recording.get(printer_id):
                return
            self.recording[printer_id] = False
            frames_dir = self.frames_dir.pop(printer_id, None)
            frame_count = self.frame_count.pop(printer_id, 0)
            job_name = self.current_job.pop(printer_id, 'timelapse')
            self.last_layer.pop(printer_id, None)
            self.pending_timelapse[printer_id] = False

        print(f"[Timelapse] Recording stopped for {printer_id}. Frames: {frame_count}")
        self._stop_camera_if_ours(printer_id)

        if frame_count < 2:
            print(f"[Timelapse] Not enough frames ({frame_count}), skipping assembly")
            if frames_dir and os.path.exists(frames_dir):
                shutil.rmtree(frames_dir, ignore_errors=True)
            self._emit_status(printer_id, recording=False, assembling=False,
                              error='Not enough frames captured')
            return

        self._emit_status(printer_id, recording=False, assembling=True)
        t = threading.Thread(
            target=self._assemble_video,
            args=(printer_id, frames_dir, frame_count, job_name),
            daemon=True,
        )
        t.start()

    def _assemble_video(self, printer_id, frames_dir, frame_count, job_name):
        if not self.timelapse_dir:
            print("[Timelapse] Cannot assemble: no storage directory")
            shutil.rmtree(frames_dir, ignore_errors=True)
            self._emit_status(printer_id, recording=False, assembling=False,
                              error='No storage directory configured')
            return

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in job_name)
        output_name = f"{safe_name}_{timestamp}.mp4"
        output_path = os.path.join(self.timelapse_dir, output_name)

        print(f"[Timelapse] Assembling {frame_count} frames -> {output_path}")
        success = False

        fps = self.config.get('fps', DEFAULT_CONFIG['fps'])
        preset = self.config.get('preset', DEFAULT_CONFIG['preset'])

        # Try ffmpeg first (better quality / codec support)
        if shutil.which('ffmpeg'):
            try:
                frame_pattern = os.path.join(frames_dir, 'frame_%06d.jpg')
                cmd = [
                    'ffmpeg', '-y',
                    '-framerate', str(fps),
                    '-i', frame_pattern,
                    '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p',
                    '-preset', preset,
                    output_path,
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=300)
                if result.returncode == 0:
                    success = True
                    print(f"[Timelapse] Assembled with ffmpeg: {output_path}")
                else:
                    print(f"[Timelapse] ffmpeg failed: {result.stderr.decode()[:200]}")
            except Exception as e:
                print(f"[Timelapse] ffmpeg error: {e}")

        # Fallback: OpenCV VideoWriter
        if not success:
            try:
                import cv2
                first_path = os.path.join(frames_dir, 'frame_000000.jpg')
                first = cv2.imread(first_path)
                if first is not None:
                    h, w = first.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                    out = cv2.VideoWriter(output_path, fourcc, float(fps), (w, h))
                    for i in range(frame_count):
                        fp = os.path.join(frames_dir, f'frame_{i:06d}.jpg')
                        if os.path.exists(fp):
                            frame = cv2.imread(fp)
                            if frame is not None:
                                out.write(frame)
                    out.release()
                    success = True
                    print(f"[Timelapse] Assembled with OpenCV: {output_path}")
            except Exception as e:
                print(f"[Timelapse] OpenCV assembly error: {e}")

        shutil.rmtree(frames_dir, ignore_errors=True)

        if self.socketio:
            if success:
                self.socketio.emit('timelapse_complete', {
                    'printer_id': printer_id,
                    'filename': output_name,
                })
            self._emit_status(
                printer_id,
                recording=False,
                assembling=False,
                error=None if success else 'Video assembly failed',
            )

    # ── Printer message hook ──────────────────────────────────────────────────

    def on_printer_message(self, printer_id, message):
        try:
            self._handle_message(printer_id, message)
        except Exception as e:
            print(f"[Timelapse] Error handling message: {e}")

    def _handle_message(self, printer_id, message):
        if not isinstance(message, dict):
            return

        # Locate the Status dict — it lives at different paths depending on topic
        topic = message.get('Topic', '')
        status_data = None

        if 'status' in topic.lower() and 'Status' in message:
            status_data = message['Status']
        elif 'Data' in message and isinstance(message.get('Data'), dict):
            d = message['Data']
            if 'Status' in d:
                status_data = d['Status']

        if not isinstance(status_data, dict):
            return

        print_info = status_data.get('PrintInfo')
        if not isinstance(print_info, dict):
            return

        print_status = print_info.get('Status', 0)
        current_layer = print_info.get('CurrentLayer', 0)
        total_layer = print_info.get('TotalLayer', 0)
        filename = print_info.get('Filename', 'timelapse')

        is_printing = (print_status == PRINT_STATUS_PRINTING) and total_layer > 0
        is_done = print_status in (PRINT_STATUS_COMPLETE, PRINT_STATUS_IDLE)

        recording_now = self.recording.get(printer_id, False)

        if is_printing and not recording_now:
            # A print is running — start recording if armed
            if self.pending_timelapse.get(printer_id, False):
                if self._ensure_camera(printer_id):
                    base = os.path.splitext(os.path.basename(filename or 'timelapse'))[0]
                    self._start_recording(printer_id, base)
                else:
                    print(f"[Timelapse] Camera unavailable, timelapse cancelled")
                    with self._lock:
                        self.pending_timelapse[printer_id] = False

        elif is_printing and recording_now:
            # Capture a frame whenever the layer number advances
            last = self.last_layer.get(printer_id, -1)
            if current_layer > last:
                with self._lock:
                    self.last_layer[printer_id] = current_layer
                self._capture_frame(printer_id)
                self._emit_status(
                    printer_id,
                    recording=True,
                    frame_count=self.frame_count.get(printer_id, 0),
                    current_layer=current_layer,
                    total_layer=total_layer,
                )

        elif is_done and recording_now:
            # Print finished — assemble the video
            self._finish_recording(printer_id)

    # ── Lifecycle hooks ───────────────────────────────────────────────────────

    def on_printer_disconnected(self, printer_id):
        if self.recording.get(printer_id):
            print(f"[Timelapse] Printer {printer_id} disconnected during recording")
            self._finish_recording(printer_id)

    def on_shutdown(self):
        for printer_id in list(self.recording.keys()):
            if self.recording.get(printer_id):
                self._finish_recording(printer_id)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _emit_status(self, printer_id, recording=False, assembling=False,
                     frame_count=0, current_layer=0, total_layer=0, error=None):
        if not self.socketio:
            return
        payload = {
            'printer_id': printer_id,
            'recording': recording,
            'assembling': assembling,
            'frame_count': frame_count,
            'current_layer': current_layer,
            'total_layer': total_layer,
        }
        if error:
            payload['error'] = error
        self.socketio.emit('timelapse_status', payload)
