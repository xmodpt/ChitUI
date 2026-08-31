"""
ChitUI Theme System
===================

Allows users to install, select and apply custom UI themes.

A theme is a folder living in  data/themes/<theme_id>/  containing:

    theme.json      - manifest (id, name, version, author, description, css)
    theme.css       - the stylesheet that overrides the default look
    preview.png     - (optional) screenshot shown in the theme picker
    assets/...      - (optional) images / fonts referenced by the CSS

The built-in look ("ChitUI Default Theme") is virtual: it has no folder and
no override CSS. When it is active, /themes/active.css returns an empty
stylesheet, so the stock  web/css/chitui.css  is what the user sees.

How the CSS is applied
----------------------
index.html / login.html / change-password.html all load:

    <link href="/themes/active.css" rel="stylesheet" />

AFTER chitui.css. The route below serves the active theme's CSS (or an empty
file for the default theme), so switching themes never requires editing HTML.

Theme assets are reachable at a STABLE url that does not depend on the
theme id:

    /themes/active/assets/<file>        -> active theme's folder
    /themes/<theme_id>/assets/<file>    -> specific theme (used for previews)

Only safe static file types are served (css/images/fonts). Themes cannot
ship or execute JavaScript or Python - they are pure CSS skins.

Wiring
------
main.py calls  init_themes(app, ...)  once, after login_required and the
settings helpers are defined.
"""

import io
import os
import re
import json
import shutil
import zipfile

from flask import jsonify, request, send_from_directory, send_file, Response

# File types a theme is allowed to serve
ALLOWED_ASSET_EXTENSIONS = {
    '.css', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg',
    '.woff', '.woff2', '.ttf', '.otf', '.ico'
}

MAX_THEME_ZIP_SIZE = 20 * 1024 * 1024        # 20 MB compressed
MAX_THEME_UNPACKED_SIZE = 50 * 1024 * 1024   # 50 MB uncompressed

DEFAULT_THEME = {
    "id": "default",
    "name": "ChitUI Default Theme",
    "version": "1.0.0",
    "author": "ChitUI",
    "description": "The stock ChitUI look. Dark, red accent, Bootstrap 5.",
    "builtin": True,
}

_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{1,49}$')


