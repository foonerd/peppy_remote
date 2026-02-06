#!/usr/bin/env python3
"""
PeppyMeter Remote Client

Connects to a PeppyMeter server running on Volumio and displays
the meter visualization on a remote display.

This client uses the same rendering code as the Volumio plugin
(volumio_peppymeter.py, volumio_turntable.py, etc.) but receives
audio level data over the network instead of from local ALSA/pipe.

Features:
- Auto-discovery of PeppyMeter servers via UDP broadcast
- Receives audio level data over UDP
- Receives metadata via Volumio's socket.io
- Mounts templates via SMB from the server
- Fetches config.txt via HTTP from server
- Renders using full Volumio PeppyMeter code (turntable, cassette, meters)

Installation:
    curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.sh | bash

Usage:
    peppy_remote                    # Auto-discover server
    peppy_remote --server volumio   # Connect to specific server
    peppy_remote --test             # Simple test display
"""

import argparse
import json
import logging
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# =============================================================================
# Configuration
# =============================================================================
DISCOVERY_PORT = 5579
DISCOVERY_TIMEOUT = 10  # seconds to wait for discovery
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SMB_MOUNT_BASE = os.path.join(SCRIPT_DIR, "mnt")  # Local mount point (portable)
SMB_SHARE_PATH = "Internal Storage/peppy_screensaver"
LOG_FILE = os.path.join(SCRIPT_DIR, "peppy_remote.log")


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
        "monitor": 0
    },
    "templates": {
        "use_smb": True,
        "local_path": None      # Override path for templates
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


def run_config_wizard():
    """Interactive configuration wizard."""
    config = load_config()
    
    def clear_screen():
        os.system('clear' if os.name != 'nt' else 'cls')
    
    def show_menu():
        clear_screen()
        print("=" * 50)
        print(" PeppyMeter Remote Client Configuration")
        print("=" * 50)
        print()
        
        # Server settings
        host = config["server"]["host"] or "auto-discover"
        print("Server Settings:")
        print(f"  1. Server host:       {host}")
        print(f"  2. Level port:        {config['server']['level_port']}")
        print(f"  3. Spectrum port:     {config['server']['spectrum_port']}")
        print(f"  4. Volumio port:      {config['server']['volumio_port']}")
        print(f"  5. Discovery timeout: {config['server']['discovery_timeout']}s")
        print()
        
        # Display settings
        mode = "fullscreen" if config["display"]["fullscreen"] else ("windowed" if config["display"]["windowed"] else "frameless")
        pos = config["display"]["position"]
        pos_str = f"{pos[0]}, {pos[1]}" if pos else "centered"
        print("Display Settings:")
        print(f"  6. Window mode:       {mode}")
        print(f"  7. Position:          {pos_str}")
        print(f"  8. Monitor:           {config['display']['monitor']}")
        print()
        
        # Template settings
        smb = "yes" if config["templates"]["use_smb"] else "no"
        local = config["templates"]["local_path"] or "(none)"
        print("Template Settings:")
        print(f"  9. Use SMB mount:     {smb}")
        print(f"  10. Local path:       {local}")
        print()
        
        print("-" * 50)
        print("  S = Save and exit")
        print("  R = Run with these settings")
        print("  D = Reset to defaults")
        print("  Q = Quit without saving")
        print()
    
    def get_input(prompt, default=None):
        """Get user input with optional default."""
        if default is not None:
            result = input(f"{prompt} [{default}]: ").strip()
            return result if result else str(default)
        return input(f"{prompt}: ").strip()
    
    def config_server_host():
        print()
        print("Server host:")
        print("  1. Auto-discover (recommended)")
        print("  2. Enter hostname (e.g., volumio)")
        print("  3. Enter IP address")
        print()
        choice = input("Choice [1]: ").strip() or "1"
        
        if choice == "1":
            config["server"]["host"] = None
        elif choice == "2":
            host = input("Hostname: ").strip()
            if host:
                config["server"]["host"] = host
        elif choice == "3":
            ip = input("IP address: ").strip()
            if ip:
                config["server"]["host"] = ip
    
    def config_window_mode():
        print()
        print("Window mode:")
        print("  1. Windowed (movable, with title bar)")
        print("  2. Frameless (kiosk style, fixed position)")
        print("  3. Fullscreen")
        print()
        choice = input("Choice [1]: ").strip() or "1"
        
        if choice == "1":
            config["display"]["windowed"] = True
            config["display"]["fullscreen"] = False
        elif choice == "2":
            config["display"]["windowed"] = False
            config["display"]["fullscreen"] = False
        elif choice == "3":
            config["display"]["windowed"] = False
            config["display"]["fullscreen"] = True
    
    def config_position():
        print()
        print("Window position:")
        print("  1. Centered")
        print("  2. Top-left (0, 0)")
        print("  3. Custom coordinates")
        print()
        choice = input("Choice [1]: ").strip() or "1"
        
        if choice == "1":
            config["display"]["position"] = None
        elif choice == "2":
            config["display"]["position"] = [0, 0]
        elif choice == "3":
            try:
                x = int(input("X position: ").strip() or "0")
                y = int(input("Y position: ").strip() or "0")
                config["display"]["position"] = [x, y]
            except ValueError:
                print("Invalid input, using centered")
                config["display"]["position"] = None
    
    def config_port(key, name):
        print()
        current = config["server"][key]
        try:
            value = int(get_input(f"{name}", current))
            config["server"][key] = value
        except ValueError:
            print("Invalid port number")
    
    def config_smb():
        print()
        print("Use SMB mount for templates?")
        print("  1. Yes (mount from Volumio server)")
        print("  2. No (use local templates)")
        print()
        choice = input("Choice [1]: ").strip() or "1"
        config["templates"]["use_smb"] = (choice == "1")
        
        if choice == "2":
            path = input("Local templates path: ").strip()
            if path:
                config["templates"]["local_path"] = path
    
    # Main loop
    while True:
        show_menu()
        choice = input("Choice: ").strip().upper()
        
        if choice == "1":
            config_server_host()
        elif choice == "2":
            config_port("level_port", "Level port")
        elif choice == "3":
            config_port("spectrum_port", "Spectrum port")
        elif choice == "4":
            config_port("volumio_port", "Volumio port")
        elif choice == "5":
            try:
                value = int(get_input("Discovery timeout (seconds)", config["server"]["discovery_timeout"]))
                config["server"]["discovery_timeout"] = value
            except ValueError:
                print("Invalid number")
        elif choice == "6":
            config_window_mode()
        elif choice == "7":
            config_position()
        elif choice == "8":
            try:
                value = int(get_input("Monitor number", config["display"]["monitor"]))
                config["display"]["monitor"] = value
            except ValueError:
                print("Invalid number")
        elif choice == "9":
            config_smb()
        elif choice == "10":
            path = input("Local templates path (empty to clear): ").strip()
            config["templates"]["local_path"] = path if path else None
        elif choice == "S":
            config["wizard_completed"] = True
            if save_config(config):
                print(f"\nConfiguration saved to: {CONFIG_FILE}")
            input("Press Enter to continue...")
            break
        elif choice == "R":
            config["wizard_completed"] = True
            save_config(config)
            print("\nStarting PeppyMeter Remote Client...")
            return config  # Return config to run with
        elif choice == "D":
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            print("\nReset to defaults")
            input("Press Enter to continue...")
        elif choice == "Q":
            print("\nExiting without saving")
            return None
        else:
            input("Invalid choice. Press Enter to continue...")
    
    return None  # Don't run after saving

# =============================================================================
# Server Discovery
# =============================================================================
class ServerDiscovery:
    """Discovers PeppyMeter servers via UDP broadcast."""
    
    def __init__(self, port=DISCOVERY_PORT, timeout=DISCOVERY_TIMEOUT):
        self.port = port
        self.timeout = timeout
        self.servers = {}  # {ip: discovery_data}
        self._stop = False
    
    def discover(self):
        """Listen for server announcements, return dict of discovered servers."""
        print(f"Discovering PeppyMeter servers on UDP port {self.port}...")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)  # 1 second timeout for each recv
        sock.bind(('', self.port))
        
        start_time = time.time()
        
        while not self._stop and (time.time() - start_time) < self.timeout:
            try:
                data, addr = sock.recvfrom(1024)
                try:
                    info = json.loads(data.decode('utf-8'))
                    if info.get('service') == 'peppy_level_server':
                        ip = addr[0]
                        if ip not in self.servers:
                            hostname = info.get('hostname', ip)
                            print(f"  Found: {hostname} ({ip})")
                            self.servers[ip] = {
                                'ip': ip,
                                'hostname': hostname,
                                'level_port': info.get('level_port', 5580),
                                'spectrum_port': info.get('spectrum_port', 5581),
                                'volumio_port': info.get('volumio_port', 3000),
                                'version': info.get('version', 1),
                                'config_version': info.get('config_version', '')
                            }
                        else:
                            # Update config_version if changed
                            new_version = info.get('config_version', '')
                            if new_version and new_version != self.servers[ip].get('config_version'):
                                self.servers[ip]['config_version'] = new_version
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            except socket.timeout:
                continue
            except Exception as e:
                print(f"  Discovery error: {e}")
                break
        
        sock.close()
        return self.servers
    
    def stop(self):
        self._stop = True


