# StemKart

StemKart is a local PyQt6 desktop app for recording, organizing samples, and splitting one music track into four WAV stems with Demucs:

- vocals
- drums
- bass
- other

The Samples tab provides a two-pane disk browser, persistent external references, optional copies into a local library, recording, preview playback, and waveform-based trimming to new WAV files. On Linux with PipeWire, the recording source can be a microphone, the monitor of a playback device, or an active application's audio stream. The Stem Splitter tab has playback for every stem and three export choices: save one stem, put every stem directly into an existing folder, or create a new named folder containing every stem.

## Setup

FFmpeg and Python 3.10+ are required. Linux system-audio and per-application recording additionally use the `pw-dump`, `pw-record`, and `pw-link` commands from PipeWire. The setup below uses the CPU-only PyTorch wheel so it does not install CUDA/NVIDIA packages:

```bash
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Run it with:

```bash
.venv/bin/python main.py
```

The first split downloads the Demucs model and may take a few minutes. Generated stems are held in a temporary directory until the app closes; use one of the export buttons to keep them. Sample-library metadata and copied/trimmed/recorded files are stored locally under the platform's application-data directory (on Linux, normally `~/.local/share/stemkart/samples/`), while referenced source files stay in their original locations. The Library view mirrors folders under that local `samples/` directory, so folders and audio files added manually are detected with Refresh or when the Library view is opened. Library folders can be created and renamed in the app; only empty user-created folders can be deleted. StemKart does not upload audio files.

## Notes

The window is intentionally fixed-size and uses no scrollable panels. The default model is Demucs `htdemucs`, which produces the four stems above.
