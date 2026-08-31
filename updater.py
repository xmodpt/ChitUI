"""
updater.py - ChitUI self-update support.

Checks the project's GitHub releases for a newer version and, when the user
asks for it, applies that release to the running installation.

Two update strategies are supported and picked automatically:

  * git      - the install directory is a git working tree. We fetch the tags
               and `git reset --hard <tag>`. This only rewrites *tracked*
               files, so user plugins, uploads and thumbnails (all untracked)
               survive untouched. The handful of tracked files under data/
               are saved and restored around the reset anyway.

  * tarball  - anything else (zip download, rsync'ed copy, ...). We download
               the release tarball from GitHub, unpack it to a temp folder and
               copy it over the install directory, skipping protected paths.

Both strategies take a compressed backup of the code first, so a failed
tarball update can be rolled back automatically.

Nothing here touches data/ - settings, the session secret, uploads,
thumbnails and installed themes are never part of an update.

Public API used by main.py:
    get_current_version()
    get_update_settings(settings) / merge_update_settings(settings, patch)
    check_for_updates(update_settings, force=False)
    set_skipped_version(...)          (helper for the settings merge)
    start_update(release, update_settings) -> UpdateJob
    get_job(job_id)
    detect_supervisor()
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid

import requests
from loguru import logger


# ============================================================================
# PATHS AND CONSTANTS
# ============================================================================

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(PROJECT_ROOT, 'data')
VERSION_FILE = os.path.join(PROJECT_ROOT, 'VERSION')
CACHE_FILE = os.path.join(DATA_FOLDER, 'update_cache.json')
BACKUP_DIR = os.path.join(DATA_FOLDER, 'backups')
PRESERVE_FILE = os.path.join(DATA_FOLDER, 'update_preserve.txt')

# Used only if the VERSION file is missing or unreadable.
FALLBACK_VERSION = "2.3.0"

GITHUB_OWNER = os.environ.get("CHITUI_GITHUB_OWNER", "xmodpt")
GITHUB_REPO = os.environ.get("CHITUI_GITHUB_REPO", "ChitUI")
API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
RELEASES_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

HTTP_TIMEOUT = 20
USER_AGENT = "ChitUI-Updater"

# Top-level names an update must never write to or remove.
PROTECTED_TOP_LEVEL = {
    'data',           # settings, secret key, uploads, thumbnails, themes
    '.git',
    'venv', '.venv', 'env',
    'node_modules',
    'chitui.log',
}

# Tracked files under data/ that a `git reset --hard` could revert.
# Saved before the reset and put back afterwards.
GIT_PRESERVE_PATHS = [
    'data/chitui_settings.json',
    'data/.secret_key',
    'data/file_associations.json',
    'data/themes/active_theme.json',
]

# Free space required before an update is attempted.
MIN_FREE_BYTES = 200 * 1024 * 1024

# How many code backups to keep in data/backups/.
KEEP_BACKUPS = 3

# Scratch space for downloads. main.py points tempfile at data/temp, so this
# lands on the same filesystem as the install and is easy to sweep. Both
# prefixes are recognised by cleanup_temp_files() below.
TEMP_ROOT = os.path.join(DATA_FOLDER, 'temp')
TEMP_PREFIXES = ('chitui-update-', 'chitui-plugin-')

DEFAULT_UPDATE_SETTINGS = {
    "enabled": True,             # master switch for the whole feature
    "check_on_load": True,       # check when the web UI loads
    "channel": "stable",         # "stable" or "prerelease"
    "check_interval_hours": 6,   # cache lifetime; GitHub allows 60 req/h
    "skipped_version": None,     # version the user chose to ignore
    "auto_restart": True,        # restart automatically once the update lands
}


# ============================================================================
# VERSION NUMBERS
# ============================================================================

_VERSION_RE = re.compile(
    r'^\s*[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+](.+))?\s*$'
)


def get_current_version() -> str:
    """Version of the code that is running, from the VERSION file."""
    try:
        with open(VERSION_FILE, 'r') as f:
            value = f.read().strip()
        if value:
            return value
    except Exception:
        pass
    return FALLBACK_VERSION


def parse_version(value):
    """
    Turn "v2.4.1-beta.2" into ((2, 4, 1), ("beta", 2)).

    Returns None when the string doesn't look like a version at all, so the
    caller can decline to compare rather than guess.
    """
    if not value:
        return None
    match = _VERSION_RE.match(str(value))
    if not match:
        return None

    numbers = tuple(int(match.group(i) or 0) for i in (1, 2, 3))

    pre_raw = match.group(4)
    if not pre_raw:
        return (numbers, ())

    parts = []
    for chunk in re.split(r'[.\-]', pre_raw):
        if not chunk:
            continue
        parts.append(int(chunk) if chunk.isdigit() else chunk.lower())
    return (numbers, tuple(parts))


def _compare_prerelease(a, b) -> int:
    """Semver rule: no prerelease outranks any prerelease."""
    if not a and not b:
        return 0
    if not a:
        return 1
    if not b:
        return -1

    for left, right in zip(a, b):
        if left == right:
            continue
        # Numeric identifiers rank below alphanumeric ones.
        left_num, right_num = isinstance(left, int), isinstance(right, int)
        if left_num and right_num:
            return 1 if left > right else -1
        if left_num != right_num:
            return -1 if left_num else 1
        return 1 if str(left) > str(right) else -1

    if len(a) == len(b):
        return 0
    return 1 if len(a) > len(b) else -1


def compare_versions(a, b) -> int:
    """-1 if a < b, 0 if equal, 1 if a > b. Unparseable versions compare 0."""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return 0
    if pa[0] != pb[0]:
        return 1 if pa[0] > pb[0] else -1
    return _compare_prerelease(pa[1], pb[1])


def is_newer(candidate, current) -> bool:
    return compare_versions(candidate, current) > 0


# ============================================================================
# SETTINGS HELPERS
# ============================================================================

def get_update_settings(settings: dict) -> dict:
    """Read the "updates" block out of chitui_settings.json, with defaults."""
    merged = dict(DEFAULT_UPDATE_SETTINGS)
    stored = (settings or {}).get('updates')
    if isinstance(stored, dict):
        for key in DEFAULT_UPDATE_SETTINGS:
            if key in stored:
                merged[key] = stored[key]

    # Clamp the interval - a 0 here would hammer the GitHub API on every
    # page load and burn the 60 requests/hour anonymous quota in a minute.
    try:
        hours = float(merged.get('check_interval_hours', 6))
    except (TypeError, ValueError):
        hours = 6.0
    merged['check_interval_hours'] = max(0.25, min(hours, 24 * 7))

    if merged.get('channel') not in ('stable', 'prerelease'):
        merged['channel'] = 'stable'

    for flag in ('enabled', 'check_on_load', 'auto_restart'):
        merged[flag] = bool(merged.get(flag))

    return merged


def merge_update_settings(settings: dict, patch: dict) -> dict:
    """Apply a partial update-settings patch onto the full settings dict."""
    current = get_update_settings(settings)
    for key in DEFAULT_UPDATE_SETTINGS:
        if key in patch:
            current[key] = patch[key]
    settings = dict(settings or {})
    settings['updates'] = get_update_settings({'updates': current})
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
        logger.debug(f"Could not write update cache: {exc}")


# ============================================================================
# GITHUB RELEASE LOOKUP
# ============================================================================

def _api_headers(etag=None) -> dict:
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': USER_AGENT,
    }
    token = os.environ.get('CHITUI_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if etag:
        headers['If-None-Match'] = etag
    return headers


def _slim_release(raw: dict) -> dict:
    """Keep only the fields the UI and the updater actually need."""
    tag = raw.get('tag_name') or raw.get('name') or ''
    return {
        'tag': tag,
        'version': tag.lstrip('vV'),
        'name': raw.get('name') or tag,
        'body': raw.get('body') or '',
        'html_url': raw.get('html_url') or RELEASES_URL,
        'published_at': raw.get('published_at'),
        'prerelease': bool(raw.get('prerelease')),
        'tarball_url': raw.get('tarball_url') or f"{API_BASE}/tarball/{tag}",
    }


def fetch_latest_release(channel='stable', etag=None):
    """
    Ask GitHub for the newest release on the given channel.

    Returns (status, release_or_None, etag_or_None) where status is one of
    "ok", "not_modified", "rate_limited", "not_found" or "error".
    On "error" the release slot holds the message instead.
    """
    if channel == 'prerelease':
        url = f"{API_BASE}/releases?per_page=20"
    else:
        url = f"{API_BASE}/releases/latest"

    try:
        response = requests.get(url, headers=_api_headers(etag), timeout=HTTP_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        return 'error', f"Could not reach GitHub: {exc}", None

    if response.status_code == 304:
        return 'not_modified', None, etag

    if response.status_code == 404:
        return 'not_found', None, None

    if response.status_code in (403, 429):
        remaining = response.headers.get('X-RateLimit-Remaining')
        if remaining == '0':
            return 'rate_limited', None, None
        return 'error', f"GitHub refused the request (HTTP {response.status_code})", None

    if response.status_code != 200:
        return 'error', f"GitHub returned HTTP {response.status_code}", None

    try:
        payload = response.json()
    except ValueError:
        return 'error', "GitHub returned a response that wasn't JSON", None

    new_etag = response.headers.get('ETag')

    if channel == 'prerelease':
        candidates = [r for r in payload if isinstance(r, dict) and not r.get('draft')]
        if not candidates:
            return 'not_found', None, new_etag
        best = candidates[0]
        for entry in candidates[1:]:
            if is_newer(entry.get('tag_name'), best.get('tag_name')):
                best = entry
        return 'ok', _slim_release(best), new_etag

    if not isinstance(payload, dict) or payload.get('draft'):
        return 'not_found', None, new_etag

    return 'ok', _slim_release(payload), new_etag


def check_for_updates(update_settings: dict, force: bool = False) -> dict:
    """
    Return the current update situation, hitting GitHub only when the cached
    answer has expired (or force=True).

    The result is always a complete dict - the caller never has to guess which
    keys are present.
    """
    current = get_current_version()
    channel = update_settings.get('channel', 'stable')
    skipped = update_settings.get('skipped_version')

    result = {
        'success': True,
        'enabled': bool(update_settings.get('enabled', True)),
        'current_version': current,
        'channel': channel,
        'update_available': False,
        'release': None,
        'latest_version': None,
        'checked_at': None,
        'from_cache': False,
        'skipped': False,
        'skipped_version': skipped,
        'error': None,
        'releases_url': RELEASES_URL,
    }

    if not update_settings.get('enabled', True):
        return result

    cache = _read_cache()
    cached_release = cache.get('release')
    cached_at = cache.get('checked_at') or 0
    cached_channel = cache.get('channel')
    ttl = float(update_settings.get('check_interval_hours', 6)) * 3600
    fresh = (
        not force
        and cached_channel == channel
        and (time.time() - cached_at) < ttl
        and 'release' in cache
    )

    if fresh:
        release = cached_release
        result['from_cache'] = True
        result['checked_at'] = cached_at
    else:
        status, payload, etag = fetch_latest_release(
            channel, cache.get('etag') if cached_channel == channel else None
        )

        if status == 'ok':
            release = payload
            cache = {
                'release': release,
                'etag': etag,
                'channel': channel,
                'checked_at': time.time(),
            }
            _write_cache(cache)
            result['checked_at'] = cache['checked_at']

        elif status == 'not_modified':
            release = cached_release
            cache['checked_at'] = time.time()
            _write_cache(cache)
            result['checked_at'] = cache['checked_at']

        elif status == 'not_found':
            release = None
            result['error'] = "No published releases found for this repository."
            result['checked_at'] = cached_at or None

        elif status == 'rate_limited':
            # Serve whatever we already know rather than showing an error.
            release = cached_release
            result['error'] = ("GitHub's API rate limit was reached. "
                               "Showing the last known result.")
            result['checked_at'] = cached_at or None

        else:
            release = cached_release
            result['error'] = payload if isinstance(payload, str) else "Update check failed."
            result['checked_at'] = cached_at or None

    if not release:
        result['success'] = result['error'] is None
        return result

    latest = release.get('version') or release.get('tag', '')
    result['release'] = release
    result['latest_version'] = latest

    if is_newer(latest, current):
        if skipped and compare_versions(latest, skipped) <= 0:
            result['skipped'] = True
        else:
            result['update_available'] = True

    return result


# ============================================================================
# TEMP FILE HOUSEKEEPING
# ============================================================================

def cleanup_temp_files(reason="startup"):
    """
    Delete leftover update/plugin scratch directories.

    The download paths already clean up in a finally block, but that block
    never runs if the Pi loses power or the process is killed mid-update -
    which would strand a release tarball of tens of megabytes on the SD card
    with nothing to ever remove it. This runs at startup and again just
    before each download, so a previous crash frees its space at exactly the
    moment the next update needs it.

    Returns (directories_removed, bytes_freed).
    """
    removed = 0
    freed = 0

    if not os.path.isdir(TEMP_ROOT):
        return 0, 0

    for name in os.listdir(TEMP_ROOT):
        if not name.startswith(TEMP_PREFIXES):
            continue
        path = os.path.join(TEMP_ROOT, name)
        try:
            if os.path.isdir(path):
                for root, _dirs, files in os.walk(path):
                    for filename in files:
                        try:
                            freed += os.path.getsize(os.path.join(root, filename))
                        except OSError:
                            pass
                shutil.rmtree(path, ignore_errors=True)
            else:
                freed += os.path.getsize(path)
                os.unlink(path)
            removed += 1
        except Exception as exc:
            logger.warning(f"[temp] Could not remove {name}: {exc}")

    if removed:
        logger.info(f"[temp] {reason}: removed {removed} orphaned update "
                    f"folder(s), freed {freed / (1024 * 1024):.1f} MB")
    return removed, freed


def _make_work_dir(prefix):
    """Scratch directory under data/temp, so orphans are findable later."""
    os.makedirs(TEMP_ROOT, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=TEMP_ROOT)


def _discard(path, job=None):
    """Delete a file the moment it is no longer needed."""
    if not path or not os.path.exists(path):
        return
    try:
        size = os.path.getsize(path)
        os.unlink(path)
        if job:
            job.log(f"Removed the downloaded archive "
                    f"({size / (1024 * 1024):.1f} MB freed).")
    except OSError as exc:
        logger.warning(f"[temp] Could not delete {path}: {exc}")


# ============================================================================
# ENVIRONMENT DETECTION
# ============================================================================

def _run(cmd, cwd=None, timeout=300):
    """Run a command, returning (returncode, combined_output)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd or PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout,
        )
        return proc.returncode, (proc.stdout or '').strip()
    except subprocess.TimeoutExpired:
        return 124, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError:
        return 127, f"Command not found: {cmd[0]}"
    except Exception as exc:
        return 1, str(exc)


