#!/usr/bin/env bash
# Get for Android: build peppy_remote_for_tablet.zip without requiring a desktop install.
# Sources: local install tree (validated) OR GitHub (staging). Fail closed on stale trees.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VALIDATE_PY="$SCRIPT_DIR/lib/validate_android_tree.py"

DEFAULT_REMOTE_BRANCH="main"
DEFAULT_SCREENSAVER_BRANCH="main"
REMOTE_RAW="https://raw.githubusercontent.com/foonerd/peppy_remote"
SS_RAW="https://raw.githubusercontent.com/foonerd/peppy_screensaver"
PEPPYMETER_REPO="https://github.com/foonerd/PeppyMeter.git"
SPECTRUM_REPO="https://github.com/foonerd/PeppySpectrum.git"

VOLUMIO_FILES=(
  volumio_peppymeter.py
  volumio_configfileparser.py
  volumio_turntable.py
  volumio_cassette.py
  volumio_compositor.py
  volumio_indicators.py
  volumio_spectrum.py
  volumio_basic.py
  volumio_folderimage.py
  volumio_artistfanart.py
  volumio_typeformat.py
  screensaverspectrum.py
)

FONTS=(
  DSEG7Classic-Bold.ttf DSEG7Classic-BoldItalic.ttf DSEG7Classic-Italic.ttf DSEG7Classic-Regular.ttf
  fontawesome-webfont.eot fontawesome-webfont.svg fontawesome-webfont.ttf fontawesome-webfont.woff fontawesome-webfont.woff2
  FontAwesome.otf
  gibson-bold.ttf Gibson-BoldItalic.ttf Gibson-Regular.ttf Gibson-RegularItalic.ttf
  glyphicons-halflings-regular.eot glyphicons-halflings-regular.svg glyphicons-halflings-regular.ttf
  glyphicons-halflings-regular.woff glyphicons-halflings-regular.woff2
  Lato-Bold.eot Lato-Bold.ttf Lato-Bold.woff Lato-Bold.woff2
  Lato-Light.eot Lato-Light.ttf Lato-Light.woff Lato-Light.woff2
  Lato-Regular.eot Lato-Regular.ttf Lato-Regular.woff Lato-Regular.woff2
  materialdesignicons-webfont.eot materialdesignicons-webfont.ttf materialdesignicons-webfont.woff materialdesignicons-webfont.woff2
  MaterialIcons-Regular.eot MaterialIcons-Regular.ttf MaterialIcons-Regular.woff MaterialIcons-Regular.woff2
  PeppyFont-Light.ttf PeppyFont-Regular.ttf PeppyFont-Bold.ttf PeppyFont-Italic.ttf
)

FORMAT_ICONS=(
  aac.svg aiff.svg airplay.svg alac.svg bt.svg cd.svg
  dab.svg dsd.svg dts.svg flac.svg fm.svg m4a.svg
  mp3.svg mp4.svg mqa.svg ogg.svg opus.svg qobuz.svg
  radio.svg rr.svg spotify.svg tidal.svg wav.svg
  wavpack.svg wma.svg YouTube.svg
)

ALL_ICONS="'aac', 'aiff', 'airplay', 'alac', 'bt', 'cd', 'dab', 'dsd', 'dts', 'flac', 'fm', 'm4a', 'mp3', 'mp4', 'mqa', 'ogg', 'opus', 'qobuz', 'radio', 'rr', 'spotify', 'tidal', 'wav', 'wavpack', 'wma', 'youtube'"

SOURCE=""
INSTALL_DIR=""
REMOTE_BRANCH="$DEFAULT_REMOTE_BRANCH"
SCREENSAVER_BRANCH="$DEFAULT_SCREENSAVER_BRANCH"
TEMPLATES_PATH=""
SPECTRUM_TEMPLATES_PATH=""
OUTPUT_ZIP=""
YES=0
REFRESH_HANDLERS=0
EXPECT_VERSION=""

