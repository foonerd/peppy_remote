# PeppyMeter Remote Client

Display PeppyMeter visualizations on any Debian-based system by connecting to a Volumio server running the PeppyMeter plugin.

This client uses the **same rendering code** as the Volumio plugin (turntable, cassette, meters) but receives audio data over the network. It waits for the server’s first announcement before starting the meter (syncing screen), syncs to the server’s theme (including random meter), and only reloads when the theme folder or theme name actually changes.

## Quick Install

One-liner installation:

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.sh | bash
```

With server pre-configured:

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.sh | bash -s -- --server volumio
```

On desktop systems, the installer adds two launchers: **PeppyMeter Remote** (start client) and **PeppyMeter Remote (Configure)** (wrench icon, opens the setup wizard).

### Windows

**Prerequisites:** Windows 10 or 11. The installer needs **Python 3.8+** and **Git**. If either is missing, the script will check, list what’s missing, and ask: *“Install missing dependencies via winget? [Y/n]”*. Answer **Y** (or press Enter) to install via **winget** (Windows Package Manager); you may see a UAC prompt. If you answer **n**, the script exits with manual install links. If **winget** is not available, install Python and Git manually, then run the installer again.

**First-time PowerShell:** You may need to allow script execution once:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**One-liner install:**

```powershell
irm https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.ps1 | iex
```

**With options** (pass after the one-liner):

```powershell
# Pre-configure server hostname or IP
irm ... | iex -ArgumentList '-Server','volumio'

# Custom install directory (default is your user profile\peppy_remote)
irm ... | iex -ArgumentList '-Dir','C:\peppy_remote'

# Help
irm ... | iex -ArgumentList '-Help'
```

**What the installer does:**

1. **Dependencies:** Checks for Python 3.8+ and Git. If missing, prompts to install via winget (Python.Python.3.12, Git.Git). After winget installs, it refreshes PATH and re-checks; if still not visible, it asks you to close and reopen PowerShell and run the script again.
2. **Directory:** Creates the install folder (default: `%USERPROFILE%\peppy_remote`, e.g. `C:\Users\YourName\peppy_remote`). If the folder already exists, it asks whether to remove and reinstall.
3. **Downloads:** Fetches `peppy_remote.py`, `uninstall.ps1`, and SVG icons from the repo.
4. **Repos:** Clones PeppyMeter and PeppySpectrum via Git into `screensaver\peppymeter` and `screensaver\spectrum`.
5. **Volumio handlers:** Downloads Volumio custom handlers (turntable, cassette, spectrum, etc.) and format icons into `screensaver\`.
6. **Python env:** Creates a virtual environment in `venv\` and installs required packages (pygame, pillow, python-socketio, etc.).
7. **Launchers:** Creates `peppy_remote.cmd` and `peppy_remote.ps1` (both set PYTHONPATH and run the client). Optional Desktop and Start Menu shortcuts: **PeppyMeter Remote** (runs client) and **PeppyMeter Remote (Configure)** (opens setup wizard).
8. **Config:** Writes `config.json`; use `-Server` to pre-fill the server host.

**Templates on Windows:** The client does **not** mount SMB drives. It uses **UNC paths** (e.g. `\\volumio\Internal Storage\peppy_screensaver\templates`). Ensure Volumio SMB is enabled and the share is reachable from Windows (same network, firewall allows SMB). You can also choose “local” in the wizard and point to a folder on your PC.

**Running the client on Windows:**

```powershell
# From the install folder (e.g. %USERPROFILE%\peppy_remote)
.\peppy_remote.cmd
.\peppy_remote.cmd --windowed
.\peppy_remote.cmd --config
.\peppy_remote.cmd --server volumio
.\peppy_remote.cmd --test
.\peppy_remote.cmd --config-text
```

Or double-click **PeppyMeter Remote** (or **PeppyMeter Remote (Configure)**) on the Desktop or in Start Menu if shortcuts were created.

**Uninstall (Windows):** From the install folder run `.\uninstall.ps1`. It removes the install directory and Desktop/Start Menu shortcuts. To uninstall from another location: `.\uninstall.ps1 -Dir "C:\Users\You\peppy_remote"`. Python and Git are **not** removed.

## Usage

After installation, run (on Linux use `~/peppy_remote/peppy_remote`; on Windows use `.\peppy_remote.cmd` from the install folder, e.g. `%USERPROFILE%\peppy_remote`):

```bash
# Auto-discover server on network
~/peppy_remote/peppy_remote

# Connect to specific server
~/peppy_remote/peppy_remote --server volumio
~/peppy_remote/peppy_remote --server 192.168.1.100

