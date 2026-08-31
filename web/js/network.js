// ─────────────────────────────────────────────────────────────────────────────
// Settings → Network
// DHCP / Static IP configuration + web server port
// ─────────────────────────────────────────────────────────────────────────────

let _netRevertTimerId = null;
let _netRevertDeadline = null;

// navigator.clipboard only exists in a "secure context" (HTTPS or
// localhost). ChitUI is normally reached over plain http://<pi-ip>:port,
// where that API is simply undefined — so it needs a fallback that works
// over plain HTTP too.
function copyTextToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(
            function () { showToast('Command copied to clipboard', 'success'); },
            function () { _fallbackCopyText(text); }
        );
    } else {
        _fallbackCopyText(text);
    }
}

function _fallbackCopyText(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '0';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    showToast(ok ? 'Command copied to clipboard' : 'Could not copy — please select and copy the command manually',
               ok ? 'success' : 'warning');
}

$(document).ready(function () {
    $('button[data-bs-target="#network-pane"]').on('click', loadNetworkStatus);
    $('#modalSettings').on('show.bs.modal', loadNetworkStatus);

    $('input[name="netMode"]').on('change', function () {
        $('#netStaticFields').toggleClass('d-none', $('#netModeStatic').is(':checked') === false);
        $('#netDefaultNote').toggleClass('d-none', $('#netModeStatic').is(':checked'));
    });

    $('#netSetupCopyBtn').on('click', function () {
        copyTextToClipboard($('#netSetupCmd').text());
    });

    $('#btnApplyNetwork').on('click', function () { applyNetworkSettings(false); });
    $('#netConfirmBtn').on('click', confirmNetworkChange);
});

function loadNetworkStatus() {
    $('#netModeBadge').text('checking…').attr('class', 'badge bg-secondary');
    $.ajax({
        url: '/network/status', method: 'GET',
        success: function (d) {
            if (!d.success) { showToast(d.message || 'Failed to load network status', 'danger'); return; }
            renderNetworkStatus(d);
        },
        error: function (xhr) {
            showToast((xhr.responseJSON || {}).message || 'Failed to load network status', 'danger');
            $('#netModeBadge').text('error').attr('class', 'badge bg-danger');
        }
    });
}

function renderNetworkStatus(d) {
    // Interfaces dropdown
    const $sel = $('#netIfaceSelect').empty();
    (d.interfaces || []).forEach(function (iface) {
        $sel.append($('<option>').val(iface).text(iface));
    });
    if (d.interface) $sel.val(d.interface);
    $('#netIfaceRow').toggleClass('d-none', (d.interfaces || []).length <= 1);

    // Current status card
    const cur = d.current || {};
    $('#netCurIface').text(d.interface || '-');
    $('#netCurIp').text(cur.ip ? `${cur.ip} (${cur.netmask || '/' + (cur.prefix ?? '?')})` : '-');
    $('#netCurGw').text(cur.gateway || '-');
    $('#netCurDns').text((cur.dns && cur.dns.length) ? cur.dns.join(', ') : '-');

    // Mode badge
    const mode = d.configured_mode || 'dhcp';
    if (mode === 'static') {
        $('#netModeBadge').text('Static IP').attr('class', 'badge bg-accent');
    } else {
        $('#netModeBadge').text('DHCP').attr('class', 'badge bg-success');
    }

    // Form defaults from configured settings
    $(`input[name="netMode"][value="${mode}"]`).prop('checked', true).trigger('change');
    const st = d.configured_static || {};
    $('#netIpInput').val(st.ip || '');
    $('#netMaskInput').val(st.netmask || '255.255.255.0');
    $('#netGatewayInput').val(st.gateway || '');
    $('#netDns1Input').val((st.dns && st.dns[0]) || '');
    $('#netDns2Input').val((st.dns && st.dns[1]) || '');
    $('#netPortInput').val(d.configured_port || 8080);

    // Sudo setup banner
    if (d.sudo_ok) {
        $('#netSetupBanner').addClass('d-none');
    } else {
        $('#netSetupCmd').text(d.setup_cmd || '-');
        $('#netSetupBanner').removeClass('d-none');
    }

    // Pending confirmation (e.g. page reloaded after a static-IP apply)
    if (d.pending_confirmation) {
        showNetConfirmBanner(90);
    } else {
        $('#netConfirmBanner').addClass('d-none');
        stopNetRevertCountdown();
    }
}

function collectNetworkPayload() {
    const mode = $('input[name="netMode"]:checked').val() || 'dhcp';
    const payload = {
        mode: mode,
        interface: $('#netIfaceSelect').val(),
        port: $('#netPortInput').val()
    };
    if (mode === 'static') {
        payload.ip = $('#netIpInput').val().trim();
        payload.netmask = $('#netMaskInput').val().trim();
        payload.gateway = $('#netGatewayInput').val().trim();
        const dns = [$('#netDns1Input').val().trim(), $('#netDns2Input').val().trim()].filter(Boolean);
        payload.dns = dns;
    }
    return payload;
}

