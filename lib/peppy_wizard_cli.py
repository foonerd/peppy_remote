#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Terminal configuration wizard.

Interactive text-based configuration wizard.
"""

import json
import os

from peppy_common import (
    SCRIPT_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    load_config,
    save_config,
    get_template_folders,
    get_meter_sections,
)

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
