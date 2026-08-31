"""
plugin_store.py - catalog and updates for ChitUI plugins.

The Plugin Store UI (Settings -> Plugins -> Plugin Store) has always called
/plugins/store/catalog and /plugins/store/install. This module is what answers
them, plus the update counting that drives the "plugin updates available"
warning on the main screen.

Catalog format
--------------
The catalog is a JSON document served over HTTPS. Either shape works:

    {"plugins": [ ... ]}          or          [ ... ]

Each entry is matched against the installed plugin whose folder name equals
its slug. Only "slug", "name" and "version" are required:

    {
      "slug": "tapo_p100",                       # must equal the folder name
      "name": "Tapo P100 Smart Plugs",
      "version": "1.5.0",
      "author": "xmodpt",
      "short_description": "Control TP-Link Tapo smart plugs.",
      "download_url": "https://.../tapo_p100.zip",
      "detail_url": "https://www.chitui.net/plugins/tapo-p100",
      "min_chitui_version": "2.4.0"              # optional gate
    }

A number of aliases are accepted for each field (id/folder for slug,
url/zip_url for download_url, and so on) so a hand-written catalog does not
have to match one exact spelling.

Plugin settings live in ~/.chitui/, never inside the plugin folder, so
replacing a plugin directory during an update cannot lose a user's config.
The old folder is still moved aside first and restored if anything fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import re
from concurrent.futures import ThreadPoolExecutor

import requests
from loguru import logger

import updater
from updater import compare_versions, is_newer

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(PROJECT_ROOT, 'data')
CACHE_FILE = os.path.join(DATA_FOLDER, 'plugin_store_cache.json')
PLUGIN_BACKUP_DIR = os.path.join(DATA_FOLDER, 'backups', 'plugins')

DEFAULT_STORE_URL = "https://www.chitui.net/plugins.json"

HTTP_TIMEOUT = 20
MAX_ZIP_BYTES = 50 * 1024 * 1024      # a plugin has no business being bigger
KEEP_PLUGIN_BACKUPS = 3

DEFAULT_STORE_SETTINGS = {
    "enabled": True,
    "url": DEFAULT_STORE_URL,
    "check_on_load": True,
    "check_interval_hours": 6,
}


# ============================================================================
# SETTINGS
# ============================================================================

def get_store_settings(settings: dict) -> dict:
    """Read the "plugin_store" block out of chitui_settings.json."""
    merged = dict(DEFAULT_STORE_SETTINGS)
    stored = (settings or {}).get('plugin_store')
    if isinstance(stored, dict):
        for key in DEFAULT_STORE_SETTINGS:
            if key in stored:
                merged[key] = stored[key]

    try:
        hours = float(merged.get('check_interval_hours', 6))
    except (TypeError, ValueError):
        hours = 6.0
    merged['check_interval_hours'] = max(0.25, min(hours, 24 * 7))

    url = str(merged.get('url') or '').strip()
    if not url.startswith(('http://', 'https://')):
        url = DEFAULT_STORE_URL
    merged['url'] = url

    for flag in ('enabled', 'check_on_load'):
        merged[flag] = bool(merged.get(flag))

    return merged


def merge_store_settings(settings: dict, patch: dict) -> dict:
    current = get_store_settings(settings)
    for key in DEFAULT_STORE_SETTINGS:
        if key in patch:
            current[key] = patch[key]
    settings = dict(settings or {})
    settings['plugin_store'] = get_store_settings({'plugin_store': current})
    return settings


# ============================================================================
# CACHE
# ============================================================================

def _read_cache() -> dict:
    try:
        with open(CACHE_FILE, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_cache(data: dict) -> None:
    try:
        os.makedirs(DATA_FOLDER, exist_ok=True)
        tmp = CACHE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, CACHE_FILE)
    except Exception as exc:
        logger.debug(f"Could not write plugin store cache: {exc}")


# ============================================================================
# CATALOG NORMALISATION
# ============================================================================

def _first(entry: dict, *keys, default=None):
    """Return the first key present and non-empty - catalogs vary in spelling."""
    for key in keys:
        value = entry.get(key)
        if value not in (None, '', []):
            return value
    return default


def _normalise_entry(entry: dict):
    """Coerce one catalog entry into the shape the frontend expects."""
    if not isinstance(entry, dict):
        return None

    slug = _first(entry, 'slug', 'id', 'folder', 'directory', 'plugin_id')
    name = _first(entry, 'name', 'title', default=slug)
    version = _first(entry, 'version', 'latest_version', default=None)

    if not slug or not name or not version:
        return None

    return {
        'slug': str(slug).strip(),
        'name': str(name).strip(),
        'version': str(version).strip().lstrip('vV'),
        'author': _first(entry, 'author', 'maintainer', default=''),
        'short_description': _first(entry, 'short_description', 'description',
                                    'summary', default=''),
        'download_url': _first(entry, 'download_url', 'url', 'zip_url',
                               'zip', 'asset_url'),
        'detail_url': _first(entry, 'detail_url', 'homepage', 'page', 'info_url'),
        'min_chitui_version': _first(entry, 'min_chitui_version',
                                     'requires_chitui', 'min_version'),
    }


def _fallback_or_fail(url, cache, same_source, json_error):
    """Scrape the store's HTML when the JSON catalog is unavailable.

    Kept as a fallback rather than the primary path: scraping breaks whenever
    the site's markup changes, so a published catalog should always win. But
    a store that has no catalog yet must not be a dead store - that regression
    is what took the Plugin Store offline when the JSON fetcher replaced
    2.3.0's scraper.
    """
    entries, scrape_error = scrape_catalog(url)
    if entries:
        logger.info(f"Plugin store: no JSON catalog at {url}, "
                    f"read {len(entries)} plugins from the store page instead")
        _write_cache({
            'entries': entries,
            'etag': None,          # scraped pages carry no catalog ETag
            'url': url,
            'checked_at': time.time(),
        })
        return entries, {
            'checked_at': time.time(), 'from_cache': False,
            'error': None, 'url': url, 'scraped': True,
        }

    logger.debug(f"Plugin store: HTML fallback also failed: {scrape_error}")
    return cache.get('entries', []) if same_source else [], {
        'checked_at': cache.get('checked_at'),
        'from_cache': True,
        'error': f"{json_error} Reading the store page instead also failed: {scrape_error}",
        'url': url,
    }


def fetch_catalog(store_settings: dict, force: bool = False):
    """
    Fetch and cache the remote catalog.

    Returns (entries, meta) where meta carries checked_at / from_cache / error.
    Entries is always a list, possibly empty.
    """
    url = store_settings.get('url') or DEFAULT_STORE_URL
    cache = _read_cache()
    ttl = float(store_settings.get('check_interval_hours', 6)) * 3600

    same_source = cache.get('url') == url
    fresh = (
        not force
        and same_source
        and 'entries' in cache
        and (time.time() - (cache.get('checked_at') or 0)) < ttl
    )

    if fresh:
        return cache['entries'], {
            'checked_at': cache.get('checked_at'),
            'from_cache': True,
            'error': None,
            'url': url,
        }

    headers = {'Accept': 'application/json', 'User-Agent': 'ChitUI-PluginStore'}
    if same_source and cache.get('etag'):
        headers['If-None-Match'] = cache['etag']

    try:
        response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        return cache.get('entries', []) if same_source else [], {
            'checked_at': cache.get('checked_at'),
            'from_cache': True,
            'error': f"Could not reach the plugin store: {exc}",
            'url': url,
        }

    if response.status_code == 304 and same_source:
        cache['checked_at'] = time.time()
        _write_cache(cache)
        return cache.get('entries', []), {
            'checked_at': cache['checked_at'], 'from_cache': True,
            'error': None, 'url': url,
        }

    if response.status_code != 200:
        return _fallback_or_fail(
            url, cache, same_source,
            f"The plugin store returned HTTP {response.status_code} for {url}.")

    try:
        payload = response.json()
    except ValueError:
        # Say what actually came back. A catalog URL that points at a human
        # page rather than a JSON document is by far the most common cause,
        # and "did not return valid JSON" on its own gives no way to tell that
        # apart from a corrupted response.
        content_type = (response.headers.get('Content-Type') or 'unknown').split(';')[0]
        preview = (response.text or '')[:80].replace('\n', ' ').strip()
        return _fallback_or_fail(
            url, cache, same_source,
            f"{url} returned {content_type}, not JSON"
            + (f" (starts with: {preview})" if preview else "")
            + ". The catalog URL must point at a JSON document.")

    raw = payload.get('plugins') if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        return [], {
            'checked_at': None, 'from_cache': False,
            'error': "Unexpected catalog format - expected a list of plugins.",
            'url': url,
        }

    entries = []
    skipped = 0
    for item in raw:
        normalised = _normalise_entry(item)
        if normalised:
            entries.append(normalised)
        else:
            skipped += 1
    if skipped:
        logger.warning(f"Plugin store: skipped {skipped} malformed catalog entries")

    _write_cache({
        'entries': entries,
        'etag': response.headers.get('ETag'),
        'url': url,
        'checked_at': time.time(),
    })

    return entries, {
        'checked_at': time.time(), 'from_cache': False,
        'error': None, 'url': url,
    }


# ============================================================================
# HTML FALLBACK
# ============================================================================
#
# ChitUI 2.3.0 had no JSON catalog. Its store scraped chitui.net/plugins.php
# directly, which is why it worked against a site that has never served a
# plugins.json. Moving to a JSON catalog was the right call - scraping breaks
# every time the site's markup is touched - but shipping the JSON fetcher
# before the catalog existed turned a working store into a permanent HTTP 404.
#
# So the JSON path stays primary and this runs only when it fails. A site that
# publishes a catalog never reaches this code; one that does not keeps working
# exactly as it did in 2.3.0.

STORE_LISTING_PATH = "/plugins.php"
SCRAPE_TIMEOUT = 10
SCRAPE_MAX_DETAIL_PAGES = 40


def _scrape_listing(session, base_url):
    """Parse the plugin cards out of plugins.php. Returns partial entries."""
    from bs4 import BeautifulSoup

    listing_url = base_url + STORE_LISTING_PATH
    response = session.get(listing_url, timeout=SCRAPE_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    found = {}
    # Each card is an <a> wrapping the whole tile and linking to the detail
    # page, so the slug comes straight out of the href.
    for card in soup.find_all('a', href=re.compile(r'plugin\.php\?slug=')):
        match = re.search(r'slug=([^&"\']+)', card.get('href', ''))
        if not match:
            continue
        slug = match.group(1)
        if slug in found:
            continue

        entry = {
            'slug': slug,
            'name': slug,
            'version': '',
            'short_description': '',
            'detail_url': f"{base_url}/plugin.php?slug={slug}",
            'download_url': None,
        }

        heading = card.find(['h3', 'h2', 'h4'])
        if heading:
            entry['name'] = heading.get_text(strip=True)

        description = card.find('p')
        if description:
            entry['short_description'] = description.get_text(strip=True)

        # The card prints the version as "v1.5.0" near the download count.
        version_match = re.search(r'\bv(\d+\.\d+(?:\.\d+)?)', card.get_text(' ', strip=True))
        if version_match:
            entry['version'] = version_match.group(1)

        found[slug] = entry

    return list(found.values())


def _scrape_detail(session, base_url, entry):
    """Fill in a plugin's download URL (and a better version) from its page."""
    from bs4 import BeautifulSoup

    try:
        response = session.get(entry['detail_url'], timeout=SCRAPE_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
    except Exception as exc:
        logger.debug(f"Plugin store: no detail page for {entry['slug']}: {exc}")
        return entry

    for text in soup.find_all(string=re.compile(r'Version\s+\d', re.I)):
        version_match = re.search(r'Version\s+v?([\d.]+)', text, re.I)
        if version_match:
            entry['version'] = version_match.group(1).rstrip('.')
            break

    def absolute(href):
        return href if href.startswith('http') else base_url + '/' + href.lstrip('/')

    links = [a['href'] for a in soup.find_all('a', href=True)]

    # A real .zip link beats download.php, which could also be serving the PDF
    # manual - hence the explicit type=plugin check on the second pass.
    for href in links:
        if href.endswith('.zip') or '.zip?' in href:
            entry['download_url'] = absolute(href)
            return entry

    for href in links:
        if 'download.php' in href.lower() and 'type=plugin' in href.lower():
            entry['download_url'] = absolute(href)
            return entry

    for href in links:
        if 'download' in href.lower() and 'id=' in href:
            entry['download_url'] = absolute(href)
            return entry

    logger.debug(f"Plugin store: no download link found for {entry['slug']}")
    return entry


def scrape_catalog(url):
    """Build a catalog by scraping the store's HTML pages.

    Returns (entries, error). Entries carry the same keys _normalise_entry
    produces, so callers cannot tell which path produced them.
    """
    try:
        from bs4 import BeautifulSoup  # noqa: F401
    except ImportError:
        return [], "beautifulsoup4 is not installed, so the HTML store cannot be read."

    parsed = requests.utils.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return [], "Invalid store URL."
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    session = requests.Session()
    # Some hosts serve a bot challenge to unknown agents; 2.3.0 used a browser
    # UA here for exactly that reason.
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (compatible; ChitUI-PluginStore/1.0)',
    })

    try:
        entries = _scrape_listing(session, base_url)
    except Exception as exc:
        return [], f"Could not read the plugin store page: {exc}"

    if not entries:
        return [], "The plugin store page listed no plugins."

    if len(entries) > SCRAPE_MAX_DETAIL_PAGES:
        logger.warning(f"Plugin store: {len(entries)} plugins listed, "
                       f"only enriching the first {SCRAPE_MAX_DETAIL_PAGES}")
        entries = entries[:SCRAPE_MAX_DETAIL_PAGES]

    # One request per plugin, so do them concurrently or opening the store
    # takes a second per plugin.
    with ThreadPoolExecutor(max_workers=6) as pool:
        entries = list(pool.map(lambda e: _scrape_detail(session, base_url, e), entries))

    usable = []
    for entry in entries:
        if not entry.get('version'):
            # Without a version there is nothing to compare against, and the
            # card would offer an update on every single refresh.
            logger.debug(f"Plugin store: skipping {entry['slug']} - no version found")
            continue
        normalised = _normalise_entry(entry)
        if normalised:
            usable.append(normalised)

    if not usable:
        return [], "No usable plugins could be read from the plugin store page."

    return usable, None