usage() {
  cat <<EOF
Get for Android: create peppy_remote_for_tablet.zip

Usage: $(basename "$0") [options]

Sources (pick one):
  --source local              Pack from a local peppy_remote install (must pass validation)
  --source github             Download into a staging tree, then pack (no desktop install needed)

Options:
  --install-dir PATH          Local tree (default: \$HOME/peppy_remote)
  --remote-branch REF         GitHub remote ref (default: $DEFAULT_REMOTE_BRANCH)
  --screensaver-branch REF    GitHub screensaver ref (default: $DEFAULT_SCREENSAVER_BRANCH)
  --templates PATH            Optional skins folder to include as templates/
  --spectrum-templates PATH   Optional spectrum skins folder
  --output PATH               Output zip path (default: Desktop/peppy_remote_for_tablet.zip)
  --refresh-handlers          With --source local: re-fetch screensaver handlers from GitHub
  --expect-version X.Y.Z      Footlock major.minor vs client (optional)
  --yes                       Non-interactive: default to --source github if unset
  -h, --help                  Show this help

Examples:
  $(basename "$0") --yes
  $(basename "$0") --source github --remote-branch main
  $(basename "$0") --source local --install-dir "\$HOME/peppy_remote"
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "$*"; }

desktop_dir() {
  if [[ -d "$HOME/Desktop" ]]; then
    echo "$HOME/Desktop"
  elif [[ -d "$HOME/Escritorio" ]]; then
    echo "$HOME/Escritorio"
  else
    echo "$HOME"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source) SOURCE="${2:-}"; shift 2 ;;
      --install-dir) INSTALL_DIR="${2:-}"; shift 2 ;;
      --remote-branch) REMOTE_BRANCH="${2:-}"; shift 2 ;;
      --screensaver-branch) SCREENSAVER_BRANCH="${2:-}"; shift 2 ;;
      --templates) TEMPLATES_PATH="${2:-}"; shift 2 ;;
      --spectrum-templates) SPECTRUM_TEMPLATES_PATH="${2:-}"; shift 2 ;;
      --output) OUTPUT_ZIP="${2:-}"; shift 2 ;;
      --expect-version) EXPECT_VERSION="${2:-}"; shift 2 ;;
      --refresh-handlers) REFRESH_HANDLERS=1; shift ;;
      --yes|-y) YES=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown option: $1 (try --help)" ;;
    esac
  done
}

choose_source_interactive() {
  echo ""
  echo "Get for Android: where should files come from?"
  echo "  1) GitHub (recommended: works with no desktop install)"
  echo "  2) Local install tree (must already be Android-capable)"
  echo ""
  read -r -p "Choose [1/2] (default 1): " ans
  case "${ans:-1}" in
    2|local|Local) SOURCE="local" ;;
    *) SOURCE="github" ;;
  esac
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

validate_tree() {
  local root="$1"
  need_cmd python3
  local args=("$VALIDATE_PY" "$root")
  if [[ -n "$EXPECT_VERSION" ]]; then
    args+=(--expect-version "$EXPECT_VERSION")
  fi
  python3 "${args[@]}"
}

client_version() {
  python3 "$VALIDATE_PY" "$1" --print-version 2>/dev/null || echo "unknown"
}

download_file() {
  local url="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$dest" || die "Download failed: $url"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$dest" "$url" || die "Download failed: $url"
  else
    die "Need curl or wget"
  fi
}

try_download_file() {
  local url="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$dest" 2>/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$dest" "$url" 2>/dev/null
  else
    return 1
  fi
}

patch_local_icons() {
  local ss_dir="$1"
  local f
  for f in volumio_peppymeter.py volumio_turntable.py volumio_cassette.py volumio_basic.py; do
    if [[ -f "$ss_dir/$f" ]]; then
      sed -i "s/local_icons = {'tidal', 'cd', 'qobuz', 'dab', 'fm', 'radio'}/local_icons = {$ALL_ICONS}/g" "$ss_dir/$f" 2>/dev/null || true
      sed -i "s/local_icons = {'tidal', 'cd', 'qobuz'}/local_icons = {$ALL_ICONS}/g" "$ss_dir/$f" 2>/dev/null || true
    fi
  done
}

