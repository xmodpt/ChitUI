// Settings Management
let currentSettings = {
    printers: {},
    auto_discover: false
};

// Load settings on page load
$(document).ready(function() {
    console.log('Settings module loaded');
    loadSettings();

    // Settings modal event handlers
    $('#btnDiscover').click(discoverPrinters);
    $('#btnAddManual').click(addManualPrinter);
    $('#btnSaveSettings').click(saveSettings);

    // Printer image selection
    $('#btnSelectPrinterImage').click(function() {
        openPrinterImageSelector('add');
    });
    $('#btnEditPrinterImage').click(function() {
        openPrinterImageSelector('edit');
    });

    // Edit printer handlers
    $('#btnSaveEdit').click(saveEditPrinter);
    $('#btnCancelEdit').click(cancelEditPrinter);

    // Load available printer images
    loadPrinterImages();

    $('#btnRefreshPackages').click(function() {
        loadPythonPackages();
    });

    // Maintenance actions
    $('#btnRestartApp').click(restartApplication);
    $('#btnRebootPi').click(rebootSystem);

    // Auto-discover checkbox
    $('#autoDiscoverCheck').change(function() {
        currentSettings.auto_discover = $(this).is(':checked');
    });

    // Auto-login toggle
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

    // Session timeout change handler
    $('#sessionTimeout').change(function() {
        const timeout = parseInt($(this).val());

        $.ajax({
            url: '/auth/session-timeout',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ timeout: timeout }),
            success: function(response) {
                if (response.success) {
                    const timeoutText = timeout === 0 ? 'Never' :
                        timeout < 3600 ? `${timeout / 60} minutes` :
                        `${timeout / 3600} hour${timeout / 3600 > 1 ? 's' : ''}`;
                    showToast(`Session timeout set to: ${timeoutText}`, 'success');
                }
            },
            error: function(xhr) {
                const response = xhr.responseJSON || {};
                const message = response.message || 'Failed to update session timeout';
                showToast(message, 'danger');
            }
        });
    });

    // Logout button
    $('#btnLogoutSettings').click(function() {
        if (confirm('Are you sure you want to logout?')) {
            // Clear auto-login
            localStorage.removeItem('chitui_auto_login');
            localStorage.removeItem('chitui_auto_password');

            $.ajax({
                url: '/auth/logout',
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                success: function(data) {
                    if (data.success) {
                        window.location.href = '/';
                    }
                },
                error: function(error) {
                    console.error('Logout error:', error);
                    window.location.href = '/';
                }
            });
        }
    });

    // Load settings when modal opens
    $('#modalSettings').on('show.bs.modal', function() {
        console.log('Settings modal opened');
        loadSettings();

        // Update auto-login checkbox based on localStorage
        const autoLoginEnabled = localStorage.getItem('chitui_auto_login') === 'true';
        $('#autoLoginCheck').prop('checked', autoLoginEnabled);

        // Load session timeout setting
        $.ajax({
            url: '/auth/session-timeout',
            method: 'GET',
            success: function(response) {
                $('#sessionTimeout').val(response.timeout || 0);
            },
            error: function(error) {
                console.error('Error loading session timeout:', error);
            }
        });
    });

    // Load packages when Packages tab is clicked
    $('button[data-bs-target="#packages-pane"]').on('click', function() {
        console.log('Packages tab clicked');
        loadPythonPackages();
    });
});

// Load settings from server
function loadSettings() {
    console.log('Loading settings from server...');
    $.ajax({
        url: '/settings',
        method: 'GET',
        success: function(data) {
            console.log('Settings loaded:', data);
            currentSettings = data;
            updateSettingsUI();
        },
        error: function(xhr, status, error) {
            console.error('Error loading settings:', status, error);
            showToast('Error loading settings', 'danger');
        }
    });
}

