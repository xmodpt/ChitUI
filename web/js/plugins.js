/**
 * ChitUI Plugin System - Frontend
 *
 * Handles dynamic loading and management of plugins in the UI
 */

// Plugin state
let pluginData = [];

/**
 * Load and inject plugin UI elements into the page
 */
function loadPluginUI() {
  console.log('Loading plugin UI...');

  fetch('/plugins/ui')
    .then(response => response.json())
    .then(plugins => {
      console.log('Loaded plugins:', plugins);

      plugins.forEach(plugin => {
        injectPluginUI(plugin);
      });
    })
    .catch(error => {
      console.error('Failed to load plugin UI:', error);
    });
}

/**
 * Inject a plugin's UI into the appropriate location
 */
function injectPluginUI(plugin) {
  const { type, location, html, plugin_id, title, icon } = plugin;

  if (!html) {
    console.warn(`Plugin ${plugin_id} has no HTML to inject`);
    return;
  }

  console.log(`Injecting plugin ${plugin_id} (${type}) into ${location}`);

  switch (type) {
    case 'card':
      injectCard(plugin);
      break;
    case 'toolbar':
      injectToolbar(plugin);
      break;
    case 'tab':
      injectTab(plugin);
      break;
    case 'modal':
      injectModal(plugin);
      break;
    default:
      console.warn(`Unknown plugin type: ${type}`);
  }
}

/**
 * Execute scripts embedded in plugin HTML
 * innerHTML doesn't execute scripts for security reasons, so we need to extract and execute them manually
 */
function executePluginScripts(container) {
  const scripts = container.querySelectorAll('script');
  scripts.forEach(oldScript => {
    const newScript = document.createElement('script');

    // Copy script attributes
    Array.from(oldScript.attributes).forEach(attr => {
      newScript.setAttribute(attr.name, attr.value);
    });

    // Copy script content
    newScript.textContent = oldScript.textContent;

    // Replace the old script with the new one
    oldScript.parentNode.replaceChild(newScript, oldScript);
  });
}

/**
 * Inject a plugin as a card in the main content area
 */
function injectCard(plugin) {
  const container = document.querySelector('.app-content');
  if (!container) {
    console.error('Card container not found');
    return;
  }

  const pluginDiv = document.createElement('div');
  pluginDiv.id = `plugin-${plugin.plugin_id}`;
  pluginDiv.className = 'plugin-card';
  pluginDiv.innerHTML = plugin.html;

  container.appendChild(pluginDiv);

  // Execute any scripts in the plugin
  executePluginScripts(pluginDiv);
}

/**
 * Inject a plugin into the toolbar
 */
function injectToolbar(plugin) {
  // Check if this should go into the header or main content area
  if (plugin.location === 'top' || plugin.location === 'header') {
    // Inject into header toolbar (top-right)
    const headerActions = document.querySelector('.header-actions');
    if (!headerActions) {
      console.error('Header actions section not found, cannot inject toolbar');
      return;
    }

    // Create plugin container and inject HTML
    const pluginDiv = document.createElement('div');
    pluginDiv.id = `plugin-toolbar-${plugin.plugin_id}`;
    pluginDiv.className = 'plugin-header-item';
    pluginDiv.innerHTML = plugin.html;

    headerActions.appendChild(pluginDiv);

    // Extract and execute scripts (innerHTML doesn't execute scripts for security)
    executePluginScripts(pluginDiv);
  } else {
    // Inject into main content area (after printer preview)
    const printerPreview = document.querySelector('.printer-preview');
    if (!printerPreview) {
      console.error('Printer preview section not found, cannot inject toolbar');
      return;
    }

    // Create a toolbar container if it doesn't exist
    let toolbarContainer = document.getElementById('plugin-toolbar-container');
    if (!toolbarContainer) {
      toolbarContainer = document.createElement('div');
      toolbarContainer.id = 'plugin-toolbar-container';
      toolbarContainer.className = 'd-flex gap-2 mt-3';

      // Insert toolbar container after printer preview
      printerPreview.parentNode.insertBefore(toolbarContainer, printerPreview.nextSibling);
    }

    // Create plugin container and inject HTML
    const pluginDiv = document.createElement('div');
    pluginDiv.id = `plugin-toolbar-${plugin.plugin_id}`;
    pluginDiv.className = 'plugin-toolbar-item';
    pluginDiv.innerHTML = plugin.html;

    toolbarContainer.appendChild(pluginDiv);

    // Extract and execute scripts
    executePluginScripts(pluginDiv);
  }
}

/**
 * Inject a plugin as a tab
 */
