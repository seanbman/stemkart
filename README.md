<p align="center">
  <img src="packaging/linux/icons/hicolor/256x256/apps/stemkart.png" alt="StemKart icon" width="128">
</p>

<h1 align="center">StemKart</h1>

<p align="center">A local Linux desktop app for recording, organizing, editing, and splitting audio.</p>

StemKart keeps audio on the local machine while providing a two-pane sample browser, persistent file references, recording, waveform trimming, Demucs stem splitting, and a reorderable effects chain.

## Installation

### Standalone Linux release

For a released build, download the latest standalone Debian package from the [GitHub Releases page](https://github.com/seanbman/stemkart/releases).

#### Debian or Ubuntu package

Install the downloaded `.deb` with Ubuntu Software or from a terminal:

```bash
sudo apt install ./stemkart_*.deb
```

The package installs StemKart, its bundled runtime files, the application icon, and a desktop entry. StemKart then appears in the Applications menu and the running window can be grouped under the StemKart icon in the dock. Pinning it to favorites remains an optional desktop preference.

An AppImage is not published yet. The current beta release uses the `.deb` format for standard Ubuntu/Debian menu and dock integration.

### From source

Source installation is intended for development or for users who want to run the checkout directly. Python 3.10+, `uv`, FFmpeg, Rubber Band, and the PipeWire tools are required for the corresponding features.

```bash
git clone https://github.com/seanbman/stemkart.git
cd stemkart
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Run the application:

```bash
.venv/bin/python main.py
```

To add the checked-out application to the current user's menu and dock identity, install the development desktop entry:

```bash
./packaging/linux/install_desktop.sh
```

This launcher points at the checkout's `.venv`; it is not a replacement for the standalone release package.

## User guide

Start with the [User guide index](docs/user-guide/README.md). It is organized as a numbered walkthrough:

1. [Installation and first launch](docs/user-guide/01-installation.md)
2. [Samples and the Explorer](docs/user-guide/02-samples.md)
3. [Library and References](docs/user-guide/03-library.md)
4. [Stem Splitter](docs/user-guide/04-stem-splitter.md)
5. [Effects](docs/user-guide/05-effects.md)
6. [Recording](docs/user-guide/06-recording.md)
7. [Troubleshooting](docs/user-guide/07-troubleshooting.md)

## Developer documentation

The [Developer documentation index](docs/developer/README.md) covers the project setup, architecture, tests, packaging, and license notices:

1. [Development setup](docs/developer/01-development-setup.md)
2. [Architecture](docs/developer/02-architecture.md)
3. [Testing](docs/developer/03-testing.md)
4. [Packaging and release](docs/developer/04-packaging.md)
5. [Licenses and third-party notices](docs/developer/05-licenses-and-notices.md)

## Project notes

- Audio remains local; StemKart does not upload source files.
- Demucs model downloads happen on the first split and may take several minutes.
- The default Demucs model is `htdemucs`, producing vocals, drums, bass, and other stems.
- The application uses a fixed-size, native Qt-oriented interface with no scrollable panels.

## License

StemKart is intended to be distributed under the GNU General Public License, version 3. The source distribution also includes separate notices for bundled or required third-party software; see the [license and notices guide](docs/developer/05-licenses-and-notices.md).

<p align="center"><a href="docs/user-guide/README.md">User guide</a> · <a href="docs/developer/README.md">Developer documentation</a></p>