# ============================================================================
# MERGING WITH WHAT IS INSTALLED
# ============================================================================

def _installed_map(plugin_manager) -> dict:
    """folder name -> {name, version} for every installed plugin."""
    installed = {}
    try:
        for entry in plugin_manager.get_plugin_info():
            installed[entry['id']] = {
                'name': entry.get('name', entry['id']),
                'version': str(entry.get('version', '0.0.0')),
                'enabled': entry.get('enabled', True),
            }
    except Exception as exc:
        logger.error(f"Could not enumerate installed plugins: {exc}")
    return installed


def build_catalog(plugin_manager, store_settings: dict, force: bool = False) -> dict:
    """
    The payload behind /plugins/store/catalog.

    Every catalog entry gains installed / installed_version / has_update, and
    plugins installed locally but absent from the catalog are appended so the
    "Installed" filter shows the complete picture rather than only the ones
    the store happens to know about.
    """
    entries, meta = fetch_catalog(store_settings, force=force)
    installed = _installed_map(plugin_manager)
    chitui_version = updater.get_current_version()

    plugins = []
    seen = set()

    for entry in entries:
        slug = entry['slug']
        seen.add(slug)
        local = installed.get(slug)

        item = dict(entry)
        item['installed'] = local is not None
        item['installed_version'] = local['version'] if local else None
        item['has_update'] = bool(local and is_newer(entry['version'], local['version']))

        # Never offer an update that needs a newer ChitUI than the one running.
        required = entry.get('min_chitui_version')
        if required and compare_versions(chitui_version, required) < 0:
            item['has_update'] = False
            item['blocked'] = True
            item['blocked_reason'] = (
                f"Needs ChitUI {required} or newer (you have {chitui_version})."
            )
        else:
            item['blocked'] = False

        plugins.append(item)

    for slug, local in installed.items():
        if slug in seen:
            continue
        plugins.append({
            'slug': slug,
            'name': local['name'],
            'version': local['version'],
            'author': '',
            'short_description': 'Installed locally - not listed in the plugin store.',
            'download_url': None,
            'detail_url': None,
            'installed': True,
            'installed_version': local['version'],
            'has_update': False,
            'blocked': False,
        })

    plugins.sort(key=lambda p: (not p.get('has_update'), p['name'].lower()))

    return {
        # 'success' stays true when the fetch failed but we still have local
        # plugins to show, so the dialog can list them instead of going blank.
        # That means 'error' can be set on a successful response, and the
        # frontend has to look at it - catalog_count tells it whether anything
        # at all came from the store, which is the difference between "the
        # store is down" and "the store is fine but has nothing new".
        'success': meta['error'] is None or bool(plugins),
        'plugins': plugins,
        'catalog_count': len(entries),
        'update_count': sum(1 for p in plugins if p.get('has_update')),
        'checked_at': meta['checked_at'],
        'from_cache': meta['from_cache'],
        'error': meta['error'],
        'store_url': meta['url'],
    }