def is_git_install() -> bool:
    if not os.path.isdir(os.path.join(PROJECT_ROOT, '.git')):
        return False
    if not shutil.which('git'):
        return False
    code, _ = _run(['git', 'rev-parse', '--is-inside-work-tree'], timeout=15)
    return code == 0


def detect_supervisor() -> str:
    """
    Work out whether something will bring ChitUI back up after it exits.

    Returns "systemd", "run.sh" or "none". A bare `python3 main.py` has no
    supervisor, so the user has to restart by hand.
    """
    if os.environ.get('INVOCATION_ID') or os.environ.get('JOURNAL_STREAM'):
        return 'systemd'
    try:
        with open(f'/proc/{os.getppid()}/cmdline', 'rb') as f:
            parent = f.read().decode('utf-8', 'replace')
        if 'run.sh' in parent:
            return 'run.sh'
    except Exception:
        pass
    return 'none'


def _read_preserve_list():
    """Extra relative paths the user asked us never to overwrite."""
    paths = []
    try:
        with open(PRESERVE_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    paths.append(line.strip('/'))
    except Exception:
        pass
    return paths


# ============================================================================
# UPDATE JOB
# ============================================================================

_jobs = {}
_jobs_lock = threading.Lock()
_update_running = threading.Lock()


class UpdateJob:
    """
    One upgrade attempt, with its log buffered so a reconnecting browser can
    replay everything it missed instead of staring at an empty terminal.
    """

    def __init__(self, release, update_settings):
        self.id = uuid.uuid4().hex
        self.release = release
        self.settings = update_settings
        self.created_at = time.time()
        self.lines = []
        self.finished = False
        self.result = None
        self._cv = threading.Condition()

    # -- writing --------------------------------------------------------
    def log(self, message, level='info'):
        logger.info(f"[update] {message}")
        with self._cv:
            self.lines.append({'type': 'log', 'msg': str(message), 'level': level})
            self._cv.notify_all()

    def finish(self, success, message, **extra):
        payload = {'type': 'done', 'success': bool(success),
                   'message': str(message)}
        payload.update(extra)
        with self._cv:
            self.result = payload
            self.lines.append(payload)
            self.finished = True
            self._cv.notify_all()

    # -- reading --------------------------------------------------------
    def follow(self):
        """Yield every log item from the start, then new ones as they arrive.

        Yields None periodically so the SSE endpoint can emit a keep-alive.
        """
        index = 0
        while True:
            item = None
            with self._cv:
                if index >= len(self.lines):
                    if self.finished:
                        return
                    self._cv.wait(20)
                if index < len(self.lines):
                    item = self.lines[index]
                    index += 1
            if item is None:
                yield None
                continue
            yield item
            if item.get('type') == 'done':
                return


def get_job(job_id):
    with _jobs_lock:
        return _jobs.get(job_id)


def is_update_running() -> bool:
    """True while an upgrade job holds the single-update lock."""
    if _update_running.acquire(blocking=False):
        _update_running.release()
        return False
    return True


def _register_job(job):
    with _jobs_lock:
        _jobs[job.id] = job
        # Drop jobs older than an hour so the dict can't grow forever.
        cutoff = time.time() - 3600
        for stale in [k for k, v in _jobs.items() if v.finished and v.created_at < cutoff]:
            _jobs.pop(stale, None)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def _should_skip(rel_path, extra_protected):
    """True if this relative path must not be backed up or overwritten."""
    parts = rel_path.replace('\\', '/').split('/')
    if parts[0] in PROTECTED_TOP_LEVEL:
        return True
    if '__pycache__' in parts:
        return True
    if parts[-1].endswith('.pyc'):
        return True
    for protected in extra_protected:
        if rel_path == protected or rel_path.startswith(protected + '/'):
            return True
    return False


def _create_backup(job, extra_protected):
    """Tar up the code (not data/) so a botched update can be undone."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    name = f"chitui-{get_current_version()}-{stamp}.tar.gz"
    path = os.path.join(BACKUP_DIR, name)

    job.log(f"Creating a backup of the current code: data/backups/{name}")

    count = 0
    with tarfile.open(path, 'w:gz') as tar:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            rel_root = os.path.relpath(root, PROJECT_ROOT)
            if rel_root == '.':
                rel_root = ''
            dirs[:] = [
                d for d in dirs
                if not _should_skip(os.path.join(rel_root, d).replace('\\', '/'),
                                    extra_protected)
            ]
            for filename in files:
                rel = os.path.join(rel_root, filename).replace('\\', '/')
                if _should_skip(rel, extra_protected):
                    continue
                try:
                    tar.add(os.path.join(root, filename), arcname=rel)
                    count += 1
                except Exception as exc:
                    job.log(f"  skipped {rel}: {exc}", 'warn')

    size_mb = os.path.getsize(path) / (1024 * 1024)
    job.log(f"Backup written: {count} files, {size_mb:.1f} MB")
    _prune_backups(job)
    return path


def _prune_backups(job):
    try:
        entries = sorted(
            (os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
             if f.startswith('chitui-') and f.endswith('.tar.gz')),
            key=os.path.getmtime, reverse=True,
        )
        for old in entries[KEEP_BACKUPS:]:
            os.unlink(old)
            job.log(f"Removed old backup: {os.path.basename(old)}")
    except Exception as exc:
        logger.debug(f"Backup pruning failed: {exc}")


def _restore_backup(job, backup_path):
    job.log("Rolling back to the pre-update backup...", 'warn')
    try:
        with tarfile.open(backup_path, 'r:gz') as tar:
            for member in tar.getmembers():
                if member.name.startswith('/') or '..' in member.name.split('/'):
                    continue
                tar.extract(member, PROJECT_ROOT)
        job.log("Rollback finished - the previous version is back in place.", 'ok')
        return True
    except Exception as exc:
        job.log(f"Rollback FAILED: {exc}", 'error')
        job.log(f"Restore manually with: tar -xzf {backup_path} -C {PROJECT_ROOT}", 'error')
        return False


# ---------------------------------------------------------------------------
# Strategy: git
# ---------------------------------------------------------------------------

def _update_via_git(job, tag):
    job.log("Install is a git working tree - updating with git.")

    code, origin = _run(['git', 'remote', 'get-url', 'origin'], timeout=20)
    if code == 0 and origin:
        job.log(f"Remote: {origin}")

    code, dirty = _run(['git', 'status', '--porcelain'], timeout=60)
    if code == 0 and dirty:
        changed = [l for l in dirty.splitlines() if not l.startswith('??')]
        if changed:
            job.log(f"{len(changed)} tracked file(s) have local modifications "
                    f"and will be reset:", 'warn')
            for line in changed[:15]:
                job.log(f"    {line}", 'warn')
            if len(changed) > 15:
                job.log(f"    ... and {len(changed) - 15} more", 'warn')

    code, shallow = _run(['git', 'rev-parse', '--is-shallow-repository'], timeout=20)
    if code == 0 and shallow.strip() == 'true':
        job.log("Repository is a shallow clone - fetching full history...")
        _run(['git', 'fetch', '--unshallow'], timeout=900)

    job.log("Fetching tags from origin...")
    code, output = _run(['git', 'fetch', '--tags', '--force', '--prune', 'origin'],
                        timeout=900)
    if code != 0:
        job.log(output or "git fetch failed", 'error')
        return False, "git fetch failed - check the network connection."
    if output:
        job.log(output)

    code, _ = _run(['git', 'rev-parse', '--verify', f'{tag}^{{commit}}'], timeout=30)
    if code != 0:
        return False, f"Tag '{tag}' does not exist in the repository after fetching."

    # Tracked files under data/ would be reverted by the reset. Stash copies.
    saved = {}
    for rel in GIT_PRESERVE_PATHS:
        source = os.path.join(PROJECT_ROOT, rel)
        if os.path.isfile(source):
            handle = tempfile.NamedTemporaryFile(delete=False)
            handle.close()
            shutil.copy2(source, handle.name)
            saved[rel] = handle.name

    job.log(f"Checking out {tag}...")
    code, output = _run(['git', 'reset', '--hard', tag], timeout=300)
    if output:
        job.log(output)
    if code != 0:
        for rel, tmp in saved.items():
            os.unlink(tmp)
        return False, "git reset failed - the working tree was left unchanged."

    # Put the user's own files back if the reset touched them.
    for rel, tmp in saved.items():
        target = os.path.join(PROJECT_ROOT, rel)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(tmp, target)
        except Exception as exc:
            job.log(f"Could not restore {rel}: {exc}", 'error')
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if saved:
        job.log(f"Restored {len(saved)} local configuration file(s).")

    code, head = _run(['git', 'log', '-1', '--format=%h  %s'], timeout=20)
    if code == 0 and head:
        job.log(f"Now at: {head}")

    return True, "git update applied."


# ---------------------------------------------------------------------------
# Strategy: tarball
# ---------------------------------------------------------------------------

def _safe_members(tar, job):
    """Yield archive members, refusing absolute paths and directory escapes."""
    for member in tar.getmembers():
        name = member.name.replace('\\', '/')
        if name.startswith('/') or '..' in name.split('/'):
            job.log(f"Refusing unsafe archive path: {name}", 'warn')
            continue
        if member.issym() or member.islnk():
            job.log(f"Skipping link in archive: {name}", 'warn')
            continue
        yield member


def _update_via_tarball(job, release, extra_protected):
    url = release.get('tarball_url')
    job.log("Install is not a git checkout - downloading the release tarball.")
    job.log(f"Source: {url}")

    # Sweep anything a previous crashed attempt left behind before we start
    # downloading - that is precisely when the space is needed.
    cleanup_temp_files("pre-update")

    work_dir = _make_work_dir('chitui-update-')
    archive_path = os.path.join(work_dir, 'release.tar.gz')

    try:
        try:
            with requests.get(url, headers=_api_headers(), stream=True,
                              timeout=HTTP_TIMEOUT, allow_redirects=True) as response:
                if response.status_code != 200:
                    return False, f"Download failed with HTTP {response.status_code}."
                downloaded = 0
                with open(archive_path, 'wb') as out:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            out.write(chunk)
                            downloaded += len(chunk)
        except requests.exceptions.RequestException as exc:
            return False, f"Download failed: {exc}"

        job.log(f"Downloaded {downloaded / (1024 * 1024):.1f} MB")

        extract_dir = os.path.join(work_dir, 'extracted')
        os.makedirs(extract_dir, exist_ok=True)
        job.log("Unpacking archive...")
        try:
            with tarfile.open(archive_path, 'r:gz') as tar:
                for member in _safe_members(tar, job):
                    tar.extract(member, extract_dir)
        except tarfile.TarError as exc:
            return False, f"The downloaded archive could not be read: {exc}"

        # The archive has served its purpose. Dropping it now instead of at
        # the end halves peak disk usage during the copy, which matters on a
        # Pi with a small SD card.
        _discard(archive_path, job)

        entries = [e for e in os.listdir(extract_dir)
                   if os.path.isdir(os.path.join(extract_dir, e))]
        if len(entries) != 1:
            return False, "Unexpected archive layout - expected a single top-level folder."
        source_root = os.path.join(extract_dir, entries[0])

        # A release that doesn't contain main.py is not a ChitUI release.
        if not os.path.isfile(os.path.join(source_root, 'main.py')):
            return False, "The downloaded release does not look like ChitUI (no main.py)."

        job.log("Copying new files into place...")
        copied = 0
        for root, dirs, files in os.walk(source_root):
            rel_root = os.path.relpath(root, source_root)
            rel_root = '' if rel_root == '.' else rel_root
            dirs[:] = [
                d for d in dirs
                if not _should_skip(os.path.join(rel_root, d).replace('\\', '/'),
                                    extra_protected)
            ]
            for filename in files:
                rel = os.path.join(rel_root, filename).replace('\\', '/')
                if _should_skip(rel, extra_protected):
                    continue
                target = os.path.join(PROJECT_ROOT, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(os.path.join(root, filename), target)
                copied += 1

        job.log(f"{copied} file(s) updated. Protected paths (data/, logs, "
                f"user plugins) were left untouched.")
        return True, "Release files installed."

    finally:
        # Always remove the scratch directory, on success or failure.
        shutil.rmtree(work_dir, ignore_errors=True)
        if os.path.exists(work_dir):
            job.log(f"Could not remove the temporary folder {work_dir} - "
                    f"delete it by hand to reclaim the space.", 'warn')


# ---------------------------------------------------------------------------
# Post-update chores
# ---------------------------------------------------------------------------

def _install_requirements(job):
    requirements = os.path.join(PROJECT_ROOT, 'requirements.txt')
    if not os.path.isfile(requirements):
        job.log("No requirements.txt in this release - skipping dependency install.")
        return True

    job.log("Installing Python dependencies from requirements.txt...")
    base = [sys.executable, '-m', 'pip', 'install', '-r', requirements]

    def attempt(extra_args, label):
        code, output = _run(base + extra_args, timeout=1800)
        lines = (output or '').splitlines()
        # pip is chatty about already-satisfied packages; the tail is where
        # anything interesting lives.
        for line in lines[-30:]:
            job.log(f"    {line}")
        if len(lines) > 30:
            job.log(f"    ... ({len(lines) - 30} earlier lines omitted)")
        return code, (output or '')

    code, output = attempt(['--break-system-packages'], 'default')
    if code == 0:
        job.log("Dependencies are up to date.", 'ok')
        return True

    lowered = output.lower()

    # Retrying with different flags only helps for permission problems.
    # A missing package or a build failure will fail identically every time,
    # and the PEP 668 wall of text from a bare retry just buries the real
    # error, so bail out with the actual reason instead.
    if 'permission denied' in lowered or 'errno 13' in lowered:
        job.log("Permission denied - retrying as a user-level install...", 'warn')
        code, output = attempt(['--user', '--break-system-packages'], 'user')
        if code == 0:
            job.log("Dependencies are up to date.", 'ok')
            return True

    if 'no matching distribution' in lowered or 'could not find a version' in lowered:
        job.log("A package in requirements.txt could not be found on PyPI.", 'error')
    elif 'externally-managed-environment' in lowered:
        job.log("This Python is externally managed and pip refused to write to it. "
                "Install the dependencies by hand, or run ChitUI in a virtualenv.", 'error')
    else:
        job.log(f"pip exited with code {code}.", 'error')

    job.log("The new files are installed, but dependencies were not updated. "
            "Fix this with: pip3 install -r requirements.txt --break-system-packages",
            'error')
    return False


def _fix_permissions(job):
    """Shell helpers lose their executable bit when copied out of a tarball."""
    fixed = 0
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in ('.git', 'data', 'node_modules', '__pycache__')]
        for filename in files:
            if filename.endswith('.sh'):
                try:
                    os.chmod(os.path.join(root, filename), 0o755)
                    fixed += 1
                except OSError:
                    pass
    if fixed:
        job.log(f"Restored the executable bit on {fixed} shell script(s).")


def _write_version(job, release):
    """Make sure VERSION reflects what we just installed."""
    version = release.get('version') or release.get('tag', '').lstrip('vV')
    if not version:
        return
    try:
        with open(VERSION_FILE, 'r') as f:
            on_disk = f.read().strip()
    except Exception:
        on_disk = None
    if on_disk == version:
        return
    try:
        with open(VERSION_FILE, 'w') as f:
            f.write(version + '\n')
        job.log(f"VERSION set to {version}")
    except Exception as exc:
        job.log(f"Could not write the VERSION file: {exc}", 'warn')


def _clear_pycache(job):
    removed = 0
    for root, dirs, _files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in ('.git', 'data')]
        for name in list(dirs):
            if name == '__pycache__':
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                dirs.remove(name)
                removed += 1
    if removed:
        job.log(f"Cleared {removed} stale __pycache__ folder(s).")


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _preflight(job):
    if not os.access(PROJECT_ROOT, os.W_OK):
        return f"The install directory is not writable: {PROJECT_ROOT}"
    probe = os.path.join(PROJECT_ROOT, '.update_write_test')
    try:
        with open(probe, 'w') as f:
            f.write('ok')
        os.unlink(probe)
    except Exception as exc:
        return f"Cannot write to the install directory: {exc}"

    try:
        free = shutil.disk_usage(PROJECT_ROOT).free
        job.log(f"Free disk space: {free / (1024 * 1024):.0f} MB")
        if free < MIN_FREE_BYTES:
            return (f"Not enough free disk space "
                    f"({free / (1024 * 1024):.0f} MB available, "
                    f"{MIN_FREE_BYTES / (1024 * 1024):.0f} MB needed).")
    except Exception:
        pass
    return None


def _update_worker(job):
    release = job.release
    tag = release.get('tag')
    version = release.get('version') or tag
    backup_path = None
    strategy = 'git' if is_git_install() else 'tarball'
    extra_protected = _read_preserve_list()

    try:
        job.log(f"ChitUI update: {get_current_version()}  ->  {version}")
        job.log(f"Install directory: {PROJECT_ROOT}")
        if extra_protected:
            job.log(f"Extra preserved paths from data/update_preserve.txt: "
                    f"{', '.join(extra_protected)}")

        problem = _preflight(job)
        if problem:
            job.finish(False, problem)
            return

        supervisor = detect_supervisor()
        if supervisor == 'systemd':
            job.log("Running under systemd - ChitUI will come back automatically.")
        elif supervisor == 'run.sh':
            job.log("Running under run.sh - ChitUI will come back automatically.")
        else:
            job.log("No supervisor detected (started with a plain `python3 main.py`). "
                    "You will have to start ChitUI again yourself after the update.", 'warn')

        job.log("")
        backup_path = _create_backup(job, extra_protected)
        job.log("")

        if strategy == 'git':
            ok, message = _update_via_git(job, tag)
        else:
            ok, message = _update_via_tarball(job, release, extra_protected)

        if not ok:
            job.log(message, 'error')
            if strategy == 'tarball' and backup_path:
                _restore_backup(job, backup_path)
            job.finish(False, message, backup=os.path.basename(backup_path or ''))
            return

        job.log(message, 'ok')
        job.log("")

        _write_version(job, release)
        _fix_permissions(job)
        _clear_pycache(job)
        deps_ok = _install_requirements(job)

        job.log("")
        job.log(f"ChitUI {version} is installed.", 'ok')
        job.log("A restart is required for the new version to take effect.")

        job.finish(
            True,
            f"ChitUI {version} installed successfully.",
            version=version,
            strategy=strategy,
            supervisor=supervisor,
            dependencies_ok=deps_ok,
            can_auto_restart=(supervisor != 'none'),
            backup=os.path.basename(backup_path or ''),
        )

    except Exception as exc:
        logger.exception("Update failed with an unexpected error")
        job.log(f"Unexpected error: {exc}", 'error')
        if strategy == 'tarball' and backup_path:
            _restore_backup(job, backup_path)
        job.finish(False, f"Update failed: {exc}",
                   backup=os.path.basename(backup_path or ''))
    finally:
        try:
            _update_running.release()
        except RuntimeError:
            pass


def start_update(release: dict, update_settings: dict):
    """
    Kick off an update in the background.

    Returns (job, error_message). Exactly one of the two is None.
    """
    if not release or not release.get('tag'):
        return None, "No release information was supplied."

    if not _update_running.acquire(blocking=False):
        return None, "An update is already running."

    job = UpdateJob(release, update_settings)
    _register_job(job)

    thread = threading.Thread(target=_update_worker, args=(job,),
                              name='chitui-updater', daemon=True)
    thread.start()
    return job, None
