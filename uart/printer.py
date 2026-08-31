"""
uart/printer.py
---------------
UART serial backend for Chitu-based resin printers.
Handles: Mars 2, Mars Pro, Photon, and any other printer
that exposes a UART header on the mainboard.

Protocol facts (confirmed by Octoprint-Chituboard + working test):
  - 8N1, no flow control (xonxoff=False, rtscts=False, dsrdtr=False)
  - NO checksums - firmware rejects N0 M110 style GCode
  - Commands sent as plain ASCII + CRLF
  - M4002        -> "ok N:1"                    (hello / ping)
  - M4000        -> "ok B:x E1:x Z:x F:x D:x T:x"  (status)
  - M6030 'file' -> starts print
  - M25 I0       -> pause  (I0 is required by CBD firmware)
  - M24          -> resume
  - M33          -> cancel/stop
  - G28 Z0       -> home Z axis
  - G0 Z{n} F600 I0 -> move Z (F600 feedrate, I0 required)
  - Printer sends unsolicited status broadcasts when idle (normal)
  - Firmware V4.4.3+ broke serial on Mars 2 - downgrade to V4.3.x if no response
  - RX uses raw byte accumulation + idle flush, NOT readline()
  - _cmd_lock prevents poll thread and command sender fighting over the RX queue

Physical wiring (3.3V logic only):
  Pi GPIO14 (TX, pin 8)  -> Printer RX
  Pi GPIO15 (RX, pin 10) -> Printer TX
  Pi GND    (pin 6)      -> Printer GND
  DO NOT connect 5V - power Pi separately
"""

import queue
import time
import threading
from loguru import logger

try:
    import serial
    import serial.tools.list_ports
    UART_SUPPORT = True
except ImportError:
    UART_SUPPORT = False
    logger.warning("pyserial not installed - UART support disabled. "
                   "Run: sudo apt install python3-serial")


