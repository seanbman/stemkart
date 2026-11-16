#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON="${STEMKART_PYTHON:-$REPO_ROOT/.venv/bin/python}"
OUTPUT_DIR="${1:-$REPO_ROOT/dist/release}"
BUILD_ROOT="$REPO_ROOT/dist/deb-build"

if [[ ! -x "$PYTHON" ]]; then
    echo "StemKart Python environment not found: $PYTHON" >&2
    exit 1
fi
if ! "$PYTHON" -c 'import PyInstaller' >/dev/null 2>&1; then
    echo "PyInstaller is required. Install it with: uv pip install --python .venv/bin/python pyinstaller" >&2
    exit 1
fi
for executable in ffmpeg rubberband; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        echo "Required bundled executable not found: $executable" >&2
        exit 1
    fi
done

VERSION="${STEMKART_VERSION:-$("$PYTHON" -c 'import re; from pathlib import Path; text=Path("pyproject.toml").read_text(); print(re.search(r"^version = \"([^\"]+)\"$", text, re.M).group(1))')}"
DEB_VERSION="${VERSION//-/~}"
ARCH="${STEMKART_ARCH:-$(dpkg --print-architecture)}"
PACKAGE_NAME="stemkart_${DEB_VERSION}_${ARCH}"
PACKAGE_ROOT="$BUILD_ROOT/$PACKAGE_NAME"

rm -rf "$BUILD_ROOT"
mkdir -p "$OUTPUT_DIR" "$PACKAGE_ROOT/DEBIAN"

"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --onedir \
    --name StemKart \
    --distpath "$BUILD_ROOT/pyinstaller-dist" \
    --workpath "$BUILD_ROOT/pyinstaller-work" \
    --specpath "$BUILD_ROOT" \
    --add-data "$REPO_ROOT/icons:icons" \
    --add-data "$REPO_ROOT/fonts:fonts" \
    --hidden-import demucs \
    --hidden-import demucs.separate \
    "$REPO_ROOT/main.py" >/dev/null

install -d "$PACKAGE_ROOT/usr/lib/stemkart" "$PACKAGE_ROOT/usr/lib/stemkart/bin"
cp -a "$BUILD_ROOT/pyinstaller-dist/StemKart/." "$PACKAGE_ROOT/usr/lib/stemkart/"
install -Dm755 "$(command -v ffmpeg)" "$PACKAGE_ROOT/usr/lib/stemkart/bin/ffmpeg"
install -Dm755 "$(command -v rubberband)" "$PACKAGE_ROOT/usr/lib/stemkart/bin/rubberband"

install -Dm755 /dev/stdin "$PACKAGE_ROOT/usr/bin/stemkart" <<'LAUNCHER'
#!/usr/bin/env sh
set -eu
APP_ROOT="/usr/lib/stemkart"
export PATH="$APP_ROOT/bin:$PATH"
exec "$APP_ROOT/StemKart" "$@"
LAUNCHER

install -Dm644 "$REPO_ROOT/packaging/linux/stemkart.desktop" \
    "$PACKAGE_ROOT/usr/share/applications/stemkart.desktop"
install -Dm644 "$REPO_ROOT/LICENSE" "$PACKAGE_ROOT/usr/share/doc/stemkart/LICENSE"
install -Dm644 "$REPO_ROOT/packaging/linux/THIRD_PARTY_NOTICES.md" \
    "$PACKAGE_ROOT/usr/share/doc/stemkart/THIRD_PARTY_NOTICES.md"
for notice in \
    /usr/share/doc/ffmpeg/copyright \
    /usr/share/doc/rubberband-cli/copyright \
    /usr/share/doc/librubberband2/copyright; do
    if [[ -f "$notice" ]]; then
        install -Dm644 "$notice" "$PACKAGE_ROOT/usr/share/doc/stemkart/$(basename "$(dirname "$notice")")-copyright"
    fi
done

while IFS= read -r -d '' icon; do
    relative_path="${icon#"$SCRIPT_DIR/icons/hicolor/"}"
    install -Dm644 "$icon" "$PACKAGE_ROOT/usr/share/icons/hicolor/$relative_path"
done < <(find "$SCRIPT_DIR/icons/hicolor" -type f -name stemkart.png -print0)

cat > "$PACKAGE_ROOT/DEBIAN/control" <<CONTROL
Package: stemkart
Version: $DEB_VERSION
Section: sound
Priority: optional
Architecture: $ARCH
Maintainer: StemKart contributors
Description: Local audio recording, organization, effects, and stem splitting
 StemKart is a PyQt6 desktop application for local audio workflows.
Depends: libc6, libstdc++6, libgcc-s1, libgl1, libegl1, libasound2, libpulse0, libxkbcommon0, libxkbcommon-x11-0
CONTROL

chmod 0755 "$PACKAGE_ROOT/DEBIAN/control"
dpkg-deb --build --root-owner-group -Z gzip -z 1 "$PACKAGE_ROOT" "$OUTPUT_DIR/$PACKAGE_NAME.deb" >/dev/null
sha256sum "$OUTPUT_DIR/$PACKAGE_NAME.deb" > "$OUTPUT_DIR/$PACKAGE_NAME.deb.sha256"

echo "Built: $OUTPUT_DIR/$PACKAGE_NAME.deb"
du -h "$OUTPUT_DIR/$PACKAGE_NAME.deb"