# =============================================================================
# Config Version Listener (UDP) - detect server config/template changes
# =============================================================================
class ConfigVersionListener(threading.Thread):
    """
    Listens for UDP discovery packets from the PeppyMeter server and sets
    reload_requested when config_version in a packet differs from the
    client's current version (so the client can reload config/templates).
    """
    def __init__(self, port, current_version_holder, server_ip=None):
        super().__init__(daemon=True)
        self.port = port
        self.current_version_holder = current_version_holder  # dict with 'version' key
        self.server_ip = server_ip  # if set, only accept packets from this IP
        self.reload_requested = False
        self._stop = False
        self._sock = None

    def run(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(1.0)
        try:
            self._sock.bind(('', self.port))
        except OSError as e:
            print(f"  ConfigVersionListener: could not bind to port {self.port}: {e}")
            return
        while not self._stop:
            try:
                data, addr = self._sock.recvfrom(1024)
                if self.server_ip is not None and addr[0] != self.server_ip:
                    continue
                try:
                    info = json.loads(data.decode('utf-8'))
                    if info.get('service') != 'peppy_level_server':
                        continue
                    new_version = info.get('config_version', '')
                    if not new_version:
                        continue
                    current = self.current_version_holder.get('version', '')
                    if new_version != current:
                        self.reload_requested = True
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            except socket.timeout:
                continue
            except OSError:
                if self._stop:
                    break
                raise
        try:
            self._sock.close()
        except Exception:
            pass
        self._sock = None

    def stop_listener(self):
        self._stop = True
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


# =============================================================================
# Config Fetcher (HTTP)
# =============================================================================
class ConfigFetcher:
    """
    Fetches config.txt from server via HTTP.
    
    Uses Volumio plugin API endpoint to get config without SMB symlink issues.
    The server IP address from discovery is used for robust connectivity.
    """
    
    def __init__(self, server_ip, volumio_port=3000):
        self.server_ip = server_ip
        self.volumio_port = volumio_port
        self.cached_config = None
        self.cached_version = None
    
    def fetch(self):
        """
        Fetch config from server via HTTP.
        
        Returns (success, config_content, version) tuple.
        """
        # Use direct IP address for reliable connectivity
        url = f"http://{self.server_ip}:{self.volumio_port}/api/v1/pluginEndpoint?endpoint=peppy_screensaver&method=getRemoteConfig"
        
        try:
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                
                # Volumio REST API wraps plugin response in 'data' field
                # Response format: {"success": true, "data": {"success": true, "version": "...", "config": "..."}}
                if data.get('success'):
                    inner = data.get('data', {})
                    if inner.get('success'):
                        self.cached_config = inner.get('config', '')
                        self.cached_version = inner.get('version', '')
                        return True, self.cached_config, self.cached_version
                    else:
                        error = inner.get('error', 'Unknown error')
                        print(f"  Plugin error fetching config: {error}")
                        return False, None, None
                else:
                    error = data.get('error', 'Unknown error')
                    print(f"  Server error fetching config: {error}")
                    return False, None, None
                    
        except urllib.error.HTTPError as e:
            print(f"  HTTP error fetching config: {e.code} {e.reason}")
            return False, None, None
        except urllib.error.URLError as e:
            print(f"  URL error fetching config: {e.reason}")
            return False, None, None
        except json.JSONDecodeError as e:
            print(f"  JSON error parsing config response: {e}")
            return False, None, None
        except Exception as e:
            print(f"  Error fetching config: {e}")
            return False, None, None
    
    def has_changed(self, new_version):
        """Check if config version has changed."""
        return new_version and new_version != self.cached_version


def _is_ip_address(host):
    """Return True if host is an IPv4 or IPv6 address, False for a hostname."""
    try:
        import ipaddress
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


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
            with open(filepath, 'r') as f:
                content = f.read()
            
            modified = False
            for pattern in patterns:
                if pattern in content:
                    # Check if already patched (contains our full set)
                    if ALL_ICONS_SET not in content:
                        content = content.replace(pattern, f"local_icons = {ALL_ICONS_SET}")
                        modified = True
            
            if modified:
                with open(filepath, 'w') as f:
                    f.write(content)
                patched_count += 1
                
        except Exception as e:
            print(f"  Warning: Failed to patch {handler_file}: {e}")
    
    return patched_count


# =============================================================================
# SMB Mount Manager
# =============================================================================
class SMBMount:
    """Manages SMB mount for remote templates."""
    
    def __init__(self, hostname, mount_point=None):
        self.hostname = hostname
        self.mount_point = Path(mount_point if mount_point else SMB_MOUNT_BASE)
        # .local is for mDNS hostnames only; use host as-is for IP addresses
        if _is_ip_address(hostname):
            self.share_path = f"//{hostname}/{SMB_SHARE_PATH}"
        else:
            self.share_path = f"//{hostname}.local/{SMB_SHARE_PATH}"
        self._mounted = False
    
    def mount(self):
        """Mount the SMB share. Returns True on success."""
        # Create mount point
        self.mount_point.mkdir(parents=True, exist_ok=True)
        
        # Check if already mounted
        if self._is_mounted():
            print(f"SMB share already mounted at {self.mount_point}")
            self._mounted = True
            return True
        
        # Try guest mount first
        print(f"Mounting {self.share_path} at {self.mount_point}...")
        
        # Try guest mount
        result = subprocess.run(
            ['sudo', 'mount', '-t', 'cifs', self.share_path, str(self.mount_point),
             '-o', 'guest,ro,nofail'],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            print("  Mounted as guest")
            self._mounted = True
            return True
        
        # Try with volumio credentials
        result = subprocess.run(
            ['sudo', 'mount', '-t', 'cifs', self.share_path, str(self.mount_point),
             '-o', 'user=volumio,password=volumio,ro,nofail'],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            print("  Mounted with volumio credentials")
            self._mounted = True
            return True
        
        print(f"  Failed to mount: {result.stderr}")
        return False
    
    def unmount(self):
        """Unmount the SMB share."""
        if self._mounted and self._is_mounted():
            subprocess.run(['sudo', 'umount', str(self.mount_point)], 
                         capture_output=True)
            self._mounted = False
    
    def _is_mounted(self):
        """Check if the mount point is currently mounted."""
        result = subprocess.run(['mountpoint', '-q', str(self.mount_point)])
        return result.returncode == 0
    
    @property
    def templates_path(self):
        """Path to templates directory."""
        return self.mount_point / 'templates'


# =============================================================================
# Level Data Receiver
# =============================================================================
class LevelReceiver:
    """
    Receives audio level data over UDP.
    
    Protocol v2 features:
    - Sends registration packet to server on startup
    - Sends periodic heartbeat packets to maintain connection
    - Sends unregister packet on clean shutdown
    
    This allows the server to track connected clients for diagnostics
    while remaining backward compatible (server still broadcasts to all).
    """
    
    CLIENT_VERSION = 2  # Protocol version
    HEARTBEAT_INTERVAL = 30  # seconds between heartbeats
    
    def __init__(self, server_ip, port=5580, client_id=None, subscriptions=None):
        self.server_ip = server_ip
        self.port = port
        self.sock = None
        self._running = False
        self._thread = None
        self._heartbeat_thread = None
        
        # Generate unique client_id if not provided
        if client_id:
            self.client_id = client_id
        else:
            # Use hostname + random suffix for uniqueness
            hostname = socket.gethostname()
            import uuid
            suffix = uuid.uuid4().hex[:6]
            self.client_id = f"{hostname}-{suffix}"
        
        # What data streams this client subscribes to
        self.subscriptions = subscriptions or ['meters']
        
        # Current level data (thread-safe via GIL for simple reads)
        self.left = 0.0
        self.right = 0.0
        self.mono = 0.0
        self.seq = 0
        self.last_update = 0
    
    def _send_registration(self):
        """Send registration packet to server."""
        if not self.sock or not self.server_ip:
            return
        try:
            msg = json.dumps({
                'type': 'register',
                'client_id': self.client_id,
                'version': self.CLIENT_VERSION,
                'subscribe': self.subscriptions
            }).encode('utf-8')
            self.sock.sendto(msg, (self.server_ip, self.port))
            print(f"  Registered with server as '{self.client_id}' (v{self.CLIENT_VERSION})")
        except Exception as e:
            print(f"  Registration failed: {e}")
    
    def _send_heartbeat(self):
        """Send heartbeat packet to server."""
        if not self.sock or not self.server_ip:
            return
        try:
            msg = json.dumps({
                'type': 'heartbeat',
                'client_id': self.client_id
            }).encode('utf-8')
            self.sock.sendto(msg, (self.server_ip, self.port))
        except Exception:
            pass  # Heartbeat failures are silent
    
    def _send_unregister(self):
        """Send unregister packet to server on clean shutdown."""
        if not self.sock or not self.server_ip:
            return
        try:
            msg = json.dumps({
                'type': 'unregister',
                'client_id': self.client_id
            }).encode('utf-8')
            self.sock.sendto(msg, (self.server_ip, self.port))
        except Exception:
            pass  # Unregister failures are silent
    
    def _heartbeat_loop(self):
        """Background thread to send periodic heartbeats."""
        while self._running:
            time.sleep(self.HEARTBEAT_INTERVAL)
            if self._running:
                self._send_heartbeat()
    
    def start(self):
        """Start receiving level data in background thread."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(1.0)
        self.sock.bind(('', self.port))
        
        self._running = True
        
        # Send registration to server
        self._send_registration()
        
        # Start heartbeat thread
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        
        # Start receive thread
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        print(f"Level receiver started on UDP port {self.port}")
    
    def _receive_loop(self):
        """Background thread to receive level data."""
        while self._running:
            try:
                data, addr = self.sock.recvfrom(1024)
                if len(data) == 16:  # uint32 + 3 floats
                    seq, left, right, mono = struct.unpack('<Ifff', data)
                    self.seq = seq
                    self.left = left
                    self.right = right
                    self.mono = mono
                    self.last_update = time.time()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    print(f"Level receiver error: {e}")
                break
    
    def stop(self):
        """Stop receiving."""
        # Send unregister before stopping
        self._send_unregister()
        
        self._running = False
        if self.sock:
            self.sock.close()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2.0)
    
    def get_levels(self):
        """Get current level data as tuple (left, right, mono)."""
        return (self.left, self.right, self.mono)


# =============================================================================
# Spectrum Data Receiver
# =============================================================================
class SpectrumReceiver:
    """
    Receives spectrum analyzer (FFT) data over UDP.
    
    Packet format (variable size, little-endian):
        - seq (uint32): Sequence number for loss detection
        - size (uint16): Number of frequency bins
        - bins (float32 * size): Frequency bin values (0-100)
    """
    
    def __init__(self, server_ip, port=5581):
        self.server_ip = server_ip
        self.port = port
        self.sock = None
        self._running = False
        self._thread = None
        
        # Current spectrum data (thread-safe via GIL for simple reads)
        self.seq = 0
        self.size = 0
        self.bins = []  # List of frequency bin values
        self.last_update = 0
        self._first_packet_logged = False
    
    def start(self):
        """Start receiving spectrum data in background thread."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(1.0)
        self.sock.bind(('', self.port))
        
        self._running = True
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        print(f"Spectrum receiver started on UDP port {self.port}")
    
    def _receive_loop(self):
        """Background thread to receive spectrum data."""
        while self._running:
            try:
                data, addr = self.sock.recvfrom(1024)
                source_ip = addr[0]
                
                # FILTER: Only accept packets from our configured server
                # This prevents interference from other Volumio instances on the network
                if source_ip != self.server_ip:
                    continue
                
                if len(data) >= 6:  # Minimum: uint32 + uint16
                    # Unpack header
                    seq, size = struct.unpack('<IH', data[:6])
                    expected_len = 6 + (size * 4)  # header + bins (float32 each)
                    
                    if len(data) >= expected_len:
                        # Unpack bins
                        fmt = '<' + str(size) + 'f'
                        bins = list(struct.unpack(fmt, data[6:expected_len]))
                        
                        self.seq = seq
                        self.size = size
                        self.bins = bins
                        self.last_update = time.time()
                        
                        # Log first successful packet
                        if not self._first_packet_logged:
                            print(f"Spectrum receiver: first packet from {source_ip} - {size} bins")
                            self._first_packet_logged = True
                            
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    print(f"Spectrum receiver error: {e}")
                break
    
    def stop(self):
        """Stop receiving."""
        self._running = False
        if self.sock:
            self.sock.close()
        if self._thread:
            self._thread.join(timeout=2.0)
    
    def get_data(self):
        """
        Get current spectrum data in the format PeppySpectrum expects.
        
        Returns raw bytes matching the pipe format (int32 per bin, little-endian).
        """
        if not self.bins:
            return None
        
        # Convert float bins back to int32 bytes (same format as pipe)
        result = bytearray()
        for val in self.bins:
            int_val = int(val) & 0xFFFFFFFF
            result.extend([
                int_val & 0xFF,
                (int_val >> 8) & 0xFF,
                (int_val >> 16) & 0xFF,
                (int_val >> 24) & 0xFF
            ])
        return bytes(result)
    
    def get_bins(self):
        """Get current spectrum bins as list of floats."""
        return self.bins.copy() if self.bins else []
    
    def has_data(self):
        """Check if we've received any spectrum data."""
        return self.last_update > 0


# =============================================================================
# Remote Data Source (for PeppyMeter integration)
# =============================================================================
class RemoteDataSource:
    """
    A DataSource implementation that gets data from the LevelReceiver.
    This mimics PeppyMeter's DataSource interface for seamless integration.
    """
    
    def __init__(self, level_receiver):
        self.level_receiver = level_receiver
        self.volume = 100  # Used by some meters
        self.data = (0.0, 0.0, 0.0)  # (left, right, mono)
    
    def start_data_source(self):
        """Start the data source (already running via LevelReceiver)."""
        pass
    
    def stop_data_source(self):
        """Stop the data source."""
        pass
    
    def get_current_data(self):
        """Return current data as tuple (left, right, mono)."""
        return (self.level_receiver.left, 
                self.level_receiver.right, 
                self.level_receiver.mono)
    
    def get_current_left_channel_data(self):
        return self.level_receiver.left
    
    def get_current_right_channel_data(self):
        return self.level_receiver.right
    
    def get_current_mono_channel_data(self):
        return self.level_receiver.mono


# =============================================================================
# Remote Spectrum Output (for remote spectrum visualization)
# =============================================================================
class RemoteSpectrumOutput:
    """
    A SpectrumOutput replacement that uses network data instead of pipe.
    
    This class initializes the Spectrum visual components normally but
    bypasses the pipe-reading data source, instead receiving bar heights
    from the SpectrumReceiver and injecting them directly.
    """
    
    def __init__(self, util, meter_config_volumio, screensaver_path, spectrum_receiver):
        """Initialize remote spectrum output.
        
        :param util: PeppyMeter utility class
        :param meter_config_volumio: Volumio meter configuration
        :param screensaver_path: Path to screensaver directory (contains 'spectrum' subfolder)
        :param spectrum_receiver: SpectrumReceiver instance for network data
        """
        self.util = util
        self.meter_config_volumio = meter_config_volumio
        self.screensaver_path = screensaver_path
        self.spectrum_receiver = spectrum_receiver
        self.sp = None
        self._initialized = False
        self._fade_in_done = False
        self._fade_factor = 0.0
        self._last_packet_seq = -1  # Track last processed packet
        self._local_bins = None  # Local copy for decay between packets
        self._decay_rate = 0.85  # Decay multiplier per frame (0.85 = fast drop)
        
        # Get spectrum config from meter section
        from volumio_configfileparser import SPECTRUM, SPECTRUM_SIZE, SPECTRUM_POS
        from configfileparser import METER
        
        meter_config = util.meter_config
        meter_section = meter_config_volumio[meter_config[METER]]
        
        self.w = meter_section[SPECTRUM_SIZE][0]
        self.h = meter_section[SPECTRUM_SIZE][1]
        self.s = meter_section[SPECTRUM]
        # Get spectrum position within the meter layout (from meters.txt spectrum.pos)
        self.pos = meter_section.get(SPECTRUM_POS, (0, 0)) or (0, 0)
        # screensaver_path is ~/peppy_remote/screensaver, spectrum is directly under it
        self.SpectrumPath = os.path.join(screensaver_path, 'spectrum')
        
    
    def start(self):
        """Initialize spectrum visual components (but not data source)."""
        try:
            import pygame as pg
            import configparser
            from spectrumutil import SpectrumUtil
            from spectrum.spectrum import Spectrum
            from spectrumconfigparser import SCREEN_WIDTH, SCREEN_HEIGHT, AVAILABLE_SPECTRUM_NAMES
            
            # Set up util for spectrum
            self.util.spectrum_size = (self.w, self.h, self.s)
            self.util.pygame_screen = self.util.PYGAME_SCREEN
            self.util.image_util = SpectrumUtil()
            
            # Save original screen_rect (full meter display area)
            original_screen_rect = getattr(self.util, 'screen_rect', None)
            
            # Get templates_spectrum path from SMB mount
            # screensaver_path is ~/peppy_remote/screensaver
            # SMB mount is at ~/peppy_remote/mnt (contains templates/ and templates_spectrum/)
            install_dir = os.path.dirname(self.screensaver_path)  # ~/peppy_remote
            templates_spectrum_path = os.path.join(install_dir, 'mnt', 'templates_spectrum')
            
            # Get the meter folder name (e.g., "1280x720_custom_3") from config
            from configfileparser import SCREEN_INFO, METER_FOLDER
            meter_folder = self.util.meter_config[SCREEN_INFO][METER_FOLDER]  # e.g., "1280x720_custom_3"
            
            # Set up spectrum config.txt to point to the right template folder
            spectrum_config_path = os.path.join(self.SpectrumPath, 'config.txt')
            if os.path.exists(spectrum_config_path):
                sp_config = configparser.ConfigParser()
                sp_config.read(spectrum_config_path)
                
                # Update paths for remote client
                if 'current' not in sp_config:
                    sp_config['current'] = {}
                sp_config['current']['base.folder'] = templates_spectrum_path
                sp_config['current']['spectrum.folder'] = meter_folder
                # Update pipe name to avoid error (won't be used since we don't start data source)
                sp_config['current']['pipe.name'] = '/tmp/myfifosa'
                
                with open(spectrum_config_path, 'w') as f:
                    sp_config.write(f)
            
            # Change to spectrum path to find config
            original_cwd = os.getcwd()
            os.chdir(self.SpectrumPath)
            
            try:
                # Create spectrum object (standalone=False for plugin mode)
                # Note: Spectrum.__init__ calls ScreensaverSpectrum which overwrites util.screen_rect
                self.sp = Spectrum(self.util, standalone=False)
                
                
                # Override dimensions
                self.sp.config[SCREEN_WIDTH] = self.w
                self.sp.config[SCREEN_HEIGHT] = self.h
                
                # Set spectrum name and reload configs
                self.sp.config[AVAILABLE_SPECTRUM_NAMES] = [self.s]
                self.sp.spectrum_configs = self.sp.config_parser.get_spectrum_configs()
                
                self.sp.init_spectrums()
                
                # Initialize visual components (from Spectrum.start() but without data source)
                # This sets up bounding_box for all components
                from spectrumconfigparser import REFLECTION_GAP
                self.sp.index = 0
                self.sp.set_background()
                self.sp.set_bars()
                self.sp.reflection_gap = self.sp.spectrum_configs[self.sp.index].get(REFLECTION_GAP, 0)
                self.sp.set_reflections()
                self.sp.set_toppings()
                self.sp.set_foreground()
                self.sp.init_variables()
                
                # CRITICAL: Offset all component positions by spectrum.pos from meters.txt
                # The spectrum renders with coordinates relative to its own canvas (0,0)
                # but we need to position it within the meter layout
                pos_x, pos_y = self.pos
                if pos_x != 0 or pos_y != 0:
                    for comp in self.sp.components:
                        if hasattr(comp, 'content_x'):
                            comp.content_x += pos_x
                        if hasattr(comp, 'content_y'):
                            comp.content_y += pos_y
                    print(f"[RemoteSpectrum] Applied position offset: ({pos_x}, {pos_y})")
                
                # Restore original screen_rect (full screen) - Spectrum.__init__ overwrote it
                if original_screen_rect is not None:
                    self.util.screen_rect = original_screen_rect
                else:
                    # Set to full screen if wasn't set before
                    from configfileparser import SCREEN_INFO, WIDTH, HEIGHT
                    screen_w = self.util.meter_config[SCREEN_INFO][WIDTH]
                    screen_h = self.util.meter_config[SCREEN_INFO][HEIGHT]
                    self.util.screen_rect = pg.Rect(0, 0, screen_w, screen_h)
                
                # Store spectrum clip rect for drawing
                # The spectrum content is positioned based on spectrum.x and spectrum.y from spectrum.txt
                # NOT from self.pos (meters.txt spectrum.pos which is often 0,0)
                spectrum_x = self.sp.spectrum_configs[0].get('spectrum.x', 0)
                spectrum_y = self.sp.spectrum_configs[0].get('spectrum.y', 0)
                # The clip rect should cover where the spectrum actually renders
                # Content ranges from (spectrum_x, spectrum_y - bar_height) to (spectrum_x + w, spectrum_y + reflection_height)
                # For safety, use the full spectrum canvas dimensions positioned at spectrum.x/y
                self.spectrum_clip_rect = pg.Rect(spectrum_x, spectrum_y - self.sp.height, self.w, self.h + self.sp.height)
                
                # Set run flag but DON'T start data source (we feed via network)
                self.sp.run_flag = True
                # NOT calling: self.sp.start_data_source()
                
                self._initialized = True
                
            finally:
                os.chdir(original_cwd)
                
        except Exception as e:
            print(f"[RemoteSpectrum] Failed to initialize: {e}")
            import traceback
            traceback.print_exc()
            self.sp = None
    
    def update(self):
        """Update spectrum from network data and render."""
        if not self._initialized or self.sp is None:
            if not hasattr(self, '_dbg_init_warn'):
                print(f"[RemoteSpectrum] update: not initialized={not self._initialized}, sp={self.sp}")
                self._dbg_init_warn = True
            return
        
        # Get bar heights from network
        bins = self.spectrum_receiver.get_bins()
        current_seq = self.spectrum_receiver.seq
        
        # Initialize local bins on first data (match the spectrum's bar count)
        if self._local_bins is None and hasattr(self.sp, '_prev_bar_heights'):
            self._local_bins = [0.0] * len(self.sp._prev_bar_heights)
        
        if not self._local_bins:
            return
        
        # Check if we have new packet data
        new_packet = bins and current_seq != self._last_packet_seq
        
        
        # SMOOTH ANIMATION LOGIC:
        # 1. Always decay local bins (bars fall naturally)
        # 2. Only push bars UP when server sends genuinely NEW higher values
        # 3. Ignore repeated/stale server data so decay can work
        
        num_bars = min(len(self._local_bins), len(self.sp._prev_bar_heights))
        
        # Track previous server data to detect actual changes
        if not hasattr(self, '_prev_server_bins'):
            self._prev_server_bins = None
        
        # Check if server sent genuinely different data (not just repeated)
        server_data_changed = False
        if bins and self._prev_server_bins:
            # Consider it "changed" if ANY bin differs by more than 1
            for i in range(min(len(bins), len(self._prev_server_bins))):
                if abs(bins[i] - self._prev_server_bins[i]) > 1:
                    server_data_changed = True
                    break
        elif bins and not self._prev_server_bins:
            server_data_changed = True  # First data
        
        if bins:
            self._prev_server_bins = bins.copy()
        
        # Step 1: Apply decay to all local bins (ALWAYS)
        for i in range(num_bars):
            self._local_bins[i] *= self._decay_rate
            if self._local_bins[i] < 0.5:
                self._local_bins[i] = 0
        
        # Step 2: Only use server data when it's genuinely NEW and HIGHER
        if bins and server_data_changed:
            num_to_copy = min(len(bins), num_bars)
            for i in range(num_to_copy):
                server_val = bins[i]
                if server_val > self._local_bins[i]:
                    self._local_bins[i] = server_val  # Instant rise to peak
        
        # Step 3: Update visual components
        for i in range(num_bars):
            new_height = self._local_bins[i]
            
            # Fade in effect on first data
            if not self._fade_in_done:
                new_height *= self._fade_factor
            
            idx = i + 1  # 1-based index for Spectrum methods
            
            # Force update by setting prev to different value (bypass optimization)
            self.sp._prev_bar_heights[i] = new_height + 100
            
            try:
                self.sp.set_bar_y(idx, new_height)
                if hasattr(self.sp, 'set_reflection_y'):
                    self.sp.set_reflection_y(idx, new_height)
                if hasattr(self.sp, 'set_topping_y'):
                    self.sp.set_topping_y(idx, new_height)
            except Exception:
                pass
        
        
        # Gradual fade-in
        if not self._fade_in_done:
            self._fade_factor = min(1.0, self._fade_factor + 0.05)  # ~20 frames to full
            if self._fade_factor >= 1.0:
                self._fade_in_done = True
        
        # Draw spectrum (without display.update - parent handles that)
        try:
            import pygame as pg
            prev_clip = self.util.pygame_screen.get_clip()
            # Use spectrum-specific clip rect
            clip_rect = getattr(self, 'spectrum_clip_rect', self.util.screen_rect)
            self.util.pygame_screen.set_clip(clip_rect)
            
            # Clean and draw
            if hasattr(self.sp, '_dirty_rects') and self.sp._dirty_rects:
                for rect in self.sp._dirty_rects:
                    self.sp.draw_area(rect)
                self.sp._dirty_rects = []
            self.sp.draw()
            
            self.util.pygame_screen.set_clip(prev_clip)
        except Exception:
            pass  # Silently handle draw errors
    
    def stop_thread(self):
        """Stop spectrum."""
        if self.sp:
            try:
                self.sp.stop()
            except Exception:
                pass
        self._initialized = False
    
    def get_current_bins(self):
        """Get current bar heights (for compatibility)."""
        if self.sp and hasattr(self.sp, '_prev_bar_heights'):
            return list(self.sp._prev_bar_heights)
        return None


# =============================================================================
# Setup Remote Config
# =============================================================================
def setup_remote_config(peppymeter_path, templates_path, config_fetcher):
    """
    Set up config.txt for remote client mode.
    
    Fetches config from server via HTTP and adjusts paths for local use.
    """
    import configparser
    
    config_path = os.path.join(peppymeter_path, "config.txt")
    server_config_fetched = False
    
    # Try to fetch config from server via HTTP
    if config_fetcher:
        print("Fetching config from server via HTTP...")
        success, config_content, version = config_fetcher.fetch()
        if success and config_content:
            try:
                with open(config_path, 'w') as f:
                    f.write(config_content)
                server_config_fetched = True
                print(f"  Config fetched successfully (version: {version})")
            except Exception as e:
                print(f"  Failed to write fetched config: {e}")
    
    if not server_config_fetched:
        print("Server config not available, using defaults")
    
    # Now read and adjust the config for local use
    config = configparser.ConfigParser()
    
    if os.path.exists(config_path):
        try:
            config.read(config_path)
        except Exception:
            pass  # Start fresh if parse error
    
    # Ensure all required sections exist
    if 'current' not in config:
        config['current'] = {}
    if 'sdl.env' not in config:
        config['sdl.env'] = {}
    if 'data.source' not in config:
        config['data.source'] = {}
    
    # Update paths for local client
    config['current']['base.folder'] = templates_path
    
    # SDL settings for windowed display (not embedded framebuffer)
    # These will be read by volumio_peppymeter's init_display()
    config['sdl.env']['framebuffer.device'] = ''
    config['sdl.env']['video.driver'] = ''  # Empty for auto-detect (X11/Wayland)
    config['sdl.env']['video.display'] = os.environ.get('DISPLAY', ':0')
    config['sdl.env']['mouse.enabled'] = 'False'
    config['sdl.env']['double.buffer'] = 'True'
    config['sdl.env']['no.frame'] = 'False'  # Allow window frame on desktop
    
    # Data source configuration
    # Keep 'pipe' type - it will fail silently (no data) since /tmp/myfifo doesn't exist
    # The actual data comes from RemoteDataSource which we inject at runtime
    # Don't use 'noise' - it generates random values causing chaotic meter behavior
    # Keep smooth buffer for smoother needle movement
    config['data.source']['smooth.buffer.size'] = '4'
    
    # Write adjusted config
    with open(config_path, 'w') as f:
        config.write(f)
    
    if server_config_fetched:
        meter_folder = config['current'].get('meter.folder', 'unknown')
        print(f"  Config adjusted for local use (meter: {meter_folder})")
    
    return config_path


# =============================================================================
# Full PeppyMeter Display (using volumio_peppymeter)
# =============================================================================
def run_peppymeter_display(level_receiver, server_info, templates_path, config_fetcher, spectrum_receiver=None):
    """Run full PeppyMeter rendering using volumio_peppymeter code.
    
    :param level_receiver: LevelReceiver for audio level data
    :param server_info: Server information dict (ip, ports, etc.)
    :param templates_path: Path to templates
    :param config_fetcher: ConfigFetcher instance
    :param spectrum_receiver: Optional SpectrumReceiver for spectrum data
    """
    
    import ctypes
    
    # Set up paths - mirrors Volumio plugin structure
    screensaver_path = os.path.join(SCRIPT_DIR, "screensaver")
    peppymeter_path = os.path.join(screensaver_path, "peppymeter")
    
    if not os.path.exists(peppymeter_path):
        print(f"ERROR: PeppyMeter not found at {peppymeter_path}")
        print("Run the installer first:")
        print("  curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.sh | bash")
        return False
    
    if not os.path.exists(os.path.join(screensaver_path, "volumio_peppymeter.py")):
        print(f"ERROR: volumio_peppymeter.py not found at {screensaver_path}")
        print("Run the installer to download Volumio custom handlers.")
        return False
    
    spectrum_path = os.path.join(screensaver_path, "spectrum")
    if not os.path.exists(spectrum_path):
        print(f"ERROR: PeppySpectrum not found at {spectrum_path}")
        print("Run the installer to download PeppySpectrum.")
        return False
    
    # Setup format icons - copy bundled icons to screensaver/ where handlers expect them
    # This must happen BEFORE importing handlers as they check for icons at init time
    setup_format_icons(screensaver_path, server_info['ip'], 
                       server_info.get('volumio_port', 3000))
    
    # Fetch and setup config BEFORE any imports that might read it
    config_path = setup_remote_config(peppymeter_path, templates_path, config_fetcher)
    
    # Set SDL environment for desktop BEFORE pygame import
    # This prevents volumio_peppymeter's init_display from setting framebuffer mode
    # Remove ALL framebuffer-related SDL variables
    for var in ['SDL_FBDEV', 'SDL_MOUSEDEV', 'SDL_MOUSEDRV', 'SDL_NOMOUSE']:
        os.environ.pop(var, None)
    
    # Remove SDL_VIDEODRIVER if it's set to framebuffer/headless modes OR empty string
    sdl_driver = os.environ.get('SDL_VIDEODRIVER', None)
    if sdl_driver is not None and (sdl_driver == '' or sdl_driver in ('dummy', 'fbcon', 'directfb')):
        del os.environ['SDL_VIDEODRIVER']
    
    # Ensure DISPLAY is set for X11
    if 'DISPLAY' not in os.environ:
        os.environ['DISPLAY'] = ':0'
    
    print(f"  SDL environment configured for desktop (DISPLAY={os.environ.get('DISPLAY')})")
    
    # Add paths to Python path
    # Order matters: screensaver first (volumio_*.py), then peppymeter, then spectrum
    spectrum_path = os.path.join(screensaver_path, "spectrum")
    if screensaver_path not in sys.path:
        sys.path.insert(0, screensaver_path)
    if peppymeter_path not in sys.path:
        sys.path.insert(0, peppymeter_path)
    if spectrum_path not in sys.path:
        sys.path.insert(0, spectrum_path)
    
    # Change to peppymeter directory (volumio_peppymeter expects this)
    original_cwd = os.getcwd()
    os.chdir(peppymeter_path)
    
    try:
        # Enable X11 threading
        try:
            ctypes.CDLL('libX11.so.6').XInitThreads()
        except Exception:
            pass  # Not on X11 or library not found
        
        print("Loading PeppyMeter...")
        
        # Import PeppyMeter components
        # Note: peppymeter.peppymeter because Peppymeter class is in peppymeter/peppymeter.py
        from peppymeter.peppymeter import Peppymeter
        from configfileparser import (
            SCREEN_INFO, WIDTH, HEIGHT, DEPTH, SDL_ENV, DOUBLE_BUFFER, SCREEN_RECT
        )
        from volumio_configfileparser import Volumio_ConfigFileParser, COLOR_DEPTH
        
        # Import volumio_peppymeter functions (NOT init_display - we have our own for desktop)
        from volumio_peppymeter import (
            start_display_output, CallBack,
            init_debug_config, log_debug, memory_limit
        )
        
        # Initialize base PeppyMeter
        print("Initializing PeppyMeter...")
        pm = Peppymeter(standalone=True, timer_controlled_random_meter=False, 
                       quit_pygame_on_stop=False)
        
        # Parse Volumio configuration
        parser = Volumio_ConfigFileParser(pm.util)
        meter_config_volumio = parser.meter_config_volumio
        
        # Initialize debug settings
        init_debug_config(meter_config_volumio)
        log_debug("=== PeppyMeter Remote Client starting ===", "basic")
        
        # Replace data source with remote data source
        print("Connecting remote data source...")
        remote_ds = RemoteDataSource(level_receiver)
        
        # Stop the original data source if it exists (prevents noise/pipe conflicts)
        original_ds = getattr(pm, 'data_source', None)
        if original_ds and original_ds != remote_ds:
            if hasattr(original_ds, 'stop_data_source'):
                try:
                    original_ds.stop_data_source()
                except Exception:
                    pass
        
        # Inject remote data source into both Peppymeter AND Meter
        # PeppyMeter's meter.run() uses self.data_source internally
        pm.data_source = remote_ds
        if hasattr(pm, 'meter') and pm.meter:
            pm.meter.data_source = remote_ds
        
        # Create callback handler
        callback = CallBack(pm.util, meter_config_volumio, pm.meter)
        pm.meter.callback_start = callback.peppy_meter_start
        pm.meter.callback_stop = callback.peppy_meter_stop
        pm.dependent = callback.peppy_meter_update
        pm.meter.malloc_trim = callback.trim_memory
        pm.malloc_trim = callback.exit_trim_memory
        
        # Get screen dimensions
        screen_w = pm.util.meter_config[SCREEN_INFO][WIDTH]
        screen_h = pm.util.meter_config[SCREEN_INFO][HEIGHT]
        depth = meter_config_volumio[COLOR_DEPTH]
        pm.util.meter_config[SCREEN_INFO][DEPTH] = depth
        print(f"Display: {screen_w}x{screen_h}")
        
        memory_limit()
        
        # Initialize display - CLIENT SPECIFIC (not using init_display from volumio_peppymeter)
        # volumio_peppymeter.init_display() sets SDL_FBDEV which breaks X11 desktop display
        # We initialize pygame directly for desktop use
        import pygame as pg
        
        # Ensure clean SDL environment for X11/Wayland desktop
        # These must be unset/correct BEFORE pg.display.init()
        for var in ['SDL_FBDEV', 'SDL_MOUSEDEV', 'SDL_MOUSEDRV', 'SDL_NOMOUSE']:
            os.environ.pop(var, None)
        # Don't set SDL_VIDEODRIVER - let SDL auto-detect (x11, wayland)
        os.environ.pop('SDL_VIDEODRIVER', None)
        if 'DISPLAY' not in os.environ:
            os.environ['DISPLAY'] = ':0'
        
        pg.display.init()
        pg.mouse.set_visible(False)
        pg.font.init()
        
        # Config version listener: detect server config/template changes and reload
        current_version_holder = {'version': config_fetcher.cached_version or ''}
        discovery_port = server_info.get('discovery_port', DISCOVERY_PORT)
        version_listener = ConfigVersionListener(
            discovery_port, current_version_holder, server_ip=server_info['ip']
        )
        version_listener.start()
        
        peppy_running_file = '/tmp/peppyrunning'
        from pathlib import Path
        
        # Determine display flags from config (passed via environment)
        is_windowed = os.environ.get('PEPPY_DISPLAY_WINDOWED', '1') == '1'
        is_fullscreen = os.environ.get('PEPPY_DISPLAY_FULLSCREEN', '0') == '1'
        
        flags = 0
        if is_fullscreen:
            flags |= pg.FULLSCREEN
        elif not is_windowed:
            # Frameless kiosk mode
            flags |= pg.NOFRAME
        # If windowed, no special flags (default window with title bar)
        
        if pm.util.meter_config[SDL_ENV][DOUBLE_BUFFER]:
            flags |= pg.DOUBLEBUF
        
        screen = pg.display.set_mode((screen_w, screen_h), flags, depth)
        pm.util.meter_config[SCREEN_RECT] = pg.Rect(0, 0, screen_w, screen_h)
        
        # Set window title if windowed
        if is_windowed and not is_fullscreen:
            pg.display.set_caption("PeppyMeter Remote")
        
        pm.util.PYGAME_SCREEN = screen
        pm.util.screen_copy = pm.util.PYGAME_SCREEN
        
        # Initialize remote spectrum if receiver is provided and spectrum is enabled
        remote_spectrum = None
        if spectrum_receiver:
            try:
                from volumio_configfileparser import SPECTRUM_VISIBLE
                from configfileparser import METER
                meter_name = pm.util.meter_config[METER]
                meter_section = meter_config_volumio.get(meter_name, {})
                spectrum_visible = meter_section.get(SPECTRUM_VISIBLE, False)
                
                if spectrum_visible:
                    print("Initializing remote spectrum...")
                    remote_spectrum = RemoteSpectrumOutput(
                        pm.util, meter_config_volumio, screensaver_path, spectrum_receiver
                    )
                    remote_spectrum.start()
                    # Inject into callback so it's used instead of local SpectrumOutput
                    callback.spectrum_output = remote_spectrum
                    print("  Remote spectrum initialized")
                else:
                    print("  Spectrum not visible in current meter config")
            except Exception as e:
                print(f"  Remote spectrum initialization failed: {e}")
                import traceback
                traceback.print_exc()
        
        mode_str = "fullscreen" if is_fullscreen else ("windowed" if is_windowed else "frameless")
        print(f"Starting meter display ({mode_str})...")
        print("Press ESC or Q to exit, or click/touch screen")
        
        Path(peppy_running_file).touch()
        Path(peppy_running_file).chmod(0o777)
        
        # Support both old and new volumio_peppymeter (new has check_reload_callback)
        try:
            import inspect
            _sig = inspect.signature(start_display_output)
            reload_callback_supported = 'check_reload_callback' in _sig.parameters
        except Exception:
            reload_callback_supported = False
        
        try:
            while True:
                current_version_holder['version'] = config_fetcher.cached_version or ''
                version_listener.reload_requested = False
                Path(peppy_running_file).touch()
                Path(peppy_running_file).chmod(0o777)
                if reload_callback_supported:
                    start_display_output(pm, callback, meter_config_volumio,
                                        volumio_host=server_info['ip'],
                                        volumio_port=server_info['volumio_port'],
                                        check_reload_callback=lambda: version_listener.reload_requested)
                else:
                    start_display_output(pm, callback, meter_config_volumio,
                                        volumio_host=server_info['ip'],
                                        volumio_port=server_info['volumio_port'])
                if not version_listener.reload_requested:
                    break
                print("Config changed on server, reloading...")
                setup_remote_config(peppymeter_path, templates_path, config_fetcher)
                pm = Peppymeter(standalone=True, timer_controlled_random_meter=False,
                               quit_pygame_on_stop=False)
                parser = Volumio_ConfigFileParser(pm.util)
                meter_config_volumio = parser.meter_config_volumio
                init_debug_config(meter_config_volumio)
                pm.data_source = remote_ds
                if hasattr(pm, 'meter') and pm.meter:
                    pm.meter.data_source = remote_ds
                callback = CallBack(pm.util, meter_config_volumio, pm.meter)
                pm.meter.callback_start = callback.peppy_meter_start
                pm.meter.callback_stop = callback.peppy_meter_stop
                pm.dependent = callback.peppy_meter_update
                pm.meter.malloc_trim = callback.trim_memory
                pm.malloc_trim = callback.exit_trim_memory
                
                # Stop old spectrum if running
                if spectrum_receiver and remote_spectrum:
                    remote_spectrum.stop_thread()
                    remote_spectrum = None
                
                # Use new config dimensions; recreate window if resolution changed
                new_screen_w = pm.util.meter_config[SCREEN_INFO][WIDTH]
                new_screen_h = pm.util.meter_config[SCREEN_INFO][HEIGHT]
                new_depth = meter_config_volumio[COLOR_DEPTH]
                
                if (new_screen_w, new_screen_h) != (screen_w, screen_h):
                    print(f"Display resizing: {screen_w}x{screen_h} -> {new_screen_w}x{new_screen_h}")
                    # Recompute display flags for new window
                    new_flags = 0
                    if is_fullscreen:
                        new_flags |= pg.FULLSCREEN
                    elif not is_windowed:
                        new_flags |= pg.NOFRAME
                    if pm.util.meter_config[SDL_ENV][DOUBLE_BUFFER]:
                        new_flags |= pg.DOUBLEBUF
                    
                    screen = pg.display.set_mode((new_screen_w, new_screen_h), new_flags, new_depth)
                    screen_w = new_screen_w
                    screen_h = new_screen_h
                    depth = new_depth
                    
                    if is_windowed and not is_fullscreen:
                        pg.display.set_caption("PeppyMeter Remote")
                    print(f"Display resized to {screen_w}x{screen_h}")
                else:
                    # Update depth even if size unchanged
                    depth = new_depth
                
                # Attach current window to the new pm BEFORE spectrum init
                pm.util.meter_config[SCREEN_INFO][WIDTH] = screen_w
                pm.util.meter_config[SCREEN_INFO][HEIGHT] = screen_h
                pm.util.meter_config[SCREEN_INFO][DEPTH] = depth
                pm.util.PYGAME_SCREEN = screen
                pm.util.screen_copy = screen
                pm.util.meter_config[SCREEN_RECT] = pg.Rect(0, 0, screen_w, screen_h)
                
                # Re-initialize remote spectrum AFTER screen is attached
                if spectrum_receiver:
                    try:
                        from volumio_configfileparser import SPECTRUM_VISIBLE
                        from configfileparser import METER
                        meter_name = pm.util.meter_config[METER]
                        meter_section = meter_config_volumio.get(meter_name, {})
                        spectrum_visible = meter_section.get(SPECTRUM_VISIBLE, False)
                        if spectrum_visible:
                            remote_spectrum = RemoteSpectrumOutput(
                                pm.util, meter_config_volumio, screensaver_path, spectrum_receiver
                            )
                            remote_spectrum.start()
                            callback.spectrum_output = remote_spectrum
                    except Exception as e:
                        print(f"  Remote spectrum reload failed: {e}")
                
                print("Config reloaded from server.")
        finally:
            version_listener.stop_listener()
            if remote_spectrum:
                try:
                    remote_spectrum.stop_thread()
                except Exception:
                    pass
            if os.path.exists(peppy_running_file):
                os.remove(peppy_running_file)
        
        return True
        
    except ImportError as e:
        print(f"ERROR: Could not import PeppyMeter components: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"ERROR: PeppyMeter failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        os.chdir(original_cwd)


# =============================================================================
# Simple Test Display (pygame)
# =============================================================================
def run_test_display(level_receiver):
    """Simple pygame display for testing - shows VU bars."""
    
    # Ensure SDL environment is set for desktop (in case we're falling back after failure)
    os.environ.pop('SDL_FBDEV', None)
    os.environ.pop('SDL_MOUSEDEV', None)
    os.environ.pop('SDL_MOUSEDRV', None)
    os.environ.pop('SDL_NOMOUSE', None)
    os.environ.pop('SDL_VIDEODRIVER', None)  # Remove any driver setting
    if 'DISPLAY' not in os.environ:
        os.environ['DISPLAY'] = ':0'
    
    try:
        import pygame
    except ImportError:
        print("pygame not installed. Install with: pip install pygame")
        return
    
    # Quit pygame if it was partially initialized
    try:
        pygame.quit()
    except:
        pass
    
    pygame.init()
    screen = pygame.display.set_mode((800, 480))
    pygame.display.set_caption("PeppyMeter Remote - Test Mode")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 36)
    font_small = pygame.font.Font(None, 24)
    
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
        
        # Clear screen
        screen.fill((20, 20, 30))
        
        # Get levels
        left, right, mono = level_receiver.get_levels()
        
        # Draw VU bars
        bar_width = 60
        bar_max_height = 300
        bar_y = 100
        
        # Left channel
        left_height = int((left / 100.0) * bar_max_height)
        pygame.draw.rect(screen, (0, 200, 0), 
                        (200, bar_y + bar_max_height - left_height, bar_width, left_height))
        pygame.draw.rect(screen, (100, 100, 100), 
                        (200, bar_y, bar_width, bar_max_height), 2)
        left_text = font_small.render(f"L: {left:.1f}", True, (200, 200, 200))
        screen.blit(left_text, (200, bar_y + bar_max_height + 10))
        
        # Right channel
        right_height = int((right / 100.0) * bar_max_height)
        pygame.draw.rect(screen, (0, 200, 0), 
                        (300, bar_y + bar_max_height - right_height, bar_width, right_height))
        pygame.draw.rect(screen, (100, 100, 100), 
                        (300, bar_y, bar_width, bar_max_height), 2)
        right_text = font_small.render(f"R: {right:.1f}", True, (200, 200, 200))
        screen.blit(right_text, (300, bar_y + bar_max_height + 10))
        
        # Mono channel
        mono_height = int((mono / 100.0) * bar_max_height)
        pygame.draw.rect(screen, (0, 150, 200), 
                        (540, bar_y + bar_max_height - mono_height, bar_width, mono_height))
        pygame.draw.rect(screen, (100, 100, 100), 
                        (540, bar_y, bar_width, bar_max_height), 2)
        mono_text = font_small.render(f"M: {mono:.1f}", True, (200, 200, 200))
        screen.blit(mono_text, (540, bar_y + bar_max_height + 10))
        
        # Title
        title_text = font.render("PeppyMeter Remote - Test Display", True, (255, 255, 255))
        screen.blit(title_text, (50, 20))
        
        # Instructions
        info_text = font_small.render("Press ESC or Q to exit", True, (150, 150, 150))
        screen.blit(info_text, (50, 60))
        
        # Sequence number (for debugging)
        seq_text = font_small.render(f"Seq: {level_receiver.seq}", True, (100, 100, 100))
        screen.blit(seq_text, (650, 450))
        
        pygame.display.flip()
        clock.tick(30)
    
    pygame.quit()


# =============================================================================
# Main
# =============================================================================
def main():
    # Check if we're running with a terminal (for interactive features)
    has_terminal = sys.stdin.isatty() and sys.stdout.isatty()
    
    # Setup logging - to file if no terminal, stdout if terminal
    setup_logging(to_file=not has_terminal)
    
    parser = argparse.ArgumentParser(
        description='PeppyMeter Remote Client',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--config', '-c', action='store_true',
                       help='Run interactive configuration wizard')
    parser.add_argument('--server', '-s', 
                       help='Server hostname or IP (skip discovery)')
    parser.add_argument('--level-port', type=int,
                       help='UDP port for level data')
    parser.add_argument('--spectrum-port', type=int,
                       help='UDP port for spectrum data')
    parser.add_argument('--volumio-port', type=int,
                       help='Volumio socket.io port')
    parser.add_argument('--no-mount', action='store_true',
                       help='Skip SMB mount (use local templates)')
    parser.add_argument('--templates', 
                       help='Path to templates directory (overrides SMB mount)')
    parser.add_argument('--test', action='store_true',
                       help='Run simple test display instead of full PeppyMeter')
    parser.add_argument('--discovery-timeout', type=int,
                       help='Discovery timeout in seconds')
    parser.add_argument('--windowed', action='store_true',
                       help='Run in windowed mode (movable window)')
    parser.add_argument('--fullscreen', action='store_true',
                       help='Run in fullscreen mode')
    
    args = parser.parse_args()
    
    # Run configuration wizard if requested OR on first run (only if we have a terminal)
    run_wizard = args.config
    
    if not run_wizard and has_terminal and is_first_run():
        print("=" * 50)
        print(" Welcome to PeppyMeter Remote Client!")
        print("=" * 50)
        print()
        print("This appears to be your first run.")
        print("Let's configure your settings.")
        print()
        input("Press Enter to continue...")
        run_wizard = True
    elif not run_wizard and not has_terminal and is_first_run():
        # No terminal and first run - just print message and use defaults
        print("First run detected but no terminal available.")
        print("Using auto-discovery with default settings.")
        print(f"Run with --config flag to configure, or edit {CONFIG_FILE}")
    
    if run_wizard:
        if not has_terminal:
            print("ERROR: --config requires a terminal for interactive input.")
            print("Run from a terminal window to configure settings.")
            return
        wizard_config = run_config_wizard()
        if wizard_config is None:
            # User quit without wanting to run
            return
        # wizard_config returned means user chose "Run"
        config = wizard_config
    else:
        # Load config from file
        config = load_config()
    
    # Command-line arguments override config file
    if args.server:
        config["server"]["host"] = args.server
    if args.level_port:
        config["server"]["level_port"] = args.level_port
    if args.spectrum_port:
        config["server"]["spectrum_port"] = args.spectrum_port
    if args.volumio_port:
        config["server"]["volumio_port"] = args.volumio_port
    if args.discovery_timeout:
        config["server"]["discovery_timeout"] = args.discovery_timeout
    if args.windowed:
        config["display"]["windowed"] = True
        config["display"]["fullscreen"] = False
    if args.fullscreen:
        config["display"]["fullscreen"] = True
        config["display"]["windowed"] = False
    if args.no_mount:
        config["templates"]["use_smb"] = False
    if args.templates:
        config["templates"]["local_path"] = args.templates
        config["templates"]["use_smb"] = False
    
    # Store display config in environment for use by display init
    # (This is read by run_peppymeter_display)
    os.environ['PEPPY_DISPLAY_WINDOWED'] = '1' if config["display"]["windowed"] else '0'
    os.environ['PEPPY_DISPLAY_FULLSCREEN'] = '1' if config["display"]["fullscreen"] else '0'
    if config["display"]["position"]:
        os.environ['SDL_VIDEO_WINDOW_POS'] = f"{config['display']['position'][0]},{config['display']['position'][1]}"
    
    # Server discovery or manual specification
    server_info = None
    server_host = config["server"]["host"]
    
    if server_host:
        # Manual server specification
        # Try to resolve hostname to IP
        try:
            ip = socket.gethostbyname(server_host)
        except socket.gaierror:
            # Try with .local suffix
            try:
                ip = socket.gethostbyname(f"{server_host}.local")
            except socket.gaierror:
                ip = server_host  # Assume it's an IP
        
        server_info = {
            'ip': ip,
            'hostname': server_host,
            'level_port': config["server"]["level_port"],
            'spectrum_port': config["server"]["spectrum_port"],
            'volumio_port': config["server"]["volumio_port"]
        }
        print(f"Using server: {server_host} ({ip})")
    else:
        # Auto-discovery
        discovery = ServerDiscovery(
            port=config["server"]["discovery_port"],
            timeout=config["server"]["discovery_timeout"]
        )
        servers = discovery.discover()
        
        if not servers:
            print("No PeppyMeter servers found.")
            print("Use --server <hostname_or_ip> to specify manually.")
            print("Or run --config to configure settings.")
            sys.exit(1)
        elif len(servers) == 1:
            server_info = list(servers.values())[0]
            print(f"Using discovered server: {server_info['hostname']}")
        else:
            # Multiple servers - let user choose
            print("\nMultiple servers found:")
            server_list = list(servers.values())
            for i, srv in enumerate(server_list):
                print(f"  {i+1}. {srv['hostname']} ({srv['ip']})")
            
            while True:
                try:
                    choice = input("\nSelect server (number): ").strip()
                    idx = int(choice) - 1
                    if 0 <= idx < len(server_list):
                        server_info = server_list[idx]
                        break
                except (ValueError, KeyboardInterrupt):
                    print("\nCancelled.")
                    sys.exit(0)
    
    # SMB mount for templates (if enabled)
    smb_mount = None
    if config["templates"]["use_smb"] and not config["templates"]["local_path"]:
        smb_mount = SMBMount(server_info['hostname'])
        if not smb_mount.mount():
            print("WARNING: Could not mount SMB share. Templates may not be available.")
    
    # Config fetcher (uses HTTP to get config from server)
    config_fetcher = ConfigFetcher(server_info['ip'], server_info['volumio_port'])
    
    # Show config version if available from discovery
    if server_info.get('config_version'):
        print(f"Server config version: {server_info['config_version']}")
    
    # Start level receiver with registration for both data streams
    level_receiver = LevelReceiver(
        server_info['ip'], 
        server_info['level_port'],
        subscriptions=['meters', 'spectrum']  # Register interest in both streams
    )
    level_receiver.start()
    
    # Start spectrum receiver (no registration needed, handled by level_receiver)
    spectrum_port = server_info.get('spectrum_port', config['server'].get('spectrum_port', 5581))
    spectrum_receiver = SpectrumReceiver(server_info['ip'], spectrum_port)
    spectrum_receiver.start()
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        print("\nShutting down...")
        level_receiver.stop()
        spectrum_receiver.stop()
        if smb_mount:
            smb_mount.unmount()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Determine templates path
    if config["templates"]["local_path"]:
        templates_path = config["templates"]["local_path"]
    elif smb_mount and smb_mount._mounted:
        templates_path = str(smb_mount.templates_path)
    else:
        templates_path = os.path.join(SCRIPT_DIR, "data", "templates")
    
    # Run display
    if args.test:
        # Simple test display
        run_test_display(level_receiver)
    else:
        # Full PeppyMeter rendering
        success = run_peppymeter_display(level_receiver, server_info, 
                                         templates_path, config_fetcher,
                                         spectrum_receiver=spectrum_receiver)
        if not success:
            print("\nFalling back to test display...")
            run_test_display(level_receiver)
    
    # Cleanup
    level_receiver.stop()
    spectrum_receiver.stop()
    if smb_mount:
        smb_mount.unmount()


if __name__ == '__main__':
    main()
