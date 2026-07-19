# Android install guide (Pydroid 3)

Step-by-step guide to run **PeppyMeter Remote** on a phone or tablet using Pydroid 3.

This is **experimental**. You use the **same** `peppy_remote.py` as on Linux/Windows (not a separate Android app).  
Pydroid bring-up was proven by **Lee.Yan** — thank you.

---

## Before you start

You need:

1. A Volumio system with [PeppyMeter Screensaver](https://github.com/foonerd/peppy_screensaver) installed, **Remote Display Server** enabled, and the **same version** as the remote client (for example both **3.4.4**).
2. Phone or tablet on the **same Wi‑Fi** as Volumio.
3. A Windows or Linux PC — you will install peppy_remote from the **Android feature branch** on that PC, then copy files to the tablet.
4. A USB file-transfer cable or another way to copy folders to the tablet (Files app, SMB, etc.).
5. Optional but strongly recommended: a **USB keyboard** for typing in Pydroid Terminal.

---

## Step 0 — Install peppy_remote on the PC (from the Android branch)

Install on Windows or Linux **first**. Use the Android feature branch for the remote client, and a matching released screensaver tree for handlers (usually `main` at **3.4.4**).

| Repo | Branch |
|------|--------|
| peppy_remote (script, `lib/`, installer) | `feature/android-pydroid` |
| peppy_screensaver (handlers under `screensaver/`) | `main` (3.4.4) |

Banner when correct:

```text
Remote files:  feature/android-pydroid (peppy_remote)
Handler files: main (peppy_screensaver)
```

### Linux

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/feature/android-pydroid/install.sh \
  | bash -s -- --remote-branch feature/android-pydroid --screensaver-branch main
```

With server pre-set:

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/feature/android-pydroid/install.sh \
  | bash -s -- --remote-branch feature/android-pydroid --screensaver-branch main --server volumio
```

Default install folder: `~/peppy_remote`.

### Windows

PowerShell cannot pass arguments through `irm ... | iex`. Download the installer from the feature branch, then run it.

**Step A — download installer:**

```powershell
irm https://raw.githubusercontent.com/foonerd/peppy_remote/feature/android-pydroid/install.ps1 -OutFile install.ps1
```

**Step B — run:**

```powershell
.\install.ps1 -RemoteBranch feature/android-pydroid -ScreensaverBranch main
```

With server pre-set:

```powershell
.\install.ps1 -RemoteBranch feature/android-pydroid -ScreensaverBranch main -Server volumio
```

If execution policy blocks the script:

```cmd
powershell -ExecutionPolicy Bypass -File install.ps1 -RemoteBranch feature/android-pydroid -ScreensaverBranch main
```

Default install folder: `%USERPROFILE%\peppy_remote`.

**Note:** If winget just installed Python/Git, close all terminals, open a new one, and run Step B again.

### Optional — if Volumio plugin is still on experimental

Only if your Volumio PeppyMeter Screensaver is running from `experimental` (not the 3.4.4 release):

```bash
# Linux
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/feature/android-pydroid/install.sh \
  | bash -s -- --remote-branch feature/android-pydroid --screensaver-branch experimental
```

```powershell
# Windows (after downloading install.ps1 from feature/android-pydroid)
.\install.ps1 -RemoteBranch feature/android-pydroid -ScreensaverBranch experimental
```

Versions must still match between plugin and client.

### Smoke-test on the PC (optional)

Confirm the PC install works against Volumio before copying to the tablet:

```bash
# Linux
~/peppy_remote/peppy_remote --server volumio
```

```powershell
# Windows
cd $env:USERPROFILE\peppy_remote
.\peppy_remote.cmd --server volumio
```

---

## Step 1 — Install Pydroid on the tablet

1. Open the **Google Play Store**.
2. Install **Pydroid 3**.
3. Install the **Pydroid repository** plugin (required for many pip packages).
4. Open Pydroid 3 once so it can finish setup.

When you later install Python packages, Google may ask you to authorize access — that is normal for Pydroid pip.

---

## Step 2 — Install Python packages in Pydroid

1. On the tablet, open **Pydroid 3**.
2. Open the menu (☰) → **Pip**.
3. Install every package listed below (one by one, or use Pip’s install-from-requirements if you copied `requirements-android.txt` onto the tablet).

Packages:

```text
pillow
cssselect2
tinycss2
defusedxml
webencodings
python-socketio[client]
python-engineio
bidict
requests
certifi
charset-normalizer
idna
urllib3
websocket-client
mss
pyscreenshot
easyprocess
entrypoint2
```

The same list ships in the repo as [`requirements-android.txt`](../requirements-android.txt).

### Important rules

| Do | Do not |
|----|--------|
| Use Pydroid’s built-in **pygame** | `pip install pygame` |
| Leave **cairosvg** uninstalled | Install **cairosvg** |

If you already installed cairosvg by mistake: Pip → uninstall **cairosvg**.

---

## Step 3 — Prepare files on your PC

Use the install from **Step 0**. Open that folder:

- Windows (typical): `%USERPROFILE%\peppy_remote`  
  Example: `C:\Users\YourName\peppy_remote`
- Linux (typical): `~/peppy_remote`

You should see at least:

- `peppy_remote.py`
- `lib\` (folder)
- `screensaver\` (folder)

If `screensaver` is missing, re-run Step 0.

Also prepare your meter skins on the PC:

- A `templates` folder (meter themes)
- A `templates_spectrum` folder (spectrum themes)

You can copy these from Volumio’s share (on Windows often via  
`\\volumio\Internal Storage\peppy_screensaver\...`) or from another machine that already has them.

---

## Step 4 — Copy files to the tablet

Connect the tablet and copy files into **Download**.

### 4a. Create the client folder

Create this folder on the tablet:

```text
Internal storage / Download / peppy_remote
```

Full path (used later in config):

```text
/storage/emulated/0/Download/peppy_remote
```

### 4b. Copy these into `Download/peppy_remote`

| Copy this from the PC | Into tablet |
|----------------------|-------------|
| `peppy_remote.py` | `Download/peppy_remote/peppy_remote.py` |
| entire `lib` folder | `Download/peppy_remote/lib/` |
| entire `screensaver` folder | `Download/peppy_remote/screensaver/` |
| `requirements-android.txt` (optional) | `Download/peppy_remote/` |

`screensaver` is large (often ~100 MB). It must include:

- `volumio_*.py` handlers
- `peppymeter/`
- `spectrum/`
- `fonts/`
- `format-icons/`

### 4c. Do **not** copy

- `venv` (or `venv\`)
- `cairo`
- `mnt`
- `peppy_remote.cmd` / `peppy_remote.ps1` / Linux launcher named `peppy_remote`
- `uninstall.ps1` / `uninstall.sh`
- log files
- `__pycache__` folders
- `.git` folders inside peppymeter/spectrum (if present)

### 4d. Copy templates next to (not inside) peppy_remote

Copy to:

```text
Download/templates/
Download/templates_spectrum/
```

So the tablet looks like this:

```text
Download/
├── peppy_remote/
│   ├── peppy_remote.py
│   ├── lib/
│   └── screensaver/
├── templates/
└── templates_spectrum/
```

`config.json` will appear inside `peppy_remote/` after you configure (next step). You do not need to create it by hand.

---

## Step 5 — Configure (Terminal)

Typing is easier with a **USB keyboard**.

1. Open **Pydroid 3**.
2. Menu → **Terminal**.
3. Run:

```bash
python ./download/peppy_remote/peppy_remote.py --config
```

4. Create or edit a profile.
5. Set the Volumio server (auto-discover, hostname, or IP).
6. When asked for templates, use **absolute** paths (this is important):

```text
/storage/emulated/0/Download/templates
/storage/emulated/0/Download/templates_spectrum
```

7. Finish the wizard and save.

SMB mount is **not** available on Android — always use local folders as above.

---

## Step 6 — Run (IDE)

1. In Pydroid, switch to **IDE** mode (not Terminal).
2. Menu → Open → navigate to:

   `download/peppy_remote/peppy_remote.py`

3. Tap the yellow **Run** triangle.
4. Wait for discovery / “Waiting for server” / meters to appear.
5. Play music on Volumio — meters should move.

### If no server is found

Close the run, then either:

- Set the server host to Volumio’s IP in `--config`, or  
- From Terminal, after config exists, you can test with:

```bash
python ./download/peppy_remote/peppy_remote.py --server 192.168.x.x
```

(Replace with your Volumio IP.)

---

## What success looks like

- A fullscreen (or letterboxed) PeppyMeter skin appears.
- VU / needles / spectrum react to playback.
- Track metadata updates when the song changes (when the skin shows it).

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| `ModuleNotFoundError` / missing imports | Confirm `lib/` and `screensaver/` are inside `Download/peppy_remote/`, not only next to it |
| Blank screen / no themes | Template paths must be absolute; folders must exist under `Download/templates` |
| Errors mentioning cairo / cairosvg | Uninstall `cairosvg` in Pip |
| “No servers found” | Same Wi‑Fi as Volumio; Remote Display Server enabled; try `--server` with IP |
| Version mismatch screen | Update plugin and remote to the same release (e.g. both 3.4.4) |
| Hard to type in Terminal | Use a USB keyboard |
| App closes immediately | Run from Terminal once to read the error text |

More copy-size notes: [COPY_CHECKLIST.md](COPY_CHECKLIST.md).  
Tester checklist: [TABLET_VERIFY.md](TABLET_VERIFY.md).

---

## Updating later

When you update peppy_remote on the PC:

1. Copy the new `peppy_remote.py`, `lib/`, and (if needed) `screensaver/` over the tablet folders again.
2. Keep your tablet `config.json` unless you want to re-run `--config`.
3. Keep using matching versions with the Volumio plugin.

There is no separate Android zip that tracks every development build — you always copy from a normal PC install.
