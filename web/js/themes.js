/**
 * ChitUI Theme System - frontend
 * Settings → Appearance tab: list, apply, upload and delete UI themes.
 */
(function () {
  'use strict';

  let themesLoaded = false;

  function escapeHtml(s) {
    return $('<div>').text(s || '').html();
  }

  function showThemeAlert(type, message) {
    $('#themeAlert')
      .removeClass('d-none alert-success alert-danger alert-info')
      .addClass('alert-' + type)
      .html(message);
  }

  function clearThemeAlert() {
    $('#themeAlert').addClass('d-none');
  }

  /** Re-load the active theme stylesheet without a full page reload */
  function refreshThemeStylesheet() {
    const link = document.getElementById('themeStylesheet');
    if (link) link.href = '/themes/active.css?v=' + Date.now();
  }

  /* ---------------- Color mode (chosen per theme card) -------------- */
  /* Bootstrap's color-modes.js applies the saved mode from localStorage
     'theme' before first paint; applying a card writes it.              */

  function applyColorMode(mode) {
    localStorage.setItem('theme', mode);
    document.documentElement.setAttribute('data-bs-theme', mode);
  }

  function themeCard(theme, activeId, activeMode) {
    const isActive = theme.id === activeId;
    const mode = isActive ? activeMode : (localStorage.getItem('theme') === 'light' ? 'light' : 'dark');
    const preview = theme.preview_url
      ? `<img src="${theme.preview_url}" class="w-100 rounded-top theme-preview-img"
             style="height:140px;object-fit:cover;cursor:zoom-in;"
             data-preview-url="${theme.preview_url}" data-theme-name="${escapeHtml(theme.name)}"
             title="Click to enlarge" alt="preview">`
      : `<div class="d-flex align-items-center justify-content-center rounded-top"
             style="height:140px;background:var(--bs-tertiary-bg);">
           <i class="bi bi-palette" style="font-size:2.5rem;opacity:.35;"></i>
         </div>`;

    return `
      <div class="col-md-6 col-xl-4">
        <div class="card h-100 ${isActive ? 'border-success' : ''}" data-theme-card="${theme.id}">
          ${preview}
          <div class="card-body d-flex flex-column">
            <div class="d-flex align-items-start justify-content-between">
              <h6 class="card-title mb-1">${escapeHtml(theme.name)}</h6>
              ${isActive ? `<span class="badge text-bg-success flex-shrink-0"><i class="bi bi-check-lg"></i> Active · ${activeMode === 'light' ? 'Light' : 'Dark'}</span>` : ''}
            </div>
            <div class="text-muted small mb-2">
              v${escapeHtml(theme.version)} · ${escapeHtml(theme.author)}
            </div>
            <p class="text-muted small flex-grow-1 mb-3">${escapeHtml(theme.description)}</p>
            <div class="btn-group btn-group-sm w-100 mb-2" role="group" aria-label="Color mode">
              <button type="button" class="btn ${mode === 'light' ? 'btn-secondary' : 'btn-outline-secondary'} theme-mode-btn"
                      data-mode="light"><i class="bi bi-sun-fill me-1"></i>Light</button>
              <button type="button" class="btn ${mode === 'dark' ? 'btn-secondary' : 'btn-outline-secondary'} theme-mode-btn"
                      data-mode="dark"><i class="bi bi-moon-stars-fill me-1"></i>Dark</button>
            </div>
            <div class="d-flex gap-2">
              <button class="btn btn-sm btn-accent theme-apply-btn flex-grow-1"
                      data-theme-id="${theme.id}">
                <i class="bi bi-brush me-1"></i>Apply
              </button>
              ${theme.builtin ? '' : `
                <button class="btn btn-sm btn-outline-danger theme-delete-btn"
                        data-theme-id="${theme.id}" data-theme-name="${escapeHtml(theme.name)}">
                  <i class="bi bi-trash"></i>
                </button>`}
            </div>
          </div>
        </div>
      </div>`;
  }

  function loadThemes(force) {
    if (themesLoaded && !force) return;

    $('#themeList').html(`
      <div class="text-center py-4 w-100">
        <div class="spinner-border text-secondary" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
      </div>`);

    $.getJSON('/themes')
      .done(function (data) {
        themesLoaded = true;
        const cards = (data.themes || [])
          .map(t => themeCard(t, data.active, data.active_mode || 'dark')).join('');
        $('#themeList').html(`<div class="row g-3">${cards}</div>`);
      })
      .fail(function () {
        $('#themeList').html(
          '<div class="alert alert-danger">Failed to load themes.</div>');
      });
  }

  function applyTheme(themeId, mode) {
    clearThemeAlert();
    $.ajax({
      url: '/themes/apply',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ theme_id: themeId, mode: mode })
    })
      .done(function () {
        applyColorMode(mode);          // this browser switches immediately
        refreshThemeStylesheet();
        loadThemes(true);
        showThemeAlert('success',
          `<i class="bi bi-check-circle me-2"></i>Theme applied in ${mode} mode.`);
      })
      .fail(function (xhr) {
        const msg = (xhr.responseJSON && xhr.responseJSON.message) || 'Failed to apply theme';
        showThemeAlert('danger', '<i class="bi bi-x-circle me-2"></i>' + escapeHtml(msg));
      });
  }

  function deleteTheme(themeId, themeName) {
    if (!confirm(`Delete theme "${themeName}"? This cannot be undone.`)) return;
    clearThemeAlert();
    $.post(`/themes/${themeId}/delete`)
      .done(function (data) {
        if (data.reverted_to_default) refreshThemeStylesheet();
        loadThemes(true);
        showThemeAlert('success', '<i class="bi bi-check-circle me-2"></i>Theme deleted.');
      })
      .fail(function (xhr) {
        const msg = (xhr.responseJSON && xhr.responseJSON.message) || 'Failed to delete theme';
        showThemeAlert('danger', '<i class="bi bi-x-circle me-2"></i>' + escapeHtml(msg));
      });
  }

  function uploadTheme() {
    const input = document.getElementById('themeUploadInput');
    if (!input.files || !input.files.length) {
      showThemeAlert('info', 'Choose a theme ZIP file first.');
      return;
    }

    const formData = new FormData();
    formData.append('theme', input.files[0]);

    const $btn = $('#themeUploadBtn');
    $btn.prop('disabled', true)
        .html('<span class="spinner-border spinner-border-sm me-1"></span>Uploading...');
    clearThemeAlert();

    $.ajax({
      url: '/themes/upload',
      method: 'POST',
      data: formData,
      processData: false,
      contentType: false
    })
      .done(function (data) {
        input.value = '';
        loadThemes(true);
        showThemeAlert('success',
          '<i class="bi bi-check-circle me-2"></i>' + escapeHtml(data.message) +
          ' Press <strong>Apply</strong> on its card to activate it.');
      })
      .fail(function (xhr) {
        const msg = (xhr.responseJSON && xhr.responseJSON.message) || 'Upload failed';
        showThemeAlert('danger', '<i class="bi bi-x-circle me-2"></i>' + escapeHtml(msg));
      })
      .always(function () {
        $btn.prop('disabled', false)
            .html('<i class="bi bi-upload me-1"></i>Upload');
      });
  }

  /** Full-size preview modal (injected once, on demand) */
  function ensurePreviewModal() {
    if (document.getElementById('themePreviewModal')) return;
    $('body').append(`
      <div class="modal fade" id="themePreviewModal" tabindex="-1" aria-hidden="true" style="z-index:1080;">
        <div class="modal-dialog modal-xl modal-dialog-centered">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title" id="themePreviewModalTitle">Theme preview</h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body p-2 text-center">
              <img id="themePreviewModalImg" src="" alt="Theme preview"
                   class="img-fluid rounded" style="max-height:75vh;">
            </div>
          </div>
        </div>
      </div>`);
    // Bootstrap removes body scroll-lock when ANY modal closes; restore it
    // if the settings modal underneath is still open (stacked modals).
    $('#themePreviewModal').on('hidden.bs.modal', function () {
      if ($('.modal.show').length) $('body').addClass('modal-open');
    });
  }

  function openPreviewModal(url, name) {
    ensurePreviewModal();
    $('#themePreviewModalTitle').text(name + ' — preview');
    $('#themePreviewModalImg').attr('src', url);
    bootstrap.Modal.getOrCreateInstance(
      document.getElementById('themePreviewModal')).show();
  }

  $(function () {
    // Theme Designer needs a desktop-sized screen
    $(document).on('click', '#themeDesignerLink', function (e) {
      if (window.matchMedia('(max-width: 991.98px)').matches) {
        e.preventDefault();
        showThemeAlert('info',
          '<i class="bi bi-display me-2"></i><strong>Theme Designer is desktop-only.</strong> ' +
          'It needs a large screen for the live preview and drag &amp; drop editing &mdash; ' +
          'please open it in a PC browser. Themes you create there can be applied from any device, including this one.');
      }
    });

    // Per-card Light/Dark selection
    $(document).on('click', '.theme-mode-btn', function () {
      const $group = $(this).closest('.btn-group');
      $group.find('.theme-mode-btn')
        .removeClass('btn-secondary').addClass('btn-outline-secondary');
      $(this).removeClass('btn-outline-secondary').addClass('btn-secondary');
    });

    // Lazy-load the list the first time the Appearance tab is opened
    $(document).on('shown.bs.tab',
      'button[data-bs-target="#appearance-pane"]',
      function () { loadThemes(false); });

    $(document).on('click', '.theme-preview-img', function () {
      openPreviewModal($(this).data('preview-url'), $(this).data('theme-name'));
    });

    $(document).on('click', '.theme-apply-btn', function () {
      const $card = $(this).closest('[data-theme-card]');
      const mode = $card.find('.theme-mode-btn.btn-secondary').data('mode') || 'dark';
      applyTheme($(this).data('theme-id'), mode);
    });

    $(document).on('click', '.theme-delete-btn', function () {
      deleteTheme($(this).data('theme-id'), $(this).data('theme-name'));
    });

    $(document).on('click', '#themeUploadBtn', uploadTheme);
  });
})();
