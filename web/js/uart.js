/**
 * uart.js – ChitUI UART Printer & Raspicam Frontend Support
 *
 * Handles:
 *  - Detecting UART printers when selected
 *  - Showing UART controls panel (Home Z, Move Z) next to printer image
 *  - Disabling Local storage tab for UART printers (USB-only)
 *  - Receiving uart_status SocketIO events → updating sidebar + controls
 *  - Raspicam tab in Camera Stream card (when enabled in settings)
 */

// ─── State ────────────────────────────────────────────────────────────────────
var currentUartPrinterId = null;
var raspicamActive       = false;

// ─── SocketIO: uart_status event ─────────────────────────────────────────────
function registerUartSocketEvents(socket) {
    socket.on('uart_status', function (data) {
        var pid    = data.printer_id;
        var online = data.online;
        var status = data.status || {};

        // Sync into shared printers dict
        if (typeof printers !== 'undefined' && printers[pid]) {
            printers[pid].online      = online;
            printers[pid].uart_status = status;
        }

        // Update sidebar badge
        var $badge = $('#printer_' + pid + ' .printerStatusBadge');
        var $text  = $('#printer_' + pid + ' .printerStatus');
        if (online) {
            $badge.removeClass('status-offline').addClass('status-online');
            $text.text(status.status_text || 'Online');
        } else {
            $badge.removeClass('status-online').addClass('status-offline');
            $text.text('Offline');
        }

        // If currently selected, refresh controls
        if (typeof currentPrinter !== 'undefined' && currentPrinter === pid) {
            _updateUartControlsState(online, status);
        }

        // ── Drive the print status modal for UART printers ────────────────────
        // Translate UART machine_status (0=Idle,1=Printing,2=Paused,3=Stopped)
        // into the PrintInfo shape that updatePrintOverlay() expects.
        if (typeof currentPrinter !== 'undefined' && currentPrinter === pid &&
            typeof updatePrintOverlay === 'function' && online) {

            var machineStatus = status.machine_status;

            if (machineStatus === 1 || machineStatus === 2) {
                // Map UART machine_status → SDCP PrintInfo.Status
                // 1=Printing → 3 (SDCP_PRINT_STATUS_EXPOSURING)
                // 2=Paused   → 6 (SDCP_PRINT_STATUS_PAUSED)
                var printStatus = (machineStatus === 2) ? 6 : 3;

                var printInfo = {
                    Status:       printStatus,
                    CurrentLayer: status.CurrentLayer || 0,
                    TotalLayer:   status.TotalLayer   || 0,
                    Filename:     status.Filename     || (printers[pid] && printers[pid]._uartFilename) || '',
                    CurrentTicks: 0,
                    TotalTicks:   0,
                    ErrorNumber:  0
                };
                updatePrintOverlay(pid, printInfo);

            } else {
                // Idle or stopped from the printer's perspective.
                // Only clean up the overlay if we also have no locally-tracked
                // filename — if _uartFilename is still set the Mars 2 is just
                // silent (it stops responding during homing/printing) and we
                // must not tear down the print card prematurely.
                var hasTrackedPrint = printers[pid] && printers[pid]._uartFilename;
                if (!hasTrackedPrint) {
                    var idlePrintInfo = {
                        Status:       9,  // SDCP_PRINT_STATUS_COMPLETE
                        CurrentLayer: 0,
                        TotalLayer:   0,
                        Filename:     '',
                        CurrentTicks: 0,
                        TotalTicks:   0,
                        ErrorNumber:  0
                    };
                    updatePrintOverlay(pid, idlePrintInfo);
                }
                // If hasTrackedPrint: we keep showing the last known printing
                // state. The backend will clear _printing_file (and stop
                // synthesising) only when the print actually finishes or is
                // cancelled, at which point _uartFilename will also be cleared.
            }
        }
        // ─────────────────────────────────────────────────────────────────────
    });

    // Intercept action_print for UART printers:
    // Store the filename so the uart_status handler can reference it before
    // the first poll arrives. The modal is opened by the backend's immediate
    // uart_status emit (fired right after start_print succeeds in main.py),
    // NOT here — calling updatePrintOverlay() synchronously inside socket.emit
    // caused Bootstrap to try opening the print modal while the confirm modal
    // was still in its hide animation, which silently blocked the print command.
    var _origEmit = socket.emit.bind(socket);
    socket.emit = function (event, data) {
        if (event === 'action_print' && data && data.id && data.data) {
            var pid = data.id;
            if (typeof printers !== 'undefined' && printers[pid] &&
                printers[pid].connection_type === 'uart') {
                var filename = data.data.replace(/^\/?(usb\/)?/, '');
                printers[pid]._uartFilename = filename;
                // Reset tracking so modalShownOnce is clear for this new print
                delete printers[pid].printTracking;
            }
        }
        // When the user stops/cancels a UART print, clear the tracked filename
        // so the idle-guard in the uart_status handler releases and the overlay
        // correctly tears down on the next idle poll.
        if ((event === 'action_stop' || event === 'action_cancel') && data && data.id) {
            var pid = data.id;
            if (typeof printers !== 'undefined' && printers[pid] &&
                printers[pid].connection_type === 'uart') {
                printers[pid]._uartFilename = null;
            }
        }
        return _origEmit.apply(this, arguments);
    };

    // Show a toast/alert on printer errors (previously silent for UART)
    socket.on('printer_error', function (data) {
        var msg = (data && data.msg) ? data.msg : 'Printer error';
        if (typeof showToast === 'function') {
            showToast(msg, 'danger');
        } else {
            console.error('[printer_error]', msg);
        }
    });
}

