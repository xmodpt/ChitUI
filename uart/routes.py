"""
uart/routes.py
--------------
Flask API routes for UART printer management.
Registered in main.py via:  from uart.routes import register_uart_routes
                             register_uart_routes(app, ...)
"""

import hashlib
import os
import threading

from flask import request, jsonify
from loguru import logger
from werkzeug.utils import secure_filename

from uart.printer import UARTPrinter, UART_SUPPORT


def register_uart_routes(app, login_required, printers, uart_connections,
                         socketio, load_settings, save_settings,
                         USB_GADGET_FOLDER, UPLOAD_FOLDER,
                         THUMBNAILS_FOLDER, USE_USB_GADGET,
                         extract_thumbnail_for_file, file_db, Path,
                         plugin_manager=None):
    """
    Register all /uart/* and UART-related /printer/* routes on the Flask app.
    """

    # ------------------------------------------------------------------
    # Helper shared by routes
    # ------------------------------------------------------------------

    def _connect_uart(printer_id: str, port: str, baudrate: int = 115200):
        """Connect/reconnect a UART printer in background thread."""
        if printer_id in uart_connections:
            uart_connections[printer_id].disconnect()

        uart = UARTPrinter(printer_id, port, baudrate)
        uart.set_socketio(socketio, printers, plugin_manager)
        connected = uart.connect()

        if connected:
            uart_connections[printer_id] = uart
            if printer_id in printers:
                printers[printer_id]['online'] = True
            socketio.emit('printers', printers)
            logger.info(f"UART printer {printer_id} connected on {port}")
        else:
            logger.warning(f"UART printer {printer_id} failed to connect on {port}")
            if printer_id in printers:
                printers[printer_id]['online'] = False
            socketio.emit('printers', printers)

        return connected

    # Store helper so main.py can call it on startup
    app._connect_uart_printer = _connect_uart

    # ------------------------------------------------------------------
    # GET /uart/ports
    # ------------------------------------------------------------------

    @app.route('/uart/ports', methods=['GET'])
    @login_required
    def uart_list_ports():
        """List available serial ports on this Pi."""
        if not UART_SUPPORT:
            return jsonify({'success': False,
                            'message': 'pyserial not installed',
                            'ports': []})
        try:
            import serial.tools.list_ports
            ports = [{'device': p.device,
                      'description': p.description or p.device}
                     for p in serial.tools.list_ports.comports()]
            for k in ['/dev/ttyS0', '/dev/ttyAMA0', '/dev/serial0']:
                if os.path.exists(k) and not any(p['device'] == k for p in ports):
                    ports.insert(0, {'device': k,
                                     'description': f'{k} (Pi UART)'})
            return jsonify({'success': True,
                            'ports': ports,
                            'uart_support': UART_SUPPORT})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e), 'ports': []})

    # ------------------------------------------------------------------
    # POST /uart/connect
    # ------------------------------------------------------------------

    @app.route('/uart/connect', methods=['POST'])
    @login_required
    def uart_connect():
        """Connect/reconnect a UART printer."""
        data       = request.json
        printer_id = data.get('printer_id')
        port       = data.get('port', '/dev/ttyS0')
        baudrate   = int(data.get('baudrate', 115200))

        if not printer_id or printer_id not in printers:
            return jsonify({'success': False,
                            'message': 'Printer not found'}), 404
        if not UART_SUPPORT:
            return jsonify({'success': False,
                            'message': 'pyserial not installed. '
                                       'Run: sudo apt install python3-serial'}), 400

        # Persist port setting
        settings = load_settings()
        if printer_id in settings.get('printers', {}):
            settings['printers'][printer_id]['uart_port']     = port
            settings['printers'][printer_id]['uart_baudrate'] = baudrate
            save_settings(settings)

        printers[printer_id]['uart_port']     = port
        printers[printer_id]['uart_baudrate'] = baudrate

        threading.Thread(target=_connect_uart,
                         args=(printer_id, port, baudrate),
                         daemon=True).start()
        return jsonify({'success': True,
                        'message': f'Connecting to {port}...'})

    # ------------------------------------------------------------------
    # POST /uart/disconnect
    # ------------------------------------------------------------------

    @app.route('/uart/disconnect', methods=['POST'])
    @login_required
    def uart_disconnect():
        """Disconnect a UART printer."""
        data       = request.json
        printer_id = data.get('printer_id')
        if printer_id in uart_connections:
            uart_connections[printer_id].disconnect()
            del uart_connections[printer_id]
            if printer_id in printers:
                printers[printer_id]['online'] = False
            socketio.emit('printers', printers)
        return jsonify({'success': True})

    # ------------------------------------------------------------------
    # GET /uart/status/<printer_id>
    # ------------------------------------------------------------------

    @app.route('/uart/status/<printer_id>', methods=['GET'])
    @login_required
    def uart_get_status(printer_id):
        """Get current UART printer status."""
        if printer_id not in uart_connections:
            return jsonify({'success': False,
                            'message': 'Not connected',
                            'online': False})
        uart   = uart_connections[printer_id]
        status = uart.get_status()
        return jsonify({'success': True,
                        'online': uart.is_connected(),
                        'status': status})

    # ------------------------------------------------------------------
    # GET /uart/files/<printer_id>
    # ------------------------------------------------------------------

    @app.route('/uart/files/<printer_id>', methods=['GET'])
    @login_required
    def uart_get_files(printer_id):
        """
        List files for a UART printer.
        Reads from the Pi's USB gadget folder directly.
        Returns rich objects with thumbnail paths.
        """
        folder = USB_GADGET_FOLDER if USE_USB_GADGET else UPLOAD_FOLDER
        files  = []
        try:
            if os.path.exists(folder):
                for fname in sorted(os.listdir(folder)):
                    ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
                    if ext in {'ctb', 'goo', 'prz'}:
                        fpath = os.path.join(folder, fname)
                        stem  = fname.rsplit('.', 1)[0]
                        has_small = os.path.exists(
                            os.path.join(THUMBNAILS_FOLDER, f"{stem}_small.png"))
                        has_big = os.path.exists(
                            os.path.join(THUMBNAILS_FOLDER, f"{stem}_big.png"))
                        files.append({
                            'name':        fname,
                            'path':        '/usb/' + fname,
                            'size':        os.path.getsize(fpath),
                            'thumb_small': f"/thumbnails/{stem}_small.png"
                                           if has_small else None,
                            'thumb_big':   f"/thumbnails/{stem}_big.png"
                                           if has_big else None,
                        })
        except Exception as e:
            logger.error(f"UART file list error: {e}")
            return jsonify({'success': False, 'message': str(e), 'files': []})

        return jsonify({'success': True, 'files': files, 'folder': folder})

    # ------------------------------------------------------------------
    # POST /uart/command
    # ------------------------------------------------------------------

    @app.route('/uart/command', methods=['POST'])
    @login_required
    def uart_send_command():
        """Send control command to a UART printer."""
        data       = request.json
        printer_id = data.get('printer_id')
        action     = data.get('action')
        extra      = data.get('data', {})

        if not printer_id or printer_id not in uart_connections:
            return jsonify({'success': False,
                            'message': 'Printer not connected'}), 404

        uart = uart_connections[printer_id]

        if action == 'print':
            ok = uart.start_print(extra.get('filename', ''))
            return jsonify({'success': ok})
        elif action == 'pause':
            return jsonify({'success': uart.pause()})
        elif action == 'resume':
            return jsonify({'success': uart.resume()})
        elif action == 'cancel':
            return jsonify({'success': uart.cancel()})
        elif action == 'home_z':
            return jsonify({'success': uart.home_z()})
        elif action == 'move_z':
            mm = float(extra.get('mm', 0))
            return jsonify({'success': uart.move_z(mm)})
        elif action == 'raw':
            resp = uart.send_raw(extra.get('cmd', ''))
            return jsonify({'success': True, 'response': resp})
        else:
            return jsonify({'success': False,
                            'message': f'Unknown action: {action}'}), 400

    # ------------------------------------------------------------------
    # POST /printer/manual/uart
    # ------------------------------------------------------------------

    @app.route('/printer/manual/uart', methods=['POST'])
    @login_required
    def add_uart_printer():
        """Add a UART-connected printer to the registry."""
        data          = request.json
        name          = data.get('name', 'UART Printer')
        port          = data.get('port', '/dev/ttyS0')
        baudrate      = int(data.get('baudrate', 115200))
        model         = data.get('model', 'Elegoo Mars 2')
        brand         = data.get('brand', 'Elegoo')
        printer_image = data.get('image', '')

        printer_id = hashlib.md5(f"uart:{port}".encode()).hexdigest()

        settings = load_settings()
        if printer_id in settings.get('printers', {}):
            return jsonify({'success': False,
                            'message': 'UART printer on this port already exists'}), 400

        printer = {
            'connection':      printer_id,
            'name':            name,
            'model':           model,
            'brand':           brand,
            'ip':              '',
            'protocol':        'UART',
            'firmware':        'Unknown',
            'connection_type': 'uart',
            'uart_port':       port,
            'uart_baudrate':   baudrate,
            'usb_device_type': 'virtual',
            'online':          False
        }
        if printer_image:
            printer['image'] = printer_image

        printers[printer_id] = printer

        settings['printers'][printer_id] = {
            'name':            name,
            'model':           model,
            'brand':           brand,
            'ip':              '',
            'connection_type': 'uart',
            'uart_port':       port,
            'uart_baudrate':   baudrate,
            'usb_device_type': 'virtual',
            'enabled':         True,
            'manual':          True
        }
        if printer_image:
            settings['printers'][printer_id]['image'] = printer_image
        save_settings(settings)

        socketio.emit('printers', printers)

        threading.Thread(target=_connect_uart,
                         args=(printer_id, port, baudrate),
                         daemon=True).start()

        return jsonify({'success': True,
                        'printer': printer,
                        'printer_id': printer_id})

    # ------------------------------------------------------------------
    # POST /thumbnail/re-extract/<printer_id>/<filename>
    # ------------------------------------------------------------------

    @app.route('/thumbnail/re-extract/<printer_id>/<path:filename>',
               methods=['POST'])
    @login_required
    def re_extract_thumbnail(printer_id, filename):
        """Re-extract thumbnail for an existing file on disk."""
        safe_name = secure_filename(filename)
        filepath  = None
        for candidate in [os.path.join(USB_GADGET_FOLDER, safe_name),
                          os.path.join(UPLOAD_FOLDER, safe_name)]:
            if os.path.exists(candidate):
                filepath = Path(candidate)
                break

        if not filepath:
            return jsonify({'success': False,
                            'message': f'File not found: {safe_name}'}), 404

        success, small_thumb, big_thumb = extract_thumbnail_for_file(
            filepath, output_to_thumbnails=True)
        if success:
            file_db.add_file(safe_name, small_thumb, big_thumb)
            return jsonify({
                'success':         True,
                'thumbnail_small': small_thumb,
                'thumbnail_big':   big_thumb,
                'message':         'Thumbnail extracted successfully'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Could not extract thumbnail '
                           '(unsupported format or corrupt file)'
            }), 422

    # ------------------------------------------------------------------
    # GET /identity  (slave discovery)
    # ------------------------------------------------------------------

    @app.route('/identity', methods=['GET'])
    def get_identity():
        """Identity endpoint for slave discovery."""
        settings      = load_settings()
        primary_type  = 'sdcp'
        primary_model = 'Unknown'
        for _, pcfg in settings.get('printers', {}).items():
            primary_type  = pcfg.get('connection_type', 'sdcp')
            primary_model = pcfg.get('model', 'Unknown')
            break

        return jsonify({
            'app':           'chitui',
            'version':       '2.1',
            'printer_type':  primary_type,
            'printer_model': primary_model,
            'slave_capable': True,
            'uart_support':  UART_SUPPORT
        })
