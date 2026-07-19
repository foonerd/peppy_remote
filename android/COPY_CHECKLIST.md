# Android copy checklist (dry-run)

Verified against a real Linux install at `~/peppy_remote` (installer layout; same payload as Windows `install.ps1`):

| Path | Size (approx) | Copy to phone? |
|------|----------------|----------------|
| `peppy_remote.py` | ~70KB | **Yes** |
| `lib/` | ~0.5MB | **Yes** (exclude `__pycache__`) |
| `screensaver/` | ~100MB | **Yes** (handlers, peppymeter, spectrum, fonts, format-icons) |
| `screensaver/fonts/` | 43 files typical | Included with `screensaver/` |
| Install-root `fonts/` | n/a | **No** (not created by installer) |
| `venv/` | ~80MB+ | **No** |
| `mnt/`, launchers, logs | n/a | **No** |

Phone siblings (not inside `peppy_remote/`):

- `Download/templates/`
- `Download/templates_spectrum/`

Source templates from Volumio SMB/UNC via PC, then USB/file-transfer to the tablet.
