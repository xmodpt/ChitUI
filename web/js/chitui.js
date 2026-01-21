/**
 * ChitUI Plus - Main Client-Side JavaScript
 *
 * This file handles all client-side functionality for the ChitUI web interface.
 * It manages real-time communication with the server via Socket.IO, printer discovery,
 * status monitoring, file management, and print job control.
 *
 * Architecture:
 * - Socket.IO Client: Real-time bidirectional communication with Flask server
 * - Event-driven: Responds to server events and user interactions
 * - Global State: Maintains printer list, current printer, and UI state
 *
 * Key Features:
 * - Auto-discovery and connection to Chitu printers
 * - Real-time printer status updates
 * - File upload/delete/print management
 * - Temperature monitoring and control
 * - Print job control (start/pause/resume/stop)
 * - USB gadget integration for local file storage
 * - Default printer auto-selection
 *
 * Dependencies:
 * - socket.io-client: Real-time communication
 * - Bootstrap: UI framework and modals
 * - jQuery: DOM manipulation (legacy - consider migrating to vanilla JS)
 *
 * @author ChitUI Developer
 * @version 1.0
 */

// ========================================================================
// SOCKET.IO AND COMMUNICATION SETUP
// ========================================================================

// Initialize Socket.IO connection to Flask server
const socket = io();

// ========================================================================
// GLOBAL STATE MANAGEMENT
// ========================================================================

// WebSocket connections to printers (managed server-side)
var websockets = []

// Discovered printers dictionary: {printer_id: printer_info}
var printers = {}

// Track previous online status for change detection
var printersPreviousStatus = {}

// Currently selected printer ID
var currentPrinter = null

// Default printer settings for auto-selection
var defaultPrinterId = null          // Default printer to auto-select on page load
var defaultPrinterLoaded = false     // Track if default setting is loaded from server
var printersLoaded = false           // Track if printer list is loaded from server

// UI elements and state
var progress = null                   // Print progress percentage
var printStatusModal = null          // Bootstrap modal for print status
var cameraFullscreenModal = null     // Bootstrap modal for fullscreen camera view
var cameraActive = false             // Camera streaming state

// ========================================================================
// DEFAULT PRINTER AUTO-SELECTION
// ========================================================================

/**
 * Auto-select default printer when both printers list and settings are loaded
 *
 * This function implements a coordination pattern to ensure we don't try to
 * select the default printer before both the printer list and user settings
 * are available. It's called from multiple places:
 * - When printer list is received
 * - When settings are loaded
 *
 * Conditions required for auto-selection:
 * 1. No printer currently selected
 * 2. Default printer ID is loaded from settings
 * 3. Printer list is loaded from server
 * 4. Default printer exists in the printer list
 */
function tryAutoSelectDefaultPrinter() {
  console.log('tryAutoSelectDefaultPrinter called:');
  console.log('  currentPrinter:', currentPrinter);
  console.log('  defaultPrinterLoaded:', defaultPrinterLoaded);
  console.log('  printersLoaded:', printersLoaded);
  console.log('  defaultPrinterId:', defaultPrinterId);
  console.log('  printers:', Object.keys(printers));
  console.log('  default printer exists in printers?', defaultPrinterId && printers[defaultPrinterId]);

  if (!currentPrinter && defaultPrinterLoaded && printersLoaded && defaultPrinterId && printers[defaultPrinterId]) {
    console.log('✓ Auto-selecting default printer:', defaultPrinterId);
    showPrinter(defaultPrinterId);
  } else {
    console.log('✗ Conditions not met for auto-select');
  }
}

// ========================================================================
// SOCKET.IO EVENT HANDLERS
// ========================================================================
//
// These handlers respond to events emitted by the Flask server.
// They manage printer discovery, status updates, and real-time notifications.
//
// ========================================================================

/**
 * Socket.IO Connection Event
 * Fired when connection to server is established
 */
socket.on("connect", () => {
  console.log('socket.io connected: ' + socket.id);
  setServerStatus(true)
});

/**
 * Socket.IO Disconnection Event
 * Fired when connection to server is lost
 */
socket.on("disconnect", () => {
  console.log("socket.io disconnected");
  setServerStatus(false)
});

/**
 * Printers List Event
 * Receives updated list of discovered printers from server
 * Handles printer status changes and auto-reloads page if any printer goes online/offline
 *
 * @param {Object} data - Dictionary of printers {printer_id: printer_info}
 */
socket.on("printers", (data) => {
  console.log(JSON.stringify(data))

  // Check if any printer status changed (online <-> offline)
  var statusChanged = false
  for (var id in data) {
    var currentOnline = data[id].online
    var previousOnline = printersPreviousStatus[id]

    // If we have previous status and it changed
    if (previousOnline !== undefined && previousOnline !== currentOnline) {
      console.log(`Printer ${data[id].name} status changed: ${previousOnline ? 'online' : 'offline'} -> ${currentOnline ? 'online' : 'offline'}`)
      statusChanged = true
      break
    }
  }

  // Update previous status tracking
  for (var id in data) {
    printersPreviousStatus[id] = data[id].online
  }

  // If status changed, reload the page to refresh all info
  if (statusChanged) {
    console.log('Printer status changed - reloading page...')
    location.reload()
    return
  }

  printers = data
  $("#printersList").empty()
  addPrinters(data)

  // Mark printers as loaded and try to auto-select default
  printersLoaded = true
  tryAutoSelectDefaultPrinter()
});

socket.on("printer_response", (data) => {
  switch (data.Data.Cmd) {
    case SDCP_CMD_STATUS:
      // Status responses are already handled by printer_status event
      // but we can log it here for debugging
      console.log('Status response received');
      break;
      
    case SDCP_CMD_ATTRIBUTES:
      // Attributes responses are already handled by printer_attributes event
      // but we can log it here for debugging
      console.log('Attributes response received');
      break;
      
    case SDCP_CMD_RETRIEVE_FILE_LIST:
      handle_printer_files(data);
      break;
      
    case SDCP_CMD_BATCH_DELETE_FILES:
      modalConfirm.hide();
      break;
      
    case SDCP_CMD_START_PRINTING:
      modalConfirm.hide();
      break;
      
    case SDCP_CMD_RETRIEVE_TASK_DETAILS:
      handle_task_details(data);
      break;
      
    default:
      console.log('Unknown response:', data);
      break;
  }
});

socket.on("printer_error", (data) => {
  console.log("=== ERROR ===")
  console.log(data)
  alert("Error Code:" + data.Data.Data.ErrorCode)
});

socket.on("printer_notice", (data) => {
  console.log("=== NOTICE ===")
  console.log(data)
  alert("Notice:" + data.Data.Data.Message)
});

socket.on("printer_status", (data) => {
  handle_printer_status(data)
});

socket.on("printer_attributes", (data) => {
  handle_printer_attributes(data)
});

socket.on("refresh_page", (data) => {
  console.log("Page refresh requested:", data.reason);

  // For virtual USB operations, wait for reload to complete before refreshing
  if (data.reason === 'virtual_usb_delete' || data.reason === 'virtual_usb_upload') {
    console.log("Virtual USB operation - waiting for gadget reload...");

    // Show toast notification with countdown
    let countdown = 3;
    const toastInterval = setInterval(() => {
      if (countdown > 0) {
        // Update any visible toast or status message
        console.log(`Reloading USB gadget... ${countdown}s`);
        countdown--;
      } else {
        clearInterval(toastInterval);
      }
    }, 1000);

    // Wait 3 seconds for USB gadget reload, then refresh file list
    setTimeout(() => {
      if (currentPrinter) {
        if (printers[currentPrinter]) {
          printers[currentPrinter]['files'] = [];
        }
        console.log("Refreshing file lists...");
        getPrinterFiles(currentPrinter, '/local');
        getPrinterFiles(currentPrinter, '/usb');
      }
    }, 3000);
  } else {
    // For other operations, just reload the page
    setTimeout(() => {
      window.location.reload();
    }, 1000);
  }
});

function handle_printer_status(data) {
  if (!printers[data.MainboardID].hasOwnProperty('status')) {
    printers[data.MainboardID]['status'] = {}
  }
  var filter = ['CurrentStatus', 'PrintScreen', 'ReleaseFilm', 'TempOfUVLED', 'TimeLapseStatus', 'PrintInfo']
  $.each(data.Status, function (key, val) {
    if (filter.includes(key)) {
      if (val.length == 1) {
        val = val[0]
      }
      printers[data.MainboardID]['status'][key] = val
    }
  })
  printer_status = printers[data.MainboardID]['status']
  
  if (typeof printer_status['PreviousStatus'] !== undefined
    && printer_status['PreviousStatus'] == SDCP_MACHINE_STATUS_UNKNOWN_8
    && printer_status['CurrentStatus'] == SDCP_MACHINE_STATUS_IDLE) {
    socket.emit("printer_files", { id: data.MainboardID, url: '/local' })
  }
  printers[data.MainboardID]['status']['PreviousStatus'] = printer_status['CurrentStatus']
  updatePrinterStatus(data)
  createTable('Status', data.Status)
  if (data.Status.CurrentStatus.includes(1)) {
    createTable('Print', data.Status.PrintInfo)
    updatePrintOverlay(data.MainboardID, data.Status.PrintInfo)
  } else {
    if (printStatusModal && data.MainboardID === currentPrinter) {
      printStatusModal.hide()
    }
  }
}

function handle_printer_attributes(data) {
  console.log(data)
  if (!printers[data.MainboardID].hasOwnProperty('attributes')) {
    printers[data.MainboardID]['attributes'] = {}
  }
  var filter = ['Resolution', 'XYZsize', 'NumberOfVideoStreamConnected', 'MaximumVideoStreamAllowed', 'UsbDiskStatus', 'Capabilities', 'SupportFileType', 'DevicesStatus', 'ReleaseFilmMax', 'CameraStatus', 'RemainingMemory', 'TLPNoCapPos', 'TLPStartCapPos', 'TLPInterLayers']
  $.each(data.Attributes, function (key, val) {
    if (filter.includes(key)) {
      printers[data.MainboardID]['attributes'][key] = val
    }
  })
  createTable('Attributes', data.Attributes)
  
  // Update storage display if RemainingMemory is present
  if (data.Attributes.RemainingMemory !== undefined) {
    updateStorageDisplay(data.Attributes.RemainingMemory);
  }
}