def count_updates(plugin_manager, store_settings: dict, force: bool = False) -> dict:
    """Cheap summary for the main-screen warning banner."""
    catalog = build_catalog(plugin_manager, store_settings, force=force)
    pending = [
        {'slug': p['slug'], 'name': p['name'],
         'from': p['installed_version'], 'to': p['version']}
        for p in catalog['plugins'] if p.get('has_update')
    ]
    return {
        'success': catalog['success'],
        'update_count': len(pending),
        'updates': pending,
        'checked_at': catalog['checked_at'],
        'error': catalog['error'],
    }


# ============================================================================
# INSTALL / UPDATE
# ============================================================================

def _log(q, message, level='info'):
    logger.info(f"[plugin-store] {message}")
    q.put({'type': 'log', 'msg': str(message), 'level': level})


def _prune_plugin_backups(slug):
    try:
        entries = sorted(
            (os.path.join(PLUGIN_BACKUP_DIR, d) for d in os.listdir(PLUGIN_BACKUP_DIR)
             if d.startswith(slug + '-')),
            key=os.path.getmtime, reverse=True,
        )
        for old in entries[KEEP_PLUGIN_BACKUPS:]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass


def _download_zip(q, url, dest_path):
    """Stream a plugin ZIP to disk, refusing anything oversized or non-zip."""
    _log(q, f"Downloading from {url}")
    try:
        with requests.get(url, stream=True, timeout=HTTP_TIMEOUT,
                          allow_redirects=True,
                          headers={'User-Agent': 'ChitUI-PluginStore'}) as response:
            if response.status_code != 200:
                return False, f"Download failed with HTTP {response.status_code}."

            declared = response.headers.get('Content-Length')
            if declared and int(declared) > MAX_ZIP_BYTES:
                return False, (f"Refusing to download {int(declared) // (1024*1024)} MB - "
                               f"the limit for a plugin is {MAX_ZIP_BYTES // (1024*1024)} MB.")

            written = 0
            with open(dest_path, 'wb') as out:
                for chunk in response.iter_content(chunk_size=128 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MAX_ZIP_BYTES:
                        return False, ("The download exceeded the "
                                       f"{MAX_ZIP_BYTES // (1024*1024)} MB plugin size limit.")
                    out.write(chunk)
    except requests.exceptions.RequestException as exc:
        return False, f"Download failed: {exc}"

    _log(q, f"Downloaded {written / 1024:.0f} KB")

    if not zipfile.is_zipfile(dest_path):
        return False, "The downloaded file is not a ZIP archive."

    return True, None


def _safe_extract(q, zip_path, target_dir):
    """Extract a ZIP, rejecting absolute paths and directory traversal."""
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            normalised = member.replace('\\', '/')
            if normalised.startswith('/') or '..' in normalised.split('/'):
                return False, f"Refusing unsafe path in archive: {member}"
        archive.extractall(target_dir)
    return True, None


def _install_dependencies(q, plugin_dir, manifest):
    """Install a plugin's Python dependencies, mirroring the upload installer."""
    reqs = os.path.join(plugin_dir, 'requirements.txt')
    deps = manifest.get('dependencies') or []

    if os.path.exists(reqs):
        cmd = [sys.executable, '-m', 'pip', 'install', '-r', reqs, '--break-system-packages']
    elif deps:
        cmd = [sys.executable, '-m', 'pip', 'install', *deps, '--break-system-packages']
    else:
        _log(q, "No Python dependencies required.")
        return True

    _log(q, "Installing Python dependencies...")
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=1800)
        for line in (proc.stdout or '').splitlines()[-25:]:
            _log(q, f"    {line}")
        if proc.returncode == 0:
            _log(q, "Dependencies installed.", 'ok')
            return True
        _log(q, f"pip exited with code {proc.returncode}.", 'warn')
        return False
    except Exception as exc:
        _log(q, f"Dependency install failed: {exc}", 'warn')
        return False