# Simple test display (VU bars only, no full PeppyMeter)
~/peppy_remote/peppy_remote --test

# Interactive configuration wizard (GUI when display available)
~/peppy_remote/peppy_remote --config

# Configuration wizard in terminal (text) only
~/peppy_remote/peppy_remote --config-text
```

To exit the meter, press **ESC** or **Q**, or click/touch the screen.

## Configuration

### Setup Wizard (Recommended)

Run the configuration wizard for easy setup:

```bash
~/peppy_remote/peppy_remote --config
```

- **With a display** (desktop): Opens a **GUI wizard** (requires `python3-tk`, installed by the installer). Steps: Welcome → Choose server (auto-discover, hostname, or IP) → Display mode → Template sources (SMB or local paths) → Spectrum decay → Logger/debug (level, trace_spectrum, trace_network, trace_config) → Save & Run or Save & Exit.
- **Without a display** (e.g. SSH): Falls back to the **terminal (text) wizard** in the same session.
- **Terminal-only wizard:** Use `--config-text` to force the text-based wizard and skip the GUI:

```bash
~/peppy_remote/peppy_remote --config-text
```

On first run, if you launch from a desktop shortcut, the GUI wizard opens automatically.

The wizard configures:
- Server (auto-discover, enter hostname, or enter IP; after discovery you can choose “Use hostname” or “Use IP address” for the selected server)
- Display mode (windowed, fullscreen)
- Template sources (SMB mount or local paths with optional Browse)
- Spectrum decay rate
- Debug level and trace options

Settings are saved to `~/peppy_remote/config.json` and persist between runs.

### Display Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Windowed** | Normal window with title bar, movable/resizable | Desktop use, testing |
| **Frameless** | No window decorations, fixed position | Kiosk displays, embedded |
| **Fullscreen** | Full screen on selected monitor | Dedicated displays |

Command-line overrides:
```bash
~/peppy_remote/peppy_remote --windowed      # Movable window
~/peppy_remote/peppy_remote --fullscreen    # Full screen
```

### Configuration File

Settings are stored in `~/peppy_remote/config.json`:

```json
{
  "server": {
    "host": null,
    "level_port": 5580,
    "spectrum_port": 5581,
    "volumio_port": 3000,
    "discovery_port": 5579,
    "discovery_timeout": 10
  },
  "display": {
    "windowed": true,
    "position": null,
    "fullscreen": false,
    "monitor": 0
  },
  "templates": {
    "use_smb": true,
    "local_path": null,
    "spectrum_local_path": null
  },
  "spectrum": {
    "decay_rate": 0.95
  },
  "debug": {
    "level": "off",
    "trace_spectrum": false,
    "trace_network": false,
    "trace_config": false
  }
}
```

**Note:** Persist countdown (duration and freeze/countdown mode) is supplied by the server in the config response; it is not stored in `config.json`.

### Configuration Options

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| server | host | null | Server hostname/IP (null = auto-discover) |
| server | level_port | 5580 | UDP port for meter level data |
| server | spectrum_port | 5581 | UDP port for spectrum FFT data |
| templates | use_smb | true | Mount templates from server via SMB |
| templates | local_path | null | Local meter templates path |
| templates | spectrum_local_path | null | Local spectrum templates path |
| spectrum | decay_rate | 0.95 | Bar decay per frame (0.85=fast, 0.98=slow) |
| debug | level | "off" | Debug level: off/basic/verbose/trace |
| debug | trace_spectrum | false | Log per-packet spectrum data |
| debug | trace_network | false | Log network connection details |
| debug | trace_config | false | Log config fetch and theme/sync decisions |

Command-line arguments override config file settings:

```bash
# Debugging
~/peppy_remote/peppy_remote --debug verbose
~/peppy_remote/peppy_remote --debug trace --trace-spectrum

# Spectrum tuning
~/peppy_remote/peppy_remote --decay-rate 0.97