class UARTPrinter:
    """
    Full UART serial backend for a single Chitu-based printer.
    Designed to be instantiated once per printer and kept alive.
    """

    # M-code commands - plain ASCII, no checksums
    CMD_HELLO     = "M4002"
    CMD_STATUS    = "M4000"
    CMD_FILE_LIST = "M4001"
    CMD_PRINT     = "M6030"
    CMD_PAUSE     = "M25 I0"   # I0 required by CBD firmware (confirmed by Octoprint-Chituboard)
    CMD_RESUME    = "M24"
    CMD_CANCEL    = "M33"      # M33 = stop/cancel; M3012 is wrong for Mars 2 CBD firmware
    CMD_Z_HOME    = "G28 Z0"
    CMD_Z_MOVE    = "G0 Z{} F600 I0"  # F600 feedrate, I0 required by CBD firmware

    # Unsolicited idle status broadcast prefixes - not command responses
    NOISE_PREFIXES = ("T:", "B:", "ok T:", "ok B:")

    def __init__(self, printer_id: str, port: str = '/dev/ttyS0',
                 baudrate: int = 115200):
        self.printer_id = printer_id
        self.port       = port
        self.baudrate   = baudrate

        self._ser         = None
        self._lock        = threading.Lock()      # serial port write lock
        self._cmd_lock    = threading.Lock()      # one command at a time
        self._running     = False
        self._rx_thread   = None
        self._poll_thread = None
        self._online      = False
        self._rx_queue    = queue.Queue()
        self._last_status = {}

        # socketio reference injected after creation (avoids circular import)
        self._socketio       = None
        self._printers       = None   # reference to global printers dict
        self._plugin_manager = None   # reference to PluginManager for notifications
        self._last_machine_status = None  # track transitions for notify
        self._printing_file  = None   # filename currently being printed (None = idle)

    def set_socketio(self, socketio, printers: dict, plugin_manager=None):
        """Inject SocketIO, printers dict, and optional plugin_manager after construction."""
        self._socketio       = socketio
        self._printers       = printers
        self._plugin_manager = plugin_manager

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Open serial port with correct 8N1 settings and no flow control.
        Flushes boot noise, sends M4002. Stays connected even without a
        hello response so the user can debug via manual commands.
        """
        if not UART_SUPPORT:
            logger.error("pyserial not installed - cannot connect via UART")
            return False
        try:
            ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=2,
                write_timeout=2,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )
            # Let boot noise settle, then flush
            time.sleep(0.3)
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            self._ser     = ser
            self._running = True
            self._start_rx_thread()

            logger.info(f"UART {self.printer_id}: sending M4002 on {self.port} @ {self.baudrate}")
            self._write_cmd(self.CMD_HELLO)

            # Wait up to 3s for any response
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if not self._rx_queue.empty():
                    resp = self._rx_queue.get_nowait()
                    logger.info(f"UART {self.printer_id}: hello response: {resp!r}")
                    self._online = True
                    self._start_poll_thread()
                    return True
                time.sleep(0.05)

            logger.warning(f"UART {self.printer_id}: no response to M4002 on {self.port}")
            logger.warning("Check: raspi-config serial console disabled? Firmware V4.3.x?")
            self._online = True   # stay connected for manual testing
            self._start_poll_thread()
            return True

        except Exception as e:
            logger.error(f"UART connect error on {self.port}: {e}")
            self._running = False
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
            return False

    def disconnect(self):
        """Close serial connection and stop all threads."""
        self._running = False
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2)
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2)
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._online = False
        logger.info(f"UART printer {self.printer_id} disconnected")

    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open and self._online

    # ------------------------------------------------------------------
    # RX thread - raw byte accumulation
    # ------------------------------------------------------------------

    def _start_rx_thread(self):
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

    def _rx_loop(self):
        """
        Accumulate raw bytes. Flush to rx_queue on 150ms of silence.
        Handles \\r, \\r\\n, and burst data correctly unlike readline().
        Strips null bytes (firmware 4.4.3 bug).
        """
        buf       = b""
        last_data = time.time()
        IDLE_S    = 0.15

        while self._running:
            ser = self._ser
            if ser and ser.is_open:
                try:
                    waiting = ser.in_waiting
                    if waiting:
                        buf      += ser.read(waiting)
                        last_data = time.time()
                    elif buf and (time.time() - last_data) > IDLE_S:
                        text = buf.decode("ascii", errors="replace")
                        text = text.replace("\r\n", "\n").replace("\r", "\n")
                        for line in text.split("\n"):
                            line = line.replace("\x00", "").strip()
                            if line:
                                self._rx_queue.put(line)
                        buf = b""
                    else:
                        time.sleep(0.01)
                except Exception as e:
                    logger.debug(f"UART RX error: {e}")
                    time.sleep(0.05)
            else:
                time.sleep(0.1)

    # ------------------------------------------------------------------
    # TX helpers
    # ------------------------------------------------------------------

    def _write_cmd(self, cmd: str):
        """Write a command to the serial port (adds CRLF)."""
        if not self._ser or not self._ser.is_open:
            return
        try:
            with self._lock:
                self._ser.write((cmd + "\r\n").encode("ascii"))
                self._ser.flush()
            logger.debug(f"UART TX: {cmd!r}")
        except Exception as e:
            logger.error(f"UART write error: {e}")
            self._online = False

    def _send_cmd(self, cmd: str, timeout: float = 2.0) -> str:
        """
        Send a command and collect the response.
        Uses _cmd_lock to prevent poll thread stealing the response.
        """
        if not self._ser or not self._ser.is_open:
            return ""

        with self._cmd_lock:
            # Drain stale noise
            while not self._rx_queue.empty():
                try:
                    self._rx_queue.get_nowait()
                except Exception:
                    break

            self._write_cmd(cmd)

            deadline       = time.time() + timeout
            response_lines = []
            while time.time() < deadline:
                try:
                    line = self._rx_queue.get(timeout=0.05)
                    line = line.replace("\x00", "").strip()
                    if not line:
                        continue
                    if self._is_noise(line):
                        logger.debug(f"UART noise ignored: {line!r}")
                        continue
                    response_lines.append(line)
                    if line.startswith("ok") or "error" in line.lower():
                        break
                except queue.Empty:
                    if response_lines:
                        break
            return "\n".join(response_lines)

    def _is_noise(self, line: str) -> bool:
        """True if line is an unsolicited idle broadcast, not a cmd response."""
        for prefix in self.NOISE_PREFIXES:
            if line.startswith(prefix):
                return True
        if (line.startswith("ok ") and
                ("B:" in line or "E1:" in line or "T:" in line) and
                "N:" not in line):
            return True
        return False

    # ------------------------------------------------------------------
    # Poll thread
    # ------------------------------------------------------------------

    def _start_poll_thread(self):
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        """
        Poll every 3s. Falls back to M4002 ping when M4000 returns nothing
        (Mars 2 is silent when idle). Requires 3 consecutive failures before
        marking offline. Skips cycle if _cmd_lock is held (cmd in flight).
        """
        fail_count = 0
        MAX_FAILS  = 3

        while self._running:
            if self._cmd_lock.locked():
                time.sleep(0.5)
                continue
            try:
                status = self.get_status()
                if status:
                    fail_count = 0
                    self._last_status = status
                    self._online = True
                    if self._printers and self.printer_id in self._printers:
                        self._printers[self.printer_id]['online'] = True
                    # If printer reports idle/stopped and we thought we were
                    # printing, the print has finished — clear the tracked file
                    if self._printing_file and status.get('machine_status', 0) in (0, 3):
                        self._printing_file = None
                    if self._socketio:
                        self._socketio.emit("uart_status", {
                            "printer_id": self.printer_id,
                            "status": status,
                            "online": True
                        })
                    self._notify_status_change(status)
                else:
                    # M4000 empty - Mars 2 is often silent while printing.
                    # If we know a print is in progress, keep reporting it.
                    if self._printing_file:
                        synthesised = {
                            "machine_status": 1,
                            "status_text":    "Printing",
                            "Filename":       self._printing_file,
                            "CurrentLayer":   0,
                            "TotalLayer":     0,
                        }
                        fail_count = 0
                        self._online = True
                        if self._printers and self.printer_id in self._printers:
                            self._printers[self.printer_id]['online'] = True
                        if self._socketio:
                            self._socketio.emit("uart_status", {
                                "printer_id": self.printer_id,
                                "status":     synthesised,
                                "online":     True
                            })
                        self._notify_status_change(synthesised)
                    else:
                        # Idle and not printing — try keep-alive ping
                        ping = self._send_cmd(self.CMD_HELLO, timeout=1.5)
                        if ping:
                            fail_count = 0
                            self._online = True
                            if self._printers and self.printer_id in self._printers:
                                self._printers[self.printer_id]['online'] = True
                            if self._socketio:
                                self._socketio.emit("uart_status", {
                                    "printer_id": self.printer_id,
                                    "status": {"status_text": "Idle",
                                               "machine_status": 0},
                                    "online": True
                                })
                        else:
                            fail_count += 1
                            logger.debug(f"UART {self.printer_id}: no response "
                                         f"({fail_count}/{MAX_FAILS})")
                            if fail_count >= MAX_FAILS and self._online:
                                self._online = False
                                if self._printers and self.printer_id in self._printers:
                                    self._printers[self.printer_id]['online'] = False
                                if self._socketio:
                                    self._socketio.emit("uart_status", {
                                        "printer_id": self.printer_id,
                                        "status": {},
                                        "online": False
                                    })
                                    self._socketio.emit("printers", self._printers)
            except Exception as e:
                logger.error(f"UART poll error: {e}")
            time.sleep(3)

    # ------------------------------------------------------------------
    # Plugin notification bridge
    # ------------------------------------------------------------------

    def _notify_status_change(self, status: dict):
        """
        Translate UART machine_status into the WiFi PrintInfo shape and call
        plugin_manager.notify_printer_message() so plugins like chitu_notify
        work identically for UART and WiFi printers.

        UART machine_status: 0=Idle, 1=Printing, 2=Paused, 3=Stopped
        SDCP PrintInfo.Status: 0=Idle, 3=Exposuring, 6=Paused, 8=Stopped, 9=Complete
        """
        if not self._plugin_manager:
            return

        machine_status = status.get('machine_status')
        if machine_status is None:
            return

        # Map UART → SDCP PrintInfo.Status
        _STATUS_MAP = {0: 0, 1: 3, 2: 6, 3: 8}
        sdcp_print_status = _STATUS_MAP.get(machine_status, 0)

        # Detect transition: stopped→idle should emit Complete (9) once
        prev = self._last_machine_status
        self._last_machine_status = machine_status

        if machine_status == prev:
            return  # no change, skip

        printer_name = ''
        if self._printers and self.printer_id in self._printers:
            printer_name = self._printers[self.printer_id].get('name', self.printer_id)

        # Build a WiFi-compatible message so on_printer_message works unchanged
        print_info = {
            'Status':       sdcp_print_status,
            'CurrentLayer': status.get('CurrentLayer', 0),
            'TotalLayer':   status.get('TotalLayer', 0),
            'Filename':     status.get('Filename', ''),
            'CurrentTicks': 0,
            'TotalTicks':   0,
            'ErrorNumber':  0,
        }

        # When transitioning from Printing/Paused → Idle, emit Complete (9)
        # so the notify plugin sees a proper print end event
        if prev in (1, 2) and machine_status == 0:
            print_info['Status'] = 9  # SDCP_PRINT_STATUS_COMPLETE

        message = {
            'MainboardID': self.printer_id,
            'Status': {
                'CurrentStatus': [machine_status],
                'PrintInfo':     [print_info],
            }
        }

        try:
            self._plugin_manager.notify_printer_message(self.printer_id, message)
        except Exception as e:
            logger.debug(f"UART notify error: {e}")

    # ------------------------------------------------------------------
    # Public command API
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        resp = self._send_cmd(self.CMD_STATUS)
        if not resp:
            return {}
        return self._parse_status(resp)

    def _parse_status(self, raw: str) -> dict:
        """
        Parse M4000 response.
        Confirmed format: ok B:0/0 E1:0/0 E2:0/0 X:0.000 Y:0.000
                          Z:0.000 F:0/256 D:0/0/0 T:0
        Also handles legacy: MachineStatus: / PrintInfo:
        """
        status = {"raw": raw}
        try:
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("ok ") and "Z:" in line:
                    for part in line[3:].split():
                        if ":" in part:
                            k, v = part.split(":", 1)
                            status[k] = v
                    if "D" in status:
                        d = status["D"].split("/")
                        if len(d) >= 3:
                            try:
                                state = int(d[2])
                                status["machine_status"] = state
                                status["status_text"] = {
                                    0: "Idle", 1: "Printing",
                                    2: "Paused", 3: "Stopped"
                                }.get(state, "Unknown")
                                status["CurrentLayer"] = int(d[0])
                                status["TotalLayer"]   = int(d[1])
                            except (ValueError, IndexError):
                                pass
                elif line.startswith("MachineStatus:"):
                    val = int(line.split(":")[1])
                    status["machine_status"] = val
                    status["status_text"] = {
                        0: "Idle", 1: "Printing",
                        2: "Paused", 3: "Stopped"
                    }.get(val, "Unknown")
                elif line.startswith("PrintInfo:"):
                    for pair in line[len("PrintInfo:"):].split(","):
                        if ":" in pair:
                            k, v = pair.split(":", 1)
                            try:
                                status[k.strip()] = int(v.strip())
                            except ValueError:
                                status[k.strip()] = v.strip()
        except Exception as e:
            logger.debug(f"UART status parse error: {e}")
        return status

    def get_files(self) -> list:
        resp = self._send_cmd(self.CMD_FILE_LIST, timeout=5.0)
        return [line.strip() for line in resp.split("\n")
                if line.strip().endswith(('.ctb', '.goo', '.prz'))]

    def start_print(self, filename: str) -> bool:
        clean = filename.lstrip("/").replace("usb/", "", 1)
        ok = bool(self._send_cmd(f"{self.CMD_PRINT} '{clean}'"))
        if ok:
            self._printing_file = filename
            self._last_machine_status = None  # force notify on next poll
        return ok

    def pause(self) -> bool:
        return bool(self._send_cmd(self.CMD_PAUSE))

    def resume(self) -> bool:
        return bool(self._send_cmd(self.CMD_RESUME))

    def cancel(self) -> bool:
        ok = bool(self._send_cmd(self.CMD_CANCEL))
        if ok:
            self._printing_file = None
        return ok

    def home_z(self) -> bool:
        return bool(self._send_cmd(self.CMD_Z_HOME))

    def move_z(self, mm: float) -> bool:
        return bool(self._send_cmd(self.CMD_Z_MOVE.format(round(mm, 2))))

    def send_raw(self, cmd: str) -> str:
        return self._send_cmd(cmd)