// Update UI with current settings
function updateSettingsUI() {
    // Update auto-discover checkbox
    $('#autoDiscoverCheck').prop('checked', currentSettings.auto_discover || false);
    
    // Update saved printers list
    const savedPrintersList = $('#savedPrintersList');
    savedPrintersList.empty();
    
    const printerCount = Object.keys(currentSettings.printers || {}).length;
    
    if (printerCount === 0) {
        savedPrintersList.html(`
            <div class="list-group-item text-muted text-center">
                <i class="bi bi-info-circle"></i> No printers configured yet
            </div>
        `);
    } else {
        $.each(currentSettings.printers, function(printerId, printer) {
            const template = $('#tmplSavedPrinter').html();
            const $item = $(template);
            
            $item.attr('data-printer-id', printerId);
            $item.find('.printer-name').text(printer.name);
            $item.find('.printer-ip').text(printer.ip);
            $item.find('.printer-enabled-toggle').prop('checked', printer.enabled !== false);

            // Check if this is the default printer
            const isDefault = currentSettings.default_printer === printerId;
            if (isDefault) {
                $item.find('.btn-set-default').addClass('is-default').attr('title', 'Default Printer');
            }

            // Handle enable/disable toggle
            $item.find('.printer-enabled-toggle').change(function() {
                currentSettings.printers[printerId].enabled = $(this).is(':checked');
            });

            // Handle set as default button
            $item.find('.btn-set-default').click(function() {
                setDefaultPrinter(printerId, printer.name);
            });

            // Handle edit button
            $item.find('.btn-edit-printer').click(function() {
                editPrinter(printerId, printer);
            });

            // Handle remove button
            $item.find('.btn-remove-printer').click(function() {
                if (confirm(`Remove printer "${printer.name}"?`)) {
                    removePrinter(printerId);
                }
            });

            savedPrintersList.append($item);
        });
    }
}

// Discover printers
function discoverPrinters() {
    const $btn = $('#btnDiscover');
    const $spinner = $('#discoverSpinner');
    
    $btn.prop('disabled', true);
    $spinner.removeClass('d-none');
    
    console.log('Starting printer discovery...');
    
    $.ajax({
        url: '/discover',
        method: 'POST',
        timeout: 5000, // 5 second timeout
        success: function(data) {
            console.log('Discovery response:', data);
            if (data.success) {
                const count = data.count || Object.keys(data.printers || {}).length;
                showToast(`Discovered ${count} printer(s)`, 'success');
                
                // Wait a moment for printers to connect, then reload settings
                setTimeout(function() {
                    loadSettings();
                }, 1000);
            } else {
                showToast(data.message || 'No printers discovered', 'warning');
            }
        },
        error: function(xhr, status, error) {
            console.error('Discovery error:', status, error, xhr.responseJSON);
            const message = xhr.responseJSON?.message || 'No printers discovered';
            showToast(message, 'warning');
        },
        complete: function() {
            $btn.prop('disabled', false);
            $spinner.addClass('d-none');
        }
    });
}

// Add printer manually
function addManualPrinter() {
    const ip = $('#manualPrinterIP').val().trim();
    const name = $('#manualPrinterName').val().trim() || `Printer-${ip}`;
    const image = $('#manualPrinterImage').val().trim();
    const usbDeviceType = $('#manualUSBDeviceType').val() || 'physical';

    if (!ip) {
        showToast('Please enter an IP address', 'warning');
        return;
    }

    // Basic IP validation
    const ipPattern = /^(\d{1,3}\.){3}\d{1,3}$/;
    if (!ipPattern.test(ip)) {
        showToast('Please enter a valid IP address', 'warning');
        return;
    }

    // Validate IP octets
    const octets = ip.split('.');
    for (let i = 0; i < octets.length; i++) {
        const octet = parseInt(octets[i]);
        if (octet < 0 || octet > 255) {
            showToast('IP address octets must be between 0 and 255', 'warning');
            return;
        }
    }

    console.log('Adding manual printer:', ip, name, image, 'USB Type:', usbDeviceType);

    const $btn = $('#btnAddManual');
    $btn.prop('disabled', true);

    const printerData = {
        ip: ip,
        name: name,
        usb_device_type: usbDeviceType
    };
    if (image) {
        printerData.image = image;
    }

    $.ajax({
        url: '/printer/manual',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(printerData),
        timeout: 5000,
        success: function(data) {
            console.log('Add printer response:', data);
            if (data.success) {
                showToast(`Added printer "${name}" - attempting to connect...`, 'success');
                $('#manualPrinterIP').val('');
                $('#manualPrinterName').val('');
                $('#manualPrinterImage').val('');
                $('#manualUSBDeviceType').val('physical');
                $('#selectedImageText').text('Select Printer Image (Optional)');

                // Wait a moment for connection, then reload settings
                setTimeout(function() {
                    loadSettings();
                }, 1500);
            } else {
                showToast(data.message || 'Failed to add printer', 'danger');
            }
        },
        error: function(xhr, status, error) {
            console.error('Add printer error:', status, error, xhr.responseJSON);
            const message = xhr.responseJSON?.message || 'Failed to add printer';
            showToast(message, 'danger');
        },
        complete: function() {
            $btn.prop('disabled', false);
        }
    });
}