// ─── Called by showPrinter() hook in chitui.js ───────────────────────────────
function onShowPrinter(printerId, printer) {
    var isUart = printer.connection_type === 'uart';
    currentUartPrinterId = isUart ? printerId : null;

    if (isUart) {
        _showUartControls(printerId, printer);
        _disableLocalStorageTab();
        _updateUartControlsState(printer.online, printer.uart_status || {});
    } else {
        _hideUartControls();
        _enableLocalStorageTab();
    }
}

// ─── UART Controls Panel ──────────────────────────────────────────────────────
function _showUartControls(printerId, printer) {
    // Inject panel once
    if ($('#uartControlsPanel').length === 0) {
        var html = [
            '<div id="uartControlsPanel" class="uart-controls-panel ms-3 d-flex flex-column gap-2 align-self-center">',
            '  <div class="text-muted small mb-1"><i class="bi bi-usb-symbol me-1"></i><strong>UART Controls</strong></div>',
            '  <button class="btn btn-sm btn-outline-primary btn-icon" id="uartBtnHomeZ" title="Home Z axis">',
            '    <i class="bi bi-house-fill"></i> Home Z',
            '  </button>',
            '  <div class="input-group input-group-sm">',
            '    <input type="number" id="uartZMoveMm" class="form-control" placeholder="mm" step="1" value="10" style="max-width:68px;">',
            '    <button class="btn btn-outline-secondary" id="uartBtnMoveZUp" title="Move Z up"><i class="bi bi-arrow-up"></i></button>',
            '    <button class="btn btn-outline-secondary" id="uartBtnMoveZDown" title="Move Z down"><i class="bi bi-arrow-down"></i></button>',
            '  </div>',
            '  <div id="uartKeepAlive" class="badge bg-secondary mt-1" style="font-size:0.7rem;">',
            '    <i class="bi bi-wifi"></i> Connecting…',
            '  </div>',
            '</div>'
        ].join('\n');

        $('.printer-preview-left').after(html);

        $('#uartBtnHomeZ').on('click', function () {
            _uartCommand(currentUartPrinterId, 'home_z');
        });
        $('#uartBtnMoveZUp').on('click', function () {
            var mm = parseFloat($('#uartZMoveMm').val()) || 10;
            _uartCommand(currentUartPrinterId, 'move_z', { mm: mm });
        });
        $('#uartBtnMoveZDown').on('click', function () {
            var mm = parseFloat($('#uartZMoveMm').val()) || 10;
            _uartCommand(currentUartPrinterId, 'move_z', { mm: -mm });
        });
    }
    $('#uartControlsPanel').removeClass('d-none');
}

function _hideUartControls() {
    $('#uartControlsPanel').addClass('d-none');
}

function _updateUartControlsState(online, status) {
    var $ka = $('#uartKeepAlive');
    if (online) {
        $ka.removeClass('bg-secondary bg-danger').addClass('bg-success');
        $ka.html('<i class="bi bi-wifi"></i> ' + (status.status_text || 'Online'));
    } else {
        $ka.removeClass('bg-success bg-secondary').addClass('bg-danger');
        $ka.html('<i class="bi bi-wifi-off"></i> Offline');
    }
    $('#uartBtnHomeZ, #uartBtnMoveZUp, #uartBtnMoveZDown').prop('disabled', !online);
}

// ─── Local storage tab ────────────────────────────────────────────────────────
function _disableLocalStorageTab() {
    // Hide the "Local" tab – UART printers have no local/internal storage
    var $tab = $('[data-bs-target="#pane-Local"], #tab-Local').closest('li');
    $tab.addClass('d-none');
}

function _enableLocalStorageTab() {
    var $tab = $('[data-bs-target="#pane-Local"], #tab-Local').closest('li');
    $tab.removeClass('d-none');
}

// ─── UART command helper ──────────────────────────────────────────────────────
function _uartCommand(printerId, action, extra) {
    if (!printerId) return;
    $.ajax({
        url:         '/uart/command',
        method:      'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            printer_id: printerId,
            action:     action,
            data:       extra || {}
        }),
        success: function (data) {
            if (!data.success && typeof showToast === 'function') {
                showToast('UART command failed', 'warning');
            }
        },
        error: function () {
            if (typeof showToast === 'function') showToast('UART command error', 'danger');
        }
    });
}

