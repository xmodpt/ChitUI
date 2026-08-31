// ─────────────────────────────────────────────────────────────────────────────
// Settings Management
// ─────────────────────────────────────────────────────────────────────────────
let currentSettings = { printers: {}, auto_discover: false };

$(document).ready(function() {

    // ── Core settings actions ─────────────────────────────────────────
    $('#btnDiscover').click(discoverPrinters);
    $('#btnSaveSettings').click(saveSettings);

    // ── Edit modal ────────────────────────────────────────────────────
    $('#btnEditPrinterImage').click(function() { openPrinterImageSelector('edit'); });
    $('#btnSaveEdit').click(saveEditPrinter);

    // ── Printer images ────────────────────────────────────────────────
    loadPrinterImages();

    // ── Packages ──────────────────────────────────────────────────────
    $('#btnRefreshPackages').click(loadPythonPackages);
    $('button[data-bs-target="#packages-pane"]').on('click', loadPythonPackages);

    // ── Maintenance ───────────────────────────────────────────────────
    $('#btnRestartApp').click(restartApplication);
    $('#btnRebootPi').click(rebootSystem);

    // ── Raspicam ──────────────────────────────────────────────────────
    _initRaspicamToggle();

    // ── Auto-discover ─────────────────────────────────────────────────
    $('#autoDiscoverCheck').change(function() {
        currentSettings.auto_discover = $(this).is(':checked');
    });

    // ── Auto-login ────────────────────────────────────────────────────
    $('#autoLoginCheck').change(function() {
        const isEnabled = $(this).is(':checked');
        if (isEnabled) {
            localStorage.setItem('chitui_auto_login', 'true');
            showToast('Auto-login enabled for this device', 'success');
        } else {
            localStorage.removeItem('chitui_auto_login');
            localStorage.removeItem('chitui_auto_password');
            showToast('Auto-login disabled', 'info');
        }
    });

    // ── Session timeout ───────────────────────────────────────────────
    $('#sessionTimeout').change(function() {
        const timeout = parseInt($(this).val());
        $.ajax({
            url: '/auth/session-timeout', method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ timeout }),
            success: function(r) {
                if (r.success) {
                    const label = timeout === 0 ? 'Never'
                        : timeout < 3600 ? `${timeout/60} minutes`
                        : `${timeout/3600} hour${timeout/3600>1?'s':''}`;
                    showToast(`Session timeout set to: ${label}`, 'success');
                }
            },
            error: function(xhr) {
                showToast((xhr.responseJSON||{}).message || 'Failed to update session timeout', 'danger');
            }
        });
    });

    // ── Logout ────────────────────────────────────────────────────────
    $('#btnLogoutSettings').click(function() {
        if (!confirm('Are you sure you want to logout?')) return;
        localStorage.removeItem('chitui_auto_login');
        localStorage.removeItem('chitui_auto_password');
        $.ajax({
            url: '/auth/logout', method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            success: function(d) { if (d.success) window.location.href = '/'; },
            error: function() { window.location.href = '/'; }
        });
    });

    // ── Settings modal open ───────────────────────────────────────────
    $('#modalSettings').on('show.bs.modal', function() {
        loadSettings();
        $('#autoLoginCheck').prop('checked', localStorage.getItem('chitui_auto_login') === 'true');
        $.ajax({
            url: '/auth/session-timeout', method: 'GET',
            success: function(r) { $('#sessionTimeout').val(r.timeout || 0); }
        });
    });

    // ── Wizard init ───────────────────────────────────────────────────
    initWizard();
});

// ─────────────────────────────────────────────────────────────────────────────
// Load / Save settings
// ─────────────────────────────────────────────────────────────────────────────
function loadSettings() {
    $.ajax({
        url: '/settings', method: 'GET',
        success: function(data) { currentSettings = data; updateSettingsUI(); },
        error: function(xhr, s, e) { console.error('Error loading settings:', s, e); showToast('Error loading settings', 'danger'); }
    });
}

function updateSettingsUI() {
    $('#autoDiscoverCheck').prop('checked', currentSettings.auto_discover || false);
    const $list = $('#savedPrintersList').empty();
    const count = Object.keys(currentSettings.printers || {}).length;
    if (count === 0) {
        $list.html('<div class="list-group-item text-muted text-center"><i class="bi bi-info-circle"></i> No printers configured yet</div>');
        return;
    }
    $.each(currentSettings.printers, function(printerId, printer) {
        const $item = $($('#tmplSavedPrinter').html()).attr('data-printer-id', printerId);
        $item.find('.printer-name').text(printer.name);
        const isUart = printer.connection_type === 'uart';
        $item.find('.printer-ip').text(isUart
            ? `UART · ${printer.uart_port||'/dev/ttyS0'} · ${printer.uart_baudrate||115200} baud`
            : (printer.ip || ''));
        $item.find('.printer-enabled-toggle').prop('checked', printer.enabled !== false)
            .change(function() { currentSettings.printers[printerId].enabled = $(this).is(':checked'); });
        if (currentSettings.default_printer === printerId)
            $item.find('.btn-set-default').addClass('is-default').attr('title', 'Default Printer');
        $item.find('.btn-set-default').click(function() { setDefaultPrinter(printerId, printer.name); });
        $item.find('.btn-edit-printer').click(function() { editPrinter(printerId, printer); });
        $item.find('.btn-remove-printer').click(function() {
            if (confirm(`Remove printer "${printer.name}"?`)) removePrinter(printerId);
        });
        $list.append($item);
    });
}