// Remove printer
function removePrinter(printerId) {
    $.ajax({
        url: `/printer/${printerId}`,
        method: 'DELETE',
        success: function(data) {
            if (data.success) {
                showToast('Printer removed', 'success');
                delete currentSettings.printers[printerId];
                updateSettingsUI();
            }
        },
        error: function(xhr, status, error) {
            showToast('Failed to remove printer', 'danger');
        }
    });
}

// Save settings
function saveSettings() {
    console.log('Saving settings:', currentSettings);
    
    $.ajax({
        url: '/settings',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(currentSettings),
        success: function(data) {
            console.log('Settings save response:', data);
            if (data.success) {
                showToast('Settings saved successfully', 'success');
                $('#modalSettings').modal('hide');
                
                // Refresh printers list - check if socket is available
                if (typeof socket !== 'undefined' && socket) {
                    socket.emit('printers', {});
                } else {
                    console.log('Socket not available, reloading page...');
                    setTimeout(function() {
                        window.location.reload();
                    }, 1000);
                }
            }
        },
        error: function(xhr, status, error) {
            console.error('Save settings error:', status, error);
            showToast('Failed to save settings', 'danger');
        }
    });
}

// Show toast notification
function showToast(message, type = 'info') {
    const $toast = $('#toastUpload');
    const bgClass = type === 'success' ? 'bg-success' :
                    type === 'danger' ? 'bg-danger' :
                    type === 'warning' ? 'bg-warning' : 'bg-info';

    $toast.find('.toast-header').removeClass('bg-success bg-danger bg-warning bg-info bg-body-secondary')
          .addClass(bgClass);
    $toast.find('.toast-body').text(message);

    const toast = new bootstrap.Toast($toast[0]);
    toast.show();
}

// Load Python packages
function loadPythonPackages() {
    console.log('Loading Python packages...');
    const $container = $('#packagesListContainer');
    const $refreshBtn = $('#btnRefreshPackages');

    // Show loading spinner
    $container.html(`
        <div class="text-center py-4">
            <div class="spinner-border text-secondary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p class="text-muted mt-2">Loading packages...</p>
        </div>
    `);

    // Disable button
    $refreshBtn.prop('disabled', true);

    $.ajax({
        url: '/python-packages',
        method: 'GET',
        timeout: 15000,
        success: function(data) {
            console.log('Packages loaded:', data);
            if (data.success && data.packages) {
                renderPackagesList(data.packages, data.count);
            } else {
                $container.html(`
                    <div class="alert alert-warning">
                        <i class="bi bi-exclamation-triangle"></i> Failed to load packages
                    </div>
                `);
            }
        },
        error: function(xhr, status, error) {
            console.error('Error loading packages:', status, error);
            const errorMsg = xhr.responseJSON?.error || 'Failed to load Python packages';
            $container.html(`
                <div class="alert alert-danger">
                    <i class="bi bi-x-circle"></i> Error: ${errorMsg}
                </div>
            `);
        },
        complete: function() {
            $refreshBtn.prop('disabled', false);
        }
    });
}

// Render packages list
function renderPackagesList(packages, count) {
    const $container = $('#packagesListContainer');

    if (!packages || packages.length === 0) {
        $container.html(`
            <div class="alert alert-info">
                <i class="bi bi-info-circle"></i> No packages found
            </div>
        `);
        return;
    }

    let html = `
        <div class="mb-3">
            <span class="badge bg-secondary">${count} packages installed</span>
        </div>
        <div class="table-responsive" style="max-height: 450px; overflow-y: auto;">
            <table class="table table-sm table-hover">
                <thead class="sticky-top bg-body">
                    <tr>
                        <th style="width: 50%;">Package Name</th>
                        <th style="width: 50%;">Version</th>
                    </tr>
                </thead>
                <tbody>
    `;

    packages.forEach(function(pkg) {
        const packageName = escapeHtml(pkg.name);
        const currentVersion = escapeHtml(pkg.version);

        html += `<tr>`;
        html += `<td><code>${packageName}</code></td>`;
        html += `<td><span class="text-muted">${currentVersion}</span></td>`;
        html += `</tr>`;
    });

    html += `
                </tbody>
            </table>
        </div>
    `;

    $container.html(html);
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, function(m) { return map[m]; });
}

