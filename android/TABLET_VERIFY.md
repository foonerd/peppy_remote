# Tablet verification (prototype)

Run on the dedicated Pydroid tablet after copying a **feature/android-pydroid** build.

## Prep

1. On PC: checkout `feature/android-pydroid`, ensure install has matching `screensaver/` (3.4.4).
2. Copy per [COPY_CHECKLIST.md](COPY_CHECKLIST.md) and README Android section.
3. Pip install `requirements-android.txt` (no pygame, no cairosvg).

## Checklist

| # | Step | Pass? | Notes |
|---|------|-------|-------|
| 1 | Pip deps OK; `cairosvg` absent | | |
| 2 | `download/peppy_remote/{peppy_remote.py,lib,screensaver}` present | | |
| 3 | `Download/templates` + `templates_spectrum` present | | |
| 4 | Terminal: `python ./download/peppy_remote/peppy_remote.py --config` | | Absolute paths |
| 5 | IDE yellow-triangle run | | |
| 6 | Meters draw / levels move | | |
| 7 | If no discovery: `--server <ip>` | | |
| 8 | Handler sync: quiet fail vs crash? | | |
| 9 | Version wait / mismatch UI OK? | | |
| 10 | SCALED / fullscreen look acceptable? | | |
| 11 | Format icons without cairosvg OK? | | |

## Difficulty assessment

After the run, note:

- Blockers (must fix before merge)
- Nice-to-haves
- Whether sync should skip entirely on Android instead of soft-fail