fetch_handlers_into() {
  local ss_dir="$1"
  local file
  mkdir -p "$ss_dir"
  info "Fetching screensaver handlers ($SCREENSAVER_BRANCH)..."
  for file in "${VOLUMIO_FILES[@]}"; do
    download_file \
      "$SS_RAW/$SCREENSAVER_BRANCH/volumio_peppymeter/$file" \
      "$ss_dir/$file"
  done
  patch_local_icons "$ss_dir"
}

fetch_fonts_icons_into() {
  local ss_dir="$1"
  local font icon
  info "Fetching fonts and format-icons ($REMOTE_BRANCH)..."
  mkdir -p "$ss_dir/fonts" "$ss_dir/format-icons"
  for font in "${FONTS[@]}"; do
    download_file "$REMOTE_RAW/$REMOTE_BRANCH/fonts/$font" "$ss_dir/fonts/$font"
  done
  for icon in "${FORMAT_ICONS[@]}"; do
    download_file "$REMOTE_RAW/$REMOTE_BRANCH/format-icons/$icon" "$ss_dir/format-icons/$icon"
  done
}

fetch_engines_into() {
  local ss_dir="$1"
  local stage="$2"
  need_cmd git
  mkdir -p "$ss_dir" "$stage"

  info "Cloning PeppyMeter..."
  rm -rf "$stage/PeppyMeter"
  git clone --depth 1 "$PEPPYMETER_REPO" "$stage/PeppyMeter" >/dev/null 2>&1 \
    || die "Failed to clone PeppyMeter"
  rm -rf "$ss_dir/peppymeter"
  mkdir -p "$ss_dir/peppymeter"
  cp -a "$stage/PeppyMeter/." "$ss_dir/peppymeter/"

  info "Cloning PeppySpectrum..."
  rm -rf "$stage/PeppySpectrum"
  git clone --depth 1 "$SPECTRUM_REPO" "$stage/PeppySpectrum" >/dev/null 2>&1 \
    || die "Failed to clone PeppySpectrum"
  rm -rf "$ss_dir/spectrum"
  mkdir -p "$ss_dir/spectrum"
  cp -a "$stage/PeppySpectrum/." "$ss_dir/spectrum/"
}

fetch_remote_client_into() {
  local dest="$1"
  local lib_names=(
    peppy_common.py peppy_version.py peppy_network.py peppy_persist.py
    peppy_receivers.py peppy_spectrum.py peppy_smb.py peppy_asset.py
    peppy_wizard_cli.py peppy_wizard_gui.py
  )
  local f
  mkdir -p "$dest/lib"
  info "Fetching peppy_remote client ($REMOTE_BRANCH)..."
  download_file \
    "$REMOTE_RAW/$REMOTE_BRANCH/peppy_remote.py" \
    "$dest/peppy_remote.py"
  for f in "${lib_names[@]}"; do
    download_file \
      "$REMOTE_RAW/$REMOTE_BRANCH/lib/$f" \
      "$dest/lib/$f"
  done
  if ! try_download_file \
      "$REMOTE_RAW/$REMOTE_BRANCH/requirements-android.txt" \
      "$dest/requirements-android.txt"; then
    if [[ -f "$REPO_ROOT/requirements-android.txt" ]]; then
      cp "$REPO_ROOT/requirements-android.txt" "$dest/requirements-android.txt"
    else
      cat >"$dest/requirements-android.txt" <<'REQ'
# Install via Pydroid Pip with "Use prebuilt libraries repository"
# Do NOT install pygame or cairosvg
requests
numpy
pillow
websocket-client
zeroconf
REQ
    fi
  fi
}

