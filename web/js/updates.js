/**
 * updates.js - ChitUI self-update UI.
 *
 * Three entry points:
 *   1. On page load, ask the server whether a newer release exists and show a
 *      banner at the top of the main screen if so.
 *   2. The banner's "What's new" button opens a modal with the release notes
 *      and an Update / Cancel choice.
 *   3. Settings -> General has an Updates section with the on/off switches and
 *      a manual "Check for Updates" button that renders the same notes inline.
 *
 * The actual upgrade streams its log over SSE into the modal, then restarts
 * ChitUI and waits for it to come back before reloading the page.
 */

(function () {
  'use strict';

  // ─────────────────────────────────────────────────────────────────────────
  // State
  // ─────────────────────────────────────────────────────────────────────────

  var state = {
    current: null,      // version we are running
    release: null,      // latest release object from the server
    available: false,   // is that release newer than ours?
    settings: null,     // the "updates" settings block
    eventSource: null,  // live SSE connection during an upgrade
    restartTimer: null, // countdown before the automatic restart
    updating: false,
    pluginUpdates: []   // plugins with a newer version in the store
  };

  var DISMISS_KEY = 'chituiUpdateDismissed';
  var PLUGIN_DISMISS_KEY = 'chituiPluginUpdatesDismissed';

  // ─────────────────────────────────────────────────────────────────────────
  // Small helpers
  // ─────────────────────────────────────────────────────────────────────────

  function el(id) {
    return document.getElementById(id);
  }

  function esc(text) {
    if (text === null || text === undefined) return '';
    return String(text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function formatDate(iso) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString(undefined,
        { year: 'numeric', month: 'long', day: 'numeric' });
    } catch (e) {
      return iso;
    }
  }

  /**
   * Render the subset of Markdown that GitHub release notes actually use.
   *
   * Everything is HTML-escaped first and only then re-marked-up, so a release
   * body can never inject markup into the page. That is the whole reason this
   * exists instead of trusting the API's body_html field.
   */
  function mdToHtml(markdown) {
    if (!markdown) {
      return '<p class="text-muted fst-italic mb-0">No release notes were provided.</p>';
    }

    var lines = esc(markdown).replace(/\r\n/g, '\n').split('\n');
    var html = [];
    var listType = null;   // 'ul' | 'ol' | null
    var inCode = false;

    function closeList() {
      if (listType) { html.push('</' + listType + '>'); listType = null; }
    }

    function inline(text) {
      return text
        // `code`
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        // [label](url) - only http(s) targets are turned into links
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
          '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
        // **bold**
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        // *italic* / _italic_
        .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
        .replace(/(^|[\s(])_([^_\n]+)_/g, '$1<em>$2</em>')
        // bare URLs
        .replace(/(^|[\s])(https?:\/\/[^\s<]+)/g,
          '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    }

    lines.forEach(function (raw) {
      var line = raw.replace(/\s+$/, '');

      // Fenced code blocks pass through untouched.
      if (/^\s*```/.test(line)) {
        closeList();
        html.push(inCode ? '</code></pre>' : '<pre class="update-code"><code>');
        inCode = !inCode;
        return;
      }
      if (inCode) { html.push(raw); return; }

      if (!line.trim()) { closeList(); return; }

      var heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        closeList();
        var level = Math.min(heading[1].length + 3, 6); // h1 -> h4, keep it modest
        html.push('<h' + level + ' class="update-md-heading">' +
          inline(heading[2]) + '</h' + level + '>');
        return;
      }

      if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) {
        closeList();
        html.push('<hr>');
        return;
      }

      var bullet = line.match(/^\s*[-*+]\s+(.*)$/);
      if (bullet) {
        if (listType !== 'ul') { closeList(); html.push('<ul>'); listType = 'ul'; }
        html.push('<li>' + inline(bullet[1]) + '</li>');
        return;
      }

      var numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (numbered) {
        if (listType !== 'ol') { closeList(); html.push('<ol>'); listType = 'ol'; }
        html.push('<li>' + inline(numbered[1]) + '</li>');
        return;
      }

      var quote = line.match(/^\s*&gt;\s?(.*)$/);
      if (quote) {
        closeList();
        html.push('<blockquote class="update-md-quote">' + inline(quote[1]) + '</blockquote>');
        return;
      }

      closeList();
      html.push('<p>' + inline(line) + '</p>');
    });

    if (inCode) html.push('</code></pre>');
    closeList();
    return html.join('\n');
  }

  /**
   * Is any known printer mid-print?
   *
   * Updating ends in a restart, which drops the websocket relay, so this is
   * worth warning about. The server has no print-state of its own to check,
   * so this reads the same `printers` object the dashboard renders from.
   */
  function printersBusy() {
    var busy = [];
    try {
      if (typeof printers !== 'object' || !printers) return busy;
      Object.keys(printers).forEach(function (id) {
        var p = printers[id];
        var status = p && p.status && p.status.CurrentStatus;
        if (!status) return;
        var codes = Array.isArray(status) ? status : [status];
        if (codes.indexOf(1) !== -1) {     // SDCP_MACHINE_STATUS_PRINTING
          busy.push(p.name || id);
        }
      });
    } catch (e) { /* dashboard not ready yet - treat as idle */ }
    return busy;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Status fetching
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * fetch() wrapper that turns the three failure modes into messages the user
   * can act on.
   *
   * The bare version of this reported "Failed to fetch" for everything, which
   * is what the browser says when the request never completed - and says
   * nothing about why. The common cause is a ChitUI whose files were updated
   * but which was never restarted: index.html and this script are served
   * straight from disk, so the new UI appears while the running Python
   * process still has no idea these endpoints exist.
   */
  function apiFetch(url, options) {
    return fetch(url, Object.assign({ credentials: 'same-origin' }, options || {}))
      .catch(function () {
        throw new Error('Could not reach ChitUI itself. It may have stopped, ' +
                        'or be restarting - reload the page in a moment.');
      })
      .then(function (response) {
        if (response.status === 401) {
          throw new Error('Your session expired. Reload the page and log in again.');
        }
        if (response.status === 404) {
          throw new Error('This ChitUI has not been restarted since the update ' +
                          'was installed, so it does not have the ' + url.split('?')[0] +
                          ' endpoint yet. Restart ChitUI and try again.');
        }
        return response.text().then(function (body) {
          var data;
          try {
            data = JSON.parse(body);
          } catch (e) {
            // An HTML error page, a proxy notice, anything but JSON.
            throw new Error('ChitUI returned an unexpected response (HTTP ' +
                            response.status + '). Check the ChitUI log for the ' +
                            'real error.');
          }
          if (!response.ok && data && data.message) throw new Error(data.message);
          return data;
        });
      });
  }



  function fetchStatus(force, onLoad) {
    var params = [];
    if (force) params.push('force=1');
    if (onLoad) params.push('on_load=1');
    var url = '/updates/status' + (params.length ? '?' + params.join('&') : '');

    return apiFetch(url)
      .then(function (data) {
        state.current = data.current_version || state.current;
        state.release = data.release || null;
        state.available = !!data.update_available;
        state.settings = data.settings || state.settings;
        paintVersionLabels();
        return data;
      });
  }

  function paintVersionLabels() {
    if (!state.current) return;
    document.querySelectorAll('[data-chitui-version]').forEach(function (node) {
      node.textContent = state.current;
    });
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Banner on the main screen
  // ─────────────────────────────────────────────────────────────────────────

  function bannerDismissedFor(version) {
    try {
      return sessionStorage.getItem(DISMISS_KEY) === version;
    } catch (e) { return false; }
  }

  function dismissBanner(version) {
    try { sessionStorage.setItem(DISMISS_KEY, version); } catch (e) { /* private mode */ }
    var banner = el('updateBanner');
    if (banner) banner.classList.add('d-none');
  }

  function showBanner() {
    var banner = el('updateBanner');
    if (!banner || !state.release) return;

    el('updateBannerVersion').textContent = state.release.version;
    el('updateBannerCurrent').textContent = state.current;

    var pre = el('updateBannerPrerelease');
    if (pre) pre.classList.toggle('d-none', !state.release.prerelease);

    banner.classList.remove('d-none');
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Details modal
  // ─────────────────────────────────────────────────────────────────────────

  function modalInstance() {
    return bootstrap.Modal.getOrCreateInstance(el('modalUpdate'),
      { backdrop: 'static', keyboard: false });
  }

  function showStep(step) {
    ['updateStepDetails', 'updateStepProgress', 'updateStepRestart'].forEach(function (id, i) {
      el(id).classList.toggle('d-none', i !== step);
    });
    el('updateFooterDetails').classList.toggle('d-none', step !== 0);
    el('updateFooterProgress').classList.toggle('d-none', step !== 1);
    el('updateFooterRestart').classList.toggle('d-none', step !== 2);
  }

  function openDetailsModal() {
    if (!state.release) return;

    el('updateModalTitle').textContent = 'ChitUI ' + state.release.version + ' is available';
    el('updateFromVersion').textContent = state.current;
    el('updateToVersion').textContent = state.release.version;
    el('updateReleaseDate').textContent = formatDate(state.release.published_at);
    el('updateReleaseName').textContent = state.release.name || state.release.tag;
    el('updateReleaseLink').href = state.release.html_url;
    el('updateReleaseNotes').innerHTML = mdToHtml(state.release.body);

    el('updatePrereleaseWarning').classList.toggle('d-none', !state.release.prerelease);

    // Printing guard
    var busy = printersBusy();
    var warn = el('updatePrintWarning');
    if (busy.length) {
      el('updatePrintWarningText').textContent =
        busy.length === 1
          ? 'Printer "' + busy[0] + '" is printing right now.'
          : busy.length + ' printers are printing right now.';
      warn.classList.remove('d-none');
      el('updateConfirmPrinting').checked = false;
      el('btnDoUpdate').disabled = true;
    } else {
      warn.classList.add('d-none');
      el('btnDoUpdate').disabled = false;
    }

    el('updateTerminal').innerHTML = '';
    el('updateResult').classList.add('d-none');
    el('updateResult').innerHTML = '';
    showStep(0);
    modalInstance().show();
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Running the update
  // ─────────────────────────────────────────────────────────────────────────

  function terminalLog(message, level) {
    var terminal = el('updateTerminal');
    var cursor = terminal.querySelector('.plugin-terminal-cursor');
    if (cursor) cursor.remove();

    var line = document.createElement('span');
    line.className = 'plugin-log-line level-' + (level || 'info');
    line.textContent = message + '\n';
    terminal.appendChild(line);

    var next = document.createElement('span');
    next.className = 'plugin-terminal-cursor';
    terminal.appendChild(next);

    terminal.scrollTop = terminal.scrollHeight;
  }

  function startUpdate() {
    if (state.updating) return;
    state.updating = true;

    showStep(1);
    el('updateSpinner').classList.remove('d-none');
    el('btnUpdateClose').disabled = true;
    terminalLog('Requesting update from the server...');

    apiFetch('/updates/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version: state.release ? state.release.version : null })
    })
      .then(function (data) {
        if (!data.success) { failUpdate(data.message || 'The server refused the update.'); return; }
        streamUpdate(data.job_id);
      })
      .catch(function (err) {
        failUpdate('Could not reach the server: ' + err.message);
      });
  }

  function streamUpdate(jobId) {
    var source = new EventSource('/updates/job/' + jobId + '/stream');
    state.eventSource = source;

    source.addEventListener('log', function (event) {
      try {
        var item = JSON.parse(event.data);
        terminalLog(item.msg, item.level);
      } catch (e) { /* malformed frame - ignore */ }
    });

    source.addEventListener('done', function (event) {
      source.close();
      state.eventSource = null;

      var item = {};
      try { item = JSON.parse(event.data); } catch (e) { /* keep defaults */ }

      if (item.success) succeedUpdate(item);
      else failUpdate(item.message || 'The update failed.');
    });

    source.onerror = function () {
      source.close();
      state.eventSource = null;
      // The server may simply have restarted early; check before crying wolf.
      failUpdate('Lost the connection to the server during the update.');
    };
  }

  function succeedUpdate(item) {
    state.updating = false;
    el('updateSpinner').classList.add('d-none');

    var cursor = el('updateTerminal').querySelector('.plugin-terminal-cursor');
    if (cursor) cursor.remove();

    var result = el('updateResult');
    result.classList.remove('d-none');

    // A dependency failure doesn't undo the update, but it does mean the new
    // version may not start - say so rather than showing a clean green tick.
    var depsWarning = (item.dependencies_ok === false)
      ? '<div class="alert alert-warning mb-0 mt-2 py-2 small">' +
        '<i class="bi bi-exclamation-triangle-fill me-1"></i>' +
        'Some Python dependencies could not be installed. Check the log above, then run ' +
        '<code>pip3 install -r requirements.txt --break-system-packages</code> ' +
        'on the Pi before restarting.</div>'
      : '';

    result.innerHTML =
      '<div class="alert alert-success mb-0 d-flex align-items-start gap-2">' +
      '<i class="bi bi-check-circle-fill fs-5"></i><div>' +
      '<div class="fw-semibold">ChitUI ' + esc(item.version) + ' installed.</div>' +
      '<div class="small">' +
      (item.can_auto_restart
        ? 'ChitUI needs to restart for the new version to load.'
        : 'No supervisor was detected, so you will need to start ChitUI again yourself.') +
      (item.backup ? ' A backup of the previous version is in <code>data/backups/</code>.' : '') +
      '</div></div></div>' + depsWarning;

    el('btnUpdateClose').disabled = false;
    el('btnRestartNow').classList.toggle('d-none', !item.can_auto_restart);
    el('updateFooterProgress').classList.remove('d-none');

    // Don't auto-restart into a version whose dependencies are missing.
    if (item.can_auto_restart && item.dependencies_ok !== false &&
        state.settings && state.settings.auto_restart) {
      beginRestartCountdown(5);
    }
  }

  function failUpdate(message) {
    state.updating = false;
    el('updateSpinner').classList.add('d-none');
    terminalLog('ERROR: ' + message, 'error');

    var result = el('updateResult');
    result.classList.remove('d-none');
    result.innerHTML =
      '<div class="alert alert-danger mb-0 d-flex align-items-start gap-2">' +
      '<i class="bi bi-x-circle-fill fs-5"></i><div>' +
      '<div class="fw-semibold">Update failed</div>' +
      '<div class="small">' + esc(message) + '</div></div></div>';

    el('btnUpdateClose').disabled = false;
    el('btnRestartNow').classList.add('d-none');
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Restart and wait for the server to come back
  // ─────────────────────────────────────────────────────────────────────────

  function beginRestartCountdown(seconds) {
    var label = el('updateCountdown');
    label.classList.remove('d-none');
    el('btnCancelRestart').classList.remove('d-none');
    var left = seconds;

    function tick() {
      label.textContent = 'Restarting automatically in ' + left + 's...';
      if (left <= 0) {
        clearInterval(state.restartTimer);
        state.restartTimer = null;
        label.classList.add('d-none');
        doRestart();
        return;
      }
      left -= 1;
    }

    tick();
    state.restartTimer = setInterval(tick, 1000);
  }

  function cancelCountdown() {
    if (state.restartTimer) {
      clearInterval(state.restartTimer);
      state.restartTimer = null;
    }
    var label = el('updateCountdown');
    if (label) label.classList.add('d-none');
    var cancel = el('btnCancelRestart');
    if (cancel) cancel.classList.add('d-none');
  }

  function doRestart() {
    cancelCountdown();
    showStep(2);
    el('updateRestartMessage').textContent = 'Asking ChitUI to restart...';

    fetch('/maintenance/restart', { method: 'POST', credentials: 'same-origin' })
      .catch(function () { /* the connection dropping is the expected outcome */ })
      .then(function () {
        setTimeout(waitForServer, 3000);
      });
  }

  function waitForServer() {
    var attempts = 0;
    var maxAttempts = 60;   // ~2 minutes at 2s intervals

    el('updateRestartMessage').textContent = 'Waiting for ChitUI to come back online...';

    var poll = setInterval(function () {
      attempts += 1;

      if (attempts > maxAttempts) {
        clearInterval(poll);
        el('updateRestartSpinner').classList.add('d-none');
        el('updateRestartMessage').innerHTML =
          'ChitUI has not come back yet. It may still be starting - ' +
          '<a href="javascript:location.reload()">reload the page</a> to check, ' +
          'or start it again on the Pi.';
        return;
      }

      fetch('/updates/ping', { cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          clearInterval(poll);
          el('updateRestartMessage').textContent =
            'ChitUI ' + (data.version || '') + ' is back. Reloading...';
          setTimeout(function () { location.reload(true); }, 1200);
        })
        .catch(function () { /* still down - keep polling */ });
    }, 2000);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Settings -> General -> Updates
  // ─────────────────────────────────────────────────────────────────────────

  function paintUpdateSettings() {
    if (!state.settings) return;
    var enabled = el('updateAlertsEnabled');
    if (enabled) enabled.checked = !!state.settings.enabled;

    var onLoad = el('updateCheckOnLoad');
    if (onLoad) onLoad.checked = !!state.settings.check_on_load;

    var autoRestart = el('updateAutoRestart');
    if (autoRestart) autoRestart.checked = !!state.settings.auto_restart;

    var channel = el('updateChannel');
    if (channel) channel.value = state.settings.channel || 'stable';

    // Everything below the master switch follows it.
    var off = !state.settings.enabled;
    ['updateCheckOnLoad', 'updateAutoRestart', 'updateChannel',
     'btnCheckUpdatesNow'].forEach(function (id) {
      var node = el(id);
      if (node) node.disabled = off;
    });
    if (off) {
      var panel = el('updateCheckResult');
      if (panel) { panel.classList.add('d-none'); panel.innerHTML = ''; }
    }
  }

  function saveUpdateSettings(patch) {
    return apiFetch('/updates/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    })
      .then(function (data) {
        if (data.success) {
          state.settings = data.settings;
          paintUpdateSettings();
        } else if (typeof showToast === 'function') {
          showToast(data.message || 'Could not save update settings', 'danger');
        }
        return data;
      });
  }

  /** Manual "Check for Updates" in the settings pane. */
  function checkFromSettings() {
    var button = el('btnCheckUpdatesNow');
    var panel = el('updateCheckResult');
    var original = button.innerHTML;

    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Checking...';
    panel.classList.remove('d-none');
    panel.innerHTML =
      '<div class="text-muted small d-flex align-items-center gap-2 py-2">' +
      '<span class="spinner-border spinner-border-sm"></span>' +
      'Contacting GitHub...</div>';

    fetchStatus(true, false)
      .then(function (data) {
        if (data.update_available || (data.skipped && data.release)) {
          renderUpdateFound(panel, data);
          showBannerIfWanted(true);
        } else if (data.error) {
          panel.innerHTML =
            '<div class="alert alert-warning mb-0">' +
            '<div class="fw-semibold"><i class="bi bi-exclamation-triangle-fill me-1"></i>' +
            'Could not check for updates</div>' +
            '<div class="small mt-1">' + esc(data.error) + '</div></div>';
        } else {
          panel.innerHTML =
            '<div class="alert alert-success mb-0 py-2 d-flex align-items-center gap-2">' +
            '<i class="bi bi-check-circle-fill"></i>' +
            '<div><div class="fw-semibold">ChitUI is up to date</div>' +
            '<div class="small">You are running the latest release, version ' +
            esc(data.current_version) + '.</div></div></div>';
        }
      })
      .catch(function (err) {
        panel.innerHTML =
          '<div class="alert alert-danger mb-0 py-2 small">' +
          '<i class="bi bi-x-circle me-1"></i>Check failed: ' + esc(err.message) + '</div>';
      })
      .then(function () {
        button.disabled = false;
        button.innerHTML = original;
      });
  }

  function renderUpdateFound(panel, data) {
    var release = data.release;
    var skippedNote = data.skipped
      ? '<div class="small text-muted mt-1">You previously chose to skip this version.</div>'
      : '';

    panel.innerHTML =
      '<div class="card border-warning">' +
      '  <div class="card-header bg-warning bg-opacity-10 border-warning py-2 ' +
      '       d-flex align-items-center justify-content-between flex-wrap gap-2">' +
      '    <div>' +
      '      <span class="fw-semibold"><i class="bi bi-arrow-up-circle-fill text-warning me-1"></i>' +
      '        Version ' + esc(release.version) + ' is available</span>' +
      '      <span class="text-muted small ms-2">you have ' + esc(data.current_version) + '</span>' +
      (release.prerelease
        ? '      <span class="badge bg-secondary ms-2">pre-release</span>' : '') +
      skippedNote +
      '    </div>' +
      '    <button type="button" class="btn btn-sm btn-warning" id="btnUpdateNowFromSettings">' +
      '      <i class="bi bi-download me-1"></i>Update Now...</button>' +
      '  </div>' +
      '  <div class="card-body py-2">' +
      '    <div class="small text-muted mb-2">' +
      esc(release.name || release.tag) +
      (release.published_at ? ' &middot; released ' + esc(formatDate(release.published_at)) : '') +
      '    </div>' +
      '    <div class="update-notes update-notes-compact">' + mdToHtml(release.body) + '</div>' +
      '    <a href="' + esc(release.html_url) + '" target="_blank" rel="noopener noreferrer" ' +
      '       class="small">View on GitHub <i class="bi bi-box-arrow-up-right"></i></a>' +
      '  </div>' +
      '</div>';

    el('btnUpdateNowFromSettings').addEventListener('click', function () {
      // Close Settings, then open the update dialog so the release notes and
      // the printing check are shown before anything is written to disk.
      var settingsEl = el('modalSettings');
      if (settingsEl && settingsEl.classList.contains('show')) {
        bootstrap.Modal.getOrCreateInstance(settingsEl).hide();
        setTimeout(function () {
          document.querySelectorAll('.modal-backdrop').forEach(function (b) { b.remove(); });
          document.body.classList.remove('modal-open');
          document.body.style.removeProperty('overflow');
          document.body.style.removeProperty('padding-right');
          openDetailsModal();
        }, 350);
      } else {
        openDetailsModal();
      }
    });
  }


  // ─────────────────────────────────────────────────────────────────────────
  // Plugin updates
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * A signature of the pending set, so dismissing "2 updates" doesn't also
   * silence a third one that appears later in the same session.
   */
  function pluginSignature(list) {
    return list.map(function (p) { return p.slug + '@' + p.to; }).sort().join(',');
  }

  function fetchPluginUpdates(force, onLoad) {
    var params = [];
    if (force) params.push('force=1');
    if (onLoad) params.push('on_load=1');
    var url = '/plugins/store/updates' + (params.length ? '?' + params.join('&') : '');

    return apiFetch(url)
      .then(function (data) {
        state.pluginUpdates = data.updates || [];
        return data;
      });
  }

  function showPluginBanner() {
    var banner = el('pluginUpdateBanner');
    var list = state.pluginUpdates;
    if (!banner || !list.length) return;

    try {
      if (sessionStorage.getItem(PLUGIN_DISMISS_KEY) === pluginSignature(list)) return;
    } catch (e) { /* private mode - just show it */ }

    el('pluginUpdateCount').textContent = list.length === 1
      ? '1 plugin update is available'
      : list.length + ' plugin updates are available';

    el('pluginUpdateList').textContent = list.map(function (p) {
      return p.name + ' ' + (p.from || '?') + ' \u2192 ' + p.to;
    }).join(' \u00b7 ');

    banner.classList.remove('d-none');
  }

  /** Open the Plugin Store and land the user on the Updates filter. */
  function openPluginStoreUpdates() {
    if (typeof window.storeOpen !== 'function') {
      if (typeof showToast === 'function') {
        showToast('The Plugin Store is unavailable.', 'warning');
      }
      return;
    }

    window.storeOpen();

    // storeLoadCatalog() is async and re-renders the grid when it lands, so
    // wait for the Updates filter button to exist and for the count badge to
    // be populated before clicking it - otherwise the filter is applied to an
    // empty list and immediately overwritten.
    var attempts = 0;
    var timer = setInterval(function () {
      attempts += 1;
      var filterBtn = document.querySelector('[data-store-filter="updates"]');
      var grid = el('storePluginGrid');
      var ready = filterBtn && grid && !grid.classList.contains('d-none');

      if (ready || attempts > 40) {          // give up after ~8s
        clearInterval(timer);
        if (filterBtn && typeof window.storeSetFilter === 'function') {
          window.storeSetFilter(filterBtn, 'updates');
        }
      }
    }, 200);
  }

  function bindPluginBanner() {
    var open = el('btnOpenPluginUpdates');
    if (open) open.addEventListener('click', openPluginStoreUpdates);

    var dismiss = el('btnPluginUpdateDismiss');
    if (dismiss) {
      dismiss.addEventListener('click', function () {
        try {
          sessionStorage.setItem(PLUGIN_DISMISS_KEY, pluginSignature(state.pluginUpdates));
        } catch (e) { /* private mode */ }
        el('pluginUpdateBanner').classList.add('d-none');
      });
    }
  }


  // ── Plugin settings pane ─────────────────────────────────────────────────

  function paintPluginSettings(cfg) {
    if (!cfg) return;
    state.pluginSettings = cfg;

    var enabled = el('pluginStoreEnabled');
    if (enabled) enabled.checked = !!cfg.enabled;

    var onLoad = el('pluginCheckOnLoad');
    if (onLoad) onLoad.checked = !!cfg.check_on_load;

    var url = el('pluginStoreUrl');
    if (url && document.activeElement !== url) url.value = cfg.url || '';

    var off = !cfg.enabled;
    ['pluginCheckOnLoad', 'pluginStoreUrl', 'btnSavePluginStoreUrl',
     'btnCheckPluginUpdates', 'btnOpenStoreFromSettings'].forEach(function (id) {
      var node = el(id);
      if (node) node.disabled = off;
    });
  }

  function savePluginSettings(patch) {
    return apiFetch('/plugins/store/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    })
      .then(function (data) {
        if (data.success) paintPluginSettings(data.settings);
        else if (typeof showToast === 'function') {
          showToast(data.message || 'Could not save plugin store settings', 'danger');
        }
        return data;
      });
  }

  function checkPluginsFromSettings() {
    var button = el('btnCheckPluginUpdates');
    var panel = el('pluginCheckResult');
    var original = button.innerHTML;

    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Checking...';
    panel.classList.remove('d-none');
    panel.innerHTML = '<div class="text-muted small d-flex align-items-center gap-2 py-2">' +
      '<span class="spinner-border spinner-border-sm"></span>Contacting the plugin store...</div>';

    fetchPluginUpdates(true, false)
      .then(function (data) {
        if (data.error && !data.update_count) {
          panel.innerHTML =
            '<div class="alert alert-warning mb-0">' +
            '<div class="fw-semibold"><i class="bi bi-exclamation-triangle-fill me-1"></i>' +
            'Could not read the plugin catalog</div>' +
            '<div class="small mt-1">' + esc(data.error) + '</div>' +
            '<div class="small mt-2 text-muted">The catalog URL above must serve a ' +
            'JSON list of plugins. If you have not published one yet, this is expected ' +
            '&mdash; ChitUI works normally without it, and you can switch the plugin ' +
            'store off above to stop the check.</div></div>';
          return;
        }

        if (!data.update_count) {
          panel.innerHTML =
            '<div class="alert alert-success mb-0 py-2 d-flex align-items-center gap-2">' +
            '<i class="bi bi-check-circle-fill"></i>' +
            '<div><div class="fw-semibold">All plugins are up to date</div>' +
            '<div class="small">No newer versions are available in the store.</div></div></div>';
          return;
        }

        var rows = data.updates.map(function (p) {
          return '<li>' + esc(p.name) + ' <code>' + esc(p.from || '?') +
                 '</code> &rarr; <code>' + esc(p.to) + '</code></li>';
        }).join('');

        panel.innerHTML =
          '<div class="alert alert-warning mb-0">' +
          '<div class="d-flex align-items-start gap-2">' +
          '<i class="bi bi-puzzle-fill fs-5"></i>' +
          '<div class="flex-grow-1">' +
          '<div class="fw-semibold">' + data.update_count +
          (data.update_count === 1 ? ' plugin update is' : ' plugin updates are') +
          ' available</div>' +
          '<ul class="small mb-2 mt-1 ps-3">' + rows + '</ul>' +
          '<button type="button" class="btn btn-sm btn-warning" ' +
          'id="btnGoToStoreFromResult"><i class="bi bi-shop me-1"></i>' +
          'Open Plugin Store</button>' +
          '</div></div></div>';

        el('btnGoToStoreFromResult').addEventListener('click', leaveSettingsForStore);
        showPluginBanner();
      })
      .catch(function (err) {
        panel.innerHTML = '<div class="alert alert-danger mb-0 py-2 small">' +
          '<i class="bi bi-x-circle me-1"></i>Check failed: ' + esc(err.message) + '</div>';
      })
      .then(function () {
        button.disabled = false;
        button.innerHTML = original;
      });
  }

  /**
   * The store lives in its own modal, so the Settings dialog has to close
   * first. settings.js already does this dance for its own store button;
   * the backdrop cleanup is needed because stacked Bootstrap modals otherwise
   * leave an un-clickable overlay behind.
   */
  function leaveSettingsForStore() {
    var settingsEl = el('modalSettings');
    if (settingsEl && settingsEl.classList.contains('show')) {
      bootstrap.Modal.getOrCreateInstance(settingsEl).hide();
      setTimeout(function () {
        document.querySelectorAll('.modal-backdrop').forEach(function (b) { b.remove(); });
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('overflow');
        document.body.style.removeProperty('padding-right');
        openPluginStoreUpdates();
      }, 350);
    } else {
      openPluginStoreUpdates();
    }
  }

  function bindPluginSettings() {
    var enabled = el('pluginStoreEnabled');
    if (enabled) {
      enabled.addEventListener('change', function () {
        savePluginSettings({ enabled: this.checked });
        if (!this.checked) el('pluginUpdateBanner').classList.add('d-none');
      });
    }

    var onLoad = el('pluginCheckOnLoad');
    if (onLoad) {
      onLoad.addEventListener('change', function () {
        savePluginSettings({ check_on_load: this.checked });
      });
    }

    var saveUrl = el('btnSavePluginStoreUrl');
    if (saveUrl) {
      saveUrl.addEventListener('click', function () {
        savePluginSettings({ url: el('pluginStoreUrl').value.trim() })
          .then(function (data) {
            if (data && data.success && typeof showToast === 'function') {
              showToast('Catalog URL saved.', 'success');
            }
          });
      });
    }

    var check = el('btnCheckPluginUpdates');
    if (check) check.addEventListener('click', checkPluginsFromSettings);

    var openStore = el('btnOpenStoreFromSettings');
    if (openStore) openStore.addEventListener('click', leaveSettingsForStore);

    apiFetch('/plugins/store/settings')
      .then(function (data) { if (data.success) paintPluginSettings(data.settings); })
      .catch(function () { /* leave the controls at their defaults */ });
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Wiring
  // ─────────────────────────────────────────────────────────────────────────

  function showBannerIfWanted(force) {
    if (!state.available || !state.release) return;
    if (!force && bannerDismissedFor(state.release.version)) return;
    showBanner();
  }

  function bind() {
    var details = el('btnUpdateDetails');
    if (details) details.addEventListener('click', openDetailsModal);

    var dismiss = el('btnUpdateDismiss');
    if (dismiss) {
      dismiss.addEventListener('click', function () {
        dismissBanner(state.release ? state.release.version : 'unknown');
      });
    }

    var skip = el('btnUpdateSkip');
    if (skip) {
      skip.addEventListener('click', function () {
        if (!state.release) return;
        var version = state.release.version;
        fetch('/updates/skip', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ version: version })
        }).then(function () {
          state.available = false;
          dismissBanner(version);
          if (typeof showToast === 'function') {
            showToast('ChitUI ' + version + ' will not be suggested again.', 'info');
          }
        });
      });
    }

    var doUpdate = el('btnDoUpdate');
    if (doUpdate) doUpdate.addEventListener('click', startUpdate);

    var confirmPrinting = el('updateConfirmPrinting');
    if (confirmPrinting) {
      confirmPrinting.addEventListener('change', function () {
        el('btnDoUpdate').disabled = !this.checked;
      });
    }

    var restartNow = el('btnRestartNow');
    if (restartNow) restartNow.addEventListener('click', doRestart);

    var cancelRestart = el('btnCancelRestart');
    if (cancelRestart) cancelRestart.addEventListener('click', cancelCountdown);

    var closeBtn = el('btnUpdateClose');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        cancelCountdown();
        modalInstance().hide();
        // A successful update leaves the banner meaningless.
        if (!state.updating && state.release) {
          var banner = el('updateBanner');
          if (banner) banner.classList.add('d-none');
        }
      });
    }

    // Settings pane
    var enabled = el('updateAlertsEnabled');
    if (enabled) {
      enabled.addEventListener('change', function () {
        saveUpdateSettings({ enabled: this.checked });
        if (!this.checked) {
          var banner = el('updateBanner');
          if (banner) banner.classList.add('d-none');
        }
      });
    }

    var onLoad = el('updateCheckOnLoad');
    if (onLoad) {
      onLoad.addEventListener('change', function () {
        saveUpdateSettings({ check_on_load: this.checked });
      });
    }

    var autoRestart = el('updateAutoRestart');
    if (autoRestart) {
      autoRestart.addEventListener('change', function () {
        saveUpdateSettings({ auto_restart: this.checked });
      });
    }

    var channel = el('updateChannel');
    if (channel) {
      channel.addEventListener('change', function () {
        saveUpdateSettings({ channel: this.value });
      });
    }

    var check = el('btnCheckUpdatesNow');
    if (check) check.addEventListener('click', checkFromSettings);
  }

  function init() {
    bind();
    bindPluginBanner();
    bindPluginSettings();

    fetchStatus(false, true)
      .then(function (data) {
        paintUpdateSettings();
        if (data.enabled && data.settings && data.settings.check_on_load) {
          showBannerIfWanted(false);
        }
      })
      .catch(function (err) {
        // A failed check must never get in the way of using the printer.
        console.warn('Update check failed:', err);
      });

    // Plugin updates are checked independently: a broken or unreachable
    // plugin store must not stop the ChitUI update check, or vice versa.
    fetchPluginUpdates(false, true)
      .then(function () { showPluginBanner(); })
      .catch(function (err) {
        console.warn('Plugin update check failed:', err);
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Exposed for the Settings dialog and for manual use from the console.
  window.chituiUpdates = {
    check: function () { return fetchStatus(true, false); },
    open: openDetailsModal,
    checkPlugins: function () { return fetchPluginUpdates(true, false); },
    openPluginStore: openPluginStoreUpdates,
    state: state
  };
})();
