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
import tempfile
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
        spectrum_local = config["templates"].get("spectrum_local_path") or "(none)"
        print("Template Settings:")
        print(f"  9. Use SMB mount:     {smb}")
        print(f"  10. Local path:       {local}")
        print(f"  11. Spectrum path:    {spectrum_local}")
        print()
        
        # Theme (kiosk / fixed meter)
        mf = config["display"].get("meter_folder") or "(server)"
        mm = config["display"].get("meter") or "(server)"
        print("Meter theme (kiosk):")
        print(f"  12. Theme:            folder={mf}, meter={mm}")
        print()
        
        # Spectrum settings
        decay = config.get("spectrum", {}).get("decay_rate", 0.95)
        print("Spectrum Settings:")
        print(f"  13. Decay rate:       {decay} (0.85=fast, 0.98=slow)")
        print()
        
        # Debug settings
        debug_level = config.get("debug", {}).get("level", "off")
        trace_spectrum = "yes" if config.get("debug", {}).get("trace_spectrum", False) else "no"
        trace_network = "yes" if config.get("debug", {}).get("trace_network", False) else "no"
        print("Debug Settings:")
        print(f"  14. Debug level:      {debug_level}")
        print(f"  15. Trace spectrum:   {trace_spectrum}")
        print(f"  16. Trace network:    {trace_network}")
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
    
    def config_theme():
        print()
        print("Meter theme (kiosk):")
        print("  1. Use server theme (follow Volumio)")
        print("  2. Fixed theme (choose folder + meter)")
        print()
        choice = input("Choice [1]: ").strip() or "1"
        if choice != "2":
            config["display"]["meter_folder"] = None
            config["display"]["meter"] = None
            return
        templates_path = (config["templates"].get("local_path") or "").strip() or os.path.join(SCRIPT_DIR, "data", "templates")
        folders = get_template_folders(templates_path)
        if not folders:
            print("No template folders found (need meters.txt in each folder). Using server theme.")
            config["display"]["meter_folder"] = None
            config["display"]["meter"] = None
            return
        print("Template folders:")
        for i, name in enumerate(folders, 1):
            print(f"  {i}. {name}")
        fchoice = input(f"Folder [1]: ").strip() or "1"
        try:
            idx = int(fchoice)
            folder = folders[idx - 1] if 1 <= idx <= len(folders) else folders[0]
        except ValueError:
            folder = fchoice if fchoice in folders else folders[0]
        config["display"]["meter_folder"] = folder
        print()
        print("Meter mode:")
        print("  1. Fixed (one meter)")
        print("  2. Random from folder")
        print("  3. Random from list (pick several)")
        print()
        mchoice = input("Choice [1]: ").strip() or "1"
        sections = get_meter_sections(templates_path, folder)
        if mchoice == "2":
            config["display"]["meter"] = "random"
        elif mchoice == "3" and sections:
            print("Meters in folder:")
            for i, name in enumerate(sections, 1):
                print(f"  {i}. {name}")
            sel = input("Numbers to include (e.g. 1,3,5 or names comma-separated): ").strip()
            if sel:
                parts = [p.strip() for p in sel.split(",")]
                chosen = []
                for p in parts:
                    if p.isdigit() and 1 <= int(p) <= len(sections):
                        chosen.append(sections[int(p) - 1])
                    elif p in sections:
                        chosen.append(p)
                config["display"]["meter"] = ",".join(chosen) if chosen else "random"
            else:
                config["display"]["meter"] = "random"
        else:
            if not sections:
                config["display"]["meter"] = "random"
            else:
                print("Meters in folder:")
                for i, name in enumerate(sections, 1):
                    print(f"  {i}. {name}")
                schoice = input(f"Meter [1]: ").strip() or "1"
                try:
                    idx = int(schoice)
                    config["display"]["meter"] = sections[idx - 1] if 1 <= idx <= len(sections) else sections[0]
                except ValueError:
                    config["display"]["meter"] = schoice if schoice in sections else sections[0]
    
    def config_decay_rate():
        print()
        print("Spectrum decay rate (per frame):")
        print("  Controls how fast spectrum bars fall after peaks")
        print("  0.85 = fast drop (aggressive)")
        print("  0.95 = medium drop (default)")
        print("  0.98 = slow drop (smooth)")
        print()
        current = config.get("spectrum", {}).get("decay_rate", 0.95)
        try:
            value = float(get_input("Decay rate (0.5-0.99)", current))
            value = max(0.5, min(0.99, value))  # Clamp to valid range
            if "spectrum" not in config:
                config["spectrum"] = {}
            config["spectrum"]["decay_rate"] = value
        except ValueError:
            print("Invalid number")
    
    def config_debug_level():
        print()
        print("Debug output level:")
        print("  1. off     - No debug output")
        print("  2. basic   - Key events (discovery, config changes)")
        print("  3. verbose - Detailed operations")
        print("  4. trace   - Per-packet data (high volume)")
        print()
        levels = {"1": "off", "2": "basic", "3": "verbose", "4": "trace"}
        current = config.get("debug", {}).get("level", "off")
        current_num = {"off": "1", "basic": "2", "verbose": "3", "trace": "4"}.get(current, "1")
        choice = input(f"Choice [{current_num}]: ").strip() or current_num
        if choice in levels:
            if "debug" not in config:
                config["debug"] = {}
            config["debug"]["level"] = levels[choice]
    
    def config_trace_toggle(key, name):
        print()
        current = config.get("debug", {}).get(key, False)
        current_str = "yes" if current else "no"
        print(f"{name}:")
        print(f"  1. No")
        print(f"  2. Yes")
        print()
        choice = input(f"Choice [{'2' if current else '1'}]: ").strip() or ('2' if current else '1')
        if "debug" not in config:
            config["debug"] = {}
        config["debug"][key] = (choice == "2")
    
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
        elif choice == "11":
            path = input("Spectrum templates path (empty to clear): ").strip()
            config["templates"]["spectrum_local_path"] = path if path else None
        elif choice == "12":
            config_theme()
        elif choice == "13":
            config_decay_rate()
        elif choice == "14":
            config_debug_level()
        elif choice == "15":
            config_trace_toggle("trace_spectrum", "Trace spectrum packets")
        elif choice == "16":
            config_trace_toggle("trace_network", "Trace network connections")
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