// ─── Raspicam ─────────────────────────────────────────────────────────────────
function initRaspicam() {
    if ($('#tab-raspicam').length > 0) return; // already added

    $.ajax({
        url:    '/raspicam/status',
        method: 'GET',
        success: function (data) {
            if (data.enabled) _addRaspicamTab();
        },
        error: function () { /* silently ignore if not logged in yet */ }
    });
}

function _addRaspicamTab() {
    if ($('#tab-raspicam').length > 0) return;

    $('#cameraTabs').append(
        '<li class="nav-item">' +
        '<button class="nav-link" id="tab-raspicam"' +
        ' data-bs-toggle="pill" data-bs-target="#tabpane-raspicam" type="button">' +
        '<i class="bi bi-camera-fill"></i> Pi Cam' +
        '</button></li>'
    );

    $('#cameraPanes').append(
        '<div class="tab-pane fade" id="tabpane-raspicam">' +
        '<div class="d-flex justify-content-end mb-2">' +
        '<div class="btn-group btn-group-sm">' +
        '<button class="btn btn-outline-success" id="btnStartRaspicam"><i class="bi bi-play-fill"></i></button>' +
        '<button class="btn btn-outline-danger" id="btnStopRaspicam" disabled><i class="bi bi-stop-fill"></i></button>' +
        '<button class="btn btn-outline-secondary" id="btnFullscreenRaspicam" disabled' +
        ' title="Fullscreen"><i class="bi bi-arrows-fullscreen"></i></button>' +
        '</div></div>' +
        '<div class="camera-container" id="raspicamContainer">' +
        '<div class="camera-placeholder" id="raspicamPlaceholder">' +
        '<i class="bi bi-camera-fill" style="font-size:3rem;color:var(--bs-secondary-color);"></i>' +
        '<p class="text-muted mt-3 mb-0">Click play to start Pi Camera</p>' +
        '</div>' +
        '<img id="raspicamStream" src="" alt="Pi Camera"' +
        ' style="display:none;width:100%;border-radius:8px;">' +
        '</div></div>'
    );

    $('#btnStartRaspicam').on('click', _startRaspicam);
    $('#btnStopRaspicam').on('click', _stopRaspicam);
    $('#btnFullscreenRaspicam').on('click', _fullscreenRaspicam);
}

/**
 * Open the Pi Camera stream in the shared fullscreen modal.
 *
 * #modalCameraFullscreen is the same modal the printer camera uses. It used to
 * hardcode /camera/video and a fixed 640x480 <img>, so it is set up per-source
 * here (and in chitui.js for the printer camera) instead.
 */
function _fullscreenRaspicam() {
    var el = document.getElementById('modalCameraFullscreen');
    if (!el || typeof bootstrap === 'undefined') return;

    $('#modalCameraFullscreen .modal-title')
        .html('<i class="bi bi-camera-fill me-2"></i>Pi Camera');

    // Scale to the viewport rather than the printer camera's fixed 640x480 -
    // the Pi camera streams 1280x720 by default.
    $('#cameraStreamFullscreen')
        .attr('style', 'max-width:100%;max-height:80vh;width:auto;height:auto;' +
                       'display:block;margin:0 auto;')
        .attr('src', '/raspicam/video?' + Date.now());

    bootstrap.Modal.getOrCreateInstance(el).show();
}

function _startRaspicam() {
    $.ajax({
        url:    '/raspicam/start',
        method: 'POST',
        success: function (data) {
            if (data.success) {
                raspicamActive = true;
                $('#raspicamStream').attr('src', '/raspicam/video?' + Date.now()).show();
                $('#raspicamPlaceholder').hide();
                $('#btnStartRaspicam').prop('disabled', true);
                $('#btnStopRaspicam').prop('disabled', false);
                $('#btnFullscreenRaspicam').prop('disabled', false);
            } else {
                if (typeof showToast === 'function')
                    showToast(data.message || 'Failed to start Pi Camera', 'danger');
            }
        },
        error: function () {
            if (typeof showToast === 'function')
                showToast('Failed to start Pi Camera', 'danger');
        }
    });
}

function _stopRaspicam() {
    $.ajax({
        url:    '/raspicam/stop',
        method: 'POST',
        success: function () {
            raspicamActive = false;
            $('#raspicamStream').attr('src', '').hide();
            $('#raspicamPlaceholder').show();
            $('#btnStartRaspicam').prop('disabled', false);
            $('#btnStopRaspicam').prop('disabled', true);
            $('#btnFullscreenRaspicam').prop('disabled', true);
            // Close the fullscreen view too, otherwise it keeps a viewer
            // attached to a stream the user just stopped.
            var _fsEl = document.getElementById('modalCameraFullscreen');
            if (_fsEl && $(_fsEl).hasClass('show') && typeof bootstrap !== 'undefined') {
                bootstrap.Modal.getOrCreateInstance(_fsEl).hide();
            }
        }
    });
}

// ─── Init ─────────────────────────────────────────────────────────────────────
$(document).ready(function () {
    initRaspicam();
});
