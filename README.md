# Stem Splitter

A small PyQt6 desktop app for browsing samples and splitting one music track into four WAV stems with Demucs:

- vocals
- drums
- bass
- other

The Samples tab provides a two-pane disk browser, persistent external references, optional copies into a local library, preview playback, and waveform-based trimming to new WAV files. The Stem Splitter tab has playback for every stem and three export choices: save one stem, put every stem directly into an existing folder, or create a new named folder containing every stem.

## Setup

FFmpeg and Python 3.10+ are required. The setup below uses the CPU-only PyTorch wheel so it does not install CUDA/NVIDIA packages:

```bash
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Run it with:

```bash
.venv/bin/python main.py
```

The first split downloads the Demucs model and may take a few minutes. Generated stems are held in a temporary directory until the app closes; use one of the export buttons to keep them. Sample-library metadata and copied/trimmed files are stored under `samples/` beside the application, while referenced source files stay in their original locations.

## Notes

The window is intentionally fixed-size and uses no scrollable panels. The default model is Demucs `htdemucs`, which produces the four stems above.