def install_worker(q, plugin_manager, app, socketio, slug, download_url,
                   expected_name=None):
    """
    Download, verify and install (or update) one plugin.

    Pushes log frames onto q in exactly the format the existing
    /plugins/install/<job_id>/stream endpoint and its frontend already speak,
    so the Plugin Store reuses the terminal UI unchanged.
    """
    # Clear anything a previously crashed install left on the SD card before
    # pulling down a new archive.
    updater.cleanup_temp_files("pre-plugin-install")

    work_dir = updater._make_work_dir('chitui-plugin-')
    zip_path = os.path.join(work_dir, 'plugin.zip')
    target_path = os.path.join(plugin_manager.plugins_dir, slug)
    backup_path = None
    is_update = os.path.isdir(target_path)

    try:
        _log(q, f"{'Updating' if is_update else 'Installing'} plugin: {expected_name or slug}")

        ok, error = _download_zip(q, download_url, zip_path)
        if not ok:
            q.put({'type': 'done', 'success': False, 'message': error})
            return

        extract_dir = os.path.join(work_dir, 'extracted')
        os.makedirs(extract_dir, exist_ok=True)
        _log(q, "Unpacking archive...")
        ok, error = _safe_extract(q, zip_path, extract_dir)
        if not ok:
            q.put({'type': 'done', 'success': False, 'message': error})
            return

        # Unpacked - the archive is dead weight from here on.
        try:
            freed = os.path.getsize(zip_path)
            os.unlink(zip_path)
            _log(q, f"Removed the downloaded archive ({freed / 1024:.0f} KB freed).")
        except OSError:
            pass

        dirs = [d for d in os.listdir(extract_dir)
                if os.path.isdir(os.path.join(extract_dir, d))]
        if len(dirs) != 1:
            q.put({'type': 'done', 'success': False,
                   'message': "The ZIP must contain exactly one top-level folder."})
            return
        source_dir = os.path.join(extract_dir, dirs[0])

        manifest_path = os.path.join(source_dir, 'plugin.json')
        init_path = os.path.join(source_dir, '__init__.py')
        if not os.path.exists(manifest_path) or not os.path.exists(init_path):
            q.put({'type': 'done', 'success': False,
                   'message': "Invalid plugin: plugin.json or __init__.py is missing."})
            return

        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except json.JSONDecodeError as exc:
            q.put({'type': 'done', 'success': False,
                   'message': f"plugin.json is not valid JSON: {exc}"})
            return

        for field in ('name', 'version', 'author'):
            if field not in manifest:
                q.put({'type': 'done', 'success': False,
                       'message': f"plugin.json is missing the '{field}' field."})
                return

        _log(q, f"Package: {manifest['name']} v{manifest['version']} by {manifest['author']}")

        # The folder inside the ZIP must match the slug we were asked to
        # install, otherwise a catalog entry could overwrite a different plugin.
        if dirs[0] != slug:
            _log(q, f"Archive folder '{dirs[0]}' does not match the expected "
                    f"'{slug}' - using the archive's own name.", 'warn')
            target_path = os.path.join(plugin_manager.plugins_dir, dirs[0])
            is_update = os.path.isdir(target_path)

        # ── Move the existing version aside so a failure can be undone ──
        if is_update:
            os.makedirs(PLUGIN_BACKUP_DIR, exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            backup_path = os.path.join(PLUGIN_BACKUP_DIR, f"{os.path.basename(target_path)}-{stamp}")
            shutil.move(target_path, backup_path)
            _log(q, f"Previous version backed up to data/backups/plugins/"
                    f"{os.path.basename(backup_path)}")
            _log(q, "Your plugin settings live in ~/.chitui/ and are not affected.")

        try:
            shutil.copytree(source_dir, target_path)
        except Exception as exc:
            _log(q, f"Could not copy plugin files: {exc}", 'error')
            if backup_path:
                shutil.rmtree(target_path, ignore_errors=True)
                shutil.move(backup_path, target_path)
                _log(q, "Restored the previous version.", 'warn')
            q.put({'type': 'done', 'success': False, 'message': str(exc)})
            return

        _install_dependencies(q, target_path, manifest)

        # ── Hot-load so the plugin works without a restart where possible ──
        _log(q, "Activating plugin...")
        try:
            result = plugin_manager.load_plugin(os.path.basename(target_path), app, socketio)
            if result is not None:
                _log(q, f"'{manifest['name']}' is active.", 'ok')
            else:
                _log(q, "Installed, but could not be activated in the running "
                        "server. Restart ChitUI to finish.", 'warn')
        except Exception as exc:
            _log(q, f"Installed, but activation failed ({exc}). "
                    f"Restart ChitUI to finish.", 'warn')

        if backup_path:
            _prune_plugin_backups(os.path.basename(target_path))

        _log(q, f"{manifest['name']} {manifest['version']} "
                f"{'updated' if is_update else 'installed'} successfully.", 'ok')

        q.put({
            'type': 'done',
            'success': True,
            'message': f"{manifest['name']} {manifest['version']} "
                       f"{'updated' if is_update else 'installed'}.",
            'plugin_id': os.path.basename(target_path),
            'plugin_name': manifest['name'],
            'version': manifest['version'],
            'was_update': is_update,
        })

    except Exception as exc:
        logger.exception("Plugin store install failed")
        _log(q, f"Unexpected error: {exc}", 'error')
        if backup_path and not os.path.isdir(target_path):
            try:
                shutil.move(backup_path, target_path)
                _log(q, "Restored the previous version.", 'warn')
            except Exception:
                pass
        q.put({'type': 'done', 'success': False, 'message': str(exc)})
    finally:
        # Runs on every exit path, including the early returns above.
        shutil.rmtree(work_dir, ignore_errors=True)
        if os.path.exists(work_dir):
            _log(q, f"Could not remove the temporary folder {work_dir} - "
                    f"delete it by hand to reclaim the space.", 'warn')


def start_install(plugin_manager, app, socketio, jobs_dict, slug,
                  download_url, expected_name=None):
    """Queue an install job and return its id, reusing the existing SSE plumbing."""
    import queue as _queue
    import uuid as _uuid

    job_id = str(_uuid.uuid4())
    log_queue = _queue.Queue()
    jobs_dict[job_id] = log_queue

    thread = threading.Thread(
        target=install_worker,
        args=(log_queue, plugin_manager, app, socketio, slug, download_url, expected_name),
        name=f'plugin-install-{slug}', daemon=True,
    )
    thread.start()
    return job_id