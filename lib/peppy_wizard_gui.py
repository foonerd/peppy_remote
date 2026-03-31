#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Graphical configuration wizard.

tkinter-based GUI wizard with multi-profile support.
Profile manager landing page with new/edit/rename/delete/import/export.
New profiles: linear step wizard. Edit profiles: tabbed section view.
"""

import json
import os
import sys
import threading

from peppy_common import (
    SCRIPT_DIR,
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
    _new_profile,
    get_template_folders,
    get_meter_sections,
)
from peppy_network import ServerDiscovery
from peppy_smb import SMBMount
from peppy_asset import _unc_paths_for_windows


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


# =============================================================================
# Shared helpers
# =============================================================================

def _center_window(root, w, h):
    """Center a window on screen."""
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")


def _clear_frame(frame):
    """Destroy all children of a frame."""
    for child in frame.winfo_children():
        child.destroy()


# =============================================================================
# Profile Editor (linear wizard for new, tabbed for edit)
# =============================================================================

def _run_profile_editor(root, main_frame, store, result, profile_id=None):
    """Run the profile editor.

    For new profiles (profile_id=None): linear step wizard.
    For existing profiles: tabbed section view for direct access.

    :param root: Tk root window
    :param main_frame: Container frame (will be cleared and rebuilt)
    :param store: v2 config store dict
    :param result: Shared result dict for return values
    :param profile_id: Profile to edit, or None for new profile
    """
    import tkinter as tk
    from tkinter import ttk, filedialog

    _clear_frame(main_frame)

    is_new = profile_id is None

    if is_new:
        config = _new_profile()
        editor_title = "New Profile"
    else:
        config = get_profile(store, profile_id)
        editor_title = config.get('name', profile_id)

    wrap_labels = []

    def _wrap_label(parent, **kwargs):
        w = kwargs.pop('wraplength', 440)
        lbl = ttk.Label(parent, wraplength=w, **kwargs)
        wrap_labels.append(lbl)
        return lbl

    # ---- Container: Notebook for edit, plain frame for new ----
    if is_new:
        container = main_frame
        notebook = None
    else:
        # Configure tab style: wide even tabs that fill the width
        style = ttk.Style()
        style.configure('Peppy.TNotebook.Tab', padding=(20, 6))
        style.configure('Peppy.TNotebook', tabposition='n')
        notebook = ttk.Notebook(main_frame, style='Peppy.TNotebook')
        notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        container = notebook

    steps = []
    current_step = [0]  # used by linear mode only

    # ================================================================
    # Step content: Server
    # ================================================================
    server_frame = ttk.Frame(container)
    if is_new:
        ttk.Label(server_frame, text="Server", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))

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
    use_hostname_var = tk.BooleanVar(value=True)
    discovered = []

    # Profile name field (for new profiles)
    if is_new:
        name_row = ttk.Frame(server_frame)
        name_row.pack(anchor=tk.W, fill=tk.X, pady=(0, 10))
        ttk.Label(name_row, text="Profile name:", font=('', 9)).pack(anchor=tk.W)
        profile_name_var = tk.StringVar(value="")
        ttk.Entry(name_row, textvariable=profile_name_var, width=36).pack(anchor=tk.W, fill=tk.X, pady=2)
    else:
        profile_name_var = tk.StringVar(value=config.get('name', ''))

    def _fill_servers_list(servers_dict):
        if not lb.winfo_exists():
            return
        lb.delete(0, tk.END)
        discovered.clear()
        for s in servers_dict.values():
            discovered.append(s)
            lb.insert(tk.END, f"{s.get('hostname', s['ip'])} ({s['ip']})")
        if not discovered:
            lb.insert(tk.END, "No servers found - try manual entry")
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
            disc = ServerDiscovery(port=config["server"]["discovery_port"],
                                  timeout=config["server"]["discovery_timeout"])
            servers = disc.discover()
            root.after(0, lambda: _fill_servers_list(servers))

        t = threading.Thread(target=do_discovery, daemon=True)
        t.start()

    ttk.Radiobutton(server_frame, text="Auto-discover server (recommended)",
                    variable=server_mode_var, value="auto",
                    command=_on_server_mode_change).pack(anchor=tk.W)
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
            if is_new and not profile_name_var.get():
                profile_name_var.set(s.get("hostname", s["ip"]) or "")
        _update_discovery_choice_visibility()

    lb.bind('<<ListboxSelect>>', on_select_server)

    discovery_choice_frame = ttk.Frame(server_frame)
    ttk.Radiobutton(discovery_choice_frame, text="Use hostname",
                    variable=use_hostname_var, value=True).pack(side=tk.LEFT, padx=(0, 16))
    ttk.Radiobutton(discovery_choice_frame, text="Use IP address",
                    variable=use_hostname_var, value=False).pack(side=tk.LEFT)

    ttk.Button(server_frame, text="Discover servers",
               command=lambda: (server_mode_var.set("auto"),
                                _on_server_mode_change(),
                                discover_servers())).pack(anchor=tk.W, pady=4)

    ttk.Radiobutton(server_frame, text="Enter hostname",
                    variable=server_mode_var, value="hostname",
                    command=_on_server_mode_change).pack(anchor=tk.W, pady=(8, 0))
    ent_hostname = ttk.Entry(server_frame, textvariable=hostname_var, width=36)
    ent_hostname.pack(anchor=tk.W, fill=tk.X, pady=2)
    ttk.Radiobutton(server_frame, text="Enter IP address",
                    variable=server_mode_var, value="ip",
                    command=_on_server_mode_change).pack(anchor=tk.W, pady=(8, 0))
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

    # ================================================================
    # Step content: Display
    # ================================================================
    display_frame = ttk.Frame(container)
    if is_new:
        ttk.Label(display_frame, text="Display mode", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    display_mode = tk.StringVar(value="windowed" if config["display"]["windowed"] else
                                ("fullscreen" if config["display"]["fullscreen"] else "frameless"))
    ttk.Radiobutton(display_frame, text="Windowed (movable window with title bar)",
                    variable=display_mode, value="windowed").pack(anchor=tk.W)
    ttk.Radiobutton(display_frame, text="Fullscreen",
                    variable=display_mode, value="fullscreen").pack(anchor=tk.W)
    steps.append(display_frame)

    # ================================================================
    # Step content: Templates
    # ================================================================
    templates_frame = ttk.Frame(container)
    if is_new:
        ttk.Label(templates_frame, text="Template & spectrum theme sources",
                  font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    use_smb_var = tk.BooleanVar(value=config["templates"]["use_smb"])
    ttk.Radiobutton(templates_frame, text="SMB mount from Volumio server (recommended)",
                    variable=use_smb_var, value=True).pack(anchor=tk.W)
    ttk.Radiobutton(templates_frame, text="Use local template paths",
                    variable=use_smb_var, value=False).pack(anchor=tk.W)
    _wrap_label(templates_frame,
                text="When using local paths, meter and spectrum themes must be placed in these folders on this machine.",
                font=('', 9), foreground='#555').pack(anchor=tk.W, pady=(4, 8))
    _wrap_label(templates_frame, text="Meter templates path (leave empty if using SMB):",
                font=('', 9)).pack(anchor=tk.W)
    local_path_var = tk.StringVar(value=config["templates"]["local_path"] or "")
    local_path_row = ttk.Frame(templates_frame)
    local_path_row.pack(anchor=tk.W, fill=tk.X, pady=2)
    ent_local_path = ttk.Entry(local_path_row, textvariable=local_path_var, width=40)
    ent_local_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

    def browse_local_path():
        path = filedialog.askdirectory(parent=root, title="Choose meter templates folder",
                                       initialdir=local_path_var.get() or None)
        if path:
            local_path_var.set(path)

    ttk.Button(local_path_row, text="Browse...", width=8, command=browse_local_path).pack(side=tk.LEFT)
    _wrap_label(templates_frame, text="Spectrum templates path (leave empty if using SMB):",
                font=('', 9)).pack(anchor=tk.W, pady=(6, 0))
    spectrum_local_var = tk.StringVar(value=config["templates"].get("spectrum_local_path") or "")
    spectrum_path_row = ttk.Frame(templates_frame)
    spectrum_path_row.pack(anchor=tk.W, fill=tk.X, pady=2)
    ent_spectrum_local = ttk.Entry(spectrum_path_row, textvariable=spectrum_local_var, width=40)
    ent_spectrum_local.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

    def browse_spectrum_path():
        path = filedialog.askdirectory(parent=root, title="Choose spectrum templates folder",
                                       initialdir=spectrum_local_var.get() or local_path_var.get() or None)
        if path:
            spectrum_local_var.set(path)

    ttk.Button(spectrum_path_row, text="Browse...", width=8, command=browse_spectrum_path).pack(side=tk.LEFT)

    # SMB "Mount now" row
    wizard_smb_templates_path = [None]
    smb_mount_row = ttk.Frame(templates_frame)
    mount_status_var = tk.StringVar(value="Not mounted")
    lbl_mount_status = ttk.Label(smb_mount_row, textvariable=mount_status_var,
                                  font=('', 9), foreground='#555')

    def _wizard_server_host():
        """Return current server host from wizard state or applied config."""
        mode = server_mode_var.get()
        if mode == "hostname":
            h = (hostname_var.get() or "").strip()
            if h:
                return h
        elif mode == "ip":
            h = (ip_var.get() or "").strip()
            if h:
                return h
        else:
            sel = lb.curselection()
            if sel and discovered and 0 <= sel[0] < len(discovered):
                s = discovered[sel[0]]
                return s.get("hostname", s["ip"]) if use_hostname_var.get() else s["ip"]
        return config["server"].get("host") or None

    def _do_smb_mount():
        host = _wizard_server_host()
        if not host:
            mount_status_var.set("Select a server first")
            return
        mount_status_var.set("Mounting...")
        btn_mount_now.config(state=tk.DISABLED)

        def run_mount():
            ok = False
            path = None
            msg = ""
            try:
                if sys.platform == 'win32':
                    server_info = {'hostname': host, 'ip': host}
                    unc_templates, _ = _unc_paths_for_windows(server_info)
                    if unc_templates and os.path.isdir(unc_templates):
                        path = unc_templates
                        ok = True
                        msg = "Connected"
                    else:
                        msg = "Could not access server templates (check SMB share and network)"
                else:
                    smb = SMBMount(host)
                    ok = smb.mount()
                    if ok:
                        path = os.path.join(SCRIPT_DIR, "mnt", "templates")
                    msg = "Mounted" if ok else "Mount failed (check server and sudo)"
            except Exception as e:
                msg = f"Mount failed: {e}"
            root.after(0, lambda: _mount_done(ok, msg, path))

        def _mount_done(ok, msg, path=None):
            if not btn_mount_now.winfo_exists():
                return
            mount_status_var.set(msg)
            btn_mount_now.config(state=tk.NORMAL)
            wizard_smb_templates_path[0] = path if ok else None

        t = threading.Thread(target=run_mount, daemon=True)
        t.start()

    btn_mount_now = ttk.Button(smb_mount_row, text="Mount now", width=10, command=_do_smb_mount)
    btn_mount_now.pack(side=tk.LEFT, padx=(0, 8))
    lbl_mount_status.pack(side=tk.LEFT)

    def _on_smb_choice():
        if use_smb_var.get():
            smb_mount_row.pack(anchor=tk.W, pady=(10, 0))
        else:
            smb_mount_row.pack_forget()

    use_smb_var.trace_add("write", lambda *_: _on_smb_choice())
    _on_smb_choice()
    steps.append(templates_frame)

    # ================================================================
    # Step content: Theme (kiosk / fixed meter)
    # ================================================================
    theme_frame = ttk.Frame(container)
    if is_new:
        ttk.Label(theme_frame, text="Meter theme", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    _wrap_label(theme_frame,
                text="Use server theme (follow Volumio) or lock this client to a fixed theme (kiosk).",
                font=('', 9)).pack(anchor=tk.W, pady=(0, 6))
    theme_mode_var = tk.StringVar(value="server")
    _mf = config["display"].get("meter_folder")
    _mm = config["display"].get("meter")
    if _mf and _mm:
        if _mm.strip().lower() == "random":
            theme_mode_var.set("random_folder")
        elif "," in (_mm or ""):
            theme_mode_var.set("random_list")
        else:
            theme_mode_var.set("fixed")
    ttk.Radiobutton(theme_frame, text="Use server theme",
                    variable=theme_mode_var, value="server").pack(anchor=tk.W)
    ttk.Radiobutton(theme_frame, text="Fixed theme (one meter)",
                    variable=theme_mode_var, value="fixed").pack(anchor=tk.W)
    ttk.Radiobutton(theme_frame, text="Random from folder",
                    variable=theme_mode_var, value="random_folder").pack(anchor=tk.W)
    ttk.Radiobutton(theme_frame, text="Random from list",
                    variable=theme_mode_var, value="random_list").pack(anchor=tk.W)
    theme_folder_var = tk.StringVar(value=config["display"].get("meter_folder") or "")
    theme_meter_var = tk.StringVar(value=config["display"].get("meter") or "")

    def _theme_templates_path():
        if use_smb_var.get():
            if wizard_smb_templates_path[0] and os.path.isdir(wizard_smb_templates_path[0]):
                return wizard_smb_templates_path[0]
            smb_path = os.path.join(SCRIPT_DIR, "mnt", "templates")
            if os.path.isdir(smb_path):
                return smb_path
        lp = (local_path_var.get() or "").strip()
        return lp if lp else os.path.join(SCRIPT_DIR, "data", "templates")

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

    theme_folder_var.trace_add("write", lambda *_: _refresh_theme_meters())
    ttk.Label(theme_frame, text="Template folder:", font=('', 9)).pack(anchor=tk.W, pady=(10, 0))
    folder_combo = ttk.Combobox(theme_frame, textvariable=theme_folder_var, width=36, state="readonly")
    folder_combo.pack(anchor=tk.W, fill=tk.X, pady=2)
    ttk.Label(theme_frame, text="Meter (fixed) or list (random from list):",
              font=('', 9)).pack(anchor=tk.W, pady=(8, 0))
    meter_row = ttk.Frame(theme_frame)
    meter_row.pack(anchor=tk.W, fill=tk.X, pady=2)
    meter_combo = ttk.Combobox(meter_row, textvariable=theme_meter_var, width=24, state="readonly")
    meter_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
    meter_listbox = tk.Listbox(meter_row, selectmode=tk.MULTIPLE, height=4, width=24)
    meter_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    _refresh_theme_folders()
    steps.append(theme_frame)

    # ================================================================
    # Step content: Spectrum
    # ================================================================
    spectrum_frame = ttk.Frame(container)
    if is_new:
        ttk.Label(spectrum_frame, text="Spectrum settings", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    decay = config.get("spectrum", {}).get("decay_rate", 0.95)
    _wrap_label(spectrum_frame,
                text="Decay rate (how fast bars fall after peaks): 0.85 = fast, 0.98 = slow",
                font=('', 9)).pack(anchor=tk.W)
    decay_var = tk.StringVar(value=str(decay))
    ttk.Spinbox(spectrum_frame, textvariable=decay_var, from_=0.5, to=0.99,
                increment=0.01, width=8).pack(anchor=tk.W, pady=4)
    steps.append(spectrum_frame)

    # ================================================================
    # Step content: Debug
    # ================================================================
    debug_frame = ttk.Frame(container)
    if is_new:
        ttk.Label(debug_frame, text="Logger / debug", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    debug_level_var = tk.StringVar(value=config.get("debug", {}).get("level", "off"))
    ttk.Label(debug_frame, text="Debug level:", font=('', 9)).pack(anchor=tk.W)
    ttk.Combobox(debug_frame, textvariable=debug_level_var,
                 values=("off", "basic", "verbose", "trace"),
                 state="readonly", width=12).pack(anchor=tk.W, pady=2)
    trace_spectrum_var = tk.BooleanVar(value=config.get("debug", {}).get("trace_spectrum", False))
    trace_network_var = tk.BooleanVar(value=config.get("debug", {}).get("trace_network", False))
    trace_wizard_var = tk.BooleanVar(value=config.get("debug", {}).get("trace_wizard", False))
    ttk.Checkbutton(debug_frame, text="Trace spectrum packets",
                    variable=trace_spectrum_var).pack(anchor=tk.W, pady=2)
    ttk.Checkbutton(debug_frame, text="Trace network connections",
                    variable=trace_network_var).pack(anchor=tk.W, pady=2)
    ttk.Checkbutton(debug_frame, text="Trace wizard operations",
                    variable=trace_wizard_var).pack(anchor=tk.W, pady=2)
    steps.append(debug_frame)

    # ================================================================
    # Apply functions (shared by both modes)
    # ================================================================
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
        if is_new:
            config["name"] = (profile_name_var.get() or "").strip() or config["server"]["host"] or "Default"

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
        config["debug"]["trace_wizard"] = trace_wizard_var.get()

    def apply_all():
        apply_server()
        apply_display()
        apply_templates()
        apply_theme()
        apply_spectrum()
        apply_debug()

    # ================================================================
    # Save / cancel actions (shared by both modes)
    # ================================================================
    def _save_profile():
        """Save current config to store as profile."""
        nonlocal profile_id
        apply_all()
        if is_new:
            hostname = config["server"].get("host")
            name = config.get("name") or hostname or "Default"
            profile_id = create_profile(store, config, hostname=hostname, name=name)
            set_active_profile(store, profile_id)
        else:
            update_profile(store, profile_id, config)
        save_config_store(store)
        log_client(f"Profile saved: {profile_id!r}", "verbose", "wizard")

    def save_and_run():
        _save_profile()
        cfg = get_profile(store, profile_id)
        cfg['wizard_completed'] = True
        result['config'] = cfg
        result['run_after'] = True
        root.quit()
        root.destroy()

    def save_and_exit():
        _save_profile()
        result['config'] = None
        result['run_after'] = False
        root.quit()
        root.destroy()

    def back_to_manager():
        _show_profile_manager(root, main_frame, store, result)

    def cancel_click():
        result['config'] = None
        root.quit()
        root.destroy()

    # ================================================================
    # Navigation: TABBED mode (edit existing profile)
    # ================================================================
    if not is_new:
        # Add tabs to notebook with evenly padded labels
        tab_names = ["Server", "Display", "Templates", "Theme", "Spectrum", "Debug"]
        for frame, name in zip(steps, tab_names):
            notebook.add(frame, text=name, padding=6)

        # Refresh theme folders when Theme tab is selected
        def _on_tab_change(evt):
            sel = notebook.index(notebook.select())
            if sel == 3:  # Theme tab
                _refresh_theme_folders()

        notebook.bind('<<NotebookTabChanged>>', _on_tab_change)

        # Buttons: always visible in tabbed mode
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        ttk.Button(btn_frame, text="Cancel", command=cancel_click).pack(side=tk.LEFT)
        has_profiles = len(store.get('profiles', {})) > 0
        if has_profiles:
            ttk.Button(btn_frame, text="Profiles", command=back_to_manager).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Save & Run", command=save_and_run).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_frame, text="Save & Exit", command=save_and_exit).pack(side=tk.RIGHT, padx=4)

        # Start discovery on Server tab
        discover_servers()

        root.title(f"PeppyMeter Remote - Edit: {editor_title}")
        return

    # ================================================================
    # Navigation: LINEAR mode (new profile wizard)
    # ================================================================

    # Done step (linear mode only)
    done_frame = ttk.Frame(container)
    ttk.Label(done_frame, text="You're all set", font=('', 12, 'bold')).pack(anchor=tk.W, pady=(0, 10))
    _wrap_label(done_frame,
                text="Save your settings and choose whether to start the client now.",
                justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 16))
    warnings_var = tk.StringVar(value="")
    lbl_warnings = ttk.Label(done_frame, textvariable=warnings_var, font=('', 9),
                              foreground='#c00', wraplength=440)
    lbl_warnings.pack(anchor=tk.W, pady=(0, 8))
    steps.append(done_frame)

    # Wraplength update
    def _update_wraplength(_evt=None):
        w = main_frame.winfo_width()
        if w > 40:
            for lbl in wrap_labels:
                lbl.configure(wraplength=max(80, w - 40))
            lbl_warnings.configure(wraplength=max(80, w - 40))

    main_frame.bind('<Configure>', _update_wraplength)

    # Step title label
    step_titles = ["Server", "Display", "Templates", "Theme", "Spectrum", "Debug", "Done"]
    title_var = tk.StringVar(value=f"{editor_title} - {step_titles[0]}")
    lbl_title = ttk.Label(main_frame, textvariable=title_var, font=('', 11, 'bold'))
    lbl_title.pack(anchor=tk.W, pady=(0, 8))

    def show_step(i):
        for j, f in enumerate(steps):
            if j == i:
                f.pack(fill=tk.BOTH, expand=True)
            else:
                f.pack_forget()
        title_var.set(f"{editor_title} - {step_titles[i]}")

    # Buttons
    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill=tk.X, pady=(16, 0), side=tk.BOTTOM)

    btn_cancel = ttk.Button(btn_frame, text="Cancel", command=cancel_click)
    btn_cancel.pack(side=tk.LEFT)
    btn_profiles = ttk.Button(btn_frame, text="Profiles", command=back_to_manager)
    btn_back = ttk.Button(btn_frame, text="Back")
    btn_save_exit = ttk.Button(btn_frame, text="Save & Exit", command=save_and_exit)
    btn_save_run = ttk.Button(btn_frame, text="Save & Run", command=save_and_run)
    btn_next = ttk.Button(btn_frame, text="Next")
    btn_next.pack(side=tk.RIGHT, padx=4)

    has_profiles = len(store.get('profiles', {})) > 0
    if has_profiles:
        btn_profiles.pack(side=tk.LEFT, padx=4)

    last_step = len(steps) - 1

    def next_click():
        i = current_step[0]
        if i == 0:
            apply_server()
        elif i == 1:
            apply_display()
        elif i == 2:
            apply_templates()
        elif i == 3:
            apply_theme()
        elif i == 4:
            apply_spectrum()
        elif i == 5:
            apply_debug()
            warnings = validate_profile_paths(config)
            if warnings:
                warnings_var.set("Path warnings:\n" + "\n".join(warnings))
            else:
                warnings_var.set("")

        if i < last_step:
            current_step[0] = i + 1
            show_step(current_step[0])
            if current_step[0] == 0:
                discover_servers()
            elif current_step[0] == 3:
                _refresh_theme_folders()
            if current_step[0] == last_step:
                btn_next.pack_forget()
                btn_back.pack(side=tk.RIGHT, padx=4)
                btn_save_run.pack(side=tk.RIGHT, padx=4)
                btn_save_exit.pack(side=tk.RIGHT, padx=4)

    def back_click():
        i = current_step[0]
        if i > 0:
            current_step[0] = i - 1
            show_step(current_step[0])
            if current_step[0] == 3:
                _refresh_theme_folders()
            if i == last_step:
                btn_save_run.pack_forget()
                btn_save_exit.pack_forget()
                btn_back.pack_forget()
                btn_next.pack(side=tk.RIGHT, padx=4)

    btn_next.config(command=next_click)
    btn_back.config(command=back_click)

    show_step(0)
    discover_servers()

    root.title(f"PeppyMeter Remote - {editor_title}")


# =============================================================================
# Profile Manager (landing page)
# =============================================================================

def _show_profile_manager(root, main_frame, store, result):
    """Show the profile manager landing page."""
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    _clear_frame(main_frame)
    root.title("PeppyMeter Remote - Profile Manager")

    ttk.Label(main_frame, text="PeppyMeter Remote",
              font=('', 14, 'bold')).pack(anchor=tk.W, pady=(0, 4))
    ttk.Label(main_frame, text="Profile Manager",
              font=('', 11)).pack(anchor=tk.W, pady=(0, 12))

    # Profile listbox
    list_frame = ttk.Frame(main_frame)
    list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

    lb = tk.Listbox(list_frame, height=8, selectmode=tk.SINGLE, font=('', 10))
    lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=lb.yview)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    lb.config(yscrollcommand=scroll.set)

    profile_ids = []

    def refresh_list():
        lb.delete(0, tk.END)
        profile_ids.clear()
        active_id = get_active_profile_id(store)
        for pid, name, host in get_profile_list(store):
            marker = " *" if pid == active_id else "  "
            host_str = f" ({host})" if host else " (auto-discover)"
            lb.insert(tk.END, f"{marker} {name}{host_str}")
            profile_ids.append(pid)
        if active_id in profile_ids:
            idx = profile_ids.index(active_id)
            lb.selection_set(idx)
            lb.see(idx)

    def selected_pid():
        sel = lb.curselection()
        if sel and 0 <= sel[0] < len(profile_ids):
            return profile_ids[sel[0]]
        return None

    # Action buttons
    action_frame = ttk.Frame(main_frame)
    action_frame.pack(fill=tk.X, pady=(0, 12))

    def on_new():
        _run_profile_editor(root, main_frame, store, result, profile_id=None)

    def on_edit():
        pid = selected_pid()
        if pid:
            _run_profile_editor(root, main_frame, store, result, profile_id=pid)

    def on_activate():
        pid = selected_pid()
        if pid:
            set_active_profile(store, pid)
            save_config_store(store)
            refresh_list()

    def on_rename():
        pid = selected_pid()
        if not pid:
            return
        profile = get_profile(store, pid)
        old_name = profile.get('name', pid) if profile else pid

        dlg = tk.Toplevel(root)
        dlg.title("Rename Profile")
        dlg.geometry("350x120")
        dlg.transient(root)
        dlg.grab_set()
        _center_window(dlg, 350, 120)

        ttk.Label(dlg, text=f"Rename '{old_name}':").pack(padx=20, pady=(16, 4), anchor=tk.W)
        name_var = tk.StringVar(value=old_name)
        ent = ttk.Entry(dlg, textvariable=name_var, width=40)
        ent.pack(padx=20, fill=tk.X)
        ent.select_range(0, tk.END)
        ent.focus_set()

        def do_rename():
            new_name = name_var.get().strip()
            if new_name and new_name != old_name:
                rename_profile(store, pid, new_name)
                save_config_store(store)
            dlg.destroy()
            refresh_list()

        ent.bind('<Return>', lambda _: do_rename())
        btn_row = ttk.Frame(dlg)
        btn_row.pack(fill=tk.X, padx=20, pady=(8, 0))
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Rename", command=do_rename).pack(side=tk.RIGHT)

    def on_delete():
        pid = selected_pid()
        if not pid:
            return
        if len(profile_ids) <= 1:
            messagebox.showwarning("Delete", "Cannot delete the last profile.", parent=root)
            return
        profile = get_profile(store, pid)
        name = profile.get('name', pid) if profile else pid
        if messagebox.askyesno("Delete Profile", f"Delete profile '{name}'?", parent=root):
            delete_profile(store, pid)
            save_config_store(store)
            refresh_list()

    def on_import():
        filepath = filedialog.askopenfilename(
            parent=root, title="Import Profile",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not filepath:
            return
        pid, pcfg, warnings = import_profile(filepath)
        if pid is None:
            messagebox.showerror("Import Failed", "\n".join(warnings), parent=root)
            return
        if warnings:
            messagebox.showwarning("Import Warnings", "\n".join(warnings), parent=root)
        if pid in store.get('profiles', {}):
            existing_name = store['profiles'][pid].get('name', pid)
            answer = messagebox.askyesnocancel(
                "Profile Exists",
                f"A profile with ID '{pid}' already exists (name: {existing_name}).\n\n"
                "Yes = Overwrite existing\n"
                "No = Create with new name\n"
                "Cancel = Abort import",
                parent=root
            )
            if answer is None:
                return
            if answer:
                update_profile(store, pid, pcfg)
            else:
                hostname = pcfg.get('server', {}).get('host') if isinstance(pcfg.get('server'), dict) else None
                name = pcfg.get('name') or pid
                pid = create_profile(store, pcfg, hostname=hostname, name=name + " (imported)")
        else:
            store.setdefault('profiles', {})[pid] = pcfg
            if not store.get('active_profile'):
                store['active_profile'] = pid
        save_config_store(store)
        refresh_list()
        name = pcfg.get('name', pid)
        messagebox.showinfo("Import", f"Imported profile '{name}'.", parent=root)

    def on_export():
        pid = selected_pid()
        if not pid:
            return
        profile = get_profile(store, pid)
        name = profile.get('name', pid) if profile else pid
        default_filename = f"peppy_profile_{pid}.json"
        filepath = filedialog.asksaveasfilename(
            parent=root, title="Export Profile",
            initialfile=default_filename,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not filepath:
            return
        if export_profile(store, pid, filepath):
            messagebox.showinfo("Export", f"Exported '{name}' to:\n{filepath}", parent=root)
        else:
            messagebox.showerror("Export Failed", "Could not write export file.", parent=root)

    # Button grid: evenly spaced, 4 columns x 2 rows
    for c in range(4):
        action_frame.columnconfigure(c, weight=1, uniform='btn')
    bw = 11  # uniform button width
    ttk.Button(action_frame, text="New", width=bw, command=on_new).grid(row=0, column=0, padx=2, pady=2, sticky='ew')
    ttk.Button(action_frame, text="Edit", width=bw, command=on_edit).grid(row=0, column=1, padx=2, pady=2, sticky='ew')
    ttk.Button(action_frame, text="Rename", width=bw, command=on_rename).grid(row=0, column=2, padx=2, pady=2, sticky='ew')
    ttk.Button(action_frame, text="Set Active", width=bw, command=on_activate).grid(row=0, column=3, padx=2, pady=2, sticky='ew')
    ttk.Button(action_frame, text="Delete", width=bw, command=on_delete).grid(row=1, column=0, padx=2, pady=2, sticky='ew')
    ttk.Button(action_frame, text="Import", width=bw, command=on_import).grid(row=1, column=1, padx=2, pady=2, sticky='ew')
    ttk.Button(action_frame, text="Export", width=bw, command=on_export).grid(row=1, column=2, padx=2, pady=2, sticky='ew')

    # Bottom: Start / Cancel
    bottom = ttk.Frame(main_frame)
    bottom.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))

    def on_start():
        pid = selected_pid()
        if not pid:
            messagebox.showwarning("Start", "Select a profile first.", parent=root)
            return
        set_active_profile(store, pid)
        save_config_store(store)
        cfg = get_profile(store, pid)
        cfg['wizard_completed'] = True
        result['config'] = cfg
        result['run_after'] = True
        root.quit()
        root.destroy()

    def on_cancel():
        result['config'] = None
        root.quit()
        root.destroy()

    ttk.Button(bottom, text="Cancel", command=on_cancel).pack(side=tk.LEFT)
    ttk.Button(bottom, text="Start", command=on_start).pack(side=tk.RIGHT, padx=4)

    refresh_list()
    lb.bind('<Double-Button-1>', lambda _: on_edit())


# =============================================================================
# Entry Point
# =============================================================================

def run_wizard_ui(initial_config=None):
    """Graphical setup wizard with multi-profile support.

    Uses tkinter (no extra dependency on most Linux desktops: python3-tk).
    Returns config dict on "Save & Run" / "Start", None on cancel or "Save & Exit".

    :param initial_config: Ignored if profiles exist in store (backward compat parameter)
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return None

    store = load_config_store()
    result = {'config': None, 'run_after': False}

    root = tk.Tk()
    root.resizable(True, True)
    root.minsize(440, 480)
    root.geometry("600x580")
    _center_window(root, 600, 580)

    main_frame = ttk.Frame(root, padding=20)
    main_frame.pack(fill=tk.BOTH, expand=True)

    profiles = store.get('profiles', {})

    if not profiles:
        root.title("PeppyMeter Remote - New Profile")
        _run_profile_editor(root, main_frame, store, result, profile_id=None)
    else:
        _show_profile_manager(root, main_frame, store, result)

    root.protocol("WM_DELETE_WINDOW", lambda: (
        result.update({'config': None}),
        root.quit(),
        root.destroy()
    ))
    root.mainloop()

    if result.get('config') is not None and result.get('run_after'):
        return result['config']
    return None
