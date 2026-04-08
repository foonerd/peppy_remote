#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Common infrastructure.

Shared constants, configuration, logging, and utility functions
used by all peppy_remote modules.

Config model v2 (multi-profile):
  config.json holds a store with config_version, active_profile, and a
  profiles dict.  Each profile is a complete config (server, display,
  templates, spectrum, debug).  load_config() returns the active profile
  as a flat dict - all existing callers see no change.  Profile
  management functions (load_config_store, create_profile, etc.) are
  used by the wizard and main() only.

  v1 configs (flat, with wizard_completed) are auto-migrated on first
  load.
"""

__version__ = "3.4.0"  # Footlocked to PeppyMeter Screensaver release

import json
import logging
import os
import platform
import re
import sys
import tempfile
import time
from datetime import datetime

# =============================================================================
# Configuration
# =============================================================================
DISCOVERY_PORT = 5579
DISCOVERY_TIMEOUT = 10  # seconds to wait for discovery
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SMB_MOUNT_BASE = os.path.join(SCRIPT_DIR, "mnt")  # Local mount point (portable)
SMB_SHARE_PATH = "Internal Storage/peppy_screensaver"
LOG_FILE = os.path.join(SCRIPT_DIR, "peppy_remote.log")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
PERSIST_FILE = os.path.join(tempfile.gettempdir(), 'peppy_persist')

# Current config store version
CONFIG_VERSION = 2
# Export file marker and version
EXPORT_MARKER = "peppy_remote_profile"
EXPORT_VERSION = 1


def _parse_semver_tuple(s):
    """Parse major.minor.patch style string to a 3-tuple. Returns None if invalid."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    parts = s.split('.')
    if len(parts) < 2:
        return None
    nums = []
    for p in parts[:3]:
        n = ''
        for c in p:
            if c.isdigit():
                n += c
            else:
                break
        if not n:
            return None
        nums.append(int(n))
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def _compare_remote_release_versions(client_ver, server_ver):
    """Returns -1 if client < server, 0 if equal, +1 if client > server, None if unparsable."""
    tc = _parse_semver_tuple(client_ver)
    ts = _parse_semver_tuple(server_ver)
    if tc is None or ts is None:
        return None
    if tc < ts:
        return -1
    if tc > ts:
        return 1
    return 0


def _resolve_pygame_ui_font(pg, size):
    """Return a pygame Font for UI text on Linux, Windows, and macOS.

    Tries common system families (including Linux ``sans``), validates with a test
    render to avoid SDL ``NULL pointer`` failures, then ``SysFont(None)``, then
    ``Font(None)`` as last resort.
    """
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 20
    if size < 1:
        size = 1

    names = (
        'sans',
        'DejaVu Sans',
        'Liberation Sans',
        'Segoe UI',
        'Arial',
        'Calibri',
        'Microsoft Sans Serif',
        'Helvetica Neue',
    )

    def _font_renders(f):
        if f is None:
            return False
        try:
            f.render(' ', True, (255, 255, 255))
            return True
        except Exception:
            return False

    for name in names:
        try:
            f = pg.font.SysFont(name, size)
        except Exception:
            continue
        if _font_renders(f):
            return f

    try:
        f = pg.font.SysFont(None, size)
        if _font_renders(f):
            return f
    except Exception:
        pass

    return pg.font.Font(None, size)


def _norm_str(s):
    """Normalize config/meter strings for comparisons (UDP vs HTTP vs on-disk)."""
    return (s or '').strip()


def _is_kiosk_random_mode(client_override):
    """Return True if client_override specifies random or comma-separated list mode.

    In kiosk-random, the configured meter value is 'random' or a comma-separated
    list of section names. Peppymeter resolves this to an actual section name at
    runtime, so reload comparisons must only consider the folder - the meter name
    will always differ from the literal 'random'/list string.
    """
    if not client_override:
        return False
    _, meter = client_override
    m = (meter or '').strip().lower()
    return m == 'random' or ',' in m


# Seconds to retry HTTP to Volumio before treating server as offline (version check phase only)
SERVER_WAIT_TIMEOUT_SEC = 120.0
SERVER_RETRY_INTERVAL_SEC = 2.0