function injectTab(plugin) {
  // Determine which tab container to use based on location
  let navTabsId, navPanesId;

  if (plugin.location === 'camera') {
    navTabsId = 'cameraTabs';
    navPanesId = 'cameraPanes';
  } else if (plugin.location === 'printer-info') {
    navTabsId = 'navTabs';
    navPanesId = 'navPanes';
  } else {
    // Default to printer info tabs for backward compatibility
    navTabsId = 'navTabs';
    navPanesId = 'navPanes';
  }

  // Add to navigation tabs
  const navTabs = document.getElementById(navTabsId);
  if (!navTabs) {
    console.error(`Tab container ${navTabsId} not found`);
    return;
  }

  const tab = document.createElement('li');
  tab.className = 'nav-item';
  tab.innerHTML = `
    <button class="nav-link" id="tab-${plugin.plugin_id}"
            data-bs-toggle="pill" data-bs-target="#tab${plugin.plugin_id}"
            type="button">
      <i class="${plugin.icon}"></i> ${plugin.title}
    </button>
  `;
  navTabs.appendChild(tab);

  // Add content pane
  const navPanes = document.getElementById(navPanesId);
  if (!navPanes) {
    console.error(`Pane container ${navPanesId} not found`);
    return;
  }

  const pane = document.createElement('div');
  pane.className = 'tab-pane fade';
  pane.id = `tab${plugin.plugin_id}`;
  pane.innerHTML = plugin.html;
  navPanes.appendChild(pane);

  // Execute any scripts in the plugin
  executePluginScripts(pane);
}

/**
 * Inject a plugin as a modal
 */
function injectModal(plugin) {
  const body = document.body;

  const modalDiv = document.createElement('div');
  modalDiv.id = `plugin-modal-${plugin.plugin_id}`;
  modalDiv.innerHTML = plugin.html;

  body.appendChild(modalDiv);

  // Execute any scripts in the plugin
  executePluginScripts(modalDiv);
}

/**
 * Load plugin list for management UI
 */
function loadPluginList() {
  return fetch('/plugins')
    .then(response => response.json())
    .then(data => {
      pluginData = data;
      return data;
    });
}

/**
 * Enable a plugin
 */
function enablePlugin(pluginId) {
  return fetch(`/plugins/${pluginId}/enable`, { method: 'POST' })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        console.log(`Plugin ${pluginId} enabled`);
        // Reload page to load the plugin
        window.location.reload();
      } else {
        console.error(`Failed to enable plugin: ${data.message}`);
        alert(`Failed to enable plugin: ${data.message}`);
      }
    });
}

/**
 * Disable a plugin
 */
function disablePlugin(pluginId) {
  return fetch(`/plugins/${pluginId}/disable`, { method: 'POST' })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        console.log(`Plugin ${pluginId} disabled`);
        // Reload page to unload the plugin
        window.location.reload();
      } else {
        console.error(`Failed to disable plugin: ${data.message}`);
        alert(`Failed to disable plugin: ${data.message}`);
      }
    });
}

/**
 * Delete a plugin
 */
function deletePlugin(pluginId, pluginName) {
  if (!confirm(`Are you sure you want to delete the plugin "${pluginName}"? This cannot be undone.`)) {
    return;
  }

  return fetch(`/plugins/${pluginId}/delete`, { method: 'POST' })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        console.log(`Plugin ${pluginId} deleted`);
        alert(`Plugin "${pluginName}" deleted successfully`);
        // Reload page
        window.location.reload();
      } else {
        console.error(`Failed to delete plugin: ${data.message}`);
        alert(`Failed to delete plugin: ${data.message}`);
      }
    })
    .catch(error => {
      console.error('Error deleting plugin:', error);
      alert(`Error deleting plugin: ${error}`);
    });
}

/**
 * Upload a plugin
 */
function uploadPlugin(file) {
  const formData = new FormData();
  formData.append('plugin', file);

  return fetch('/plugins/upload', {
    method: 'POST',
    body: formData
  })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        console.log('Plugin uploaded successfully');
        alert(`${data.message}`);
        // Reload page to show the new plugin
        window.location.reload();
      } else {
        console.error(`Failed to upload plugin: ${data.message}`);
        alert(`Failed to upload plugin: ${data.message}`);
      }
      return data;
    })
    .catch(error => {
      console.error('Error uploading plugin:', error);
      alert(`Error uploading plugin: ${error}`);
    });
}

/**
 * Render plugin manager UI
 */