# Local templates (skip SMB)
~/peppy_remote/peppy_remote --templates /path/to/templates --spectrum-templates /path/to/spectrum
```

## Requirements

- **Linux**: Debian-based (Ubuntu, Raspberry Pi OS, etc.)
- **Windows** (optional): Windows 10/11, Python 3.8+, Git; templates use UNC paths (no SMB mount)
- Network access to Volumio box
- Volumio must have PeppyMeter plugin with "Remote Display Server" enabled
- **Linux GUI wizard**: `python3-tk` (installed automatically by the install script on desktop systems)

## Network Ports

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 5579 | UDP | Server → Client | Discovery broadcasts |
| 5580 | UDP | Server → Client | Audio level data (~30/sec) |
| 5581 | UDP | Server → Client | Spectrum FFT data (~30/sec) |
| 3000 | TCP | Client → Server | Volumio socket.io (metadata) |
| 445 | TCP | Client → Server | SMB (templates) |

## How It Works

1. **Discovery**: Client listens for UDP broadcasts from PeppyMeter server (port 5579)
   - Discovery packets include `config_version` (for config change detection) and `active_meter` (protocol v3: server’s current theme for random-meter sync).
2. **Startup / syncing**: Before the meter starts, the client shows a “Waiting for data from server” / “Please wait a moment.” screen until the first UDP announcement is received (or a 10 s timeout). That ensures the client knows the server’s current theme (including when the server uses random meter) before drawing.
3. **Config**: Fetches `config.txt` from server via HTTP (Volumio plugin API)
   - Uses direct IP address from discovery for reliable connectivity
   - Endpoint: `/api/v1/pluginEndpoint?endpoint=peppy_screensaver&method=getRemoteConfig`
   - Persist countdown settings (duration, freeze vs countdown) are taken from the server and used when playback stops.
4. **Templates**: Mounts template skins from server via SMB (or uses local/UNC paths if configured).
5. **Audio Levels**: Receives real-time level data via UDP (port 5580).
6. **Spectrum Data**: Receives FFT frequency bins via UDP (port 5581) for spectrum visualizations.
7. **Metadata**: Connects to Volumio socket.io (port 3000) for track info, album art, playback state.
8. **Rendering**: Uses full Volumio PeppyMeter code (turntable, cassette, meters, spectrum, indicators).
9. **Config/theme reload**: When the server sends a config or theme change (e.g. new `config_version` or `active_meter`), the client reloads only if the **theme folder** or **theme name** would actually change. If the current display already matches (same folder and same theme), the client continues without restarting the meter.

## Installation Structure

After installation the directory looks like this (Linux; on Windows the launcher is `peppy_remote.cmd` / `peppy_remote.ps1` and the uninstall script is `uninstall.ps1`):

```
~/peppy_remote/
├── peppy_remote          # Launcher script (Linux; runs peppy_remote.py via venv)
├── peppy_remote.py       # Main client
├── uninstall.sh          # Uninstall script (Linux)
├── peppy_remote.svg      # App icon (desktop launchers)
├── peppy_remote_config.svg
├── config.json           # Client configuration
├── screensaver/          # Mirrors Volumio plugin structure
│   ├── peppymeter/       # PeppyMeter base engine (git clone)
│   │   ├── peppymeter.py
│   │   ├── configfileparser.py
│   │   ├── meter.py, needle.py, etc.
│   │   └── ...
│   ├── spectrum/         # PeppySpectrum engine (git clone)
│   │   ├── spectrum.py
│   │   ├── spectrumutil.py
│   │   ├── spectrumconfigparser.py
│   │   └── ...
│   ├── volumio_peppymeter.py   # Volumio main handler
│   ├── volumio_configfileparser.py
│   ├── volumio_turntable.py    # Turntable/vinyl animations
│   ├── volumio_cassette.py     # Cassette deck animations
│   ├── volumio_compositor.py   # Layer compositing
│   ├── volumio_indicators.py   # Volume/mute/shuffle icons
│   ├── volumio_spectrum.py     # Spectrum integration
│   ├── volumio_basic.py        # Basic display handler
│   ├── screensaverspectrum.py
│   ├── fonts/            # Bundled fonts for meter/theme text (see Fonts below)
│   └── format-icons/     # Track type icons (SVG)
├── mnt/                  # SMB mount for templates (Linux)
├── venv/                 # Python virtual environment
└── config.json
```

## Fonts

The installer downloads a full set of fonts to `screensaver/fonts/` so the meter and themes render correctly without relying on the server or system fonts. Bundled families include **DSEG7** (LCD-style digits), **Lato**, **Gibson**, **Font Awesome**, **Material Icons**, **Material Design Icons**, and **Glyphicons Halflings**. Themes and handlers reference these from the screensaver path.

## Format Icons

The client displays format icons (FLAC, MP3, Tidal, Qobuz, etc.) for the currently playing track.

**How it works:**
1. The installer downloads common format icons to `screensaver/format-icons/`
2. At startup, the client checks for any missing icons
3. Missing icons are fetched from Volumio server (`/app/assets-common/format-icons/`) and cached locally
4. Handler files are patched to check local icons first for all formats

**Bundled icons:**
`aac`, `aiff`, `airplay`, `alac`, `bt`, `cd`, `dab`, `dsd`, `dts`, `flac`, `fm`, `m4a`, `mp3`, `mp4`, `mqa`, `ogg`, `opus`, `qobuz`, `radio`, `rr`, `spotify`, `tidal`, `wav`, `wavpack`, `wma`, `youtube`

If a format icon isn't available locally or on the server, the format name is displayed as text.

## Server Setup

On your Volumio box:

1. Go to plugin settings for PeppyMeter
2. Enable "Remote Display Server"
3. Choose server mode:
   - **Server Only**: Headless, only streams data (no local display)
   - **Server + Local**: Streams data AND shows local display
4. Save settings

## Uninstall

**Linux:**

```bash
~/peppy_remote/uninstall.sh
```

Removes: install directory, sudoers entry for mount, desktop launchers. System packages are NOT removed.

**Windows:** Run from the install folder (or pass `-Dir`):

```powershell
.\uninstall.ps1
```

Removes: install directory, Desktop and Start Menu shortcuts. Python and Git are NOT removed.

## Troubleshooting

**No servers found:**
- Check that PeppyMeter is running on Volumio
- Check that "Remote Display Server" is enabled
- Verify network connectivity: `ping volumio.local`
- Try manual server: `peppy_remote --server <ip_address>`

**SMB mount fails (templates):**
- Ensure cifs-utils is installed
- Check Volumio SMB is accessible: `smbclient -L //volumio.local -N`
- Verify sudoers entry exists: `cat /etc/sudoers.d/peppy_remote`
- Note: Config is fetched via HTTP, only templates use SMB

