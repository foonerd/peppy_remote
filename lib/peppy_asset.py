#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Asset setup.

Format icon fetching, font downloading, handler patching,
and UNC path resolution for remote client compatibility.
"""

import base64
import hashlib
import json
import os
import re
import urllib.request
import urllib.error

from peppy_common import (
    SMB_SHARE_PATH,
    log_client,
)

def _unc_paths_for_windows(server_info):
    """Build UNC paths for meter/spectrum templates on Windows (no SMB mount).
    
    :param server_info: dict with 'ip' and/or 'hostname'
    :return: (templates_path, spectrum_templates_path) or (None, None) if invalid
    """
    if os.name != 'nt':
        return None, None
    host = server_info.get('ip') or server_info.get('hostname')
    if not host:
        return None, None
    # UNC: \\host\share\path (backslashes)
    share_path = SMB_SHARE_PATH.replace('/', '\\')
    unc_base = '\\\\' + host + '\\' + share_path
    templates_path = unc_base + '\\templates'
    spectrum_path = unc_base + '\\templates_spectrum'
    return templates_path, spectrum_path


# =============================================================================
# Format Icons Manager
# =============================================================================
# List of known format icons (installed by install.sh from repo)
KNOWN_FORMAT_ICONS = [
    'aac', 'aiff', 'airplay', 'alac', 'bt', 'cd', 'dab', 'dsd', 'dts',
    'flac', 'fm', 'm4a', 'mp3', 'mp4', 'mqa', 'ogg', 'opus', 'qobuz',
    'radio', 'rr', 'spotify', 'tidal', 'wav', 'wavpack', 'wma', 'youtube'
]

# Python set literal for all icons (used in handler patching)
ALL_ICONS_SET = "{" + ", ".join(f"'{icon}'" for icon in KNOWN_FORMAT_ICONS) + "}"


def setup_format_icons(screensaver_path, server_ip, volumio_port=3000):
    """Ensure format icons are available for the handlers.
    
    Icons are installed by install.sh to screensaver/format-icons/.
    This function:
    1. Checks which icons exist locally
    2. Fetches any missing icons from Volumio server upfront (no lazy loading)
    3. Patches handler files to check local icons for ALL formats
    
    :param screensaver_path: Path to screensaver/ directory (where handlers live)
    :param server_ip: Volumio server IP for fetching missing icons
    :param volumio_port: Volumio HTTP port (default 3000)
    """
    icons_dir = os.path.join(screensaver_path, 'format-icons')
    
    # Create directory if it doesn't exist
    os.makedirs(icons_dir, exist_ok=True)
    
    # Check for missing icons and fetch from Volumio server
    icons_fetched = 0
    for icon_name in KNOWN_FORMAT_ICONS:
        icon_path = os.path.join(icons_dir, f"{icon_name}.svg")
        if not os.path.exists(icon_path):
            if _fetch_format_icon(icon_name, icons_dir, server_ip, volumio_port):
                icons_fetched += 1
    
    if icons_fetched > 0:
        print(f"  Fetched {icons_fetched} missing icons from Volumio server")
    
    # Patch handler files to use expanded local_icons set
    # This ensures ALL formats check the local path first
    handlers_patched = _patch_handlers_for_local_icons(screensaver_path)
    if handlers_patched > 0:
        print(f"  Patched {handlers_patched} handlers for local icon support")


def _fetch_format_icon(fmt, icons_dir, server_ip, volumio_port):
    """Fetch a format icon from Volumio server.
    
    :param fmt: Format name (e.g., 'flac', 'mp3')
    :param icons_dir: Directory to save fetched icons
    :param server_ip: Volumio server IP
    :param volumio_port: Volumio HTTP port
    :return: True if fetched successfully, False otherwise
    """
    icon_filename = f"{fmt}.svg"
    local_path = os.path.join(icons_dir, icon_filename)
    
    # Volumio serves static files from /app/assets-common/
    url = f"http://{server_ip}:{volumio_port}/app/assets-common/format-icons/{icon_filename}"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'PeppyRemote/1.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                content = response.read()
                # Verify it's actually SVG content
                if b'<svg' in content.lower() or b'<?xml' in content:
                    with open(local_path, 'wb') as f:
                        f.write(content)
                    return True
    except Exception:
        pass  # Silently fail - handler will use text fallback
    
    return False


# Fonts required by handlers (font.path + light/regular/bold; digi hardcoded as DSEG7Classic-Italic.ttf)
KNOWN_PEPPY_FONTS = [
    'Lato-Light.ttf', 'Lato-Regular.ttf', 'Lato-Bold.ttf',
    'PeppyFont-Light.ttf', 'PeppyFont-Regular.ttf', 'PeppyFont-Bold.ttf', 'PeppyFont-Italic.ttf',
    'DSEG7Classic-Italic.ttf',
]


def setup_fonts(screensaver_path, server_ip, volumio_port=3000):
    """Ensure required fonts are available locally. Fetch missing from server.
    
    :param screensaver_path: Path to screensaver/ directory
    :param server_ip: Volumio server IP
    :param volumio_port: Volumio HTTP port
    """
    fonts_dir = os.path.join(screensaver_path, 'fonts')
    os.makedirs(fonts_dir, exist_ok=True)
    log_client("--- Font Setup ---", "basic")
    for filename in KNOWN_PEPPY_FONTS:
        local_path = os.path.join(fonts_dir, filename)
        if os.path.exists(local_path):
            log_client(f"  {filename}: present", "basic")
        else:
            if _fetch_font(filename, fonts_dir, server_ip, volumio_port):
                log_client(f"  {filename}: fetched from server", "basic")
            else:
                log_client(f"  {filename}: MISSING", "basic")
                print(f"  Required font missing: {filename}")


def _fetch_font(filename, fonts_dir, server_ip, volumio_port):
    """Fetch a font from server via plugin endpoint (base64). On error, log and return False."""
    local_path = os.path.join(fonts_dir, filename)
    url = f"http://{server_ip}:{volumio_port}/api/v1/pluginEndpoint"
    body = json.dumps({
        'endpoint': 'peppy_screensaver_font',
        'data': {'filename': filename},
    }).encode('utf-8')
    try:
        req = urllib.request.Request(
            url,
            data=body,
            method='POST',
            headers={'Content-Type': 'application/json', 'User-Agent': 'PeppyRemote/1.0'},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8', errors='replace'))
            if not data.get('success'):
                print(f"  Required font missing: {filename}")
                return False
            inner = data.get('data', {})
            if inner.get('success') and inner.get('data'):
                import base64
                with open(local_path, 'wb') as f:
                    f.write(base64.b64decode(inner['data']))
                return True
            print(f"  Required font missing: {filename}")
            return False
    except Exception:
        print(f"  Required font missing: {filename}")
        return False


def _patch_handlers_for_local_icons(screensaver_path):
    """Patch handler files to use expanded local_icons set.
    
    The original handlers have a small local_icons set like:
        local_icons = {'tidal', 'cd', 'qobuz', 'dab', 'fm', 'radio'}
    
    We patch to include ALL formats so they check the local path first:
        local_icons = {'aac', 'aiff', 'airplay', ...}
    
    :param screensaver_path: Path to screensaver/ directory
    :return: Number of files patched
    """
    # Patterns to match and replace
    patterns = [
        "local_icons = {'tidal', 'cd', 'qobuz', 'dab', 'fm', 'radio'}",
        "local_icons = {'tidal', 'cd', 'qobuz'}",
    ]
    
    # Handler files that need patching
    handler_files = [
        'volumio_peppymeter.py',
        'volumio_turntable.py', 
        'volumio_cassette.py',
        'volumio_basic.py',
    ]
    
    patched_count = 0
    
    for handler_file in handler_files:
        filepath = os.path.join(screensaver_path, handler_file)
        if not os.path.exists(filepath):
            continue
            
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            modified = False
            for pattern in patterns:
                if pattern in content:
                    # Check if already patched (contains our full set)
                    if ALL_ICONS_SET not in content:
                        content = content.replace(pattern, f"local_icons = {ALL_ICONS_SET}")
                        modified = True
            
            if modified:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                patched_count += 1
                
        except Exception as e:
            print(f"  Warning: Failed to patch {handler_file}: {e}")
    
    return patched_count


# =============================================================================
# Handler sync (keep handlers byte-identical to the connected server)
# =============================================================================
# The plugin exposes a server-authoritative manifest of the exact handler (.py) and
# font files it runs. On connect we diff that manifest by sha256 and pull only the
# files that differ, so the remote always runs the same handler code as its server -
# no dependency on which GitHub branch the client was installed from, and adding a
# handler module to the plugin never again needs an installer file-list edit.
# Safe by construction: integrity-checked (sha256), atomic per-file writes, and a
# silent no-op against older servers that lack the endpoint (keeps bundled files).

def _peppy_safe_name(name):
    """Reject anything that could escape the target directory."""
    return bool(name) and '/' not in name and '\\' not in name and '..' not in name


def _peppy_post_json(url, payload, timeout=10):
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={'Content-Type': 'application/json', 'User-Agent': 'PeppyRemote/1.0'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8', errors='replace'))


def _peppy_sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _sync_one_file(base_url, kind, item, dest_dir):
    """Download one manifest entry into dest_dir if its sha256 differs from local.

    Returns one of: "current" (local already matches), "updated" (file written),
    or "failed" (any fetch/decode/hash/write problem; existing file is kept).
    """
    name = item.get('name')
    want = (item.get('sha256') or '').lower()
    if not _peppy_safe_name(name):
        return "failed"
    dest = os.path.join(dest_dir, name)

    # Already current?
    if want and os.path.exists(dest):
        try:
            if _peppy_sha256_file(dest).lower() == want:
                return "current"
        except Exception:
            pass

    try:
        resp = _peppy_post_json(
            base_url,
            {'endpoint': 'peppy_screensaver_file', 'data': {'kind': kind, 'name': name}},
            timeout=15,
        )
    except Exception as e:
        log_client(f"Handler sync: fetch {name} failed ({e})", "verbose")
        return "failed"

    inner = (resp or {}).get('data') or {}
    if not inner.get('success') or not inner.get('data'):
        log_client(f"Handler sync: server did not return {name}", "verbose")
        return "failed"

    try:
        raw = base64.b64decode(inner['data'])
    except Exception:
        log_client(f"Handler sync: bad base64 for {name}", "verbose")
        return "failed"

    got = hashlib.sha256(raw).hexdigest().lower()
    if want and got != want:
        log_client(f"Handler sync: sha256 mismatch for {name}; keeping existing file", "basic")
        return "failed"

    tmp = dest + '.peppytmp'
    try:
        os.makedirs(dest_dir, exist_ok=True)
        with open(tmp, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
        log_client(f"Handler sync: updated {name}", "verbose")
        return "updated"
    except Exception as e:
        log_client(f"Handler sync: write {name} failed ({e})", "verbose")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return "failed"


def _verify_manifest_files(items, dest_dir):
    """Return the names of manifest entries whose local file is missing or whose
    sha256 does not match the manifest. Read-only (hashes local files only)."""
    stale = []
    for item in items:
        name = item.get('name')
        want = (item.get('sha256') or '').lower()
        if not _peppy_safe_name(name) or not want:
            continue
        dest = os.path.join(dest_dir, name)
        if not os.path.exists(dest):
            stale.append(name)
            continue
        try:
            if _peppy_sha256_file(dest).lower() != want:
                stale.append(name)
        except Exception:
            stale.append(name)
    return stale


def sync_handlers_from_server(screensaver_path, server_ip, volumio_port=3000):
    """Pull the handler (.py) + font set the connected server actually runs.

    Must run BEFORE the handlers are imported (and before setup_format_icons, so the
    local-icon patch is applied to the freshly synced files). No-ops safely if the
    server is older and has no manifest endpoint.

    Returns a result dict: {ok, stale, updated, plugin_version, reason}. `ok` is True
    when every manifest file is verified present with a matching hash locally (or when
    the server has no manifest endpoint, which is a legitimate older-server no-op).
    """
    base_url = f"http://{server_ip}:{volumio_port}/api/v1/pluginEndpoint"
    try:
        resp = _peppy_post_json(base_url, {'endpoint': 'peppy_screensaver_manifest'}, timeout=10)
    except Exception as e:
        log_client(f"Handler sync: manifest unavailable ({e}); using bundled handlers", "verbose")
        return {"ok": True, "stale": [], "updated": 0, "plugin_version": None, "reason": "no-manifest"}

    data = (resp or {}).get('data') or {}
    if not data.get('success'):
        log_client("Handler sync: server has no manifest endpoint; using bundled handlers", "basic")
        return {"ok": True, "stale": [], "updated": 0, "plugin_version": None, "reason": "no-manifest"}

    handlers = data.get('handlers') or []
    fonts = data.get('fonts') or []
    plugin_version = data.get('plugin_version')
    log_client(
        f"Handler sync: server plugin {plugin_version!r}, "
        f"{len(handlers)} handler(s), {len(fonts)} font(s)", "basic"
    )

    updated = 0
    failed = []
    fonts_dir = os.path.join(screensaver_path, 'fonts')
    for item in handlers:
        status = _sync_one_file(base_url, 'handler', item, screensaver_path)
        if status == "updated":
            updated += 1
        elif status == "failed":
            failed.append(item.get('name'))
    for item in fonts:
        status = _sync_one_file(base_url, 'font', item, fonts_dir)
        if status == "updated":
            updated += 1
        elif status == "failed":
            failed.append(item.get('name'))

    # Read-only verification: local set must now equal the manifest, hash for hash.
    stale = _verify_manifest_files(handlers, screensaver_path)
    stale += _verify_manifest_files(fonts, fonts_dir)

    if stale:
        log_client(
            "Handler sync: FAILED to sync " + str(len(stale)) + " file(s): "
            + ", ".join(n for n in stale if n)
            + " - remote may render stale features", "basic"
        )
    elif updated:
        log_client(f"Handler sync: updated {updated} file(s) from server", "basic")
    else:
        log_client("Handler sync: all files already current", "basic")

    return {
        "ok": not stale,
        "stale": stale,
        "updated": updated,
        "plugin_version": plugin_version,
        "reason": "synced",
    }