function updateStorageDisplay(remainingBytes) {
  // RemainingMemory is in bytes, convert to GB
  const remainingGB = remainingBytes / (1024 * 1024 * 1024);

  // Try to determine total storage from remaining
  // Most printers have 16GB or 32GB internal storage
  let totalGB = 32;
  if (remainingGB > 32) {
    totalGB = 64;
  } else if (remainingGB < 16) {
    totalGB = 16;
  }

  const usedGB = totalGB - remainingGB;
  const usedPercent = (usedGB / totalGB * 100).toFixed(1);

  // Format sizes nicely
  function formatSize(gb) {
    if (gb < 1) {
      return (gb * 1024).toFixed(0) + ' MB';
    }
    return gb.toFixed(2) + ' GB';
  }

  // Update circular gauge (file manager)
  const $gaugeCircle = $('#storageGaugeCircle');
  const $gaugePercent = $('#storageGaugePercent');
  const circumference = 2 * Math.PI * 80; // 2 * PI * radius (80)
  const offset = circumference - (usedPercent / 100 * circumference);

  $gaugeCircle.css('stroke-dashoffset', offset);
  $gaugePercent.text(Math.round(usedPercent) + '%');

  // Color code the circular gauge
  $gaugeCircle.removeClass('warning danger');
  if (usedPercent < 70) {
    // Green (default)
  } else if (usedPercent < 90) {
    $gaugeCircle.addClass('warning');
  } else {
    $gaugeCircle.addClass('danger');
  }

  // Update simple display text
  $('#storageUsedSimple').text(formatSize(usedGB));
  $('#storageFreeSimple').text(formatSize(remainingGB));
  $('#storageInfo').show();

  // Update detailed display (settings modal)
  $('#storageUsed').text(formatSize(usedGB));
  $('#storageTotal').text(formatSize(totalGB));
  $('#storageFree').text(formatSize(remainingGB));
  $('#storagePercent').text(usedPercent + '%');
  $('#storageProgressBar').css('width', usedPercent + '%').attr('aria-valuenow', usedPercent);

  // Color code the progress bar
  const $progressBar = $('#storageProgressBar');
  $progressBar.removeClass('bg-success bg-warning bg-danger');
  if (usedPercent < 70) {
    $progressBar.addClass('bg-success');
  } else if (usedPercent < 90) {
    $progressBar.addClass('bg-warning');
  } else {
    $progressBar.addClass('bg-danger');
  }
}

function updateUsbGadgetStorage() {
  fetch('/usb-gadget/storage')
    .then(response => response.json())
    .then(data => {
      if (data.success && data.available) {
        // Show the gauge
        $('#usbGaugeWrapper').show();

        // Format size function
        function formatSize(bytes) {
          const mb = bytes / (1024 * 1024);
          const gb = bytes / (1024 * 1024 * 1024);
          if (gb >= 1) {
            return gb.toFixed(2) + ' GB';
          }
          return mb.toFixed(0) + ' MB';
        }

        // Update gauge
        const usedPercent = data.percent;
        const $gaugeCircle = $('#usbGaugeCircle');
        const $gaugePercent = $('#usbGaugePercent');
        const circumference = 2 * Math.PI * 80; // 2 * PI * radius (80)
        const offset = circumference - (usedPercent / 100 * circumference);

        $gaugeCircle.css('stroke-dashoffset', offset);
        $gaugePercent.text(Math.round(usedPercent) + '%');

        // Color code the USB gauge
        $gaugeCircle.removeClass('warning danger');
        if (usedPercent < 70) {
          // Green (default)
        } else if (usedPercent < 90) {
          $gaugeCircle.addClass('warning');
        } else {
          $gaugeCircle.addClass('danger');
        }

        // Update details text
        $('#usbGaugeDetails').text(formatSize(data.used) + ' / ' + formatSize(data.total));
      } else {
        // Hide the gauge if USB gadget is not available
        $('#usbGaugeWrapper').hide();
      }
    })
    .catch(error => {
      console.error('Error fetching USB gadget storage:', error);
      $('#usbGaugeWrapper').hide();
    });
}

// Update USB gadget storage every 5 seconds
setInterval(updateUsbGadgetStorage, 5000);
// Also update immediately on page load
$(document).ready(function() {
  updateUsbGadgetStorage();
});


function handle_task_details(data) {
  console.log('=== Task details received ===');
  console.log('Full data:', JSON.stringify(data, null, 2));

  var id = data.Data.MainboardID;
  console.log('Printer ID:', id);

  if (data.Data && data.Data.Data && data.Data.Data.HistoryDetailList) {
    console.log('HistoryDetailList found, length:', data.Data.Data.HistoryDetailList.length);

    if (data.Data.Data.HistoryDetailList.length > 0) {
      var taskDetail = data.Data.Data.HistoryDetailList[0];
      console.log('Task detail object keys:', Object.keys(taskDetail));
      console.log('Task detail:', taskDetail);

      if (!printers[id].currentPrint) {
        printers[id].currentPrint = {};
      }

      // Store thumbnail (v2 style - use Thumbnail field directly)
      if (taskDetail.Thumbnail) {
        console.log('Thumbnail found! Type:', typeof taskDetail.Thumbnail);
        console.log('Thumbnail preview:', taskDetail.Thumbnail.substring(0, 100));
        printers[id].currentPrint.thumbnail = taskDetail.Thumbnail;

        // Display if modal is currently showing for this printer (v2 style)
        if (currentPrinter === id && $('#modalPrintStatus').hasClass('show')) {
          console.log('Modal is showing, displaying thumbnail now');
          displayThumbnail(taskDetail.Thumbnail);
        } else {
          console.log('Modal not showing yet or different printer, thumbnail stored for later');
        }
      } else {
        console.warn('No Thumbnail field found. Available fields:', Object.keys(taskDetail));
      }

      // Store other task details if needed
      if (taskDetail.Name) {
        printers[id].currentPrint.fileName = taskDetail.Name;
      }
    }
  } else {
    console.warn('HistoryDetailList not found in expected location. Data structure:', data.Data);
  }
  console.log('=== End task details ===');
}

function handle_printer_files(data) {
  console.log('=== handle_printer_files called ===');
  console.log('Data received:', data);

  var id = data.Data.MainboardID
  var files = []
  if (printers[id]['files'] !== undefined) {
    files = printers[id]['files']
  }

  $.each(data.Data.Data.FileList, function (i, f) {
    if (f.type === 0) {
      getPrinterFiles(id, f.name)
    } else {
      if (!files.includes(f.name)) {
        files.push(f.name)
        console.log('Added file:', f.name);
      }
    }
  })

  printers[id]['files'] = files
  console.log('Total files for printer ' + id + ':', files.length);
  console.log('Files array:', files);

  createTable('Files', files)
  addFileOptions()
  console.log('=== handle_printer_files complete ===');
}

function addPrinters(printers) {
  $.each(printers, function (id, printer) {
    var template = $("#tmplPrintersListItem").html()
    var item = $(template)
    // Use custom image if set, otherwise use auto-generated from brand/model
    var printerIcon = printer.image || (printer.brand + '_' + printer.model).split(" ").join("").toLowerCase() + '.webp'
    item.attr('id', 'printer_' + id)
    item.attr("data-connection-id", printer.connection)
    item.attr("data-printer-id", id)
    item.find(".printerName").text(printer.name)
    item.find(".printerType").text(printer.brand + ' ' + printer.model)

    // Set printer icon with error handling to avoid broken image icons
    var iconImg = item.find(".printerIcon")
    iconImg.attr("src", 'img/' + printerIcon)
    iconImg.on('error', function() {
      // Hide the image if it fails to load
      $(this).hide()
    })

    // Set online/offline status
    var statusBadge = item.find(".printerStatusBadge")
    var statusText = item.find(".printerStatus")
    if (printer.online === true) {
      statusBadge.removeClass("status-offline").addClass("status-online")
      statusText.text("Online")
    } else {
      statusBadge.removeClass("status-online").addClass("status-offline")
      statusText.text("Offline")
    }

    item.on('click', function () {
      showPrinter($(this).data('printer-id'))
    })
    $("#printersList").append(item)
    console.log('Emitting printer_info for printer:', id)
    socket.emit("printer_info", { id: id })
  });
}

function showPrinter(id) {
  currentPrinter = id
  var p = printers[id]
  // Use custom image if set, otherwise use auto-generated from brand/model
  var printerIcon = p.image || (p.brand + '_' + p.model).split(" ").join("").toLowerCase() + '.webp'
  $('#printerName').text(p.name)
  $('#printerType').text(p.brand + ' ' + p.model)

  // Hide image and show placeholder while loading
  $("#printerIcon").addClass('d-none').attr('src', '')
  $("#printerIconPlaceholder").removeClass('d-none')

  // Preload the image to avoid showing broken image icon
  var imgPath = 'img/' + printerIcon
  var img = new Image()
  img.onload = function() {
    // Image loaded successfully, show it and hide placeholder
    $("#printerIcon").attr("src", imgPath).removeClass('d-none')
    $("#printerIconPlaceholder").addClass('d-none')
  }
  img.onerror = function() {
    // Image failed to load, keep placeholder visible
    console.log('Printer icon not found:', imgPath)
    $("#printerIcon").addClass('d-none')
    $("#printerIconPlaceholder").removeClass('d-none')
  }
  img.src = imgPath

  // Only create tables if data exists
  if (p.status) {
    createTable('Status', p.status, true)
    if (p.status.PrintInfo) {
      createTable('Print', p.status.PrintInfo)
    }
  }
  if (p.attributes) {
    createTable('Attributes', p.attributes)
  }

  // Handle files - clear old files first, then display if already loaded, otherwise request them
  // Clear the Files table and file manager immediately when switching printers
  if ($('#tableFiles').length > 0) {
    $('#tableFiles').empty();
    // Also clear the file manager
    const fileManagerBody = document.getElementById('fileManagerBody');
    if (fileManagerBody) {
      fileManagerBody.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-4"><i class="bi bi-inbox" style="font-size: 2rem; display: block; margin-bottom: 0.5rem;"></i>No files found. Upload a file to get started.</td></tr>';
    }
  }

  if (printers[id]['files'] !== undefined && printers[id]['files'].length > 0) {
    console.log('Displaying existing files for printer:', id, '(', printers[id]['files'].length, 'files)');
    createTable('Files', printers[id]['files'])
    addFileOptions()
  } else {
    console.log('Requesting files for printer:', id);
    getPrinterFiles(id, '/local')
    getPrinterFiles(id, '/usb')
  }

  $('#uploadPrinter').val(id)
  
  if (p.status && p.status.CurrentStatus && p.status.CurrentStatus.includes(SDCP_MACHINE_STATUS_PRINTING)) {
    updatePrintOverlay(id, p.status.PrintInfo)
  }
}

function createTable(name, data, active = false) {
  if ($('#tab-' + name).length == 0) {
    var tTab = $("#tmplNavTab").html()
    var tab = $(tTab)
    tab.find('button').attr('id', 'tab-' + name)
    tab.find('button').attr('data-bs-target', '#tab' + name)
    tab.find('button').text(name)
    if (active) {
      tab.find('button').addClass('active')
    }
    $('#navTabs').append(tab)
  }

  if ($('#tab' + name).length == 0) {
    var tPane = $("#tmplNavPane").html()
    var pane = $(tPane)
    pane.attr('id', 'tab' + name)
    pane.find('tbody').attr('id', 'table' + name)
    if (active) {
      pane.addClass('active')
    }
    $('#navPanes').append(pane)
  }
  fillTable(name, data)
}

