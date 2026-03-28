#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Terminal configuration wizard.

Interactive text-based configuration wizard with multi-profile support.
"""

import json
import os
import sys

from peppy_common import (
    SMB_MOUNT_BASE,
    SCRIPT_DIR,
    CONFIG_FILE,
    DEFAULT_PROFILE,
    log_client,
    load_config_store,
    save_config_store,
    get_profile_list,
    get_active_profile_id,
    set_active_profile,
    create_profile,
    delete_profile,
    rename_profile,
    get_profile,
    update_profile,
    export_profile,
    import_profile,
    validate_profile_paths,
    slugify_profile_id,
    _new_profile,
    get_template_folders,
    get_meter_sections,
)
from peppy_smb import SMBMount
from peppy_asset import _unc_paths_for_windows


def _clear_screen():
    os.system('clear' if os.name != 'nt' else 'cls')


def _get_input(prompt, default=None):
    """Get user input with optional default."""
    if default is not None:
        result = input(f"{prompt} [{default}]: ").strip()
        return result if result else str(default)
    return input(f"{prompt}: ").strip()


def _pause(msg="Press Enter to continue..."):
    input(msg)


# =============================================================================
# Profile Editor - edit a single profile's settings (16-item menu)
# =============================================================================

def _edit_profile(config, profile_name):
    """Edit a profile config dict interactively.

    Modifies config in place. Returns when user chooses Back.

    :param config: Profile config dict (modified in place)
    :param profile_name: Display name for the header
    """

    def show_menu():
        _clear_screen()
        print("=" * 54)
        print(f" Edit Profile: {profile_name}")
        print("=" * 54)
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
        trace_wizard = "yes" if config.get("debug", {}).get("trace_wizard", False) else "no"
        print("Debug Settings:")
        print(f"  14. Debug level:      {debug_level}")
        print(f"  15. Trace spectrum:   {trace_spectrum}")
        print(f"  16. Trace network:    {trace_network}")
        print(f"  17. Trace wizard:     {trace_wizard}")
        print()

        # Path validation warnings
        warnings = validate_profile_paths(config)
        if warnings:
            print("  ** Path warnings:")
            for w in warnings:
                print(f"     {w}")
            print()

        print("-" * 54)
        print("  B = Back to profile manager")
        print()

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
            value = int(_get_input(f"{name}", current))
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

    def _try_smb_mount():
        """Attempt SMB mount for template browsing. Returns templates path or None."""
        host = config["server"].get("host")
        if not host:
            print("  No server host configured (option 1). Cannot mount.")
            _pause()
            return None
        print(f"  Mounting templates from {host}...")
        try:
            if sys.platform == 'win32':
                server_info = {'hostname': host, 'ip': host}
                unc_templates, _ = _unc_paths_for_windows(server_info)
                if unc_templates and os.path.isdir(unc_templates):
                    print(f"  Connected: {unc_templates}")
                    return unc_templates
                print("  Could not access server templates (check SMB share and network)")
                return None
            else:
                smb = SMBMount(host)
                if smb.mount():
                    path = os.path.join(SCRIPT_DIR, "mnt", "templates")
                    if os.path.isdir(path):
                        print(f"  Mounted: {path}")
                        return path
                print("  Mount failed (check server is running and sudo is available)")
                return None
        except Exception as e:
            print(f"  Mount failed: {e}")
            return None

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
        # Resolve templates path: local_path -> SMB mount -> mount now -> manual
        templates_path = (config["templates"].get("local_path") or "").strip()
        if not templates_path:
            smb_path = os.path.join(SCRIPT_DIR, "mnt", "templates")
            if os.path.isdir(smb_path):
                templates_path = smb_path
        folders = get_template_folders(templates_path) if templates_path else []
        # If no folders found, offer mount / manual path / cancel
        while not folders:
            print()
            if not templates_path:
                print("No templates path available.")
            else:
                print(f"No template folders found in: {templates_path}")
                print("(Folders need a meters.txt file inside them.)")
            print()
            if config["templates"].get("use_smb"):
                print("  1. Mount server templates now")
                print("  2. Enter a templates path manually")
                print("  3. Cancel (use server theme)")
                print()
                pchoice = input("Choice [1]: ").strip() or "1"
            else:
                print("  1. Enter a templates path manually")
                print("  2. Cancel (use server theme)")
                print()
                pchoice = input("Choice [1]: ").strip() or "1"
                # Remap: no mount option, so 1=manual, 2=cancel
                pchoice = {"1": "2", "2": "3"}.get(pchoice, "3")
            if pchoice == "1":
                mounted_path = _try_smb_mount()
                if mounted_path:
                    templates_path = mounted_path
                    folders = get_template_folders(templates_path)
                    if not folders:
                        print(f"  Mounted but no template folders found in: {templates_path}")
            elif pchoice == "2":
                manual = input("Templates path: ").strip()
                if manual and os.path.isdir(manual):
                    templates_path = manual
                    folders = get_template_folders(templates_path)
                    if not folders:
                        print(f"  No template folders with meters.txt found in: {manual}")
                elif manual:
                    print(f"  Directory not found: {manual}")
                else:
                    print("  No path entered.")
            else:
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
            value = float(_get_input("Decay rate (0.5-0.99)", current))
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
        print(f"{name}:")
        print(f"  1. No")
        print(f"  2. Yes")
        print()
        choice = input(f"Choice [{'2' if current else '1'}]: ").strip() or ('2' if current else '1')
        if "debug" not in config:
            config["debug"] = {}
        config["debug"][key] = (choice == "2")

    # Editor main loop
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
                value = int(_get_input("Discovery timeout (seconds)", config["server"]["discovery_timeout"]))
                config["server"]["discovery_timeout"] = value
            except ValueError:
                print("Invalid number")
        elif choice == "6":
            config_window_mode()
        elif choice == "7":
            config_position()
        elif choice == "8":
            try:
                value = int(_get_input("Monitor number", config["display"]["monitor"]))
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
        elif choice == "17":
            config_trace_toggle("trace_wizard", "Trace wizard operations")
        elif choice == "B":
            return
        else:
            _pause("Invalid choice. Press Enter to continue...")


# =============================================================================
# New Profile Wizard - guided setup for a new profile
# =============================================================================

def _new_profile_wizard(store):
    """Create a new profile interactively.

    Asks for server host first (needed for profile ID), then opens the
    full editor. Saves and returns the new profile ID, or None if cancelled.

    :param store: v2 config store (modified in place)
    :returns: New profile ID, or None if cancelled
    """
    _clear_screen()
    print("=" * 50)
    print(" New Profile")
    print("=" * 50)
    print()

    # Step 1: Server host (needed for profile ID and name)
    print("Server host:")
    print("  1. Auto-discover (recommended)")
    print("  2. Enter hostname (e.g., volumio)")
    print("  3. Enter IP address")
    print()
    choice = input("Choice [1]: ").strip() or "1"

    host = None
    if choice == "2":
        host = input("Hostname: ").strip() or None
    elif choice == "3":
        host = input("IP address: ").strip() or None

    # Step 2: Friendly name
    default_name = host or "Default"
    name = input(f"\nProfile name [{default_name}]: ").strip() or default_name

    # Create profile
    config = _new_profile()
    config['server']['host'] = host
    config['name'] = name

    profile_id = create_profile(store, config, hostname=host, name=name)
    save_config_store(store)

    print(f"\nProfile '{name}' created (id: {profile_id})")
    print("Opening editor to configure remaining settings...")
    _pause()

    # Open full editor
    profile_config = get_profile(store, profile_id)
    _edit_profile(profile_config, name)

    # Save edited config back
    update_profile(store, profile_id, profile_config)
    save_config_store(store)

    log_client(f"New profile wizard completed: {profile_id!r}", "verbose", "wizard")
    return profile_id


# =============================================================================
# Import / Export
# =============================================================================

def _do_import(store):
    """Import a profile from a JSON file.

    :param store: v2 config store (modified in place)
    :returns: True if imported successfully
    """
    print()
    filepath = input("Import file path: ").strip()
    if not filepath:
        print("Cancelled.")
        return False

    filepath = os.path.expanduser(filepath)
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        _pause()
        return False

    profile_id, profile_config, warnings = import_profile(filepath)
    if profile_id is None:
        print("Import failed:")
        for w in warnings:
            print(f"  {w}")
        _pause()
        return False

    # Show warnings
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  {w}")
        print()

    # Check for ID collision
    if profile_id in store.get('profiles', {}):
        existing_name = store['profiles'][profile_id].get('name', profile_id)
        print(f"A profile with ID '{profile_id}' already exists (name: {existing_name}).")
        print("  1. Overwrite existing")
        print("  2. Create with new name")
        print("  3. Cancel")
        print()
        choice = input("Choice [3]: ").strip() or "3"
        if choice == "1":
            update_profile(store, profile_id, profile_config)
        elif choice == "2":
            new_name = input("New profile name: ").strip()
            if not new_name:
                print("Cancelled.")
                _pause()
                return False
            hostname = None
            if isinstance(profile_config.get('server'), dict):
                hostname = profile_config['server'].get('host')
            profile_id = create_profile(store, profile_config, hostname=hostname, name=new_name)
        else:
            print("Cancelled.")
            _pause()
            return False
    else:
        store.setdefault('profiles', {})[profile_id] = profile_config
        if not store.get('active_profile'):
            store['active_profile'] = profile_id

    save_config_store(store)
    name = profile_config.get('name', profile_id)
    print(f"\nImported profile '{name}' (id: {profile_id})")
    _pause()
    return True


def _do_export(store):
    """Export a profile to a JSON file.

    :param store: v2 config store
    """
    profiles = get_profile_list(store)
    if not profiles:
        print("No profiles to export.")
        _pause()
        return

    if len(profiles) == 1:
        pid = profiles[0][0]
        name = profiles[0][1]
        print(f"\nExporting profile: {name}")
    else:
        print("\nSelect profile to export:")
        for i, (pid, name, host) in enumerate(profiles, 1):
            host_str = f" ({host})" if host else ""
            print(f"  {i}. {name}{host_str}")
        print()
        choice = input("Number: ").strip()
        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(profiles)):
                print("Invalid selection.")
                _pause()
                return
            pid = profiles[idx][0]
            name = profiles[idx][1]
        except ValueError:
            print("Invalid selection.")
            _pause()
            return

    default_filename = f"peppy_profile_{pid}.json"
    filepath = input(f"Export file path [{default_filename}]: ").strip() or default_filename
    filepath = os.path.expanduser(filepath)

    if os.path.exists(filepath):
        confirm = input(f"File exists. Overwrite? [y/N]: ").strip().lower()
        if confirm != 'y':
            print("Cancelled.")
            _pause()
            return

    if export_profile(store, pid, filepath):
        print(f"\nExported '{name}' to {filepath}")
    else:
        print("Export failed.")
    _pause()


# =============================================================================
# Profile Manager - top-level menu
# =============================================================================

def run_config_wizard():
    """Interactive configuration wizard with multi-profile support.

    Shows profile manager landing menu. On first run (no profiles),
    goes directly to new profile wizard.

    :returns: Config dict to run with, or None to exit without running
    """
    store = load_config_store()
    profiles = store.get('profiles', {})

    # First run - no profiles, go straight to new profile wizard
    if not profiles:
        _clear_screen()
        print("=" * 50)
        print(" Welcome to PeppyMeter Remote Client!")
        print("=" * 50)
        print()
        print("No profiles configured. Let's create one.")
        _pause()

        profile_id = _new_profile_wizard(store)
        if profile_id is None:
            return None

        set_active_profile(store, profile_id)
        save_config_store(store)

        # Ask whether to start
        print()
        print("  R = Run with this profile")
        print("  Q = Exit")
        choice = input("Choice [R]: ").strip().upper() or "R"
        if choice == "R":
            config = get_profile(store, profile_id)
            config['wizard_completed'] = True
            return config
        return None

    # Profile manager loop
    while True:
        _clear_screen()
        active_id = get_active_profile_id(store)
        profile_list = get_profile_list(store)

        print("=" * 58)
        print(" PeppyMeter Remote - Profile Manager")
        print("=" * 58)
        print()
        print("Profiles:")
        for i, (pid, name, host) in enumerate(profile_list, 1):
            marker = " *" if pid == active_id else "  "
            host_str = f" ({host})" if host else " (auto-discover)"
            print(f"  {i}.{marker} {name}{host_str}")
        print()
        print("-" * 58)
        print("  N = New profile")
        print("  E = Edit profile (enter number)")
        print("  A = Set active profile (enter number)")
        print("  R = Rename profile")
        print("  D = Delete profile")
        print("  I = Import profile from file")
        print("  X = Export profile to file")
        print()
        print("  S = Start with active profile")
        print("  Q = Quit without starting")
        print()

        choice = input("Choice: ").strip().upper()

        if choice == "N":
            new_id = _new_profile_wizard(store)
            if new_id:
                set_active_profile(store, new_id)
                save_config_store(store)

        elif choice == "E":
            num = input("Profile number to edit: ").strip()
            try:
                idx = int(num) - 1
                if 0 <= idx < len(profile_list):
                    pid = profile_list[idx][0]
                    pname = profile_list[idx][1]
                    profile_config = get_profile(store, pid)
                    _edit_profile(profile_config, pname)
                    update_profile(store, pid, profile_config)
                    save_config_store(store)
                else:
                    print("Invalid number.")
                    _pause()
            except ValueError:
                print("Invalid input.")
                _pause()

        elif choice == "A":
            num = input("Profile number to activate: ").strip()
            try:
                idx = int(num) - 1
                if 0 <= idx < len(profile_list):
                    pid = profile_list[idx][0]
                    set_active_profile(store, pid)
                    save_config_store(store)
                    print(f"Active profile set to: {profile_list[idx][1]}")
                    _pause()
                else:
                    print("Invalid number.")
                    _pause()
            except ValueError:
                print("Invalid input.")
                _pause()

        elif choice == "R":
            num = input("Profile number to rename: ").strip()
            try:
                idx = int(num) - 1
                if 0 <= idx < len(profile_list):
                    pid = profile_list[idx][0]
                    old_name = profile_list[idx][1]
                    new_name = input(f"New name for '{old_name}': ").strip()
                    if new_name:
                        rename_profile(store, pid, new_name)
                        save_config_store(store)
                        print(f"Renamed to: {new_name}")
                    else:
                        print("Name not changed.")
                    _pause()
                else:
                    print("Invalid number.")
                    _pause()
            except ValueError:
                print("Invalid input.")
                _pause()

        elif choice == "D":
            if len(profile_list) <= 1:
                print("Cannot delete the last profile.")
                _pause()
                continue
            num = input("Profile number to delete: ").strip()
            try:
                idx = int(num) - 1
                if 0 <= idx < len(profile_list):
                    pid = profile_list[idx][0]
                    pname = profile_list[idx][1]
                    confirm = input(f"Delete '{pname}'? [y/N]: ").strip().lower()
                    if confirm == 'y':
                        if delete_profile(store, pid):
                            save_config_store(store)
                            print(f"Deleted: {pname}")
                        else:
                            print("Delete failed.")
                    else:
                        print("Cancelled.")
                    _pause()
                else:
                    print("Invalid number.")
                    _pause()
            except ValueError:
                print("Invalid input.")
                _pause()

        elif choice == "I":
            _do_import(store)

        elif choice == "X":
            _do_export(store)

        elif choice == "S":
            active_id = get_active_profile_id(store)
            if not active_id:
                print("No active profile set.")
                _pause()
                continue
            config = get_profile(store, active_id)
            config['wizard_completed'] = True
            print(f"\nStarting with profile: {config.get('name', active_id)}")
            return config

        elif choice == "Q":
            return None

        else:
            _pause("Invalid choice. Press Enter to continue...")
