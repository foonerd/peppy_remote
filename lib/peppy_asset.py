#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Asset setup.

Format icon fetching, font downloading, handler patching,
and UNC path resolution for remote client compatibility.
"""

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
    'PeppyFont-Light.ttf', 'PeppyFont-Regular.ttf', 'PeppyFont-Bold.ttf',
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