function formatDisplayValue(key, val, tableName) {
  // Format Status tab values
  if (tableName === 'Status') {
    switch(key) {
      case 'CurrentStatus':
        if (Array.isArray(val) && val.length > 0) {
          var statusClass = 'idle';
          var statusText = 'Idle';

          switch(val[0]) {
            case SDCP_MACHINE_STATUS_IDLE:
              statusClass = 'idle';
              statusText = 'Idle';
              break;
            case SDCP_MACHINE_STATUS_PRINTING:
              statusClass = 'printing';
              statusText = 'Printing';
              break;
            case SDCP_MACHINE_STATUS_FILE_TRANSFERRING:
              statusClass = 'warning';
              statusText = 'File Transfer';
              break;
            case SDCP_MACHINE_STATUS_EXPOSURE_TESTING:
              statusClass = 'printing';
              statusText = 'Exposure Test';
              break;
            case SDCP_MACHINE_STATUS_DEVICES_TESTING:
              statusClass = 'warning';
              statusText = 'Device Testing';
              break;
            default:
              statusClass = 'warning';
              statusText = 'Unknown';
          }

          return '<span class="status-indicator ' + statusClass + '">●</span> ' + statusText;
        }
        return val;

      case 'PrintScreen':
        var hours = Math.floor(val / 3600);
        var mins = Math.floor((val % 3600) / 60);
        if (hours > 0) {
          return hours + 'h ' + mins + 'm';
        }
        return mins + 'm ' + Math.round(val % 60) + 's';

      case 'ReleaseFilm':
        return val.toLocaleString() + ' cycles';

      case 'TempOfUVLED':
        return val.toFixed(1) + ' °C';

      case 'TimeLapseStatus':
        return val === 1 ? 'Enabled' : 'Disabled';
    }
  }

  // Format Attributes tab values
  if (tableName === 'Attributes') {
    // Filter out PrintInfo object
    if (key === 'PrintInfo') {
      return null;
    }

    switch(key) {
      case 'RemainingMemory':
        var gb = (val / (1024 * 1024 * 1024)).toFixed(2);
        return gb + ' GB';

      case 'Resolution':
        return val;

      case 'XYZsize':
        return val;

      case 'Capabilities':
      case 'SupportFileType':
        if (Array.isArray(val)) {
          return val.join(', ');
        }
        return val;

      case 'DevicesStatus':
        if (typeof val === 'object') {
          var statuses = [];
          for (var device in val) {
            var deviceName = device.replace(/Status|State/g, '').replace(/([A-Z])/g, ' $1').trim();
            var statusIcon = val[device] === 1 ? '✓' : '✗';
            statuses.push(statusIcon + ' ' + deviceName);
          }
          return statuses.join('<br>');
        }
        return val;

      case 'NetworkStatus':
        return val.toUpperCase();

      case 'UsbDiskStatus':
      case 'SDCPStatus':
      case 'CameraStatus':
        return val === 1 ? '✓ Connected' : '○ Disconnected';

      case 'ReleaseFilmMax':
        return val.toLocaleString() + ' cycles';

      case 'NumberOfVideoStreamConnected':
      case 'MaximumVideoStreamAllowed':
      case 'NumberOfCloudSDCPServicesConnected':
      case 'MaximumCloudSDCPSercicesAllowed':
        return val;

      // Hide less useful fields
      case 'TLPNoCapPos':
      case 'TLPStartCapPos':
      case 'TLPInterLayers':
        return null;
    }
  }

  return val;
}

function fillTable(table, data) {
  var t = $('#table' + table);
  t.empty();

  // Special card-based UI for Status tab
  if (table === 'Status') {
    var grid = $('<div class="status-grid"></div>');

    $.each(data, function (key, val) {
      // Skip PrintInfo - it has its own Print tab
      if (key === 'PrintInfo') {
        return true;
      }

      // Format the value
      var formattedVal = formatDisplayValue(key, val, table);

      // Skip if formatDisplayValue returns null
      if (formattedVal === null) {
        return true;
      }

      // If still an object after formatting, stringify it
      if (typeof formattedVal === 'object') {
        formattedVal = JSON.stringify(formattedVal);
      }

      // Make key more readable
      var displayKey = key.replace(/([A-Z])/g, ' $1').trim();

      // Get icon for each status type
      var icon = '';
      switch(key) {
        case 'CurrentStatus':
          icon = '<i class="bi bi-circle-fill"></i>';
          break;
        case 'PrintScreen':
          icon = '<i class="bi bi-clock-history"></i>';
          break;
        case 'ReleaseFilm':
          icon = '<i class="bi bi-arrow-repeat"></i>';
          break;
        case 'TempOfUVLED':
          icon = '<i class="bi bi-thermometer-half"></i>';
          break;
        case 'TimeLapseStatus':
          icon = '<i class="bi bi-camera-video"></i>';
          break;
        default:
          icon = '<i class="bi bi-info-circle"></i>';
      }

      var card = $('<div class="status-card"></div>');
      card.append('<div class="status-card-label">' + icon + displayKey + '</div>');
      card.append('<div class="status-card-value">' + formattedVal + '</div>');
      grid.append(card);
    });

    t.append(grid);
    return;
  }

  // Special handling for Files tab (array of filenames)
  if (table === 'Files') {
    if (Array.isArray(data)) {
      $.each(data, function (index, filename) {
        var row = $('<tr data-file="' + filename + '"><td class="fieldKey">' + index + '</td><td class="fieldValue">' + filename + '</td></tr>');
        t.append(row);
      });
    }
    return;
  }

  // Regular table layout for other tabs (object with key-value pairs)
  $.each(data, function (key, val) {
    // Format the value
    var formattedVal = formatDisplayValue(key, val, table);

    // Skip if formatDisplayValue returns null
    if (formattedVal === null) {
      return true; // continue to next iteration
    }

    // If still an object after formatting, stringify it
    if (typeof formattedVal === 'object') {
      formattedVal = JSON.stringify(formattedVal);
    }

    // Make key more readable
    var displayKey = key.replace(/([A-Z])/g, ' $1').trim();

    var row = $('<tr><td class="fieldKey">' + displayKey + '</td><td class="fieldValue">' + formattedVal + '</td></tr>');
    t.append(row);
  });
}

function getPrinterFiles(id, url) {
  socket.emit("printer_files", { id: id, url: url })
}

function addFileOptions() {
  $('#tableFiles .fieldValue').each(function () {
    var file = $(this).text()
    var options = $('<i class="bi bi-printer-fill fileOption ps-3" data-action="print" data-file="' + file + '" data-bs-toggle="tooltip" title="Print this file"></i><i class="bi bi-download fileOption ps-1" data-action="download" data-file="' + file + '" data-bs-toggle="tooltip" title="Download file"></i><i class="bi bi-trash-fill fileOption ps-1" data-action="delete" data-file="' + file + '" data-bs-toggle="tooltip" title="Delete file"></i>')
    $(this).append(options)
    $(this).parent().attr('data-file', file)
  })
  $('.fileOption').on('click', function (e) {
    var action = $(this).data('action')
    var file = $(this).data('file')

    // Handle download action directly without confirmation
    if (action === 'download') {
      downloadFile(file)
      return
    }

    // Show confirmation modal for other actions
    $('#modalConfirmTitle').text('Confirm ' + action)
    $('#modalConfirmAction').text(action)
    $('#modalConfirmValue').text(file)
    $('#btnConfirm').data('action', action).data('value', file)
    modalConfirm.show()
  })

  // Initialize Bootstrap tooltips
  var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
  tooltipTriggerList.map(function (tooltipTriggerEl) {
    return new bootstrap.Tooltip(tooltipTriggerEl)
  })
}

function updatePrinterStatus(data) {
  var info = $('#printer_' + data.MainboardID).find('.printerInfo')
  switch (data.Status.CurrentStatus[0]) {
    case SDCP_MACHINE_STATUS_IDLE:
      info.text("Idle")
      updatePrinterStatusIcon(data.MainboardID, "success", false)
      break
    case SDCP_MACHINE_STATUS_PRINTING:
      info.text("Printing")
      updatePrinterStatusIcon(data.MainboardID, "success", true)
      break
    case SDCP_MACHINE_STATUS_FILE_TRANSFERRING:
      info.text("File Transfer")
      updatePrinterStatusIcon(data.MainboardID, "warning", true)
      break
    case SDCP_MACHINE_STATUS_EXPOSURE_TESTING:
      info.text("Exposure Test")
      updatePrinterStatusIcon(data.MainboardID, "info", true)
      break
    case SDCP_MACHINE_STATUS_DEVICES_TESTING:
      info.text("Devices Self-Test")
      updatePrinterStatusIcon(data.MainboardID, "warning", true)
      break
    case SDCP_MACHINE_STATUS_UNKNOWN_8:
      info.text("UNKNOWN STATUS")
      updatePrinterStatusIcon(data.MainboardID, "info", true)
      break
    default:
      break
  }
}

function updatePrinterStatusIcon(id, style, spinner) {
  var el = 'printerStatus'
  if (spinner) {
    el = 'printerSpinner'
    $('.printerStatus').addClass('visually-hidden')
    $('.printerSpinner').removeClass('visually-hidden')
  } else {
    $('.printerStatus').removeClass('visually-hidden')
    $('.printerSpinner').addClass('visually-hidden')
  }
  var status = $('#printer_' + id).find('.' + el)
  status.removeClass(function (index, css) {
    return (css.match(/\btext-\S+/g) || []).join(' ');
  }).addClass("text-" + style);
  status.find('i').removeClass().addClass('bi-circle-fill')
}

function setServerStatus(online) {
  serverStatus = $('.serverStatus')
  if (online) {
    serverStatus.removeClass('bi-cloud text-danger').addClass('bi-cloud-check-fill')
  } else {
    serverStatus.removeClass('bi-cloud-check-fill').addClass('bi-cloud text-danger')
  }
}

