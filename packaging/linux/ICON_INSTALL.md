# StemKart Linux application icons

Approved monochrome record-cart icon, on a solid white rounded-square tile with transparent corners. Generated from `icons/source/stemkart-master.png`; no bevels, shadows, or additional artwork.

## Files and icon names

- `icons/hicolor/{SIZE}x{SIZE}/apps/stemkart.png` — freedesktop.org icon-theme sizes: 16, 22, 24, 32, 48, 64, 96, 128, 256, and 512 px.
- `stemkart.png` — 512×512 RGBA copy for AppImage root icon / other packagers expecting a standalone icon file.
- `icons/source/stemkart-master.png` — approved original full-resolution master for future regeneration.

All desktop-launcher references should use `Icon=stemkart` (without filename extension or filesystem path). The existing `packaging/linux/stemkart.desktop` already does this.

## Install

For a system installation, copy `icons/hicolor/` contents into `/usr/share/icons/hicolor/` (or `${PREFIX}/share/icons/hicolor/`) while retaining their size directories. For a per-user installation, copy into `~/.local/share/icons/hicolor/`. Refresh the icon theme cache when applicable.

For an AppImage AppDir, place `stemkart.desktop` and `stemkart.png` (512×512) at the AppDir root; the icon filename must match the `Icon=stemkart` desktop entry. An optional `.DirIcon` symlink can point to `stemkart.png`.

For a per-user desktop installation from this checkout, run `./packaging/linux/install_desktop.sh`. It installs the local `.venv` launcher, desktop entry, and all icon sizes under `~/.local/share/`. PNG files have real alpha transparency outside the rounded white tile, and no baked checkerboard.
