#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "StemKart's .venv was not found at $REPO_ROOT/.venv" >&2
    echo "Create the environment and install requirements before running this script." >&2
    exit 1
fi

# Launch the checked-out app directly from its existing environment. This also
# works with uv-managed environments that intentionally do not include pip.
EXEC_COMMAND="$PYTHON $REPO_ROOT/main.py"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APPLICATIONS_DIR="$DATA_HOME/applications"
ICON_ROOT="$DATA_HOME/icons/hicolor"
DESKTOP_FILE="$APPLICATIONS_DIR/stemkart.desktop"

mkdir -p "$APPLICATIONS_DIR"
while IFS= read -r -d '' icon; do
    relative_path="${icon#"$SCRIPT_DIR/icons/hicolor/"}"
    install -Dm644 "$icon" "$ICON_ROOT/$relative_path"
done < <(find "$SCRIPT_DIR/icons/hicolor" -type f -name stemkart.png -print0)

desktop_tmp="$(mktemp)"
trap 'rm -f "$desktop_tmp"' EXIT
sed "s|^Exec=.*|Exec=$EXEC_COMMAND|" \
    "$SCRIPT_DIR/stemkart.desktop" > "$desktop_tmp"
install -Dm644 "$desktop_tmp" "$DESKTOP_FILE"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP_FILE"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$ICON_ROOT" >/dev/null 2>&1 || true
fi

echo "Installed StemKart desktop entry: $DESKTOP_FILE"
echo "Installed StemKart icons under: $ICON_ROOT"