// Restart Application
function restartApplication() {
    if (!confirm('Are you sure you want to restart the application? This will temporarily disconnect all printers and reload all plugins.')) {
        return;
    }

    const $btn = $('#btnRestartApp');
    $btn.prop('disabled', true);
    $btn.html('<i class="bi bi-arrow-clockwise"></i> Restarting...');

    console.log('Restarting application...');
    showToast('Restarting application...', 'info');

    $.ajax({
        url: '/maintenance/restart',
        method: 'POST',
        timeout: 5000,
        success: function(data) {
            console.log('Restart response:', data);
            showToast('Application is restarting. The page will reload in 5 seconds...', 'success');
            $('#modalSettings').modal('hide');

            // Wait 5 seconds for the restart process to complete
            setTimeout(function() {
                window.location.reload();
            }, 5000);
        },
        error: function(xhr, status, error) {
            console.error('Restart error:', status, error);
            showToast('Application restart initiated. Reloading in 5 seconds...', 'success');
            $('#modalSettings').modal('hide');

            // Reload anyway after a few seconds
            setTimeout(function() {
                window.location.reload();
            }, 5000);
        }
    });
}

// Reboot System
function rebootSystem() {
    if (!confirm('Are you sure you want to reboot the Raspberry Pi? This will disconnect all printers and take about 30-60 seconds.')) {
        return;
    }

    const $btn = $('#btnRebootPi');
    $btn.prop('disabled', true);
    $btn.html('<i class="bi bi-power"></i> Rebooting...');

    console.log('Rebooting system...');
    showToast('Rebooting system...', 'warning');

    $.ajax({
        url: '/maintenance/reboot',
        method: 'POST',
        timeout: 5000,
        success: function(data) {
            console.log('Reboot response:', data);
            showToast('System is rebooting. Please wait 30-60 seconds before reconnecting.', 'warning');
            $('#modalSettings').modal('hide');
        },
        error: function(xhr, status, error) {
            console.error('Reboot error:', status, error);
            showToast('System reboot initiated. Please wait 30-60 seconds before reconnecting.', 'warning');
            $('#modalSettings').modal('hide');
        }
    });
}

// Load available printer images
let availablePrinterImages = [];

function loadPrinterImages() {
    // Fetch available images from server
    $.ajax({
        url: '/printer/images',
        method: 'GET',
        success: function(data) {
            if (data.success) {
                availablePrinterImages = data.images;
                populatePrinterImageGrid();
            } else {
                console.error('Failed to load printer images:', data.message);
            }
        },
        error: function(xhr, status, error) {
            console.error('Error loading printer images:', status, error);
        }
    });
}

function populatePrinterImageGrid() {
    // Populate the printer image grid in the modal
    const $grid = $('#printerImageGrid');

    // Clear existing images (except the default icon which is first)
    $grid.find('.col-6:not(:first)').remove();

    // Add each available image
    availablePrinterImages.forEach(function(imagePath) {
        const imageName = imagePath.replace(/\.(webp|png|jpg)$/i, '').replace(/_/g, ' ');
        const $option = $(`
            <div class="col-6 col-md-4">
                <div class="printer-image-option" data-image="${imagePath}">
                    <div class="printer-image-preview">
                        <img src="img/${imagePath}" alt="${imageName}">
                    </div>
                    <small class="d-block text-center mt-2">${imageName}</small>
                </div>
            </div>
        `);

        $grid.append($option);
    });

    // Add click handlers to all printer image options
    $('.printer-image-option').click(function() {
        const imagePath = $(this).data('image');
        const mode = $('#modalPrinterImage').data('mode');
        selectPrinterImage(imagePath, mode);
    });
}

// Open printer image selector modal
function openPrinterImageSelector(mode) {
    // Store the mode (add or edit) in the modal's data
    $('#modalPrinterImage').data('mode', mode);

    // Highlight the currently selected image
    const currentImage = mode === 'add' ?
        $('#manualPrinterImage').val() :
        $('#editPrinterImage').val();

    $('.printer-image-option').removeClass('selected');
    $(`.printer-image-option[data-image="${currentImage}"]`).addClass('selected');

    // Show the modal
    const modal = new bootstrap.Modal($('#modalPrinterImage')[0]);
    modal.show();
}

// Select printer image
function selectPrinterImage(imagePath, mode) {
    console.log('Selected image:', imagePath, 'for mode:', mode);

    if (mode === 'add') {
        $('#manualPrinterImage').val(imagePath);
        if (imagePath) {
            const imageName = imagePath.replace('.webp', '').replace(/_/g, ' ');
            $('#selectedImageText').text(imageName);
        } else {
            $('#selectedImageText').text('Select Printer Image (Optional)');
        }
    } else if (mode === 'edit') {
        $('#editPrinterImage').val(imagePath);
        if (imagePath) {
            const imageName = imagePath.replace('.webp', '').replace(/_/g, ' ');
            $('#editSelectedImageText').text(imageName);
        } else {
            $('#editSelectedImageText').text('Select Printer Image (Optional)');
        }
    }

    // Close the modal
    bootstrap.Modal.getInstance($('#modalPrinterImage')[0]).hide();
}

