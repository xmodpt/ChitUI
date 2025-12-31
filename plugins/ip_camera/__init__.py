"""
IP Camera Plugin for ChitUI Plus

Stream IP cameras using RTSP, HTTP, or other protocols.
Supports OpenCV, GStreamer, and FFmpeg.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from plugins.base import ChitUIPlugin
from flask import Blueprint, jsonify, Response, request, render_template_string
import threading
import time
import json
import logging

logger = logging.getLogger(__name__)

# Try to import OpenCV
try:
    import cv2
    CAMERA_SUPPORT = True
except ImportError:
    CAMERA_SUPPORT = False
    logger.warning("OpenCV not installed. IP Camera plugin requires opencv-python-headless")


class IPCameraStream:
    """Handles a single IP camera stream"""

    def __init__(self, camera_id, camera_config):
        self.camera_id = camera_id
        self.config = camera_config
        self.name = camera_config.get('name', 'IP Camera')
        self.url = camera_config.get('url', '')
        self.protocol = camera_config.get('protocol', 'rtsp')

        self.cap = None
        self.running = False
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.capture_thread = None

    def start(self):
        """Start the camera stream"""
        if not CAMERA_SUPPORT:
            logger.error("OpenCV not available")
            return False

        if self.running:
            logger.warning(f"Camera {self.camera_id} already running")
            return False

        self.running = True

        try:
            logger.info(f"Connecting to IP camera: {self.name} ({self.url})")

            # Configure OpenCV for different protocols
            if self.protocol.lower() == 'rtsp':
                os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|udp'
                self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            elif self.protocol.lower() == 'http' or self.protocol.lower() == 'mjpeg':
                self.cap = cv2.VideoCapture(self.url)
            else:
                # Try to auto-detect
                self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

            if not self.cap.isOpened():
                logger.error(f"Failed to open camera stream: {self.url}")
                self.running = False
                return False

            # Set buffer size to reduce latency
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            # Try to read first frame to verify connection
            for i in range(10):
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    logger.info(f"Camera {self.name} connected: {frame.shape}")
                    # Start capture thread
                    self.capture_thread = threading.Thread(
                        target=self._capture_frames,
                        daemon=True
                    )
                    self.capture_thread.start()
                    return True
                time.sleep(0.5)

            logger.error(f"No frames received from camera: {self.name}")
            self.running = False
            return False

        except Exception as e:
            logger.error(f"Error starting camera {self.name}: {e}")
            self.running = False
            return False

    def _capture_frames(self):
        """Background thread to capture frames"""
        logger.info(f"Capture thread started for {self.name}")
        frame_count = 0

        while self.running and self.cap:
            try:
                # Skip frames to reduce latency
                for _ in range(2):
                    if not self.running:
                        break
                    self.cap.grab()

                ret, frame = self.cap.retrieve()

                if ret and frame is not None:
                    # Resize for web streaming
                    max_width = 1280
                    height, width = frame.shape[:2]
                    if width > max_width:
                        scale = max_width / width
                        new_width = int(width * scale)
                        new_height = int(height * scale)
                        frame = cv2.resize(frame, (new_width, new_height))

                    # Encode as JPEG
                    ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])

                    if ret:
                        with self.frame_lock:
                            self.latest_frame = buffer.tobytes()
                        frame_count += 1
                else:
                    time.sleep(0.01)

            except Exception as e:
                logger.error(f"Capture error for {self.name}: {e}")
                break

        logger.info(f"Capture thread stopped for {self.name}. Total frames: {frame_count}")

    def get_frame(self):
        """Get the latest frame"""
        with self.frame_lock:
            return self.latest_frame

    def stop(self):
        """Stop the camera stream"""
        self.running = False

        # Wait for capture thread to stop
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2)

        # Release camera
        if self.cap:
            try:
                self.cap.release()
                self.cap = None
            except Exception as e:
                logger.error(f"Error releasing camera {self.name}: {e}")

        self.latest_frame = None
        logger.info(f"Camera {self.name} stopped")


class Plugin(ChitUIPlugin):
    """IP Camera Plugin Implementation"""

    def __init__(self, plugin_dir):
        super().__init__(plugin_dir)
        self.cameras = {}  # Active camera streams
        self.camera_configs = []  # Camera configurations
        self.config_file = os.path.expanduser('~/.chitui/ip_camera_config.json')
        self.load_camera_configs()

    def get_name(self):
        return "IP Camera"

    def get_version(self):
        return "1.0.0"

    def get_description(self):
        return "Stream IP cameras using RTSP, HTTP, or other protocols. Supports OpenCV, GStreamer, and FFmpeg."

    def get_author(self):
        return "ChitUI Plus"

    def get_dependencies(self):
        return ['opencv-python-headless>=4.5.0']

    def load_camera_configs(self):
        """Load camera configurations from file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    self.camera_configs = json.load(f)
                logger.info(f"Loaded {len(self.camera_configs)} camera configurations")
        except Exception as e:
            logger.error(f"Error loading camera configs: {e}")
            self.camera_configs = []

    def save_camera_configs(self):
        """Save camera configurations to file"""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.camera_configs, f, indent=2)
            logger.info(f"Saved {len(self.camera_configs)} camera configurations")
        except Exception as e:
            logger.error(f"Error saving camera configs: {e}")

    def on_startup(self, app, socketio):
        """Initialize plugin on startup"""
        logger.info(f"{self.get_name()} plugin starting...")

        if not CAMERA_SUPPORT:
            logger.warning("OpenCV not installed. Camera streaming will not work.")

        self.socketio = socketio
        self.app = app

        # Create Flask blueprint
        self.blueprint = Blueprint(
            'ip_camera',
            __name__,
            static_folder=self.get_static_folder(),
            template_folder=self.get_template_folder()
        )

        # Register routes
        self._register_routes()

        logger.info(f"{self.get_name()} plugin started with {len(self.camera_configs)} cameras configured")

    def _register_routes(self):
        """Register Flask routes"""

        @self.blueprint.route('/cameras')
        def get_cameras():
            """Get list of configured cameras"""
            cameras_status = []
            for idx, config in enumerate(self.camera_configs):
                camera_id = f"camera_{idx}"
                is_active = camera_id in self.cameras and self.cameras[camera_id].running
                cameras_status.append({
                    'id': camera_id,
                    'name': config.get('name', 'IP Camera'),
                    'url': config.get('url', ''),
                    'protocol': config.get('protocol', 'rtsp'),
                    'active': is_active
                })
            return jsonify({'ok': True, 'cameras': cameras_status})

        @self.blueprint.route('/camera/<camera_id>/start', methods=['POST'])
        def start_camera(camera_id):
            """Start a specific camera stream"""
            if not CAMERA_SUPPORT:
                return jsonify({'ok': False, 'msg': 'OpenCV not installed. Run: pip install opencv-python-headless'})

            # Check if camera already running
            if camera_id in self.cameras and self.cameras[camera_id].running:
                return jsonify({'ok': False, 'msg': 'Camera already running'})

            # Find camera config
            try:
                idx = int(camera_id.split('_')[1])
                if idx < 0 or idx >= len(self.camera_configs):
                    return jsonify({'ok': False, 'msg': 'Invalid camera ID'})

                config = self.camera_configs[idx]

                # Create and start camera stream
                camera_stream = IPCameraStream(camera_id, config)
                if camera_stream.start():
                    self.cameras[camera_id] = camera_stream
                    return jsonify({'ok': True, 'msg': f'Camera {config["name"]} started'})
                else:
                    return jsonify({'ok': False, 'msg': f'Failed to connect to camera {config["name"]}'})

            except Exception as e:
                logger.error(f"Error starting camera: {e}")
                return jsonify({'ok': False, 'msg': str(e)})

        @self.blueprint.route('/camera/<camera_id>/stop', methods=['POST'])
        def stop_camera(camera_id):
            """Stop a specific camera stream"""
            if camera_id not in self.cameras:
                return jsonify({'ok': False, 'msg': 'Camera not running'})

            try:
                self.cameras[camera_id].stop()
                del self.cameras[camera_id]
                return jsonify({'ok': True, 'msg': 'Camera stopped'})
            except Exception as e:
                logger.error(f"Error stopping camera: {e}")
                return jsonify({'ok': False, 'msg': str(e)})

        @self.blueprint.route('/camera/<camera_id>/video')
        def stream_camera(camera_id):
            """Stream video from a specific camera"""
            if camera_id not in self.cameras or not self.cameras[camera_id].running:
                return Response('Camera not active', status=404)

            def generate():
                camera = self.cameras[camera_id]
                last_frame = None

                while camera.running:
                    frame = camera.get_frame()

                    if frame and frame != last_frame:
                        last_frame = frame
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    else:
                        time.sleep(0.01)

            return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

        @self.blueprint.route('/config', methods=['GET', 'POST'])
        def camera_config():
            """Get or update camera configurations"""
            if request.method == 'GET':
                return jsonify({'ok': True, 'cameras': self.camera_configs})

            elif request.method == 'POST':
                try:
                    data = request.get_json()
                    action = data.get('action')

                    if action == 'add':
                        # Add new camera
                        new_camera = {
                            'name': data.get('name', 'IP Camera'),
                            'url': data.get('url', ''),
                            'protocol': data.get('protocol', 'rtsp')
                        }
                        self.camera_configs.append(new_camera)
                        self.save_camera_configs()
                        return jsonify({'ok': True, 'msg': 'Camera added'})

                    elif action == 'update':
                        # Update existing camera
                        idx = data.get('index')
                        if 0 <= idx < len(self.camera_configs):
                            self.camera_configs[idx] = {
                                'name': data.get('name', 'IP Camera'),
                                'url': data.get('url', ''),
                                'protocol': data.get('protocol', 'rtsp')
                            }
                            self.save_camera_configs()
                            return jsonify({'ok': True, 'msg': 'Camera updated'})
                        return jsonify({'ok': False, 'msg': 'Invalid camera index'})

                    elif action == 'delete':
                        # Delete camera
                        idx = data.get('index')
                        if 0 <= idx < len(self.camera_configs):
                            # Stop camera if running
                            camera_id = f"camera_{idx}"
                            if camera_id in self.cameras:
                                self.cameras[camera_id].stop()
                                del self.cameras[camera_id]

                            del self.camera_configs[idx]
                            self.save_camera_configs()
                            return jsonify({'ok': True, 'msg': 'Camera deleted'})
                        return jsonify({'ok': False, 'msg': 'Invalid camera index'})

                    else:
                        return jsonify({'ok': False, 'msg': 'Invalid action'})

                except Exception as e:
                    logger.error(f"Error in camera config: {e}")
                    return jsonify({'ok': False, 'msg': str(e)})

        @self.blueprint.route('/test', methods=['POST'])
        def test_connection():
            """Test camera connection without starting stream"""
            if not CAMERA_SUPPORT:
                return jsonify({'ok': False, 'msg': 'OpenCV not installed. Run: pip install opencv-python-headless'})

            try:
                data = request.get_json()
                url = data.get('url', '')
                protocol = data.get('protocol', 'rtsp')

                if not url:
                    return jsonify({'ok': False, 'msg': 'No URL provided'})

                logger.info(f"Testing camera connection: {url}")

                # Configure OpenCV for different protocols
                if protocol.lower() == 'rtsp':
                    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|udp'
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                elif protocol.lower() == 'http' or protocol.lower() == 'mjpeg':
                    cap = cv2.VideoCapture(url)
                else:
                    # Try to auto-detect
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)

                if not cap.isOpened():
                    cap.release()
                    return jsonify({'ok': False, 'msg': 'Failed to open camera stream. Check URL and network connectivity.'})

                # Try to read first frame with timeout
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                success = False

                for i in range(10):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        height, width = frame.shape[:2]
                        cap.release()
                        return jsonify({
                            'ok': True,
                            'msg': f'Connection successful! Camera resolution: {width}x{height}'
                        })
                    time.sleep(0.5)

                cap.release()
                return jsonify({'ok': False, 'msg': 'Connected but no frames received. Camera may not be streaming.'})

            except Exception as e:
                logger.error(f"Error testing camera: {e}")
                return jsonify({'ok': False, 'msg': f'Connection test failed: {str(e)}'})

        @self.blueprint.route('/settings', methods=['GET'])
        def get_settings():
            """Get settings HTML"""
            settings_template = os.path.join(self.get_template_folder(), 'settings.html')
            if os.path.exists(settings_template):
                with open(settings_template, 'r') as f:
                    return f.read()
            return 'Settings template not found', 404

    def on_shutdown(self):
        """Stop all cameras on shutdown"""
        logger.info(f"{self.get_name()} plugin shutting down")
        for camera_id, camera in list(self.cameras.items()):
            try:
                camera.stop()
            except Exception as e:
                logger.error(f"Error stopping camera {camera_id}: {e}")
        self.cameras.clear()

    def get_ui_integration(self):
        """Return UI configuration"""
        return {
            'type': 'tab',
            'location': 'camera',
            'icon': 'bi-camera-video',
            'title': 'IP Camera',
            'template': 'ip_camera.html'
        }
