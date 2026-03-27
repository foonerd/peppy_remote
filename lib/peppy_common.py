#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Common infrastructure.

Shared constants, configuration, logging, and utility functions
used by all peppy_remote modules.
"""

__version__ = "3.3.2"  # Footlocked to PeppyMeter Screensaver release

import json
import logging
import os
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
    'config': False      # Config parsing details
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


def log_client(message, level='basic', trace_component=None):
    """Log a debug message if the current debug level allows it.
    
    Args:
        message: The message to log
        level: Required level - 'basic', 'verbose', or 'trace'
        trace_component: For trace level, which component flag to check
                        ('spectrum', 'network', 'config')
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

CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

# Default configuration
DEFAULT_CONFIG = {
    "wizard_completed": False,  # True after user saves from config wizard (stops first-run prompt)
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
        "meter_folder": None,   # Kiosk: fixed template folder (e.g. "1920x720_5skins") or None = use server
        "meter": None,          # Kiosk: "section", "random", or "sect1,sect2,sect3"; None = use server
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
        "trace_config": False
    }
}


def load_config():
    """Load configuration from file, return merged with defaults."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # Deep copy
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                user_config = json.load(f)
            # Deep merge user config into defaults
            for section in config:
                if section in user_config:
                    if isinstance(config[section], dict) and isinstance(user_config[section], dict):
                        # Only update if user_config section is also a dict
                        config[section].update(user_config[section])
                    elif user_config[section] is not None or section == "wizard_completed":
                        # Replace (handles old format, scalar values, and wizard_completed bool)
                        config[section] = user_config[section]
                    # If user_config[section] is None (and not wizard_completed), keep the default
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load config file: {e}")
    
    return config


def is_first_run():
    """Check if this is first run (no config, invalid/old format, or wizard not yet completed)."""
    if not os.path.exists(CONFIG_FILE):
        return True
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            user_config = json.load(f)
        
        # Old format: "server" is string/null, not a dict
        if "server" in user_config and not isinstance(user_config["server"], dict):
            return True
        
        # Explicit flag: wizard_completed False means user has not completed wizard yet
        # Missing key = existing config (backward compat), treat as not first run
        if "wizard_completed" in user_config and user_config["wizard_completed"] is False:
            return True
        
        return False
    except (json.JSONDecodeError, IOError):
        return True


def save_config(config):
    """Save configuration to file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except IOError as e:
        print(f"Error saving config: {e}")
        return False


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



PERSIST_FILE = os.path.join(tempfile.gettempdir(), 'peppy_persist')
