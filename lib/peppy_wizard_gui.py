#!/usr/bin/env python3
"""
PeppyMeter Remote Client - Graphical configuration wizard.

tkinter-based GUI setup wizard.
"""

import json
import os
import sys
import threading

from peppy_common import (
    SCRIPT_DIR,
    DEFAULT_CONFIG,
    save_config,
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
    # Resolved SMB templates path after successful "Mount now" (Linux: mnt/templates, Windows: UNC)
    wizard_smb_templates_path = [None]
    # SMB "Mount now" row (shown when SMB is selected; same on Linux and Windows)
    smb_mount_row = ttk.Frame(templates_frame)
    mount_status_var = tk.StringVar(value="Not mounted")
    lbl_mount_status = ttk.Label(smb_mount_row, textvariable=mount_status_var, font=('', 9), foreground='#555')
    def _wizard_server_host():
        """Return current server host from wizard state, or None if not set.

        Tries tkinter widget state first (live Step 1), then falls back to
        config['server']['host'] which is set by apply_server() when the
        user leaves Step 1.  The listbox selection is lost when the frame
        is hidden by pack_forget(), so the fallback is needed for Steps 2+.
        """
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
        # Fallback: apply_server() already saved host when user left Step 1
        return config["server"].get("host") or None
    def _do_smb_mount():
        host = _wizard_server_host()
        if not host:
            mount_status_var.set("Select a server first (Step 1)")
            return
        mount_status_var.set("Mounting…")
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
                    msg = "Mounted" if ok else ("Mount failed (check server and sudo)")
            except Exception as e:
                msg = f"Mount failed: {e}"
            root.after(0, lambda: _mount_done(ok, msg, path))
        def _mount_done(ok, msg, path=None):
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
        if use_smb_var.get():
            if wizard_smb_templates_path[0] and os.path.isdir(wizard_smb_templates_path[0]):
                return wizard_smb_templates_path[0]
            smb_path = os.path.join(SCRIPT_DIR, "mnt", "templates")
            if os.path.isdir(smb_path):
                return smb_path
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
            _refresh_theme_folders()
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