function updatePrintOverlay(printerId, printInfo) {
  if (!printInfo || printerId !== currentPrinter) return;
  
  if (!printStatusModal) {
    printStatusModal = new bootstrap.Modal($('#modalPrintStatus'), {
      backdrop: true,  // Allow closing by clicking backdrop
      keyboard: true   // Allow closing with Escape key
    });
  }
  
  if (!printers[printerId].printTracking) {
    printers[printerId].printTracking = {
      startTime: Date.now(),
      startLayer: printInfo.CurrentLayer || 0,
      lastUpdate: Date.now(),
      estimatedTotal: 0,
      thumbnailRequested: false,
      modalShownOnce: false
    };
  }
  
  var tracking = printers[printerId].printTracking;

  if (printInfo.TaskId && !tracking.thumbnailRequested) {
    console.log('Requesting task details for TaskId:', printInfo.TaskId);
    socket.emit('get_task_details', { id: printerId, taskId: printInfo.TaskId });
    tracking.thumbnailRequested = true;
  } else if (!printInfo.TaskId) {
    console.log('No TaskId in printInfo:', printInfo);
  }

  if (printers[printerId].currentPrint && printers[printerId].currentPrint.thumbnail) {
    displayThumbnail(printers[printerId].currentPrint.thumbnail);
  }
  
  // Always enable camera button when printing (it will start camera if needed)
  $('#btnPrintCamera').prop('disabled', false);
  
  var statusText = getPrintStatusText(printInfo.Status);
  $('#printStatusText').text(statusText);
  
  $('#printFileName').text(printInfo.Filename || 'Unknown');
  
  var currentLayer = printInfo.CurrentLayer || 0;
  var totalLayers = printInfo.TotalLayer || 0;
  $('#printLayers').text(currentLayer + ' / ' + totalLayers);
  
  var percentage = totalLayers > 0 ? Math.round((currentLayer / totalLayers) * 100) : 0;
  $('#printProgress').css('width', percentage + '%').text(percentage + '%');

  // Use CurrentTicks and TotalTicks directly from printer (like SdcpMonitor)
  // This matches what the printer display shows
  var currentTime = formatTime(printInfo.CurrentTicks || 0);
  var totalTime = formatTime(printInfo.TotalTicks || 0);
  var remainingTime = (printInfo.TotalTicks || 0) - (printInfo.CurrentTicks || 0);
  var remainingTimeText = remainingTime > 0 ? formatTime(remainingTime) : '--:--:--';

  // Display format: "elapsed / total" (matches printer display)
  $('#printTime').text(currentTime + ' / ' + totalTime);
  $('#printTotalTime').text('Remaining: ' + remainingTimeText);
  
  tracking.lastUpdate = Date.now();
  
  if (printInfo.ErrorNumber && printInfo.ErrorNumber !== 0) {
    var errorText = getPrintErrorText(printInfo.ErrorNumber);
    $('#printError').text('Error: ' + errorText).removeClass('d-none');
  } else {
    $('#printError').addClass('d-none');
  }
  
  updatePrintControls(printInfo.Status);

  if (printInfo.Status !== SDCP_PRINT_STATUS_IDLE &&
      printInfo.Status !== SDCP_PRINT_STATUS_COMPLETE) {
    // Show main dashboard print controls card
    $('#printControlsRow').removeClass('d-none');

    // Update main dashboard card
    $('#mainPrintFileName').text(printInfo.Filename || 'Unknown');
    $('#mainPrintStatus').text(statusText);
    $('#mainPrintProgress').css('width', percentage + '%').text(percentage + '%');
    $('#mainPrintLayers').text(currentLayer + ' / ' + totalLayers);
    $('#mainPrintTime').text(currentTime + ' / ' + totalTime);

    // Update main dashboard pause/resume buttons
    if (printInfo.Status === SDCP_PRINT_STATUS_PAUSED) {
      $('#btnMainPausePrint').addClass('d-none');
      $('#btnMainResumePrint').removeClass('d-none');
    } else {
      $('#btnMainPausePrint').removeClass('d-none');
      $('#btnMainResumePrint').addClass('d-none');
    }

    // Auto-show modal on first print start (with small delay to allow data to load)
    if (!$('#modalPrintStatus').hasClass('show') && !tracking.modalShownOnce) {
      tracking.modalShownOnce = true;
      // Small delay to ensure data is ready, especially on mobile
      setTimeout(function() {
        // Only show if still printing and modal wasn't manually opened
        if (!$('#modalPrintStatus').hasClass('show')) {
          printStatusModal.show();
        }
      }, 500); // 500ms delay
    }
  } else {
    // Hide main card when print is complete/idle
    $('#printControlsRow').addClass('d-none');
    delete printers[printerId].printTracking;
    delete printers[printerId].currentPrint;
  }
}

function displayThumbnail(thumbnailUrl) {
  console.log('=== displayThumbnail ===');
  console.log('Thumbnail URL:', thumbnailUrl);

  if (thumbnailUrl && thumbnailUrl !== '') {
    var finalUrl = thumbnailUrl;

    // Check if it's an HTTP URL (not a data URI)
    if (thumbnailUrl.startsWith('http://') || thumbnailUrl.startsWith('https://')) {
      console.log('Thumbnail is HTTP URL, proxying through backend');
      // Proxy through backend to avoid CORS issues
      finalUrl = '/thumbnail/' + currentPrinter + '?url=' + encodeURIComponent(thumbnailUrl);
      console.log('Proxied URL:', finalUrl);
    } else if (thumbnailUrl.startsWith('data:')) {
      console.log('Thumbnail is data URI');
    } else {
      console.warn('Unknown thumbnail URL format:', thumbnailUrl.substring(0, 50));
    }

    console.log('Setting thumbnail src and showing image');
    $('#printThumbnail').attr('src', finalUrl);
    $('#printThumbnail').removeClass('d-none');
    $('#printThumbnailPlaceholder').addClass('d-none');
    console.log('Thumbnail display initiated');
  } else {
    console.log('No thumbnail URL, showing placeholder');
    // Show placeholder
    $('#printThumbnail').addClass('d-none');
    $('#printThumbnailPlaceholder').removeClass('d-none');
  }
  console.log('=== End displayThumbnail ===');
}

function getPrintStatusText(status) {
  switch(status) {
    case SDCP_PRINT_STATUS_IDLE: return 'Idle';
    case SDCP_PRINT_STATUS_HOMING: return 'Homing';
    case SDCP_PRINT_STATUS_DROPPING: return 'Descending';
    case SDCP_PRINT_STATUS_EXPOSURING: return 'Exposing';
    case SDCP_PRINT_STATUS_LIFTING: return 'Lifting';
    case SDCP_PRINT_STATUS_PAUSING: return 'Pausing...';
    case SDCP_PRINT_STATUS_PAUSED: return 'Paused';
    case SDCP_PRINT_STATUS_STOPPING: return 'Stopping...';
    case SDCP_PRINT_STATUS_STOPED: return 'Stopped';
    case SDCP_PRINT_STATUS_COMPLETE: return 'Complete!';
    case SDCP_PRINT_STATUS_FILE_CHECKING: return 'Checking File...';
    default: return 'Unknown Status';
  }
}

function getPrintErrorText(errorNumber) {
  switch(errorNumber) {
    case SDCP_PRINT_ERROR_NONE: return 'None';
    case SDCP_PRINT_ERROR_CHECK: return 'File MD5 Check Failed';
    case SDCP_PRINT_ERROR_FILEIO: return 'File Read Failed';
    case SDCP_PRINT_ERROR_INVLAID_RESOLUTION: return 'Resolution Mismatch';
    case SDCP_PRINT_ERROR_UNKNOWN_FORMAT: return 'Format Mismatch';
    case SDCP_PRINT_ERROR_UNKNOWN_MODEL: return 'Machine Model Mismatch';
    default: return 'Unknown Error (' + errorNumber + ')';
  }
}

function formatTime(milliseconds) {
  var seconds = Math.floor(milliseconds / 1000);
  var hours = Math.floor(seconds / 3600);
  var minutes = Math.floor((seconds % 3600) / 60);
  var secs = seconds % 60;
  
  return hours.toString().padStart(2, '0') + ':' + 
         minutes.toString().padStart(2, '0') + ':' + 
         secs.toString().padStart(2, '0');
}

function updatePrintControls(status) {
  var btnPause = $('#btnPausePrint');
  var btnResume = $('#btnResumePrint');
  var btnStop = $('#btnStopPrint');
  
  switch(status) {
    case SDCP_PRINT_STATUS_PAUSED:
      btnPause.addClass('d-none');
      btnResume.removeClass('d-none');
      btnStop.removeClass('d-none');
      break;
    case SDCP_PRINT_STATUS_PAUSING:
    case SDCP_PRINT_STATUS_STOPPING:
      btnPause.addClass('d-none').prop('disabled', true);
      btnResume.addClass('d-none').prop('disabled', true);
      btnStop.addClass('d-none').prop('disabled', true);
      break;
    case SDCP_PRINT_STATUS_IDLE:
    case SDCP_PRINT_STATUS_COMPLETE:
    case SDCP_PRINT_STATUS_STOPED:
      btnPause.addClass('d-none');
      btnResume.addClass('d-none');
      btnStop.addClass('d-none');
      break;
    default:
      btnPause.removeClass('d-none').prop('disabled', false);
      btnResume.addClass('d-none');
      btnStop.removeClass('d-none').prop('disabled', false);
  }
}

// ============ CAMERA CONTROLS ============

$('#btnStartCamera').on('click', function() {
  console.log('Starting camera...');
  $(this).prop('disabled', true);
  $('#cameraStatus').text('Connecting...');
  
  $.ajax({
    url: '/camera/start',
    type: 'POST',
    success: function(response) {
      if (response.ok) {
        console.log('Camera started successfully');
        cameraActive = true;
        $('#btnStartCamera').prop('disabled', true);
        $('#btnStopCamera').prop('disabled', false);
        $('#btnFullscreenCamera').prop('disabled', false);
        $('#btnPrintCamera').prop('disabled', false);
        $('#cameraPlaceholder').hide();
        $('#cameraStream').show().attr('src', '/camera/video');
        $('#cameraStatus').text('Streaming');
      } else {
        console.error('Camera start failed:', response.msg);
        alert('Camera Error: ' + response.msg);
        $('#btnStartCamera').prop('disabled', false);
        $('#cameraStatus').text(response.msg);
        cameraActive = false;
      }
    },
    error: function(xhr, status, error) {
      console.error('Camera start error:', error);
      alert('Failed to start camera: ' + error);
      $('#btnStartCamera').prop('disabled', false);
      $('#cameraStatus').text('Error starting camera');
      cameraActive = false;
    }
  });
});

$('#btnStopCamera').on('click', function() {
  console.log('Stopping camera...');
  
  // Close fullscreen modal if open
  if (cameraFullscreenModal && $('#modalCameraFullscreen').hasClass('show')) {
    cameraFullscreenModal.hide();
  }
  
  // Update UI immediately
  cameraActive = false;
  $('#btnStartCamera').prop('disabled', false);
  $('#btnStopCamera').prop('disabled', true);
  $('#btnFullscreenCamera').prop('disabled', true);
  $('#btnPrintCamera').prop('disabled', true);
  $('#cameraStream').hide().attr('src', '');
  $('#cameraPlaceholder').show();
  $('#cameraStatus').text('Camera stopped');
  
  // Send stop request to server
  $.ajax({
    url: '/camera/stop',
    type: 'POST',
    success: function(response) {
      console.log('Camera stopped successfully');
    },
    error: function(xhr, status, error) {
      console.log('Camera stop request failed (but UI updated):', error);
    }
  });
});