stage_from_github() {
  local stage="$1"
  local tree="$stage/peppy_remote"
  rm -rf "$tree"
  mkdir -p "$tree/screensaver"
  fetch_remote_client_into "$tree"
  fetch_handlers_into "$tree/screensaver"
  fetch_fonts_icons_into "$tree/screensaver"
  fetch_engines_into "$tree/screensaver" "$stage/clones"
  echo "$tree"
}

copy_tree_filtered() {
  local src="$1" dest="$2"
  mkdir -p "$dest"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a \
      --exclude 'venv/' \
      --exclude '.venv/' \
      --exclude 'cairo/' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude 'peppy_remote.sh' \
      --exclude 'peppy_remote.desktop' \
      --exclude 'peppy_remote_config.desktop' \
      --exclude 'launch_*' \
      --exclude 'uninstall.sh' \
      --exclude 'uninstall.ps1' \
      --exclude '.git/' \
      --exclude 'mnt/' \
      --exclude 'config.json' \
      --exclude 'ANDROID_PACK_INFO.txt' \
      "$src/" "$dest/"
  else
    (
      cd "$src"
      tar \
        --exclude='venv' --exclude='.venv' --exclude='cairo' \
        --exclude='__pycache__' --exclude='.git' --exclude='mnt' \
        --exclude='peppy_remote.sh' --exclude='peppy_remote.desktop' \
        --exclude='config.json' \
        -cf - .
    ) | (cd "$dest" && tar -xf -)
  fi
}

write_start_here() {
  local dest="$1"
  if [[ -f "$SCRIPT_DIR/START_HERE.md" ]]; then
    cp "$SCRIPT_DIR/START_HERE.md" "$dest/START_HERE.txt"
  else
    echo "See android/START_HERE.md in the peppy_remote repo." >"$dest/START_HERE.txt"
  fi
}

write_pack_info() {
  local dest="$1" source_label="$2" client_ver="$3"
  cat >"$dest/ANDROID_PACK_INFO.txt" <<EOF
peppy_remote Android pack
=========================
source:              $source_label
remote_ref:          $REMOTE_BRANCH
screensaver_ref:     $SCREENSAVER_BRANCH
client_version:      $client_ver
packed_at_utc:       $(date -u +"%Y-%m-%dT%H:%M:%SZ")
host:                $(hostname 2>/dev/null || echo unknown)
tool:                get-android.sh
EOF
}

copy_templates() {
  local pack_root="$1"
  mkdir -p "$pack_root/templates" "$pack_root/templates_spectrum"
  if [[ -n "$TEMPLATES_PATH" ]]; then
    [[ -d "$TEMPLATES_PATH" ]] || die "Templates path not found: $TEMPLATES_PATH"
    info "Copying templates from $TEMPLATES_PATH"
    cp -a "$TEMPLATES_PATH/." "$pack_root/templates/"
  else
    cat >"$pack_root/templates/PUT_SKINS_HERE.txt" <<'EOF'
Put your PeppyMeter skin folders here (same layout as on Volumio / desktop remote).
EOF
  fi
  if [[ -n "$SPECTRUM_TEMPLATES_PATH" ]]; then
    [[ -d "$SPECTRUM_TEMPLATES_PATH" ]] || die "Spectrum templates path not found: $SPECTRUM_TEMPLATES_PATH"
    info "Copying spectrum templates from $SPECTRUM_TEMPLATES_PATH"
    cp -a "$SPECTRUM_TEMPLATES_PATH/." "$pack_root/templates_spectrum/"
  else
    cat >"$pack_root/templates_spectrum/PUT_SKINS_HERE.txt" <<'EOF'
Put your PeppySpectrum skin folders here.
EOF
  fi
}

