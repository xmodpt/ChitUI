"""
Raspberry Pi camera support for ChitUI.

The frontend (web/js/uart.js and web/js/settings.js) has always called
/raspicam/status, /raspicam/settings, /raspicam/start, /raspicam/stop and
/raspicam/video, but no backend ever implemented them - every page load
produced a 404 in the console. This module provides them.

How it captures
---------------
It shells out to the camera CLI rather than using picamera2. That is a
deliberate choice: picamera2 is an apt package (python3-picamera2) that links
against system libcamera and cannot be pip-installed into a virtualenv, so a
ChitUI running in a venv could never import it. The CLI tools are always
present on a Pi OS image that can see a camera at all, and they emit MJPEG
directly, which is exactly what an <img> tag wants.

Binary, newest first:
    rpicam-vid      Bookworm and later
    libcamera-vid   Bullseye
    raspivid        Buster and earlier (legacy stack)

One capture process is shared by every viewer. A reader thread splits the
MJPEG byte stream into frames and publishes the most recent one; each HTTP
client loops on a condition variable and writes whatever the latest frame is,
so a slow client drops frames instead of stalling the camera.
"""

import os
import re
import shutil
import subprocess
import threading
import time

from flask import Response, jsonify, request

# JPEG markers used to cut the MJPEG stream into frames
_SOI = b'\xff\xd8\xff'   # start of image
_EOI = b'\xff\xd9'       # end of image

# Preferred binaries, newest stack first
_BINARIES = ['rpicam-vid', 'libcamera-vid', 'raspivid']

DEFAULTS = {
    'enabled': False,
    'width': 1280,
    'height': 720,
    'fps': 15,
    'quality': 80,
    'rotation': 0,       # 0 or 180 (libcamera only supports these two)
    'hflip': False,
    'vflip': False,
}


def _find_binary():
    """Return (name, path) of the first available camera CLI, or (None, None)."""
    for name in _BINARIES:
        path = shutil.which(name)
        if path:
            return name, path
    return None, None