def init_themes(app, *, login_required, load_settings, save_settings,
                data_folder, project_root, logger):
    """Register all theme routes on the Flask app."""

    themes_folder = os.path.join(data_folder, 'themes')
    os.makedirs(themes_folder, exist_ok=True)

    template_folder = os.path.join(project_root, 'theme_template')

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _read_manifest(theme_id):
        """Read and normalise a theme.json manifest. Returns dict or None."""
        manifest_path = os.path.join(themes_folder, theme_id, 'theme.json')
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
        except Exception as e:
            logger.warning(f"Theme '{theme_id}': cannot read theme.json ({e})")
            return None

        manifest['id'] = theme_id  # folder name is authoritative
        manifest.setdefault('name', theme_id)
        manifest.setdefault('version', '0.0.0')
        manifest.setdefault('author', 'Unknown')
        manifest.setdefault('description', '')
        manifest.setdefault('css', 'theme.css')
        manifest['builtin'] = False

        css_path = os.path.join(themes_folder, theme_id, manifest['css'])
        if not os.path.isfile(css_path):
            logger.warning(f"Theme '{theme_id}': CSS file '{manifest['css']}' missing")
            return None

        # Preview url if a preview image exists
        for candidate in (manifest.get('preview'), 'preview.png', 'preview.jpg', 'preview.webp'):
            if candidate and os.path.isfile(os.path.join(themes_folder, theme_id, candidate)):
                manifest['preview_url'] = f'/themes/{theme_id}/assets/{candidate}'
                break

        return manifest

    def _installed_themes():
        themes = []
        try:
            for entry in sorted(os.listdir(themes_folder)):
                if not os.path.isdir(os.path.join(themes_folder, entry)):
                    continue
                manifest = _read_manifest(entry)
                if manifest:
                    themes.append(manifest)
        except FileNotFoundError:
            pass
        return themes

    # Active theme is persisted in its OWN file, not in settings.json.
    # (ChitUI's settings save rebuilds settings.json from known fields,
    #  which silently dropped the theme choice on the next save/reboot.)
    active_theme_file = os.path.join(themes_folder, 'active_theme.json')

    def _active_theme_id():
        theme_id = None
        try:
            with open(active_theme_file, 'r') as f:
                theme_id = json.load(f).get('theme')
        except FileNotFoundError:
            # One-time migration from the old settings.json location
            try:
                theme_id = load_settings().get('ui', {}).get('theme')
            except Exception:
                theme_id = None
        except Exception as e:
            logger.warning(f"Could not read active theme file: {e}")

        if not theme_id:
            return 'default'
        # If the stored theme is currently missing/invalid, FALL BACK to
        # default for this request only - the stored choice is kept, so the
        # theme comes back automatically if it is reinstalled.
        if theme_id != 'default' and _read_manifest(theme_id) is None:
            return 'default'
        return theme_id

    def _active_mode():
        """Preferred color mode ('light'/'dark') saved with the theme."""
        try:
            with open(active_theme_file, 'r') as f:
                mode = json.load(f).get('mode')
                return mode if mode in ('light', 'dark') else 'dark'
        except Exception:
            return 'dark'

    def _set_active_theme(theme_id, mode=None):
        try:
            if mode not in ('light', 'dark'):
                mode = _active_mode()
            tmp = active_theme_file + '.tmp'
            with open(tmp, 'w') as f:
                json.dump({'theme': theme_id, 'mode': mode}, f)
            os.replace(tmp, active_theme_file)   # atomic - survives power loss
            return True
        except Exception as e:
            logger.error(f"Failed to save active theme: {e}")
            return False

    def _asset_allowed(filename):
        return os.path.splitext(filename)[1].lower() in ALLOWED_ASSET_EXTENSIONS

    # ------------------------------------------------------------------ #
    # Public routes (no auth - pure static content, needed on login page) #
    # ------------------------------------------------------------------ #

    @app.route('/themes/active.css')
    def theme_active_css():
        """CSS of the currently active theme. Empty file for the default theme."""
        theme_id = _active_theme_id()
        if theme_id == 'default':
            resp = Response('/* ChitUI Default Theme - no overrides */\n',
                            mimetype='text/css')
        else:
            manifest = _read_manifest(theme_id)
            css_file = manifest['css'] if manifest else 'theme.css'
            resp = send_from_directory(os.path.join(themes_folder, theme_id),
                                       css_file, mimetype='text/css')
        # Always revalidate so a theme switch is picked up on next page load
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    @app.route('/themes/active/assets/<path:filename>')
    def theme_active_asset(filename):
        """Assets of the ACTIVE theme - stable url for use inside theme.css."""
        theme_id = _active_theme_id()
        if theme_id == 'default' or not _asset_allowed(filename):
            return jsonify({"error": "Not found"}), 404
        return send_from_directory(os.path.join(themes_folder, theme_id), filename)

    @app.route('/themes/<theme_id>/assets/<path:filename>')
    def theme_asset(theme_id, filename):
        """Assets of a specific theme (previews in the theme picker)."""
        if not _ID_RE.match(theme_id) or not _asset_allowed(filename):
            return jsonify({"error": "Not found"}), 404
        return send_from_directory(os.path.join(themes_folder, theme_id), filename)

    # ------------------------------------------------------------------ #
    # Management routes (auth required)                                  #
    # ------------------------------------------------------------------ #

    @app.route('/themes', methods=['GET'])
    @login_required
    def themes_list():
        """List all themes (default + installed) and which one is active."""
        default = dict(DEFAULT_THEME)
        # Preview for the built-in theme. It lives in web/img/themes/ rather
        # than web/img/ because /printer/images lists every image sitting
        # directly in web/img - so a theme screenshot parked there turned up
        # in the printer picture picker. os.listdir() does not recurse, so a
        # subfolder keeps it out of that list for good.
        #
        # The legacy path is still honoured so an installation that upgrades
        # main.py without moving the file does not silently lose its preview.
        for rel_path, url in (
            (('web', 'img', 'themes', 'default-preview.png'), '/img/themes/default-preview.png'),
            (('web', 'img', 'theme_default_preview.png'), '/img/theme_default_preview.png'),
        ):
            if os.path.isfile(os.path.join(project_root, *rel_path)):
                default['preview_url'] = url
                break
        return jsonify({
            "success": True,
            "active": _active_theme_id(),
            "active_mode": _active_mode(),
            "themes": [default] + _installed_themes(),
        })

    @app.route('/themes/apply', methods=['POST'])
    @login_required
    def themes_apply():
        """Set the active theme. Body: {"theme_id": "..."}"""
        data = request.get_json(silent=True) or {}
        theme_id = data.get('theme_id', '')
        mode = data.get('mode')  # optional: 'light' | 'dark'
        if mode is not None and mode not in ('light', 'dark'):
            return jsonify({"success": False, "message": "mode must be 'light' or 'dark'"}), 400

        if theme_id != 'default':
            if not _ID_RE.match(theme_id):
                return jsonify({"success": False, "message": "Invalid theme id"}), 400
            if _read_manifest(theme_id) is None:
                return jsonify({"success": False, "message": "Theme not found or invalid"}), 404

        if not _set_active_theme(theme_id, mode):
            return jsonify({"success": False, "message": "Failed to save theme selection"}), 500

        logger.info(f"UI theme changed to '{theme_id}' (mode: {mode or 'unchanged'})")
        return jsonify({"success": True, "active": theme_id, "active_mode": _active_mode()})

    @app.route('/themes/<theme_id>/delete', methods=['POST'])
    @login_required
    def themes_delete(theme_id):
        """Delete an installed theme. Reverts to default if it was active."""
        if theme_id == 'default':
            return jsonify({"success": False, "message": "The default theme cannot be deleted"}), 400
        if not _ID_RE.match(theme_id):
            return jsonify({"success": False, "message": "Invalid theme id"}), 400

        theme_path = os.path.join(themes_folder, theme_id)
        if not os.path.isdir(theme_path):
            return jsonify({"success": False, "message": "Theme not found"}), 404

        reverted = False
        if _active_theme_id() == theme_id:
            _set_active_theme('default')
            reverted = True

        try:
            shutil.rmtree(theme_path)
        except Exception as e:
            logger.error(f"Failed to delete theme '{theme_id}': {e}")
            return jsonify({"success": False, "message": f"Failed to delete: {e}"}), 500

        logger.info(f"Theme '{theme_id}' deleted" + (" (was active, reverted to default)" if reverted else ""))
        return jsonify({"success": True, "reverted_to_default": reverted})

    @app.route('/themes/upload', methods=['POST'])
    @login_required
    def themes_upload():
        """
        Install a theme from an uploaded ZIP.

        Accepted ZIP layouts:
            my_theme.zip
            └── theme.json / theme.css / ...          (files at zip root)
        or
            my_theme.zip
            └── my_theme/
                └── theme.json / theme.css / ...      (one top-level folder)
        """
        if 'theme' not in request.files:
            return jsonify({"success": False, "message": "No theme file provided"}), 400

        theme_file = request.files['theme']
        if not theme_file.filename:
            return jsonify({"success": False, "message": "No file selected"}), 400
        if not theme_file.filename.lower().endswith('.zip'):
            return jsonify({"success": False, "message": "Theme must be a ZIP file"}), 400

        raw = theme_file.read()
        if len(raw) > MAX_THEME_ZIP_SIZE:
            return jsonify({"success": False, "message": "Theme ZIP exceeds 20 MB limit"}), 400

        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return jsonify({"success": False, "message": "File is not a valid ZIP archive"}), 400

        # ---- Safety: reject path traversal, absolute paths, oversize ----
        total_unpacked = 0
        members = []
        for info in zf.infolist():
            name = info.filename
            if name.startswith('__MACOSX') or name.endswith('.DS_Store'):
                continue
            norm = os.path.normpath(name)
            if norm.startswith('..') or os.path.isabs(norm) or '\\..' in norm:
                return jsonify({"success": False, "message": "ZIP contains unsafe paths"}), 400
            total_unpacked += info.file_size
            if total_unpacked > MAX_THEME_UNPACKED_SIZE:
                return jsonify({"success": False, "message": "Theme unpacks to more than 50 MB"}), 400
            if not info.is_dir():
                members.append(info)

        # ---- Locate theme.json (zip root, or inside one top-level dir) ----
        names = [m.filename for m in members]
        prefix = ''
        if 'theme.json' not in names:
            roots = {n.split('/', 1)[0] for n in names if '/' in n}
            candidates = [r for r in roots if f'{r}/theme.json' in names]
            if len(candidates) != 1:
                return jsonify({
                    "success": False,
                    "message": "theme.json not found. The ZIP must contain theme.json "
                               "at its root or inside a single top-level folder."
                }), 400
            prefix = candidates[0] + '/'

        # ---- Parse and validate the manifest ----
        try:
            manifest = json.loads(zf.read(prefix + 'theme.json').decode('utf-8'))
        except Exception as e:
            return jsonify({"success": False, "message": f"theme.json is not valid JSON: {e}"}), 400

        theme_id = str(manifest.get('id', '')).strip().lower()
        if not _ID_RE.match(theme_id):
            return jsonify({
                "success": False,
                "message": "theme.json must contain an \"id\": 2-50 chars, "
                           "lowercase letters, numbers, - and _ only."
            }), 400
        if theme_id == 'default':
            return jsonify({"success": False, "message": "\"default\" is a reserved theme id"}), 400
        if not manifest.get('name'):
            return jsonify({"success": False, "message": "theme.json must contain a \"name\""}), 400

        css_file = manifest.get('css', 'theme.css')
        if (prefix + css_file) not in names:
            return jsonify({"success": False,
                            "message": f"CSS file '{css_file}' referenced in theme.json is missing from the ZIP"}), 400

        # ---- Install (extract to data/themes/<id>) ----
        theme_path = os.path.join(themes_folder, theme_id)
        updated = os.path.isdir(theme_path)
        tmp_path = theme_path + '.installing'
        if os.path.isdir(tmp_path):
            shutil.rmtree(tmp_path)

        try:
            os.makedirs(tmp_path, exist_ok=True)
            for info in members:
                rel = info.filename[len(prefix):] if info.filename.startswith(prefix) else info.filename
                if not rel:
                    continue
                dest = os.path.join(tmp_path, os.path.normpath(rel))
                # Belt-and-braces: dest must stay inside tmp_path
                if not os.path.abspath(dest).startswith(os.path.abspath(tmp_path) + os.sep):
                    raise ValueError(f"Unsafe path in ZIP: {info.filename}")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(info) as src, open(dest, 'wb') as out:
                    shutil.copyfileobj(src, out)

            if updated:
                shutil.rmtree(theme_path)
            os.replace(tmp_path, theme_path)
        except Exception as e:
            shutil.rmtree(tmp_path, ignore_errors=True)
            logger.error(f"Theme install failed: {e}")
            return jsonify({"success": False, "message": f"Install failed: {e}"}), 500

        installed = _read_manifest(theme_id)
        logger.info(f"Theme '{theme_id}' {'updated' if updated else 'installed'} "
                    f"(v{installed.get('version')} by {installed.get('author')})")
        return jsonify({
            "success": True,
            "updated": updated,
            "theme": installed,
            "message": f"Theme \"{installed['name']}\" {'updated' if updated else 'installed'} successfully",
        })

    @app.route('/themes/template', methods=['GET'])
    @login_required
    def themes_template():
        """Download the starter theme template as a ready-to-edit ZIP."""
        if not os.path.isdir(template_folder):
            return jsonify({"success": False, "message": "Template folder missing on server"}), 404

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(template_folder):
                for fname in files:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, template_folder)
                    zf.write(full, os.path.join('my_theme', rel))
        buf.seek(0)
        return send_file(buf, mimetype='application/zip', as_attachment=True,
                         download_name='chitui_theme_template.zip')

    logger.info(f"Theme system initialised (themes folder: {themes_folder})")
