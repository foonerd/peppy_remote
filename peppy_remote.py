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
import os
import signal
import socket
import struct
import sys
import tempfile
import threading
import time

# Add lib/ directory to Python path for module imports
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(_SCRIPT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from peppy_common import (
    __version__,
    SCRIPT_DIR,
    DISCOVERY_PORT,
    SMB_MOUNT_BASE,
    SMB_SHARE_PATH,
    SERVER_WAIT_TIMEOUT_SEC,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    CLIENT_DEBUG_LEVEL,
    CLIENT_DEBUG_TRACE,
    _norm_str,
    _is_kiosk_random_mode,
    _resolve_pygame_ui_font,
    setup_logging,
    init_client_debug,
    log_client,
    load_config,
    is_first_run,
    save_config,
    load_config_store,
    get_profile,
    get_profile_list,
    get_active_profile_id,
    set_active_profile,
    save_config_store,
)
from peppy_version import check_remote_version_and_exit_if_mismatch
from peppy_network import ServerDiscovery, ConfigVersionListener, ConfigFetcher
from peppy_persist import PersistManager
from peppy_receivers import LevelReceiver, SpectrumReceiver, RemoteDataSource
from peppy_spectrum import RemoteSpectrumOutput
from peppy_smb import SMBMount
from peppy_asset import (
    setup_format_icons,
    setup_fonts,
    _patch_handlers_for_local_icons,
    _unc_paths_for_windows,
)
from peppy_wizard_cli import run_config_wizard
from peppy_wizard_gui import can_show_wizard_ui, run_wizard_ui


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
                    # Keep runtime active meter authoritative to prevent random flip-flop loops.
                    # Validation mismatch can happen during config/template races.
                    chosen_meter = active_meter_override
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
                        # Deterministic fallback (never "random" for runtime override):
                        # keep existing concrete meter if valid, otherwise first section.
                        sections = meters_cfg.sections()
                        log_client(f"Active meter '{active_meter_override}' not found in {meter_folder}, using deterministic fallback", "verbose")
                        if meter_value and meter_value in sections and meter_value.lower() != "random" and "," not in meter_value:
                            chosen_meter = meter_value
                        elif sections:
                            chosen_meter = sections[0]
                        else:
                            chosen_meter = active_meter_override
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

def _create_peppymeter(label=""):
    """Create PeppyMeter instance, parse Volumio config, init debug, log fonts.

    Uses lazy imports - must be called after sys.path includes screensaver/ paths.

    :param label: Tag for font log lines (e.g. 'sync', 'reload', or '' for initial)
    :returns: (pm, meter_config_volumio)
    """
    from peppymeter.peppymeter import Peppymeter
    from volumio_configfileparser import Volumio_ConfigFileParser
    from volumio_peppymeter import init_debug_config

    pm = Peppymeter(standalone=True, timer_controlled_random_meter=False,
                    quit_pygame_on_stop=False)
    parser = Volumio_ConfigFileParser(pm.util)
    meter_config_volumio = parser.meter_config_volumio
    init_debug_config(meter_config_volumio)

    # Log effective font configuration (visible via remote's own debug)
    tag = f" ({label})" if label else ""
    _use_sys = meter_config_volumio.get('use.system.fonts', False)
    _font_mode = "system fonts (Lato)" if _use_sys else "PeppyFont (universal)"
    log_client(f"--- Font Mode{tag}: {_font_mode} ---", "basic")
    log_client(f"  font.path = {meter_config_volumio.get('font.path', '')}", "basic")
    log_client(f"  font.light = {meter_config_volumio.get('font.light', '')}", "basic")
    log_client(f"  font.regular = {meter_config_volumio.get('font.regular', '')}", "basic")
    log_client(f"  font.bold = {meter_config_volumio.get('font.bold', '')}", "basic")

    return pm, meter_config_volumio


def _wire_peppymeter(pm, meter_config_volumio, remote_ds):
    """Wire remote data source and create callback handler.

    :param pm: Peppymeter instance
    :param meter_config_volumio: Parsed Volumio meter config dict
    :param remote_ds: RemoteDataSource instance
    :returns: CallBack instance (fully wired to pm)
    """
    from volumio_peppymeter import CallBack

    pm.data_source = remote_ds
    if hasattr(pm, 'meter') and pm.meter:
        pm.meter.data_source = remote_ds

    callback = CallBack(pm.util, meter_config_volumio, pm.meter)
    pm.meter.callback_start = callback.peppy_meter_start
    pm.meter.callback_stop = callback.peppy_meter_stop
    pm.dependent = callback.peppy_meter_update
    pm.meter.malloc_trim = callback.trim_memory
    pm.malloc_trim = callback.exit_trim_memory

    return callback


def _attach_display(pm, meter_config_volumio, screen, screen_w, screen_h, depth,
                    is_windowed, is_fullscreen):
    """Resize pygame display if template resolution changed, attach screen to pm.

    :returns: (screen, screen_w, screen_h, depth)
    """
    import pygame as pg
    from configfileparser import SCREEN_INFO, WIDTH, HEIGHT, DEPTH, SDL_ENV, DOUBLE_BUFFER, SCREEN_RECT
    from volumio_configfileparser import COLOR_DEPTH

    new_screen_w = pm.util.meter_config[SCREEN_INFO][WIDTH]
    new_screen_h = pm.util.meter_config[SCREEN_INFO][HEIGHT]
    new_depth = meter_config_volumio[COLOR_DEPTH]

    if (new_screen_w, new_screen_h) != (screen_w, screen_h):
        print(f"Display resizing: {screen_w}x{screen_h} -> {new_screen_w}x{new_screen_h}")
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
        depth = new_depth

    # Attach screen to PeppyMeter
    pm.util.meter_config[SCREEN_INFO][WIDTH] = screen_w
    pm.util.meter_config[SCREEN_INFO][HEIGHT] = screen_h
    pm.util.meter_config[SCREEN_INFO][DEPTH] = depth
    pm.util.PYGAME_SCREEN = screen
    pm.util.screen_copy = screen
    pm.util.meter_config[SCREEN_RECT] = pg.Rect(0, 0, screen_w, screen_h)

    return screen, screen_w, screen_h, depth


def _init_remote_spectrum(pm, meter_config_volumio, screensaver_path,
                          spectrum_receiver, callback, client_config):
    """Initialize remote spectrum if receiver is provided and spectrum is visible.

    :returns: RemoteSpectrumOutput instance, or None
    """
    if not spectrum_receiver:
        return None
    try:
        from volumio_configfileparser import SPECTRUM_VISIBLE
        from configfileparser import METER
        meter_name = pm.util.meter_config[METER]
        meter_section = meter_config_volumio.get(meter_name, {})
        spectrum_visible = meter_section.get(SPECTRUM_VISIBLE, False)
        if not spectrum_visible:
            return None
        spectrum_config = (client_config or {}).get('spectrum', {})
        decay_rate = spectrum_config.get('decay_rate', 0.95)
        templates_config = (client_config or {}).get('templates', {})
        spectrum_templates_path = templates_config.get('spectrum_local_path')
        remote_spectrum = RemoteSpectrumOutput(
            pm.util, meter_config_volumio, screensaver_path, spectrum_receiver,
            decay_rate=decay_rate,
            spectrum_templates_path=spectrum_templates_path
        )
        remote_spectrum.start()
        callback.spectrum_output = remote_spectrum
        return remote_spectrum
    except Exception as e:
        print(f"  Remote spectrum init failed: {e}")
        import traceback
        traceback.print_exc()
        return None


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
        # Peppymeter, Volumio_ConfigFileParser, CallBack, init_debug_config are
        # imported inside the helper functions (_create_peppymeter, _wire_peppymeter)
        from configfileparser import (
            SCREEN_INFO, WIDTH, HEIGHT, DEPTH, SDL_ENV, DOUBLE_BUFFER, SCREEN_RECT,
            METER, METER_FOLDER
        )
        from volumio_configfileparser import COLOR_DEPTH
        
        # Import volumio_peppymeter functions (NOT init_display - we have our own for desktop)
        from volumio_peppymeter import start_display_output, log_debug, memory_limit
        
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
        
        # Initialize PeppyMeter, parse Volumio config, init debug
        print("Initializing PeppyMeter...")
        pm, meter_config_volumio = _create_peppymeter()
        log_debug("=== PeppyMeter Remote Client starting ===", "basic")

        # UDP listeners and registration before RemoteDataSource (supports multiple clients on one host)
        current_version_holder = {
            'version': _norm_str(config_fetcher.cached_version),
            'active_meter': _norm_str(initial_chosen_meter),
            'active_meter_folder': _norm_str(initial_meter_folder)
        }
        _disc_port = server_info.get('discovery_port', DISCOVERY_PORT)
        version_listener = ConfigVersionListener(
            _disc_port, current_version_holder, server_ip=server_info['ip']
        )
        version_listener.start()
        version_listener.wait_until_bound(5.0)
        sp_default = int(server_info.get('spectrum_port', 5581))
        level_receiver.spectrum_default_port = sp_default
        if spectrum_receiver:
            try:
                spectrum_receiver.bind_socket()
            except Exception as e:
                log_client(f"SpectrumReceiver bind_socket: {e}", "verbose", "network")
            level_receiver.spectrum_listen_port = spectrum_receiver.bound_port
        level_receiver.discovery_listen_port = version_listener.bound_port
        if not getattr(level_receiver, '_running', False):
            level_receiver.start()
        if spectrum_receiver and not getattr(spectrum_receiver, '_running', False):
            spectrum_receiver.start()
        
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
        
        # Wire remote data source and create callback handler
        callback = _wire_peppymeter(pm, meter_config_volumio, remote_ds)
        
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
        font_sync = _resolve_pygame_ui_font(pg, min(28, max(18, screen_h // 25)))
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
            current_version_holder['active_meter'] = _norm_str(new_chosen_meter)
            current_version_holder['active_meter_folder'] = _norm_str(new_meter_folder)
            current_version_holder['version'] = _norm_str(config_fetcher.cached_version)
            pm, meter_config_volumio = _create_peppymeter(label="sync")
            callback = _wire_peppymeter(pm, meter_config_volumio, remote_ds)
            callback.persist_manager = persist_manager
            screen, screen_w, screen_h, depth = _attach_display(
                pm, meter_config_volumio, screen, screen_w, screen_h, depth,
                is_windowed, is_fullscreen
            )
            version_listener.reload_requested = False
            version_listener.new_active_meter = None
        
        # Initialize remote spectrum if receiver is provided and spectrum is enabled
        remote_spectrum = _init_remote_spectrum(
            pm, meter_config_volumio, screensaver_path, spectrum_receiver,
            callback, client_config
        )
        
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

        # In kiosk-random mode, suppress server active_meter broadcasts from
        # triggering reloads - the client picks its own random meter locally.
        if _is_kiosk_random_mode(client_override):
            version_listener.ignore_active_meter = True

        def _check_reload_callback():
            """Return True only when we actually need to reload (folder or theme would change)."""
            if not version_listener.reload_requested:
                _reload_check_done[0] = False
                return False
            if _reload_check_done[0]:
                return _reload_should_exit[0]
            # Authoritative on-screen state is pm.util; do not prefer current_version_holder (it can
            # race ahead of the display after UDP and falsely match parse_server_meter_state).
            active_meter_override = version_listener.new_active_meter or current_version_holder.get('active_meter', '')
            current_folder = pm.util.meter_config.get(SCREEN_INFO, {}).get(METER_FOLDER, '')
            current_meter = (pm.util.meter_config.get(METER, '') or '') or current_version_holder.get('active_meter', '')
            success, config_content, _ = config_fetcher.fetch()
            if not success or not config_content:
                _reload_check_done[0] = True
                _reload_should_exit[0] = True
                return True
            new_meter_folder, new_chosen_meter = parse_server_meter_state(
                config_content, templates_path, active_meter_override, client_theme_override=client_override
            )
            if (_norm_str(new_meter_folder) == _norm_str(current_folder) and
                    (_is_kiosk_random_mode(client_override) or
                     _norm_str(new_chosen_meter) == _norm_str(current_meter))):
                version_listener.reload_requested = False
                version_listener.new_active_meter = None
                current_version_holder['version'] = _norm_str(config_fetcher.cached_version)
                current_version_holder['active_meter'] = _norm_str(new_chosen_meter) or _norm_str(current_meter)
                current_version_holder['active_meter_folder'] = _norm_str(new_meter_folder) or _norm_str(current_folder)
                _reload_check_done[0] = True
                _reload_should_exit[0] = False
                print("Config/folder+theme unchanged, continuing.")
                return False
            _reload_check_done[0] = True
            _reload_should_exit[0] = True
            return True

        try:
            while True:
                current_version_holder['version'] = _norm_str(config_fetcher.cached_version)
                # Do not copy listener active_meter into current_version_holder here — that made
                # reload checks think we already matched the server while the display still showed
                # the previous meter. Do not clear reload_requested; the callback clears it when
                # appropriate after comparing on-screen state to the server.
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
                active_meter_override = version_listener.new_active_meter or pending_active_meter or current_version_holder.get('active_meter', '')
                
                # Fallback: if we exited without callback (old volumio_peppymeter), check here
                current_folder = pm.util.meter_config.get(SCREEN_INFO, {}).get(METER_FOLDER, '')
                current_meter = (pm.util.meter_config.get(METER, '') or '') or current_version_holder.get('active_meter', '')
                prev_ver = _norm_str(current_version_holder.get('version'))
                success, config_content, _ = config_fetcher.fetch()
                new_ver = _norm_str(config_fetcher.cached_version or '')
                if success and config_content:
                    new_meter_folder, new_chosen_meter = parse_server_meter_state(
                        config_content, templates_path, active_meter_override, client_theme_override=client_override
                    )
                    if (_norm_str(new_meter_folder) == _norm_str(current_folder) and
                            (_is_kiosk_random_mode(client_override) or
                             _norm_str(new_chosen_meter) == _norm_str(current_meter))):
                        current_version_holder['version'] = new_ver
                        current_version_holder['active_meter'] = _norm_str(new_chosen_meter) or _norm_str(current_meter)
                        current_version_holder['active_meter_folder'] = _norm_str(new_meter_folder) or _norm_str(current_folder)
                        version_listener.reload_requested = False
                        version_listener.new_active_meter = None
                        print("Config/folder+theme unchanged, continuing.")
                        continue

                if not success or not config_content:
                    print("Reloading: could not verify server config (HTTP); retrying full setup.")
                else:
                    parts = []
                    if prev_ver != new_ver:
                        parts.append(f"config version {prev_ver!r} -> {new_ver!r}")
                    nf = _norm_str(new_meter_folder)
                    cf = _norm_str(current_folder)
                    nm = _norm_str(new_chosen_meter)
                    cm = _norm_str(current_meter)
                    if nf != cf:
                        parts.append(f"meter folder {cf!r} -> {nf!r}")
                    if nm != cm:
                        parts.append(f"meter {cm!r} -> {nm!r}")
                    if parts:
                        print("Reloading: " + "; ".join(parts))
                    else:
                        print("Reloading: re-syncing with server (display/state mismatch).")
                
                # Restore CWD before reload - spectrum may have changed it to its template directory
                os.chdir(peppymeter_path)
                config_path, new_chosen_meter, new_meter_folder = setup_remote_config(
                    peppymeter_path, templates_path, config_fetcher, active_meter_override, client_config=client_config
                )
                current_version_holder['active_meter'] = _norm_str(new_chosen_meter)
                current_version_holder['active_meter_folder'] = _norm_str(new_meter_folder)
                current_version_holder['version'] = _norm_str(config_fetcher.cached_version)
                pm, meter_config_volumio = _create_peppymeter(label="reload")
                callback = _wire_peppymeter(pm, meter_config_volumio, remote_ds)
                
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
                
                # Resize display if resolution changed, attach screen to pm
                screen, screen_w, screen_h, depth = _attach_display(
                    pm, meter_config_volumio, screen, screen_w, screen_h, depth,
                    is_windowed, is_fullscreen
                )
                
                # Re-initialize remote spectrum AFTER screen is attached
                remote_spectrum = _init_remote_spectrum(
                    pm, meter_config_volumio, screensaver_path, spectrum_receiver,
                    callback, client_config
                )
                
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
    parser.add_argument('--profile', '-p',
                       help='Start with named profile (name or ID)')
    parser.add_argument('--wizard-debug', action='store_true',
                       help='Enable debug logging before wizard runs (for troubleshooting wizard itself)')
    parser.add_argument('--version', '-V', action='store_true',
                       help='Show version and exit')
    parser.add_argument('--skip-version-check', action='store_true',
                       help='Skip remote vs server release check (diagnostics only)')
    parser.add_argument('--server-wait-timeout', type=int, default=int(SERVER_WAIT_TIMEOUT_SEC),
                       help='Seconds to wait for Volumio HTTP during version check (default %d)' % int(SERVER_WAIT_TIMEOUT_SEC))
    
    args = parser.parse_args()
    
    if args.version:
        print(f"peppy_remote {__version__}")
        sys.exit(0)
    
    # --wizard-debug: activate debug logging before wizard runs
    # Sets module-level globals directly so log_client() works in wizard code
    if args.wizard_debug:
        import peppy_common
        peppy_common.CLIENT_DEBUG_LEVEL = 'verbose'
        peppy_common.CLIENT_DEBUG_TRACE['wizard'] = True
        log_client("Wizard debug enabled via --wizard-debug", "basic")
    
    # --profile: select profile by name or ID before loading config
    if args.profile and not (args.config or args.config_text):
        store = load_config_store()
        profile_list = get_profile_list(store)
        matched_id = None
        search = args.profile.strip().lower()
        # Try exact ID match first, then name match
        for pid, name, host in profile_list:
            if pid == search:
                matched_id = pid
                break
        if not matched_id:
            for pid, name, host in profile_list:
                if (name or '').lower() == search:
                    matched_id = pid
                    break
        if matched_id:
            set_active_profile(store, matched_id)
            save_config_store(store)
            log_client(f"Profile selected via --profile: {matched_id!r}", "basic")
        else:
            print(f"Profile not found: {args.profile}")
            print("Available profiles:")
            for pid, name, host in profile_list:
                host_str = f" ({host})" if host else ""
                print(f"  {name}{host_str}  [id: {pid}]")
            sys.exit(1)
    
    # Run configuration wizard if requested OR on first run
    run_wizard = args.config or args.config_text
    
    if not run_wizard and is_first_run():
        # First run - let wizard handle the welcome message
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
        # Load config from file (active profile)
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
            'volumio_port': config["server"]["volumio_port"],
            'plugin_version': '',
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
    
    spectrum_port = int(server_info.get('spectrum_port', config['server'].get('spectrum_port', 5581)))
    # Remote vs PeppyMeter Screensaver release (blocking UI if mismatch)
    if not args.test:
        check_remote_version_and_exit_if_mismatch(
            server_info, config_fetcher, skip_check=args.skip_version_check,
            wait_timeout_sec=max(5, int(args.server_wait_timeout)),
        )
    # Receivers: discovery UDP bind may run inside run_peppymeter_display (multiple clients / one host)
    level_receiver = LevelReceiver(
        server_info['ip'],
        server_info['level_port'],
        subscriptions=['meters', 'spectrum'],
        spectrum_default_port=spectrum_port,
    )
    spectrum_receiver = SpectrumReceiver(server_info['ip'], spectrum_port)
    
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
        # Simple test display (no discovery listener; bind default ports only)
        level_receiver.start()
        spectrum_receiver.start()
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