def detect_cameras(logger=None):
    """Ask the camera stack what it can see.

    Returns (list_of_camera_descriptions, raw_output). An empty list means no
    camera is attached, the ribbon is in backwards, or the stack is not
    configured.
    """
    name, path = _find_binary()
    if not path:
        return [], 'no camera binary found'

    if name == 'raspivid':
        # The legacy stack has no --list-cameras; vcgencmd is the equivalent.
        vc = shutil.which('vcgencmd')
        if not vc:
            return [], 'vcgencmd not available'
        try:
            out = subprocess.run([vc, 'get_camera'], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
        except Exception as e:
            return [], f'vcgencmd failed: {e}'
        # e.g. "supported=1 detected=1"
        if 'detected=1' in out:
            return ['legacy camera (vcgencmd detected=1)'], out
        return [], out

    # libcamera stack: `<binary> --list-cameras` prints one indented line per
    # camera, e.g. "0 : imx219 [3280x2464 10-bit RGGB] (/base/soc/...)"
    try:
        proc = subprocess.run([path, '--list-cameras'], capture_output=True,
                              text=True, timeout=10)
        out = (proc.stdout or '') + (proc.stderr or '')
    except Exception as e:
        return [], f'{name} --list-cameras failed: {e}'

    cams = []
    for line in out.splitlines():
        m = re.match(r'\s*(\d+)\s*:\s*(\S+)', line)
        if m:
            cams.append(line.strip())
    if 'no cameras available' in out.lower():
        cams = []
    if logger and not cams:
        logger.debug(f'raspicam: no cameras reported by {name}: {out.strip()[:200]}')
    return cams, out.strip()


def _build_command(binary_name, path, cfg):
    """Assemble the capture command for the detected binary."""
    w = int(cfg.get('width', DEFAULTS['width']))
    h = int(cfg.get('height', DEFAULTS['height']))
    fps = int(cfg.get('fps', DEFAULTS['fps']))
    quality = int(cfg.get('quality', DEFAULTS['quality']))
    rotation = int(cfg.get('rotation', 0))

    if binary_name == 'raspivid':
        cmd = [path, '-t', '0', '-cd', 'MJPEG', '-w', str(w), '-h', str(h),
               '-fps', str(fps), '-q', str(quality), '-n', '-o', '-']
        if rotation:
            cmd += ['-rot', str(rotation)]
        if cfg.get('hflip'):
            cmd += ['-hf']
        if cfg.get('vflip'):
            cmd += ['-vf']
        return cmd

    # rpicam-vid / libcamera-vid
    cmd = [path, '-t', '0', '--codec', 'mjpeg', '--width', str(w),
           '--height', str(h), '--framerate', str(fps), '--quality', str(quality),
           '--nopreview', '--flush', '-o', '-']
    if rotation in (90, 180, 270):
        # libcamera only accepts 0 and 180; anything else is approximated.
        cmd += ['--rotation', '180' if rotation >= 180 else '0']
    if cfg.get('hflip'):
        cmd += ['--hflip']
    if cfg.get('vflip'):
        cmd += ['--vflip']
    return cmd


class RaspicamStreamer:
    """Owns the capture subprocess and publishes the latest JPEG frame."""

    def __init__(self, logger):
        self.logger = logger
        self._proc = None
        self._thread = None
        # Reentrant on purpose: start()'s failure path calls stop() while it
        # still holds the lock. With a plain Lock that deadlocked the request
        # thread forever the first time a camera failed to produce frames.
        self._lock = threading.RLock()
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._running = False
        self._error = None
        self._viewers = 0
        self._started_at = None
        self._stderr_tail = ''

    # -- state ----------------------------------------------------------
    @property
    def running(self):
        return self._running and self._proc is not None and self._proc.poll() is None

    @property
    def error(self):
        return self._error

    @property
    def viewers(self):
        return self._viewers

    @property
    def uptime(self):
        return int(time.time() - self._started_at) if self._started_at else 0

    # -- control --------------------------------------------------------
    def start(self, cfg):
        with self._lock:
            if self.running:
                return True, 'already running'

            name, path = _find_binary()
            if not path:
                self._error = ('No camera program found. Install it with: '
                               'sudo apt install -y rpicam-apps  '
                               '(or libcamera-apps on Bullseye)')
                return False, self._error

            cmd = _build_command(name, path, cfg)
            self.logger.info(f"raspicam: starting capture: {' '.join(cmd)}")
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0)
            except Exception as e:
                self._error = f'Could not start {name}: {e}'
                self.logger.error(f'raspicam: {self._error}')
                return False, self._error

            self._error = None
            self._stderr_tail = ''
            self._running = True
            self._started_at = time.time()
            self._frame = None
            self._seq = 0

            self._thread = threading.Thread(target=self._reader, daemon=True)
            self._thread.start()

            # Give the camera a moment to produce a first frame so that a
            # failure (no camera attached, busy, permissions) is reported to
            # the user now rather than as a blank <img> later.
            deadline = time.time() + 5
            while time.time() < deadline:
                if self._frame is not None:
                    return True, 'started'
                if self._proc.poll() is not None:
                    break
                time.sleep(0.1)

            if self._frame is None:
                stderr = self._drain_stderr()
                self.stop()
                self._error = stderr or 'Camera produced no frames within 5s'
                return False, self._error

            return True, 'started'

    def stop(self):
        with self._lock:
            self._running = False
            proc, self._proc = self._proc, None
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            self._started_at = None
            # Wake any viewers so their generators can exit
            with self._cond:
                self._cond.notify_all()
        self.logger.info('raspicam: capture stopped')
        return True

    def _drain_stderr(self):
        """Return the most recent stderr line from the capture process.

        Both start() and the reader thread call this, and whichever got there
        first used to consume the pipe and leave the other with nothing - so a
        real diagnostic like "no cameras available" was replaced by a generic
        timeout message. The last line is now retained.
        """
        proc = self._proc
        if not proc or not proc.stderr:
            return self._stderr_tail
        try:
            data = proc.stderr.read(4096) or b''
        except Exception:
            data = b''
        if data:
            text = data.decode('utf-8', 'replace')
            lines = [l for l in text.splitlines() if l.strip()]
            if lines:
                # Keep it short enough to show in a toast
                self._stderr_tail = lines[-1][:300]
        return self._stderr_tail

    # -- capture loop ---------------------------------------------------
    def _reader(self):
        """Split the MJPEG stream into frames and publish the newest one."""
        buf = bytearray()
        proc = self._proc
        try:
            while self._running and proc and proc.poll() is None:
                chunk = proc.stdout.read(32768)
                if not chunk:
                    break
                buf.extend(chunk)

                # Pull out every complete frame currently in the buffer.
                while True:
                    start = buf.find(_SOI)
                    if start < 0:
                        # No frame start yet - do not let the buffer grow
                        # without bound if we are fed garbage.
                        if len(buf) > 4 * 1024 * 1024:
                            del buf[:-1024]
                        break
                    end = buf.find(_EOI, start + len(_SOI))
                    if end < 0:
                        if start > 0:
                            del buf[:start]
                        break
                    frame = bytes(buf[start:end + len(_EOI)])
                    del buf[:end + len(_EOI)]
                    with self._cond:
                        self._frame = frame
                        self._seq += 1
                        self._cond.notify_all()
        except Exception as e:
            self.logger.warning(f'raspicam: reader stopped: {e}')
        finally:
            if self._running:
                # Process died on its own
                err = self._drain_stderr()
                self._error = err or 'Camera process exited unexpectedly'
                self.logger.warning(f'raspicam: {self._error}')
            self._running = False
            with self._cond:
                self._cond.notify_all()

    # -- viewers --------------------------------------------------------
    def frames(self):
        """Yield multipart MJPEG chunks for one HTTP client."""
        boundary = b'--frame\r\n'
        last_seq = -1
        self._viewers += 1
        try:
            while self.running:
                with self._cond:
                    # Wait for a frame newer than the one we already sent. A
                    # slow client simply misses frames; it never backs up the
                    # capture thread.
                    if self._seq == last_seq:
                        self._cond.wait(timeout=5)
                    if self._seq == last_seq:
                        continue  # timed out - re-check self.running
                    frame = self._frame
                    last_seq = self._seq
                if not frame:
                    continue
                yield (boundary +
                       b'Content-Type: image/jpeg\r\n' +
                       b'Content-Length: ' + str(len(frame)).encode() + b'\r\n\r\n' +
                       frame + b'\r\n')
        finally:
            self._viewers -= 1