// Camera fullscreen button handler
$('#btnFullscreenCamera').on('click', function() {
  if (!cameraFullscreenModal) {
    cameraFullscreenModal = new bootstrap.Modal($('#modalCameraFullscreen'), {});
  }
  
  // Start streaming to fullscreen modal
  $('#cameraStreamFullscreen').attr('src', '/camera/video');
  cameraFullscreenModal.show();
});

// Clean up fullscreen stream when modal closes
$('#modalCameraFullscreen').on('hidden.bs.modal', function() {
  $('#cameraStreamFullscreen').attr('src', '');
});

// Restore print overlay data when reopened
$('#modalPrintStatus').on('show.bs.modal', function() {
  console.log('Print modal opening, restoring data...');

  // Restore thumbnail if it exists
  if (currentPrinter && printers[currentPrinter] && printers[currentPrinter].currentPrint) {
    var currentPrint = printers[currentPrinter].currentPrint;

    if (currentPrint.thumbnail) {
      console.log('Restoring thumbnail from stored data');
      displayThumbnail(currentPrint.thumbnail);
    }
  }
});

// Reset print overlay when closed
$('#modalPrintStatus').on('hidden.bs.modal', function() {
  // Reset camera/thumbnail view
  $('#printCameraContainer').addClass('d-none');
  $('#printThumbnailContainer').removeClass('d-none');
  $('#btnPrintCamera').html('<i class="bi bi-camera-video"></i> Camera');

  // Clear thumbnail (but keep data in printers object)
  $('#printThumbnail').addClass('d-none').attr('src', '');
  $('#printThumbnailPlaceholder').removeClass('d-none');
});

// Print overlay camera button
$('#btnPrintCamera').on('click', function() {
  // Toggle camera view in the print overlay
  if ($('#printCameraContainer').hasClass('d-none')) {
    // Show camera view, hide thumbnail
    if (!cameraActive) {
      // Start camera first
      console.log('Starting camera from print overlay...');
      $(this).prop('disabled', true).html('<i class="bi bi-hourglass-split"></i> Starting...');

      $.ajax({
        url: '/camera/start',
        type: 'POST',
        success: function(response) {
          if (response.ok) {
            console.log('Camera started successfully');
            cameraActive = true;
            $('#btnStartCamera').prop('disabled', true);
            $('#btnStopCamera').prop('disabled', false);
            $('#btnFullscreenCamera').prop('disabled', false);
            $('#cameraPlaceholder').hide();
            $('#cameraStream').show().attr('src', '/camera/video');
            $('#cameraStatus').text('Streaming');

            // Show camera in print overlay
            $('#printCameraView').attr('src', '/camera/video?' + new Date().getTime());
            $('#printCameraContainer').removeClass('d-none');
            $('#printThumbnailContainer').addClass('d-none');
            $('#btnPrintCamera').prop('disabled', false).html('<i class="bi bi-image"></i> Thumbnail');
          } else {
            console.error('Camera start failed:', response.msg);
            alert('Camera Error: ' + response.msg);
            $('#btnPrintCamera').prop('disabled', false).html('<i class="bi bi-camera-video"></i> Camera');
          }
        },
        error: function(xhr, status, error) {
          console.error('Camera start error:', error);
          alert('Failed to start camera: ' + error);
          $('#btnPrintCamera').prop('disabled', false).html('<i class="bi bi-camera-video"></i> Camera');
        }
      });
    } else {
      // Camera already active, just show it
      $('#printCameraView').attr('src', '/camera/video?' + new Date().getTime());
      $('#printCameraContainer').removeClass('d-none');
      $('#printThumbnailContainer').addClass('d-none');
      $(this).html('<i class="bi bi-image"></i> Thumbnail');
    }
  } else {
    // Hide camera view, show thumbnail
    $('#printCameraContainer').addClass('d-none');
    $('#printThumbnailContainer').removeClass('d-none');
    $(this).html('<i class="bi bi-camera-video"></i> Camera');
  }
});

// ============ FILE UPLOAD ============

$('#btnUpload').on('click', function () {
  uploadFile()
});

// Upload destination radio button handlers
$('input[name="destination"]').on('change', function() {
  const destination = $(this).val();
  $('#uploadDestPath').val(destination);
  console.log('Upload destination changed to:', destination);
});

function uploadFile() {
  // Generate a unique upload ID for progress tracking
  var uploadId = generateUUID();

  // Get the form data and add the upload ID
  var formData = new FormData($('#formUpload')[0]);
  formData.append('upload_id', uploadId);

  // Show and reset progress bar
  $('.progress-enhanced').show();
  $('#progressUpload').text('0%').css('width', '0%');

  // Start progress tracking BEFORE starting upload to avoid race condition
  var progressEventSource = null;
  var progressStarted = false;

  var req = $.ajax({
    url: '/upload',
    type: 'POST',
    data: formData,
    cache: false,
    contentType: false,
    processData: false,
    xhr: function () {
      var myXhr = $.ajaxSettings.xhr();
      if (myXhr.upload) {
        myXhr.upload.addEventListener('progress', function (e) {
          if (e.lengthComputable) {
            var percent = Math.floor(e.loaded / e.total * 100);
            $('#progressUpload').text('Upload to ChitUI: ' + percent + '%').css('width', percent + '%');

            // Start server-to-printer progress tracking when client upload reaches 50%
            // This gives time for EventSource to connect before server upload begins
            if (percent >= 50 && !progressStarted) {
              progressStarted = true;
              progressEventSource = fileTransferProgress(uploadId);
            }
          }
        }, false);
      }
      return myXhr;
    }
  })
  req.done(function (data) {
    $('#uploadFile').val('')

    // Show success toast with appropriate message
    var toastMsg = '✓ File uploaded successfully!';
    if (data.usb_gadget) {
      if (data.refresh_triggered) {
        toastMsg = '✓ File saved to USB gadget. Checking printer...';
      } else {
        toastMsg = '⚠ File saved. You may need to reconnect USB or refresh on printer.';
      }
    }

    $("#toastUploadText").text(toastMsg);
    $("#toastUpload").show()
    setTimeout(function () {
      $("#toastUpload").hide()
    }, 5000)

    // Handle file list refresh based on upload type
    if (data.usb_gadget) {
      // USB gadget upload - use retry logic since printer may need time to detect
      console.log('USB gadget upload detected, starting retry logic...')
      refreshFileListWithRetry(data.filename, 0)
    } else {
      // Network upload - single refresh is usually sufficient
      setTimeout(function() {
        if (currentPrinter) {
          console.log('Refreshing file list after network upload...')
          refreshFileList()
        }
      }, 1000)
    }
  })
  req.fail(function (xhr, status, error) {
    // Close progress EventSource if it was started
    if (progressEventSource) {
      progressEventSource.close();
    }

    // Reset progress bar
    $('#progressUpload').text('0%').css('width', '0%')
      .removeClass('progress-bar-striped progress-bar-animated text-bg-warning');

    // Better error handling - check if responseJSON exists
    var errorMsg = 'Upload failed';
    if (xhr.responseJSON && xhr.responseJSON.msg) {
      errorMsg = xhr.responseJSON.msg;
    } else if (xhr.responseText) {
      try {
        var response = JSON.parse(xhr.responseText);
        errorMsg = response.msg || errorMsg;
      } catch (e) {
        errorMsg = xhr.responseText || errorMsg;
      }
    } else if (error) {
      errorMsg = error;
    }

    alert(errorMsg);
  })
  req.always(function () {
    // Cleanup if needed
  })
}

// Helper function to generate UUID
function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

function fileTransferProgress(uploadId) {
  $('#progressUpload').addClass('progress-bar-striped progress-bar-animated')

  // Use WebSocket for progress updates (more reliable than SSE)
  var progressHandler = function(data) {
    // Only handle events for this specific upload
    if (data.upload_id !== uploadId) return;

    var progressValue = parseInt(data.progress);
    console.log('Upload progress:', progressValue + '%');

    if (progressValue > 0) {
      $('#progressUpload').text('Upload to printer: ' + progressValue + '%').css('width', progressValue + '%').addClass('text-bg-warning');
    }
    if (progressValue >= 100) {
      setTimeout(function () {
        $('#progressUpload').text('0%').css('width', '0%');
        setTimeout(function () {
          $('#progressUpload').removeClass('progress-bar-striped progress-bar-animated text-bg-warning')
        }, 1000)
      }, 1000)
      // Remove the listener after completion
      socket.off('upload_progress', progressHandler);
    }
  };

  // Listen for progress events
  socket.on('upload_progress', progressHandler);

  // Return an object with a close method for compatibility
  return {
    close: function() {
      socket.off('upload_progress', progressHandler);
    },
    handler: progressHandler
  };
}

// ============ FILE DOWNLOAD ============