function saveSettings() {
    $.ajax({
        url: '/settings', method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(currentSettings),
        success: function(data) {
            if (data.success) {
                showToast('Settings saved successfully', 'success');
                $('#modalSettings').modal('hide');
                if (typeof socket !== 'undefined' && socket) socket.emit('printers', {});
                else setTimeout(() => window.location.reload(), 1000);
            }
        },
        error: function() { showToast('Failed to save settings', 'danger'); }
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Discover
// ─────────────────────────────────────────────────────────────────────────────
function discoverPrinters() {
    const $btn = $('#btnDiscover'), $spin = $('#discoverSpinner');
    $btn.prop('disabled', true); $spin.removeClass('d-none');
    $.ajax({
        url: '/discover', method: 'POST', timeout: 5000,
        success: function(data) {
            if (data.success) {
                showToast(`Discovered ${data.count || Object.keys(data.printers||{}).length} printer(s)`, 'success');
                setTimeout(loadSettings, 1000);
            } else showToast(data.message || 'No printers discovered', 'warning');
        },
        error: function(xhr) { showToast(xhr.responseJSON?.message || 'No printers discovered', 'warning'); },
        complete: function() { $btn.prop('disabled', false); $spin.addClass('d-none'); }
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Remove printer
// ─────────────────────────────────────────────────────────────────────────────
function removePrinter(printerId) {
    $.ajax({
        url: `/printer/${printerId}`, method: 'DELETE',
        success: function(data) {
            if (data.success) {
                showToast('Printer removed', 'success');
                delete currentSettings.printers[printerId];
                updateSettingsUI();
            }
        },
        error: function() { showToast('Failed to remove printer', 'danger'); }
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Edit printer modal
// ─────────────────────────────────────────────────────────────────────────────
function editPrinter(printerId, printer) {
    const isUart = printer.connection_type === 'uart';
    $('#editPrinterId').val(printerId);
    $('#editPrinterType').val(isUart ? 'uart' : 'ip');
    $('#editPrinterName').val(printer.name);
    $('#editPrinterImage').val(printer.image || '');
    $('#editSelectedImageText').text(
        printer.image ? printer.image.replace(/\.(webp|png|jpg)$/i,'').replace(/_/g,' ') : 'Select Printer Image (Optional)'
    );
    if (isUart) {
        $('#editIPFields').addClass('d-none');
        $('#editUARTFields').removeClass('d-none');
        const port = printer.uart_port || '/dev/ttyS0';
        const $sel = $('#editUartPort');
        if (!$sel.find(`option[value="${port}"]`).length) $sel.append(`<option value="${port}">${port}</option>`);
        $sel.val(port);
        $('#editUartBaudrate').val(printer.uart_baudrate || 115200);
        $('#editUartBrand').val(printer.brand || '');
        $('#editUartModel').val(printer.model || '');
    } else {
        $('#editIPFields').removeClass('d-none');
        $('#editUARTFields').addClass('d-none');
        $('#editPrinterIP').val(printer.ip || '');
        $('#editUSBDeviceType').val(printer.usb_device_type || 'physical');
    }
    new bootstrap.Modal($('#modalEditPrinter')[0]).show();
}

function saveEditPrinter() {
    const printerId = $('#editPrinterId').val();
    const printerType = $('#editPrinterType').val();
    const name = $('#editPrinterName').val().trim();
    const image = $('#editPrinterImage').val().trim();
    if (!name) { showToast('Please enter a printer name', 'warning'); return; }
    const $btn = $('#btnSaveEdit').prop('disabled', true);

    const done = (ok, msg) => {
        $btn.prop('disabled', false);
        if (ok) {
            showToast(`Printer "${name}" updated`, 'success');
            bootstrap.Modal.getInstance($('#modalEditPrinter')[0]).hide();
            loadSettings();
        } else showToast(msg || 'Failed to update printer', 'danger');
    };

    if (printerType === 'uart') {
        const payload = { name, port: $('#editUartPort').val(), baudrate: parseInt($('#editUartBaudrate').val())||115200,
            brand: $('#editUartBrand').val().trim(), model: $('#editUartModel').val().trim() };
        if (image) payload.image = image;
        $.ajax({ url: `/printer/${printerId}/uart`, method: 'PUT', contentType: 'application/json',
            data: JSON.stringify(payload),
            success: d => done(d.success, d.message),
            error: xhr => done(false, xhr.responseJSON?.message) });
    } else {
        const ip = $('#editPrinterIP').val().trim();
        if (!ip || !/^(\d{1,3}\.){3}\d{1,3}$/.test(ip)) { showToast('Please enter a valid IP address', 'warning'); $btn.prop('disabled', false); return; }
        const payload = { name, ip, usb_device_type: $('#editUSBDeviceType').val() || 'physical' };
        if (image) payload.image = image;
        $.ajax({ url: `/printer/${printerId}`, method: 'PUT', contentType: 'application/json',
            data: JSON.stringify(payload),
            success: d => done(d.success, d.message),
            error: xhr => done(false, xhr.responseJSON?.message) });
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Set default printer
// ─────────────────────────────────────────────────────────────────────────────
function setDefaultPrinter(printerId, printerName) {
    $.ajax({
        url: '/printer/default', method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ printer_id: printerId }),
        success: function(data) {
            if (data.success) {
                showToast(`"${printerName}" set as default printer`, 'success');
                currentSettings.default_printer = printerId;
                updateSettingsUI();
            } else showToast(data.message || 'Failed to set default printer', 'danger');
        },
        error: function(xhr) { showToast(xhr.responseJSON?.message || 'Failed to set default printer', 'danger'); }
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Add Printer Wizard
// ─────────────────────────────────────────────────────────────────────────────
function initWizard() {
    const STEPS = 4;
    let currentStep = 1;
    let printerType = null; // 'ip' | 'uart'

    const stepTitles = ['Welcome', 'Connection Type', 'Configuration', 'Adding Printer'];
    const $modal     = $('#modalAddPrinterWizard');
    const $progress  = $('#wizardProgress');
    const $stepLabel = $('#wizardStepLabel');
    const $stepTitle = $('#wizardStepTitle');
    const $btnBack   = $('#wzBtnBack');
    const $btnNext   = $('#wzBtnNext');
    const $footer    = $('#wzFooter');

    function showStep(n) {
        currentStep = n;
        // Update progress
        $progress.css('width', `${(n / STEPS) * 100}%`);
        $stepLabel.text(`Step ${n} of ${STEPS}`);
        $stepTitle.text(stepTitles[n - 1]);

        // Hide all steps
        ['#wzStep1','#wzStep2','#wzStep3IP','#wzStep3UART','#wzStep4'].forEach(s => $(s).addClass('d-none'));

        // Show current step
        if (n === 1) { $('#wzStep1').removeClass('d-none'); }
        else if (n === 2) { $('#wzStep2').removeClass('d-none'); }
        else if (n === 3) {
            if (printerType === 'ip') $('#wzStep3IP').removeClass('d-none');
            else { $('#wzStep3UART').removeClass('d-none'); loadWizardUartPorts(); }
        }
        else if (n === 4) { $('#wzStep4').removeClass('d-none'); }

        // Footer buttons
        $footer.removeClass('d-none');
        $btnBack.toggleClass('d-none', n === 1);
        if (n === 4) {
            $btnNext.addClass('d-none');
            $btnBack.addClass('d-none');
        } else if (n === 3) {
            $btnNext.html('Add Printer <i class="bi bi-check-lg ms-1"></i>').removeClass('d-none');
        } else {
            $btnNext.html('Next <i class="bi bi-arrow-right ms-1"></i>').removeClass('d-none');
        }
    }

    function resetWizard() {
        printerType = null;
        // Clear IP fields
        $('#wzIP').val('').removeClass('is-invalid');
        $('#wzIPName').val('');
        $('#wzUSBDeviceType').val('physical');
        $('#wzIPImage').val('');
        $('#wzIPImageText').text('Select image…');
        // Clear UART fields
        $('#wzUARTName').val('');
        $('#wzUARTPort').val('/dev/ttyS0');
        $('#wzUARTBaudrate').val('115200');
        $('#wzUARTBrand').val('');
        $('#wzUARTModel').val('');
        $('#wzUARTImage').val('');
        $('#wzUARTImageText').text('Select image…');
        // Clear step 4 states
        $('#wzAdding').removeClass('d-none');
        $('#wzSuccess, #wzError').addClass('d-none');
        showStep(1);
    }

    // Open wizard → reset
    $modal.on('show.bs.modal', function() { resetWizard(); });

    // Type selection cards on step 2 act as Next
    $('#wzChooseIP').click(function() { printerType = 'ip'; showStep(3); });
    $('#wzChooseUART').click(function() { printerType = 'uart'; showStep(3); });

    // Image selectors
    $('#btnWzSelectIPImage').click(function() { openPrinterImageSelector('wzIP'); });
    $('#btnWzSelectUARTImage').click(function() { openPrinterImageSelector('wzUART'); });

    // Back button
    $btnBack.click(function() {
        if (currentStep === 3) { printerType = null; showStep(2); }
        else if (currentStep > 1) showStep(currentStep - 1);
    });

    // Next button
    $btnNext.click(function() {
        if (currentStep === 1) { showStep(2); }
        else if (currentStep === 2) {
            // Should not happen since type cards advance directly, but just in case
            if (!printerType) { showToast('Please select a connection type', 'warning'); return; }
            showStep(3);
        }
        else if (currentStep === 3) {
            if (!validateStep3()) return;
            showStep(4);
            submitPrinter();
        }
    });

    function validateStep3() {
        if (printerType === 'ip') {
            const ip = $('#wzIP').val().trim();
            const ipOk = /^(\d{1,3}\.){3}\d{1,3}$/.test(ip) &&
                ip.split('.').every(o => +o >= 0 && +o <= 255);
            $('#wzIP').toggleClass('is-invalid', !ipOk);
            if (!ipOk) { showToast('Please enter a valid IP address', 'warning'); return false; }
        } else {
            const name = $('#wzUARTName').val().trim();
            if (!name) { showToast('Please enter a printer name', 'warning'); $('#wzUARTName').addClass('is-invalid'); return false; }
            $('#wzUARTName').removeClass('is-invalid');
        }
        return true;
    }

    function submitPrinter() {
        $('#wzAdding').removeClass('d-none');
        $('#wzSuccess, #wzError').addClass('d-none');

        const onSuccess = (name) => {
            $('#wzAdding').addClass('d-none');
            $('#wzSuccess').removeClass('d-none');
            $('#wzSuccessMsg').text(`"${name}" has been added and is now connecting.`);
            loadSettings();
            setTimeout(() => {
                bootstrap.Modal.getInstance($modal[0]).hide();
            }, 2000);
        };

        const onError = (msg) => {
            $('#wzAdding').addClass('d-none');
            $('#wzError').removeClass('d-none');
            $('#wzErrorMsg').text(msg || 'Could not add printer. Please try again.');
            // Show back button so user can go back and fix
            $btnBack.removeClass('d-none').off('click.wzerr').on('click.wzerr', function() {
                $btnBack.off('click.wzerr').click(function() {
                    if (currentStep === 3) { printerType = null; showStep(2); }
                    else if (currentStep > 1) showStep(currentStep - 1);
                });
                showStep(3);
            });
        };

        if (printerType === 'ip') {
            const ip   = $('#wzIP').val().trim();
            const name = $('#wzIPName').val().trim() || `Printer-${ip}`;
            const payload = { ip, name, usb_device_type: $('#wzUSBDeviceType').val() };
            const img = $('#wzIPImage').val().trim();
            if (img) payload.image = img;

            $.ajax({
                url: '/printer/manual', method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify(payload), timeout: 8000,
                success: d => d.success ? onSuccess(name) : onError(d.message),
                error: xhr => onError(xhr.responseJSON?.message || 'Failed to add printer')
            });
        } else {
            const name = $('#wzUARTName').val().trim();
            const payload = {
                name, port: $('#wzUARTPort').val(),
                baudrate: parseInt($('#wzUARTBaudrate').val()) || 115200,
                brand: $('#wzUARTBrand').val().trim() || 'Unknown',
                model: $('#wzUARTModel').val().trim() || 'Unknown'
            };
            const img = $('#wzUARTImage').val().trim();
            if (img) payload.image = img;

            $.ajax({
                url: '/printer/manual/uart', method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify(payload), timeout: 8000,
                success: d => d.success ? onSuccess(name) : onError(d.message),
                error: xhr => onError(xhr.responseJSON?.message || 'Failed to add UART printer')
            });
        }
    }

    function loadWizardUartPorts() {
        $.ajax({
            url: '/uart/ports', method: 'GET',
            success: function(data) {
                if (data.success && data.ports && data.ports.length) {
                    const $sel = $('#wzUARTPort').empty();
                    data.ports.forEach(p => $sel.append(`<option value="${p.device}">${p.description}</option>`));
                }
            }
        });
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Printer images (shared by wizard + edit modal)
// ─────────────────────────────────────────────────────────────────────────────
let availablePrinterImages = [];

function loadPrinterImages() {
    $.ajax({
        url: '/printer/images', method: 'GET',
        success: function(data) {
            if (data.success) { availablePrinterImages = data.images; populatePrinterImageGrid(); }
        }
    });
}

function populatePrinterImageGrid() {
    const $grid = $('#printerImageGrid');
    $grid.find('.col-6:not(:first)').remove();
    availablePrinterImages.forEach(function(imagePath) {
        const name = imagePath.replace(/\.(webp|png|jpg)$/i,'').replace(/_/g,' ');
        $grid.append(`
            <div class="col-6 col-md-4">
                <div class="printer-image-option" data-image="${imagePath}">
                    <div class="printer-image-preview"><img src="img/${imagePath}" alt="${name}"></div>
                    <small class="d-block text-center mt-2">${name}</small>
                </div>
            </div>`);
    });
    $(document).on('click', '.printer-image-option', function() {
        selectPrinterImage($(this).data('image'), $('#modalPrinterImage').data('mode'));
    });
}

// mode: 'edit' | 'wzIP' | 'wzUART'
function openPrinterImageSelector(mode) {
    const $imgModal  = $('#modalPrinterImage');
    const $editModal = $('#modalEditPrinter');
    const editIsOpen = $editModal.hasClass('show');

    $imgModal.data('mode', mode);
    $imgModal.data('returnToEdit', editIsOpen);
    $imgModal.removeData('imageSelected');

    const cur = mode === 'edit'   ? $('#editPrinterImage').val()
              : mode === 'wzIP'   ? $('#wzIPImage').val()
              : $('#wzUARTImage').val();
    $('.printer-image-option').removeClass('selected');
    $(`.printer-image-option[data-image="${cur}"]`).addClass('selected');

    if (editIsOpen) {
        // Hide edit modal first, then open image modal on top cleanly
        bootstrap.Modal.getInstance($editModal[0]).hide();
        $editModal.one('hidden.bs.modal.imgpicker', function() {
            new bootstrap.Modal($imgModal[0]).show();
        });
    } else {
        new bootstrap.Modal($imgModal[0]).show();
    }

    // If user dismisses the image modal without picking, reopen edit modal
    $imgModal.one('hidden.bs.modal.imgpicker', function() {
        if ($imgModal.data('returnToEdit') && !$imgModal.data('imageSelected')) {
            new bootstrap.Modal($editModal[0]).show();
        }
        $imgModal.removeData('imageSelected');
    });
}

function selectPrinterImage(imagePath, mode) {
    const $imgModal  = $('#modalPrinterImage');
    const $editModal = $('#modalEditPrinter');
    const label = imagePath ? imagePath.replace(/\.(webp|png|jpg)$/i,'').replace(/_/g,' ') : '';

    if (mode === 'edit') {
        $('#editPrinterImage').val(imagePath);
        $('#editSelectedImageText').text(label || 'Select Printer Image (Optional)');
    } else if (mode === 'wzIP') {
        $('#wzIPImage').val(imagePath);
        $('#wzIPImageText').text(label || 'Select image…');
    } else if (mode === 'wzUART') {
        $('#wzUARTImage').val(imagePath);
        $('#wzUARTImageText').text(label || 'Select image…');
    }

    // Flag that a selection was made
    $imgModal.data('imageSelected', true);
    const returnToEdit = $imgModal.data('returnToEdit');
    bootstrap.Modal.getInstance($imgModal[0]).hide();

    // Reopen edit modal after image modal fully closes
    if (returnToEdit) {
        $imgModal.one('hidden.bs.modal.imgpicker', function() {
            new bootstrap.Modal($editModal[0]).show();
        });
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Toast
// ─────────────────────────────────────────────────────────────────────────────
function showToast(message, type = 'info') {
    const $toast = $('#toastUpload');
    const bg = type==='success'?'bg-success':type==='danger'?'bg-danger':type==='warning'?'bg-warning':'bg-info';
    $toast.find('.toast-header').removeClass('bg-success bg-danger bg-warning bg-info bg-body-secondary').addClass(bg);
    $toast.find('.toast-body').text(message);
    new bootstrap.Toast($toast[0]).show();
}

// ─────────────────────────────────────────────────────────────────────────────
// Python packages
// ─────────────────────────────────────────────────────────────────────────────
function loadPythonPackages() {
    const $c = $('#packagesListContainer'), $btn = $('#btnRefreshPackages');
    $c.html('<div class="text-center py-4"><div class="spinner-border text-secondary" role="status"></div><p class="text-muted mt-2">Loading…</p></div>');
    $btn.prop('disabled', true);
    $.ajax({
        url: '/python-packages', method: 'GET', timeout: 15000,
        success: function(data) {
            if (data.success && data.packages) renderPackagesList(data.packages, data.count);
            else $c.html('<div class="alert alert-warning"><i class="bi bi-exclamation-triangle"></i> Failed to load packages</div>');
        },
        error: function(xhr) {
            $c.html(`<div class="alert alert-danger"><i class="bi bi-x-circle"></i> Error: ${xhr.responseJSON?.error||'Failed to load Python packages'}</div>`);
        },
        complete: function() { $btn.prop('disabled', false); }
    });
}

function renderPackagesList(packages, count) {
    const $c = $('#packagesListContainer');
    if (!packages || !packages.length) { $c.html('<div class="alert alert-info"><i class="bi bi-info-circle"></i> No packages found</div>'); return; }
    let html = `<div class="mb-3"><span class="badge bg-secondary">${count} packages installed</span></div>
        <div class="table-responsive" style="max-height:450px;overflow-y:auto;">
        <table class="table table-sm table-hover"><thead class="sticky-top bg-body">
        <tr><th>Package Name</th><th>Version</th></tr></thead><tbody>`;
    packages.forEach(p => { html += `<tr><td><code>${escapeHtml(p.name)}</code></td><td><span class="text-muted">${escapeHtml(p.version)}</span></td></tr>`; });
    $c.html(html + '</tbody></table></div>');
}

function escapeHtml(t) {
    return t.replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
}

// ─────────────────────────────────────────────────────────────────────────────
// Maintenance
// ─────────────────────────────────────────────────────────────────────────────
function restartApplication() {
    if (!confirm('Are you sure you want to restart the application?')) return;
    $('#btnRestartApp').prop('disabled', true).html('<i class="bi bi-arrow-clockwise"></i> Restarting…');
    showToast('Restarting application…', 'info');
    $.ajax({ url: '/maintenance/restart', method: 'POST', timeout: 5000,
        complete: function() {
            showToast('Restarting… page will reload in 5 s', 'success');
            $('#modalSettings').modal('hide');
            setTimeout(() => window.location.reload(), 5000);
        }
    });
}

function rebootSystem() {
    if (!confirm('Are you sure you want to reboot the Raspberry Pi?')) return;
    $('#btnRebootPi').prop('disabled', true).html('<i class="bi bi-power"></i> Rebooting…');
    showToast('Rebooting system…', 'warning');
    $.ajax({ url: '/maintenance/reboot', method: 'POST', timeout: 5000,
        complete: function() {
            showToast('Reboot initiated. Wait 30–60 s before reconnecting.', 'warning');
            $('#modalSettings').modal('hide');
        }
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Raspicam
// ─────────────────────────────────────────────────────────────────────────────
function _initRaspicamToggle() {
    $.ajax({ url: '/raspicam/status', method: 'GET',
        success: d => {
            $('#raspicamEnabledCheck').prop('checked', d.enabled);
            // Report what the camera stack actually sees, so a dead ribbon
            // cable or a missing rpicam-apps install is obvious here instead
            // of showing up as a black <img> later.
            var $hint = $('#raspicamHint');
            if (!$hint.length) return;
            if (d.available) {
                $hint.html('<span class="text-success">' +
                    '<i class="bi bi-check-circle"></i> Camera detected' +
                    (d.binary ? ' via <code>' + d.binary + '</code>' : '') +
                    '</span>' + (d.cameras && d.cameras.length
                        ? '<br><small class="text-muted">' + d.cameras[0] + '</small>' : ''));
            } else if (!d.binary) {
                $hint.html('<span class="text-warning">' +
                    '<i class="bi bi-exclamation-triangle"></i> No camera program found. ' +
                    'Install with <code>sudo apt install -y rpicam-apps</code></span>');
            } else {
                $hint.html('<span class="text-warning">' +
                    '<i class="bi bi-exclamation-triangle"></i> No camera detected by <code>' +
                    d.binary + '</code>. Check the CSI ribbon cable.</span>' +
                    (d.detail ? '<br><small class="text-muted">' +
                        $('<div>').text(d.detail.split('\n')[0]).html() + '</small>' : ''));
            }
        },
        // Without this, a backend that does not implement /raspicam/* logs a
        // bare 404 to the console on every page load.
        error: () => $('#raspicamHint').html(
            '<span class="text-muted">Pi Camera support is not available on this server.</span>')
    });
    $('#raspicamEnabledCheck').off('change').on('change', function() {
        const enabled = $(this).is(':checked');
        $.ajax({ url: '/raspicam/settings', method: 'POST', contentType: 'application/json',
            data: JSON.stringify({ enabled }),
            success: d => {
                if (d.success) {
                    showToast(enabled ? 'Pi Camera enabled' : 'Pi Camera disabled', enabled ? 'success' : 'info');
                    if (typeof initRaspicam === 'function') initRaspicam();
                }
            }
        });
    });
}




// ── Plugin Store ──────────────────────────────────────────────────────────────

(function () {
  let _storePlugins = [];
  let _storeFilter  = 'all';
  let _storeSearch  = '';
  let _storeLoaded  = false;

  const ICON_MAP = {
    notify: 'bi-bell-fill', camera: 'bi-camera-video', leak: 'bi-droplet-fill',
    gpio: 'bi-lightning-charge', relay: 'bi-lightning-charge', terminal: 'bi-terminal',
    stats: 'bi-cpu', rpi: 'bi-cpu', raspberry: 'bi-cpu', usb: 'bi-usb-symbol',
    temp: 'bi-thermometer-half', power: 'bi-power', fan: 'bi-fan', light: 'bi-lightbulb',
  };

  function _guessIcon(p) {
    const key = ((p.name || '') + ' ' + (p.slug || '')).toLowerCase();
    for (const [k, v] of Object.entries(ICON_MAP)) {
      if (key.includes(k)) return v;
    }
    return 'bi-puzzle';
  }

  function _esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  // ── Open & load ────────────────────────────────────────────────────────────
  window.storeOpen = function () {
    const modal = new bootstrap.Modal(document.getElementById('modalPluginStore'));
    modal.show();
    if (!_storeLoaded) storeLoadCatalog();
  };

  window.storeLoadCatalog = function () {
    _storeLoaded = false;
    document.getElementById('storeSkeletonGrid').classList.remove('d-none');
    document.getElementById('storePluginGrid').classList.add('d-none');
    document.getElementById('storeErrorState').classList.add('d-none');
    document.getElementById('storeEmptyState').classList.add('d-none');
    document.getElementById('storeUpdateBanner').classList.add('d-none');
    document.getElementById('storeWarning').classList.add('d-none');

    fetch('/plugins/store/catalog')
      .then(r => { if (!r.ok) throw new Error(`Server ${r.status}`); return r.json(); })
      .then(data => {
        if (!data.success) throw new Error(data.error || 'Unknown error');
        _storePlugins = data.plugins || [];
        _storeLoaded  = true;

        document.getElementById('storeSkeletonGrid').classList.add('d-none');
        document.getElementById('storeLastChecked').textContent =
          'Last checked: ' + new Date().toLocaleTimeString() +
          (data.store_url ? '  \u2022  ' + data.store_url : '');

        // The backend reports success whenever it has anything to show, so a
        // failed catalog fetch arrives as success:true with error set and only
        // locally installed plugins in the list. Without this the dialog looked
        // perfectly healthy while the store was completely unreachable.
        if (data.error) {
          document.getElementById('storeWarningText').textContent =
            (data.catalog_count ? 'Showing a cached catalog. ' : 'No plugins could be loaded from the store. ')
            + data.error;
          document.getElementById('storeWarning').classList.remove('d-none');
        }

        const updates = _storePlugins.filter(p => p.has_update).length;
        const countBadge = document.getElementById('storeUpdateCount');
        if (updates > 0) {
          countBadge.textContent = updates;
          countBadge.style.display = '';
          document.getElementById('storeUpdateBannerText').textContent =
            `${updates} update${updates > 1 ? 's' : ''} available`;
          document.getElementById('storeUpdateBanner').classList.remove('d-none');
        } else {
          countBadge.style.display = 'none';
        }

        _storeRender();
      })
      .catch(err => {
        document.getElementById('storeSkeletonGrid').classList.add('d-none');
        document.getElementById('storeErrorState').classList.remove('d-none');
        document.getElementById('storeErrorMessage').textContent = err.message;
        console.error('Plugin store error:', err);
      });
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  function _storeRender() {
    let list = _storePlugins.filter(p => {
      if (_storeFilter === 'updates')   return p.has_update;
      if (_storeFilter === 'installed') return p.installed;
      return true;
    });
    if (_storeSearch) {
      const q = _storeSearch.toLowerCase();
      list = list.filter(p =>
        ((p.name || '') + ' ' + (p.short_description || '')).toLowerCase().includes(q)
      );
    }
    const grid  = document.getElementById('storePluginGrid');
    const empty = document.getElementById('storeEmptyState');
    if (!list.length) {
      grid.classList.add('d-none');
      empty.classList.remove('d-none');
      return;
    }
    empty.classList.add('d-none');
    grid.innerHTML = list.map(_storeCardHTML).join('');
    grid.classList.remove('d-none');
  }

  function _storeCardHTML(p) {
    const icon = _guessIcon(p);
    const ver  = p.version ? `v${p.version}` : '';
    const iver = p.installed_version ? `v${p.installed_version}` : '';

    let badge = '';
    if (p.has_update)     badge = `<span class="store-badge-update"><i class="bi bi-arrow-up-circle me-1"></i>Update</span>`;
    else if (p.installed) badge = `<span class="store-badge-installed"><i class="bi bi-check2 me-1"></i>Installed</span>`;

    // Use data-slug on button — NO inline onclick with URL data (breaks with & in URLs)
    let actionBtn = '';
    if (p.has_update) {
      actionBtn = `<button class="btn store-btn-update store-action-btn" data-slug="${_esc(p.slug)}">
        <i class="bi bi-arrow-up-circle me-1"></i>Update to ${ver}</button>`;
    } else if (p.installed) {
      actionBtn = `<button class="btn btn-sm btn-outline-secondary" disabled>
        <i class="bi bi-check2 me-1"></i>Installed</button>`;
    } else if (p.download_url) {
      actionBtn = `<button class="btn store-btn-install btn-sm store-action-btn" data-slug="${_esc(p.slug)}">
        <i class="bi bi-download me-1"></i>Install</button>`;
    } else {
      actionBtn = `<button class="btn btn-sm btn-outline-secondary" disabled>Not available</button>`;
    }

    const versionLine = p.has_update
      ? `<span class="store-version">${iver} → ${ver}</span>`
      : `<span class="store-version">${ver}</span>`;

    return `
      <div class="col-md-6 col-lg-4">
        <div class="store-plugin-card p-3 d-flex flex-column h-100">
          <div class="d-flex align-items-start gap-3 mb-2">
            <div class="store-plugin-icon"><i class="bi ${icon}"></i></div>
            <div class="flex-grow-1 min-w-0">
              <div class="d-flex align-items-center gap-2 flex-wrap">
                <span class="fw-semibold">${_esc(p.name)}</span>
                ${badge}
              </div>
              <div class="d-flex align-items-center gap-2 mt-1">${versionLine}</div>
            </div>
          </div>
          <p class="text-secondary small mb-3 flex-grow-1" style="line-height:1.5;">
            ${_esc(p.short_description) || '<em class="text-muted">No description available.</em>'}
          </p>
          <div class="d-flex align-items-center justify-content-between mt-auto gap-2">
            ${actionBtn}
            ${p.detail_url ? `<a href="${_esc(p.detail_url)}" target="_blank" class="btn btn-sm btn-link text-secondary p-0" title="View on chitui.net"><i class="bi bi-box-arrow-up-right"></i></a>` : ''}
          </div>
        </div>
      </div>`;
  }

  // ── Install / Update — event delegation on the grid ───────────────────────
  // This avoids passing URLs through inline onclick attributes (breaks with & chars)
  document.addEventListener('click', function (e) {
    const btn = e.target.closest('.store-action-btn');
    if (!btn) return;
    const slug = btn.dataset.slug;
    if (!slug) return;
    const plugin = _storePlugins.find(p => p.slug === slug);
    if (!plugin) return;

    if (!plugin.download_url) {
      showToast('No download URL available for this plugin.', 'warning');
      return;
    }

    _storeDoInstall(btn, plugin);
  });

  function _storeDoInstall(btn, plugin) {
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Installing…';

    // Open the progress modal
    const progressModal = bootstrap.Modal.getOrCreateInstance(
      document.getElementById('modalStoreInstall'), { backdrop: 'static', keyboard: false }
    );
    const terminal  = document.getElementById('storeInstallTerminal');
    const result    = document.getElementById('storeInstallResult');
    const closeBtn  = document.getElementById('btnStoreInstallClose');
    const titleSpan = document.getElementById('storeInstallPluginName');

    titleSpan.textContent = `Installing ${plugin.name}…`;
    terminal.innerHTML = '';
    result.className = 'd-none';
    result.innerHTML = '';
    closeBtn.disabled = true;
    progressModal.show();

    function _log(msg, level) {
      const line = document.createElement('div');
      line.style.color = level === 'error' ? '#f38ba8' : level === 'success' ? '#a6e3a1' : '#cdd6f4';
      line.textContent = msg;
      terminal.appendChild(line);
      terminal.scrollTop = terminal.scrollHeight;
    }

    function _finish(success, message) {
      closeBtn.disabled = false;
      closeBtn.onclick = function () {
        progressModal.hide();
        // Re-enable install btn if failed; leave disabled (Installed) if success
        if (!success) {
          btn.disabled = false;
          btn.innerHTML = orig;
        }
      };
      result.className = 'd-block px-4 py-3';
      if (success) {
        result.innerHTML = `
          <div class="d-flex align-items-center gap-2" style="color:#a6e3a1;">
            <i class="bi bi-check-circle-fill fs-5"></i>
            <div>
              <div class="fw-semibold">${_esc(plugin.name)} installed successfully!</div>
              <div class="small" style="color:#6c757d;">Restart ChitUI to activate the plugin.</div>
            </div>
          </div>`;
        plugin.installed = true;
        plugin.installed_version = plugin.version;
        plugin.has_update = false;
        const upd = _storePlugins.filter(x => x.has_update).length;
        const cb  = document.getElementById('storeUpdateCount');
        if (upd > 0) { cb.textContent = upd; }
        else { cb.style.display = 'none'; document.getElementById('storeUpdateBanner').classList.add('d-none'); }
        _storeRender();
      } else {
        result.innerHTML = `
          <div class="d-flex align-items-center gap-2" style="color:#f38ba8;">
            <i class="bi bi-x-circle-fill fs-5"></i>
            <div>
              <div class="fw-semibold">Installation failed</div>
              <div class="small">${_esc(message || 'Unknown error')}</div>
            </div>
          </div>`;
      }
    }

    _log(`Requesting download of ${plugin.name} from server…`);

    // Send the download URL to Flask which fetches it server-side (avoids CORS).
    fetch('/plugins/store/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slug: plugin.slug, download_url: plugin.download_url })
    })
      .then(r => r.json())
      .then(data => {
        if (!data.success) throw new Error(data.message || 'Install request failed');

        const jobId = data.job_id;
        const es = new EventSource(`/plugins/install/${jobId}/stream`);

        es.addEventListener('log', e => {
          const item = JSON.parse(e.data);
          _log(item.msg, item.level);
        });

        es.addEventListener('done', e => {
          es.close();
          const res = JSON.parse(e.data);
          _finish(res.success, res.message);
        });

        es.onerror = () => {
          es.close();
          _log('Connection lost during install.', 'error');
          _finish(false, 'Connection lost during install.');
        };
      })
      .catch(err => {
        console.error('Store install error:', err);
        _log('Error: ' + err.message, 'error');
        _finish(false, err.message);
      });
  }

  // ── Filter & search ────────────────────────────────────────────────────────
  window.storeSetFilter = function (btn, filter) {
    _storeFilter = filter;
    document.querySelectorAll('[data-store-filter]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    _storeRender();
  };

  // ── Wire up DOM events ───────────────────────────────────────────────────
  // Button is inside #modalSettings so it isn't in DOM at DOMContentLoaded.
  // Use delegation. On click: hide settings modal, then open store.
  //
  // We do NOT rely on hidden.bs.modal firing at the right time. Instead we
  // manually dispose the settings modal, remove leftover backdrops, and open
  // the store after Bootstrap's fade duration (300 ms).
  document.addEventListener('click', function (e) {
    if (!e.target.closest('#btnOpenPluginStore')) return;

    const settingsEl = document.getElementById('modalSettings');
    const storeEl    = document.getElementById('modalPluginStore');

    // If settings isn't open just show the store immediately
    if (!settingsEl || !settingsEl.classList.contains('show')) {
      bootstrap.Modal.getOrCreateInstance(storeEl).show();
      if (!_storeLoaded) storeLoadCatalog();
      return;
    }

    // Forcibly hide the settings modal without waiting for events
    const settingsInstance = bootstrap.Modal.getOrCreateInstance(settingsEl);
    settingsInstance.hide();

    // Wait for Bootstrap's modal fade (default 300 ms) + a small buffer,
    // then clean up any orphaned backdrops and show the store.
    setTimeout(function () {
      // Remove any lingering backdrops Bootstrap may have left behind
      document.querySelectorAll('.modal-backdrop').forEach(function (el) {
        el.remove();
      });
      document.body.classList.remove('modal-open');
      document.body.style.removeProperty('overflow');
      document.body.style.removeProperty('padding-right');

      bootstrap.Modal.getOrCreateInstance(storeEl).show();
      if (!_storeLoaded) storeLoadCatalog();
    }, 350);
  });

  // Wire search input when store modal opens
  document.addEventListener('shown.bs.modal', function (e) {
    if (e.target.id !== 'modalPluginStore') return;
    const searchInput = document.getElementById('storeSearchInput');
    if (searchInput && !searchInput._storeBound) {
      searchInput._storeBound = true;
      searchInput.addEventListener('input', function () {
        _storeSearch = this.value.trim();
        _storeRender();
      });
    }
  });
}());