def init_raspicam(app, *, login_required, load_settings, save_settings, logger):
    """Register the /raspicam/* routes on the Flask app.

    Mirrors the init_themes()/register_uart_routes() pattern used elsewhere so
    main.py only needs one more try/except block.
    """
    streamer = RaspicamStreamer(logger)

    def _cfg():
        settings = load_settings()
        cfg = dict(DEFAULTS)
        cfg.update(settings.get('raspicam', {}) or {})
        return cfg

    def _save_cfg(updates):
        settings = load_settings()
        cfg = dict(DEFAULTS)
        cfg.update(settings.get('raspicam', {}) or {})
        cfg.update(updates)
        settings['raspicam'] = cfg
        save_settings(settings)
        return cfg

    binary_name, binary_path = _find_binary()
    if binary_path:
        logger.info(f'raspicam: using {binary_name} ({binary_path})')
    else:
        logger.info('raspicam: no camera binary found '
                    '(install rpicam-apps to enable the Pi camera)')

    @app.route('/raspicam/status', methods=['GET'])
    @login_required
    def raspicam_status():
        cfg = _cfg()
        name, path = _find_binary()
        cams, raw = detect_cameras(logger) if path else ([], 'no camera binary found')
        return jsonify({
            'enabled': bool(cfg.get('enabled')),
            'available': bool(path) and bool(cams),
            'binary': name,
            'binary_path': path,
            'cameras': cams,
            'detail': raw if not cams else '',
            'streaming': streamer.running,
            'viewers': streamer.viewers,
            'uptime': streamer.uptime,
            'error': streamer.error,
            'settings': {k: cfg[k] for k in DEFAULTS if k != 'enabled'},
        })

    @app.route('/raspicam/settings', methods=['POST'])
    @login_required
    def raspicam_settings():
        data = request.get_json(silent=True) or {}
        updates = {}

        if 'enabled' in data:
            updates['enabled'] = bool(data['enabled'])
        for key in ('width', 'height', 'fps', 'quality', 'rotation'):
            if key in data:
                try:
                    updates[key] = int(data[key])
                except (TypeError, ValueError):
                    return jsonify({'success': False,
                                    'message': f'{key} must be a number'}), 400
        for key in ('hflip', 'vflip'):
            if key in data:
                updates[key] = bool(data[key])

        cfg = _save_cfg(updates)
        logger.info(f'raspicam: settings updated ({updates})')

        # Turning it off, or changing capture parameters, means the running
        # stream no longer matches the config - restart it.
        if streamer.running:
            if not cfg.get('enabled'):
                streamer.stop()
            elif any(k in updates for k in
                     ('width', 'height', 'fps', 'quality', 'rotation', 'hflip', 'vflip')):
                streamer.stop()
                streamer.start(cfg)

        return jsonify({'success': True, 'settings': cfg})

    @app.route('/raspicam/start', methods=['POST'])
    @login_required
    def raspicam_start():
        cfg = _cfg()
        if not cfg.get('enabled'):
            return jsonify({'success': False,
                            'message': 'Pi Camera is disabled in Settings'}), 400
        ok, msg = streamer.start(cfg)
        return jsonify({'success': ok, 'message': msg}), (200 if ok else 500)

    @app.route('/raspicam/stop', methods=['POST'])
    @login_required
    def raspicam_stop():
        streamer.stop()
        return jsonify({'success': True})

    @app.route('/raspicam/snapshot', methods=['GET'])
    @login_required
    def raspicam_snapshot():
        """Single JPEG - handy for timelapse or a dashboard thumbnail."""
        if not streamer.running:
            ok, msg = streamer.start(_cfg())
            if not ok:
                return jsonify({'success': False, 'message': msg}), 503
        deadline = time.time() + 5
        while time.time() < deadline:
            frame = streamer._frame
            if frame:
                return Response(frame, mimetype='image/jpeg')
            time.sleep(0.05)
        return jsonify({'success': False, 'message': 'No frame available'}), 503

    @app.route('/raspicam/video')
    @login_required
    def raspicam_video():
        if not streamer.running:
            ok, msg = streamer.start(_cfg())
            if not ok:
                return jsonify({'success': False, 'message': msg}), 503
        return Response(streamer.frames(),
                        mimetype='multipart/x-mixed-replace; boundary=frame',
                        headers={'Cache-Control': 'no-store, no-cache, must-revalidate',
                                 'Pragma': 'no-cache',
                                 'Age': '0'})

    app.raspicam_streamer = streamer
    return streamer