function downloadFile(filename) {
  /**
   * Download a file from the printer's storage
   * Constructs download URL and triggers browser download
   */
  if (!currentPrinter) {
    alert('No printer selected');
    return;
  }

  var printer = printers[currentPrinter];
  if (!printer) {
    alert('Printer not found');
    return;
  }

  // Construct the download URL
  var downloadUrl = '/download/' + currentPrinter + '/' + encodeURIComponent(filename);

  console.log('Downloading file:', filename, 'from printer:', currentPrinter);

  // Trigger download by creating a temporary anchor element
  var link = document.createElement('a');
  link.href = downloadUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

// ============ FILE LIST REFRESH HELPERS ============

function refreshFileList() {
  /**
   * Simple file list refresh - clears cache and requests fresh lists
   */
  if (!currentPrinter) return;

  // Clear the cached files list
  if (printers[currentPrinter]) {
    printers[currentPrinter]['files'] = []
  }

  // Request fresh file list from both USB and local
  getPrinterFiles(currentPrinter, '/usb')
  getPrinterFiles(currentPrinter, '/local')
}

function refreshFileListWithRetry(uploadedFilename, attemptNumber) {
  /**
   * Retry file list refresh for USB gadget uploads
   * The printer may need time to detect the new file
   *
   * @param {string} uploadedFilename - Name of the uploaded file to look for
   * @param {number} attemptNumber - Current retry attempt (0-based)
   */
  var maxAttempts = 5;
  var delays = [2000, 3000, 5000, 7000, 10000]; // Progressive delays in milliseconds

  if (attemptNumber >= maxAttempts) {
    console.log('Max retry attempts reached for file list refresh')
    $("#toastUploadText").text('⚠ File saved but not detected yet. Try refreshing the printer screen or reconnecting USB.');
    $("#toastUpload").show()
    setTimeout(function () {
      $("#toastUpload").hide()
    }, 5000)
    return;
  }

  var delay = delays[attemptNumber] || 10000;

  console.log('Refreshing file list (attempt ' + (attemptNumber + 1) + '/' + maxAttempts + ') in ' + (delay/1000) + 's...')

  setTimeout(function() {
    if (!currentPrinter) {
      console.log('No current printer, stopping retry')
      return;
    }

    // Clear the cached files list
    if (printers[currentPrinter]) {
      printers[currentPrinter]['files'] = []
    }

    // Store current file count to detect if new file appeared
    var fileCountBefore = printers[currentPrinter] && printers[currentPrinter]['files']
      ? printers[currentPrinter]['files'].length
      : 0;

    // Request fresh file list from both USB and local
    getPrinterFiles(currentPrinter, '/usb')
    getPrinterFiles(currentPrinter, '/local')

    // Wait a bit for the file list to come back, then check if file appeared
    setTimeout(function() {
      var fileFound = false;
      if (printers[currentPrinter] && printers[currentPrinter]['files']) {
        fileFound = printers[currentPrinter]['files'].some(function(file) {
          return file.name === uploadedFilename;
        });
      }

      if (fileFound) {
        console.log('✓ File detected on printer!')
        $("#toastUploadText").text('✓ File detected on printer!');
        $("#toastUpload").show()
        setTimeout(function () {
          $("#toastUpload").hide()
        }, 3000)
      } else {
        // File not found, retry
        console.log('File not detected yet, will retry...')
        refreshFileListWithRetry(uploadedFilename, attemptNumber + 1)
      }
    }, 1500); // Wait 1.5s for printer response

  }, delay);
}

// ============ UI EVENT HANDLERS ============

$('.serverStatus').on('mouseenter', function (e) {
  if ($(this).hasClass('bi-cloud-check-fill')) {
    $(this).removeClass('bi-cloud-check-fill').addClass('bi-cloud-plus text-primary')
  }
});

$('.serverStatus').on('mouseleave', function (e) {
  $(this).removeClass('bi-cloud-plus text-primary').addClass('bi-cloud-check-fill')
});

$('.serverStatus').on('click', function (e) {
  socket.emit("printers", "{}")
});

$('#toastUpload .btn-close').on('click', function (e) {
  $("#toastUpload").hide()
});

var modalConfirm;
$(document).ready(function () {
  modalConfirm = new bootstrap.Modal($('#modalConfirm'), {});

  // Fetch default printer setting
  $.ajax({
    url: '/settings',
    method: 'GET',
    success: function(data) {
      console.log('Settings loaded:', data);
      console.log('default_printer from settings:', data.default_printer);
      if (data && data.default_printer) {
        defaultPrinterId = data.default_printer;
        console.log('✓ Default printer setting loaded:', defaultPrinterId);
      } else {
        console.log('✗ No default printer set in settings');
      }
      defaultPrinterLoaded = true
      tryAutoSelectDefaultPrinter()
    },
    error: function(xhr, status, error) {
      console.log('✗ Could not load default printer setting:', error);
      defaultPrinterLoaded = true  // Mark as loaded even on error
      tryAutoSelectDefaultPrinter()
    }
  });

  // Thumbnail image load/error handlers for debugging
  $('#printThumbnail').on('load', function() {
    console.log('✓ Thumbnail image loaded successfully');
    console.log('Image src:', $(this).attr('src').substring(0, 100));
    console.log('Image dimensions:', this.naturalWidth + 'x' + this.naturalHeight);
  });

  $('#printThumbnail').on('error', function() {
    console.error('✗ Thumbnail image failed to load');
    console.error('Failed src:', $(this).attr('src'));
    // Show placeholder on error
    $(this).addClass('d-none');
    $('#printThumbnailPlaceholder').removeClass('d-none');
  });
});

$('#btnConfirm').on('click', function () {
  var action = $(this).data('action')
  var value = $(this).data('value')
  socket.emit('action_' + action, { id: currentPrinter, data: value })

  // Hide the confirmation modal
  modalConfirm.hide()

  // If deleting, refresh the file list after a short delay
  if (action === 'delete') {
    setTimeout(function() {
      if (currentPrinter) {
        // Clear the cached files list
        if (printers[currentPrinter]) {
          printers[currentPrinter]['files'] = []
        }
        // Request fresh file list from both locations
        getPrinterFiles(currentPrinter, '/local')
        getPrinterFiles(currentPrinter, '/usb')
      }
    }, 500)
  }
});

// Print control button handlers
$('#btnPausePrint').on('click', function () {
  if (currentPrinter) {
    socket.emit('action_pause', { id: currentPrinter })
  }
});

$('#btnResumePrint').on('click', function () {
  if (currentPrinter) {
    socket.emit('action_resume', { id: currentPrinter })
  }
});

$('#btnStopPrint').on('click', function () {
  if (currentPrinter && confirm('Are you sure you want to stop the print?')) {
    socket.emit('action_stop', { id: currentPrinter })
  }
});

// Main dashboard "Show Details" button
$('#btnShowPrintStatusMain').on('click', function () {
  if (printStatusModal) {
    printStatusModal.show();
  }
});

// Main dashboard print control buttons
$('#btnMainPausePrint').on('click', function () {
  if (currentPrinter) {
    socket.emit('action_pause', { id: currentPrinter });
  }
});

$('#btnMainResumePrint').on('click', function () {
  if (currentPrinter) {
    socket.emit('action_resume', { id: currentPrinter });
  }
});

$('#btnMainStopPrint').on('click', function () {
  if (currentPrinter && confirm('Are you sure you want to stop the print?')) {
    socket.emit('action_stop', { id: currentPrinter });
  }
});

$('#btnCleanStorage').on('click', function() {
  if (!currentPrinter) {
    alert('Please select a printer first');
    return;
  }
  
  if (confirm('This will delete all print history and cached data.\n\nYour .goo files will NOT be deleted.\n\nContinue?')) {
    const $btn = $(this);
    const originalText = $btn.html();
    
    $btn.prop('disabled', true).html('<i class="bi bi-hourglass-split"></i> Cleaning...');
    
    // Send command to clear history
    socket.emit('action_clear_history', { id: currentPrinter });
    
    // Re-enable button after a delay
    setTimeout(function() {
      $btn.prop('disabled', false).html(originalText);
      
      // Request updated attributes to refresh storage display
      socket.emit('get_attributes', { id: currentPrinter });
      
      alert('Storage cleanup initiated. Storage info will update in a moment.');
    }, 3000);
  }
});

$('#btnWipeStorage').on('click', function() {
  if (!currentPrinter) {
    alert('Please select a printer first');
    return;
  }
  
  if (!confirm('⚠️ WARNING: This will FORMAT the printer\'s local storage!\n\nThis will delete:\n• All .goo files\n• Print history\n• Thumbnails\n• Everything on /local\n\nThis action CANNOT be undone!\n\nAre you ABSOLUTELY sure?')) {
    return;
  }
  
  // Double confirmation
  const confirmText = prompt('Type "FORMAT" to confirm:');
  if (confirmText !== 'FORMAT') {
    alert('Cancelled. Storage was not formatted.');
    return;
  }
  
  const $btn = $(this);
  const originalText = $btn.html();
  
  $btn.prop('disabled', true).html('<i class="bi bi-hourglass-split"></i> Formatting...');
  
  // Send command to format local storage
  socket.emit('action_wipe_storage', { id: currentPrinter });
  
  // Re-enable button after a delay
  setTimeout(function() {
    $btn.prop('disabled', false).html(originalText);
    
    // Request updated file list and attributes
    getPrinterFiles(currentPrinter, '/local');
    getPrinterFiles(currentPrinter, '/usb');
    socket.emit('get_attributes', { id: currentPrinter });
    
    alert('Local storage has been formatted.');
  }, 5000);
});

// ============ INITIALIZATION ============

// Force dark mode
document.documentElement.setAttribute('data-bs-theme', 'dark');

// Mobile menu toggle
const mobileMenuToggle = document.getElementById('mobileMenuToggle');
const sidebar = document.querySelector('.app-sidebar');
const sidebarOverlay = document.getElementById('sidebarOverlay');

function toggleSidebar() {
  // Don't toggle if sidebar is pinned
  if (sidebar.classList.contains('pinned')) {
    return;
  }
  sidebar.classList.toggle('show');
  sidebarOverlay.classList.toggle('show');
  document.body.style.overflow = sidebar.classList.contains('show') ? 'hidden' : '';
}

if (mobileMenuToggle) {
  mobileMenuToggle.addEventListener('click', function() {
    // Show sidebar even if pinned when using mobile menu toggle
    if (sidebar.classList.contains('pinned')) {
      sidebar.classList.remove('show');
      sidebarOverlay.classList.remove('show');
      document.body.style.overflow = '';
    } else {
      toggleSidebar();
    }
  });
}

if (sidebarOverlay) {
  sidebarOverlay.addEventListener('click', toggleSidebar);
}

// Sidebar pin functionality
const sidebarPinBtn = document.getElementById('sidebarPinBtn');
const appContent = document.querySelector('.app-content');
let sidebarPinned = localStorage.getItem('sidebarPinned') === 'true';

function updateSidebarPinState() {
  const pinIcon = sidebarPinBtn.querySelector('i');
  const isMobile = window.innerWidth <= 768;

  // On mobile, ignore pinned state - always hide sidebar
  if (isMobile) {
    sidebar.classList.remove('pinned');
    sidebar.classList.remove('show');
    appContent.classList.remove('sidebar-pinned');
    sidebarOverlay.classList.remove('show');
    document.body.style.overflow = '';
    // Keep pin icon state but don't apply pinned class
    pinIcon.className = sidebarPinned ? 'bi bi-pin-fill' : 'bi bi-pin-angle';
    sidebarPinBtn.title = sidebarPinned ? 'Unpin sidebar' : 'Pin sidebar';
    return;
  }

  // Desktop behavior - apply pinned state normally
  if (sidebarPinned) {
    sidebar.classList.add('pinned');
    sidebar.classList.remove('show');
    appContent.classList.add('sidebar-pinned');
    sidebarOverlay.classList.remove('show');
    document.body.style.overflow = '';
    pinIcon.className = 'bi bi-pin-fill';
    sidebarPinBtn.title = 'Unpin sidebar';
  } else {
    sidebar.classList.remove('pinned');
    appContent.classList.remove('sidebar-pinned');
    pinIcon.className = 'bi bi-pin-angle';
    sidebarPinBtn.title = 'Pin sidebar';
  }
}

if (sidebarPinBtn) {
  // Load saved pin state on page load
  updateSidebarPinState();

  // Update on window resize (when switching between mobile/desktop)
  window.addEventListener('resize', updateSidebarPinState);

  sidebarPinBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    sidebarPinned = !sidebarPinned;
    localStorage.setItem('sidebarPinned', sidebarPinned);
    updateSidebarPinState();
  });
}