# =============================================================================
# Client Debug System
# =============================================================================
# Debug levels: 'off', 'basic', 'verbose', 'trace'
# - off: No debug output
# - basic: Key events (discovery, connections, config changes)
# - verbose: Detailed operations (mount, network, config parsing)
# - trace: Per-frame/per-packet data (spectrum bins, level values)
CLIENT_DEBUG_LEVEL = 'off'
CLIENT_DEBUG_TRACE = {
    'spectrum': False,   # Per-packet spectrum logging
    'network': False,    # Connection/discovery details
    'config': False,     # Config parsing details
    'wizard': False      # Wizard operations (profile management, import/export)
}


def setup_logging(to_file=False):
    """Setup logging - to file when running without terminal, stdout otherwise."""
    if to_file:
        # Redirect stdout/stderr to log file
        log_file = open(LOG_FILE, 'a')
        log_file.write(f"\n{'='*60}\n")
        log_file.write(f"PeppyMeter Remote Client started at {datetime.now()}\n")
        log_file.write(f"{'='*60}\n")
        sys.stdout = log_file
        sys.stderr = log_file
    # Also setup logging module for any libraries that use it
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )


def init_client_debug(config):
    """Initialize client debug settings from config."""
    global CLIENT_DEBUG_LEVEL, CLIENT_DEBUG_TRACE

    debug_config = config.get('debug', {})
    CLIENT_DEBUG_LEVEL = debug_config.get('level', 'off')
    CLIENT_DEBUG_TRACE['spectrum'] = debug_config.get('trace_spectrum', False)
    CLIENT_DEBUG_TRACE['network'] = debug_config.get('trace_network', False)
    CLIENT_DEBUG_TRACE['config'] = debug_config.get('trace_config', False)
    CLIENT_DEBUG_TRACE['wizard'] = debug_config.get('trace_wizard', False)


def log_client(message, level='basic', trace_component=None):
    """Log a debug message if the current debug level allows it.

    Args:
        message: The message to log
        level: Required level - 'basic', 'verbose', or 'trace'
        trace_component: For trace level, which component flag to check
                        ('spectrum', 'network', 'config', 'wizard')
    """
    level_order = {'off': 0, 'basic': 1, 'verbose': 2, 'trace': 3}
    current_level = level_order.get(CLIENT_DEBUG_LEVEL, 0)
    required_level = level_order.get(level, 1)

    # Check if level is sufficient
    if current_level < required_level:
        return

    # For trace level, also check component-specific flag
    if level == 'trace' and trace_component:
        if not CLIENT_DEBUG_TRACE.get(trace_component, False):
            return

    # Format and print
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    print(f"[{timestamp}] [CLIENT] {message}")


# =============================================================================
# Configuration - Profile Model
# =============================================================================

# Default profile - used as template for new profiles and for reset-to-defaults.
# This is the same shape as v1 DEFAULT_CONFIG minus wizard_completed.
# Callers that reference DEFAULT_CONFIG continue to work.
DEFAULT_PROFILE = {
    "name": "",
    "server": {
        "host": None,           # None = auto-discover
        "level_port": 5580,
        "spectrum_port": 5581,
        "volumio_port": 3000,
        "discovery_port": 5579,
        "discovery_timeout": 10
    },
    "display": {
        "windowed": True,       # True = movable window with title bar
        "position": None,       # None = centered, or [x, y]
        "fullscreen": False,
        "monitor": 0,
        "meter_folder": None,   # Kiosk: fixed template folder or None = use server
        "meter": None,          # Kiosk: "section", "random", or "sect1,sect2,..."; None = use server
    },
    "templates": {
        "use_smb": True,
        "local_path": None,             # Override path for meter templates
        "spectrum_local_path": None     # Override path for spectrum templates
    },
    "spectrum": {
        "decay_rate": 0.95      # Per-frame decay (0.85=fast, 0.98=slow)
    },
    "debug": {
        "level": "off",         # off/basic/verbose/trace
        "trace_spectrum": False,
        "trace_network": False,
        "trace_config": False,
        "trace_wizard": False
    }
}