// Edit printer
function editPrinter(printerId, printer) {
    console.log('Editing printer:', printerId, printer);

    // Populate edit form
    $('#editPrinterId').val(printerId);
    $('#editPrinterIP').val(printer.ip);
    $('#editPrinterName').val(printer.name);
    $('#editPrinterImage').val(printer.image || '');
    $('#editUSBDeviceType').val(printer.usb_device_type || 'physical');

    // Update image button text
    if (printer.image) {
        const imageName = printer.image.replace('.webp', '').replace(/_/g, ' ');
        $('#editSelectedImageText').text(imageName);
    } else {
        $('#editSelectedImageText').text('Select Printer Image (Optional)');
    }

    // Show edit section
    $('#editPrinterSection').removeClass('d-none');

    // Scroll to edit section
    $('#editPrinterSection')[0].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Cancel edit printer
function cancelEditPrinter() {
    // Clear edit form
    $('#editPrinterId').val('');
    $('#editPrinterIP').val('');
    $('#editPrinterName').val('');
    $('#editPrinterImage').val('');
    $('#editUSBDeviceType').val('physical');
    $('#editSelectedImageText').text('Select Printer Image (Optional)');

    // Hide edit section
    $('#editPrinterSection').addClass('d-none');
}

// Save edited printer
function saveEditPrinter() {
    const printerId = $('#editPrinterId').val();
    const ip = $('#editPrinterIP').val().trim();
    const name = $('#editPrinterName').val().trim();
    const image = $('#editPrinterImage').val().trim();
    const usbDeviceType = $('#editUSBDeviceType').val() || 'physical';

    if (!ip) {
        showToast('Please enter an IP address', 'warning');
        return;
    }

    if (!name) {
        showToast('Please enter a printer name', 'warning');
        return;
    }

    // Basic IP validation
    const ipPattern = /^(\d{1,3}\.){3}\d{1,3}$/;
    if (!ipPattern.test(ip)) {
        showToast('Please enter a valid IP address', 'warning');
        return;
    }

    // Validate IP octets
    const octets = ip.split('.');
    for (let i = 0; i < octets.length; i++) {
        const octet = parseInt(octets[i]);
        if (octet < 0 || octet > 255) {
            showToast('IP address octets must be between 0 and 255', 'warning');
            return;
        }
    }

    console.log('Updating printer:', printerId, ip, name, image, 'USB Type:', usbDeviceType);

    const $btn = $('#btnSaveEdit');
    $btn.prop('disabled', true);

    const printerData = {
        ip: ip,
        name: name,
        usb_device_type: usbDeviceType
    };
    if (image) {
        printerData.image = image;
    }

    $.ajax({
        url: `/printer/${printerId}`,
        method: 'PUT',
        contentType: 'application/json',
        data: JSON.stringify(printerData),
        success: function(data) {
            console.log('Update printer response:', data);
            if (data.success) {
                showToast(`Printer "${name}" updated successfully`, 'success');
                cancelEditPrinter();
                loadSettings();
            } else {
                showToast(data.message || 'Failed to update printer', 'danger');
            }
        },
        error: function(xhr, status, error) {
            console.error('Update printer error:', status, error, xhr.responseJSON);
            const message = xhr.responseJSON?.message || 'Failed to update printer';
            showToast(message, 'danger');
        },
        complete: function() {
            $btn.prop('disabled', false);
        }
    });
}

// Set default printer
function setDefaultPrinter(printerId, printerName) {
    console.log('Setting default printer:', printerId, printerName);

    $.ajax({
        url: '/printer/default',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ printer_id: printerId }),
        success: function(data) {
            console.log('Set default printer response:', data);
            if (data.success) {
                showToast(`"${printerName}" set as default printer`, 'success');
                currentSettings.default_printer = printerId;
                updateSettingsUI(); // Refresh to show the new default
            } else {
                showToast(data.message || 'Failed to set default printer', 'danger');
            }
        },
        error: function(xhr, status, error) {
            console.error('Set default printer error:', status, error, xhr.responseJSON);
            const message = xhr.responseJSON?.message || 'Failed to set default printer';
            showToast(message, 'danger');
        }
    });
}

