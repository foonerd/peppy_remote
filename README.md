# PeppyMeter Remote Client

Remote display client for the [PeppyMeter Screensaver](https://github.com/foonerd/peppy_screensaver) Volumio plugin. Display PeppyMeter visualizations on any Debian-based system (or Windows) by connecting to a Volumio server running the plugin.

This client uses the **same rendering code** as the Volumio plugin (turntable, cassette, meters) but receives audio data over the network. It waits for the server's first announcement before starting the meter (syncing screen). By default it syncs to the server's theme (including random meter) and only reloads when the theme folder or theme name actually changes. You can optionally **lock this client to a fixed theme** (kiosk mode) so it always shows one template folder and meter, ignoring server theme changes.

**Features:** Full meter rendering (turntable, cassette, basic skins), spectrum analyzer, album art and vinyl display, playback indicators (volume, mute, shuffle, repeat, progress), format icons, ticker and scrolling text, time display, persist countdown during pause. Auto-discovery, config wizard, SMB or local templates, windowed/frameless/fullscreen modes.

**Version:** Client version is aligned with [PeppyMeter Screensaver](https://github.com/foonerd/peppy_screensaver) (e.g. 3.3.2). Use the same version for best compatibility. Run `peppy_remote --version` to see the installed version.

### Version compatibility and startup

- **Authoritative check:** The client compares its release with the **PeppyMeter Screensaver** version returned by Volumio over **HTTP** (`getRemoteConfig`). UDP discovery may include a `plugin_version` field from broadcasts on your LAN, but it is **not** used for compatibility decisions—only a successful HTTP response from the server you selected counts.
- **Wait for Volumio:** After you pick a server (discovery or `--server`), the client retries HTTP every few seconds for up to **120 seconds** by default (see `--server-wait-timeout`). While waiting, a pygame window may show "Waiting for server" (or console messages if pygame is unavailable).
- **Semver match:** If the server plugin is too old, does not advertise a version, or the release does not match the client, a **blocking** message explains the problem; the client then exits. Use **`--skip-version-check`** only if you understand the risk (e.g. temporary testing).
- **Server logs:** When the remote sends control messages with **`client_version`**, the plugin can log comparisons at **Verbose** debug (see PeppyMeter Screensaver debug settings).

## Quick Install

One-liner installation (both repos from `main` branch):

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.sh | bash
```

With server pre-configured:

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.sh | bash -s -- --server volumio
```

With custom install directory:

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.sh | bash -s -- --dir /opt/peppy_remote
```

Or set `PEPPY_REMOTE_DIR` before running (e.g. `PEPPY_REMOTE_DIR=/opt/peppy_remote curl ... | bash`).

## Testing from experimental branch

> **This section is for testers.** If you were asked to install from the experimental branch, follow these instructions exactly. Do not mix with the Quick Install instructions above.

The installer downloads files from two GitHub repos:

| Repo | What it provides |
|------|-----------------|
| **peppy_remote** | Client script, fonts, format icons, installer itself |
| **peppy_screensaver** | Volumio handlers (turntable, cassette, basic, spectrum, etc.) |

When testing experimental code, **both** the installer script and the downloaded content must come from the experimental branch. The installer on `main` may not have the latest flags or fixes.

### Linux - experimental

The curl URL points to the experimental branch so you get the latest installer. The `--both experimental` flag tells the installer to download all content files from experimental too:

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/experimental/install.sh | bash -s -- --both experimental
```

With server pre-configured:

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/experimental/install.sh | bash -s -- --both experimental --server volumio
```

### Windows - experimental

The PowerShell one-liner (`irm ... | iex`) **cannot pass arguments**. You must download the installer first, then run it.

**Step 1:** Download the installer from the experimental branch:

```powershell
irm https://raw.githubusercontent.com/foonerd/peppy_remote/experimental/install.ps1 -OutFile install.ps1
```

**Step 2:** Run it:

```powershell
.\install.ps1 -Both experimental
```

With server pre-configured:

```powershell
.\install.ps1 -Both experimental -Server volumio
```

**If PowerShell blocks the script** (execution policy), use Command Prompt instead:

```cmd
powershell -ExecutionPolicy Bypass -File install.ps1 -Both experimental
```

**If the script fails after installing Python/Git via winget**, close ALL PowerShell/Command Prompt windows, open a new one, and run Step 2 again. This is a Windows PATH issue - newly installed programs are not visible until a new terminal session.

### What the banner should show

When the installer runs, the banner shows where files are coming from. For experimental on both repos it should read:

```
Remote files:  experimental (peppy_remote)
Handler files: experimental (peppy_screensaver)
```

If you see `main` where you expected `experimental`, the wrong installer or flags were used.

## Branch selection reference

The installer supports three branch flags. By default both repos use `main`.

| Flag (Linux) | Flag (Windows) | What it does |
|--------------|----------------|--------------|
| `--both <branch>` | `-Both <branch>` | Sets both repos to the same branch |
| `--remote-branch <branch>` | `-RemoteBranch <branch>` | Sets peppy_remote repo only |
| `--screensaver-branch <branch>` | `-ScreensaverBranch <branch>` | Sets peppy_screensaver repo only |
| `-b <branch>` | `-b <branch>` | Legacy alias for `--both`/`-Both` |

**Rules:**
- `--both`/`-b` cannot be combined with `--remote-branch` or `--screensaver-branch`. The installer will error.
- `--remote-branch` and `--screensaver-branch` can be used together for mixed scenarios.

**Examples - Linux:**

```bash
# Both repos from main (default)
curl -sSL .../main/install.sh | bash

# Both repos from experimental
curl -sSL .../experimental/install.sh | bash -s -- --both experimental

# Only screensaver handlers from experimental (remote files from main)
curl -sSL .../main/install.sh | bash -s -- --screensaver-branch experimental

# Only remote files from experimental (handlers from main)
curl -sSL .../experimental/install.sh | bash -s -- --remote-branch experimental

# Both set independently (equivalent to --both experimental)
curl -sSL .../experimental/install.sh | bash -s -- --remote-branch experimental --screensaver-branch experimental
```

**Examples - Windows** (download installer first, then run):

```powershell
.\install.ps1                                              # both repos from main
.\install.ps1 -Both experimental                           # both repos from experimental
.\install.ps1 -ScreensaverBranch experimental              # screensaver only from experimental
.\install.ps1 -RemoteBranch experimental                   # remote only from experimental
.\install.ps1 -RemoteBranch experimental -ScreensaverBranch experimental  # both explicit
```

> **Important:** When using `--remote-branch` to get remote files from a non-main branch, download the installer from that same branch. The installer script itself is a peppy_remote file - if you download it from `main` but ask for `--remote-branch experimental`, you get the old installer code with the new content files, which may not work as expected.

## Linux installer details

**What the Linux installer does:**

1. **Dependencies:** Installs `python3` (3.12+ required; must match server), `python3-pip`, `python3-venv`, `python3-tk`, `git`, `cifs-utils`, and SDL2 packages (libsdl2-2.0-0, libsdl2-ttf, libsdl2-image, libsdl2-mixer).
2. **Directory:** Creates the install folder (default `~/peppy_remote`). If it exists, asks whether to remove and reinstall.
3. **Downloads:** Fetches `peppy_remote.py`, `uninstall.sh`, and SVG icons from the peppy_remote repo branch.
4. **Repos:** Clones PeppyMeter and PeppySpectrum via Git into `screensaver/peppymeter` and `screensaver/spectrum`.
5. **Volumio handlers:** Downloads Volumio custom handlers (turntable, cassette, spectrum, etc.) from the peppy_screensaver repo branch into `screensaver/`.
6. **Fonts:** Downloads bundled fonts to `screensaver/fonts/` so themes render correctly.
7. **Patches:** Patches handler files to use local format icons first for all formats.
8. **Python env:** Creates a virtual environment in `venv/` and installs required packages (pygame, pillow, python-socketio, cairosvg, etc.).
9. **Launcher:** Creates `peppy_remote` script that activates venv, sets PYTHONPATH, and runs `peppy_remote.py`.
10. **Sudoers:** Creates `/etc/sudoers.d/peppy_remote` for passwordless SMB mount/umount.
11. **Config:** Writes `config.json`; use `--server` to pre-fill the server host.
12. **Desktop shortcuts:** If `~/.local/share/applications` exists, creates **PeppyMeter Remote** (start client) and **PeppyMeter Remote (Configure)** (wrench icon, opens setup wizard).

## Windows installer details

**Prerequisites:** Windows 10 or 11. The installer needs **Python 3.12+** and **Git** (Python version must match the server; Volumio uses 3.12). If either is missing, the script will check, list what's missing, and ask: *"Install missing dependencies via winget? [Y/n]"*. Answer **Y** (or press Enter) to install via **winget** (Windows Package Manager); you may see a UAC prompt. If you answer **n**, the script exits with manual install links. If **winget** is not available, install Python and Git manually, then run the installer again.

**First-time PowerShell:** You may need to allow script execution once:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**One-liner install** (both repos from `main`, no branch options):

```powershell
irm https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.ps1 | iex
```

> The one-liner cannot pass arguments. For branch selection, server config, or any other options, download the installer first then run it (see below).

**With options** (download the script first, then run with parameters):

```powershell
irm https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.ps1 -OutFile install.ps1
.\install.ps1 -Server volumio
.\install.ps1 -Dir C:\peppy_remote
.\install.ps1 -Both experimental
.\install.ps1 -RemoteBranch experimental
.\install.ps1 -ScreensaverBranch experimental
.\install.ps1 -Help
```

**What the installer does:**

1. **Dependencies:** Checks for Python 3.12+ and Git. Python version must match the server. If missing, prompts to install via winget (Python.Python.3.12, Git.Git). After winget installs, it refreshes PATH and re-checks; if still not visible, it asks you to close and reopen PowerShell and run the script again.
2. **Directory:** Creates the install folder (default: `%USERPROFILE%\peppy_remote`, e.g. `C:\Users\YourName\peppy_remote`). If the folder already exists, it asks whether to remove and reinstall.
3. **Downloads:** Fetches `peppy_remote.py`, `uninstall.ps1`, and SVG icons from the peppy_remote repo branch.
4. **Repos:** Clones PeppyMeter and PeppySpectrum via Git into `screensaver\peppymeter` and `screensaver\spectrum`. Volumio handlers are downloaded from the peppy_screensaver repo branch.
5. **Volumio handlers:** Downloads Volumio custom handlers (turntable, cassette, spectrum, etc.) and format icons into `screensaver\`.
6. **Python env:** Creates a virtual environment in `venv\` and installs required packages (pygame, pillow, python-socketio, etc.).
7. **Cairo runtime:** If the Cairo C library is not already available, the installer downloads a standalone Cairo DLL (from [preshing/cairo-windows](https://github.com/preshing/cairo-windows)) into `cairo\` and configures the launchers so the client finds it. Required for the full meter (cassette, turntable, basic skins).
8. **Launchers:** Creates `peppy_remote.cmd` and `peppy_remote.ps1` (PYTHONPATH, PYTHONUTF8=1 for UTF-8, and if Cairo was installed, PATH for `cairo\`). Optional Desktop and Start Menu shortcuts: **PeppyMeter Remote** (runs client) and **PeppyMeter Remote (Configure)** (opens setup wizard).
9. **Config:** Writes `config.json`; use `-Server` to pre-fill the server host.

**UTF-8 on Windows:** The installer and launchers force UTF-8 (PowerShell output encoding during install; `PYTHONUTF8=1` when running the client) so file and console encoding match Linux and avoid cp950/cp1252 issues. For system-wide UTF-8: Settings > Time & language > Language & region > Administrative language settings > Change system locale > check "Beta: Use Unicode UTF-8 for worldwide language support".

**Templates on Windows:** The client does **not** mount SMB drives. It uses **UNC paths** (e.g. `\\volumio\Internal Storage\peppy_screensaver\templates`). Ensure Volumio SMB is enabled and the share is reachable from Windows (same network, firewall allows SMB). In the wizard, when you choose SMB, use **Mount now** to test the UNC path; on success the Meter theme step will list server themes. You can also choose "local" in the wizard and point to a folder on your PC.

**Cairo on Windows:** The installer installs a Cairo runtime when needed. If you still see "no library called cairo" or "cannot load library libcairo-2.dll", see Troubleshooting for manual options (GTK3 Runtime or MSYS2).

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

After installation, run the client (on Linux use `~/peppy_remote/peppy_remote` or your install path if you used `--dir`; on Windows use `.\peppy_remote.cmd` from the install folder, e.g. `%USERPROFILE%\peppy_remote`):

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

- **With a display** (desktop): Opens a **GUI wizard** (requires `python3-tk`, installed by the installer). Steps: Welcome → Choose server (auto-discover, hostname, or IP) → Display mode → Template sources (SMB or local paths; **Mount now** to connect so the next step can list server themes — Linux: mount SMB; Windows: test UNC) → **Meter theme** (use server theme or fixed/kiosk: folder + fixed meter, random from folder, or random from list) → Spectrum decay → Logger/debug (level, trace_spectrum, trace_network, trace_config) → Save & Run or Save & Exit.
- **Without a display** (e.g. SSH): Falls back to the **terminal (text) wizard** in the same session.
- **Terminal-only wizard:** Use `--config-text` to force the text-based wizard and skip the GUI:

```bash
~/peppy_remote/peppy_remote --config-text
```

On first run, if you launch from a desktop shortcut, the GUI wizard opens automatically.

The wizard configures:
- Server (auto-discover, enter hostname, or enter IP; after discovery you can choose "Use hostname" or "Use IP address" for the selected server)
- Display mode (windowed, fullscreen)
- Template sources (SMB or local paths with optional Browse; **Mount now** on both Linux and Windows to connect and list server themes in the next step)
- **Meter theme**: Use server theme (follow Volumio) or lock to a fixed theme (kiosk): choose template folder, then fixed (one meter), random from folder, or random from list
- Spectrum decay rate
- Debug level and trace options

Settings are saved to `~/peppy_remote/config.json` and persist between runs.

### Fixed theme (kiosk)

To lock this client to a specific meter theme (e.g. one display always showing the same skin), set **Meter theme** in the wizard to something other than "Use server theme", or edit `config.json` and set both `display.meter_folder` (template folder name, e.g. `1920x720_5skins`) and `display.meter` (section name from that folder's `meters.txt`, or `"random"` for random from that folder, or a comma-separated list for random from that list). When both are set, the client ignores server theme changes and never reloads for theme updates. Leave both `null` to follow the server theme.

### Display Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Windowed** | Normal window with title bar, movable/resizable | Desktop use, testing |
| **Frameless** | No window decorations, fixed position | Kiosk displays, embedded |
| **Fullscreen** | Full screen on selected monitor | Dedicated displays |

**Windowed** and **Fullscreen** are set via the wizard or `--windowed` / `--fullscreen`. **Frameless** is set by editing `config.json`: set `display.windowed` to `false` and `display.fullscreen` to `false` and optionally set `display.position` to `[x, y]`.

Command-line overrides:
```bash
~/peppy_remote/peppy_remote --windowed      # Movable window
~/peppy_remote/peppy_remote --fullscreen   # Full screen
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
    "monitor": 0,
    "meter_folder": null,
    "meter": null
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
| server | volumio_port | 3000 | Volumio socket.io port |
| server | discovery_port | 5579 | UDP port for discovery broadcasts |
| server | discovery_timeout | 10 | Seconds to wait for discovery before timeout |
| display | windowed | true | Movable window with title bar |
| display | fullscreen | false | Full screen mode |
| display | position | null | Window position [x, y] or null (centered) |
| display | monitor | 0 | Monitor index for fullscreen |
| display | meter_folder | null | Kiosk: template folder (e.g. `1920x720_5skins`); null = use server theme |
| display | meter | null | Kiosk: meter section name, `"random"`, or comma-separated list; null = use server theme |
| templates | use_smb | true | Templates from server (Linux: SMB mount; Windows: UNC paths). Use **Mount now** in the wizard to list server themes. |
| templates | local_path | null | Local meter templates path |
| templates | spectrum_local_path | null | Local spectrum templates path |
| spectrum | decay_rate | 0.95 | Bar decay per frame (0.85=fast, 0.98=slow) |
| debug | level | "off" | Debug level: off/basic/verbose/trace |
| debug | trace_spectrum | false | Log per-packet spectrum data |
| debug | trace_network | false | Log network connection details |
| debug | trace_config | false | Log config fetch and theme/sync decisions |

**CLI-only (not in `config.json`):** `--skip-version-check`, `--server-wait-timeout <seconds>` (default 120; minimum effective wait 5 s). Used during the HTTP version check phase before the meter starts.

Command-line arguments override config file settings:

```bash
# Server and ports
~/peppy_remote/peppy_remote --server volumio
~/peppy_remote/peppy_remote --level-port 5580 --spectrum-port 5581 --volumio-port 3000
~/peppy_remote/peppy_remote --discovery-timeout 15

# Display
~/peppy_remote/peppy_remote --windowed
~/peppy_remote/peppy_remote --fullscreen

# Templates (skip SMB mount)
~/peppy_remote/peppy_remote --no-mount --templates /path/to/templates --spectrum-templates /path/to/spectrum

# Debugging
~/peppy_remote/peppy_remote --debug verbose
~/peppy_remote/peppy_remote --debug trace --trace-spectrum --trace-network --trace-config

# Spectrum tuning
~/peppy_remote/peppy_remote --decay-rate 0.97

# Version / HTTP wait (defaults: wait up to 120s for Volumio HTTP before failing)
~/peppy_remote/peppy_remote --server-wait-timeout 180
~/peppy_remote/peppy_remote --skip-version-check   # not recommended for normal use
```

## Requirements

- **Linux**: Debian-based (Ubuntu, Raspberry Pi OS, etc.). **Python 3.12+** required; version must match the server (Volumio uses 3.12). The client can run on a Pi as a remote display; use windowed or fullscreen and avoid heavy spectrum templates if CPU is limited.
- **Windows**: Windows 10/11, **Python 3.12+**, Git; templates use UNC paths (no SMB mount)
- Network access to Volumio box
- Volumio must have [PeppyMeter Screensaver](https://github.com/foonerd/peppy_screensaver) plugin **v3.3.0 or higher** with "Remote Display Server" enabled (protocol v3 with `active_meter` in discovery). For best compatibility use the same version as the plugin (e.g. client 3.3.2 with PeppyMeter Screensaver 3.3.2).
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
   - Discovery packets include `config_version` (for config change detection), `active_meter` (protocol v3: server's current theme for random-meter sync), and may include **`plugin_version`** (informational only; not used for compatibility—see [Version compatibility and startup](#version-compatibility-and-startup)).
2. **HTTP and version check**: Before starting the full client, the client waits for Volumio to answer HTTP **`getRemoteConfig`** on the chosen server and compares the plugin's advertised release with this client (unless **`--skip-version-check`**). Timeout and retry behavior are controlled by **`--server-wait-timeout`** (default 120 s).
3. **Startup / syncing**: After the version check passes, the client shows a "Waiting for data from server" / "Please wait a moment." screen until the first UDP announcement is received (or a 10 s timeout). That ensures the client knows the server's current theme (including when the server uses random meter) before drawing.
4. **Config**: Fetches `config.txt` from server via HTTP (Volumio plugin API)
   - Uses direct IP address from discovery for reliable connectivity
   - Endpoint: `/api/v1/pluginEndpoint?endpoint=peppy_screensaver&method=getRemoteConfig`
   - Persist countdown settings (duration, freeze vs countdown) are taken from the server and used when playback stops.
5. **Templates**: Mounts template skins from server via SMB (or uses local/UNC paths if configured).
6. **Audio Levels**: Receives real-time level data via UDP (port 5580).
7. **Spectrum Data**: Receives FFT frequency bins via UDP (port 5581) for spectrum visualizations.
8. **Metadata**: Connects to Volumio socket.io (port 3000) for track info, album art URLs, playback state.
9. **Album art and vinyl**: Album art is fetched via HTTP from Volumio (or from local paths when using SMB templates). Vinyl images use the server's `getVinylImage` endpoint when configured.
10. **Rendering**: Uses full Volumio PeppyMeter code (turntable, cassette, meters, spectrum, indicators).
11. **Config/theme reload**: When the server sends a config or theme change (e.g. new `config_version` or `active_meter`), the client reloads only if the **theme folder** or **theme name** would actually change. If the current display already matches (same folder and same theme), the client continues without restarting the meter. If you set **meter theme** (kiosk) in config (`display.meter_folder` and `display.meter`), this client always uses that fixed theme and ignores server theme changes; reload checks use the override so the meter does not restart for server theme updates.

For server random-meter sync, discovery `active_meter` is treated as the runtime authority. The fetched `config.txt` may still contain `meter=random`; that value is config intent, not the immediate runtime meter. This prevents random-meter flip-flop loops during reload decisions.

## Installation Structure

The installer downloads files from two repos. Client scripts, fonts, and icons come from [peppy_remote](https://github.com/foonerd/peppy_remote). Volumio handlers and format icons come from [peppy_screensaver](https://github.com/foonerd/peppy_screensaver). Both default to the `main` branch; use `--both`/`-Both` to set both to another branch, or `--remote-branch`/`--screensaver-branch` (`-RemoteBranch`/`-ScreensaverBranch` on Windows) to set each independently. See [Branch selection reference](#branch-selection-reference) for full details. PeppyMeter and PeppySpectrum are cloned from [foonerd/PeppyMeter](https://github.com/foonerd/PeppyMeter) and [foonerd/PeppySpectrum](https://github.com/foonerd/PeppySpectrum).

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

On your Volumio box (requires [PeppyMeter Screensaver](https://github.com/foonerd/peppy_screensaver) plugin **v3.3.0 or higher**):

1. Go to **Settings > Plugins > PeppyMeter Screensaver > Settings**
2. Under **Remote Display Settings**, enable **Enable Remote Server**
3. Choose **Server Mode**:
   - **Server Only**: Headless — no local display, only streams data. Use when the Volumio box has no display (e.g. dedicated server). Saves CPU and avoids spectrum pipe contention.
   - **Server + Local**: Streams data AND shows visualization locally (default)
4. Optionally adjust **Level Port** (5580), **Discovery Port** (5579), and **Config Sync Interval** (default 1 s). Config sync controls how quickly clients detect config/theme changes.
5. Save settings

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

**Version mismatch / "server plugin is too old" / client exits after wait:**
- Match **peppy_remote** to the **PeppyMeter Screensaver** release on Volumio (same semver, e.g. 3.3.2 with 3.3.2).
- Ensure Volumio is up and the plugin is enabled so HTTP `getRemoteConfig` succeeds; increase **`--server-wait-timeout`** if the device is slow to boot.
- For emergency testing only: **`--skip-version-check`** (expect undefined behavior if versions differ).
- On the server, **Verbose** debug can log remote **`client_version`** vs server release when control messages arrive.

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

**Theme restarts or flicker when server hasn't changed:**
- The client only reloads when the **theme folder** or **theme name** would change. If you see "Config/folder+theme unchanged, continuing." in the log, the meter correctly did not restart. If restarts still happen, check that the server plugin is up to date (protocol v3 with `active_meter` in discovery).

**Server Only mode — EADDRINUSE on restart:**
- In Server Only (headless) mode, the plugin closes UDP sockets and the spectrum pipe on shutdown so the next start can bind cleanly. If you see "Address already in use" after a restart, ensure the previous PeppyMeter process has fully exited (check `ps` for volumio_peppymeter or peppy processes).

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

**Windows – Execution policy / script won't run:**
- Run: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
- Or download `install.ps1` and run: `powershell -ExecutionPolicy Bypass -File install.ps1`

**Windows – Templates / meter skins not loading:**
- On Windows the client uses **UNC paths** to the Volumio SMB share (no local mount). Ensure Volumio SMB is enabled and the share is reachable: in File Explorer try `\\volumio\Internal Storage` or `\\<volumio_ip>\Internal Storage`.
- If UNC fails, use the config wizard and choose **local** templates; copy `Internal Storage\peppy_screensaver\templates` (and `templates_spectrum`) from the server to a folder on your PC and point the wizard to that folder.
- Check Windows Firewall allows SMB (File and Printer Sharing) for the relevant network profile.

**Windows – "No servers found":**
- Ensure the Volumio machine and Windows PC are on the same network. Try: `ping volumio.local` or `ping <volumio_ip>`.
- Run with a fixed server: `.\peppy_remote.cmd --server <volumio_ip_or_hostname>`.
- Windows Firewall may block UDP discovery; allow the client (or Python) for Private networks if needed.

**Windows - no library called cairo / OSError cairocffi / Falling back to test display:**
- The installer normally installs a Cairo runtime into `cairo\` and sets PATH in the launcher. If you still see this error, the install step may have failed (e.g. network or archive layout). You can install Cairo manually:
- Option 1: Install **GTK3 Runtime** (includes Cairo). Download from [GTK for Windows Runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases), run the installer, and ensure the GTK bin folder is on your system PATH.
- Option 2: With **MSYS2**, run: `pacman -S mingw-w64-ucrt-x86_64-cairo` (or the 32-bit variant), then add the MSYS2 bin folder to PATH when running the client.
- After installing Cairo, run `peppy_remote.cmd` again; the full meter should start instead of falling back to test display.