// File upload drag and drop
const uploadArea = document.querySelector('.upload-area');
const fileInput = document.getElementById('uploadFile');
const uploadButton = document.getElementById('btnUpload');
const progressContainer = document.querySelector('.progress-enhanced');

if (uploadArea && fileInput) {
  uploadArea.addEventListener('click', (e) => {
    if (!e.target.closest('button')) {
      fileInput.click();
    }
  });
  
  uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.classList.add('drag-over');
  });

  uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('drag-over');
  });

  uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('drag-over');
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      handleFileSelect();
    }
  });

  fileInput.addEventListener('change', handleFileSelect);
}

function handleFileSelect() {
  if (fileInput.files.length > 0) {
    const fileName = fileInput.files[0].name;
    uploadArea.querySelector('h5').textContent = fileName;
    if (uploadButton) uploadButton.style.display = 'block';

    // Show destination selector (but hide USB option if gadget disabled)
    const uploadDestination = document.getElementById('uploadDestination');
    if (uploadDestination) {
      uploadDestination.style.display = 'block';

      // Check if USB gadget is available
      fetch('/status')
        .then(response => response.json())
        .then(data => {
          const usbOption = document.getElementById('destUsb');
          const usbLabel = usbOption?.parentElement;

          if (!data.usb_gadget.enabled && usbLabel) {
            // Hide USB option if gadget is disabled
            usbLabel.style.display = 'none';
            // Force selection to Local Storage
            document.getElementById('destLocal').checked = true;
            document.getElementById('uploadDestPath').value = 'local';
          } else if (usbLabel) {
            usbLabel.style.display = 'block';
          }
        })
        .catch(err => console.error('Failed to check USB status:', err));
    }
  }
}

// File search functionality
const fileSearchInput = document.getElementById('fileSearchInput');
if (fileSearchInput) {
  fileSearchInput.addEventListener('input', (e) => {
    const searchTerm = e.target.value.toLowerCase();
    const fileRows = document.querySelectorAll('#fileManagerBody tr');
    
    fileRows.forEach(row => {
      if (row.cells.length === 1) return;
      const fileName = row.cells[1]?.textContent.toLowerCase() || '';
      row.style.display = fileName.includes(searchTerm) ? '' : 'none';
    });
  });
}

// Refresh files button
const btnRefreshFiles = document.getElementById('btnRefreshFiles');
if (btnRefreshFiles) {
  btnRefreshFiles.addEventListener('click', () => {
    console.log('Refreshing files...');
    if (currentPrinter) {
      // Clear the cached files list
      if (printers[currentPrinter]) {
        printers[currentPrinter]['files'] = []
      }
      // Request fresh file list from both locations
      getPrinterFiles(currentPrinter, '/local')
      getPrinterFiles(currentPrinter, '/usb')
    }
  });
}

// Refresh PI USB button - manually trigger USB gadget reload
const btnRefreshPI = document.getElementById('btnRefreshPI');
if (btnRefreshPI) {
  btnRefreshPI.addEventListener('click', () => {
    console.log('Refreshing PI USB gadget...');

    // Disable button and show loading state
    const originalHTML = btnRefreshPI.innerHTML;
    btnRefreshPI.disabled = true;
    btnRefreshPI.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Refreshing...';

    // Call the backend endpoint
    fetch('/usb-gadget/refresh', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      }
    })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        console.log('USB gadget refresh successful');

        // Start countdown timer
        let countdown = 3;
        btnRefreshPI.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Refreshing... ' + countdown + 's';

        const countdownInterval = setInterval(() => {
          countdown--;
          if (countdown > 0) {
            btnRefreshPI.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Refreshing... ' + countdown + 's';
          } else {
            clearInterval(countdownInterval);
            btnRefreshPI.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading files...';
          }
        }, 1000);

        // Refresh file list after a delay to allow printer to detect changes
        setTimeout(() => {
          if (currentPrinter) {
            if (printers[currentPrinter]) {
              printers[currentPrinter]['files'] = [];
            }
            getPrinterFiles(currentPrinter, '/local');
            getPrinterFiles(currentPrinter, '/usb');
          }
          // Re-enable button and restore original text
          btnRefreshPI.disabled = false;
          btnRefreshPI.innerHTML = originalHTML;
        }, 3000);
      } else {
        console.error('USB gadget refresh failed:', data.message);
        alert('Failed to refresh USB gadget: ' + data.message);
        // Re-enable button and restore original text
        btnRefreshPI.disabled = false;
        btnRefreshPI.innerHTML = originalHTML;
      }
    })
    .catch(error => {
      console.error('Error refreshing USB gadget:', error);
      alert('Error refreshing USB gadget: ' + error.message);
      // Re-enable button and restore original text
      btnRefreshPI.disabled = false;
      btnRefreshPI.innerHTML = originalHTML;
    });
  });
}

// Monitor for files being added to the Files tab
function syncFilesToFileManager() {
  console.log('Initializing file sync...');
  
  const observer = new MutationObserver(() => {
    const fileTable = document.getElementById('tableFiles');
    if (fileTable && fileTable.children.length > 0) {
      syncTableToFileManager(fileTable);
    }
    hideFilesTab();
  });
  
  observer.observe(document.body, { 
    childList: true, 
    subtree: true
  });
  
  setInterval(() => {
    const fileTable = document.getElementById('tableFiles');
    if (fileTable && fileTable.children.length > 0) {
      syncTableToFileManager(fileTable);
    }
    hideFilesTab();
  }, 2000);
}

function hideFilesTab() {
  const filesTab = document.getElementById('tab-Files');
  if (filesTab && filesTab.parentElement) {
    filesTab.parentElement.style.visibility = 'hidden';
    filesTab.parentElement.style.position = 'absolute';
    filesTab.parentElement.style.pointerEvents = 'none';
  }
}

let lastFileCount = 0;

function syncTableToFileManager(sourceTbody) {
  const fileManagerBody = document.getElementById('fileManagerBody');
  if (!fileManagerBody) {
    console.warn('fileManagerBody not found!');
    return;
  }
  if (!sourceTbody) {
    console.warn('sourceTbody not provided!');
    return;
  }

  const rows = sourceTbody.querySelectorAll('tr');
  console.log(`syncTableToFileManager: Found ${rows.length} rows in source table`);

  if (rows.length === lastFileCount) {
    console.log('File count unchanged, skipping sync');
    return;
  }

  lastFileCount = rows.length;
  console.log(`Syncing ${rows.length} files to File Manager`);
  
  fileManagerBody.innerHTML = '';
  
  rows.forEach((row, index) => {
    const newRow = document.createElement('tr');
    newRow.classList.add('file-row');
    
    const cells = row.querySelectorAll('td');
    
    if (cells.length >= 2) {
      const fieldValueContent = cells[1].innerHTML;

      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = fieldValueContent;
      const filenameWithPath = tempDiv.childNodes[0]?.textContent || '';

      // Extract just the filename without path prefix (/local/, /usb/, etc.)
      const filename = filenameWithPath.split('/').pop();

      // Create thumbnail cell
      const thumbnailCell = document.createElement('td');
      thumbnailCell.style.width = '100px';
      thumbnailCell.style.textAlign = 'center';

      // Extract file extension to check if it supports thumbnails
      const ext = filename.split('.').pop().toLowerCase();
      if (ext === 'goo' || ext === 'ctb') {
        const stem = filename.substring(0, filename.lastIndexOf('.'));
        const thumbnailImg = document.createElement('img');
        thumbnailImg.src = `/thumbnails/${stem}_big.png`;
        thumbnailImg.style.width = '80px';
        thumbnailImg.style.height = '80px';
        thumbnailImg.style.objectFit = 'contain';
        thumbnailImg.style.borderRadius = '6px';
        thumbnailImg.style.border = '2px solid #3a3a3a';
        thumbnailImg.style.background = '#1a1a1a';
        thumbnailImg.style.cursor = 'pointer';
        thumbnailImg.alt = 'Preview';

        // Add click handler for lightbox
        thumbnailImg.addEventListener('click', function() {
          openThumbnailLightbox(this.src);
        });

        // Handle missing thumbnails gracefully
        thumbnailImg.onerror = function() {
          this.style.display = 'none';
          const placeholder = document.createElement('div');
          placeholder.textContent = '📄';
          placeholder.style.fontSize = '2rem';
          placeholder.style.width = '80px';
          placeholder.style.height = '80px';
          placeholder.style.display = 'flex';
          placeholder.style.alignItems = 'center';
          placeholder.style.justifyContent = 'center';
          placeholder.style.color = '#555';
          this.parentElement.appendChild(placeholder);
        };

        thumbnailCell.appendChild(thumbnailImg);
      } else {
        // No thumbnail for other file types
        const placeholder = document.createElement('div');
        placeholder.textContent = '📄';
        placeholder.style.fontSize = '2rem';
        placeholder.style.width = '80px';
        placeholder.style.height = '80px';
        placeholder.style.display = 'flex';
        placeholder.style.alignItems = 'center';
        placeholder.style.justifyContent = 'center';
        placeholder.style.color = '#555';
        thumbnailCell.appendChild(placeholder);
      }
      newRow.appendChild(thumbnailCell);

      const indexCell = document.createElement('td');
      indexCell.textContent = index + 1;
      indexCell.style.width = '50px';
      newRow.appendChild(indexCell);

      const filenameCell = document.createElement('td');
      filenameCell.textContent = filenameWithPath; // Show full path in table
      filenameCell.style.fontFamily = 'monospace';
      newRow.appendChild(filenameCell);

      const actionsCell = document.createElement('td');

      // Add expand button for mobile
      const expandBtn = document.createElement('button');
      expandBtn.className = 'file-expand-btn';
      expandBtn.innerHTML = '<i class="bi bi-chevron-double-down"></i>';
      expandBtn.setAttribute('aria-label', 'Show file details');
      expandBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        const detailsRow = this.closest('tr').nextElementSibling;
        if (detailsRow && detailsRow.classList.contains('file-details-row')) {
          detailsRow.classList.toggle('show');
          this.classList.toggle('expanded');
        }
      });
      actionsCell.appendChild(expandBtn);

      // Add action icons
      const icons = tempDiv.querySelectorAll('i');
      icons.forEach(icon => {
        const clonedIcon = icon.cloneNode(true);
        const iconAction = icon.getAttribute('data-action');

        // Re-attach click handler to cloned icon
        clonedIcon.addEventListener('click', function(e) {
          var action = this.getAttribute('data-action');
          var file = this.getAttribute('data-file');
          if (action && file) {
            // Handle download directly
            if (action === 'download') {
              downloadFile(file);
              return;
            }
            // Show confirmation modal for other actions
            $('#modalConfirmTitle').text('Confirm ' + action);
            $('#modalConfirmAction').text(action);
            $('#modalConfirmValue').text(file);
            $('#btnConfirm').data('action', action).data('value', file);
            modalConfirm.show();
          }
        });
        actionsCell.appendChild(clonedIcon);
      });
      actionsCell.style.textAlign = 'right';
      newRow.appendChild(actionsCell);
      
      const dataFile = row.getAttribute('data-file');
      if (dataFile) {
        newRow.setAttribute('data-file', dataFile);
      }

      fileManagerBody.appendChild(newRow);

      // Create expandable details row for mobile
      const detailsRow = document.createElement('tr');
      detailsRow.className = 'file-details-row';
      const detailsCell = document.createElement('td');
      detailsCell.colSpan = 4;

      const detailsContent = document.createElement('div');
      detailsContent.className = 'file-details-content';

      // File name detail
      const nameItem = document.createElement('div');
      nameItem.className = 'file-detail-item';
      nameItem.innerHTML = `
        <span class="file-detail-label">File Name:</span>
        <span class="file-detail-value">${filename}</span>
      `;
      detailsContent.appendChild(nameItem);

      // Full path detail
      const pathItem = document.createElement('div');
      pathItem.className = 'file-detail-item';
      pathItem.innerHTML = `
        <span class="file-detail-label">Path:</span>
        <span class="file-detail-value">${filenameWithPath}</span>
      `;
      detailsContent.appendChild(pathItem);

      // File number detail
      const numberItem = document.createElement('div');
      numberItem.className = 'file-detail-item';
      numberItem.innerHTML = `
        <span class="file-detail-label">File #:</span>
        <span class="file-detail-value">${index + 1}</span>
      `;
      detailsContent.appendChild(numberItem);

      detailsCell.appendChild(detailsContent);
      detailsRow.appendChild(detailsCell);
      fileManagerBody.appendChild(detailsRow);
    }
  });

  console.log('File Manager updated with', fileManagerBody.children.length, 'files');

  // Initialize Bootstrap tooltips for file manager icons
  var tooltipTriggerList = [].slice.call(document.querySelectorAll('#fileManagerTable [data-bs-toggle="tooltip"]'))
  tooltipTriggerList.map(function (tooltipTriggerEl) {
    return new bootstrap.Tooltip(tooltipTriggerEl)
  })
}

