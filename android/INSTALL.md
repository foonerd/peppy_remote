# Get for Android (Pydroid 3)

Build one tablet zip on a PC, copy it to the tablet, follow **START_HERE**.  
You do **not** need a desktop peppy_remote install first.

This is **experimental**. Same `peppy_remote.py` as Linux/Windows (not a separate APK).  
Pydroid bring-up was proven by **Lee.Yan** — thank you.

---

## What you need

1. Volumio with [PeppyMeter Screensaver](https://github.com/foonerd/peppy_screensaver), **Remote Display Server** on, **same version** as the client (e.g. both **3.4.4**).
2. Phone/tablet on the **same Wi‑Fi**.
3. A Windows or Linux PC with **Git**, **Python 3**, and **zip** (Linux) — only to run Get for Android once.
4. Optional: USB keyboard for Pydroid Terminal.

---

## Part A — On the PC: Get for Android

### Grandma path (recommended)

Defaults download an Android-capable client from GitHub and write  
`peppy_remote_for_tablet.zip` on your Desktop.

**Linux** (from a clone of this repo, or download the two scripts + `android/lib/` + `START_HERE.md`):

```bash
cd /path/to/peppy_remote
chmod +x android/get-android.sh
./android/get-android.sh --yes
```

Or one-shot after cloning the Android branch:

```bash
git clone --depth 1 -b feature/android-pydroid https://github.com/foonerd/peppy_remote.git
cd peppy_remote
./android/get-android.sh --yes
```

**Windows** (PowerShell):

```powershell
git clone --depth 1 -b feature/android-pydroid https://github.com/foonerd/peppy_remote.git
cd peppy_remote
powershell -ExecutionPolicy Bypass -File .\android\get-android.ps1 -Yes
```

When it finishes you should see:

```text
Created: …/Desktop/peppy_remote_for_tablet.zip
```

### Choose source (optional)

| Source | When to use |
|--------|-------------|
| **GitHub** (default with `--yes` / `-Yes`) | No desktop install, or local install is old |
| **Local** | You already have a validated Android-capable `~/peppy_remote` (or `%USERPROFILE%\peppy_remote`) |

```bash
# Linux — GitHub (explicit)
./android/get-android.sh --source github

# Linux — local (fails closed if not Android-ready)
./android/get-android.sh --source local --install-dir "$HOME/peppy_remote"

# Refresh handlers on a local tree, then pack
./android/get-android.sh --source local --refresh-handlers
```

```powershell
# Windows
.\android\get-android.ps1 -Source github
.\android\get-android.ps1 -Source local -InstallDir "$env:USERPROFILE\peppy_remote"
.\android\get-android.ps1 -Source local -RefreshHandlers
```

Optional skins on the PC:

```bash
./android/get-android.sh --yes \
  --templates /path/to/templates \
  --spectrum-templates /path/to/templates_spectrum
```

Stale / non-Android local trees are **refused** (no zip). Use GitHub source or upgrade the client.

---

## Part B — On the tablet

1. Copy `peppy_remote_for_tablet.zip` into the tablet **Download** folder.
2. Unzip it. You should see `peppy_remote/`, `templates/`, `templates_spectrum/`.
3. Open `peppy_remote/START_HERE.txt` and follow the six steps  
   (Play Store apps → Pip packages → skins if empty → `--config` → Run from Pydroid).

Also in the zip: `ANDROID_PACK_INFO.txt` (what was packed — useful for support).

---

## Quick checklist

| Step | Done when |
|------|-----------|
| PC Get for Android | Zip on Desktop |
| Unzip on tablet | Folders under Download |
| Pydroid + Repository + Permissions | Installed from Play Store |
| Pip (prebuilt repo) | Packages from `requirements-android.txt`; **no** pygame / **no** cairosvg |
| Skins | Under `Download/templates` (and spectrum) |
| Config | Absolute template paths; Volumio found or set by IP |
| Run | Open `peppy_remote.py` from Pydroid’s file UI → yellow play |

---

## Advanced

### Desktop install (optional)

A full Linux/Windows remote is **not required** for the tablet path.  
If you want a PC remote as well:

| Repo | Branch (Android testing) |
|------|--------------------------|
| peppy_remote | `feature/android-pydroid` |
| peppy_screensaver handlers | `main` (e.g. 3.4.4) |

**Linux:**

```bash
curl -sSL https://raw.githubusercontent.com/foonerd/peppy_remote/feature/android-pydroid/install.sh \
  | bash -s -- --remote-branch feature/android-pydroid --screensaver-branch main
```

**Windows:**

```powershell
irm https://raw.githubusercontent.com/foonerd/peppy_remote/feature/android-pydroid/install.ps1 -OutFile install.ps1
.\install.ps1 -RemoteBranch feature/android-pydroid -ScreensaverBranch main
```

Then you may pack with `--source local` **only if** validation passes (`is_android` + modular `lib/` + screensaver engines).

### Pin branches / tags

```bash
./android/get-android.sh --source github \
  --remote-branch feature/android-pydroid \
  --screensaver-branch main \
  --expect-version 3.4.4
```

```powershell
.\android\get-android.ps1 -Source github `
  -RemoteBranch feature/android-pydroid `
  -ScreensaverBranch main `
  -ExpectVersion 3.4.4
```

### Manual copy (no zip tool)

See [COPY_CHECKLIST.md](COPY_CHECKLIST.md) if you must copy folders by hand from a PC install.  
Prefer Get for Android so junk (`venv`, launchers) is never packed.

### Tablet verify / troubleshooting

| Problem | What to try |
|---------|-------------|
| Get for Android refuses local | Tree is stale — use `--source github` or upgrade remote |
| `ModuleNotFoundError` | `lib/` and `screensaver/` must be inside `Download/peppy_remote/` |
| Blank / no themes | Absolute template paths under Download |
| cairo / cairosvg errors | Uninstall `cairosvg` in Pip |
| No servers found | Same Wi‑Fi; Remote Display Server on; `--server` with IP |
| Version mismatch | Match plugin and client (e.g. both 3.4.4) |

Tester checklist: [TABLET_VERIFY.md](TABLET_VERIFY.md).

### Updating later

Re-run Get for Android on the PC, copy the new zip, unzip over the tablet folders  
(keep `config.json` unless you want to reconfigure).  
There is **no** living GitHub Release zip of evolving client bits.

### Design notes

- Independent entry points: [`get-android.sh`](get-android.sh) / [`get-android.ps1`](get-android.ps1)
- Validation: [`lib/validate_android_tree.py`](lib/validate_android_tree.py)
- Tablet card: [`START_HERE.md`](START_HERE.md) (copied into the zip as `START_HERE.txt`)