function renderPluginManager() {
  loadPluginList().then(plugins => {
    const container = document.getElementById('pluginManagerList');
    if (!container) return;

    container.innerHTML = '';

    // Add upload section
    const uploadSection = document.createElement('div');
    uploadSection.className = 'card mb-3 bg-dark border-secondary';
    uploadSection.innerHTML = `
      <div class="card-body">
        <h6 class="card-title"><i class="bi bi-upload me-2"></i>Install New Plugin</h6>
        <p class="text-muted small mb-3">
          Upload a plugin ZIP file to install it. The plugin will be available after reloading the page.
        </p>
        <div class="input-group">
          <input type="file" class="form-control" id="pluginFileInput" accept=".zip">
          <button class="btn btn-primary" id="btnUploadPlugin">
            <i class="bi bi-upload"></i> Upload
          </button>
        </div>
      </div>
    `;
    container.appendChild(uploadSection);

    // Add upload button handler
    document.getElementById('btnUploadPlugin').addEventListener('click', function() {
      const fileInput = document.getElementById('pluginFileInput');
      if (fileInput.files.length === 0) {
        alert('Please select a plugin ZIP file');
        return;
      }
      uploadPlugin(fileInput.files[0]);
    });

    if (plugins.length === 0) {
      const noPlugins = document.createElement('p');
      noPlugins.className = 'text-muted text-center py-3';
      noPlugins.textContent = 'No plugins installed yet';
      container.appendChild(noPlugins);
      return;
    }

    // Add installed plugins section
    const installedHeader = document.createElement('h6');
    installedHeader.className = 'mt-4 mb-3';
    installedHeader.innerHTML = '<i class="bi bi-puzzle me-2"></i>Installed Plugins';
    container.appendChild(installedHeader);

    plugins.forEach(plugin => {
      const card = document.createElement('div');
      card.className = 'card mb-3';

      // Check if plugin has settings (self-reported by plugin)
      const hasSettings = plugin.has_settings === true;

      console.log(`Plugin ${plugin.id}: hasSettings = ${hasSettings}`);

      const configureButton = hasSettings ?
        `<button class="btn btn-sm btn-outline-secondary" onclick="showPluginSettings('${plugin.id}')">
          <i class="bi bi-gear"></i> Configure
        </button>` : '';

      card.innerHTML = `
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-start">
            <div class="flex-grow-1">
              <h5 class="card-title mb-1">${plugin.name}</h5>
              <p class="card-text text-muted small mb-2">${plugin.description || 'No description'}</p>
              <p class="card-text mb-0">
                <small class="text-muted">
                  Version: ${plugin.version} | Author: ${plugin.author} | ID: ${plugin.id}
                </small>
              </p>
            </div>
            <div class="d-flex align-items-center gap-2">
              ${configureButton}
              <div class="form-check form-switch">
                <input class="form-check-input" type="checkbox"
                       id="plugin-toggle-${plugin.id}"
                       ${plugin.enabled ? 'checked' : ''}
                       onchange="togglePlugin('${plugin.id}', this.checked)">
                <label class="form-check-label" for="plugin-toggle-${plugin.id}">
                  ${plugin.enabled ? 'Enabled' : 'Disabled'}
                </label>
              </div>
              <button class="btn btn-sm btn-danger" onclick="deletePlugin('${plugin.id}', '${plugin.name}')">
                <i class="bi bi-trash"></i>
              </button>
            </div>
          </div>
          <div id="plugin-settings-${plugin.id}" class="plugin-settings-panel" style="display: none;"></div>
        </div>
      `;
      container.appendChild(card);
    });
  });
}

/**
 * Show plugin settings
 */
function showPluginSettings(pluginId) {
  const panel = document.getElementById(`plugin-settings-${pluginId}`);
  if (!panel) return;

  // Toggle visibility
  if (panel.style.display === 'none') {
    // Load settings
    fetch(`/plugin/${pluginId}/settings`)
      .then(response => response.text())
      .then(html => {
        panel.innerHTML = html;
        panel.style.display = 'block';

        // Execute any scripts in the settings HTML
        executePluginScripts(panel);
      })
      .catch(error => {
        console.error('Error loading plugin settings:', error);
        alert('Failed to load plugin settings');
      });
  } else {
    panel.style.display = 'none';
    panel.innerHTML = '';
  }
}

/**
 * Toggle plugin enabled/disabled
 */
function togglePlugin(pluginId, enabled) {
  if (enabled) {
    enablePlugin(pluginId);
  } else {
    disablePlugin(pluginId);
  }
}

// Initialize plugins on page load
document.addEventListener('DOMContentLoaded', () => {
  // Load plugin UI elements
  loadPluginUI();

  // If on settings page, load plugin manager
  if (document.getElementById('pluginManagerList')) {
    renderPluginManager();
  }
});