build_zip() {
  local src_tree="$1" source_label="$2"
  need_cmd zip

  [[ -n "$OUTPUT_ZIP" ]] || OUTPUT_ZIP="$(desktop_dir)/peppy_remote_for_tablet.zip"
  mkdir -p "$(dirname "$OUTPUT_ZIP")"

  local pack_root dest_tree ver work_base
  # Prefer cache (or next to output) over /tmp: clearer for restricted environments
  work_base="${XDG_CACHE_HOME:-$HOME/.cache}/peppy_android_work"
  mkdir -p "$work_base"
  pack_root="$(mktemp -d "$work_base/pack.XXXXXX" 2>/dev/null || mktemp -d "$(dirname "$OUTPUT_ZIP")/.peppy_android_pack.XXXXXX")"
  dest_tree="$pack_root/peppy_remote"
  copy_tree_filtered "$src_tree" "$dest_tree"

  if [[ ! -f "$dest_tree/requirements-android.txt" ]]; then
    if [[ -f "$REPO_ROOT/requirements-android.txt" ]]; then
      cp "$REPO_ROOT/requirements-android.txt" "$dest_tree/requirements-android.txt"
    else
      die "Missing requirements-android.txt"
    fi
  fi

  write_start_here "$dest_tree"
  ver="$(client_version "$dest_tree")"
  write_pack_info "$dest_tree" "$source_label" "$ver"
  copy_templates "$pack_root"

  rm -f "$OUTPUT_ZIP"
  (
    cd "$pack_root"
    zip -qr "$OUTPUT_ZIP" peppy_remote templates templates_spectrum
  )
  rm -rf "$pack_root"
  info ""
  info "Created: $OUTPUT_ZIP"
  info "Copy that zip to the tablet Download folder, unzip, then open START_HERE.txt"
}

main() {
  parse_args "$@"

  if [[ -z "$SOURCE" ]]; then
    if [[ "$YES" -eq 1 ]]; then
      SOURCE="github"
    elif [[ -t 0 ]]; then
      choose_source_interactive
    else
      die "No --source and not interactive. Use --source github|local or --yes"
    fi
  fi

  SOURCE="$(echo "$SOURCE" | tr '[:upper:]' '[:lower:]')"
  case "$SOURCE" in
    local|github) ;;
    *) die "--source must be local or github" ;;
  esac

  [[ -f "$VALIDATE_PY" ]] || die "Missing validator: $VALIDATE_PY"

  local tree=""
  local source_label=""
  local stage=""

  if [[ "$SOURCE" == "local" ]]; then
    [[ -n "$INSTALL_DIR" ]] || INSTALL_DIR="${HOME}/peppy_remote"
    [[ -d "$INSTALL_DIR" ]] || die "Local install not found: $INSTALL_DIR: use --source github"
    tree="$INSTALL_DIR"
    source_label="local:$tree"

    if [[ "$REFRESH_HANDLERS" -eq 1 ]]; then
      info "Refreshing handlers from GitHub into local tree..."
      fetch_handlers_into "$tree/screensaver"
      fetch_fonts_icons_into "$tree/screensaver"
      if [[ ! -d "$tree/screensaver/peppymeter" ]] || [[ ! -d "$tree/screensaver/spectrum" ]]; then
        stage="${TMPDIR:-/tmp}/peppy_android_stage_$$"
        mkdir -p "$stage"
        fetch_engines_into "$tree/screensaver" "$stage/clones"
        rm -rf "$stage"
      fi
    fi

    info "Validating local tree (fail closed)..."
    if ! validate_tree "$tree"; then
      die "Local tree is not Android-ready. Re-run with --source github, or upgrade the client / use --refresh-handlers."
    fi
  else
    need_cmd git
    stage="${XDG_CACHE_HOME:-$HOME/.cache}/peppy_android_stage"
    mkdir -p "$stage"
    info "Building staging tree from GitHub (no desktop install required)..."
    tree="$(stage_from_github "$stage")"
    source_label="github:remote=$REMOTE_BRANCH;screensaver=$SCREENSAVER_BRANCH"
    info "Validating staged tree..."
    validate_tree "$tree" || die "Staged GitHub tree failed validation: check branches"
  fi

  build_zip "$tree" "$source_label"
}

main "$@"
