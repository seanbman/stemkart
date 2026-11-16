# 1. Installation and first launch

[← User guide](README.md) · [Contents](README.md) · [Next: Samples and the Explorer →](02-samples.md)

## 1.1 Standalone release

Download a release from the [StemKart GitHub Releases page](https://github.com/seanbman/stemkart/releases).

For Ubuntu or Debian, install the `.deb` package:

```bash
sudo apt install ./stemkart_*.deb
```

The package provides the application, desktop-menu entry, icon, and bundled release runtime. Open StemKart from the Applications menu. To keep it in the dock, right-click its running icon and choose **Add to Favorites**.

For a portable AppImage:

```bash
chmod +x StemKart-*.AppImage
./StemKart-*.AppImage
```

## 1.2 Source checkout

Install the source version when you want to follow development or run a checkout directly:

```bash
git clone https://github.com/seanbman/stemkart.git
cd stemkart
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python main.py
```

FFmpeg and the Rubber Band CLI are required for the audio-processing features. PipeWire's `pw-dump`, `pw-record`, and `pw-link` commands are additionally required for system-audio and application-audio recording.

## 1.3 Optional menu entry for a source checkout

From the repository root, run:

```bash
./packaging/linux/install_desktop.sh
```

This installs a per-user desktop entry and the approved icon set under `~/.local/share/`. It launches the current checkout, so keep the repository and `.venv` in place.

## 1.4 First launch checklist

1. Open **Samples**.
2. Choose a directory in the Explorer.
3. Select an audio file to load it into the waveform editor.
4. Use **Stem Splitter** for Demucs separation or **Effects** for local effects processing.
5. Export files into the local library when you want them managed by StemKart.

## 1.5 Local data

On Linux, StemKart normally stores:

- Managed samples, recordings, trimmed files, and `library.json`: `~/.local/share/stemkart/samples/`
- General settings: `~/.config/stemkart/settings.json`
- Effects presets: `~/.config/stemkart/effects-presets.json`

Referenced source files remain in their original directories. Linking a file to the Library records its path; it does not duplicate the audio.

[← User guide](README.md) · [Contents](README.md) · [Next: Samples and the Explorer →](02-samples.md)