def can_show_wizard_ui():
    """Return True if a graphical wizard can be shown (DISPLAY set and tkinter available)."""
    if os.name == 'nt':
        # Windows: assume GUI available
        pass
    else:
        if not os.environ.get('DISPLAY'):
            return False
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def run_wizard_ui(initial_config=None):
    """
    Graphical setup wizard for initial install and configuration.
    Uses tkinter (no extra dependency on most Linux desktops: python3-tk).
    Returns config dict on "Save & Run", None on cancel or "Save & Exit".
    """
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog
    except ImportError:
        return None

    config = json.loads(json.dumps(initial_config or DEFAULT_CONFIG))
    result = {'config': None, 'run_after': False}

    root = tk.Tk()
    root.title("PeppyMeter Remote — Setup")
    root.resizable(True, True)
    root.minsize(440, 480)
    root.geometry("500x540")

    # Centered on screen
    root.update_idletasks()
    w, h = 500, 540
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")

    main_frame = ttk.Frame(root, padding=20)
    main_frame.pack(fill=tk.BOTH, expand=True)

    # Labels that should word-wrap; wraplength updated on resize so text follows window width
    wrap_labels = []

    def _wrap_label(parent, **kwargs):
        w = kwargs.pop('wraplength', 440)
        lbl = ttk.Label(parent, wraplength=w, **kwargs)
        wrap_labels.append(lbl)
        return lbl

    steps = []
    current_step = [0]  # use list so inner funcs can mutate

    # ----- Step 0: Welcome -----
    welcome_frame = ttk.Frame(main_frame)
    ttk.Label(welcome_frame, text="Welcome to PeppyMeter Remote", font=('', 14, 'bold')).pack(pady=(0, 12))
    _wrap_label(welcome_frame, text="This wizard will help you connect this display to your Volumio PeppyMeter server and choose display options.", justify=tk.CENTER).pack(pady=(0, 20))
    _wrap_label(welcome_frame, text="You’ll need your Volumio device powered on and on the same network.", font=('', 9)).pack(pady=(0, 8))
    steps.append(welcome_frame)

    # ----- Step 1: Server -----
    server_frame = ttk.Frame(main_frame)
    ttk.Label(server_frame, text="Choose your Volumio server", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    # Server mode: "auto" | "hostname" | "ip"
    current_host = config["server"]["host"] or ""
    def _looks_like_ip(s):
        if not s:
            return False
        parts = s.strip().split(".")
        return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    if not current_host:
        server_mode_var = tk.StringVar(value="auto")
    elif _looks_like_ip(current_host):
        server_mode_var = tk.StringVar(value="ip")
    else:
        server_mode_var = tk.StringVar(value="hostname")
    hostname_var = tk.StringVar(value=current_host if not _looks_like_ip(current_host) else "")
    ip_var = tk.StringVar(value=current_host if _looks_like_ip(current_host) else "")
    use_hostname_var = tk.BooleanVar(value=True)  # When auto + selection: True = use hostname, False = use IP
    discovered = []  # list of dicts from ServerDiscovery

    def _fill_servers_list(servers_dict):
        lb.delete(0, tk.END)
        discovered.clear()
        for s in servers_dict.values():
            discovered.append(s)
            lb.insert(tk.END, f"{s.get('hostname', s['ip'])} ({s['ip']})")
        if not discovered:
            lb.insert(tk.END, "No servers found — try manual entry")
        _update_discovery_choice_visibility()

    def _update_discovery_choice_visibility():
        sel = lb.curselection()
        has_selection = sel and discovered and 0 <= sel[0] < len(discovered)
        if has_selection and server_mode_var.get() == "auto":
            discovery_choice_frame.pack(anchor=tk.W, fill=tk.X, pady=(6, 0))
        else:
            discovery_choice_frame.pack_forget()

    def _on_server_mode_change():
        mode = server_mode_var.get()
        ent_hostname.config(state=tk.NORMAL if mode == "hostname" else tk.DISABLED)
        ent_ip.config(state=tk.NORMAL if mode == "ip" else tk.DISABLED)
        if mode != "auto":
            discovery_choice_frame.pack_forget()
        else:
            _update_discovery_choice_visibility()

    def discover_servers():
        server_mode_var.set("auto")
        _on_server_mode_change()
        lb.delete(0, tk.END)
        lb.insert(tk.END, "Searching...")
        root.update_idletasks()

        def do_discovery():
            discovery = ServerDiscovery(port=config["server"]["discovery_port"], timeout=config["server"]["discovery_timeout"])
            servers = discovery.discover()
            root.after(0, lambda: _fill_servers_list(servers))

        t = threading.Thread(target=do_discovery, daemon=True)
        t.start()

    rb_auto = ttk.Radiobutton(server_frame, text="Auto-discover server (recommended)", variable=server_mode_var, value="auto",
                             command=_on_server_mode_change)
    rb_auto.pack(anchor=tk.W)
    listbox_frame = ttk.Frame(server_frame)
    listbox_frame.pack(fill=tk.BOTH, expand=True, pady=8)
    lb = tk.Listbox(listbox_frame, height=4, selectmode=tk.SINGLE, font=('', 10))
    lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll = ttk.Scrollbar(listbox_frame, orient=tk.VERTICAL, command=lb.yview)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    lb.config(yscrollcommand=scroll.set)

    def on_select_server(evt):
        sel = lb.curselection()
        if sel and discovered and 0 <= sel[0] < len(discovered):
            s = discovered[sel[0]]
            hostname_var.set(s.get("hostname", s["ip"]) or "")
            ip_var.set(s.get("ip", "") or "")
        _update_discovery_choice_visibility()

    lb.bind('<<ListboxSelect>>', on_select_server)

    # When discovery has populated the list and user selects one: Use hostname / Use IP address
    discovery_choice_frame = ttk.Frame(server_frame)
    ttk.Radiobutton(discovery_choice_frame, text="Use hostname", variable=use_hostname_var, value=True).pack(side=tk.LEFT, padx=(0, 16))
    ttk.Radiobutton(discovery_choice_frame, text="Use IP address", variable=use_hostname_var, value=False).pack(side=tk.LEFT)

    btn_discover = ttk.Button(server_frame, text="Discover servers",
                             command=lambda: (server_mode_var.set("auto"), _on_server_mode_change(), discover_servers()))
    btn_discover.pack(anchor=tk.W, pady=4)

    ttk.Radiobutton(server_frame, text="Enter hostname", variable=server_mode_var, value="hostname", command=_on_server_mode_change).pack(anchor=tk.W, pady=(8, 0))
    ent_hostname = ttk.Entry(server_frame, textvariable=hostname_var, width=36)
    ent_hostname.pack(anchor=tk.W, fill=tk.X, pady=2)
    ttk.Radiobutton(server_frame, text="Enter IP address", variable=server_mode_var, value="ip", command=_on_server_mode_change).pack(anchor=tk.W, pady=(8, 0))
    ent_ip = ttk.Entry(server_frame, textvariable=ip_var, width=36)
    ent_ip.pack(anchor=tk.W, fill=tk.X, pady=2)

    if server_mode_var.get() == "hostname":
        ent_hostname.config(state=tk.NORMAL)
        ent_ip.config(state=tk.DISABLED)
    elif server_mode_var.get() == "ip":
        ent_hostname.config(state=tk.DISABLED)
        ent_ip.config(state=tk.NORMAL)
    else:
        ent_hostname.config(state=tk.DISABLED)
        ent_ip.config(state=tk.DISABLED)
    steps.append(server_frame)

    # ----- Step 2: Display -----
    display_frame = ttk.Frame(main_frame)
    ttk.Label(display_frame, text="Display mode", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    display_mode = tk.StringVar(value="windowed" if config["display"]["windowed"] else ("fullscreen" if config["display"]["fullscreen"] else "frameless"))
    ttk.Radiobutton(display_frame, text="Windowed (movable window with title bar)", variable=display_mode, value="windowed").pack(anchor=tk.W)
    ttk.Radiobutton(display_frame, text="Fullscreen", variable=display_mode, value="fullscreen").pack(anchor=tk.W)
    steps.append(display_frame)

    # ----- Step 3: Templates -----
    templates_frame = ttk.Frame(main_frame)
    ttk.Label(templates_frame, text="Template & spectrum theme sources", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    use_smb_var = tk.BooleanVar(value=config["templates"]["use_smb"])
    ttk.Radiobutton(templates_frame, text="SMB mount from Volumio server (recommended)", variable=use_smb_var, value=True).pack(anchor=tk.W)
    ttk.Radiobutton(templates_frame, text="Use local template paths", variable=use_smb_var, value=False).pack(anchor=tk.W)
    _wrap_label(templates_frame, text="When using local paths, meter and spectrum themes must be placed in these folders on this machine.", font=('', 9), foreground='#555').pack(anchor=tk.W, pady=(4, 8))
    _wrap_label(templates_frame, text="Meter templates path (leave empty if using SMB):", font=('', 9)).pack(anchor=tk.W, pady=(0, 0))
    local_path_var = tk.StringVar(value=config["templates"]["local_path"] or "")
    local_path_row = ttk.Frame(templates_frame)
    local_path_row.pack(anchor=tk.W, fill=tk.X, pady=2)
    ent_local_path = ttk.Entry(local_path_row, textvariable=local_path_var, width=40)
    ent_local_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
    def browse_local_path():
        path = filedialog.askdirectory(parent=root, title="Choose meter templates folder", initialdir=local_path_var.get() or None)
        if path:
            local_path_var.set(path)
    ttk.Button(local_path_row, text="Browse…", width=8, command=browse_local_path).pack(side=tk.LEFT)
    _wrap_label(templates_frame, text="Spectrum templates path (leave empty if using SMB):", font=('', 9)).pack(anchor=tk.W, pady=(6, 0))
    spectrum_local_var = tk.StringVar(value=config["templates"].get("spectrum_local_path") or "")
    spectrum_path_row = ttk.Frame(templates_frame)
    spectrum_path_row.pack(anchor=tk.W, fill=tk.X, pady=2)
    ent_spectrum_local = ttk.Entry(spectrum_path_row, textvariable=spectrum_local_var, width=40)
    ent_spectrum_local.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
    def browse_spectrum_path():
        path = filedialog.askdirectory(parent=root, title="Choose spectrum templates folder", initialdir=spectrum_local_var.get() or local_path_var.get() or None)
        if path:
            spectrum_local_var.set(path)
    ttk.Button(spectrum_path_row, text="Browse…", width=8, command=browse_spectrum_path).pack(side=tk.LEFT)
    steps.append(templates_frame)

    # ----- Step 4: Theme (kiosk / fixed meter) -----
    theme_frame = ttk.Frame(main_frame)
    ttk.Label(theme_frame, text="Meter theme", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    _wrap_label(theme_frame, text="Use server theme (follow Volumio) or lock this client to a fixed theme (kiosk).", font=('', 9)).pack(anchor=tk.W, pady=(0, 6))
    theme_mode_var = tk.StringVar(value="server")
    _mf, _mm = config["display"].get("meter_folder"), config["display"].get("meter")
    if _mf and _mm:
        if _mm.strip().lower() == "random":
            theme_mode_var.set("random_folder")
        elif "," in (_mm or ""):
            theme_mode_var.set("random_list")
        else:
            theme_mode_var.set("fixed")
    ttk.Radiobutton(theme_frame, text="Use server theme", variable=theme_mode_var, value="server").pack(anchor=tk.W)
    ttk.Radiobutton(theme_frame, text="Fixed theme (one meter)", variable=theme_mode_var, value="fixed").pack(anchor=tk.W)
    ttk.Radiobutton(theme_frame, text="Random from folder", variable=theme_mode_var, value="random_folder").pack(anchor=tk.W)
    ttk.Radiobutton(theme_frame, text="Random from list", variable=theme_mode_var, value="random_list").pack(anchor=tk.W)
    theme_folder_var = tk.StringVar(value=config["display"].get("meter_folder") or "")
    theme_meter_var = tk.StringVar(value=config["display"].get("meter") or "")
    theme_meter_list_var = tk.Variable(value=[])
    def _theme_templates_path():
        return (local_path_var.get() or "").strip() or os.path.join(SCRIPT_DIR, "data", "templates")
    def _refresh_theme_folders():
        path = _theme_templates_path()
        folders = get_template_folders(path)
        folder_combo["values"] = folders
        if folders and not theme_folder_var.get() and theme_mode_var.get() != "server":
            theme_folder_var.set(folders[0])
        _refresh_theme_meters()
    def _refresh_theme_meters():
        folder = (theme_folder_var.get() or "").strip()
        path = _theme_templates_path()
        sections = get_meter_sections(path, folder) if folder else []
        meter_combo["values"] = sections
        meter_listbox.delete(0, tk.END)
        for s in sections:
            meter_listbox.insert(tk.END, s)
        if sections and theme_mode_var.get() == "fixed" and not theme_meter_var.get():
            theme_meter_var.set(sections[0])
    def _on_theme_folder_change(*_):
        _refresh_theme_meters()
    theme_folder_var.trace_add("write", _on_theme_folder_change)
    ttk.Label(theme_frame, text="Template folder:", font=('', 9)).pack(anchor=tk.W, pady=(10, 0))
    folder_combo = ttk.Combobox(theme_frame, textvariable=theme_folder_var, width=36, state="readonly")
    folder_combo.pack(anchor=tk.W, fill=tk.X, pady=2)
    ttk.Label(theme_frame, text="Meter (fixed) or list (random from list):", font=('', 9)).pack(anchor=tk.W, pady=(8, 0))
    meter_row = ttk.Frame(theme_frame)
    meter_row.pack(anchor=tk.W, fill=tk.X, pady=2)
    meter_combo = ttk.Combobox(meter_row, textvariable=theme_meter_var, width=24, state="readonly")
    meter_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
    meter_listbox = tk.Listbox(meter_row, selectmode=tk.MULTIPLE, height=4, width=24)
    meter_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    _refresh_theme_folders()
    steps.append(theme_frame)

    # ----- Step 5: Spectrum -----
    spectrum_frame = ttk.Frame(main_frame)
    ttk.Label(spectrum_frame, text="Spectrum settings", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    decay = config.get("spectrum", {}).get("decay_rate", 0.95)
    _wrap_label(spectrum_frame, text="Decay rate (how fast bars fall after peaks): 0.85 = fast, 0.98 = slow", font=('', 9)).pack(anchor=tk.W)
    decay_var = tk.StringVar(value=str(decay))
    decay_spin = ttk.Spinbox(spectrum_frame, textvariable=decay_var, from_=0.5, to=0.99, increment=0.01, width=8)
    decay_spin.pack(anchor=tk.W, pady=4)
    steps.append(spectrum_frame)

    # ----- Step 6: Logger / Debug -----
    debug_frame = ttk.Frame(main_frame)
    ttk.Label(debug_frame, text="Logger / debug", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    debug_level_var = tk.StringVar(value=config.get("debug", {}).get("level", "off"))
    ttk.Label(debug_frame, text="Debug level:", font=('', 9)).pack(anchor=tk.W)
    debug_combo = ttk.Combobox(debug_frame, textvariable=debug_level_var, values=("off", "basic", "verbose", "trace"), state="readonly", width=12)
    debug_combo.pack(anchor=tk.W, pady=2)
    trace_spectrum_var = tk.BooleanVar(value=config.get("debug", {}).get("trace_spectrum", False))
    trace_network_var = tk.BooleanVar(value=config.get("debug", {}).get("trace_network", False))
    ttk.Checkbutton(debug_frame, text="Trace spectrum packets", variable=trace_spectrum_var).pack(anchor=tk.W, pady=2)
    ttk.Checkbutton(debug_frame, text="Trace network connections", variable=trace_network_var).pack(anchor=tk.W, pady=2)
    steps.append(debug_frame)

    # ----- Step 7: Done -----
    done_frame = ttk.Frame(main_frame)
    ttk.Label(done_frame, text="You’re all set", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    _wrap_label(done_frame, text="Save your settings and choose whether to start the client now.", justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 16))
    steps.append(done_frame)

    def _update_wraplength(_evt=None):
        w = main_frame.winfo_width()
        if w > 40:
            for lbl in wrap_labels:
                lbl.configure(wraplength=max(80, w - 40))

    main_frame.bind('<Configure>', _update_wraplength)

    def show_step(i):
        for j, f in enumerate(steps):
            if j == i:
                f.pack(fill=tk.BOTH, expand=True)
            else:
                f.pack_forget()

    def apply_server():
        mode = server_mode_var.get()
        if mode == "hostname":
            config["server"]["host"] = (hostname_var.get() or "").strip() or None
        elif mode == "ip":
            config["server"]["host"] = (ip_var.get() or "").strip() or None
        else:
            sel = lb.curselection()
            if sel and discovered and 0 <= sel[0] < len(discovered):
                s = discovered[sel[0]]
                config["server"]["host"] = s.get("hostname", s["ip"]) if use_hostname_var.get() else s["ip"]
            else:
                config["server"]["host"] = None

    def apply_display():
        config["display"]["windowed"] = (display_mode.get() == "windowed")
        config["display"]["fullscreen"] = (display_mode.get() == "fullscreen")

    def apply_templates():
        config["templates"]["use_smb"] = use_smb_var.get()
        lp = (local_path_var.get() or "").strip()
        config["templates"]["local_path"] = lp if lp else None
        sp = (spectrum_local_var.get() or "").strip()
        config["templates"]["spectrum_local_path"] = sp if sp else None

    def apply_theme():
        mode = theme_mode_var.get()
        if mode == "server":
            config["display"]["meter_folder"] = None
            config["display"]["meter"] = None
        else:
            folder = (theme_folder_var.get() or "").strip()
            config["display"]["meter_folder"] = folder if folder else None
            if mode == "random_folder":
                config["display"]["meter"] = "random"
            elif mode == "fixed":
                config["display"]["meter"] = (theme_meter_var.get() or "").strip() or None
            else:
                sel = meter_listbox.curselection()
                sections = [meter_listbox.get(i) for i in sel] if sel else []
                config["display"]["meter"] = ",".join(sections) if sections else None

    def apply_spectrum():
        try:
            v = float(decay_var.get())
            v = max(0.5, min(0.99, v))
            if "spectrum" not in config:
                config["spectrum"] = {}
            config["spectrum"]["decay_rate"] = v
            decay_var.set(str(v))
        except (ValueError, TypeError):
            if "spectrum" not in config:
                config["spectrum"] = {}
            config["spectrum"]["decay_rate"] = 0.95
            decay_var.set("0.95")

    def apply_debug():
        if "debug" not in config:
            config["debug"] = {}
        config["debug"]["level"] = debug_level_var.get() or "off"
        config["debug"]["trace_spectrum"] = trace_spectrum_var.get()
        config["debug"]["trace_network"] = trace_network_var.get()

    def next_click():
        if current_step[0] == 0:
            show_step(1)
            current_step[0] = 1
            discover_servers()
        elif current_step[0] == 1:
            apply_server()
            show_step(2)
            current_step[0] = 2
        elif current_step[0] == 2:
            apply_display()
            show_step(3)
            current_step[0] = 3
        elif current_step[0] == 3:
            apply_templates()
            show_step(4)
            current_step[0] = 4
            _refresh_theme_folders()
        elif current_step[0] == 4:
            apply_theme()
            show_step(5)
            current_step[0] = 5
        elif current_step[0] == 5:
            apply_spectrum()
            show_step(6)
            current_step[0] = 6
        elif current_step[0] == 6:
            apply_debug()
            show_step(7)
            current_step[0] = 7
            btn_next.pack_forget()
            btn_back.pack(side=tk.RIGHT, padx=4)
            btn_save_run.pack(side=tk.RIGHT, padx=4)
            btn_save_exit.pack(side=tk.RIGHT, padx=4)

    def back_click():
        if current_step[0] == 1:
            show_step(0)
            current_step[0] = 0
        elif current_step[0] == 2:
            show_step(1)
            current_step[0] = 1
        elif current_step[0] == 3:
            show_step(2)
            current_step[0] = 2
        elif current_step[0] == 4:
            show_step(3)
            current_step[0] = 3
        elif current_step[0] == 5:
            show_step(4)
            current_step[0] = 4
        elif current_step[0] == 6:
            show_step(5)
            current_step[0] = 5
        elif current_step[0] == 7:
            show_step(6)
            current_step[0] = 6
            btn_save_run.pack_forget()
            btn_save_exit.pack_forget()
            btn_back.pack_forget()
            btn_next.pack(side=tk.RIGHT, padx=4)

    def save_and_run():
        apply_server()
        apply_display()
        apply_templates()
        apply_theme()
        apply_spectrum()
        apply_debug()
        config["wizard_completed"] = True
        save_config(config)
        result['config'] = config
        result['run_after'] = True
        root.quit()
        root.destroy()

    def save_and_exit():
        apply_server()
        apply_display()
        apply_templates()
        apply_theme()
        apply_spectrum()
        apply_debug()
        config["wizard_completed"] = True
        save_config(config)
        result['config'] = config
        result['run_after'] = False
        root.quit()
        root.destroy()

    def cancel_click():
        result['config'] = None
        root.quit()
        root.destroy()

    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill=tk.X, pady=(16, 0))
    btn_cancel = ttk.Button(btn_frame, text="Cancel", command=cancel_click)
    btn_cancel.pack(side=tk.LEFT)
    btn_back = ttk.Button(btn_frame, text="Back", command=back_click)
    btn_save_exit = ttk.Button(btn_frame, text="Save & Exit", command=save_and_exit)
    btn_save_run = ttk.Button(btn_frame, text="Save & Run", command=save_and_run)
    btn_next = ttk.Button(btn_frame, text="Next", command=next_click)
    btn_next.pack(side=tk.RIGHT, padx=4)

    show_step(0)
    root.protocol("WM_DELETE_WINDOW", cancel_click)
    root.mainloop()

    if result['config'] is not None and result['run_after']:
        return result['config']
    return None


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
        log_client(f"Discovery: listening on UDP port {self.port}, timeout={self.timeout}s", "verbose")
        
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
                            log_client(f"Discovery: found server {hostname} ({ip})", "basic")
                            log_client(f"Discovery: server info: {info}", "verbose")
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
                                log_client(f"Discovery: config version updated to {new_version}", "verbose")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    log_client(f"Discovery: invalid packet from {addr}", "trace", "network")
                    pass
            except socket.timeout:
                continue
            except Exception as e:
                print(f"  Discovery error: {e}")
                log_client(f"Discovery: error {e}", "verbose")
                break
        
        sock.close()
        log_client(f"Discovery: found {len(self.servers)} servers", "verbose")
        return self.servers
    
    def stop(self):
        self._stop = True


# =============================================================================
# Config Version Listener (UDP) - detect server config/template changes
# =============================================================================
class ConfigVersionListener(threading.Thread):
    """
    Listens for UDP discovery packets from the PeppyMeter server and sets
    reload_requested when config_version or active_meter in a packet differs
    from the client's current values (so the client can reload config/templates).
    
    Protocol version 3+ includes active_meter for random meter sync.
    """
    def __init__(self, port, current_version_holder, server_ip=None):
        super().__init__(daemon=True)
        self.port = port
        self.current_version_holder = current_version_holder  # dict with 'version' and 'active_meter' keys
        self.server_ip = server_ip  # if set, only accept packets from this IP
        self.reload_requested = False
        self.new_active_meter = None  # Set when active_meter changes (for config update)
        self.first_announcement_received = False  # True after first valid UDP packet (for sync screen)
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
                    
                    # Mark that we've received at least one announcement (for "waiting for server" screen)
                    self.first_announcement_received = True
                    if info.get('active_meter'):
                        self.new_active_meter = info.get('active_meter')
                    
                    # Check config_version change (file-based config changes)
                    new_version = info.get('config_version', '')
                    if new_version:
                        current = self.current_version_holder.get('version', '')
                        if new_version != current:
                            self.reload_requested = True
                    
                    # Check active_meter change (random meter sync, protocol v3+)
                    new_meter = info.get('active_meter', '')
                    if new_meter:
                        current_meter = self.current_version_holder.get('active_meter', '')
                        if new_meter != current_meter:
                            # Active meter changed - trigger reload with new meter name
                            self.new_active_meter = new_meter
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
        # Persist countdown settings from server
        self.persist_duration = 0  # 0 = disabled
        self.persist_display = "freeze"
    
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
                        # Extract persist settings from server
                        self.persist_duration = int(inner.get('persist_duration', 0) or 0)
                        self.persist_display = inner.get('persist_display', 'freeze') or 'freeze'
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


# =============================================================================
# Persist Manager - manages /tmp/peppy_persist for countdown display
# =============================================================================
PERSIST_FILE = '/tmp/peppy_persist'

class PersistManager:
    """
    Manages the persist countdown file for remote clients.
    
    On the server, Node.js creates /tmp/peppy_persist when playback stops
    with persist mode enabled. The Python render code (volumio_turntable.py)
    reads this file to show countdown.
    
    On the client, there is no Node.js - so we must manage the file ourselves,
    mirroring the server's behavior based on socket.io playback status events.
    
    Usage: Call check_metadata_status() on each render frame to monitor changes.
    """
    
    def __init__(self, persist_duration=0, persist_display="freeze"):
        """
        :param persist_duration: Countdown duration in seconds (0 = disabled)
        :param persist_display: Display mode ("freeze" or "countdown")
        """
        self.persist_duration = persist_duration
        self.persist_display = persist_display
        self._persist_timer = None
        self._last_status = None
    
    def update_settings(self, persist_duration, persist_display):
        """Update persist settings (e.g., when server config changes)."""
        new_duration = int(persist_duration or 0)
        new_display = persist_display or "freeze"
        
        # Only log if settings actually changed
        if new_duration != self.persist_duration or new_display != self.persist_display:
            self.persist_duration = new_duration
            self.persist_display = new_display
            log_client(f"Persist settings updated: duration={self.persist_duration}s, mode={self.persist_display}", "verbose")
    
    def check_metadata_status(self, metadata_dict):
        """
        Check metadata dict for status changes and handle persist file accordingly.
        
        Call this on each render frame to detect status changes.
        
        :param metadata_dict: Shared metadata dict with 'status' and 'volatile' keys
        """
        status = (metadata_dict.get("status", "") or "").lower()
        volatile = metadata_dict.get("volatile", False) or False
        
        if status != self._last_status:
            self._on_status_change(status, volatile)
    
    def _on_status_change(self, status, volatile=False):
        """
        Handle playback status changes.
        
        Mirrors the server's Node.js logic:
        - On 'play': Remove persist file and cancel timer
        - On 'stop'/'pause' (non-volatile): Start persist countdown if enabled
        
        :param status: Playback status ("play", "pause", "stop")
        :param volatile: If True, status change is transitional (skip processing)
        """
        # Ignore volatile state changes (track transitions)
        if volatile and status in ("stop", "pause"):
            return
        
        if status == "play":
            # Playback resumed - remove persist file and cancel timer
            self._cancel_timer()
            self._remove_persist_file()
            log_client("Persist: playback resumed, file removed", "trace", "persist")
        
        elif status in ("stop", "pause") and self._last_status == "play":
            # Playback stopped/paused - start persist countdown if enabled
            if self.persist_duration > 0:
                self._start_persist_countdown()
            else:
                self._remove_persist_file()
        
        self._last_status = status
    
    def _start_persist_countdown(self):
        """Create persist file and start expiration timer."""
        import time as time_module
        import threading
        
        # Cancel any existing timer
        self._cancel_timer()
        
        # Write persist file (format: duration:timestamp_ms:display_mode)
        timestamp_ms = int(time_module.time() * 1000)
        content = f"{self.persist_duration}:{timestamp_ms}:{self.persist_display}"
        
        try:
            with open(PERSIST_FILE, 'w') as f:
                f.write(content)
            log_client(f"Persist: started countdown ({self.persist_duration}s, mode={self.persist_display})", "verbose")
        except Exception as e:
            log_client(f"Persist: failed to write file: {e}", "debug")
            return
        
        # Start timer to remove file after persist_duration
        def expire_persist():
            self._remove_persist_file()
            log_client("Persist: timer expired, file removed", "verbose")
            self._persist_timer = None
        
        self._persist_timer = threading.Timer(self.persist_duration, expire_persist)
        self._persist_timer.daemon = True
        self._persist_timer.start()
    
    def _cancel_timer(self):
        """Cancel persist expiration timer if running."""
        if self._persist_timer:
            self._persist_timer.cancel()
            self._persist_timer = None
    
    def _remove_persist_file(self):
        """Remove persist file if it exists."""
        try:
            if os.path.exists(PERSIST_FILE):
                os.remove(PERSIST_FILE)
        except Exception:
            pass
    
    def cleanup(self):
        """Cleanup on shutdown - cancel timer and remove file."""
        self._cancel_timer()
        self._remove_persist_file()


def _is_ip_address(host):
    """Return True if host is an IPv4 or IPv6 address, False for a hostname."""
    try:
        import ipaddress
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


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
    for filename in KNOWN_PEPPY_FONTS:
        local_path = os.path.join(fonts_dir, filename)
        if not os.path.exists(local_path):
            if not _fetch_font(filename, fonts_dir, server_ip, volumio_port):
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
            data = json.loads(response.read().decode())
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
        # Handle stale mount points - unmount first if stale
        self._cleanup_stale_mount()
        
        # Create mount point (handle stale file handles gracefully)
        try:
            self.mount_point.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # Stale file handle (errno 116) or other issues - try to recover
            if e.errno == 116:  # ESTALE - Stale file handle
                print(f"Stale mount detected at {self.mount_point}, cleaning up...")
                self._force_unmount()
                # Remove stale directory and recreate
                try:
                    subprocess.run(['sudo', 'rm', '-rf', str(self.mount_point)],
                                 capture_output=True, timeout=5)
                except Exception:
                    pass
                self.mount_point.mkdir(parents=True, exist_ok=True)
            else:
                raise
        
        # Check if already mounted
        if self._is_mounted():
            print(f"SMB share already mounted at {self.mount_point}")
            self._mounted = True
            return True
        
        # Try SMB versions from oldest/fastest to newest (Linux cifs 3.x can be slow)
        # Order: 2.0, 2.1, 3.0, 3.1.1
        vers_list = ['2.0', '2.1', '3.0', '3.1.1']
        for vers in vers_list:
            opts_guest = f'guest,ro,nofail,vers={vers}'
            opts_creds = f'user=volumio,password=volumio,ro,nofail,vers={vers}'
            print(f"Mounting {self.share_path} at {self.mount_point} (SMB {vers})...")
            result = subprocess.run(
                ['sudo', 'mount', '-t', 'cifs', self.share_path, str(self.mount_point),
                 '-o', opts_guest],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"  Mounted as guest (SMB {vers})")
                self._mounted = True
                return True
            result = subprocess.run(
                ['sudo', 'mount', '-t', 'cifs', self.share_path, str(self.mount_point),
                 '-o', opts_creds],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"  Mounted with volumio credentials (SMB {vers})")
                self._mounted = True
                return True
        print(f"  Failed to mount (tried SMB {', '.join(vers_list)}): {result.stderr}")
        return False
    
    def unmount(self):
        """Unmount the SMB share."""
        if self._mounted and self._is_mounted():
            subprocess.run(['sudo', 'umount', str(self.mount_point)], 
                         capture_output=True)
            self._mounted = False
    
    def _is_mounted(self):
        """Check if the mount point is currently mounted."""
        try:
            result = subprocess.run(['mountpoint', '-q', str(self.mount_point)], timeout=5)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            # Timeout usually means stale mount
            return False
        except Exception:
            return False
    
    def _cleanup_stale_mount(self):
        """Clean up any stale mounts at the mount point."""
        try:
            # Check if mount point exists and might be stale
            if self.mount_point.exists():
                # Try to access it - stale mounts will fail
                try:
                    list(self.mount_point.iterdir())
                except OSError as e:
                    if e.errno == 116:  # ESTALE
                        print(f"Cleaning up stale mount at {self.mount_point}...")
                        self._force_unmount()
        except Exception:
            pass
    
    def _force_unmount(self):
        """Force unmount the mount point."""
        try:
            # Try lazy unmount first
            subprocess.run(['sudo', 'umount', '-l', str(self.mount_point)],
                         capture_output=True, timeout=10)
        except Exception:
            pass
        try:
            # Force unmount as fallback
            subprocess.run(['sudo', 'umount', '-f', str(self.mount_point)],
                         capture_output=True, timeout=10)
        except Exception:
            pass
    
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
                            log_client(f"Spectrum: first packet from {source_ip}, {size} bins", "basic")
                            self._first_packet_logged = True
                        
                        # Trace log each packet (high volume - only when trace_spectrum enabled)
                        log_client(f"Spectrum: seq={seq}, bins={size}, max={max(bins):.1f}", 
                                   "trace", "spectrum")
                            
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
    
    def __init__(self, util, meter_config_volumio, screensaver_path, spectrum_receiver, 
                 decay_rate=0.95, spectrum_templates_path=None):
        """Initialize remote spectrum output.
        
        :param util: PeppyMeter utility class
        :param meter_config_volumio: Volumio meter configuration
        :param screensaver_path: Path to screensaver directory (contains 'spectrum' subfolder)
        :param spectrum_receiver: SpectrumReceiver instance for network data
        :param decay_rate: Per-frame decay multiplier (0.85=fast, 0.98=slow)
        :param spectrum_templates_path: Override path for spectrum templates (None = use SMB mount)
        """
        self.util = util
        self.meter_config_volumio = meter_config_volumio
        self.screensaver_path = screensaver_path
        self.spectrum_receiver = spectrum_receiver
        self.sp = None
        self._initialized = False
        self._last_packet_seq = -1  # Track last processed packet
        self._local_bins = None  # Local copy for decay between packets
        # Validate and clamp decay rate to sensible range
        self._decay_rate = max(0.5, min(0.99, decay_rate))
        self._spectrum_templates_path = spectrum_templates_path  # Override for local templates
        
        log_client(f"Spectrum decay rate: {self._decay_rate}", "verbose")
        
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
            
            # Get templates_spectrum path - use override if provided, else SMB mount
            if self._spectrum_templates_path:
                templates_spectrum_path = self._spectrum_templates_path
                log_client(f"Using local spectrum templates: {templates_spectrum_path}", "verbose")
            else:
                # screensaver_path is ~/peppy_remote/screensaver
                # SMB mount is at ~/peppy_remote/mnt (contains templates/ and templates_spectrum/)
                install_dir = os.path.dirname(self.screensaver_path)  # ~/peppy_remote
                templates_spectrum_path = os.path.join(install_dir, 'mnt', 'templates_spectrum')
                log_client(f"Using SMB spectrum templates: {templates_spectrum_path}", "verbose")
            
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
                # Ensure Spectrum.__init__ loads only the active section.
                sp_config['current']['spectrum'] = self.s
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
                
                # Client-side: start from zero and wait for new spectrum data (avoids ghosted full bars
                # when meter/spectrum changes; decay + server data will drive bars)
                n_bars = len(self.sp._prev_bar_heights) if (hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights) else (len(self.sp.components) - 1 if self.sp.components else int(self.sp.config.get('size', 30)))
                n_bars = max(1, n_bars)
                self._local_bins = [0.0] * n_bars
                # Force-draw all bars at 0 so set_bars() full height is never shown (no full-value bar / ghost)
                if hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights:
                    for i in range(min(n_bars, len(self.sp._prev_bar_heights))):
                        self.sp._prev_bar_heights[i] = 999.0  # Bypass set_bar_y skip (prev==0 would skip)
                for i in range(n_bars):
                    idx = i + 1
                    try:
                        self.sp.set_bar_y(idx, 0.0)
                        if hasattr(self.sp, 'set_reflection_y'):
                            self.sp.set_reflection_y(idx, 0.0)
                        if hasattr(self.sp, 'set_topping_y'):
                            self.sp.set_topping_y(idx, 0.0)
                    except Exception:
                        pass
                
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
        
        # Fallback init if start() didn't set _local_bins (e.g. older code path)
        if self._local_bins is None and hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights:
            self._local_bins = [0.0] * len(self.sp._prev_bar_heights)
        
        if not self._local_bins:
            return
        
        # Check if we have new packet data
        new_packet = bins and current_seq != self._last_packet_seq
        
        
        # SMOOTH ANIMATION LOGIC:
        # 1. Always decay local bins (bars fall naturally)
        # 2. Only push bars UP when server sends genuinely NEW higher values
        # 3. Ignore repeated/stale server data so decay can work
        
        _prev_len = len(self.sp._prev_bar_heights) if (hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights) else len(self._local_bins)
        num_bars = min(len(self._local_bins), _prev_len)
        
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
        
        # Client-side: ignore "all bars at max" packet (pre-FFT after spectrum reinit); decay and wait for real data
        if bins and len(bins) >= 2:
            mx = max(bins)
            if mx > 50 and all(abs(b - mx) <= 2 for b in bins):
                # Treat as pre-FFT full-height burst: zero and don't apply this packet
                for i in range(num_bars):
                    if i < len(self._local_bins):
                        self._local_bins[i] *= self._decay_rate
                        if self._local_bins[i] < 0.5:
                            self._local_bins[i] = 0.0
                bins = None  # Skip Step 2 so we don't push full values into _local_bins
        
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
        
        # Step 3: Update visual components (no fade-in; follow server data + decay)
        for i in range(num_bars):
            new_height = self._local_bins[i]
            idx = i + 1  # 1-based index for Spectrum methods
            
            # Force update by setting prev to different value (bypass optimization)
            if hasattr(self.sp, '_prev_bar_heights') and self.sp._prev_bar_heights and i < len(self.sp._prev_bar_heights):
                self.sp._prev_bar_heights[i] = new_height + 100
            
            try:
                self.sp.set_bar_y(idx, new_height)
                if hasattr(self.sp, 'set_reflection_y'):
                    self.sp.set_reflection_y(idx, new_height)
                if hasattr(self.sp, 'set_topping_y'):
                    self.sp.set_topping_y(idx, new_height)
            except Exception:
                pass
        
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
# Parse server config to get (meter_folder, chosen_meter) without writing
# =============================================================================
def parse_server_meter_state(config_content, templates_path, active_meter_override=None, client_theme_override=None):
    """
    Parse server config content and return (meter_folder, chosen_meter) that would be used.
    Does not write any files. Uses same rules as setup_remote_config for chosen_meter.
    If client_theme_override is (folder, meter) with both non-empty, return that and do not parse server.
    Used to compare with current theme before deciding to restart on config change.
    """
    import configparser
    from io import StringIO

    if client_theme_override:
        c_folder, c_meter = client_theme_override
        if (c_folder or '').strip() and (c_meter or '').strip():
            return (c_folder or '').strip(), (c_meter or '').strip()
    if not config_content:
        return '', 'random'

    config = configparser.ConfigParser()
    try:
        config.read_file(StringIO(config_content))
    except Exception:
        return '', 'random'

    if 'current' not in config:
        return '', 'random'

    meter_folder = config['current'].get('meter.folder', '')
    meter_value = config['current'].get('meter', '')

    chosen_meter = None
    if active_meter_override:
        meters_file = os.path.join(templates_path, meter_folder, 'meters.txt') if meter_folder else ''
        if meters_file and os.path.exists(meters_file):
            try:
                meters_cfg = configparser.ConfigParser()
                meters_cfg.read(meters_file)
                if active_meter_override in meters_cfg.sections():
                    chosen_meter = active_meter_override
                else:
                    chosen_meter = meter_value if meter_value and meter_value != active_meter_override else 'random'
            except Exception:
                chosen_meter = active_meter_override
        else:
            chosen_meter = active_meter_override
    elif not meter_value and meter_folder:
        chosen_meter = meter_folder
    elif not meter_value:
        chosen_meter = 'random'
    else:
        chosen_meter = meter_value

    final_meter = chosen_meter or meter_folder or 'random'
    if not final_meter:
        final_meter = config['current'].get('meter.folder', 'random') or 'random'

    return meter_folder, final_meter


# =============================================================================
# Setup Remote Config
# =============================================================================
def setup_remote_config(peppymeter_path, templates_path, config_fetcher, active_meter_override=None, client_config=None):
    """
    Set up config.txt for remote client mode.
    
    Fetches config from server via HTTP and adjusts paths for local use.
    If client_config has display.meter_folder and display.meter (kiosk override), those are used instead of server theme.
    
    :param peppymeter_path: Path to peppymeter directory
    :param templates_path: Path to meter templates
    :param config_fetcher: ConfigFetcher instance for HTTP config retrieval
    :param active_meter_override: If set, override meter name (for random meter sync)
    :param client_config: Client config dict; if display.meter_folder and display.meter are set, use as fixed theme
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
    screensaver_path = os.path.dirname(peppymeter_path)
    fonts_dir = os.path.join(screensaver_path, 'fonts')
    config['current']['font.path'] = fonts_dir + os.sep
    
    # Ensure 'meter' key exists for upstream peppymeter compatibility
    # Server config has both: meter.folder (folder name) and meter (meter selection)
    # Upstream peppymeter expects [current] meter = <meter_name>
    #
    # meter.folder = template folder (e.g., '1280x720_custom_3') - contains meters.txt
    # meter = meter section name from meters.txt (e.g., 'black-white', 'vu-analog')
    #         OR 'random' or a comma-separated list
    #
    # When server broadcasts active_meter, it's the SECTION name (not folder)
    meter_folder = config['current'].get('meter.folder', '')
    meter_value = config['current'].get('meter', '')
    
    # If active_meter_override is provided (from server's random meter sync),
    # only update 'meter' - this is the section name currently displayed
    # Keep meter.folder unchanged (it's already correct from server config)
    log_client(f"Config from server: meter.folder={meter_folder!r}, meter={meter_value!r}, override={active_meter_override!r}", "trace", "config")
    
    # Client theme override (kiosk / fixed theme): use display.meter_folder + display.meter if both set
    display = (client_config or {}).get("display") or {}
    override_folder = (display.get("meter_folder") or "").strip()
    override_meter = (display.get("meter") or "").strip()
    if override_folder and override_meter:
        config['current']['meter.folder'] = override_folder
        config['current']['meter'] = override_meter
        chosen_meter = override_meter
        log_client(f"Using client theme override: folder={override_folder!r}, meter={override_meter!r}", "verbose", "config")
    else:
        # Determine the meter to use (server + active_meter_override)
        chosen_meter = None
        if active_meter_override:
            # Server sent a specific active meter - validate it exists in the template
            meters_file = os.path.join(templates_path, meter_folder, 'meters.txt') if meter_folder else ''
            if meters_file and os.path.exists(meters_file):
                try:
                    import configparser as cp
                    meters_cfg = cp.ConfigParser()
                    meters_cfg.read(meters_file)
                    # Check if the active_meter_override is a valid section in meters.txt
                    if active_meter_override in meters_cfg.sections():
                        chosen_meter = active_meter_override
                        print(f"  Using active meter from server: {active_meter_override}")
                    else:
                        # Meter doesn't exist in this template - use server's meter value or random
                        log_client(f"Active meter '{active_meter_override}' not found in {meter_folder}, using fallback", "verbose")
                        chosen_meter = meter_value if meter_value and meter_value != active_meter_override else 'random'
                        print(f"  Active meter '{active_meter_override}' not in template, using: {chosen_meter}")
                except Exception as e:
                    log_client(f"Failed to validate meter: {e}", "verbose")
                    chosen_meter = active_meter_override  # Try anyway
                    print(f"  Using active meter from server: {active_meter_override}")
            else:
                chosen_meter = active_meter_override
                print(f"  Using active meter from server: {active_meter_override}")
        elif not meter_value and meter_folder:
            # Fallback: use meter.folder value as meter if meter is missing
            chosen_meter = meter_folder
            log_client(f"Using meter.folder as meter fallback: {meter_folder}", "verbose")
        elif not meter_value:
            # Neither exists - this is a broken config, set a safe default
            print(f"  WARNING: No 'meter' or 'meter.folder' in config, using 'random'")
            chosen_meter = 'random'
        else:
            chosen_meter = meter_value
    
    # Set the chosen meter
    config['current']['meter'] = chosen_meter or meter_folder or 'random'
    
    # Safety check
    if not config['current'].get('meter'):
        config['current']['meter'] = 'random'
        log_client(f"Forced meter setting: random", "verbose")
    
    # SMOOTH_ROTATION: this is the only place that sets smooth.rotation (rollback: remove next 2 lines)
    config['current']['smooth.rotation'] = 'True'  # written to config.txt; parser reads it into meter_config_volumio
    
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
    
    # Write adjusted config - ensure meter is always set
    final_meter = config['current'].get('meter', '')
    if not final_meter:
        # Safety fallback - if somehow meter is still not set, use meter.folder
        fallback = config['current'].get('meter.folder', 'random')
        config['current']['meter'] = fallback
        log_client(f"Config missing 'meter', using fallback: {fallback}", "verbose")
        final_meter = fallback

    final_meter_folder = config['current'].get('meter.folder', '')

    with open(config_path, 'w') as f:
        config.write(f)
        f.flush()
        os.fsync(f.fileno())  # Force write to disk

    if server_config_fetched:
        print(f"  Config adjusted for local use (meter: {final_meter_folder})")
        log_client(f"Config written: meter.folder={final_meter_folder}, meter={final_meter}", "verbose")

    return config_path, final_meter, final_meter_folder


# =============================================================================
# Full PeppyMeter Display (using volumio_peppymeter)
# =============================================================================
def run_peppymeter_display(level_receiver, server_info, templates_path, config_fetcher, 
                           spectrum_receiver=None, client_config=None):
    """Run full PeppyMeter rendering using volumio_peppymeter code.
    
    :param level_receiver: LevelReceiver for audio level data
    :param server_info: Server information dict (ip, ports, etc.)
    :param templates_path: Path to templates
    :param config_fetcher: ConfigFetcher instance
    :param spectrum_receiver: Optional SpectrumReceiver for spectrum data
    :param client_config: Client configuration dict (for spectrum settings, etc.)
    """
    client_config = client_config or {}
    
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
    
    # Ensure required fonts are local; fetch missing from server
    setup_fonts(screensaver_path, server_info['ip'], 
                server_info.get('volumio_port', 3000))
    
    # Fetch and setup config BEFORE any imports that might read it
    config_path, initial_chosen_meter, initial_meter_folder = setup_remote_config(
        peppymeter_path, templates_path, config_fetcher, client_config=client_config
    )
    
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
            SCREEN_INFO, WIDTH, HEIGHT, DEPTH, SDL_ENV, DOUBLE_BUFFER, SCREEN_RECT,
            METER, METER_FOLDER
        )
        from volumio_configfileparser import Volumio_ConfigFileParser, COLOR_DEPTH
        
        # Import volumio_peppymeter functions (NOT init_display - we have our own for desktop)
        from volumio_peppymeter import (
            start_display_output, CallBack,
            init_debug_config, log_debug, memory_limit
        )
        
        # CRITICAL: Patch CurDir and PeppyPath in server modules for remote client compatibility
        # The server modules use CurDir (set to os.getcwd() at import time) to construct
        # paths like CurDir + '/screensaver/spectrum'. On the server, CurDir is the plugin
        # root. On the client, we must set it to SCRIPT_DIR (~/peppy_remote) so that:
        #   CurDir + '/screensaver/peppymeter' = ~/peppy_remote/screensaver/peppymeter (correct)
        #   CurDir + '/screensaver/spectrum'   = ~/peppy_remote/screensaver/spectrum (correct)
        # Without this patch, SpectrumOutput gets a doubled path and crashes.
        import volumio_peppymeter
        import volumio_spectrum
        volumio_peppymeter.CurDir = SCRIPT_DIR
        volumio_peppymeter.PeppyPath = os.path.join(SCRIPT_DIR, 'screensaver', 'peppymeter')
        volumio_spectrum.CurDir = SCRIPT_DIR
        
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
        
        # Create persist manager for countdown display (mirrors server's Node.js persist file handling)
        persist_manager = PersistManager(
            persist_duration=config_fetcher.persist_duration,
            persist_display=config_fetcher.persist_display
        )
        callback.persist_manager = persist_manager
        if config_fetcher.persist_duration > 0:
            log_client(f"Persist countdown enabled: {config_fetcher.persist_duration}s, mode={config_fetcher.persist_display}", "verbose")
        
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
        current_version_holder = {
            'version': config_fetcher.cached_version or '',
            'active_meter': initial_chosen_meter or '',
            'active_meter_folder': initial_meter_folder or ''
        }
        discovery_port = server_info.get('discovery_port', DISCOVERY_PORT)
        version_listener = ConfigVersionListener(
            discovery_port, current_version_holder, server_ip=server_info['ip']
        )
        version_listener.start()
        
        peppy_running_file = os.path.join(tempfile.gettempdir(), 'peppyrunning')
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
        
        # Show "Waiting for server" modal until first UDP announcement (or timeout)
        SYNC_TIMEOUT = 10.0  # seconds
        sync_start = time.time()
        font_sync = pg.font.SysFont('sans', min(28, max(18, screen_h // 25)))
        line1 = font_sync.render("Waiting for data from server", True, (240, 240, 240))
        line2 = font_sync.render("Please wait a moment.", True, (200, 200, 200))
        modal_w = min(500, int(screen_w * 0.6))
        modal_h = 140
        modal_x = (screen_w - modal_w) // 2
        modal_y = (screen_h - modal_h) // 2
        while not version_listener.first_announcement_received and (time.time() - sync_start) < SYNC_TIMEOUT:
            for ev in pg.event.get():
                if ev.type == pg.QUIT:
                    version_listener.stop_listener()
                    raise SystemExit(0)
                if ev.type == pg.KEYDOWN and ev.key in (pg.K_ESCAPE, pg.K_q):
                    version_listener.stop_listener()
                    raise SystemExit(0)
            screen.fill((30, 30, 35))
            pg.draw.rect(screen, (55, 55, 60), (modal_x, modal_y, modal_w, modal_h), border_radius=12)
            pg.draw.rect(screen, (80, 80, 88), (modal_x, modal_y, modal_w, modal_h), 2, border_radius=12)
            l1_rect = line1.get_rect(center=(screen_w // 2, modal_y + modal_h // 2 - 22))
            l2_rect = line2.get_rect(center=(screen_w // 2, modal_y + modal_h // 2 + 18))
            screen.blit(line1, l1_rect)
            screen.blit(line2, l2_rect)
            pg.display.flip()
            time.sleep(0.05)
        if version_listener.first_announcement_received and version_listener.new_active_meter:
            active_meter_override = version_listener.new_active_meter
            os.chdir(peppymeter_path)
            config_path, new_chosen_meter, new_meter_folder = setup_remote_config(
                peppymeter_path, templates_path, config_fetcher, active_meter_override, client_config=client_config
            )
            current_version_holder['active_meter'] = new_chosen_meter or ''
            current_version_holder['active_meter_folder'] = new_meter_folder or ''
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
            callback.persist_manager = persist_manager
            new_screen_w = pm.util.meter_config[SCREEN_INFO][WIDTH]
            new_screen_h = pm.util.meter_config[SCREEN_INFO][HEIGHT]
            if (new_screen_w, new_screen_h) != (screen_w, screen_h):
                new_flags = 0
                if is_fullscreen:
                    new_flags |= pg.FULLSCREEN
                elif not is_windowed:
                    new_flags |= pg.NOFRAME
                if pm.util.meter_config[SDL_ENV][DOUBLE_BUFFER]:
                    new_flags |= pg.DOUBLEBUF
                screen = pg.display.set_mode((new_screen_w, new_screen_h), new_flags, depth)
                screen_w, screen_h = new_screen_w, new_screen_h
            pm.util.meter_config[SCREEN_INFO][WIDTH] = screen_w
            pm.util.meter_config[SCREEN_INFO][HEIGHT] = screen_h
            pm.util.meter_config[SCREEN_INFO][DEPTH] = depth
            pm.util.meter_config[SCREEN_RECT] = pg.Rect(0, 0, screen_w, screen_h)
            pm.util.PYGAME_SCREEN = screen
            pm.util.screen_copy = screen
            version_listener.reload_requested = False
            version_listener.new_active_meter = None
        
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
                    # Get spectrum settings from client config
                    spectrum_config = client_config.get('spectrum', {})
                    decay_rate = spectrum_config.get('decay_rate', 0.95)
                    templates_config = client_config.get('templates', {})
                    spectrum_templates_path = templates_config.get('spectrum_local_path')
                    
                    remote_spectrum = RemoteSpectrumOutput(
                        pm.util, meter_config_volumio, screensaver_path, spectrum_receiver,
                        decay_rate=decay_rate,
                        spectrum_templates_path=spectrum_templates_path
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
        try:
            Path(peppy_running_file).chmod(0o777)
        except (OSError, AttributeError):
            pass
        # Support both old and new volumio_peppymeter (new has check_reload_callback)
        try:
            import inspect
            _sig = inspect.signature(start_display_output)
            reload_callback_supported = 'check_reload_callback' in _sig.parameters
        except Exception:
            reload_callback_supported = False

        # Cache for "should exit for reload?" so we only fetch once per reload request
        _reload_check_done = [False]
        _reload_should_exit = [None]
        # Client theme override for reload checks (kiosk: use display.meter_folder + display.meter when both set)
        _display = client_config.get("display") or {}
        _of = (str(_display.get("meter_folder") or "")).strip()
        _om = (str(_display.get("meter") or "")).strip()
        client_override = (_of, _om) if (_of and _om) else None

        def _check_reload_callback():
            """Return True only when we actually need to reload (folder or theme would change)."""
            if not version_listener.reload_requested:
                _reload_check_done[0] = False
                return False
            if _reload_check_done[0]:
                return _reload_should_exit[0]
            active_meter_override = version_listener.new_active_meter
            current_folder = pm.util.meter_config.get(SCREEN_INFO, {}).get(METER_FOLDER, '')
            current_meter = pm.util.meter_config.get(METER, '') or ''
            success, config_content, _ = config_fetcher.fetch()
            if not success or not config_content:
                _reload_check_done[0] = True
                _reload_should_exit[0] = True
                return True
            new_meter_folder, new_chosen_meter = parse_server_meter_state(
                config_content, templates_path, active_meter_override, client_theme_override=client_override
            )
            if new_meter_folder == current_folder and new_chosen_meter == current_meter:
                version_listener.reload_requested = False
                version_listener.new_active_meter = None
                current_version_holder['version'] = config_fetcher.cached_version or ''
                current_version_holder['active_meter'] = new_chosen_meter or current_meter
                current_version_holder['active_meter_folder'] = new_meter_folder or current_folder
                _reload_check_done[0] = True
                _reload_should_exit[0] = False
                print("Config/folder+theme unchanged, continuing.")
                return False
            _reload_check_done[0] = True
            _reload_should_exit[0] = True
            return True

        try:
            while True:
                current_version_holder['version'] = config_fetcher.cached_version or ''
                # Track active meter from listener (if known)
                if version_listener.new_active_meter:
                    current_version_holder['active_meter'] = version_listener.new_active_meter
                version_listener.reload_requested = False
                # Save new_active_meter BEFORE clearing for use after display loop exits
                pending_active_meter = version_listener.new_active_meter
                version_listener.new_active_meter = None
                Path(peppy_running_file).touch()
                try:
                    Path(peppy_running_file).chmod(0o777)
                except (OSError, AttributeError):
                    pass
                if reload_callback_supported:
                    start_display_output(pm, callback, meter_config_volumio,
                                        volumio_host=server_info['ip'],
                                        volumio_port=server_info['volumio_port'],
                                        check_reload_callback=_check_reload_callback)
                else:
                    start_display_output(pm, callback, meter_config_volumio,
                                        volumio_host=server_info['ip'],
                                        volumio_port=server_info['volumio_port'])
                if not version_listener.reload_requested:
                    break
                
                # Get active_meter override if server sent a new one
                active_meter_override = version_listener.new_active_meter or pending_active_meter
                
                # Fallback: if we exited without callback (old volumio_peppymeter), check here
                current_folder = pm.util.meter_config.get(SCREEN_INFO, {}).get(METER_FOLDER, '')
                current_meter = pm.util.meter_config.get(METER, '') or ''
                success, config_content, _ = config_fetcher.fetch()
                if success and config_content:
                    new_meter_folder, new_chosen_meter = parse_server_meter_state(
                        config_content, templates_path, active_meter_override, client_theme_override=client_override
                    )
                    if (new_meter_folder == current_folder and new_chosen_meter == current_meter):
                        current_version_holder['version'] = config_fetcher.cached_version or ''
                        current_version_holder['active_meter'] = new_chosen_meter or current_meter
                        current_version_holder['active_meter_folder'] = new_meter_folder or current_folder
                        version_listener.reload_requested = False
                        version_listener.new_active_meter = None
                        print("Config/folder+theme unchanged, continuing.")
                        continue
                
                if active_meter_override:
                    print(f"Server active meter changed to: {active_meter_override}")
                else:
                    print("Config changed on server, reloading...")
                
                # Restore CWD before reload - spectrum may have changed it to its template directory
                os.chdir(peppymeter_path)
                config_path, new_chosen_meter, new_meter_folder = setup_remote_config(
                    peppymeter_path, templates_path, config_fetcher, active_meter_override, client_config=client_config
                )
                current_version_holder['active_meter'] = new_chosen_meter or ''
                current_version_holder['active_meter_folder'] = new_meter_folder or ''
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
                
                # Update persist manager with potentially changed settings from server
                persist_manager.update_settings(
                    config_fetcher.persist_duration,
                    config_fetcher.persist_display
                )
                callback.persist_manager = persist_manager
                
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
                            # Get spectrum settings from client config
                            spectrum_config = client_config.get('spectrum', {})
                            decay_rate = spectrum_config.get('decay_rate', 0.95)
                            templates_config = client_config.get('templates', {})
                            spectrum_templates_path = templates_config.get('spectrum_local_path')
                            
                            remote_spectrum = RemoteSpectrumOutput(
                                pm.util, meter_config_volumio, screensaver_path, spectrum_receiver,
                                decay_rate=decay_rate,
                                spectrum_templates_path=spectrum_templates_path
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
            # Cleanup persist manager
            if persist_manager:
                persist_manager.cleanup()
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
                       help='Run interactive configuration wizard (GUI if available)')
    parser.add_argument('--config-text', action='store_true',
                       help='Run configuration wizard in terminal (text) only, skip GUI')
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
    parser.add_argument('--spectrum-templates',
                       help='Path to spectrum templates directory (overrides SMB mount)')
    parser.add_argument('--decay-rate', type=float,
                       help='Spectrum bar decay rate per frame (0.85=fast, 0.98=slow, default 0.95)')
    parser.add_argument('--debug', choices=['off', 'basic', 'verbose', 'trace'],
                       help='Debug output level')
    parser.add_argument('--trace-spectrum', action='store_true',
                       help='Enable per-packet spectrum trace logging')
    parser.add_argument('--trace-network', action='store_true',
                       help='Enable network connection trace logging')
    
    args = parser.parse_args()
    
    # Run configuration wizard if requested OR on first run (only if we have a terminal)
    run_wizard = args.config or args.config_text
    
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
        # No terminal but first run — try GUI wizard (e.g. launched from desktop)
        run_wizard = True
    
    if run_wizard:
        wizard_config = None
        used_gui = False
        if args.config_text:
            # Text-only: skip GUI, run terminal wizard only
            if has_terminal:
                wizard_config = run_config_wizard()
            else:
                print("ERROR: --config-text requires a terminal.")
                return
        else:
            if can_show_wizard_ui():
                wizard_config = run_wizard_ui(load_config())
                used_gui = True
            # Only fall back to terminal wizard when GUI was not used (e.g. no DISPLAY/tk)
            if wizard_config is None and has_terminal and not used_gui:
                wizard_config = run_config_wizard()
        if wizard_config is None:
            if run_wizard and not has_terminal and not can_show_wizard_ui():
                print("ERROR: No terminal and no GUI available for configuration.")
                print("Install python3-tk for GUI wizard, or run from a terminal with --config.")
            return
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
    if args.spectrum_templates:
        config["templates"]["spectrum_local_path"] = args.spectrum_templates
    if args.decay_rate is not None:
        config["spectrum"]["decay_rate"] = args.decay_rate
    if args.debug:
        config["debug"]["level"] = args.debug
    if args.trace_spectrum:
        config["debug"]["trace_spectrum"] = True
    if args.trace_network:
        config["debug"]["trace_network"] = True
    
    # Initialize client debug system from config
    init_client_debug(config)
    
    # Log startup if debugging enabled
    log_client("PeppyMeter Remote Client starting", "basic")
    log_client(f"Debug level: {CLIENT_DEBUG_LEVEL}", "verbose")
    log_client(f"Trace flags: {CLIENT_DEBUG_TRACE}", "verbose")
    
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
    
    # SMB mount for templates (Linux only; Windows uses UNC paths)
    smb_mount = None
    if config["templates"]["use_smb"] and not config["templates"]["local_path"]:
        if os.name == 'nt':
            # Windows: use UNC paths, no mount
            pass
        else:
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
    elif config["templates"]["use_smb"] and not config["templates"]["local_path"] and os.name == 'nt':
        unc_templates, unc_spectrum = _unc_paths_for_windows(server_info)
        if unc_templates:
            templates_path = unc_templates
            config["templates"]["spectrum_local_path"] = unc_spectrum
        else:
            templates_path = os.path.join(SCRIPT_DIR, "data", "templates")
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
                                         spectrum_receiver=spectrum_receiver,
                                         client_config=config)
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