**Config fetch fails:**
- Ensure Volumio plugin is installed and running
- Test endpoint manually: `curl "http://<server_ip>:3000/api/v1/pluginEndpoint?endpoint=peppy_screensaver&method=getRemoteConfig"`
- Check Volumio logs for plugin errors

**No audio levels:**
- Check server is broadcasting: `nc -ul 5580`
- Verify music is playing on Volumio
- Check firewall allows UDP 5580

**No spectrum data:**
- Check server is broadcasting spectrum: `nc -ul 5581`
- Verify spectrum is enabled in PeppyMeter settings
- Check firewall allows UDP 5581
- Spectrum requires peppyalsa to be running on server

**Import errors:**
- Verify screensaver directory exists: `ls ~/peppy_remote/screensaver/`
- Check volumio_*.py files downloaded: `ls ~/peppy_remote/screensaver/volumio_*.py`
- Check PeppyMeter cloned: `ls ~/peppy_remote/screensaver/peppymeter/`

**Theme restarts or flicker when server hasn’t changed:**
- The client only reloads when the **theme folder** or **theme name** would change. If you see “Config/folder+theme unchanged, continuing.” in the log, the meter correctly did not restart. If restarts still happen, check that the server plugin is up to date (protocol v3 with `active_meter` in discovery).

**Display issues ("windows not available"):**
- Ensure DISPLAY environment variable is set: `echo $DISPLAY`
- Check X11 is running: `xdpyinfo`
- Try: `export DISPLAY=:0` before running

**Config wizard opens in terminal instead of GUI (Linux):**
- Install `python3-tk`: `sudo apt install python3-tk`
- Ensure you have a display (not SSH without X forwarding). To force the text wizard: `~/peppy_remote/peppy_remote --config-text`

**Windows – Dependencies not found after winget install:**
- Close this PowerShell window, open a **new** PowerShell, then run the install script again so the updated PATH (Python/Git) is visible.
- If winget is missing, install [App Installer](https://apps.microsoft.com/store/detail/app-installer/9NBLGGH4NNS1) from Microsoft Store (includes winget on Windows 11), or install Python and Git manually from [python.org](https://www.python.org/downloads/) and [git-scm.com](https://git-scm.com/download/win).

**Windows – Execution policy / script won’t run:**
- Run: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
- Or download `install.ps1` and run: `powershell -ExecutionPolicy Bypass -File install.ps1`

**Windows – Templates / meter skins not loading:**
- On Windows the client uses **UNC paths** to the Volumio SMB share (no local mount). Ensure Volumio SMB is enabled and the share is reachable: in File Explorer try `\\volumio\Internal Storage` or `\\<volumio_ip>\Internal Storage`.
- If UNC fails, use the config wizard and choose **local** templates; copy `Internal Storage\peppy_screensaver\templates` (and `templates_spectrum`) from the server to a folder on your PC and point the wizard to that folder.
- Check Windows Firewall allows SMB (File and Printer Sharing) for the relevant network profile.

**Windows – “No servers found”:**
- Ensure the Volumio machine and Windows PC are on the same network. Try: `ping volumio.local` or `ping <volumio_ip>`.
- Run with a fixed server: `.\peppy_remote.cmd --server <volumio_ip_or_hostname>`.
- Windows Firewall may block UDP discovery; allow the client (or Python) for Private networks if needed.
