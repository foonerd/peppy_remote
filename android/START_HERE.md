# Start here (tablet)

You already have the files. Do these steps on the tablet.

## 1. Unzip (if you have a zip)

Put `peppy_remote_for_tablet.zip` in the tablet **Download** folder and unzip it.  
You should see folders named `peppy_remote`, `templates`, and `templates_spectrum`.

## 2. Install apps from Play Store

1. **Pydroid 3**
2. **Pydroid repository** plugin
3. **Pydroid permissions** plugin (lets the app use Download storage)

## 3. Install Python packages

1. Open Pydroid 3
2. Menu (☰) → **Pip** → **Install**
3. Tick **Use prebuilt libraries repository**
4. Install each package from `peppy_remote/requirements-android.txt`  
   (or install from that file if your Pip screen allows it)

**Do not** install **pygame** (already included).  
**Do not** install **cairosvg**. If it is installed, uninstall it.

## 4. Put your meter skins (if empty)

If `templates` / `templates_spectrum` are empty, copy your skins into them from a PC  
(or from your Volumio share). Then note these paths:

```text
/storage/emulated/0/Download/templates
/storage/emulated/0/Download/templates_spectrum
```

(If you unzipped inside Download, use that folder’s full path.)

## 5. Configure

Menu → **Terminal**, then:

```bash
python ./download/peppy_remote/peppy_remote.py --config
```

Set your Volumio box. For templates, paste the **absolute** paths above.  
A USB keyboard helps.

## 6. Run

1. Open `peppy_remote.py` using **Pydroid’s own folder button** (not another file app)
2. Tap the yellow **play** button
3. Play music on Volumio

If no server is found, set the Volumio IP in config, or run:

```bash
python ./download/peppy_remote/peppy_remote.py --server 192.168.x.x
```