# Backward compatibility alias - existing code references DEFAULT_CONFIG
# for deep-copy fallback and reset-to-defaults.  Shape matches v1 (flat
# profile dict with wizard_completed).
DEFAULT_CONFIG = dict(DEFAULT_PROFILE, wizard_completed=False)


def slugify_profile_id(hostname):
    """Create a profile ID slug from a hostname or label.

    Lowercase, strip non-alphanumeric except hyphens, collapse runs.
    Falls back to 'default' for empty/None input.

    :param hostname: Server hostname, IP, or user label
    :returns: Profile ID string suitable as a dict key
    """
    if not hostname:
        return 'default'
    s = str(hostname).strip().lower()
    s = re.sub(r'[^a-z0-9-]', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s or 'default'


def _deep_merge(base, overlay):
    """Deep merge overlay dict into base dict (modifies base in place).

    Only merges dict values recursively; scalars and non-dict values in
    overlay replace base values.  Keys in base not present in overlay
    are preserved (provides defaults).
    """
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _new_profile():
    """Return a deep copy of DEFAULT_PROFILE."""
    return json.loads(json.dumps(DEFAULT_PROFILE))


def _migrate_v1_to_v2(raw):
    """Migrate a v1 flat config to v2 multi-profile store.

    :param raw: Parsed JSON dict from config.json (v1 format)
    :returns: v2 store dict
    """
    log_client("Migrating v1 config to v2 multi-profile format", "verbose", "wizard")

    # Build a profile from the v1 flat config
    profile = _new_profile()
    for section in ('server', 'display', 'templates', 'spectrum', 'debug'):
        if section in raw and isinstance(raw[section], dict):
            _deep_merge(profile[section], raw[section])
        elif section in raw and raw[section] is not None:
            profile[section] = raw[section]

    # Derive profile ID and name from server host
    host = None
    if isinstance(raw.get('server'), dict):
        host = raw['server'].get('host')
    elif isinstance(raw.get('server'), str):
        host = raw['server']  # Very old v1 format: server was a plain string

    profile_id = slugify_profile_id(host)
    profile['name'] = host or 'Default'

    log_client(f"  v1 host={host!r} -> profile_id={profile_id!r}, name={profile['name']!r}", "trace", "wizard")

    store = {
        'config_version': CONFIG_VERSION,
        'active_profile': profile_id,
        'profiles': {
            profile_id: profile
        }
    }
    return store


def load_config_store():
    """Load the full v2 config store from disk.

    Handles:
    - Missing file: returns empty store (no profiles)
    - v1 format: auto-migrates to v2 and saves
    - v2 format: returns as-is with defaults merged

    :returns: v2 store dict with keys: config_version, active_profile, profiles
    """
    empty_store = {
        'config_version': CONFIG_VERSION,
        'active_profile': None,
        'profiles': {}
    }

    if not os.path.exists(CONFIG_FILE):
        log_client("No config file found, returning empty store", "verbose", "wizard")
        return empty_store

    try:
        with open(CONFIG_FILE, 'r') as f:
            raw = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log_client(f"Failed to read config file: {e}", "basic", "wizard")
        return empty_store

    # Detect format version
    if isinstance(raw, dict) and raw.get('config_version') == CONFIG_VERSION:
        # Already v2
        store = empty_store.copy()
        store['active_profile'] = raw.get('active_profile')
        store['profiles'] = raw.get('profiles', {})

        # Merge defaults into each profile (adds new keys from DEFAULT_PROFILE)
        for pid, profile in store['profiles'].items():
            merged = _new_profile()
            _deep_merge(merged, profile)
            store['profiles'][pid] = merged

        # Validate active_profile points to an existing profile
        if store['active_profile'] not in store['profiles'] and store['profiles']:
            store['active_profile'] = next(iter(store['profiles']))
            log_client(f"Active profile reset to {store['active_profile']!r}", "verbose", "wizard")

        return store

    # v1 format detected - migrate
    if isinstance(raw, dict) and ('server' in raw or 'wizard_completed' in raw):
        store = _migrate_v1_to_v2(raw)
        # Save migrated store immediately
        save_config_store(store)
        log_client("v1 config migrated and saved as v2", "basic", "wizard")
        return store

    # Unrecognized format
    log_client(f"Unrecognized config format, returning empty store", "basic", "wizard")
    return empty_store


def save_config_store(store):
    """Save the full v2 config store to disk.

    :param store: v2 store dict
    :returns: True on success, False on error
    """
    store['config_version'] = CONFIG_VERSION
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(store, f, indent=2)
        log_client("Config store saved", "trace", "wizard")
        return True
    except IOError as e:
        print(f"Error saving config: {e}")
        return False


def load_config():
    """Load the active profile config as a flat dict.

    Returns the same shape as DEFAULT_CONFIG (with wizard_completed
    injected) so all existing callers see no change.

    :returns: Flat config dict for the active profile
    """
    store = load_config_store()
    active_id = store.get('active_profile')
    profiles = store.get('profiles', {})

    if active_id and active_id in profiles:
        config = json.loads(json.dumps(profiles[active_id]))
    else:
        # No active profile - return defaults
        config = _new_profile()

    # Inject wizard_completed for backward compat
    # If profiles exist, wizard has been completed at some point
    config['wizard_completed'] = bool(profiles)
    return config


def save_config(config):
    """Save a flat profile dict into the active profile slot.

    Strips wizard_completed before storing (it is derived, not stored
    per-profile in v2).

    :param config: Flat config dict (same shape as load_config returns)
    :returns: True on success, False on error
    """
    store = load_config_store()
    active_id = store.get('active_profile')

    if not active_id:
        # No active profile yet - create one from config
        host = None
        if isinstance(config.get('server'), dict):
            host = config['server'].get('host')
        active_id = slugify_profile_id(host)
        store['active_profile'] = active_id
        log_client(f"Created initial profile {active_id!r} from save_config", "verbose", "wizard")

    # Strip wizard_completed before storing in profile
    profile = json.loads(json.dumps(config))
    profile.pop('wizard_completed', None)

    # Ensure profile has a name
    if not profile.get('name'):
        host = None
        if isinstance(profile.get('server'), dict):
            host = profile['server'].get('host')
        profile['name'] = host or active_id or 'Default'

    store['profiles'][active_id] = profile
    return save_config_store(store)


def is_first_run():
    """Check if this is first run (no config, no profiles, or v1 with wizard not completed)."""
    if not os.path.exists(CONFIG_FILE):
        return True

    try:
        with open(CONFIG_FILE, 'r') as f:
            raw = json.load(f)
    except (json.JSONDecodeError, IOError):
        return True

    # v2 format: first run if no profiles exist
    if isinstance(raw, dict) and raw.get('config_version') == CONFIG_VERSION:
        profiles = raw.get('profiles', {})
        return len(profiles) == 0

    # v1 format: check wizard_completed flag (legacy)
    if isinstance(raw, dict):
        # Old format: "server" is string/null, not a dict
        if "server" in raw and not isinstance(raw["server"], dict):
            return True
        if "wizard_completed" in raw and raw["wizard_completed"] is False:
            return True
        return False

    return True


# =============================================================================
# Profile Management
# =============================================================================

def get_profile_list(store=None):
    """Return list of (profile_id, name, host) tuples, sorted by name.

    :param store: v2 store dict (loaded if None)
    :returns: List of (id, name, host) tuples
    """
    if store is None:
        store = load_config_store()
    result = []
    for pid, profile in store.get('profiles', {}).items():
        name = profile.get('name', pid)
        host = None
        if isinstance(profile.get('server'), dict):
            host = profile['server'].get('host')
        result.append((pid, name, host))
    result.sort(key=lambda x: (x[1] or '').lower())
    return result


def get_active_profile_id(store=None):
    """Return the active profile ID, or None if no profiles.

    :param store: v2 store dict (loaded if None)
    """
    if store is None:
        store = load_config_store()
    return store.get('active_profile')


def set_active_profile(store, profile_id):
    """Set the active profile and save.

    :param store: v2 store dict
    :param profile_id: Profile ID to activate
    :returns: True if profile exists and was activated, False otherwise
    """
    if profile_id not in store.get('profiles', {}):
        log_client(f"Cannot activate profile {profile_id!r}: not found", "basic", "wizard")
        return False
    store['active_profile'] = profile_id
    log_client(f"Active profile set to {profile_id!r}", "verbose", "wizard")
    return save_config_store(store)


def create_profile(store, profile_config, hostname=None, name=None):
    """Create a new profile in the store.

    :param store: v2 store dict (modified in place)
    :param profile_config: Flat profile dict (server, display, etc.)
    :param hostname: Server hostname for ID generation (uses config if None)
    :param name: Friendly name (uses hostname if None)
    :returns: Profile ID of the created profile
    """
    if hostname is None and isinstance(profile_config.get('server'), dict):
        hostname = profile_config['server'].get('host')
    profile_id = slugify_profile_id(hostname)

    # Avoid collisions
    base_id = profile_id
    counter = 2
    while profile_id in store.get('profiles', {}):
        profile_id = f"{base_id}-{counter}"
        counter += 1

    profile = _new_profile()
    _deep_merge(profile, profile_config)
    profile.pop('wizard_completed', None)
    profile['name'] = name or hostname or profile_id

    if 'profiles' not in store:
        store['profiles'] = {}
    store['profiles'][profile_id] = profile

    # If this is the first profile, make it active
    if not store.get('active_profile') or store['active_profile'] not in store['profiles']:
        store['active_profile'] = profile_id

    log_client(f"Created profile {profile_id!r} (name={profile['name']!r})", "verbose", "wizard")
    return profile_id


def delete_profile(store, profile_id):
    """Delete a profile from the store.

    Cannot delete the last remaining profile.

    :param store: v2 store dict (modified in place)
    :param profile_id: Profile ID to delete
    :returns: True if deleted, False if not found or last profile
    """
    profiles = store.get('profiles', {})
    if profile_id not in profiles:
        log_client(f"Cannot delete profile {profile_id!r}: not found", "basic", "wizard")
        return False
    if len(profiles) <= 1:
        log_client(f"Cannot delete profile {profile_id!r}: last remaining profile", "basic", "wizard")
        return False

    del profiles[profile_id]
    log_client(f"Deleted profile {profile_id!r}", "verbose", "wizard")

    # If we deleted the active profile, switch to the first remaining
    if store.get('active_profile') == profile_id:
        store['active_profile'] = next(iter(profiles))
        log_client(f"Active profile switched to {store['active_profile']!r}", "verbose", "wizard")

    return True


def rename_profile(store, profile_id, new_name):
    """Rename a profile's friendly name.

    :param store: v2 store dict (modified in place)
    :param profile_id: Profile ID to rename
    :param new_name: New friendly name
    :returns: True if renamed, False if not found
    """
    profiles = store.get('profiles', {})
    if profile_id not in profiles:
        return False
    profiles[profile_id]['name'] = new_name
    log_client(f"Renamed profile {profile_id!r} to {new_name!r}", "verbose", "wizard")
    return True


def get_profile(store, profile_id):
    """Return a deep copy of a profile's config dict.

    :param store: v2 store dict
    :param profile_id: Profile ID
    :returns: Profile config dict, or None if not found
    """
    profiles = store.get('profiles', {})
    if profile_id not in profiles:
        return None
    return json.loads(json.dumps(profiles[profile_id]))


def update_profile(store, profile_id, profile_config):
    """Update an existing profile's config.

    :param store: v2 store dict (modified in place)
    :param profile_id: Profile ID to update
    :param profile_config: New flat profile dict
    :returns: True if updated, False if not found
    """
    profiles = store.get('profiles', {})
    if profile_id not in profiles:
        return False
    profile = json.loads(json.dumps(profile_config))
    profile.pop('wizard_completed', None)
    # Preserve name if not in new config
    if not profile.get('name') and profiles[profile_id].get('name'):
        profile['name'] = profiles[profile_id]['name']
    profiles[profile_id] = profile
    log_client(f"Updated profile {profile_id!r}", "verbose", "wizard")
    return True


# =============================================================================
# Profile Export / Import
# =============================================================================

def export_profile(store, profile_id, filepath):
    """Export a single profile to a JSON file.

    :param store: v2 store dict
    :param profile_id: Profile ID to export
    :param filepath: Destination file path
    :returns: True on success, False on error
    """
    profiles = store.get('profiles', {})
    if profile_id not in profiles:
        log_client(f"Cannot export profile {profile_id!r}: not found", "basic", "wizard")
        return False

    export_data = {
        EXPORT_MARKER: True,
        'export_version': EXPORT_VERSION,
        'exported_from': platform.system().lower(),
        'client_version': __version__,
        'profile_id': profile_id,
        'profile': json.loads(json.dumps(profiles[profile_id]))
    }

    try:
        with open(filepath, 'w') as f:
            json.dump(export_data, f, indent=2)
        log_client(f"Exported profile {profile_id!r} to {filepath}", "verbose", "wizard")
        return True
    except IOError as e:
        log_client(f"Export failed: {e}", "basic", "wizard")
        return False


def import_profile(filepath):
    """Import a profile from a JSON file.

    Validates structure but does NOT check template paths (use
    validate_profile_paths() after import to check/warn).

    :param filepath: Source file path
    :returns: (profile_id, profile_config) tuple, or (None, None) on error.
              Includes a list of warnings as third element: (id, config, warnings)
    """
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log_client(f"Import failed: {e}", "basic", "wizard")
        return None, None, [f"Cannot read file: {e}"]

    if not isinstance(data, dict) or not data.get(EXPORT_MARKER):
        return None, None, ["Not a valid peppy_remote profile export file"]

    profile = data.get('profile')
    if not isinstance(profile, dict):
        return None, None, ["Export file has no profile data"]

    profile_id = data.get('profile_id', 'imported')
    exported_from = data.get('exported_from', 'unknown')

    warnings = []

    # Merge with defaults to fill any missing keys
    merged = _new_profile()
    _deep_merge(merged, profile)

    # Check for cross-platform path issues
    path_warnings = validate_profile_paths(merged)
    if path_warnings:
        warnings.extend(path_warnings)

    if exported_from != platform.system().lower():
        warnings.append(f"Profile was exported from {exported_from}; template paths may need adjustment")

    log_client(f"Imported profile {profile_id!r} from {filepath} ({len(warnings)} warnings)", "verbose", "wizard")
    return profile_id, merged, warnings


def validate_profile_paths(config):
    """Check that template paths in a profile config exist on disk.

    :param config: Profile config dict
    :returns: List of warning strings (empty if all paths valid or null)
    """
    warnings = []
    templates = config.get('templates', {})

    local_path = templates.get('local_path')
    if local_path and not os.path.isdir(local_path):
        warnings.append(f"Meter templates path not found: {local_path}")

    spectrum_path = templates.get('spectrum_local_path')
    if spectrum_path and not os.path.isdir(spectrum_path):
        warnings.append(f"Spectrum templates path not found: {spectrum_path}")

    return warnings


# =============================================================================
# Template / Meter Utilities
# =============================================================================

def get_template_folders(templates_path):
    """Return list of folder names under templates_path that contain meters.txt."""
    if not templates_path or not os.path.isdir(templates_path):
        return []
    out = []
    try:
        for name in sorted(os.listdir(templates_path)):
            path = os.path.join(templates_path, name)
            if os.path.isdir(path) and os.path.exists(os.path.join(path, 'meters.txt')):
                out.append(name)
    except (OSError, IOError):
        pass
    return out


def get_meter_sections(templates_path, meter_folder):
    """Return list of section names from meter_folder/meters.txt."""
    if not templates_path or not meter_folder:
        return []
    meters_file = os.path.join(templates_path, meter_folder, 'meters.txt')
    if not os.path.exists(meters_file):
        return []
    try:
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(meters_file)
        return cfg.sections()
    except Exception:
        return []


def _is_ip_address(host):
    """Return True if host is an IPv4 or IPv6 address, False for a hostname."""
    try:
        import ipaddress
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
