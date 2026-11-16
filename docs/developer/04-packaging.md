# 4. Packaging and release

[← Testing](03-testing.md) · [Contents](README.md) · [Next: Licenses and third-party notices →](05-licenses-and-notices.md)

## 4.1 Desktop metadata

The approved Linux icon set lives under `packaging/linux/icons/hicolor/`. The desktop entry is `packaging/linux/stemkart.desktop` and uses the icon name `stemkart` plus `StartupWMClass=stemkart` so desktop environments can associate the running window with the installed application.

`packaging/linux/install_desktop.sh` is a per-user development helper. It installs the desktop entry and icons into `~/.local/share/` and points the launcher at the repository's `.venv`.

## 4.2 Standalone package requirements

A release `.deb` should install the application and its private runtime under an application-owned prefix such as `/opt/stemkart`, then install:

- a desktop entry under `/usr/share/applications/`;
- the hicolor icons under `/usr/share/icons/hicolor/`;
- a launcher that prepends the private FFmpeg and Rubber Band locations to `PATH`;
- license and third-party notice files.

The release package must not depend on a developer checkout or `.venv`. It should be tested on a clean supported Ubuntu/Debian environment.

An AppImage can provide the portable alternative, but its AppDir must contain the application, runtime, desktop entry, and matching `stemkart.png` icon. AppImage desktop integration is separate from the `.deb`'s standard menu integration.

## 4.3 Release artifacts

Publish versioned artifacts with checksums:

- `stemkart_<version>_<architecture>.deb`
- `StemKart-<version>-<architecture>.AppImage`
- a checksum file covering both artifacts

Keep source installation instructions usable for contributors even when standalone artifacts are available. Do not describe a build as bundled until FFmpeg, Rubber Band, their notices, and a clean-machine smoke test are all present.

## 4.4 Release checklist

1. Confirm the version in `pyproject.toml` and release metadata.
2. Run unit tests, compile checks, `git diff --check`, and documentation-link validation.
3. Build the `.deb` and AppImage from a clean checkout.
4. Inspect the package contents and desktop entry.
5. Launch the packaged application and verify the icon/menu/taskbar identity.
6. Exercise playback, recording, split, effects, and WAV export with a short local fixture.
7. Verify bundled FFmpeg and Rubber Band are used without system-path assumptions.
8. Publish artifacts and checksums with the release notes.

[← Testing](03-testing.md) · [Contents](README.md) · [Next: Licenses and third-party notices →](05-licenses-and-notices.md)