function showNetResult(message, type) {
    $('#netApplyResult')
        .removeClass('d-none alert-danger alert-success alert-warning alert-info')
        .addClass('alert-' + type)
        .text(message);
}

const DOTTED_QUAD_RE = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/;

function applyNetworkSettings(confirmRisk) {
    const payload = collectNetworkPayload();
    payload.confirm_risk = !!confirmRisk;

    if (payload.mode === 'static' && !DOTTED_QUAD_RE.test(payload.netmask || '')) {
        showNetResult('Subnet mask must be in dotted-quad form, e.g. 255.255.255.0', 'danger');
        return;
    }

    if (payload.mode === 'static' && !confirmRisk) {
        if (!confirm('Applying a static IP can disconnect this device from ChitUI if any value ' +
            'is wrong.\n\nChitUI will automatically revert to the previous network settings after ' +
            '90 seconds unless you confirm you can still reach it.\n\nContinue?')) {
            return;
        }
        payload.confirm_risk = true;
    }

    const $btn = $('#btnApplyNetwork');
    $btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-1"></span> Applying…');

    $.ajax({
        url: '/network/apply', method: 'POST', contentType: 'application/json',
        data: JSON.stringify(payload),
        success: function (d) {
            if (!d.success) { handleNetworkError(d); return; }

            let msg = d.message || 'Network settings saved.';
            if (d.warning) msg += ' ' + d.warning;
            showNetResult(msg, 'success');
            showToast('Network settings saved', 'success');

            if (d.applied_network && payload.mode === 'static') {
                showNetConfirmBanner(d.confirmation_window || 90);
                showToast('Applying static IP… verify the page still loads at the new address', 'warning');
            } else if (d.applied_network) {
                // Switched to DHCP — address may change but no confirmation flow needed
                showToast('Switched to DHCP. The device may pick up a new IP address.', 'info');
            }

            if (d.port_changed) {
                promptPortRestart(d.new_port);
            }
        },
        error: function (xhr) {
            handleNetworkError(xhr.responseJSON || {});
        },
        complete: function () {
            $btn.prop('disabled', false).html('<i class="bi bi-check2-circle"></i> Apply Network Settings');
        }
    });
}

function handleNetworkError(d) {
    if (d.needs_sudo_setup) {
        $('#netSetupCmd').text(d.setup_cmd || '-');
        $('#netSetupBanner').removeClass('d-none');
        showNetResult(d.message || 'One-time setup required — see the instructions above.', 'warning');
        return;
    }
    if (d.requires_confirmation) {
        // Client-side confirm() already guards this path; if we land here the
        // dialog was skipped somehow — just retry with confirm_risk set.
        applyNetworkSettings(true);
        return;
    }
    showNetResult(d.message || 'Failed to apply network settings', 'danger');
    showToast(d.message || 'Failed to apply network settings', 'danger');
}

function promptPortRestart(newPort) {
    if (!confirm(`The web server port has changed to ${newPort}. ChitUI needs to restart to apply ` +
        `this. Restart now?\n\nAfter restarting, reconnect at:\nhttp://${window.location.hostname}:${newPort}`)) {
        showToast(`Restart ChitUI manually to apply the new port (${newPort})`, 'warning');
        return;
    }
    $.ajax({
        url: '/maintenance/restart', method: 'POST', timeout: 5000,
        complete: function () {
            showToast('Restarting… redirecting to the new port shortly', 'success');
            $('#modalSettings').modal('hide');
            setTimeout(function () {
                window.location.href = `http://${window.location.hostname}:${newPort}`;
            }, 5000);
        }
    });
}

function showNetConfirmBanner(seconds) {
    $('#netConfirmBanner').removeClass('d-none');
    _netRevertDeadline = Date.now() + seconds * 1000;
    stopNetRevertCountdown();
    _netRevertTimerId = setInterval(function () {
        const remaining = Math.max(0, Math.round((_netRevertDeadline - Date.now()) / 1000));
        $('#netRevertCountdown').text(remaining);
        if (remaining <= 0) {
            stopNetRevertCountdown();
            $('#netConfirmBanner').addClass('d-none');
            showToast('Static IP change was not confirmed in time and has been reverted', 'warning');
            loadNetworkStatus();
        }
    }, 1000);
}

function stopNetRevertCountdown() {
    if (_netRevertTimerId) {
        clearInterval(_netRevertTimerId);
        _netRevertTimerId = null;
    }
}

function confirmNetworkChange() {
    $.ajax({
        url: '/network/confirm', method: 'POST',
        success: function () {
            stopNetRevertCountdown();
            $('#netConfirmBanner').addClass('d-none');
            showToast('Network change confirmed and kept', 'success');
            loadNetworkStatus();
        },
        error: function () {
            showToast('Failed to confirm — try again', 'danger');
        }
    });
}