setTimeout(syncFilesToFileManager, 1000);

// Format PrintInfo nicely
function formatPrintInfo() {
  const statusTable = document.getElementById('tableStatus');
  if (!statusTable) return;
  
  const rows = statusTable.querySelectorAll('tr');
  rows.forEach(row => {
    const keyCell = row.querySelector('.fieldKey');
    if (keyCell && keyCell.textContent.trim() === 'PrintInfo') {
      const valueCell = row.querySelector('.fieldValue');
      if (valueCell) {
        try {
          let printInfo;
          const printInfoText = valueCell.textContent.trim();
          
          // Check if it's already parsed or needs parsing
          if (printInfoText.startsWith('{')) {
            printInfo = JSON.parse(printInfoText);
          } else {
            // Skip formatting if it's not valid JSON
            return;
          }
          
          const formattedHTML = `
            <div class="print-info-card">
              <div class="print-info-item">
                <span class="print-info-label">Status</span>
                <span class="print-status-badge ${printInfo.Status === 0 ? 'print-status-idle' : 'print-status-printing'}">
                  ${printInfo.Status === 0 ? 'Idle' : 'Active'}
                </span>
              </div>
              <div class="print-info-item">
                <span class="print-info-label">Current Layer</span>
                <span class="print-info-value">${printInfo.CurrentLayer || 0}</span>
              </div>
              <div class="print-info-item">
                <span class="print-info-label">Total Layers</span>
                <span class="print-info-value">${printInfo.TotalLayer || 0}</span>
              </div>
              <div class="print-info-item">
                <span class="print-info-label">Progress</span>
                <span class="print-info-value large">${printInfo.TotalLayer ? Math.round((printInfo.CurrentLayer / printInfo.TotalLayer) * 100) : 0}%</span>
              </div>
              <div class="print-info-item">
                <span class="print-info-label">Elapsed Time</span>
                <span class="print-info-value">${formatMilliseconds(printInfo.CurrentTicks || 0)}</span>
              </div>
              <div class="print-info-item">
                <span class="print-info-label">Total Time</span>
                <span class="print-info-value">${formatMilliseconds(printInfo.TotalTicks || 0)}</span>
              </div>
              ${printInfo.Filename ? `
              <div class="print-info-item">
                <span class="print-info-label">File</span>
                <span class="print-info-value" style="font-size: 0.75rem; word-break: break-all;">${printInfo.Filename}</span>
              </div>
              ` : ''}
              ${printInfo.ErrorNumber && printInfo.ErrorNumber !== 0 ? `
              <div class="print-info-item">
                <span class="print-info-label">Error</span>
                <span class="print-status-badge print-status-error">Error ${printInfo.ErrorNumber}</span>
              </div>
              ` : ''}
            </div>
          `;
          
          valueCell.innerHTML = formattedHTML;
        } catch (e) {
          // Silently skip - PrintInfo might not be valid JSON yet
        }
      }
    }
  });
}

function formatMilliseconds(ms) {
  if (!ms || ms === 0) return '00:00:00';
  const seconds = Math.floor(ms / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

setInterval(formatPrintInfo, 1000);

// Auto-select first printer on load
function autoSelectFirstPrinter() {
  console.log('Starting auto-select...');

  let attempts = 0;
  const maxAttempts = 30;

  const checkInterval = setInterval(() => {
    attempts++;

    const printersList = document.getElementById('printersList');
    if (!printersList) {
      console.log('Printers list not found yet...');
      return;
    }

    const printers = printersList.querySelectorAll('.printer-card');

    if (printers.length > 0) {
      console.log(`Found ${printers.length} printer(s)`);
      clearInterval(checkInterval);

      // If a default printer is set AND it exists in the current printer list, let the default printer auto-select handle it
      if (defaultPrinterId) {
        const defaultPrinterExists = Array.from(printers).some(
          p => p.getAttribute('data-printer-id') === defaultPrinterId
        );
        if (defaultPrinterExists) {
          console.log('Default printer is set and available, letting default printer auto-select handle it');
          return;
        } else {
          console.log('Default printer is set but not available (deleted?), using fallback logic');
        }
      }

      // No default printer set or default printer not available - use fallback logic
      console.log('Using fallback auto-select logic');

      const lastPrinterId = localStorage.getItem('lastSelectedPrinter');
      let printerToSelect = null;

      // If only 1 printer, select it automatically
      if (printers.length === 1) {
        printerToSelect = printers[0];
        console.log('Only 1 printer found, auto-selecting it');
      }
      // If multiple printers, try to use last selected
      else if (lastPrinterId) {
        printerToSelect = Array.from(printers).find(
          p => p.getAttribute('data-printer-id') === lastPrinterId
        );
        if (printerToSelect) {
          console.log('Found last selected printer:', lastPrinterId);
        }
      }

      // Fallback to first printer
      if (!printerToSelect) {
        printerToSelect = printers[0];
        console.log('Selecting first printer');
      }

      setTimeout(() => {
        printers.forEach(p => p.classList.remove('active'));
        printerToSelect.classList.add('active');
        printerToSelect.click();
        console.log('Printer auto-selected');

        const waitForStatus = setInterval(() => {
          const statusTab = document.getElementById('tab-Status');
          if (statusTab) {
            console.log('Auto-clicking Status tab');
            statusTab.click();
            clearInterval(waitForStatus);
          }
        }, 200);

        setTimeout(() => clearInterval(waitForStatus), 5000);

      }, 1500);

    } else if (attempts >= maxAttempts) {
      console.log('No printers found after timeout');
      clearInterval(checkInterval);
    }
  }, 200);
}

window.addEventListener('load', () => {
  setTimeout(autoSelectFirstPrinter, 1000);
});

// Save printer selection
document.addEventListener('click', (e) => {
  const printerCard = e.target.closest('.printer-card');
  if (printerCard) {
    document.querySelectorAll('.printer-card').forEach(p => p.classList.remove('active'));
    printerCard.classList.add('active');
    
    const printerId = printerCard.getAttribute('data-printer-id');
    if (printerId) {
      localStorage.setItem('lastSelectedPrinter', printerId);
    }
    
    if (window.innerWidth <= 992) {
      toggleSidebar();
    }
  }
});

// Handle window resize
window.addEventListener('resize', () => {
  if (window.innerWidth > 992) {
    sidebar.classList.remove('show');
    sidebarOverlay.classList.remove('show');
    document.body.style.overflow = '';
  }
});

// Thumbnail lightbox
function openThumbnailLightbox(imageSrc) {
  // Create lightbox if it doesn't exist
  let lightbox = document.getElementById('thumbnailLightbox');
  if (!lightbox) {
    lightbox = document.createElement('div');
    lightbox.id = 'thumbnailLightbox';
    lightbox.style.cssText = `
      display: none;
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(0, 0, 0, 0.9);
      z-index: 9999;
      justify-content: center;
      align-items: center;
      cursor: pointer;
    `;

    const img = document.createElement('img');
    img.id = 'lightboxImage';
    img.style.cssText = `
      max-width: 90%;
      max-height: 90%;
      object-fit: contain;
      border-radius: 8px;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
    `;

    const closeBtn = document.createElement('div');
    closeBtn.textContent = '×';
    closeBtn.style.cssText = `
      position: absolute;
      top: 30px;
      right: 30px;
      font-size: 40px;
      color: white;
      cursor: pointer;
      background: rgba(0, 0, 0, 0.5);
      width: 60px;
      height: 60px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.3s;
    `;
    closeBtn.onmouseover = () => closeBtn.style.background = 'rgba(255, 255, 255, 0.2)';
    closeBtn.onmouseout = () => closeBtn.style.background = 'rgba(0, 0, 0, 0.5)';

    lightbox.appendChild(img);
    lightbox.appendChild(closeBtn);
    document.body.appendChild(lightbox);

    // Close on click
    lightbox.onclick = () => {
      lightbox.style.display = 'none';
    };

    // Close on Escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && lightbox.style.display === 'flex') {
        lightbox.style.display = 'none';
      }
    });
  }

  // Set image and show lightbox
  document.getElementById('lightboxImage').src = imageSrc;
  lightbox.style.display = 'flex';
}

// Bootstrap tooltips
(() => {
  'use strict'
  const tooltipTriggerList = Array.from(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
  tooltipTriggerList.forEach(tooltipTriggerEl => {
    new bootstrap.Tooltip(tooltipTriggerEl)
  })
})()
